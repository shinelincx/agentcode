"""通用任务执行器（intake → 图 → 人审 → 实现）

用法：
    python platform/run_task.py <task_id> start            # 首次启动；已挂起则只打印挂起信息
    python platform/run_task.py <task_id> approve [--by X] # 批准当前闸门并继续
    python platform/run_task.py <task_id> reject  [--by X] --reason "..."  # 驳回当前闸门（理由必填，回上游 Agent 修订）
    python platform/run_task.py <task_id> rerun --from <node>  # 从指定节点重跑（失败/终态任务）
    python platform/run_task.py <task_id> status           # 查看图状态/任务状态

线程 id = task_id（与任务脚本一致）；需求文本优先取 task 目录 requirement.json，
缺失时回退 tasks.requirement。审批记录写 approvals（哈希绑定产物，AC-09 语义）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import graph
import intake
import ledger
import task_approval
import task_execution

WS = graph.RIVER_WS
DB = WS / "state.db"


def _select_project(name: str) -> None:
    """按 --project 切换工作区并重算 WS/DB；未知项目报错并列出可用项目。"""
    global WS, DB
    available = graph.list_projects()
    if name not in available:
        raise SystemExit(
            f"未知项目：{name!r}（可用：{', '.join(available) or '无'}）")
    graph.set_project(name)
    WS = graph.RIVER_WS
    DB = WS / "state.db"


def _app():
    return graph.build_app(graph.make_checkpointer(WS / "graph.db"))


def _cfg(task_id: str) -> dict:
    return {"configurable": {"thread_id": task_id}}


def _requirement(task_id: str) -> str:
    requirement = intake.requirement_text(DB, WS, task_id)
    if requirement is None:
        raise SystemExit(f"任务不存在：{task_id}（先用看板 /intake 或 intake.py 创建）")
    return requirement


def _task_row(task_id: str):
    db = ledger.connect(DB)
    try:
        return db.execute("SELECT id, status, priority, current_node FROM tasks WHERE id=?",
                          (task_id,)).fetchone()
    finally:
        db.close()


def _report(app, cfg, task_id: str, *, register: bool = False) -> None:
    snap = app.get_state(cfg)
    if register and snap.next and snap.next[0] in task_approval.GATES:
        try:
            task_approval.register(DB, task_id, snap)
        except task_approval.ApprovalError as exc:
            raise SystemExit(str(exc)) from exc
    row = _task_row(task_id)
    out = {
        "task_id": task_id,
        "task_status": row["status"] if row else None,
        "current_node": row["current_node"] if row else None,
        "pending_gates": list(snap.next),
    }
    for key in ("requirements_doc", "design_doc", "prototype_dir", "implement_summary",
                "test_report", "deploy_report"):
        if snap.values.get(key):
            out[key] = snap.values[key]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if snap.next:
        node = snap.next[0]
        if node in task_approval.GATES:
            print(f"\n挂起于 {node}：请审阅产物后执行 "
                  f"`run_task.py {task_id} approve` 或 `reject`")
        elif node in task_execution.REPLAYABLE_NODES:
            print(f"\n检查点停在 {node}：若执行已中断，请运行 "
                  f"`run_task.py {task_id} rerun --from {node}`")
        else:
            print(f"\n检查点停在 {node}：若执行已中断，需人工核查旧进程、产物和副作用")


def _inflight_guard(task_id: str) -> None:
    """并发上限（budget.max_inflight_tasks）：进行中任务已满则拒绝启动。"""
    budget = graph._project_config().get("budget") or {}
    try:
        limit = max(1, int(budget.get("max_inflight_tasks", 1)))
    except (TypeError, ValueError):
        limit = 1
    db = ledger.connect(DB)
    try:
        active = ledger.inflight_tasks(db, exclude=task_id)
    finally:
        db.close()
    if len(active) >= limit:
        raise SystemExit(
            f"并发上限 {limit} 已满（进行中：{', '.join(a[:8] for a in active)}）；"
            "先完成或用 reject 结束当前任务")


def cmd_start(task_id: str) -> int:
    _inflight_guard(task_id)
    app, cfg = _app(), _cfg(task_id)
    snap = app.get_state(cfg)
    if not snap.values:
        app.invoke({"task_id": task_id, "requirement": _requirement(task_id)}, cfg)
    _report(app, cfg, task_id, register=True)
    return 0


def cmd_decide(task_id: str, decision: str, by: str, reason: str = "") -> int:
    app, cfg = _app(), _cfg(task_id)
    try:
        task_approval.resume(DB, task_id, app, cfg, app.get_state(cfg), decision, by, reason)
    except task_approval.ApprovalError as exc:
        raise SystemExit(str(exc)) from exc
    _report(app, cfg, task_id, register=True)
    return 0


def cmd_rerun(task_id: str, entry: str) -> int:
    """从指定节点重跑，或恢复中断的只读文档节点。"""
    if not entry:
        raise SystemExit(f"缺少 --from <node>（可用：{', '.join(graph.RERUN_ENTRY_NODES)}）")
    if entry not in graph.RERUN_ENTRY_NODES:
        raise SystemExit(f"不支持从节点重跑：{entry}（可用：{', '.join(graph.RERUN_ENTRY_NODES)}）")
    app = graph.build_app(graph.make_checkpointer(WS / "graph.db"), entry=entry)
    cfg = _cfg(task_id)
    snap = app.get_state(cfg)
    if not snap.values:
        raise SystemExit("任务尚无图状态，无法重跑（先 start）")
    if not task_execution.resume_interrupted(DB, task_id, app, cfg, snap, entry):
        graph.reset_task_for_rerun(task_id, entry)
        app.invoke(graph.rerun_payload(task_id, entry), cfg)
    _report(app, cfg, task_id, register=True)
    return 0


def cmd_status(task_id: str) -> int:
    app, cfg = _app(), _cfg(task_id)
    row = _task_row(task_id)
    if row is None:
        raise SystemExit(f"任务不存在：{task_id}")
    _report(app, cfg, task_id)
    return 0


def main(argv: list[str]) -> int:
    import os
    ap = argparse.ArgumentParser(description="通用任务执行器")
    ap.add_argument("task_id")
    ap.add_argument("action", choices=("start", "approve", "reject", "rerun", "status"))
    ap.add_argument("--by", default="operator", help="审批人（写 approvals.decided_by）")
    ap.add_argument("--reason", default="",
                    help="驳回理由（reject 必填，1~2000 字；回传给上游 Agent 判断是否修订）")
    ap.add_argument("--from", dest="entry", default="",
                    help=f"rerun 的起始节点：{', '.join(graph.RERUN_ENTRY_NODES)}")
    ap.add_argument("--project", default=os.environ.get("AGENTCODE_PROJECT", "river"),
                    help="目标项目名（projects/<name>），默认 river，可由 AGENTCODE_PROJECT 覆盖")
    args = ap.parse_args(argv)
    _select_project(args.project)
    if args.action == "status":
        return cmd_status(args.task_id)
    try:
        with task_execution.exclusive(WS):
            return _write_action(args)
    except task_execution.ExecutionError as exc:
        raise SystemExit(str(exc)) from exc


def _write_action(args: argparse.Namespace) -> int:
    if args.action == "start":
        return cmd_start(args.task_id)
    if args.action in ("approve", "reject"):
        decision = "approved" if args.action == "approve" else "rejected"
        if decision == "rejected":
            reason = " ".join(args.reason.split())
            if not reason:
                raise SystemExit("驳回必须填写理由：--reason \"...\"（1~2000 字）")
            if len(reason) > 2000:
                raise SystemExit("驳回理由过长（≤2000 字）")
        else:
            reason = ""
        return cmd_decide(args.task_id, decision, args.by, reason)
    return cmd_rerun(args.task_id, args.entry)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
