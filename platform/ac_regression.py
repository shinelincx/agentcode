"""AC 回归 harness（V2.2 §7.5 / 评审发现 F6）

把 AC-01～06、09、12、13、14 从手工清单固化为可重跑脚本：
    python ac_regression.py            # 快跑：全部可脚本化用例
    python ac_regression.py --only AC-13

状态语义：
    PASS  断言成立
    FAIL  断言不成立（退出码 1）
    GAP   场景所需能力尚未实现（不计失败，但计入未覆盖）
    SKIP  需外部系统/高成本执行（docker staging、真实 codex 沙箱），附手工命令

每个用例使用独立临时工作区与独立 state.db，互不影响。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ledger
import meter

RESULTS: list[tuple[str, str, str, str]] = []  # (case, check, status, detail)
_CASE = ""


def check(desc: str, cond: bool, detail: str = ""):
    RESULTS.append((_CASE, desc, "PASS" if cond else "FAIL", detail))


def skip(desc: str, detail: str):
    RESULTS.append((_CASE, desc, "SKIP", detail))


def new_db(ws: Path) -> sqlite3.Connection:
    return ledger.init_db(ws / "state.db")


def _count(db: sqlite3.Connection, sql: str, args: tuple = ()) -> int:
    return int(db.execute(sql, args).fetchone()[0])


# ---------------- AC-01 账本与图状态一致性 ----------------

def ac01(ws: Path):
    db = new_db(ws)
    tasks = [ledger.create_task(db, f"ac01-{i}") for i in range(3)]
    for t in tasks:
        run = ledger.start_node_run(db, t, "implement_be", lane="be")
        ledger.record_gate_result(db, t, "typecheck", True, node_run_id=run)
        ledger.record_operation(db, t, f"{t}:git_commit:be", "git_commit", "h1", node_run_id=run)
        ledger.finish_node_run(db, run, "done")
        ledger.set_task_status(db, t, "done")

    check("无孤儿 node_runs",
          _count(db, "SELECT COUNT(*) FROM node_runs r LEFT JOIN tasks t ON t.id=r.task_id WHERE t.id IS NULL") == 0)
    check("无孤儿 operations",
          _count(db, "SELECT COUNT(*) FROM operations o LEFT JOIN tasks t ON t.id=o.task_id WHERE t.id IS NULL") == 0)
    check("无孤儿 gate_results",
          _count(db, "SELECT COUNT(*) FROM gate_results gr LEFT JOIN tasks t ON t.id=gr.task_id WHERE t.id IS NULL") == 0)
    check("无残留 running 节点（任务已 done）",
          _count(db, "SELECT COUNT(*) FROM node_runs WHERE status='running'") == 0)
    check("每个任务都有节点执行记录（账本可解释图状态）",
          _count(db, "SELECT COUNT(*) FROM tasks t WHERE NOT EXISTS "
                     "(SELECT 1 FROM node_runs r WHERE r.task_id=t.id)") == 0)


# ---------------- AC-02 Worker 中断 / 幂等重放 ----------------

def ac02(ws: Path):
    db = new_db(ws)
    t = ledger.create_task(db, "ac02")
    run1 = ledger.start_node_run(db, t, "implement_fe", attempt=1, lane="fe")
    first = ledger.record_operation(db, t, f"{t}:git_commit:fe", "git_commit", "h1", node_run_id=run1)
    ledger.finish_node_run(db, run1, "failed")  # 进程被 kill 后重启判定为失败

    run2 = ledger.start_node_run(db, t, "implement_fe", attempt=2, lane="fe")
    dup = ledger.record_operation(db, t, f"{t}:git_commit:fe", "git_commit", "h1", node_run_id=run2)
    ledger.finish_node_run(db, run2, "done")

    check("首次副作用写入成功", first is True)
    check("重放同一幂等键被跳过（副作用不重复）", dup is False)
    check("operations 中该键仅 1 条",
          _count(db, "SELECT COUNT(*) FROM operations WHERE operation_key=?", (f"{t}:git_commit:fe",)) == 1)
    check("attempt 重开留痕（2 次 node_run）",
          _count(db, "SELECT COUNT(*) FROM node_runs WHERE task_id=?", (t,)) == 2)


# ---------------- AC-02b Worker 空交付检测（C-007 防回归） ----------------

def ac02b(ws: Path):
    import graph as g
    from langgraph.types import Command

    db = new_db(ws)
    g.RIVER_WS = ws
    g.USAGE_BUFFER = ws / "usage_buffer.jsonl"
    import worker as w

    def _ac02b_codex(prompt, workdir, **kw):
        # 文档生成阶段正常返回；实现阶段模拟"只读空跑"（C-007 场景）
        if ("Write REQUIREMENTS.md" in prompt or "Write DESIGN.md" in prompt
                or "You are the Prototype Agent" in prompt):
            return _stub_codex(prompt, workdir, **kw)
        return w.WorkerResult(
            0, "tokens used\n  9\nI couldn't make the change: read-only. No files were changed.", 9,
            "I couldn't make the change: read-only. No files were changed.")
    w.run_codex = _ac02b_codex
    w.run_gate = lambda command, workdir, timeout=900: (True, "gate on old code passes (false positive)")
    w.git = lambda workdir, *args: (0, "")  # 无改动 → 必须判失败，不得进门禁

    t = ledger.create_task(db, "ac02b")
    cfg = {"configurable": {"thread_id": "ac02b"}}
    app = g.build_app(g.make_checkpointer(ws / "graph.db"))
    app.invoke({"task_id": t, "requirement": "ac02b", "base_branch": "base",
                "lanes": [{"name": "be", "node": "implement_api", "branch": "b1",
                           "scope": [], "prompt": "p", "commit_msg": "m"}]}, cfg)
    app.invoke(Command(resume="approved"), cfg)
    app.invoke(Command(resume="approved"), cfg)

    check("空交付时 lane 判失败（不被门禁假阳性掩盖）",
          db.execute("SELECT status FROM tasks WHERE id=?", (t,)).fetchone()["status"] == "gate_failed")
    check("worker_no_changes 门禁留痕（含 codex 报告尾部）",
          _count(db, "SELECT COUNT(*) FROM gate_results WHERE gate_name='worker_no_changes' AND passed=0") == 1)
    check("无 git_commit 操作登记（空提交不记 done）",
          _count(db, "SELECT COUNT(*) FROM operations WHERE task_id=?", (t,)) == 0)
    check("retro 不把 gate_failed 覆盖成 done",
          db.execute("SELECT status FROM tasks WHERE id=?", (t,)).fetchone()["status"] == "gate_failed")


# ---------------- AC-03 Orchestrator 中断后恢复 ----------------

# 审查桩的结论开关（REVIEW-01 用它验证 PASS 放行 / FAIL 拦截两条路径）
_REVIEW_VERDICT = {"value": "PASS"}
# implement 类 prompt 采集（REVIEW-02 用它验证返工指令注入了审查意见）
_PROMPTS: list[str] = []

# KILL-01 需要真实实现（其它用例的 _stub_worker 会覆盖 worker 模块属性）
import worker as _worker_mod

_REAL_RUN_CODEX = _worker_mod.run_codex
_REAL_RUN_GATE = _worker_mod.run_gate


def _stub_codex(prompt, workdir, **kw):
    """A2 起 requirements/design 也走 codex：桩按 prompt 返回合法 Markdown 文档。

    原型节点由 Agent 写文件：桩按 prompt 中的目录写 index.html + PROTOTYPE.md。
    """
    import re

    import worker as w
    if "Write REQUIREMENTS.md" in prompt:
        out = ("# REQUIREMENTS · task stub\n\n## 需求\n- stub requirement: add a sample field\n\n"
               "## 验收标准\n- AC-1: stub acceptance criterion\n- AC-2: stub second criterion\n\n"
               "## 非目标\n- nothing else\n")
    elif "Write DESIGN.md" in prompt:
        out = ("# DESIGN · task stub\n\n## 文件归属表\n| 路径 | lane |\n| --- | --- |\n"
               "| apps/api/src/index.ts | be |\n| apps/web/src/app.tsx | fe |\n\n"
               "## 实现策略\nserial be → fe\n\n"
               "## 验收映射\n- AC-1 → apps/api/src/index.ts\n- AC-2 → apps/web/src/app.tsx\n")
    elif "You are the Prototype Agent" in prompt:
        target = Path(re.search(r"Prototype directory:\s*(.+)", prompt).group(1).strip())
        target.mkdir(parents=True, exist_ok=True)
        (target / "index.html").write_text(
            "<!DOCTYPE html>\n<html lang=\"zh\">\n<head><meta charset=\"utf-8\">"
            "<title>stub prototype</title></head>\n<body>\n<h1>stub prototype</h1>\n"
            "<p>静态占位原型：用于验证原型节点产物校验与 G2 审批哈希绑定。</p>\n"
            "<p>该页面不连接任何后端接口，仅覆盖最小长度与 HTML 骨架校验。</p>\n"
            "<button>示例操作</button>\n</body>\n</html>\n", encoding="utf-8")
        (target / "PROTOTYPE.md").write_text(
            "# PROTOTYPE · stub\n\n## 原型说明\n首页提示区域的静态占位原型，"
            "包含标题与操作按钮，用于验证产物校验流程。\n\n"
            "## 验收映射\n- AC-1 → h1 标题\n- AC-2 → button 操作\n\n"
            "## 局限\n静态页面，无真实接口与状态流转。\n", encoding="utf-8")
        out = "prototype stub written"
    elif "You are the Review Agent" in prompt:
        out = (f"# REVIEW · task stub\n\n## 结论\n{_REVIEW_VERDICT['value']} stub verdict\n\n"
               "## 问题\n- 无\n\n## 建议\n- 无\n")
    elif "You are the Retrospective Agent" in prompt:
        out = ("# RETRO · task stub\n\n## 结果\nstub delivered\n\n## 偏差\n无\n\n"
               "## 经验\nstub lesson\n\n## 后续动作\n无\n")
    else:
        _PROMPTS.append(prompt)
        out = "stub implement report"
    return w.WorkerResult(0, out + "\ntokens used\n  10\n", 10, "stub")


def _stub_worker():
    import worker as w
    w.run_codex = _stub_codex
    w.run_gate = lambda command, workdir, timeout=900: (True, "stub gate passed")
    state = {"branch": ""}

    def _fake_git(workdir, *args):
        # checkout -B 记录当前 lane 分支：scope 硬校验要求 fe lane 只改 web、be 只改 api
        if len(args) >= 3 and args[0] == "checkout" and args[1] == "-B":
            state["branch"] = args[2]
            return (0, "")
        if args[:2] == ("status", "--porcelain"):  # C-007 空交付检测：桩须模拟有改动
            if state["branch"].endswith("-fe"):
                return (0, "M  apps/web/src/app.tsx\n")
            return (0, "M  apps/api/src/index.ts\n")
        return (0, "")
    w.git = _fake_git


def ac03(ws: Path):
    import graph as g
    from langgraph.types import Command

    db = new_db(ws)
    g.RIVER_WS = ws
    g.USAGE_BUFFER = ws / "usage_buffer.jsonl"
    _stub_worker()
    t = ledger.create_task(db, "ac03")
    cfg = {"configurable": {"thread_id": "ac03"}}
    state = {
        "task_id": t, "requirement": "ac03", "base_branch": "base",
        "lanes": [{"name": "be", "node": "implement_api", "branch": "b1",
                   "scope": [], "prompt": "p", "commit_msg": "m"}],
    }
    app = g.build_app(g.make_checkpointer(ws / "graph.db"))
    app.invoke(state, cfg)
    snap = app.get_state(cfg)
    check("首次运行在 G1 挂起", bool(snap.next) and "review_g1" in snap.next)

    # 模拟 Orchestrator 崩溃重启：新连接 + 新图实例，仅依赖 state.db + graph.db 重建
    app2 = g.build_app(g.make_checkpointer(ws / "graph.db"))
    app2.invoke(Command(resume="approved"), cfg)
    check("恢复后停在 G2（G1 结论未丢失）",
          "review_g2" in app2.get_state(cfg).next)
    app2.invoke(Command(resume="approved"), cfg)

    values = app2.get_state(cfg).values
    check("恢复后 G1/G2 批准状态保留", values.get("approved_g1") is True and values.get("approved_g2") is True)
    check("任务终态 done",
          db.execute("SELECT status FROM tasks WHERE id=?", (t,)).fetchone()["status"] == "done")
    check("节点账本完整（requirements/design/implement/retro）",
          _count(db, "SELECT COUNT(DISTINCT node_name) FROM node_runs WHERE task_id=?", (t,)) >= 4)
    check("无重复副作用键",
          _count(db, "SELECT COUNT(*) FROM (SELECT operation_key FROM operations "
                     "GROUP BY operation_key HAVING COUNT(*)>1)") == 0)


# ---------------- AC-05 迁移脚本中断续跑 ----------------

def ac05(ws: Path):
    db = new_db(ws)
    before = sorted(r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    ledger.init_db(ws / "state.db")  # 模拟中断后重放同一迁移
    ledger.init_db(ws / "state.db")
    after = sorted(r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    check("重复执行迁移不报错且表集合不变", before == after and len(after) >= 6, f"{len(after)} 张表")
    t = ledger.create_task(db, "ac05")
    check("迁移重放后账本仍可写", bool(t))


# ---------------- AC-09 审批哈希绑定 / 失效重审 ----------------

def ac09(ws: Path):
    db = new_db(ws)
    t = ledger.create_task(db, "ac09")
    aid = ledger.request_approval(db, t, "G3_staging", "hashA")
    ledger.decide_approval(db, aid, "approved", "approver")
    check("批准后对同一产物哈希有效", ledger.approval_valid(db, t, "G3_staging", "hashA"))
    check("产物变更后旧审批不再放行", not ledger.approval_valid(db, t, "G3_staging", "hashB"))
    aid2 = ledger.request_approval(db, t, "G3_staging", "hashB")
    check("新哈希需新审批，未审前仍不放行", not ledger.approval_valid(db, t, "G3_staging", "hashB"))
    ledger.decide_approval(db, aid2, "approved", "approver")
    check("重新审批后放行新哈希", ledger.approval_valid(db, t, "G3_staging", "hashB"))


# ---------------- AC-13 预算边界（注入累计用量） ----------------

def _inject_calls(db: sqlite3.Connection, n: int, task_id: str | None = None):
    for _ in range(n):
        db.execute(
            "INSERT INTO cost_ledger (task_id, agent_role, executor, calls) VALUES (?,?,?,1)",
            (task_id, "worker", "codex"),
        )
    db.commit()


def ac13(ws: Path):
    import graph as g

    db = new_db(ws)
    quota = 100
    b = ledger.evaluate_budget(db, quota)
    check("①起点 NORMAL（0%）", b["state"] == "NORMAL", str(b))
    t = ledger.create_task(db, "ac13")
    run = ledger.start_node_run(db, t, "implement_be")
    ledger.finish_node_run(db, run, "done")

    _inject_calls(db, 80)
    b = ledger.evaluate_budget(db, quota)
    check("②80% → ALERT", b["state"] == "ALERT", str(b))
    t_low = ledger.create_task(db, "ac13-low", priority="low")
    try:
        ledger.start_node_run(db, t_low, "implement_be")
        low_blocked = False
    except PermissionError:
        low_blocked = True
    check("①80% 时低优先级任务排队（拒发 node_run）", low_blocked)
    check("ALERT 不影响普通优先级任务", bool(ledger.start_node_run(db, t, "test_alert")))

    _inject_calls(db, 10)
    b = ledger.evaluate_budget(db, quota)
    check("③90% → DEGRADED", b["state"] == "DEGRADED", str(b))
    check("DEGRADED 路由到降级档模型", g.route_model("DEGRADED") == "gpt-6.1-sol")
    check("DEGRADED 仍允许发节点（不误停）", bool(ledger.start_node_run(db, t, "test")))

    _inject_calls(db, 10)
    b = ledger.evaluate_budget(db, quota)
    check("④100% → PAUSED", b["state"] == "PAUSED", str(b))
    try:
        ledger.start_node_run(db, t, "deploy")
        paused_ok = False
    except PermissionError:
        paused_ok = True
    check("PAUSED 时新 node_run 被拒发", paused_ok)
    running = db.execute(
        "SELECT id FROM node_runs WHERE task_id=? AND status='running' ORDER BY started_at DESC LIMIT 1", (t,)
    ).fetchone()
    ledger.finish_node_run(db, running["id"], "done")
    check("在途节点可正常收尾（不被 kill）",
          db.execute("SELECT status FROM node_runs WHERE id=?", (running["id"],)).fetchone()["status"] == "done")
    aid = ledger.request_approval(db, t, "G3_staging", "h")  # 人审照常
    check("PAUSED 时人审流程照常", ledger.decide_approval(db, aid, "approved", "approver"))

    b = ledger.evaluate_budget(db, 200)  # 提额 = proj resume
    check("提额后解挂并恢复发节点", b["state"] == "NORMAL" and bool(ledger.start_node_run(db, t, "retro")))

    # 单任务上限 U：编排层在节点边界拒发并转人审
    t2 = ledger.create_task(db, "ac13-cap")
    _inject_calls(db, g.TASK_CALL_LIMIT, task_id=t2)
    g.RIVER_WS = ws
    g.USAGE_BUFFER = ws / "usage_buffer.jsonl"
    _stub_worker()
    cfg = {"configurable": {"thread_id": "ac13-cap"}}
    from langgraph.types import Command
    app = g.build_app(g.make_checkpointer(ws / "graph.db"))
    app.invoke({"task_id": t2, "requirement": "cap", "base_branch": "base",
                "lanes": [{"name": "be", "node": "implement_api", "branch": "b2",
                           "scope": [], "prompt": "p", "commit_msg": "m"}]}, cfg)
    app.invoke(Command(resume="approved"), cfg)
    app.invoke(Command(resume="approved"), cfg)
    cnt = _count(db, "SELECT COUNT(*) FROM node_runs WHERE task_id=? AND node_name='implement_api'", (t2,))
    status = db.execute("SELECT status FROM tasks WHERE id=?", (t2,)).fetchone()["status"]
    cap_gate = _count(db, "SELECT COUNT(*) FROM approvals WHERE task_id=? AND gate_name='budget_task_cap'", (t2,))
    check("单任务超 U 时不启动节点并转人审",
          cnt == 0 and status == "awaiting_human" and cap_gate >= 1,
          f"runs={cnt} status={status} approvals={cap_gate}")


# ---------------- AC-14 计量故障隔离 ----------------

def ac14(ws: Path):
    import meter as m

    db = new_db(ws)
    buf = ws / "usage_buffer.jsonl"
    real = m.record_usage

    def boom(*_a, **_k):
        raise sqlite3.OperationalError("injected: disk I/O error")

    m.record_usage = boom
    try:
        r = m.record_usage_safe(db, buffer_path=buf, task_id=None, agent_role="worker",
                                executor="codex", model="gpt-6.1-terra",
                                output_text="tokens used\n  7")
        check("写入失败不影响主流程（返回 None 不抛错）", r is None)
    finally:
        m.record_usage = real
    check("失败用量已落盘缓冲", buf.exists() and len(buf.read_text(encoding="utf-8").strip().splitlines()) == 1)

    ok, remain = m.flush_buffer(db, buf)
    check("节点边界补记成功且缓冲清空", ok == 1 and remain == 0)
    check("累计值收敛（calls=1、tokens=7）",
          ledger.total_calls(db) == 1 and
          int(db.execute("SELECT COALESCE(SUM(tokens_used),0) FROM cost_ledger").fetchone()[0]) == 7)
    check("评估口径随补记修正（ratio=0.001）", ledger.evaluate_budget(db, 1000)["ratio"] == 0.001)


# ---------------- AC-02c A2 文档 Agent 化守卫（空需求 / 垃圾产物 / 合法产物） ----------------

def ac02c(ws: Path):
    import graph as g
    import worker as w

    db = new_db(ws)
    g.RIVER_WS = ws
    g.USAGE_BUFFER = ws / "usage_buffer.jsonl"
    app = g.build_app(g.make_checkpointer(ws / "graph.db"))

    # 场景 1：requirement 为空 → 不调用 Worker，不进入人审，直接失败留痕
    def _never_codex(prompt, workdir, **kw):
        raise AssertionError("requirement 为空时不应调用 Worker")
    w.run_codex = _never_codex
    t1 = ledger.create_task(db, "ac02c-empty")
    cfg1 = {"configurable": {"thread_id": "ac02c-empty"}}
    app.invoke({"task_id": t1, "requirement": "   "}, cfg1)
    check("空需求不进入 G1 人审", app.get_state(cfg1).next == ())
    check("空需求记 requirement_input 门禁失败",
          _count(db, "SELECT COUNT(*) FROM gate_results "
                     "WHERE gate_name='requirement_input' AND passed=0") == 1)
    check("空需求任务终态 gate_failed",
          db.execute("SELECT status FROM tasks WHERE id=?", (t1,)).fetchone()["status"] == "gate_failed")

    # 场景 2：Worker 产物无可解析 Markdown → 门禁失败，不进入人审
    w.run_codex = lambda prompt, workdir, **kw: w.WorkerResult(
        0, "I need more context.\ntokens used\n  5\n", 5, "I need more context.")
    t2 = ledger.create_task(db, "ac02c-bad")
    cfg2 = {"configurable": {"thread_id": "ac02c-bad"}}
    app.invoke({"task_id": t2, "requirement": "add a field"}, cfg2)
    check("垃圾产物不进入 G1 人审", app.get_state(cfg2).next == ())
    check("requirements_doc 门禁失败留痕",
          _count(db, "SELECT COUNT(*) FROM gate_results "
                     "WHERE gate_name='requirements_doc' AND passed=0") == 1)
    check("node_run 以 failed 收尾（无孤儿 running）",
          _count(db, "SELECT COUNT(*) FROM node_runs WHERE task_id=? AND status='failed'", (t2,)) == 1)

    # 场景 3：合法产物 → 落盘 + 进入 G1，node_run 记录 output_path
    w.run_codex = _stub_codex
    t3 = ledger.create_task(db, "ac02c-ok")
    cfg3 = {"configurable": {"thread_id": "ac02c-ok"}}
    app.invoke({"task_id": t3, "requirement": "add a sample field"}, cfg3)
    doc = ws / t3 / "docs" / "REQUIREMENTS.md"
    check("合法产物进入 G1 人审", app.get_state(cfg3).next == ("review_g1",))
    check("REQUIREMENTS.md 已落盘且非空", doc.is_file() and doc.stat().st_size > 0)
    check("node_run 记录 output_path",
          (db.execute("SELECT output_path FROM node_runs WHERE task_id=? AND node_name='requirements'",
                      (t3,)).fetchone() or [None])[0] == str(doc))
    check("templates.lock 已快照", (ws / t3 / "templates.lock").is_file())

    # 场景 4：真实 codex 输出夹杂 INFO 日志行 → 抽取须截断噪声（A2 探针实测缺陷防回归）
    noisy = ("OpenAI Codex v0.167.1\nsession started\n# REQUIREMENTS · task x\n\n## 需求\n- x\n\n"
             "## 验收标准\n- AC-1: y\n"
             "2026-11-12T03:37:12.342466Z  INFO codex_cloud: noise line\n"
             "tokens used\n  5\n")
    doc4 = g._extract_markdown(noisy)
    check("日志行噪声被截断", "INFO" not in doc4 and doc4.endswith("- AC-1: y"), repr(doc4[-60:]))

    # 场景 5：异常超长产物判失败（噪声混入不得静默通过）
    t4 = ledger.create_task(db, "ac02c-long")
    run4 = ledger.start_node_run(db, t4, "requirements")
    check("超长产物判失败", not g._doc_gate(db, t4, "requirements", run4,
                                          "x" * 60000, ("## 需求",)))
    check("超长产物 node_run 收尾 failed",
          db.execute("SELECT status FROM node_runs WHERE id=?", (run4,)).fetchone()["status"] == "failed")

    # 场景 6（G-01）：文档节点以 read-only 沙箱执行，且只读守卫留痕通过
    orig_codex, orig_git = w.run_codex, w.git
    seen: dict = {}

    def _capture(prompt, workdir, **kw):
        seen["sandbox"] = kw.get("sandbox")
        return _stub_codex(prompt, workdir, **kw)

    w.run_codex = _capture
    w.git = lambda workdir, *args: (0, "")
    t5 = ledger.create_task(db, "ac02c-ro")
    cfg5 = {"configurable": {"thread_id": "ac02c-ro"}}
    app.invoke({"task_id": t5, "requirement": "add a sample field"}, cfg5)
    check("需求节点以 read-only 沙箱执行（G-01）", seen.get("sandbox") == "read-only",
          f"sandbox={seen.get('sandbox')}")
    check("只读守卫通过留痕 doc_repo_clean",
          _count(db, "SELECT COUNT(*) FROM gate_results WHERE task_id=? "
                     "AND gate_name='doc_repo_clean' AND passed=1", (t5,)) == 1)

    # 场景 7（G-01）：文档节点改动仓库 → 只读守卫失败，不得进入 G1
    calls = {"n": 0}

    def _dirty_after(workdir, *args):
        calls["n"] += 1
        return (0, "") if calls["n"] == 1 else (0, "?? evil.txt\n")

    w.git = _dirty_after
    w.run_codex = _stub_codex
    t6 = ledger.create_task(db, "ac02c-dirty")
    cfg6 = {"configurable": {"thread_id": "ac02c-dirty"}}
    app.invoke({"task_id": t6, "requirement": "add a sample field"}, cfg6)
    check("文档节点改动仓库 → 不进入 G1", app.get_state(cfg6).next == ())
    check("doc_repo_clean 门禁失败留痕",
          _count(db, "SELECT COUNT(*) FROM gate_results WHERE task_id=? "
                     "AND gate_name='doc_repo_clean' AND passed=0", (t6,)) == 1)
    check("只读越界任务终态 gate_failed",
          db.execute("SELECT status FROM tasks WHERE id=?", (t6,)).fetchone()["status"] == "gate_failed")
    w.run_codex, w.git = orig_codex, orig_git


# ---------------- AC-02d 结构化需求录入（intake）+ DESIGN→lanes 推导 ----------------

def ac02d(ws: Path):
    import copy

    import graph as g
    import intake

    base = {"title": "示例需求", "goal": "让 stats 返回 startedAt",
            "acceptance_criteria": ["返回 ISO 8601 时间", "前端展示启动时间"],
            "scope": {"be": ["apps/api/src/**"]}, "priority": "normal"}

    # 1) 校验拒绝路径（失败不得落库）
    def _task_count() -> int:
        path = ws / "state.db"
        return _count(ledger.connect(path), "SELECT COUNT(*) FROM tasks") if path.exists() else 0

    for label, mutate in (
        ("缺标题", lambda p: p.pop("title")),
        ("空验收标准", lambda p: p.update(acceptance_criteria=[])),
        ("非法优先级", lambda p: p.update(priority="urgent")),
        ("非法 lane", lambda p: p.update(scope={"db": ["x"]})),
    ):
        bad = copy.deepcopy(base)
        mutate(bad)
        before = _task_count()
        try:
            intake.validate_payload(bad)
            rejected = False
        except intake.IntakeError:
            rejected = True
        check(f"intake {label} 被拒绝", rejected)
        check(f"intake {label} 不落库", _task_count() == before)

    # 2) 合法录入：任务 + 归一化文本 + requirement.json
    result = intake.create_task_from_payload(ws / "state.db", ws, base)
    db = ledger.connect(ws / "state.db")
    row = db.execute("SELECT requirement, priority, status FROM tasks WHERE id=?",
                     (result["task_id"],)).fetchone()
    check("intake 任务落库且 pending", row is not None and row["status"] == "pending")
    check("归一化文本含标题与验收标准",
          row["requirement"].startswith("# 示例需求") and "## 验收标准" in row["requirement"])
    check("requirement.json 已落盘", Path(result["path"]).is_file())

    # 3) DESIGN.md 文件归属表 → lanes（intake 任务无需手写脚本）
    task_dir = ws / result["task_id"] / "docs"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "DESIGN.md").write_text(
        "| 路径 | lane |\n| --- | --- |\n| apps/api/src/x.ts | be |\n| apps/web/src/y.tsx | fe |\n",
        encoding="utf-8")
    (task_dir / "REQUIREMENTS.md").write_text("# REQ\n## 需求\n- x\n", encoding="utf-8")
    lanes = g._lanes_from_design(task_dir / "DESIGN.md", task_dir / "REQUIREMENTS.md",
                                 result["task_id"])
    check("DESIGN 归属表推导出 2 个 lane", [l["name"] for l in lanes] == ["be", "fe"])
    check("lane 的 scope/branch/lane_instruction 正确",
          lanes[0]["scope"] == ["apps/api/src/x.ts"]
          and lanes[0]["branch"] == f"feat/{result['task_id'][:8]}-be"
          and "REQ" in lanes[0]["lane_instruction"])
    check("缺 DESIGN.md 时返回空（回退默认 lanes）",
          g._lanes_from_design(ws / "nope.md", None, "x") == [])


# ---------------- AC-02e B1 Worker 租约 / epoch 栅栏（AC-04 首验） ----------------

def ac02e(ws: Path):
    import graph as g

    db = new_db(ws)
    t = ledger.create_task(db, "ac02e")
    run = ledger.start_node_run(db, t, "implement_fe", lane="fe")
    wa = ledger.register_worker(db, role="implement_fe")
    wb = ledger.register_worker(db, role="implement_fe")

    # 1) 首次领取：epoch 从 1 开始，绑定 worker + 到期时间
    e1 = ledger.acquire_lease(db, wa, run)
    row = db.execute("SELECT worker_id, lease_epoch, lease_expires_at FROM node_runs WHERE id=?",
                     (run,)).fetchone()
    check("首次领取租约 epoch=1", e1 == 1)
    check("node_runs 落租约持有者/epoch/到期",
          row["worker_id"] == wa and row["lease_epoch"] == e1 and bool(row["lease_expires_at"]))

    # 2) 存活租约不可被抢占
    held = False
    try:
        ledger.acquire_lease(db, wb, run)
    except ledger.LeaseHeldError:
        held = True
    check("存活租约不可被抢占（LeaseHeldError）", held)

    # 3) 模拟失联（心跳过期）→ 接管者 epoch 严格递增
    db.execute("UPDATE node_runs SET lease_expires_at=datetime('now','-1 seconds') WHERE id=?", (run,))
    db.commit()
    e2 = ledger.acquire_lease(db, wb, run)
    check("过期租约可被接管且 epoch 严格递增", e2 > e1)

    # 4) 旧 epoch 写被拒且不落库（收尾 / 副作用 / 续租三条路径）
    stale_finish = False
    try:
        ledger.finish_node_run(db, run, "done", worker_id=wa, epoch=e1)
    except ledger.StaleLeaseError:
        stale_finish = True
    check("旧 epoch 收尾被拒（StaleLeaseError）", stale_finish)
    check("旧 epoch 拒写后 node_run 仍 running",
          db.execute("SELECT status FROM node_runs WHERE id=?", (run,)).fetchone()["status"] == "running")

    stale_op = False
    try:
        ledger.record_operation(db, t, f"{t}:git_commit:fe", "git_commit", "h1",
                                node_run_id=run, worker_id=wa, lease_epoch=e1)
    except ledger.StaleLeaseError:
        stale_op = True
    check("旧 epoch 副作用被拒", stale_op)
    check("旧 epoch 副作用未落库",
          _count(db, "SELECT COUNT(*) FROM operations WHERE task_id=?", (t,)) == 0)

    stale_renew = False
    try:
        ledger.renew_lease(db, wa, run, e1)
    except ledger.StaleLeaseError:
        stale_renew = True
    check("旧 epoch 续租被拒", stale_renew)

    # 5) 新 epoch 写成功，收尾后无残留 running
    ok = ledger.record_operation(db, t, f"{t}:git_commit:fe", "git_commit", "h1",
                                 node_run_id=run, worker_id=wb, lease_epoch=e2)
    check("新 epoch 副作用写入成功", ok is True)
    ledger.finish_node_run(db, run, "done", worker_id=wb, epoch=e2)
    check("新 epoch 收尾成功",
          db.execute("SELECT status FROM node_runs WHERE id=?", (run,)).fetchone()["status"] == "done")
    check("无残留 running 节点",
          _count(db, "SELECT COUNT(*) FROM node_runs WHERE task_id=? AND status='running'", (t,)) == 0)

    # 6) 已结束节点不可再领租约（失败路径同样不得留 running）
    ended = False
    try:
        ledger.acquire_lease(db, wa, run)
    except ledger.LeaseError:
        ended = True
    check("已结束节点不可再领租约", ended)
    run2 = ledger.start_node_run(db, t, "test")
    wa2 = ledger.register_worker(db, role="test")
    e3 = ledger.acquire_lease(db, wa2, run2)
    ledger.finish_node_run(db, run2, "failed", worker_id=wa2, epoch=e3)
    check("失败路径收尾后无 running",
          _count(db, "SELECT COUNT(*) FROM node_runs WHERE task_id=? AND status='running'", (t,)) == 0)

    # 7) 凭据隔离：每个 Worker 独立 CODEX_HOME，互不影响
    g.RIVER_WS = ws
    base = ws / "codex-home"
    base.mkdir(parents=True, exist_ok=True)
    (base / "auth.json").write_text('{"token":"stub"}', encoding="utf-8")
    (base / "config.toml").write_text('model = "stub"\n', encoding="utf-8")
    h1, h2 = g._worker_codex_home(t, wa), g._worker_codex_home(t, wb)
    check("Worker 凭据目录相互隔离（不同路径）", h1 != h2 and h1.is_dir() and h2.is_dir())
    check("各自独立持有 auth.json/config.toml",
          all((h / n).is_file() for h in (h1, h2) for n in ("auth.json", "config.toml")))
    (h1 / "auth.json").write_text('{"token":"only-a"}', encoding="utf-8")
    check("凭据副本互不影响",
          (h2 / "auth.json").read_text(encoding="utf-8") != '{"token":"only-a"}')


# ---------------- AC-02f B2 完整 outbox（至少一次投递 / 可重放 / 不重复） ----------------

def _retro_payload(cid: str, **kw) -> str:
    base = {"id": cid, "type": "pitfall", "scope": "project", "risk": "low",
            "text": f"B2 outbox 测试条目 {cid}", "source": "AC-02f",
            "evidence": "ac_regression"}
    base.update(kw)
    return json.dumps(base, ensure_ascii=False)


def _enqueue(db: sqlite3.Connection, task_id: str, payload: str):
    db.execute("INSERT INTO retro_queue (task_id, payload) VALUES (?,?)", (task_id, payload))
    db.commit()


def _q_row(db: sqlite3.Connection, cid: str):
    for r in db.execute("SELECT * FROM retro_queue ORDER BY id").fetchall():
        if json.loads(r["payload"])["id"] == cid:
            return r
    return None


def ac02f(ws: Path):
    import memory

    db = new_db(ws)
    t = ledger.create_task(db, "ac02f")
    bad_dir = ws / "not_a_dir"
    bad_dir.write_text("x", encoding="utf-8")  # 父路径是文件 → 投递必败

    # 1) 投递失败：attempts/错误/退避落库，仍 pending（可重放）
    _enqueue(db, t, _retro_payload("C-B2-1"))
    s1 = memory.deliver(db, bad_dir, force=True)
    r = _q_row(db, "C-B2-1")
    check("投递失败仍 pending（可重放）", r["status"] == "pending")
    check("失败后 attempts=1", r["attempts"] == 1)
    check("失败原因落库", bool(r["last_error"]))
    check("失败后进入退避窗口", bool(r["next_attempt_at"]))
    check("失败项归入 retried", s1["retried"] == ["C-B2-1"])

    # 2) 退避窗口内不自动重放；force 可立即重放
    s2 = memory.deliver(db, bad_dir)
    check("退避窗口内不自动重放",
          s2["not_due"] == ["C-B2-1"] and _q_row(db, "C-B2-1")["attempts"] == 1)
    s3 = memory.deliver(db, bad_dir, force=True)
    check("force 立即重放失败项",
          s3["retried"] == ["C-B2-1"] and _q_row(db, "C-B2-1")["attempts"] == 2)

    # 3) 修复后投递成功；重复投递不产生第二条
    good_dir = ws / "proj"
    good_dir.mkdir()
    s4 = memory.deliver(db, good_dir, force=True)
    r = _q_row(db, "C-B2-1")
    check("投递成功后标记 done", s4["delivered"] == ["C-B2-1"] and r["status"] == "done")
    check("记录投递时间与清空错误", bool(r["delivered_at"]) and not r["last_error"])
    md = (good_dir / "AGENTS.md").read_text(encoding="utf-8")
    check("条目写入 AGENTS.md（仅一次）", md.count("[C-B2-1]") == 1)
    s5 = memory.deliver(db, good_dir, force=True)
    md2 = (good_dir / "AGENTS.md").read_text(encoding="utf-8")
    check("重复投递不产生第二条", s5["delivered"] == [] and md2.count("[C-B2-1]") == 1)

    # 4) 崩溃恢复：文件已写、未标 done → 重放按 [id] 去重并补标 done
    _enqueue(db, t, _retro_payload("C-B2-2"))
    with (good_dir / "AGENTS.md").open("a", encoding="utf-8") as f:
        f.write(memory.render_entry({"id": "C-B2-2", "text": "已写未标记",
                                     "source": "AC-02f", "evidence": "crash"}))
    s6 = memory.deliver(db, good_dir, force=True)
    check("崩溃后重放：按 [id] 去重并补标 done",
          s6["skipped_duplicate"] == ["C-B2-2"] and _q_row(db, "C-B2-2")["status"] == "done")
    check("去重未写入第二份",
          (good_dir / "AGENTS.md").read_text(encoding="utf-8").count("[C-B2-2]") == 1)

    # 5) 死信：超 max_attempts 转 dead，不再自动重试
    _enqueue(db, t, _retro_payload("C-B2-3"))
    s7 = memory.deliver(db, bad_dir, force=True, max_attempts=1)
    check("超过上限转死信",
          s7["dead"] == ["C-B2-3"] and _q_row(db, "C-B2-3")["status"] == "dead")
    memory.deliver(db, good_dir, force=True)
    check("死信不再被投递",
          _q_row(db, "C-B2-3")["status"] == "dead"
          and (good_dir / "AGENTS.md").read_text(encoding="utf-8").count("[C-B2-3]") == 0)

    # 6) L1 候选不投递，保留 pending 供人工审核
    _enqueue(db, t, _retro_payload("C-B2-4", scope="platform"))
    s9 = memory.deliver(db, good_dir, force=True)
    check("L1 候选留池不投递",
          s9["deferred"] == ["C-B2-4"] and _q_row(db, "C-B2-4")["status"] == "pending")


# ---------------- AC-02g B2 跨进程投递互斥（两个进程并发投递同一队列） ----------------

def ac02g(ws: Path):
    import subprocess
    import sys as _sys
    import time as _time

    db = new_db(ws)
    t = ledger.create_task(db, "ac02g")
    proj = ws / "conc_proj"
    proj.mkdir()
    n = 40
    for i in range(n):
        _enqueue(db, t, _retro_payload(f"C-CONC-{i:03d}"))
    db.close()  # 释放连接，避免与子进程争锁

    worker = ws / "conc_worker.py"
    platform_dir = Path(__file__).resolve().parent
    worker.write_text(
        "import sys, sqlite3, time\n"
        f"sys.path.insert(0, {str(platform_dir)!r})\n"
        "from pathlib import Path\n"
        "import memory\n"
        "db_path, proj, start_at = sys.argv[1], sys.argv[2], float(sys.argv[3])\n"
        "db = sqlite3.connect(db_path); db.row_factory = sqlite3.Row\n"
        "while time.time() < start_at: time.sleep(0.001)\n"
        "s = memory.deliver(db, Path(proj), force=True)\n"
        "print(len(s['delivered']), len(s['claimed_by_other']), len(s['locked']))\n",
        encoding="utf-8")
    start = _time.time() + 0.8
    procs = [subprocess.Popen(
        [_sys.executable, str(worker), str(ws / "state.db"), str(proj), str(start)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    outs = []
    for p in procs:
        out, err = p.communicate(timeout=180)
        outs.append(out.strip())
        if p.returncode != 0:
            print("   worker stderr:", err.strip()[:300])
    check("两个投递进程均正常退出", all(p.returncode == 0 for p in procs))
    delivered = sum(int(o.split()[0]) for o in outs if o)
    check("每条候选仅被一个进程成功投递一次", delivered == n)

    md = (proj / "AGENTS.md").read_text(encoding="utf-8")
    cnt = [md.count(f"[C-CONC-{i:03d}]") for i in range(n)]
    check("并发投递无重复条目", max(cnt) == 1)
    check("并发投递无丢条目", min(cnt) == 1)
    db2 = ledger.connect(ws / "state.db")
    check("每个条目只被认领一次（attempts=1 且 done）",
          _count(db2, "SELECT COUNT(*) FROM retro_queue WHERE attempts=1 AND status='done'") == n)


# ---------------- AC-02h B3 lanes 独立表迁移与合并顺序 ----------------

def ac02h(ws: Path):
    db = new_db(ws)
    t = ledger.create_task(db, "ac02h-legacy")
    db.execute("DROP TABLE lanes")  # 模拟 B3 前的旧库：lane 只存在于 node_runs
    db.commit()
    legacy = [
        ("r-be-1", "be", "implement_api", "feat/x-be", '["apps/api/src/**"]', "failed", "2026-11-11 08:00:00"),
        ("r-be-2", "be", "implement_api", "feat/x-be", '["apps/api/src/**"]', "done", "2026-11-11 08:05:00"),
        ("r-fe-1", "fe", "implement_web", "feat/x-fe", '["apps/web/src/**"]', "done", "2026-11-11 08:10:00"),
    ]
    for rid, lane, node, branch, scope, status, at in legacy:
        db.execute(
            "INSERT INTO node_runs (id, task_id, node_name, attempt, status, lane, file_scope, branch, "
            "started_at, ended_at) VALUES (?,?,?,1,?,?,?,?,?,?)",
            (rid, t, node, status, lane, scope, branch, at, at))
    db.commit()
    before = _count(db, "SELECT COUNT(DISTINCT lane) FROM node_runs WHERE task_id=?", (t,))
    db.close()

    db = ledger.init_db(ws / "state.db")  # 等价于旧库升级：建表 + 幂等回填
    rows = db.execute("SELECT * FROM lanes WHERE task_id=? ORDER BY merge_order", (t,)).fetchall()
    check("迁移后 lanes 条数 = 旧库 distinct lane 数（不丢数据）", len(rows) == before == 2)
    check("合并顺序按首次出现排列，base_branch 串成前驱链",
          [r["name"] for r in rows] == ["be", "fe"] and rows[1]["base_branch"] == "feat/x-be")
    check("同 lane 多次执行取最后一次（failed→done）为迁移状态", rows[0]["status"] == "done")
    check("分支与 file_scope 原样保留",
          rows[0]["branch"] == "feat/x-be" and rows[1]["file_scope"] == '["apps/web/src/**"]')
    db.close()
    db = ledger.init_db(ws / "state.db")  # 二次迁移必须幂等
    check("重复迁移不重复插入", _count(db, "SELECT COUNT(*) FROM lanes WHERE task_id=?", (t,)) == 2)

    t2 = ledger.create_task(db, "ac02h-plan")
    ledger.register_lanes(db, t2, [
        {"name": "be", "node": "implement_be", "branch": "b-be", "scope": ["apps/api/**"]},
        {"name": "fe", "node": "implement_fe", "branch": "b-fe", "scope": ["apps/web/**"]},
    ], base_branch="base")
    plan = ledger.lane_merge_plan(db, t2)
    check("新任务登记：merge_order 按传入顺序、base_branch 串链",
          [p["name"] for p in plan] == ["be", "fe"] and plan[0]["base_branch"] == "base"
          and plan[1]["base_branch"] == "b-be")
    ledger.set_lane_status(db, t2, "be", "done")
    ledger.mark_lane_merged(db, t2, "be")
    plan = ledger.lane_merge_plan(db, t2)
    check("lane 状态推进与合并时间可查询", plan[0]["status"] == "done" and bool(plan[0]["merged_at"]))
    try:
        ledger.set_lane_status(db, t2, "fe", "bogus")
        ok = False
    except ValueError:
        ok = True
    check("非法 lane 状态被拒", ok)


# ---------------- AC-07 孤儿 worktree 清理 ----------------

def ac07(ws: Path):
    import maintenance as m
    import os
    import time

    db = new_db(ws)
    project = ws / "proj"
    root = project / ".worktrees"
    root.mkdir(parents=True)

    # 活跃任务：running + lane + worktree 登记
    t1 = ledger.create_task(db, "ac07-active")
    ledger.register_lanes(db, t1, [{"name": "be", "node": "implement_be",
                                    "branch": "feat/a-be"}], base_branch="base")
    ledger.start_node_run(db, t1, "implement_be", lane="be", branch="feat/a-be",
                          worktree=str(root / "feat-a-be"))
    # 等待人工：任务挂起，worktree 仅能按 lane 分支名匹配
    t2 = ledger.create_task(db, "ac07-wait")
    ledger.register_lanes(db, t2, [{"name": "fe", "node": "implement_fe",
                                    "branch": "feat/b-fe"}], base_branch="base")
    ledger.set_task_status(db, t2, "awaiting_human")
    # 运行中的节点（任务状态可能尚未刷新）
    t3 = ledger.create_task(db, "ac07-run")
    ledger.start_node_run(db, t3, "implement_fe", lane="fe", worktree=str(root / "wt-live"))

    for name in ("feat-a-be", "feat-b-fe", "wt-live", "fresh"):
        (root / name).mkdir()
    stale = root / "stale"
    stale.mkdir()
    old = time.time() - 48 * 3600
    os.utime(stale, (old, old))

    dry = m.scan_worktrees(db, project)
    check("dry-run 只报告孤儿、不移动目录",
          {i["name"] for i in dry["orphans"]} == {"stale"} and stale.is_dir())
    check("活跃任务/等待人工/运行节点/宽限期目录均保留",
          {k["name"] for k in dry["kept"]} == {"feat-a-be", "feat-b-fe", "wt-live", "fresh"})

    applied = m.scan_worktrees(db, project, apply=True)
    trashed = list((root / ".trash").rglob("stale"))
    check("apply 把孤儿移入 .trash 且保留目录不受影响",
          bool(trashed) and not stale.exists() and (root / "feat-a-be").is_dir()
          and (root / "fresh").is_dir())
    check("移动后复查孤儿清空", not m.scan_worktrees(db, project)["orphans"])
    check("全部动作留痕（孤儿/移动/错误可审计）",
          len(applied["orphans"]) == 1 and len(applied["moved"]) == 1 and not applied["errors"])


# ---------------- AC-08 错误重试分类 ----------------

def ac08(ws: Path):
    import maintenance as m

    db = new_db(ws)

    def fail_run(task, node, attempt, gate_name, output):
        run = ledger.start_node_run(db, task, node, attempt=attempt, lane="be")
        ledger.record_gate_result(db, task, gate_name, False, output, node_run_id=run)
        ledger.finish_node_run(db, run, "failed")

    t1 = ledger.create_task(db, "ac08-timeout")
    fail_run(t1, "implement_be", 1, "unit", "GATE TIMEOUT after 900s: npm test")

    t2 = ledger.create_task(db, "ac08-exhausted")
    fail_run(t2, "implement_be", 2, "typecheck", "error TS2322: type mismatch")

    t3 = ledger.create_task(db, "ac08-executor")
    fail_run(t3, "implement_be", 1, "worker_no_changes", "codex: command not found")

    t4 = ledger.create_task(db, "ac08-human")
    fail_run(t4, "implement_be", 1, "scope_check", "gate stub failed")
    ledger.set_task_status(db, t4, "awaiting_human")

    report = m.collect_retry_items(db, max_retry=2)
    levels = {i["task_id"]: i["level"] for i in report["items"]}
    check("瞬时超时 → L1（同执行器重试）", levels[t1] == "L1")
    check("同节点达尝试阈值 → L3（升级人工）", levels[t2] == "L3")
    check("执行器缺失 → L2（换执行器）", levels[t3] == "L2")
    check("等待人工任务 → L3（即使输出普通）", levels[t4] == "L3")
    check("分级摘要计数一致", report["summary"] == {"L1": 1, "L2": 1, "L3": 2},
          str(report["summary"]))
    check("分类纯函数：429→L1、越界→L3、executor→L2",
          m.classify_failure("429 too many requests")[0] == "L1"
          and m.classify_failure("scope violation: wrote apps/web")[0] == "L3"
          and m.classify_failure("executor crashed")[0] == "L2")
    check("清单每项带证据片段与建议动作",
          all(i["evidence"] and i["action"] for i in report["items"]))


# ---------------- AC-11 记忆审计与回滚 ----------------

def ac11(ws: Path):
    import maintenance as m

    db = new_db(ws)
    project = ws / "proj"
    project.mkdir()
    agents = project / "AGENTS.md"
    agents.write_text(
        "# 项目记忆\n\n## Known Pitfalls\n\n## Project Conventions\n"
        "- [C-100] good entry\n"
        "  来源: task-001 | 证据: log | 回滚: 删除本行\n"
        "- [C-101] entry without source\n"
        "- [C-100] duplicated entry\n"
        "  来源: task-002 | 证据: log | 回滚: 删除本行\n"
        "- [C-999] manual entry\n"
        "  来源: manual | 证据: none | 回滚: 删除本行\n", encoding="utf-8")

    def queue(cid, status):
        payload = {"id": cid, "text": f"text {cid}", "type": "convention",
                   "scope": "project", "risk": "low", "source": "task", "evidence": "log"}
        db.execute("INSERT INTO retro_queue (task_id, payload, status) VALUES (?,?,?)",
                   ("task-001", json.dumps(payload), status))
    queue("C-100", "done")
    queue("C-102", "done")
    queue("C-103", "pending")
    db.commit()

    audit = m.audit_memory(db, project)
    codes = {(f["code"], f["entry"]) for f in audit["findings"]}
    check("缺「来源/回滚」行的条目被标记", ("format", "C-101") in codes)
    check("投递 done 但文件缺失 → missing_entry", ("missing_entry", "C-102") in codes)
    check("文件有条目但队列无记录 → unknown_source", ("unknown_source", "C-999") in codes)
    check("重复 ID 被标记", ("duplicate", "C-100") in codes)

    back = m.rollback_memory(db, project, "C-100", actor="tester")
    text = agents.read_text(encoding="utf-8")
    row = db.execute("SELECT status, last_error FROM retro_queue "
                     "WHERE payload LIKE '%\"C-100\"%'").fetchone()
    check("回滚删除目标条目及其续行，其他条目保留",
          back["ok"] and "[C-100]" not in text and "[C-101]" in text and "[C-999]" in text)
    check("回滚留痕：队列记录转 dropped 且带操作者",
          row["status"] == "dropped" and "tester" in row["last_error"])
    again = m.rollback_memory(db, project, "C-100")
    check("重复回滚被拒且文件不再变化",
          again["ok"] is False and "[C-101]" in agents.read_text(encoding="utf-8"))


# ---------------- ROLE-01 测试角色（独立全量门禁 + 报告） ----------------

def _write_role_config(ws: Path, *, test_cmd: str = "stub-test",
                       deploy_cmd: str | None = None, required: bool = True):
    # 实现节点固定跑 typecheck/unit，测试角色跑 coverage：两套命令分开，
    # 才能只让测试角色失败而不拖垮实现（P0 起实现门禁也读配置）。
    lines = ["tech_stack:", "  gate_commands:", "    typecheck: stub-typecheck",
             "    unit: stub-unit", f"    coverage: {test_cmd}",
             "test:", "  gates: [coverage]", "deploy:", "  staging:",
             f"    required: {'true' if required else 'false'}"]
    if deploy_cmd:
        lines += [f"    commands: [{deploy_cmd}]", "    smoke: stub-smoke",
                  "    healthcheck: stub-health"]
    (ws / "project.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _role_gate(failures: tuple[str, ...] = ()):
    def gate(command, workdir, timeout=900):
        if command in failures:
            return False, f"injected failure: {command}"
        return True, f"ok: {command}"
    return gate


def _role_invoke(ws: Path, app, cfg: dict, task_id: str):
    app.invoke({"task_id": task_id, "requirement": "role", "base_branch": "base",
                "lanes": [{"name": "be", "node": "implement_api", "branch": "b1",
                           "scope": [], "prompt": "p", "commit_msg": "m"}]}, cfg)
    from langgraph.types import Command
    app.invoke(Command(resume="approved"), cfg)  # G1
    app.invoke(Command(resume="approved"), cfg)  # G2 → 实现/测试/部署


def role01(ws: Path):
    import graph as g
    import worker as w
    from langgraph.types import Command

    db = new_db(ws)
    g.RIVER_WS = ws
    g.USAGE_BUFFER = ws / "usage_buffer.jsonl"
    _stub_worker()
    w.run_gate = _role_gate()
    _write_role_config(ws, deploy_cmd="stub-deploy")

    t = ledger.create_task(db, "role01")
    cfg = {"configurable": {"thread_id": "role01"}}
    app = g.build_app(g.make_checkpointer(ws / "graph.db"))
    _role_invoke(ws, app, cfg, t)
    snap = app.get_state(cfg)

    check("测试/部署完成后挂起在 G3", snap.next == ("review_g3",), str(snap.next))
    check("G3 审批物为部署报告", "DEPLOY_REPORT" in (snap.values.get("deploy_report") or ""))
    check("测试节点 done 且门禁入账",
          db.execute("SELECT status FROM node_runs WHERE task_id=? AND node_name='test'",
                     (t,)).fetchone()["status"] == "done"
          and _count(db, "SELECT COUNT(*) FROM gate_results WHERE task_id=? "
                         "AND gate_name='test_coverage' AND passed=1", (t,)) == 1)
    test_report = (ws / t / "docs" / "TEST_REPORT.md").read_text(encoding="utf-8")
    check("测试报告含结果/门禁/提交三段与 PASS",
          all(h in test_report for h in ("## 结果", "## 门禁明细", "## 实现提交"))
          and "PASS" in test_report)
    deploy_report = (ws / t / "docs" / "DEPLOY_REPORT.md").read_text(encoding="utf-8")
    check("部署节点 done、staging 操作入账、报告 PASS",
          db.execute("SELECT status FROM node_runs WHERE task_id=? AND node_name='deploy'",
                     (t,)).fetchone()["status"] == "done"
          and _count(db, "SELECT COUNT(*) FROM operations WHERE task_id=? "
                         "AND op_type='deploy_staging'", (t,)) == 1
          and "PASS" in deploy_report)

    app.invoke(Command(resume="rejected"), cfg)
    snap2 = app.get_state(cfg)
    check("G3 拒绝退回实现并重新验收",
          _count(db, "SELECT COUNT(*) FROM node_runs WHERE task_id=? "
                     "AND node_name='implement_api'", (t,)) >= 2
          and snap2.next == ("review_g3",), str(snap2.next))
    app.invoke(Command(resume="approved"), cfg)
    check("G3 批准后 retro 落账、任务 done",
          db.execute("SELECT status FROM tasks WHERE id=?", (t,)).fetchone()["status"] == "done"
          and _count(db, "SELECT COUNT(*) FROM node_runs WHERE task_id=? "
                         "AND node_name='retro'", (t,)) == 1)


# ---------------- ROLE-02 部署角色（失败/未配置分支） ----------------

def role02(ws: Path):
    import graph as g
    import worker as w

    db = new_db(ws)
    g.RIVER_WS = ws
    g.USAGE_BUFFER = ws / "usage_buffer.jsonl"
    _stub_worker()

    def run(task_id: str, gates) -> None:
        w.run_gate = gates
        cfg = {"configurable": {"thread_id": task_id}}
        app = g.build_app(g.make_checkpointer(ws / "graph.db"))
        _role_invoke(ws, app, cfg, task_id)

    _write_role_config(ws, test_cmd="test-fail", deploy_cmd="stub-deploy")
    t1 = ledger.create_task(db, "role02-test-fail")
    run(t1, _role_gate(("test-fail",)))
    t1_status = db.execute("SELECT status FROM tasks WHERE id=?", (t1,)).fetchone()["status"]
    t1_report = (ws / t1 / "docs" / "TEST_REPORT.md").read_text(encoding="utf-8")
    check("测试失败 → gate_failed(node=test) 且不进入部署",
          t1_status == "gate_failed"
          and _count(db, "SELECT COUNT(*) FROM node_runs WHERE task_id=? "
                         "AND node_name='deploy'", (t1,)) == 0)
    check("失败测试报告保留输出证据",
          "FAIL" in t1_report and "injected failure: test-fail" in t1_report)

    _write_role_config(ws, deploy_cmd="deploy-fail")
    t2 = ledger.create_task(db, "role02-deploy-fail")
    run(t2, _role_gate(("deploy-fail",)))
    t2_status = db.execute("SELECT status FROM tasks WHERE id=?", (t2,)).fetchone()["status"]
    t2_report = (ws / t2 / "docs" / "DEPLOY_REPORT.md").read_text(encoding="utf-8")
    check("部署失败 → gate_failed(node=deploy) 且报告保留失败输出",
          t2_status == "gate_failed"
          and "FAIL" in t2_report and "injected failure: deploy-fail" in t2_report)

    _write_role_config(ws, deploy_cmd=None, required=True)
    t3 = ledger.create_task(db, "role02-required")
    run(t3, _role_gate())
    t3_status = db.execute("SELECT status FROM tasks WHERE id=?", (t3,)).fetchone()["status"]
    t3_report = (ws / t3 / "docs" / "DEPLOY_REPORT.md").read_text(encoding="utf-8")
    check("required=true 但未配置 commands → 转人审且报告 BLOCKED",
          t3_status == "awaiting_human"
          and _count(db, "SELECT COUNT(*) FROM gate_results WHERE task_id=? "
                         "AND gate_name='deploy_config' AND passed=0", (t3,)) == 1
          and "BLOCKED" in t3_report)

    _write_role_config(ws, deploy_cmd=None, required=False)
    t4 = ledger.create_task(db, "role02-skip")
    run(t4, _role_gate())
    t4_status = db.execute("SELECT status FROM tasks WHERE id=?", (t4,)).fetchone()["status"]
    t4_report = (ws / t4 / "docs" / "DEPLOY_REPORT.md").read_text(encoding="utf-8")
    check("required=false → 显式跳过留痕且流程完成",
          t4_status == "done"
          and _count(db, "SELECT COUNT(*) FROM gate_results WHERE task_id=? "
                         "AND gate_name='deploy_skipped' AND passed=1", (t4,)) == 1
          and "SKIPPED" in t4_report)


# ---------------- 外部依赖场景 ----------------

def ac06(ws: Path):
    skip("沙箱越界矩阵（真实 codex 执行，约 8k tokens / 2-3 分钟）",
         "手工执行：见 docs/agentwiki/wiki/P0a确认.md AC-06 记录；结论：越界写/父目录写 BLOCKED，网络与凭据需 CODEX_HOME 隔离（已实施）")


def ac12(ws: Path):
    skip("staging 回退重跑（需 docker + 镜像构建）",
         "手工执行：wsl -d Debian -- bash -lc 'cd /mnt/d/project/river && docker compose -f docker-compose.staging.yml up -d && SMOKE_RESTART=1 bash infra/docker/staging-smoke.sh'")


# ---------------- CONSOLE-01 管理控制台（提需求 / 审批 / 流程视图） ----------------

def console01(ws: Path):
    import dashboard as dash
    import graph as g
    import intake

    db_path = ws / "state.db"
    db = new_db(ws)
    g.RIVER_WS = ws
    g.USAGE_BUFFER = ws / "usage_buffer.jsonl"
    _stub_worker()
    _write_role_config(ws, deploy_cmd="stub-deploy")

    created = intake.create_task_from_payload(db_path, ws, {
        "title": "console", "goal": "drive from web",
        "acceptance_criteria": ["a1"], "non_goals": "",
        "scope": {"be": ["apps/api/src/**"]}, "priority": "normal"})
    tid = created["task_id"]
    runner = dash.ControlRunner(ws, db_path)

    runner.execute(tid, "start")
    check("控制台启动后挂起 G1 且任务转待人工",
          db.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()["status"] == "awaiting_human"
          and _count(db, "SELECT COUNT(*) FROM approvals WHERE task_id=? "
                         "AND gate_name='G1_requirements' AND decision='pending'", (tid,)) == 1)
    snap = dash.snapshot(db_path, ws / "project.yaml")
    check("流程视图标出 G1 待审批与需求产物",
          snap["gate_states"].get(tid, {}).get("G1_requirements") == "pending"
          and any(a["task_id"] == tid and a["gate_name"] == "G1_requirements"
                  for a in snap["pending_approvals"])
          and "G1_requirements" in snap["gate_artifacts"].get(tid, {})
          and snap["task_steps"][tid]["requirements"]["status"] == "done")
    art = dash.artifact_content(db_path, tid, "requirements")
    check("产物接口返回需求文档正文",
          "REQUIREMENTS" in art.get("content", "") and art.get("size", 0) > 0)
    detail = dash.task_detail(db_path, tid)
    check("任务详情含节点时间线与六类产物槽",
          len(detail["node_runs"]) >= 1
          and set(detail["artifacts"]) == {"requirements", "design", "prototype",
                                           "prototype_html", "test", "deploy"})

    runner.execute(tid, "decide", decision="rejected")
    check("G1 驳回回到需求节点并重新挂起",
          _count(db, "SELECT COUNT(*) FROM node_runs WHERE task_id=? "
                     "AND node_name='requirements'", (tid,)) >= 2
          and db.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()["status"] == "awaiting_human")

    runner.execute(tid, "decide", decision="approved")
    check("G1 批准后生成设计并挂起 G2",
          _count(db, "SELECT COUNT(*) FROM approvals WHERE task_id=? "
                     "AND gate_name='G2_design' AND decision='pending'", (tid,)) == 1)

    runner.execute(tid, "decide", decision="approved")
    check("G2 批准后实现/测试/部署完成并挂起 G3",
          _count(db, "SELECT COUNT(*) FROM approvals WHERE task_id=? "
                     "AND gate_name='G3_staging' AND decision='pending'", (tid,)) == 1
          and _count(db, "SELECT COUNT(*) FROM node_runs WHERE task_id=? "
                         "AND node_name IN ('test','deploy') AND status='done'", (tid,)) == 2)

    runner.execute(tid, "decide", decision="approved")
    check("G3 批准后流程结束、任务 done、审批留痕",
          db.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()["status"] == "done"
          and _count(db, "SELECT COUNT(*) FROM approvals WHERE task_id=? "
                         "AND decision='approved'", (tid,)) == 3)

    # HTTP 层：页面入口 + 写端点防护 + 提需求 → 启动 → 审批的数据闭环
    import http.client as http_client
    import threading
    from http.server import ThreadingHTTPServer

    dash.Handler.db_path = db_path
    dash.Handler.yaml_path = ws / "project.yaml"
    dash.Handler.intake_ws = ws
    dash.Handler.intake_enabled = True
    dash.Handler.control_enabled = True
    dash.Handler.runner = runner
    srv = ThreadingHTTPServer(("127.0.0.1", 0), dash.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def req(method: str, path: str, body: dict | None = None, headers: dict | None = None):
        conn = http_client.HTTPConnection("127.0.0.1", port, timeout=15)
        payload = json.dumps(body).encode() if body is not None else None
        head = {"Content-Type": "application/json"} if payload else {}
        head.update(headers or {})
        conn.request(method, path, body=payload, headers=head)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        try:
            return resp.status, json.loads(raw)
        except Exception:
            return resp.status, raw.decode("utf-8", "replace")

    try:
        status, html = req("GET", "/")
        check("控制台页面含提需求入口与待审批区",
              status == 200 and "＋ 新建需求" in html and "待审批" in html)
        status_mode, mode = req("GET", "/api/mode")
        status_js, js = req("GET", "/app.js")
        status_css, css = req("GET", "/styles.css")
        check("前后端分离：静态前端资源与运行模式接口可用",
              status_mode == 200 and mode.get("control") is True
              and status_js == 200 and "getJSON('/api/overview')" in js
              and status_css == 200 and "--brand" in css)
        status_proj, projinfo = req("GET", "/api/projects")
        check("多项目：项目列表接口可用",
              status_proj == 200 and isinstance(projinfo.get("projects"), list)
              and projinfo.get("default") == "river")
        status_bad, _ = req("GET", "/api/overview?project=..%2F..%2Fetc")
        check("多项目：非法/未知项目返回 404", status_bad == 404)
        status_search, found = req("GET", f"/api/search?q={tid[:8]}")
        check("查询：按关键字检索任务",
              status_search == 200
              and any(t["id"] == tid for t in found.get("tasks", [])))
        status, _ = req("POST", "/api/task/start", {"task_id": tid})
        check("缺少 X-Control 的写请求被拒（403）", status == 403)
        status, created2 = req("POST", "/api/intake", {
            "title": "web", "goal": "from console", "acceptance_criteria": ["a1"],
            "non_goals": "", "scope": {"be": ["apps/api/src/**"]}, "priority": "normal",
        }, {"X-Intake": "1"})
        check("网页提需求返回 201 与 task_id",
              status == 201 and bool(created2.get("task_id")))
        tid2 = created2["task_id"]
        status, job = req("POST", "/api/task/start", {"task_id": tid2},
                          {"X-Control": "1"})
        check("网页启动返回 202 job", status == 202 and job.get("status") == "queued")
        state = "queued"
        for _ in range(120):
            _, jobs = req("GET", "/api/jobs")
            if jobs.get("jobs") and jobs["jobs"][0]["status"] in ("done", "failed"):
                state = jobs["jobs"][0]["status"]
                break
            time.sleep(0.1)
        check("后台执行完成并挂起 G1 待审批",
              state == "done"
              and db.execute("SELECT status FROM tasks WHERE id=?", (tid2,)).fetchone()["status"]
              == "awaiting_human")
        status, detail2 = req("GET", f"/api/task/detail?task_id={tid2}")
        check("任务详情接口返回节点与产物",
              status == 200 and detail2["task"]["id"] == tid2
              and detail2["artifacts"]["requirements"]["exists"])
        status, art2 = req("GET", f"/api/artifact?task_id={tid2}&kind=requirements")
        check("产物接口可读需求文档", status == 200 and "REQUIREMENTS" in art2.get("content", ""))
        status, _ = req("POST", "/api/task/decide",
                        {"task_id": tid2, "decision": "maybe"}, {"X-Control": "1"})
        check("非法审批结论被拒（400）", status == 400)
    finally:
        srv.shutdown()
        srv.server_close()
        dash.Handler.runner = None
        dash.Handler.control_enabled = False
        dash.Handler.intake_enabled = False
    db.close()


# ---------------- CONFIG-01 角色配置管理（注释保留写入 + 校验 + 运行时接线） ----------------

def _write_full_config(ws: Path) -> Path:
    """带中文注释的完整基线配置（CONFIG-01 专用；注释用于校验定点替换）。"""
    home = ws / "codex-home"
    home.mkdir(exist_ok=True)
    (home / "auth.json").write_text('{"token":"stub"}', encoding="utf-8")
    text = f"""# 基线配置（CONFIG-01，注释保留校验）
project: river
repo_path: {ws}
workspace: {ws}

executor:
  primary: codex            # 主执行器
  fallback: claude
  codex_home: {home}

model:
  default: gpt-6.1-terra
  degraded: gpt-6.1-sol
  strong_roles: [requirements, design, review]   # 永不降级

budget:
  unit: calls
  monthly_quota: 20000
  max_usage_per_task: 200
  max_inflight_tasks: 1
  thresholds: {{alert: 0.80, degraded: 0.90, paused: 1.00}}

gates:
  G1_requirements: {{approver: user, sla_hours: 4}}
  G2_design:       {{approver: user, sla_hours: 4}}
  G3_staging:      {{approver: user, sla_hours: 4}}

tech_stack:
  gate_commands:
    typecheck: npm run typecheck
    unit: npm run test:unit
    coverage: npm run test:coverage

test:
  gates: [typecheck, unit]

deploy:
  staging:
    required: true
    commands:
      # 单会话约束注释
      - 'stub-deploy'
    healthcheck: http://localhost:8080/api/stats
    timeout_seconds: 900
"""
    path = ws / "project.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def config01(ws: Path):
    import os
    import config_store
    import dashboard as dash
    import graph as g

    path = _write_full_config(ws)
    os.environ.pop("RIVER_CODEX_HOME", None)  # 确保读 YAML 的 codex_home

    def rejected(updates: dict, desc: str):
        before = path.read_text(encoding="utf-8")
        try:
            config_store.apply_updates(path, updates, "tester")
            ok = False
        except config_store.ConfigError:
            ok = True
        check(desc, ok and path.read_text(encoding="utf-8") == before)

    rejected({"budget.thresholds.alert": 0.95}, "阈值倒挂（alert ≥ degraded）被拒且文件不变")
    rejected({"test.gates": ["typecheck", "nope"]}, "test.gates 引用未定义门禁被拒")
    rejected({"deploy.staging.commands": []}, "required=true 且 commands 为空被拒")
    rejected({"budget.does_not_exist": 1}, "未知配置项被拒（不新建节点）")

    result = config_store.apply_updates(path, {
        "budget.monthly_quota": 25000,
        "budget.max_usage_per_task": 180,
        "budget.thresholds.alert": 0.7,
        "model.strong_roles": ["requirements", "design"],
        "test.gates": ["typecheck"],
        "deploy.staging.commands": ["stub-deploy-v2"],
        "deploy.staging.timeout_seconds": 120,
        "gates.G1_requirements.approver": "alice",
    }, "tester")
    saved = config_store.load(path)
    text = path.read_text(encoding="utf-8")
    check("合法保存写入新值（预算/模型/门禁/部署/审批人）",
          saved["budget"]["monthly_quota"] == 25000
          and saved["budget"]["max_usage_per_task"] == 180
          and saved["budget"]["thresholds"]["alert"] == 0.7
          and saved["model"]["strong_roles"] == ["requirements", "design"]
          and saved["test"]["gates"] == ["typecheck"]
          and saved["deploy"]["staging"]["commands"] == ["stub-deploy-v2"]
          and saved["deploy"]["staging"]["timeout_seconds"] == 120
          and saved["gates"]["G1_requirements"]["approver"] == "alice")
    check("中文注释与未改动键仍在（定点替换，不整文件重写）",
          "# 基线配置（CONFIG-01，注释保留校验）" in text
          and "# 永不降级" in text
          and "# 主执行器" in text
          and "max_inflight_tasks: 1" in text
          and "unit: npm run test:unit" in text)
    audit_file = ws / "config-audit.jsonl"
    entries = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    check("变更追加审计（时间/actor/旧值→新值）",
          bool(entries) and entries[-1]["actor"] == "tester"
          and any(c["path"] == "budget.monthly_quota" and c["from"] == 20000
                  and c["to"] == 25000 for c in entries[-1]["changes"])
          and set(result["changed"]) >= {"budget.monthly_quota", "test.gates"})

    g.RIVER_WS = ws
    g.USAGE_BUFFER = ws / "usage_buffer.jsonl"
    g.refresh_runtime_config()
    check("运行时读取预算：QUOTA / U 生效",
          g.QUOTA == 25000 and g.TASK_CALL_LIMIT == 180)
    check("阈值配置接入判定：取自 project.yaml（alert=0.7）",
          ledger.THRESHOLDS["alert"] == 0.7 and ledger.THRESHOLDS["paused"] == 1.0)
    dash_cfg = dash.load_budget_config(path)
    check("并发上限与闸门 SLA 可被控制台解析",
          dash_cfg["max_inflight"] == 1 and dash_cfg["slas"].get("G1_requirements") == 4.0)
    idb = ledger.init_db(ws / "inflight.db")
    t1 = ledger.create_task(idb, "inflight-1")
    t2 = ledger.create_task(idb, "inflight-2")
    ledger.set_task_status(idb, t1, "running")
    ledger.set_task_status(idb, t2, "awaiting_human")
    check("并发判定：running / awaiting_human 计入 in-flight（可排除自身）",
          sorted(ledger.inflight_tasks(idb)) == sorted([t1, t2])
          and ledger.inflight_tasks(idb, exclude=t1) == [t2])
    ledger.configure_thresholds({})  # 复位，避免影响后续用例
    check("阈值缺省时回退默认值（不继承上一项目的配置）",
          ledger.THRESHOLDS == {"alert": 0.8, "degraded": 0.9, "paused": 1.0})
    check("模型路由：强角色不降级、普通角色降级",
          g.route_model("DEGRADED", "requirements") == "gpt-6.1-terra"
          and g.route_model("DEGRADED", "implement") == "gpt-6.1-sol"
          and g.route_model("NORMAL", "implement") == "gpt-6.1-terra")
    check("repo_path / codex_home 来自配置",
          g._repo_path() == str(ws) and g._base_codex_home() == ws / "codex-home")

    # HTTP 层：只读可看 / 写保护 / 非法 400 不落盘 / 合法 200 并刷新运行时
    import http.client as http_client
    import threading
    from http.server import ThreadingHTTPServer

    dash.Handler.db_path = ws / "state.db"
    dash.Handler.yaml_path = path
    dash.Handler.intake_ws = ws
    dash.Handler.intake_enabled = True
    dash.Handler.control_enabled = True
    dash.Handler.runner = None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), dash.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def req(method: str, target: str, body: dict | None = None, headers: dict | None = None):
        conn = http_client.HTTPConnection("127.0.0.1", port, timeout=15)
        raw = json.dumps(body).encode() if body is not None else None
        head = {"Content-Type": "application/json"} if raw else {}
        head.update(headers or {})
        conn.request(method, target, body=raw, headers=head)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        try:
            return resp.status, json.loads(data)
        except Exception:
            return resp.status, data.decode("utf-8", "replace")

    try:
        status, data = req("GET", "/api/config")
        check("配置接口返回当前值与可选项",
              status == 200 and data["config"]["budget"]["monthly_quota"] == 25000
              and "typecheck" in data["meta"]["gate_commands"]
              and data["editable"] is True and bool(data["file"]))
        status, _ = req("POST", "/api/config", {"updates": {"budget.monthly_quota": 26000}})
        check("缺少 X-Control 的配置写入被拒（403）", status == 403)
        before = path.read_text(encoding="utf-8")
        status, data = req("POST", "/api/config",
                           {"updates": {"budget.thresholds.paused": 0.5}}, {"X-Control": "1"})
        check("非法配置经 HTTP 返回 400 且文件不变",
              status == 400 and path.read_text(encoding="utf-8") == before)
        status, data = req("POST", "/api/config", {
            "updates": {"budget.monthly_quota": 26000,
                        "deploy.staging.timeout_seconds": 600},
            "by": "console"}, {"X-Control": "1"})
        check("合法配置经 HTTP 保存并刷新运行时",
              status == 200 and set(data["changed"]) == {"budget.monthly_quota",
                                                         "deploy.staging.timeout_seconds"}
              and config_store.load(path)["budget"]["monthly_quota"] == 26000
              and data["runtime_refreshed"] and g.QUOTA == 26000)
        status, page = req("GET", "/config")
        status_js, page_js = req("GET", "/settings.js")
        check("设置页入口可访问且含保存与预算字段",
              status == 200 and "设置" in page
              and status_js == 200 and "保存配置" in page_js
              and "budget.monthly_quota" in page_js)
    finally:
        srv.shutdown()
        srv.server_close()
        dash.Handler.control_enabled = False
        dash.Handler.intake_enabled = False


# ---------------- PROTOTYPE-01 原型/UI 角色（触发/跳过/校验/审批绑定） ----------------

_PROTO_LANES = [{"name": "fe", "node": "implement_web", "branch": "b1",
                 "scope": [], "prompt": "p", "commit_msg": "m"}]


def _proto_task(db, ws: Path, name: str, lanes=_PROTO_LANES):
    import graph as g
    app = g.build_app(g.make_checkpointer(ws / "graph.db"))
    cfg = {"configurable": {"thread_id": name}}
    task_id = ledger.create_task(db, name)
    app.invoke({"task_id": task_id, "requirement": "prototype",
                "base_branch": "base", "lanes": lanes}, cfg)
    return app, cfg, task_id


def prototype01(ws: Path):
    import graph as g
    import worker as w
    from langgraph.types import Command

    db = new_db(ws)
    g.RIVER_WS = ws
    g.USAGE_BUFFER = ws / "usage_buffer.jsonl"
    _stub_worker()
    _write_role_config(ws, deploy_cmd="stub-deploy")

    fe_table = "## 文件归属表\n| 路径 | lane |\n| --- | --- |\n| apps/web/src/a.tsx | fe |\n"
    be_table = "## 文件归属表\n| 路径 | lane |\n| --- | --- |\n| apps/api/src/a.ts | be |\n"
    check("判定：fe lane 触发原型",
          g._needs_prototype(fe_table, "")[0] is True)
    check("判定：界面关键词触发原型",
          g._needs_prototype("## 实现策略\n去掉首页提示", "")[0] is True)
    check("判定：纯后端任务跳过原型",
          g._needs_prototype(be_table, "接口字段调整")[0] is False)
    check("判定：否定表述不误判（be 表 + 不涉及界面）",
          g._needs_prototype(be_table, "不涉及任何界面，前端无变化")[0] is False)
    check("判定：fe lane 优先于否定表述",
          g._needs_prototype(fe_table, "不涉及界面")[0] is True)
    check("判定：lane 列位置无关（按表头定位）",
          g._needs_prototype(
              "## 文件归属表\n| lane | 路径 |\n| --- | --- |\n| 前端 | apps/web/src/a.tsx |\n",
              "")[0] is True)
    check("判定：缺 lane 列返回 None（门禁失败，不猜列位）",
          g._needs_prototype(
              "## 文件归属表\n| 路径 | 说明 |\n| --- | --- |\n"
              "| apps/web/src/a.tsx | 页面 |\n", "")[0] is None)
    check("判定：未识别 lane 值回退关键词",
          g._needs_prototype(
              "## 文件归属表\n| 路径 | lane |\n| --- | --- |\n"
              "| apps/web/src/a.tsx | db |\n", "首页按钮")[0] is True)

    # 1) 界面任务：design → prototype → G2，产物落专属目录
    app, cfg, t = _proto_task(db, ws, "prototype-gen")
    app.invoke(Command(resume="approved"), cfg)  # G1 → design → prototype → G2
    snap = app.get_state(cfg)
    proto = ws / t / "prototype"
    check("界面任务挂起在 G2 且原型目录落盘",
          snap.next == ("review_g2",) and (proto / "index.html").is_file()
          and (proto / "PROTOTYPE.md").is_file())
    check("原型节点 done 且产物门禁入账",
          db.execute("SELECT status FROM node_runs WHERE task_id=? AND node_name='prototype'",
                     (t,)).fetchone()["status"] == "done"
          and _count(db, "SELECT COUNT(*) FROM gate_results WHERE task_id=? "
                         "AND gate_name='prototype_files' AND passed=1", (t,)) == 1)
    design = snap.values.get("design_doc")
    check("G2 审批摘要绑定设计文档 + 原型目录",
          ledger.artifact_digest([design, str(proto)]) != ledger.artifact_digest([design]))
    before = ledger.artifact_digest([str(proto)])
    (proto / "index.html").write_text(
        "<!DOCTYPE html><html><body><h1>changed</h1></body></html>", encoding="utf-8")
    check("原型文件变更改变产物摘要（旧审批失效语义）",
          ledger.artifact_digest([str(proto)]) != before)

    # 2) 无界面变化：留痕跳过并继续到 G2
    orig = g._needs_prototype
    g._needs_prototype = lambda d, r: (False, "test: 无界面变化")
    try:
        app2, cfg2, t2 = _proto_task(db, ws, "prototype-skip")
        app2.invoke(Command(resume="approved"), cfg2)
        snap2 = app2.get_state(cfg2)
        note = ws / t2 / "prototype" / "PROTOTYPE.md"
        check("跳过路径写说明并进入 G2",
              snap2.next == ("review_g2",) and note.is_file()
              and "跳过原型" in note.read_text(encoding="utf-8")
              and _count(db, "SELECT COUNT(*) FROM gate_results WHERE task_id=? "
                             "AND gate_name='prototype_skipped' AND passed=1", (t2,)) == 1)
        check("跳过路径不产生 index.html", not (ws / t2 / "prototype" / "index.html").exists())

        # 2b) 归属表缺 lane 列：契约不明 → 门禁失败，不猜列位、不进入 G2
        g._needs_prototype = lambda d, r: (None, "test: 归属表缺 lane 列")
        app3, cfg3, t3 = _proto_task(db, ws, "prototype-nolane")
        app3.invoke(Command(resume="approved"), cfg3)
        snap3 = app3.get_state(cfg3)
        check("缺 lane 列 → prototype 门禁失败并阻断 G2",
              snap3.next == ()
              and db.execute("SELECT status FROM tasks WHERE id=?", (t3,)).fetchone()["status"]
              == "gate_failed"
              and _count(db, "SELECT COUNT(*) FROM gate_results WHERE task_id=? "
                             "AND gate_name='prototype_scope' AND passed=0", (t3,)) == 1)
        check("缺 lane 列时不生成原型产物", not (ws / t3 / "prototype").exists())
    finally:
        g._needs_prototype = orig

    # 3) 产物不合格：节点失败并阻断流程
    good = w.run_codex

    def _bad(prompt, workdir, **kw):
        import re
        if "You are the Prototype Agent" in prompt:
            target = Path(re.search(r"Prototype directory:\s*(.+)", prompt).group(1).strip())
            target.mkdir(parents=True, exist_ok=True)
            (target / "index.html").write_text("<html></html>", encoding="utf-8")
            return w.WorkerResult(0, "bad prototype\n", 1, "stub")
        return good(prompt, workdir, **kw)

    w.run_codex = _bad
    try:
        app3, cfg3, t3 = _proto_task(db, ws, "prototype-bad")
        # 预置上一轮"看似合格"的产物，验证新一轮生成前会清空专属目录（P0 残留修复）
        stale = ws / t3 / "prototype"
        stale.mkdir(parents=True, exist_ok=True)
        (stale / "PROTOTYPE.md").write_text(
            "## 原型说明\n" + "上一轮旧产物\n" * 12 + "## 验收映射\n- AC-1\n## 局限\n无\n",
            encoding="utf-8")
        app3.invoke(Command(resume="approved"), cfg3)
        snap3 = app3.get_state(cfg3)
        check("原型产物不合格 → 节点失败、任务 gate_failed 且不进 G2",
              snap3.next == ()
              and db.execute("SELECT status FROM tasks WHERE id=?",
                             (t3,)).fetchone()["status"] == "gate_failed"
              and _count(db, "SELECT COUNT(*) FROM gate_results WHERE task_id=? "
                             "AND gate_name='prototype_files' AND passed=0", (t3,)) == 1)
        check("新一轮生成清空上一轮残留（旧 PROTOTYPE.md 不参与门禁）",
              not (stale / "PROTOTYPE.md").exists())
    finally:
        w.run_codex = good


# ---------------- PROMPT-01 模板六段式 / 契约门禁 / lane 范围硬校验 ----------------

_SIX_SECTIONS = ("## Role", "## Context", "## Task", "## Constraints",
                 "## Output contract", "## Self-check")


def prompt01(ws: Path):
    import graph as g
    import worker as w
    from langgraph.types import Command
    from templates import render as tmpl_render

    db = new_db(ws)
    g.RIVER_WS = ws
    g.USAGE_BUFFER = ws / "usage_buffer.jsonl"
    (ws / "project.yaml").write_text(
        "tech_stack:\n  type: npm-workspaces\n  web: apps/web\n  api: apps/api\n"
        "  gate_commands:\n    typecheck: stub-typecheck\n", encoding="utf-8")
    (ws / "AGENTS.md").write_text(
        "## Project Conventions\n- [C-X] stub memory entry\n", encoding="utf-8")

    # 1) 上下文注入：技术栈/目录/门禁命令 + 项目记忆
    ctx = g._project_context()
    check("上下文注入含技术栈/目录/门禁命令",
          all(k in ctx for k in ("npm-workspaces", "apps/web", "apps/api", "stub-typecheck")))
    check("上下文注入含项目记忆条目", "[C-X]" in ctx)

    # 2) 五个模板统一六段式，且不再硬编码 C-001 副本
    rendered = {
        "requirements-gen": tmpl_render("requirements-gen", task_id="t1",
                                        raw_requirement="r", project_context=ctx),
        "design-gen": tmpl_render("design-gen", task_id="t1",
                                  requirements_content="r", project_context=ctx),
        "prototype-gen": tmpl_render("prototype-gen", task_id="t1", requirements_content="r",
                                     design_content="d", prototype_dir="p",
                                     project_context=ctx),
        "implement-lane": tmpl_render("implement-lane", branch="b", lane_instruction="i",
                                      scope=["apps/api/src/**"], task_id="t1",
                                      project_context=ctx),
        "gate-fix": tmpl_render("gate-fix", gate_cmd="g", gate_output_tail="o",
                                failed_files=["apps/api/src/index.ts"], attempt=1,
                                project_context=ctx),
    }
    for name, text in rendered.items():
        check(f"{name} 六段结构完整", all(h in text for h in _SIX_SECTIONS))
    check("模板不再硬编码 C-001（改由项目记忆注入）",
          not any("esbuild spawn EPERM" in t for t in rendered.values()))
    check("implement-lane 注入 lane scope 与项目记忆",
          "apps/api/src/**" in rendered["implement-lane"] and "[C-X]" in rendered["implement-lane"])
    check("gate-fix 注入失败文件与 attempt",
          "apps/api/src/index.ts" in rendered["gate-fix"] and "Attempt: 1" in rendered["gate-fix"])

    # 3) 契约即校验：design 缺「验收映射」被拒；AC 未覆盖被拒
    t_doc = ledger.create_task(db, "prompt01-doc")
    run = ledger.start_node_run(db, t_doc, "design")
    check("design 缺验收映射被门禁拒绝",
          not g._doc_gate(db, t_doc, "design", run,
                          "# D\n## 文件归属表\n| a | be |\n## 实现策略\nx\n",
                          ("## 文件归属表", "## 实现策略", "## 验收映射")))
    req = "# R\n## 验收标准\n- AC-1: a\n- AC-2: b\n"
    ok, why = g._ac_coverage("# D\n## 验收映射\n- AC-1 → apps/api/src/index.ts\n", req)
    check("design 未覆盖 AC-2 被拒", not ok and "AC-2" in why)
    ok2, _ = g._ac_coverage("# D\n## 验收映射\n- AC-1 → a\n- AC-2 → b\n", req)
    check("AC 全覆盖通过", ok2)

    # 4) lane 范围硬校验：be lane 改了 apps/web → lane_scope 失败且不得提交
    _stub_worker()
    _write_role_config(ws, deploy_cmd="stub-deploy")

    def _web_git(workdir, *args):
        if args[:2] == ("status", "--porcelain"):
            return (0, "M  apps/web/src/app.tsx\n")
        return (0, "")
    w.git = _web_git

    t_scope = ledger.create_task(db, "prompt01-scope")
    cfg = {"configurable": {"thread_id": "prompt01-scope"}}
    app = g.build_app(g.make_checkpointer(ws / "graph.db"))
    app.invoke({"task_id": t_scope, "requirement": "scope", "base_branch": "base",
                "lanes": [{"name": "be", "node": "implement_api", "branch": "b1",
                           "scope": ["apps/api/src/**"], "prompt": "p", "commit_msg": "m"}]},
               cfg)
    app.invoke(Command(resume="approved"), cfg)  # G1
    app.invoke(Command(resume="approved"), cfg)  # G2 → implement
    check("越界改动 → lane_scope 门禁失败、任务 gate_failed、无提交",
          db.execute("SELECT status FROM tasks WHERE id=?", (t_scope,)).fetchone()["status"]
          == "gate_failed"
          and _count(db, "SELECT COUNT(*) FROM gate_results WHERE task_id=? "
                         "AND gate_name='lane_scope' AND passed=0", (t_scope,)) == 1
          and _count(db, "SELECT COUNT(*) FROM operations WHERE task_id=?", (t_scope,)) == 0)

    # 4b) 真实 git 的未暂存格式（首列空格）不得被整体 strip 破坏路径解析
    def _unstaged_git(workdir, *args):
        if args[:2] == ("status", "--porcelain"):
            return (0, " M apps/api/src/index.ts\n")
        return (0, "")
    w.git = _unstaged_git
    t_unstaged = ledger.create_task(db, "prompt01-unstaged")
    cfg_u = {"configurable": {"thread_id": "prompt01-unstaged"}}
    app_u = g.build_app(g.make_checkpointer(ws / "graph.db"))
    app_u.invoke({"task_id": t_unstaged, "requirement": "unstaged", "base_branch": "base",
                  "lanes": [{"name": "be", "node": "implement_api", "branch": "b1",
                             "scope": ["apps/api/src/**"], "prompt": "p", "commit_msg": "m"}]},
                 cfg_u)
    app_u.invoke(Command(resume="approved"), cfg_u)  # G1
    app_u.invoke(Command(resume="approved"), cfg_u)  # G2 → implement
    check("未暂存格式（ M ）路径解析正确：lane 未被误判越界并完成提交",
          _count(db, "SELECT COUNT(*) FROM gate_results WHERE task_id=? "
                     "AND gate_name='lane_scope' AND passed=0", (t_unstaged,)) == 0
          and _count(db, "SELECT COUNT(*) FROM operations WHERE task_id=? "
                         "AND op_type='git_commit'", (t_unstaged,)) == 1
          and db.execute("SELECT status FROM tasks WHERE id=?", (t_unstaged,)).fetchone()["status"]
          != "gate_failed")


# ---------------- REVIEW-01 C 层自动审查 + 复盘生成 ----------------

def review01(ws: Path):
    import graph as g
    from langgraph.types import Command

    db = new_db(ws)
    g.RIVER_WS = ws
    g.USAGE_BUFFER = ws / "usage_buffer.jsonl"
    (ws / "project.yaml").write_text(
        "tech_stack:\n"
        "  gate_commands:\n"
        "    typecheck: stub-typecheck\n"
        "    unit: stub-unit\n"
        "test:\n"
        "  gates: [typecheck, unit]\n"
        "deploy:\n"
        "  staging:\n"
        "    required: true\n"
        "    commands: [stub-deploy]\n"
        "    healthcheck: stub-health\n"
        "review:\n"
        "  codex: true\n"
        "retro:\n"
        "  generate: true\n", encoding="utf-8")
    # 本用例只验证 FAIL 终止语义，关闭返工（返工由 REVIEW-02 覆盖）
    (ws / "rules.yaml").write_text("review:\n  rework_attempts: 0\n", encoding="utf-8")

    seen: dict = {}

    def _run(task_id: str, thread: str):
        import worker as wk
        _stub_worker()
        orig = wk.run_codex

        def _capture(prompt, workdir, **kw):
            if "You are the Review Agent" in prompt:
                seen["sandbox"] = kw.get("sandbox")
            return orig(prompt, workdir, **kw)

        wk.run_codex = _capture
        app = g.build_app(g.make_checkpointer(ws / "graph.db"))
        cfg = {"configurable": {"thread_id": thread}}
        app.invoke({"task_id": task_id, "requirement": "review01", "base_branch": "base",
                    "lanes": [{"name": "be", "node": "implement_api", "branch": "b1",
                               "scope": ["apps/api/src/**"], "prompt": "p", "commit_msg": "m"}]},
                   cfg)
        app.invoke(Command(resume="approved"), cfg)  # G1
        app.invoke(Command(resume="approved"), cfg)  # G2 → implement → review → test → deploy
        return app, cfg

    _REVIEW_VERDICT["value"] = "PASS"
    t = ledger.create_task(db, "review01-pass")
    app, cfg = _run(t, "review01-pass")
    check("审查通过后继续到 G3（不被审查节点截断）", "review_g3" in app.get_state(cfg).next)
    app.invoke(Command(resume="approved"), cfg)  # G3 → retro
    docs = ws / t / "docs"
    check("自动审查落 REVIEW.md 且结论 PASS",
          (docs / "REVIEW.md").is_file()
          and "## 结论\nPASS" in (docs / "REVIEW.md").read_text(encoding="utf-8"))
    check("codex 审查产物单独落盘（REVIEW-agent.md）",
          (docs / "REVIEW-agent.md").is_file()
          and "## 建议" in (docs / "REVIEW-agent.md").read_text(encoding="utf-8"))
    check("review 以 read-only 沙箱执行（G-01 同类项）",
          seen.get("sandbox") == "read-only", f"sandbox={seen.get('sandbox')}")
    check("review 节点 done + 机械门禁全部通过",
          db.execute("SELECT status FROM node_runs WHERE task_id=? AND node_name='review'",
                     (t,)).fetchone()["status"] == "done"
          and _count(db, "SELECT COUNT(*) FROM gate_results WHERE task_id=? "
                         "AND gate_name LIKE 'review_%' AND passed=1", (t,)) == 4)
    check("复盘生成 RETRO.md 且 retro_doc 门禁通过",
          (docs / "RETRO.md").is_file()
          and "## 经验" in (docs / "RETRO.md").read_text(encoding="utf-8")
          and _count(db, "SELECT COUNT(*) FROM gate_results WHERE task_id=? "
                         "AND gate_name='retro_doc' AND passed=1", (t,)) == 1)
    check("任务终态 done", db.execute(
        "SELECT status FROM tasks WHERE id=?", (t,)).fetchone()["status"] == "done")

    _REVIEW_VERDICT["value"] = "FAIL"
    t2 = ledger.create_task(db, "review01-fail")
    app2, cfg2 = _run(t2, "review01-fail")
    check("审查 FAIL → 任务 gate_failed，未进入 test",
          db.execute("SELECT status FROM tasks WHERE id=?", (t2,)).fetchone()["status"]
          == "gate_failed"
          and _count(db, "SELECT COUNT(*) FROM node_runs WHERE task_id=? AND node_name='test'",
                     (t2,)) == 0
          and _count(db, "SELECT COUNT(*) FROM gate_results WHERE task_id=? "
                         "AND gate_name='review_agent' AND passed=0", (t2,)) == 1)
    _REVIEW_VERDICT["value"] = "PASS"


# ---------------- REVIEW-02 审查失败 → 自动返工到 implement ----------------

def review02(ws: Path):
    import graph as g
    from langgraph.types import Command

    db = new_db(ws)
    g.RIVER_WS = ws
    g.USAGE_BUFFER = ws / "usage_buffer.jsonl"
    (ws / "project.yaml").write_text(
        "tech_stack:\n"
        "  gate_commands:\n"
        "    typecheck: stub-typecheck\n"
        "    unit: stub-unit\n"
        "test:\n"
        "  gates: [typecheck, unit]\n"
        "deploy:\n"
        "  staging:\n"
        "    required: true\n"
        "    commands: [stub-deploy]\n"
        "    healthcheck: stub-health\n"
        "review:\n"
        "  codex: true\n", encoding="utf-8")
    (ws / "rules.yaml").write_text("review:\n  rework_attempts: 1\n", encoding="utf-8")

    _stub_worker()
    _PROMPTS.clear()
    _REVIEW_VERDICT["value"] = "FAIL"
    try:
        t = ledger.create_task(db, "review02")
        app = g.build_app(g.make_checkpointer(ws / "graph.db"))
        cfg = {"configurable": {"thread_id": "review02"}}
        app.invoke({"task_id": t, "requirement": "review02", "base_branch": "base",
                    "lanes": [{"name": "be", "node": "implement_api", "branch": "b1",
                               "scope": ["apps/api/src/**"], "prompt": "p", "commit_msg": "m"}]},
                   cfg)
        app.invoke(Command(resume="approved"), cfg)  # G1
        app.invoke(Command(resume="approved"), cfg)  # G2 → implement → review FAIL → 返工
    finally:
        _REVIEW_VERDICT["value"] = "PASS"

    check("审查失败触发返工：implement 二次执行",
          _count(db, "SELECT COUNT(*) FROM node_runs WHERE task_id=? "
                     "AND node_name='implement_api'", (t,)) == 2)
    check("返工超限后 gate_failed 且未进入 test",
          db.execute("SELECT status FROM tasks WHERE id=?", (t,)).fetchone()["status"]
          == "gate_failed"
          and _count(db, "SELECT COUNT(*) FROM node_runs WHERE task_id=? AND node_name='test'",
                     (t,)) == 0)
    check("返工审批留痕：首次允许、二次拒绝",
          [r["passed"] for r in db.execute(
              "SELECT passed FROM gate_results WHERE task_id=? AND gate_name='review_rework' "
              "ORDER BY id", (t,))] == [1, 0])
    check("返工指令注入上轮审查意见",
          any("上轮审查未通过" in p for p in _PROMPTS[1:]))
    check("返工后再次审查（review 二次执行且均失败）",
          _count(db, "SELECT COUNT(*) FROM node_runs WHERE task_id=? AND node_name='review' "
                     "AND status='failed'", (t,)) == 2)

    # 真实任务 d89a7cc3 暴露的回归场景：历史 review 已超限，手动从 implement 重跑
    # 必须开启新一轮返工预算，而不是被全任务历史 attempt 阻断。
    _REVIEW_VERDICT["value"] = "FAIL"
    try:
        rerun_app = g.build_app(g.make_checkpointer(ws / "graph.db"), entry="implement")
        g.reset_task_for_rerun(t, "implement")
        rerun_app.invoke(g.rerun_payload(t, "implement"), cfg)
    finally:
        _REVIEW_VERDICT["value"] = "PASS"
    check("手动重跑清零返工预算：新一轮仍执行两次 implement/review",
          _count(db, "SELECT COUNT(*) FROM node_runs WHERE task_id=? "
                     "AND node_name='implement_api'", (t,)) == 4
          and _count(db, "SELECT COUNT(*) FROM node_runs WHERE task_id=? AND node_name='review'",
                     (t,)) == 4)
    check("各轮返工门禁独立留痕（允许/拒绝 → 允许/拒绝）",
          [r["passed"] for r in db.execute(
              "SELECT passed FROM gate_results WHERE task_id=? AND gate_name='review_rework' "
              "ORDER BY id", (t,))] == [1, 0, 1, 0])


# ---------------- NODE-01 规则外置 + test/deploy 租约 + 指定节点重跑 ----------------

def node01(ws: Path):
    import graph as g
    from langgraph.types import Command

    db = new_db(ws)
    g.RIVER_WS = ws
    g.USAGE_BUFFER = ws / "usage_buffer.jsonl"
    (ws / "project.yaml").write_text(
        "tech_stack:\n"
        "  gate_commands:\n"
        "    typecheck: stub-typecheck\n"
        "    unit: stub-unit\n"
        "test:\n"
        "  gates: [typecheck, unit]\n"
        "deploy:\n"
        "  staging:\n"
        "    required: true\n"
        "    commands: [stub-deploy]\n"
        "    healthcheck: stub-health\n"
        "review:\n"
        "  codex: false\n", encoding="utf-8")

    _stub_worker()
    t = ledger.create_task(db, "node01")
    cfg = {"configurable": {"thread_id": "node01"}}
    app = g.build_app(g.make_checkpointer(ws / "graph.db"))
    app.invoke({"task_id": t, "requirement": "node01", "base_branch": "base",
                "lanes": [{"name": "be", "node": "implement_api", "branch": "b1",
                           "scope": ["apps/api/src/**"], "prompt": "p", "commit_msg": "m"}]},
               cfg)
    app.invoke(Command(resume="approved"), cfg)  # G1
    app.invoke(Command(resume="approved"), cfg)  # G2 → implement → review → test → deploy → G3
    app.invoke(Command(resume="approved"), cfg)  # G3 → retro
    check("NODE-01 完整链路终态 done", db.execute(
        "SELECT status FROM tasks WHERE id=?", (t,)).fetchone()["status"] == "done")

    check("test/deploy 节点持有 Worker 租约（worker_id + lease_epoch）",
          _count(db, "SELECT COUNT(*) FROM node_runs WHERE task_id=? AND node_name IN "
                     "('test','deploy') AND worker_id IS NOT NULL AND lease_epoch IS NOT NULL",
                 (t,)) == 2)
    check("test/deploy 租约已正常收尾（status=done）",
          _count(db, "SELECT COUNT(*) FROM node_runs WHERE task_id=? AND node_name IN "
                     "('test','deploy') AND status='done'", (t,)) == 2)

    # 从指定节点重跑：终态任务可直接复跑，重跑清空该节点旧产物
    rerun_app = g.build_app(g.make_checkpointer(ws / "graph.db"), entry="test")
    check("重跑入口要求图未挂起", not rerun_app.get_state(cfg).next)
    g.reset_task_for_rerun(t, "test")
    rerun_app.invoke(g.rerun_payload(t, "test"), cfg)
    check("从 test 重跑：该节点二次执行",
          _count(db, "SELECT COUNT(*) FROM node_runs WHERE task_id=? AND node_name='test'",
                 (t,)) == 2)
    check("从 test 重跑后再次停在 G3",
          "review_g3" in rerun_app.get_state(cfg).next)
    check("重跑重新生成 TEST_REPORT",
          (ws / t / "docs" / "TEST_REPORT.md").is_file())

    try:
        g.rerun_payload(t, "review_g3")
        gate_entry_rejected = False
    except ValueError:
        gate_entry_rejected = True
    check("闸门节点不可作为重跑入口", gate_entry_rejected)

    # rules.yaml 外置：覆盖生效、缺失回退
    (ws / "rules.yaml").write_text("review:\n  diff_max: 123\n", encoding="utf-8")
    check("rules.yaml 覆盖判定阈值", g._rule_int("review", "diff_max") == 123)
    (ws / "rules.yaml").unlink()
    check("rules.yaml 缺失回退默认值", g._rule_int("review", "diff_max") == 20000)


# ---------------- MULTI-01 多项目隔离与配置消费（C1 第二项目接入） ----------------

def multi01(ws: Path):
    """C1 验收：平台支持接入独立第二仓库，项目间 state.db / 配置互不泄漏。"""
    import graph as g
    from langgraph.types import Command

    # 1) 真实项目发现：projects/ 下应同时注册 river 与 proj-b
    projects = g.list_projects()
    check("项目发现：river 与 proj-b 均注册",
          "river" in projects and "proj-b" in projects, f"got={projects}")

    # 2) set_project 配置消费：proj-b 必须指向独立仓库 pdca-erp，且读到双 lane 配置
    g.set_project("proj-b")
    cfg_b = g._project_config()
    check("set_project 消费 proj-b 配置：project=proj-b",
          cfg_b.get("project") == "proj-b")
    tech_b = cfg_b.get("tech_stack") or {}
    # 跨平台：project.yaml 里是 Windows 路径，Linux 上 PosixPath.name 不会按反斜杠切分
    repo_b = str(cfg_b.get("repo_path", "")).replace("\\", "/").rstrip("/").split("/")[-1]
    check("proj-b 接入独立仓库 pdca-erp", repo_b.lower() == "pdca-erp",
          f"repo_path={cfg_b.get('repo_path')}")
    check("proj-b 消费双 lane 配置（frontend + backend）",
          tech_b.get("web") == "frontend" and tech_b.get("api") == "backend"
          and "type-check" in str((tech_b.get("gate_commands") or {}).get("typecheck", "")),
          f"tech_stack={tech_b}")
    g.set_project("river")  # 切回默认，避免影响后续用例

    # 3) 双项目隔离：两个临时工作区各自跑双 lane 到 done，state.db 互不泄漏
    ws_a = ws / "proj-a"
    ws_b = ws / "proj-b"
    ws_a.mkdir(parents=True, exist_ok=True)
    ws_b.mkdir(parents=True, exist_ok=True)

    def _setup(pws: Path, inflight: int):
        (pws / "project.yaml").write_text(
            "project: stub\n"
            "budget:\n"
            f"  max_inflight_tasks: {inflight}\n"
            "tech_stack:\n"
            "  gate_commands:\n"
            "    typecheck: stub-typecheck\n"
            "    unit: stub-unit\n"
            "test:\n"
            "  gates: [typecheck, unit]\n"
            "deploy:\n"
            "  staging:\n"
            "    required: false\n"
            "    commands: []\n"
            "review:\n"
            "  codex: true\n", encoding="utf-8")
        (pws / "rules.yaml").write_text("review:\n  rework_attempts: 0\n", encoding="utf-8")
        return ledger.init_db(pws / "state.db")

    db_a = _setup(ws_a, 2)
    db_b = _setup(ws_b, 1)

    _stub_worker()
    _REVIEW_VERDICT["value"] = "PASS"

    lanes = [
        {"name": "be", "node": "implement_api", "branch": "feat-stub-be",
         "scope": ["apps/api/src/**"], "prompt": "p", "commit_msg": "m"},
        {"name": "fe", "node": "implement_web", "branch": "feat-stub-fe",
         "scope": ["apps/web/src/**"], "prompt": "p", "commit_msg": "m"},
    ]

    def _run_to_done(pws: Path, db, task_id: str) -> str:
        g.RIVER_WS = pws
        g.USAGE_BUFFER = pws / "usage_buffer.jsonl"
        t = ledger.create_task(db, task_id)
        app = g.build_app(g.make_checkpointer(pws / "graph.db"))
        cfg = {"configurable": {"thread_id": task_id}}
        app.invoke({"task_id": t, "requirement": task_id, "base_branch": "base",
                    "lanes": [dict(l) for l in lanes]}, cfg)
        app.invoke(Command(resume="approved"), cfg)  # G1
        app.invoke(Command(resume="approved"), cfg)  # G2 → implement → review → test → deploy → retro
        return db.execute("SELECT status FROM tasks WHERE id=?", (t,)).fetchone()["status"]

    try:
        status_a = _run_to_done(ws_a, db_a, "multi-a")
        status_b = _run_to_done(ws_b, db_b, "multi-b")
    finally:
        _REVIEW_VERDICT["value"] = "PASS"

    check("proj-a 双 lane 闭环到 done", status_a == "done", f"status={status_a}")
    check("proj-b 双 lane 闭环到 done", status_b == "done", f"status={status_b}")

    # 4) 互不泄漏：a 的 db 只有 a 的任务，b 的 db 只有 b 的任务
    a_ids = [r["id"] for r in db_a.execute("SELECT id FROM tasks").fetchall()]
    b_ids = [r["id"] for r in db_b.execute("SELECT id FROM tasks").fetchall()]
    check("项目隔离：proj-a state.db 不含 proj-b 任务",
          "multi-b" not in a_ids, f"a_ids={a_ids}")
    check("项目隔离：proj-b state.db 不含 proj-a 任务",
          "multi-a" not in b_ids, f"b_ids={b_ids}")

    # 5) 配置消费：切换工作区后 budget.max_inflight_tasks 读各自 project.yaml
    g.RIVER_WS = ws_a
    check("proj-a 配置消费：max_inflight_tasks=2",
          g._project_config()["budget"]["max_inflight_tasks"] == 2)
    g.RIVER_WS = ws_b
    check("proj-b 配置消费：max_inflight_tasks=1",
          g._project_config()["budget"]["max_inflight_tasks"] == 1)

    g.set_project("river")  # 恢复默认


# ---------------- EVAL-01 B 层评测机制 ----------------

def eval01(ws: Path):
    import json

    import prompt_eval as pe
    import prompt_report as pr
    import templates

    # 1) 黄金用例：覆盖全部模板且渲染层断言全通过（零成本）
    cases = pe.load_cases()
    case_templates = {c["template"] for c in cases}
    manifest_templates = set(templates._load_manifest()["templates"])
    check("黄金用例覆盖 manifest 全部模板", case_templates == manifest_templates,
          f"missing={sorted(manifest_templates - case_templates)}")
    report = pe.run_eval()
    failed = [c["id"] for c in report["cases"] if not c["passed"]]
    check(f"黄金用例全部通过（{report['passed']}/{report['total']}）",
          report["failed"] == 0, "; ".join(failed))
    check("报告记录通过率与 prompt 成本",
          report["pass_rate"] == 1.0
          and all(c["chars"] > 0 and c["est_tokens"] > 0 for c in report["cases"]))

    bad = {"id": "bad", "template": "requirements-gen",
           "params": {"task_id": "x", "raw_requirement": "y"},
           "assert": {"contains": ["## 不存在的章节"]}}
    bad_result = pe.evaluate_case(bad)
    check("断言不成立时如实判 FAIL",
          not bad_result["passed"] and bad_result["checks"][0]["ok"] is False)

    # 2) 版本与回滚：lock 记录内容哈希，可发现同版本内的模板漂移
    task_dir = ws / "task-lock"
    templates.snapshot(task_dir)
    lock = json.loads((task_dir / "templates.lock").read_text(encoding="utf-8"))
    check("templates.lock 记录模板内容哈希", bool(lock.get("hashes")))
    check("未改动时 lock_drift 判定一致", templates.lock_drift(task_dir)["ok"])
    lock["hashes"]["requirements-gen"] = "0" * 64
    (task_dir / "templates.lock").write_text(json.dumps(lock), encoding="utf-8")
    drift = templates.lock_drift(task_dir)
    check("内容哈希变化 → 检出漂移并可定位模板",
          not drift["ok"] and drift["changed"] == ["requirements-gen"])

    # 3) 失败驱动 + 成本观察：账本聚合
    db = new_db(ws)
    t = ledger.create_task(db, "eval01")
    run = ledger.start_node_run(db, t, "implement_be")
    ledger.record_gate_result(db, t, "typecheck", False,
                              "TS2304: Cannot find name 'x'", node_run_id=run)
    ledger.record_gate_result(db, t, "typecheck", True, "", node_run_id=run)
    ledger.record_gate_result(db, t, "lane_scope", False,
                              "越界文件：apps/web/x.tsx", node_run_id=run)
    for tokens in (1000, 3000):
        db.execute("INSERT INTO cost_ledger (task_id, node_run_id, agent_role, executor, "
                   "model, tokens_used) VALUES (?,?,?,?,?,?)",
                   (t, run, "requirements", "codex", "m1", tokens))
    db.execute("UPDATE node_runs SET ended_at=datetime(started_at, '+30 seconds') "
               "WHERE id=?", (run,))
    db.commit()

    gates = {g["gate"]: g for g in pr.gate_stats(db)}
    check("门禁失败按类型聚合",
          gates["typecheck"]["failed"] == 1 and gates["typecheck"]["total"] == 2
          and gates["lane_scope"]["failed"] == 1)
    check("最近失败原因可检索",
          any("越界文件" in f["reason"] for f in pr.recent_failures(db)))
    costs = {c["role"]: c for c in pr.role_costs(db)}
    check("角色成本按 calls/tokens 聚合",
          costs["requirements"]["calls"] == 2 and costs["requirements"]["tokens"] == 4000
          and costs["requirements"]["avg_tokens"] == 2000.0)
    durs = {d["node"]: d for d in pr.node_durations(db)}
    check("节点耗时按 node_runs 聚合", durs["implement_be"]["avg_seconds"] == 30.0)


# ---------------- LANE-01 lane 根工作目录（G-02） ----------------

def lane01(ws: Path):
    """G-02：按 lane scope 推导 codex cwd，并识别 cwd=lane 根后的嵌套前缀路径。"""
    import graph as g

    repo = ws / "repo"
    (repo / "frontend" / "src").mkdir(parents=True, exist_ok=True)
    (repo / "backend" / "src").mkdir(parents=True, exist_ok=True)

    wd, prefix = g._lane_workdir(str(repo), ["frontend/src/**"])
    check("单根 lane scope → cwd 为 lane 根",
          Path(wd) == repo / "frontend" and prefix == "frontend", f"wd={wd} prefix={prefix}")

    wd2, p2 = g._lane_workdir(str(repo), ["frontend/**", "backend/**"])
    check("多根 lane scope → 回退仓库根", Path(wd2) == repo and p2 == "", f"wd={wd2} prefix={p2}")

    wd3, p3 = g._lane_workdir(str(repo), ["README.md"])
    check("顶层文件 scope → 回退仓库根", Path(wd3) == repo and p3 == "")
    wd4, p4 = g._lane_workdir(str(repo), ["missing/**"])
    check("目录不存在 → 回退仓库根", Path(wd4) == repo and p4 == "")

    check("嵌套前缀路径被识别",
          g._lane_prefix_violations(["frontend/frontend/src/a.ts"], "frontend")
          == ["frontend/frontend/src/a.ts"])
    check("正常仓库相对路径不误报",
          g._lane_prefix_violations(["frontend/src/a.ts"], "frontend") == [])
    hint = g._workdir_hint(str(repo), str(repo / "frontend"), "frontend")
    check("工作目录提示含 lane 根与前缀去除规则",
          "lane root" in hint and "frontend/frontend" in hint and "strip that prefix" in hint)
    check("回退仓库根时提示声明仓库根",
          "repository root" in g._workdir_hint(str(repo), str(repo), ""))


# ---------------- CI-01 CI 最小权限（G-03） ----------------

def ci01(ws: Path):
    """G-03：CI 工作流必须显式声明最小权限，不能依赖 GITHUB_TOKEN 默认权限。"""
    root = Path(__file__).resolve().parents[1]
    text = (root / ".github" / "workflows" / "agentcode-checks.yml").read_text(encoding="utf-8")
    check("CI 工作流声明 permissions 块", "\npermissions:\n" in text)
    block = text.split("permissions:", 1)[1].split("jobs:", 1)[0] if "\npermissions:\n" in text else ""
    check("CI 仅授予 contents: read", "contents: read" in block and "write" not in block, block.strip())


# ---------------- KILL-01 超时进程树终止（C5） ----------------

def kill01(ws: Path):
    """C5：codex / 门禁超时必须杀进程树，避免只杀 shim 后孙进程继续写仓库。"""
    import os as _os
    import sys as _sys
    import time as _time
    import worker as wk

    if _os.name != "nt":
        print("SKIP KILL-01（Windows 专用：taskkill /T 与 .cmd 假 shim）")
        return

    root = ws / "killprobe"
    root.mkdir()
    writer = root / "writer.py"
    writer.write_text(
        "import pathlib, time\n"
        "import sys\n"
        "p = pathlib.Path(sys.argv[1])\n"
        "for i in range(600):\n"
        "    p.write_text('x' * (i + 1))\n"
        "    time.sleep(0.05)\n", encoding="utf-8")
    beat_codex = root / "beat-codex.txt"
    beat_gate = root / "beat-gate.txt"
    cmd = root / "fake-codex.cmd"
    cmd.write_text(f'@echo off\r\n"{_sys.executable}" "{writer}" "{beat_codex}"\r\n', encoding="utf-8")
    gate_shim = root / "fake-gate.cmd"
    gate_shim.write_text(f'@echo off\r\n"{_sys.executable}" "{writer}" "{beat_gate}"\r\n',
                         encoding="utf-8")

    def _beat(name: str) -> Path:
        return root / name

    def _stopped(path: Path) -> tuple[int, int]:
        size1 = path.stat().st_size if path.exists() else -1
        _time.sleep(1.5)
        size2 = path.stat().st_size if path.exists() else -1
        return size1, size2

    gate_cmd = f'"{gate_shim}"'
    orig = wk.shutil.which
    orig_codex, orig_gate = wk.run_codex, wk.run_gate
    wk.shutil.which = lambda name: str(cmd)
    wk.run_codex, wk.run_gate = _REAL_RUN_CODEX, _REAL_RUN_GATE
    try:
        r = wk.run_codex("probe", str(root), timeout=2, log_dir=str(root / "logs"))
        passed, tail = wk.run_gate(gate_cmd, str(root), timeout=2)
    finally:
        wk.shutil.which = orig
        wk.run_codex, wk.run_gate = orig_codex, orig_gate
    s1, s2 = _stopped(_beat("beat-codex.txt"))
    check("codex 超时返回失败", not r.ok, f"rc={r.returncode}")
    check("codex 超时终止进程树（心跳停止）", s1 > 0 and s1 == s2, f"{s1}->{s2}")

    g1, g2 = _stopped(_beat("beat-gate.txt"))
    check("门禁超时返回失败", not passed and "GATE TIMEOUT" in tail, tail[:80])
    check("门禁超时终止进程树（心跳停止）", g1 > 0 and g1 == g2, f"{g1}->{g2}")


CASES = {
    "AC-01": ac01, "AC-02": ac02, "AC-02b": ac02b, "AC-02c": ac02c, "AC-02d": ac02d,
    "AC-02e": ac02e,
    "AC-02f": ac02f,
    "AC-02g": ac02g,
    "AC-02h": ac02h,
    "AC-03": ac03,
    "AC-05": ac05, "AC-06": ac06, "AC-07": ac07, "AC-08": ac08, "AC-09": ac09,
    "AC-11": ac11, "AC-12": ac12, "AC-13": ac13, "AC-14": ac14,
    "ROLE-01": role01, "ROLE-02": role02,
    "CONSOLE-01": console01,
    "CONFIG-01": config01,
    "PROTOTYPE-01": prototype01,
    "PROMPT-01": prompt01,
    "EVAL-01": eval01,
    "REVIEW-01": review01,
    "REVIEW-02": review02,
    "NODE-01": node01,
    "MULTI-01": multi01,
    "LANE-01": lane01,
    "CI-01": ci01,
    "KILL-01": kill01,
}
NOT_IN_P1 = {
    "AC-04": "双 Worker 租约——B1 首验见 AC-02e（旧 epoch 拒写 + 凭据隔离）；双进程接管演练待 B 组",
    "AC-10": "契约版本失效传播 → P3",
}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="AC regression harness")
    ap.add_argument("--only", default="", help="逗号分隔用例，如 AC-01,AC-13")
    args = ap.parse_args(argv)
    wanted = [s.strip() for s in args.only.split(",") if s.strip()] or list(CASES)

    global _CASE
    for name in wanted:
        if name not in CASES:
            print(f"unknown case: {name}")
            return 2
        _CASE = name
        ws = Path(tempfile.mkdtemp(prefix=f"ac-{name.lower()}-"))
        try:
            CASES[name](ws)
        except Exception as exc:  # 用例崩溃也要算 FAIL，不中断其余用例
            RESULTS.append((name, "用例执行异常", "FAIL", f"{type(exc).__name__}: {exc}"))
            traceback.print_exc()

    print("\n=== AC 回归结果 ===")
    for case, desc, status, detail in RESULTS:
        line = f"[{status:4}] {case}  {desc}"
        if detail and status != "PASS":
            line += f"  :: {detail}"
        print(line)
    for case, why in NOT_IN_P1.items():
        print(f"[SKIP] {case}  P1 范围外：{why}")

    total = len(RESULTS)
    n = {s: sum(1 for r in RESULTS if r[2] == s) for s in ("PASS", "FAIL", "GAP", "SKIP")}
    print(f"\n合计 {total} 条断言：PASS={n['PASS']} FAIL={n['FAIL']} GAP={n['GAP']} SKIP={n['SKIP']}")
    return 1 if n["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
