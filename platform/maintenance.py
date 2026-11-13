"""P2 B4 半自动维护清单（AC-07 / AC-08 / AC-11）

三项清单共同原则：**只出清单，不自动执行高风险动作**。
- worktrees：默认只读扫描；`--apply` 才把孤儿目录移入 `.trash/<时间戳>/`（保留期可再清）。
- retry：按 V2.2修订 §9.2 分级（L1 同执行器 / L2 换执行器 / L3 转人工）输出处理清单。
- memory-audit / memory-rollback：核对 AGENTS.md 与 retro_queue；回滚只删指定条目并留痕。

安全边界（AC-07）：
- 任务 pending/running/awaiting_human、节点 running/awaiting_human、存活租约、待审批任务
  引用的目录一律保留；
- 分支名/ lane 名与目录名匹配的保留（worktree 常用 `branch.replace('/', '-')`）；
- 修改时间在宽限期内（默认 24h）的目录保留，避免误删尚未写账本的在执行目录；
- 孤儿目录只移动不删除，`.trash` 超过保留期需显式 `--purge`。

用法：
    python maintenance.py worktrees <db> <project_dir> [--root DIR] [--apply]
                                             [--grace-hours N] [--purge] [--trash-days N] [--json]
    python maintenance.py retry <db> [--task ID] [--max-retry N] [--json]
    python maintenance.py memory-audit <db> <project_dir> [--file PATH] [--json]
    python maintenance.py memory-rollback <db> <project_dir> <entry_id> [--actor NAME] [--file PATH] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

TERMINAL_TASK_STATUSES = ("done", "failed", "cancelled")
ACTIVE_TASK_STATUSES = ("pending", "running", "awaiting_human")
ACTIVE_RUN_STATUSES = ("running", "awaiting_human")
ACTIVE_LANE_STATUSES = ("pending", "running")


def connect(db_path: str | Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    return db


def _norm(project_dir: Path, value: str | Path) -> Path:
    p = Path(value)
    if not p.is_absolute():
        p = project_dir / p
    return p.resolve()


# ---------------- AC-07 孤儿 worktree 清理 ----------------

def _active_references(db: sqlite3.Connection, project_dir: Path, root: Path) -> tuple[set[Path], dict[str, str]]:
    """返回（必须保留的绝对路径集合，必须保留的目录名 → 依据）。"""
    keep_paths: set[Path] = set()
    keep_names: dict[str, str] = {}
    pending_tasks = {
        r["task_id"] for r in db.execute(
            "SELECT DISTINCT task_id FROM approvals WHERE decision='pending'")
    }
    rows = db.execute(
        "SELECT r.task_id, r.lane, r.branch, r.worktree_path, r.status AS run_status, "
        "(r.lease_expires_at IS NOT NULL AND r.lease_expires_at > datetime('now')) AS lease_live, "
        "t.status AS task_status, l.status AS lane_status, l.branch AS lane_branch "
        "FROM node_runs r JOIN tasks t ON t.id=r.task_id "
        "LEFT JOIN lanes l ON l.task_id=r.task_id AND l.name=r.lane").fetchall()
    for r in rows:
        active = (
            r["task_status"] in ACTIVE_TASK_STATUSES
            or r["run_status"] in ACTIVE_RUN_STATUSES
            or (r["lane_status"] in ACTIVE_LANE_STATUSES and r["task_status"] not in TERMINAL_TASK_STATUSES)
            or bool(r["lease_live"])
            or r["task_id"] in pending_tasks
        )
        if not active:
            continue
        why = (f"task={r['task_id'][:8]} status={r['task_status']} "
               f"run={r['run_status']} lane={r['lane'] or '-'}")
        if r["worktree_path"]:
            keep_paths.add(_norm(project_dir, r["worktree_path"]))
        for branch in (r["branch"], r["lane_branch"]):
            if branch:
                keep_names.setdefault(str(branch).replace("/", "-"), why)
                keep_names.setdefault(str(branch).replace("/", "_"), why)
        if r["lane"]:
            keep_names.setdefault(str(r["lane"]), why)
    return keep_paths, keep_names


def scan_worktrees(db: sqlite3.Connection, project_dir: Path, *, root: Path | None = None,
                   grace_hours: float = 24.0, apply: bool = False,
                   purge: bool = False, trash_days: float = 7.0) -> dict:
    """扫描 worktree 根目录，区分保留 / 孤儿；apply=True 时孤儿移入 `.trash`。"""
    project_dir = Path(project_dir).resolve()
    root = Path(root) if root else project_dir / ".worktrees"
    if not root.is_absolute():
        root = project_dir / root
    root = root.resolve()
    report = {"root": str(root), "dry_run": not apply, "kept": [], "orphans": [],
              "moved": [], "purged": [], "errors": []}
    if not root.is_dir():
        report["errors"].append(f"worktree 根目录不存在：{root}")
        return report

    keep_paths, keep_names = _active_references(db, project_dir, root)
    now = datetime.now().timestamp()
    grace = max(grace_hours, 0) * 3600
    trash_root = root / ".trash"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if entry.name == ".trash" or not entry.is_dir():
            continue
        resolved = entry.resolve()
        reason = None
        if resolved in keep_paths:
            reason = "账本引用的活跃目录"
        elif entry.name in keep_names:
            reason = f"活跃 lane/分支同名（{keep_names[entry.name]}）"
        else:
            age = now - entry.stat().st_mtime
            if age < grace:
                reason = f"宽限期内（{age / 3600:.1f}h < {grace_hours:g}h）"
        if reason:
            report["kept"].append({"name": entry.name, "reason": reason})
            continue

        item = {"name": entry.name, "path": str(entry),
                "last_modified": datetime.fromtimestamp(entry.stat().st_mtime).isoformat(timespec="seconds")}
        report["orphans"].append(item)
        if not apply:
            continue
        dest_dir = trash_root / stamp
        dest = dest_dir / entry.name
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                dest = dest_dir / f"{entry.name}-{datetime.now().strftime('%H%M%S%f')}"
            shutil.move(str(entry), str(dest))
            report["moved"].append({"name": entry.name, "trash_path": str(dest)})
        except OSError as exc:
            report["errors"].append(f"{entry.name}: {type(exc).__name__}: {exc}")

    if purge and trash_root.is_dir():
        cutoff = now - max(trash_days, 0) * 86400
        for snap in sorted(trash_root.iterdir(), key=lambda p: p.name):
            if not snap.is_dir() or snap.stat().st_mtime >= cutoff:
                continue
            try:
                shutil.rmtree(snap)
                report["purged"].append(snap.name)
            except OSError as exc:
                report["errors"].append(f"{snap.name}: {type(exc).__name__}: {exc}")
    return report


# ---------------- AC-08 错误重试分类 ----------------

L3_PATTERNS = (
    r"\bscope\b", r"越界", r"permission denied", r"\bpermission\b", r"\b权限\b",
    r"\bsecurity\b", r"\bunsafe\b", r"ambiguous", r"需求不明", r"人工", r"\bmanual\b",
    r"approval", r"审批", r"auth\.json", r"凭据", r"credential", r"\beperm\b",
)
L2_PATTERNS = (
    r"command not found", r"\benoent\b", r"no such file", r"executor", r"模型不可用",
    r"model .*unavailable", r"supervisor", r"segmentation fault",
)
L1_PATTERNS = (
    r"timeout", r"timed out", r"etimedout", r"econnreset", r"econnrefused", r"temporar",
    r"rate limit", r"too many requests", r"\b429\b", r"\b50[234]\b", r"flaky", r"try again",
)
LEVEL_ACTION = {
    "L1": "同执行器重试：重启节点并附失败输出（attempt+1，执行器 supervisor 自愈亦归此类）",
    "L2": "换执行器重试：codex → claude（project.yaml 备执行器）",
    "L3": "转人工：挂起任务，附失败输出、分类依据与建议动作",
}


def classify_failure(text: str, *, attempts: int = 1, max_retry: int = 3,
                     task_status: str | None = None, run_status: str | None = None) -> tuple[str, str]:
    """按 V2.2修订 §9.2 输出 (级别, 依据)。级别 ∈ L1/L2/L3。"""
    if task_status == "awaiting_human" or run_status == "awaiting_human":
        return "L3", "任务/节点处于等待人工状态"
    text = text or ""
    for pattern in L3_PATTERNS:
        if re.search(pattern, text, re.I):
            return "L3", f"命中人工处理特征 /{pattern}/"
    for pattern in L2_PATTERNS:
        if re.search(pattern, text, re.I):
            return "L2", f"命中执行器故障特征 /{pattern}/"
    if attempts >= max_retry:
        return "L3", f"同一节点已失败 {attempts} 次（阈值 {max_retry}），升级人工"
    for pattern in L1_PATTERNS:
        if re.search(pattern, text, re.I):
            return "L1", f"命中瞬时故障特征 /{pattern}/"
    return "L1", "门禁/实现失败，按同执行器重试"


def collect_retry_items(db: sqlite3.Connection, *, task: str | None = None,
                        max_retry: int = 3) -> dict:
    """扫描失败/挂起节点，产出分级处理清单。"""
    sql = ("SELECT r.id, r.task_id, r.node_name, r.lane, r.attempt, r.status AS run_status, "
           "r.started_at, r.ended_at, t.status AS task_status "
           "FROM node_runs r JOIN tasks t ON t.id=r.task_id "
           "WHERE r.status IN ('failed','awaiting_human') "
           "AND t.status NOT IN ('done','cancelled')")
    args: list = []
    if task:
        sql += " AND r.task_id=?"
        args.append(task)
    sql += " ORDER BY r.started_at DESC, r.id"
    items = []
    for r in db.execute(sql, args).fetchall():
        gate = db.execute(
            "SELECT gate_name, output FROM gate_results WHERE passed=0 AND task_id=? "
            "AND (node_run_id=? OR node_run_id IS NULL) ORDER BY id DESC LIMIT 1",
            (r["task_id"], r["id"])).fetchone()
        evidence = (gate["output"] if gate and gate["output"] else "").strip()
        level, reason = classify_failure(
            evidence, attempts=int(r["attempt"] or 1), max_retry=max_retry,
            task_status=r["task_status"], run_status=r["run_status"])
        items.append({
            "task_id": r["task_id"],
            "node": r["node_name"],
            "lane": r["lane"],
            "attempt": int(r["attempt"] or 1),
            "run_id": r["id"],
            "task_status": r["task_status"],
            "gate": gate["gate_name"] if gate else None,
            "level": level,
            "reason": reason,
            "action": LEVEL_ACTION[level],
            "evidence": evidence[:400],
        })
    summary = {lv: sum(1 for i in items if i["level"] == lv) for lv in ("L1", "L2", "L3")}
    return {"generated_at": datetime.now().isoformat(timespec="seconds"),
            "max_retry": max_retry, "items": items, "summary": summary}


# ---------------- AC-11 记忆审计与回滚 ----------------

ENTRY_RE = re.compile(r"^- \[([^\]]+)\]\s*(.*)$")
SOURCE_RE = re.compile(r"来源\s*[:：]")
ROLLBACK_RE = re.compile(r"回滚\s*[:：]")


@dataclass
class MemoryEntry:
    id: str
    text: str
    line_start: int
    line_end: int  # 独占：条目行 + 续行
    has_source: bool
    has_rollback: bool


def parse_memory_text(text: str) -> tuple[list[MemoryEntry], dict[str, int]]:
    """解析 AGENTS.md 条目（`- [ID] 文本` + 缩进续行），返回条目与重复 ID 计数。"""
    lines = text.splitlines()
    entries: list[MemoryEntry] = []
    index = 0
    while index < len(lines):
        match = ENTRY_RE.match(lines[index])
        if not match:
            index += 1
            continue
        start = index
        index += 1
        while index < len(lines) and lines[index][:1].isspace() and lines[index].strip():
            index += 1
        segment = "\n".join(lines[start:index])
        entries.append(MemoryEntry(
            id=match.group(1).strip(), text=match.group(2).strip(), line_start=start,
            line_end=index, has_source=bool(SOURCE_RE.search(segment)),
            has_rollback=bool(ROLLBACK_RE.search(segment))))
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.id] = counts.get(entry.id, 0) + 1
    duplicates = {cid: n for cid, n in counts.items() if n > 1}
    return entries, duplicates


def _queue_records(db: sqlite3.Connection) -> dict[str, list[dict]]:
    records: dict[str, list[dict]] = {}
    for row in db.execute("SELECT id, task_id, payload, status, last_error FROM retro_queue ORDER BY id"):
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            continue
        cid = payload.get("id")
        if not cid:
            continue
        records.setdefault(cid, []).append({
            "queue_id": row["id"], "task_id": row["task_id"], "status": row["status"],
            "last_error": row["last_error"], "payload": payload,
        })
    return records


def _memory_path(project_dir: Path, memory_file: str | Path | None) -> Path:
    return Path(memory_file) if memory_file else Path(project_dir) / "AGENTS.md"


def audit_memory(db: sqlite3.Connection, project_dir: Path, *,
                 memory_file: str | Path | None = None) -> dict:
    """核对 AGENTS.md 条目与 retro_queue 投递记录，输出问题清单。"""
    path = _memory_path(project_dir, memory_file)
    if not path.is_file():
        raise FileNotFoundError(f"记忆文件不存在：{path}")
    text = path.read_text(encoding="utf-8")
    entries, duplicates = parse_memory_text(text)
    records = _queue_records(db)
    file_ids = {e.id for e in entries}
    findings: list[dict] = []

    for entry in entries:
        if not entry.has_source:
            findings.append({"code": "format", "entry": entry.id,
                             "detail": "条目缺少「来源」行（无法溯源）"})
        if not entry.has_rollback:
            findings.append({"code": "format", "entry": entry.id,
                             "detail": "条目缺少「回滚」说明行"})
        recs = records.get(entry.id)
        if not recs:
            findings.append({"code": "unknown_source", "entry": entry.id,
                             "detail": "AGENTS.md 有该条目但 retro_queue 无对应记录（人工维护或库被重建）"})
        elif all(r["status"] in ("dropped", "dead") for r in recs):
            findings.append({"code": "stale_entry", "entry": entry.id,
                             "detail": f"队列记录状态为 {recs[-1]['status']}，条目仍留在文件中（应回滚）"})

    for cid, recs in records.items():
        if any(r["status"] == "done" for r in recs) and cid not in file_ids:
            findings.append({"code": "missing_entry", "entry": cid,
                             "detail": "投递记录为 done，但 AGENTS.md 中找不到该条目（文件被覆盖/回滚未留痕）"})

    for cid, count in duplicates.items():
        findings.append({"code": "duplicate", "entry": cid,
                         "detail": f"同一 ID 出现 {count} 次"})

    summary: dict[str, int] = {}
    for finding in findings:
        summary[finding["code"]] = summary.get(finding["code"], 0) + 1
    return {"file": str(path), "entries": len(entries),
            "queue_done": sum(1 for recs in records.values() for r in recs if r["status"] == "done"),
            "findings": findings, "summary": summary}


def rollback_memory(db: sqlite3.Connection, project_dir: Path, entry_id: str, *,
                    actor: str = "user", memory_file: str | Path | None = None) -> dict:
    """按 ID 删除条目（含续行），并把队列记录置 dropped 作为回滚留痕。"""
    path = _memory_path(project_dir, memory_file)
    if not path.is_file():
        raise FileNotFoundError(f"记忆文件不存在：{path}")
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    entries, _ = parse_memory_text(text)
    targets = [e for e in entries if e.id == entry_id]
    if not targets:
        return {"ok": False, "entry_id": entry_id, "error": f"未找到条目 {entry_id}"}

    remove: set[int] = set()
    for entry in targets:
        remove.update(range(entry.line_start, entry.line_end))
    new_text = "".join(line for i, line in enumerate(lines) if i not in remove)

    tmp = path.with_name(path.name + ".rollback.tmp")
    tmp.write_text(new_text, encoding="utf-8")
    os.replace(tmp, path)

    now = db.execute("SELECT datetime('now')").fetchone()[0]
    trace = f"rollback {entry_id} by {actor} at {now}"
    updated = []
    for row in db.execute("SELECT id, payload, status FROM retro_queue ORDER BY id"):
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            continue
        if payload.get("id") != entry_id:
            continue
        db.execute("UPDATE retro_queue SET status='dropped', last_error=? WHERE id=?", (trace, row["id"]))
        updated.append(row["id"])
    if not updated:  # 人工维护条目：补一条 dropped 记录作为留痕
        payload = {"id": entry_id, "text": "manual entry rollback", "type": "convention",
                   "scope": "project", "risk": "low",
                   "source": f"manual rollback by {actor}", "evidence": "AGENTS.md 条目已删除"}
        cur = db.execute(
            "INSERT INTO retro_queue (task_id, payload, status, attempts, last_error) "
            "VALUES (?,?,?,?,?)",
            ("manual", json.dumps(payload, ensure_ascii=False), "dropped", 0, trace))
        updated.append(cur.lastrowid)
    db.commit()
    return {"ok": True, "entry_id": entry_id, "file": str(path),
            "removed_lines": len(remove), "queue_ids": updated, "trace": trace}


# ---------------- CLI ----------------

def _print_worktrees(report: dict) -> None:
    for item in report["kept"]:
        print(f"[KEEP]   {item['name']}  :: {item['reason']}")
    for item in report["orphans"]:
        print(f"[ORPHAN] {item['name']}  (last modified {item['last_modified']})")
    for item in report["moved"]:
        print(f"[MOVED]  {item['name']} -> {item['trash_path']}")
    for name in report["purged"]:
        print(f"[PURGED] .trash/{name}")
    for err in report["errors"]:
        print(f"[ERROR]  {err}", file=sys.stderr)
    print(f"\n保留 {len(report['kept'])}、孤儿 {len(report['orphans'])}、"
          f"移入回收站 {len(report['moved'])}、清理 {len(report['purged'])}")


def _print_retry(report: dict) -> None:
    for item in report["items"]:
        print(f"[{item['level']}] {item['task_id'][:8]} {item['node']} attempt={item['attempt']}"
              f" :: {item['reason']}")
        print(f"       动作: {item['action']}")
        if item["evidence"]:
            print(f"       证据: {item['evidence'].replace(chr(10), ' ')[:160]}")
    print(f"\nL1={report['summary']['L1']} L2={report['summary']['L2']} L3={report['summary']['L3']}")


def _print_audit(report: dict) -> None:
    for finding in report["findings"]:
        print(f"[{finding['code']}] {finding['entry']} :: {finding['detail']}")
    print(f"\n条目 {report['entries']}，已投递记录 {report['queue_done']}，"
          f"问题 {len(report['findings'])}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="P2 B4 半自动维护清单")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("worktrees", help="孤儿 worktree 清单（AC-07）")
    p.add_argument("db")
    p.add_argument("project_dir")
    p.add_argument("--root", default=None)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--grace-hours", type=float, default=24.0)
    p.add_argument("--purge", action="store_true")
    p.add_argument("--trash-days", type=float, default=7.0)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("retry", help="错误重试分级清单（AC-08）")
    p.add_argument("db")
    p.add_argument("--task", default=None)
    p.add_argument("--max-retry", type=int, default=3)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("memory-audit", help="记忆审计清单（AC-11）")
    p.add_argument("db")
    p.add_argument("project_dir")
    p.add_argument("--file", default=None)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("memory-rollback", help="按 ID 回滚记忆条目（AC-11）")
    p.add_argument("db")
    p.add_argument("project_dir")
    p.add_argument("entry_id")
    p.add_argument("--actor", default="user")
    p.add_argument("--file", default=None)
    p.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    db = connect(args.db)
    try:
        if args.cmd == "worktrees":
            report = scan_worktrees(db, Path(args.project_dir), root=Path(args.root) if args.root else None,
                                    grace_hours=args.grace_hours, apply=args.apply,
                                    purge=args.purge, trash_days=args.trash_days)
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                _print_worktrees(report)
        elif args.cmd == "retry":
            report = collect_retry_items(db, task=args.task, max_retry=args.max_retry)
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                _print_retry(report)
        elif args.cmd == "memory-audit":
            report = audit_memory(db, Path(args.project_dir), memory_file=args.file)
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                _print_audit(report)
        else:
            report = rollback_memory(db, Path(args.project_dir), args.entry_id,
                                     actor=args.actor, memory_file=args.file)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["ok"] else 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
