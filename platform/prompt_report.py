"""Prompt 运行观测报告（B 层）

从 state.db 读账本，回答两个问题：
  1) 哪个门禁在失败、失败原因是什么（失败驱动 prompt 迭代的输入）；
  2) 每个角色的调用/tokens 与每个节点的平均耗时（成本观察，决定 prompt 该不该减重）。

用法：
    python platform/prompt_report.py                 # 默认 projects/river/state.db
    python platform/prompt_report.py --db <path> --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ledger

DEFAULT_DB = Path(__file__).parent.parent / "projects" / "river" / "state.db"


def gate_stats(db: sqlite3.Connection) -> list[dict]:
    """按门禁名汇总通过/失败数（失败率降序）。"""
    rows = db.execute(
        "SELECT gate_name, passed, COUNT(*) AS n FROM gate_results "
        "GROUP BY gate_name, passed").fetchall()
    agg: dict[str, dict] = {}
    for row in rows:
        item = agg.setdefault(row["gate_name"], {"gate": row["gate_name"],
                                                 "total": 0, "failed": 0})
        item["total"] += row["n"]
        if not row["passed"]:
            item["failed"] += row["n"]
    for item in agg.values():
        item["pass_rate"] = round((item["total"] - item["failed"]) / item["total"], 4)
    return sorted(agg.values(), key=lambda x: (-x["failed"], x["gate"]))


def recent_failures(db: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """最近失败门禁（原因截断，供 prompt 迭代取样）。"""
    rows = db.execute(
        "SELECT task_id, gate_name, output, checked_at FROM gate_results "
        "WHERE passed=0 ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{"task_id": row["task_id"], "gate": row["gate_name"],
             "at": row["checked_at"],
             "reason": " ".join((row["output"] or "").split())[:200]} for row in rows]


def role_costs(db: sqlite3.Connection) -> list[dict]:
    """按角色汇总调用数与 tokens（calls 为主单位，tokens 为输出解析值）。"""
    rows = db.execute(
        "SELECT agent_role, COUNT(*) AS records, COALESCE(SUM(calls), 0) AS calls, "
        "COALESCE(SUM(tokens_used), 0) AS tokens, COUNT(DISTINCT model) AS models "
        "FROM cost_ledger GROUP BY agent_role ORDER BY tokens DESC").fetchall()
    out = []
    for row in rows:
        calls = row["calls"] or 0
        out.append({"role": row["agent_role"], "records": row["records"],
                    "calls": calls, "tokens": row["tokens"],
                    "avg_tokens": round(row["tokens"] / calls, 1) if calls else 0.0,
                    "models": row["models"]})
    return out


def node_durations(db: sqlite3.Connection) -> list[dict]:
    """按节点汇总耗时（秒），仅统计已收尾的 node_runs。"""
    rows = db.execute(
        "SELECT node_name, COUNT(*) AS runs, "
        "ROUND(AVG((julianday(ended_at) - julianday(started_at)) * 86400), 1) AS avg_s, "
        "ROUND(MAX((julianday(ended_at) - julianday(started_at)) * 86400), 1) AS max_s "
        "FROM node_runs WHERE ended_at IS NOT NULL "
        "GROUP BY node_name ORDER BY avg_s DESC").fetchall()
    return [{"node": row["node_name"], "runs": row["runs"],
             "avg_seconds": row["avg_s"], "max_seconds": row["max_s"]} for row in rows]


def build_report(db: sqlite3.Connection) -> dict:
    return {"gates": gate_stats(db),
            "recent_failures": recent_failures(db),
            "costs": role_costs(db),
            "durations": node_durations(db)}


def _print_report(report: dict) -> None:
    print("=== 门禁失败类型 ===")
    if report["gates"]:
        for g in report["gates"]:
            print(f"  {g['gate']:<20} 失败 {g['failed']}/{g['total']}  "
                  f"通过率 {g['pass_rate']:.1%}")
    else:
        print("  （无门禁记录）")

    print("\n=== 最近失败原因 ===")
    if report["recent_failures"]:
        for f in report["recent_failures"][:10]:
            print(f"  [{f['at']}] {f['task_id']} {f['gate']}: {f['reason']}")
    else:
        print("  （无失败记录）")

    print("\n=== 角色成本 ===")
    if report["costs"]:
        for c in report["costs"]:
            print(f"  {c['role']:<16} calls={c['calls']:<4} tokens={c['tokens']:<8} "
                  f"avg={c['avg_tokens']}")
    else:
        print("  （无用量记录）")

    print("\n=== 节点耗时（秒） ===")
    if report["durations"]:
        for d in report["durations"]:
            print(f"  {d['node']:<20} runs={d['runs']:<3} avg={d['avg_seconds']} "
                  f"max={d['max_seconds']}")
    else:
        print("  （无已收尾节点）")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Prompt 运行观测报告（B 层）")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    path = Path(args.db)
    if not path.is_file():
        print(f"找不到账本：{path}")
        return 2
    db = ledger.connect(path)
    try:
        report = build_report(db)
    finally:
        db.close()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
