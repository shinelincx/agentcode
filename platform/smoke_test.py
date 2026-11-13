"""P1 基础设施冒烟测试：最小协议全链路 + 预算状态机 + PAUSED 拒发（修订 2 验证）。

运行：python smoke_test.py
覆盖（对应 V2.2 AC 编号的雏形）：
  - 建库建表（6+2 张）
  - 任务/node_run 生命周期（AC-01 雏形）
  - operations 幂等键去重（AC-02 的协议根基）
  - approvals 哈希绑定（AC-09 的协议根基）
  - gate_results 失败输出留存（Retro 原料）
  - 计量写入 + tokens 解析（发现 C）
  - 预算状态机四态推进（AC-13 雏形）
  - PAUSED 拒发新 node_run（kill 语义修订 2）
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ledger
import meter

PASS: list[str] = []


def check(name: str, cond: bool, detail: str = ""):
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        raise SystemExit(1)


def main():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        db_path = Path(td) / "state.db"

        print("[1] init_db")
        db = ledger.init_db(db_path)
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        need = {"tasks", "node_runs", "operations", "approvals", "gate_results",
                "cost_ledger", "retro_queue", "budget_state"}
        check("8 tables created", need <= tables, f"missing: {need - tables}")

        print("[2] task + node_run lifecycle")
        task_id = ledger.create_task(db, "冒烟：为 river 增加一个健康检查接口")
        check("task created", len(task_id) == 36)
        run_id = ledger.start_node_run(
            db, task_id, "implement_api", lane="be",
            file_scope=["apps/api/**"], branch="feat/smoke", worktree=".worktrees/feat-smoke",
            output_path="apps/api/src/health.ts",
        )
        check("node_run started", len(run_id) == 36)
        row = db.execute("SELECT file_scope FROM node_runs WHERE id=?", (run_id,)).fetchone()
        import json as _json
        check("lane file_scope as JSON", _json.loads(row["file_scope"]) == ["apps/api/**"])
        ledger.finish_node_run(db, run_id, "done")
        check("node_run finished",
              db.execute("SELECT ended_at IS NOT NULL FROM node_runs WHERE id=?", (run_id,)).fetchone()[0])

        print("[3] operations idempotency")
        ok1 = ledger.record_operation(db, task_id, f"{task_id}:implement_api:git_commit:be",
                                      "git_commit", "abc123")
        ok2 = ledger.record_operation(db, task_id, f"{task_id}:implement_api:git_commit:be",
                                      "git_commit", "abc123")
        check("first insert ok", ok1)
        check("duplicate skipped", not ok2)
        n = db.execute("SELECT COUNT(*) FROM operations WHERE task_id=?", (task_id,)).fetchone()[0]
        check("exactly one operation", n == 1)

        print("[4] approval hash binding")
        ap_id = ledger.request_approval(db, task_id, "G3_staging", "deadbeef")
        ok = ledger.decide_approval(db, ap_id, "approved", "user")
        check("approval decided", ok)
        dup = ledger.request_approval(db, task_id, "G3_staging", "deadbeef")  # 上一个已决定，可再提
        check("re-request allowed after decision", dup > ap_id)
        row = db.execute("SELECT artifact_hash, decision, decided_by FROM approvals WHERE id=?", (ap_id,)).fetchone()
        check("hash bound", row["artifact_hash"] == "deadbeef" and row["decision"] == "approved")

        print("[5] gate result retention")
        ledger.record_gate_result(db, task_id, "typecheck", True, "", node_run_id=run_id)
        ledger.record_gate_result(db, task_id, "unit", False,
                                   "FAIL src/health.test.ts\n  expected 200, got 500", node_run_id=run_id)
        fail = db.execute("SELECT output FROM gate_results WHERE gate_name='unit'").fetchone()
        check("failure output retained", "expected 200" in fail["output"])

        print("[6] metering (tokens parse + record)")
        sample = "tokens used\n9,663\n2026-11-11T07:36:14Z INFO codex..."
        tok = meter.record_usage(db, task_id=task_id, agent_role="implement_api",
                                 executor="codex", model="gpt-6.1-terra",
                                 output_text=sample, node_run_id=run_id)
        check("tokens parsed=9663", tok == 9663)
        check("ledger calls=1", ledger.total_calls(db) == 1)

        print("[7] budget state machine 4 states (quota=10)")
        info = ledger.evaluate_budget(db, quota=10)
        check("NORMAL at 1/10", info["state"] == "NORMAL")
        for i in range(7):  # 累计 8 → ALERT
            meter.record_usage(db, task_id=task_id, agent_role="test", executor="claude",
                               model=None, output_text="", calls=1)
        info = ledger.evaluate_budget(db, quota=10)
        check("ALERT at 8/10", info["state"] == "ALERT", str(info))
        meter.record_usage(db, task_id=task_id, agent_role="test", executor="claude",
                           model=None, output_text="", calls=1)  # 9 → DEGRADED
        info = ledger.evaluate_budget(db, quota=10)
        check("DEGRADED at 9/10", info["state"] == "DEGRADED", str(info))
        meter.record_usage(db, task_id=task_id, agent_role="test", executor="claude",
                           model=None, output_text="", calls=1)  # 10 → PAUSED
        info = ledger.evaluate_budget(db, quota=10)
        check("PAUSED at 10/10", info["state"] == "PAUSED", str(info))
        trail = [r["state"] for r in db.execute(
            "SELECT state FROM budget_state ORDER BY id")]
        check("state trail recorded", trail == ["NORMAL", "ALERT", "DEGRADED", "PAUSED"], str(trail))

        print("[8] PAUSED refuses new node_run (kill-semantics rev.2)")
        try:
            ledger.start_node_run(db, task_id, "implement_web", lane="fe")
            check("PAUSED refusal", False, "no exception raised")
        except PermissionError as e:
            check("PAUSED refusal", "拒发" in str(e))

        print(f"\nALL {len(PASS)} CHECKS PASSED")


if __name__ == "__main__":
    main()
