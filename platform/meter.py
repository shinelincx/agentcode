"""计量记录器（事后计量，只记录不拦截）· V2.2 §8.2 / 发现 C

数据源：codex exec 输出末尾的 tokens 统计（如 "tokens used\\n9,663"）。
写入 cost_ledger；写入失败不阻塞主流程（缓冲重试由调用方处理）。
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

# codex exec 输出形如：
#   tokens used
#   18,505
_TOKENS_RE = re.compile(r"tokens?\s*used\s*:?\s*\n\s*([\d,]+)", re.IGNORECASE)


def parse_tokens(output_text: str) -> int | None:
    """从 codex exec 输出解析 tokens used 数值；解析不到返回 None。"""
    m = _TOKENS_RE.search(output_text or "")
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def record_usage(db: sqlite3.Connection, *, task_id: str, agent_role: str, executor: str,
                 model: str | None, output_text: str,
                 node_run_id: str | None = None, calls: int = 1) -> int | None:
    """解析输出并写入 cost_ledger（calls 为主计量单位，订阅模式）。返回 tokens 或 None。"""
    tokens = parse_tokens(output_text)
    db.execute(
        "INSERT INTO cost_ledger (task_id, node_run_id, agent_role, executor, model, tokens_used, calls) "
        "VALUES (?,?,?,?,?,?,?)",
        (task_id, node_run_id, agent_role, executor, model, tokens, calls),
    )
    db.commit()
    return tokens


def record_usage_safe(db: sqlite3.Connection, *, buffer_path: str | Path, **kw) -> int | None:
    """写入用量；失败时落盘缓冲而非抛错（AC-14：计量故障不阻塞主流程）。"""
    try:
        return record_usage(db, **kw)
    except sqlite3.Error as exc:
        _append_buffer(buffer_path, {"payload": kw, "error": str(exc)})
        return None


def flush_buffer(db: sqlite3.Connection, buffer_path: str | Path) -> tuple[int, int]:
    """节点边界补记缓冲：返回 (补记成功数, 仍失败数)。"""
    path = Path(buffer_path)
    if not path.exists():
        return (0, 0)
    ok = 0
    remain: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            record_usage(db, **item["payload"])
            ok += 1
        except sqlite3.Error:
            remain.append(line)
    path.write_text("\n".join(remain) + ("\n" if remain else ""), encoding="utf-8")
    return (ok, len(remain))


def _append_buffer(buffer_path: str | Path, item: dict):
    path = Path(buffer_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(item, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    # 自测样例（真实输出摘录）
    sample = "some log lines...\ntokens used\n9,663\n2026-11-11T07:36:14Z INFO ..."
    print("parse:", parse_tokens(sample))  # 期望 9663
    assert parse_tokens(sample) == 9663
    assert parse_tokens("no stats here") is None
    print("meter self-test ok")
