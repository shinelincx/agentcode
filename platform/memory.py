"""Retro L0 自动采纳层 + B2 完整 outbox · V2.2 §10.3

L0 条件（五项全满足才自动采纳）：scope=project（项目内）+ risk=low（非权限/非生产）
+ type ∈ {convention, pitfall}（格式/工具级）+ 未命中既有条目（幂等）+ 来源任务可溯源。
安全网：条目带 ID/日期/来源，一键回滚 = 删行；月度人工抽查（V2.2 §10.3）。
L1（scope=platform 或条件不满足）：留池待周会人工审核。

B2 投递语义（retro_queue 升级为 outbox）：
- 至少一次：投递前先 `attempts+1` 并提交；崩溃在「已写文件、未标 done」之间时，
  重放命中 AGENTS.md 的 `[id]` 标记 → 直接标 done，不产生重复条目。
- 失败可重放：失败记 `last_error` + `next_attempt_at` 指数退避；`--force` 忽略退避立即重放。
- 死信：`attempts >= max_attempts` 转 `status='dead'`，不再自动重试（留证据供人工处理）。

用法：
    python memory.py adopt <db_path> <project_dir> [--dry-run]
    python memory.py deliver <db_path> <project_dir> [--force] [--dry-run] [--max-attempts N]
    python memory.py pool <db_path>
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

L0_TYPES = {"convention", "pitfall"}
SECTION_BY_TYPE = {"pitfall": "Known Pitfalls", "convention": "Project Conventions"}

# B2 outbox 参数：指数退避 30s → 60s → 120s → 240s → 300s 封顶
MAX_ATTEMPTS = 5
BASE_BACKOFF = 30
MAX_BACKOFF = 300

AGENTS_HEADER = ("<!-- river 项目级 Agent 记忆：L0 自动采纳层维护（V2.2 §10.3），"
                 "人工可编辑，变更留痕于 retro_queue -->\n")


def classify(c: dict) -> str:
    if c.get("scope") == "project" and c.get("risk") == "low" and c.get("type") in L0_TYPES:
        return "L0"
    return "L1"


def render_entry(c: dict) -> str:
    return (f"- [{c['id']}] {c['text']}\n"
            f"  来源: {c.get('source', '?')} | 证据: {c.get('evidence', '?')} | "
            f"回滚: 删除本行（一键回滚，V2.2 §10.3 安全网）\n")


def insert_into_section(text: str, section: str, entry: str) -> str:
    """把条目插入到 '## <section>' 标题行之后（保持段落归属正确）。"""
    marker = f"## {section}\n"
    idx = text.find(marker)
    if idx == -1:
        return text + f"\n{marker}{entry}"
    insert_at = idx + len(marker)
    return text[:insert_at] + entry + text[insert_at:]


def ensure_sections(text: str) -> str:
    if "## Known Pitfalls" not in text:
        text += "\n## Known Pitfalls\n"
    if "## Project Conventions" not in text:
        text += "\n## Project Conventions\n"
    return text


def entry_exists(agents_md: Path, cid: str) -> bool:
    if not agents_md.exists():
        return False
    return re.search(rf"\[{re.escape(cid)}\]", agents_md.read_text(encoding="utf-8")) is not None


def _write_entry(project_dir: Path, c: dict) -> None:
    """把单条候选写入项目 AGENTS.md（幂等由调用方 entry_exists 保障）。"""
    agents_md = project_dir / "AGENTS.md"
    text = agents_md.read_text(encoding="utf-8") if agents_md.exists() else AGENTS_HEADER
    text = ensure_sections(text)
    text = insert_into_section(text, SECTION_BY_TYPE[c["type"]], render_entry(c))
    agents_md.write_text(text, encoding="utf-8")


def _backoff_seconds(attempts: int) -> int:
    return min(BASE_BACKOFF * (2 ** max(attempts - 1, 0)), MAX_BACKOFF)


def deliver(db: sqlite3.Connection, project_dir: Path, *, dry_run: bool = False,
            force: bool = False, max_attempts: int = MAX_ATTEMPTS) -> dict:
    """至少一次投递 pending 候选到项目 AGENTS.md。

    - 至少一次：投递前先 attempts+1 并提交；崩溃在「已写文件、未标 done」之间时，
      重放命中 `[id]` 标记 → 直接标 done，不产生重复。
    - 跨进程互斥：逐条 `BEGIN IMMEDIATE` 写事务内完成「复核 + CAS 认领 + 文件读改写 + 标 done」，
      其他投递进程等待写锁而非同时读改写（避免重复条目与丢失更新）。
    - 失败可重放：失败记 last_error + next_attempt_at 退避；force=True 忽略退避立即重放。
    - 死信：attempts >= max_attempts 转 status='dead'，不再自动重试。
    L1 候选不投递，保持 pending 供人工审核（计入 deferred）。
    """
    agents_md = project_dir / "AGENTS.md"
    stats = {"delivered": [], "retried": [], "dead": [], "deferred": [],
             "skipped_duplicate": [], "not_due": [], "claimed_by_other": [], "locked": []}
    now = db.execute("SELECT datetime('now')").fetchone()[0]
    rows = db.execute(
        "SELECT id, task_id, payload, attempts, next_attempt_at "
        "FROM retro_queue WHERE status='pending' ORDER BY id").fetchall()
    for r in rows:
        c = {"queue_id": r["id"], "task_id": r["task_id"], **json.loads(r["payload"])}
        if classify(c) != "L0":
            stats["deferred"].append(c["id"])
            continue
        if not force and r["next_attempt_at"] and r["next_attempt_at"] > now:
            stats["not_due"].append(c["id"])
            continue
        if dry_run:  # 只读预演：不认领、不写文件
            key = "skipped_duplicate" if entry_exists(agents_md, c["id"]) else "delivered"
            stats[key].append(c["id"])
            continue
        try:
            db.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            stats["locked"].append(c["id"])  # 其他投递进程持写锁；保持 pending 待下轮
            continue
        attempts = 0
        try:
            cur = db.execute("SELECT status, attempts, next_attempt_at FROM retro_queue WHERE id=?",
                             (r["id"],)).fetchone()
            if cur is None or cur["status"] != "pending" or (
                    not force and cur["next_attempt_at"] and cur["next_attempt_at"] > now):
                db.rollback()
                stats["claimed_by_other"].append(c["id"])  # 已被其他进程处理/未到期
                continue
            claimed = db.execute(
                "UPDATE retro_queue SET attempts=attempts+1 "
                "WHERE id=? AND status='pending' AND attempts=?", (r["id"], cur["attempts"]))
            if claimed.rowcount == 0:  # CAS 失败：并发进程刚认领
                db.rollback()
                stats["claimed_by_other"].append(c["id"])
                continue
            attempts = int(cur["attempts"]) + 1
            if entry_exists(agents_md, c["id"]):  # 崩溃恢复：文件已写、未标 done
                db.execute("UPDATE retro_queue SET status='done', delivered_at=datetime('now'), "
                           "next_attempt_at=NULL, last_error=NULL WHERE id=?", (r["id"],))
                db.commit()
                stats["skipped_duplicate"].append(c["id"])
                continue
            _write_entry(project_dir, c)
            db.execute("UPDATE retro_queue SET status='done', delivered_at=datetime('now'), "
                       "next_attempt_at=NULL, last_error=NULL WHERE id=?", (r["id"],))
            db.commit()
            stats["delivered"].append(c["id"])
        except Exception as exc:
            try:
                db.rollback()
            except sqlite3.Error:
                pass
            if attempts == 0:  # 认领阶段异常：不记失败次数，留待下轮
                stats["locked"].append(c["id"])
                continue
            try:  # 失败记账用独立短事务，attempts 显式写入（认领已随回滚撤销）
                if attempts >= max_attempts:
                    db.execute("UPDATE retro_queue SET status='dead', attempts=?, last_error=?, "
                               "next_attempt_at=NULL WHERE id=? AND status='pending'",
                               (attempts, str(exc)[:500], r["id"]))
                    stats["dead"].append(c["id"])
                else:
                    db.execute("UPDATE retro_queue SET attempts=?, last_error=?, "
                               "next_attempt_at=datetime('now', ?) WHERE id=? AND status='pending'",
                               (attempts, str(exc)[:500], f"+{_backoff_seconds(attempts)} seconds",
                                r["id"]))
                    stats["retried"].append(c["id"])
                db.commit()
            except sqlite3.Error:
                stats["locked"].append(c["id"])
    return stats


def adopt(db: sqlite3.Connection, project_dir: Path, *, dry_run: bool = False,
          force: bool = True) -> dict:
    """兼容入口（原 L0 直接采纳）：等价 deliver(force=True)，保留旧统计键。"""
    s = deliver(db, project_dir, dry_run=dry_run, force=force)
    return {"L0_adopted": s["delivered"], "L1_deferred": s["deferred"],
            "skipped_duplicate": s["skipped_duplicate"],
            "failed": s["retried"] + s["dead"]}


def _cli(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] in ("adopt", "deliver"):
        cmd, db_path, project_dir = argv[0], argv[1], Path(argv[2])
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        dry = "--dry-run" in argv
        max_attempts = MAX_ATTEMPTS
        if "--max-attempts" in argv:
            idx = argv.index("--max-attempts")
            if idx + 1 >= len(argv):
                print("--max-attempts 需要整数值", file=sys.stderr)
                return 2
            max_attempts = int(argv[idx + 1])
        if cmd == "adopt":
            stats = adopt(db, project_dir, dry_run=dry)
        else:
            stats = deliver(db, project_dir, dry_run=dry,
                            force="--force" in argv, max_attempts=max_attempts)
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0
    if len(argv) >= 2 and argv[0] == "pool":
        db = sqlite3.connect(argv[1])
        db.row_factory = sqlite3.Row
        for r in db.execute(
                "SELECT id, task_id, payload, status, attempts, next_attempt_at, last_error "
                "FROM retro_queue ORDER BY id"):
            c = json.loads(r["payload"])
            extra = "" if r["status"] == "done" else (
                f" attempts={r['attempts']} next={r['next_attempt_at'] or '-'}"
                + (f" err={r['last_error'][:60]}" if r["last_error"] else ""))
            print(f"{r['id']} [{r['status']}] {c.get('id', '?')} {c.get('text', '')[:50]}{extra}")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
