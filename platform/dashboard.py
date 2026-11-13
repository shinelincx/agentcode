"""平台管理控制台（P2 前移，与功能同步实现）

默认只读：直接读 projects/<proj>/state.db（SQLite ro + WAL），不改任何业务状态。
    python platform/dashboard.py                       # http://127.0.0.1:8879
    python platform/dashboard.py --port 9000 --db ...\\state.db
    python platform/dashboard.py --print               # 终端打印一次快照（CI/巡检用）

管理控制台模式（显式开启；默认仍只读）：
    python platform/dashboard.py --control             # 提需求 + 审批 + 查看节点执行
    --intake 为兼容别名（等同 --control）
    保护：仅绑回环地址；POST 必须带 X-Control: 1（intake 兼容 X-Intake: 1）且 Origin（若有）
          须为本站；请求体 ≤ 64KB；字段校验失败 400 且不落库。

写操作：
    POST /api/intake              新建任务（结构化表单）
    POST /api/task/start          启动任务（图跑到首个闸门或结束）
    POST /api/task/decide         审批 G1/G2/G3（approved|rejected）
    POST /api/task/rerun          从指定节点重跑（body: task_id + from）
    POST /api/config              保存角色配置（预算/模型/门禁/部署/审批人；保留 YAML 注释）
    执行在线程池中的单线程队列串行进行（与 CLI 语义一致），HTTP 立即返回 202 + job_id。

配置管理：
    GET  /config                  配置页（只读模式可看不可改）
    GET  /api/config              当前值 + 可选项 + 最近审计
    POST /api/config              仅 --control 可用；校验失败 400 且不落盘

设计约束：
    - 读路径零第三方依赖（stdlib http.server）；仅控制台执行时延迟导入 graph/langgraph。
    - 每请求新建只读连接，避免缓存掩盖 WAL 中的新写入。
    - 金额/配额从 project.yaml 读取（正则，不强依赖 PyYAML），读不到时用默认值。
    - 接口为 JSON，前端 3s 轮询；字段名与账本表一致，P2 新增表可增量扩展。
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import sqlite3
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).parent))
import ledger
from intake import IntakeError, create_task_from_payload

PLATFORM = Path(__file__).resolve().parent
PROJECTS_DIR = PLATFORM.parent / "projects"
DEFAULT_PROJECT = os.environ.get("AGENTCODE_PROJECT", "river")
DEFAULT_DB = PROJECTS_DIR / DEFAULT_PROJECT / "state.db"
DEFAULT_YAML = PROJECTS_DIR / DEFAULT_PROJECT / "project.yaml"

# 挂起节点 → (审批闸门名, 产物 state keys)；与 run_task.GATES 保持一致
GATE_KEYS = {
    "review_g1": ("G1_requirements", ("requirements_doc",)),
    "review_g2": ("G2_design", ("design_doc", "prototype_dir")),
    "review_g3": ("G3_staging", ("deploy_report",)),
}
# 审批闸门 → 展示用产物类型（/api/artifact）
GATE_KIND = {"G1_requirements": "requirements", "G2_design": "design",
             "G3_staging": "deploy"}
KINDS = {
    "requirements": ("requirements", "REQUIREMENTS.md"),
    "design": ("design", "DESIGN.md"),
    "prototype": ("prototype", "PROTOTYPE.md"),
    "prototype_html": ("prototype", "index.html"),
    "test": ("test", "TEST_REPORT.md"),
    "deploy": ("deploy", "DEPLOY_REPORT.md"),
}
FLOW_NODES = ["requirements", "review_g1", "design", "prototype", "review_g2",
              "implement", "test", "deploy", "review_g3", "retro"]


def load_budget_config(path: Path) -> dict:
    cfg = {"project": "river", "quota": 20000, "per_task": 200,
           "alert": 0.80, "degraded": 0.90, "paused": 1.00,
           "max_inflight": 1, "slas": {}}
    if not path.exists():
        return cfg
    text = path.read_text(encoding="utf-8")
    for key, pattern, cast in (
        ("project", r"^project:\s*(\S+)", str),
        ("quota", r"monthly_quota:\s*(\d+)", int),
        ("per_task", r"max_usage_per_task:\s*(\d+)", int),
    ):
        m = re.search(pattern, text, re.MULTILINE)
        if m:
            cfg[key] = cast(m.group(1))
    m = re.search(r"thresholds:\s*\{alert:\s*([\d.]+),\s*degraded:\s*([\d.]+),\s*paused:\s*([\d.]+)\}", text)
    if m:
        cfg["alert"], cfg["degraded"], cfg["paused"] = (float(x) for x in m.groups())
    m = re.search(r"max_inflight_tasks:\s*(\d+)", text)
    if m:
        cfg["max_inflight"] = int(m.group(1))
    for gate, hours in re.findall(r"(G[123]_\w+):\s*\{[^}]*sla_hours:\s*([\d.]+)", text):
        cfg["slas"][gate] = float(hours)
    return cfg


def ro_connect(db_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    return db


def _rows(db: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict]:
    try:
        return [dict(r) for r in db.execute(sql, args)]
    except sqlite3.Error:
        return []


def _task_row(db_path: Path, task_id: str) -> dict | None:
    db = ro_connect(db_path)
    try:
        row = db.execute(
            "SELECT id, requirement, priority, status, current_node, retry_count, "
            "created_at, updated_at FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


KIND_NODE = {"requirements": "requirements", "design": "design",
             "test": "test", "deploy": "deploy"}
# 挂起节点 → 审批闸门（供 SLA 判定）
NODE_GATE = {"requirements": "G1_requirements", "design": "G2_design",
             "deploy": "G3_staging"}


def _steps_and_gates(db: sqlite3.Connection, task_ids: list[str]):
    """每个任务的最新节点状态 + 闸门结论（供流程视图）。

    implement 可能按 lane 留下 implement_be / implement_fe 等多条记录，
    统一归并到 implement 一步；按 started_at 升序覆盖，最后一条即最新尝试。
    """
    steps: dict[str, dict] = {}
    if task_ids:
        marks = ",".join("?" * len(task_ids))
        for r in _rows(db, f"""
                SELECT task_id, node_name, status, attempt, started_at, ended_at, output_path
                FROM node_runs WHERE task_id IN ({marks})
                ORDER BY started_at, rowid""", tuple(task_ids)):
            key = "implement" if r["node_name"].startswith("implement") else r["node_name"]
            if key not in FLOW_NODES:
                continue
            steps.setdefault(r["task_id"], {})[key] = {**r, "node_name": key}

    gate_states: dict[str, dict] = {}
    for a in _rows(db, "SELECT task_id, gate_name, decision FROM approvals ORDER BY id"):
        gate_states.setdefault(a["task_id"], {})[a["gate_name"]] = a["decision"]

    gate_artifacts: dict[str, dict] = {}
    for tid, nodes in steps.items():
        for gate, kind in GATE_KIND.items():
            path = (nodes.get(KIND_NODE[kind]) or {}).get("output_path")
            if not path:
                continue
            p = Path(path)
            if p.is_file():
                gate_artifacts.setdefault(tid, {})[gate] = {
                    "path": str(p), "size": p.stat().st_size}
    return steps, gate_states, gate_artifacts


def snapshot(db_path: Path, yaml_path: Path) -> dict:
    cfg = load_budget_config(yaml_path)
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    if not db_path.exists():
        return {"generated_at": now, "error": f"账本不存在：{db_path}", "config": cfg}

    db = ro_connect(db_path)
    try:
        calls = int(db.execute("SELECT COALESCE(SUM(calls),0) FROM cost_ledger").fetchone()[0])
        tokens = int(db.execute("SELECT COALESCE(SUM(tokens_used),0) FROM cost_ledger").fetchone()[0])
        rows = db.execute("SELECT state FROM budget_state ORDER BY id DESC LIMIT 1").fetchone()
        state = rows["state"] if rows else ("PAUSED" if calls >= cfg["quota"] else "NORMAL")
        ratio = round(calls / cfg["quota"], 4) if cfg["quota"] else 0.0
        counts = {
            "tasks": int(db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]),
            "running": int(db.execute("SELECT COUNT(*) FROM tasks WHERE status='running'").fetchone()[0]),
            "awaiting": int(db.execute("SELECT COUNT(*) FROM tasks WHERE status='awaiting_human'").fetchone()[0]),
            "approvals": int(db.execute("SELECT COUNT(*) FROM approvals WHERE decision='pending'").fetchone()[0]),
            "retro_pending": int(db.execute("SELECT COUNT(*) FROM retro_queue WHERE status='pending'").fetchone()[0]),
        }
        tasks = _rows(db, """
            SELECT t.id, t.requirement, t.priority, t.status, t.current_node, t.retry_count,
                   t.created_at, t.updated_at,
                   (SELECT COALESCE(SUM(calls),0) FROM cost_ledger c WHERE c.task_id=t.id) AS calls
            FROM tasks t ORDER BY t.created_at DESC LIMIT 30""")
        # 闸门 SLA 消费：等待时长按 updated_at（进入挂起状态的时间）计算，超时标红
        for t in tasks:
            t["sla_wait_hours"] = None
            t["sla_hours"] = None
            t["sla_overdue"] = False
            gate = NODE_GATE.get(t.get("current_node") or "")
            limit = (cfg.get("slas") or {}).get(gate) if t["status"] == "awaiting_human" else None
            if not limit:
                continue
            try:
                entered = datetime.strptime(t["updated_at"], "%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError):
                continue
            hours = round((datetime.now() - entered).total_seconds() / 3600, 1)
            t["sla_wait_hours"] = hours
            t["sla_hours"] = limit
            t["sla_overdue"] = hours >= float(limit)
        runs = _rows(db, """
            SELECT r.id, r.task_id, r.node_name, r.attempt, r.status, r.lane, r.branch,
                   r.started_at, r.ended_at
            FROM node_runs r ORDER BY r.started_at DESC LIMIT 30""")
        gates = _rows(db, """
            SELECT g.task_id, g.gate_name, g.passed, g.checked_at
            FROM gate_results g ORDER BY g.checked_at DESC LIMIT 15""")
        approvals = _rows(db, """
            SELECT a.id, a.task_id, a.gate_name, a.decision, a.decided_by, a.created_at, a.decided_at
            FROM approvals a ORDER BY a.id DESC LIMIT 12""")
        # 待审批单独取全量（最多 20），避免被后续已决记录挤出最近 12 条窗口
        pending_approvals = _rows(db, """
            SELECT a.id, a.task_id, a.gate_name, a.decision, a.decided_by, a.created_at, a.decided_at
            FROM approvals a WHERE a.decision='pending' ORDER BY a.id DESC LIMIT 20""")
        by_model = _rows(db, """
            SELECT COALESCE(model,'(unknown)') AS model, COALESCE(executor,'') AS executor,
                   COUNT(*) AS calls, COALESCE(SUM(tokens_used),0) AS tokens
            FROM cost_ledger GROUP BY model, executor ORDER BY calls DESC LIMIT 6""")
        history = _rows(db, "SELECT state, reason, changed_at FROM budget_state ORDER BY id DESC LIMIT 8")
        # B1 租约面板：持有者 / epoch / 心跳 / 到期（旧库未迁移时返回空表）
        leases = _rows(db, """
            SELECT r.task_id, r.node_name, r.status, r.worker_id, r.lease_epoch,
                   r.heartbeat_at, r.lease_expires_at, w.role AS worker_role, w.host, w.pid,
                   (r.lease_expires_at IS NOT NULL AND r.lease_expires_at > datetime('now')) AS live
            FROM node_runs r LEFT JOIN workers w ON w.id = r.worker_id
            WHERE r.worker_id IS NOT NULL ORDER BY r.started_at DESC LIMIT 20""")
        # B2 outbox 面板：Retro 投递状态（pending / done / dead + 尝试次数与退避）
        outbox = _rows(db, """
            SELECT id, task_id, status, attempts, next_attempt_at, last_error,
                   delivered_at, created_at
            FROM retro_queue ORDER BY id DESC LIMIT 20""")
        # B3 lanes 面板：合并顺序 / 分支链 / 状态（旧库未迁移时返回空表）
        lanes = _rows(db, """
            SELECT task_id, name, node_name, branch, base_branch, merge_order, status, merged_at
            FROM lanes ORDER BY task_id, merge_order LIMIT 30""")
        steps, gate_states, gate_artifacts = _steps_and_gates(db, [t["id"] for t in tasks])
    finally:
        db.close()

    buffer = db_path.parent / "usage_buffer.jsonl"
    pending = 0
    if buffer.exists():
        pending = sum(1 for ln in buffer.read_text(encoding="utf-8").splitlines() if ln.strip())

    return {
        "generated_at": now,
        "config": cfg,
        "budget": {"state": state, "calls": calls, "tokens": tokens, "quota": cfg["quota"],
                   "ratio": ratio, "history": history},
        "counts": counts,
        "buffer_pending": pending,
        "tasks": tasks, "node_runs": runs, "gates": gates,
        "approvals": approvals, "pending_approvals": pending_approvals,
        "usage_by_model": by_model, "leases": leases,
        "outbox": outbox, "lanes": lanes,
        "task_steps": steps, "gate_states": gate_states, "gate_artifacts": gate_artifacts,
    }


def _artifact_path(db: sqlite3.Connection, task_id: str, kind: str, ws: Path) -> Path | None:
    node, filename = KINDS[kind]
    row = db.execute(
        "SELECT output_path FROM node_runs WHERE task_id=? AND node_name=? "
        "AND output_path IS NOT NULL ORDER BY started_at DESC LIMIT 1",
        (task_id, node)).fetchone()
    if row and row["output_path"]:
        path = Path(row["output_path"])
        return path if path.name == filename else path.parent / filename
    # 原型产物落在专属原型目录，其余落在 docs/
    base = ws / task_id / ("prototype" if node == "prototype" else "docs")
    fallback = base / filename
    return fallback if fallback.exists() else None


def task_detail(db_path: Path, task_id: str) -> dict:
    task = _task_row(db_path, task_id)
    if task is None:
        return {"error": f"任务不存在：{task_id}"}
    db = ro_connect(db_path)
    try:
        runs = _rows(db, """
            SELECT node_name, attempt, status, lane, branch, started_at, ended_at, output_path
            FROM node_runs WHERE task_id=? ORDER BY started_at DESC, rowid DESC LIMIT 200""",
                     (task_id,))
        gates = _rows(db, """
            SELECT gate_name, passed, output, checked_at FROM gate_results
            WHERE task_id=? ORDER BY checked_at DESC LIMIT 100""", (task_id,))
        approvals = _rows(db, """
            SELECT id, gate_name, artifact_hash, decision, decided_by, created_at, decided_at
            FROM approvals WHERE task_id=? ORDER BY id DESC LIMIT 30""", (task_id,))
        artifacts = {}
        for kind in KINDS:
            path = _artifact_path(db, task_id, kind, db_path.parent)
            artifacts[kind] = {
                "path": str(path) if path else "",
                "exists": bool(path and path.is_file()),
                "size": path.stat().st_size if path and path.is_file() else 0,
            }
    finally:
        db.close()
    return {"task": task, "node_runs": runs, "gates": gates,
            "approvals": approvals, "artifacts": artifacts}


def artifact_content(db_path: Path, task_id: str, kind: str) -> dict:
    if kind not in KINDS:
        return {"error": f"未知产物类型：{kind}"}
    if _task_row(db_path, task_id) is None:
        return {"error": f"任务不存在：{task_id}"}
    ws = db_path.parent.resolve()
    db = ro_connect(db_path)
    try:
        path = _artifact_path(db, task_id, kind, ws)
    finally:
        db.close()
    if path is None or not path.is_file():
        return {"error": f"{kind} 产物尚未生成"}
    try:
        path.resolve().relative_to(ws)
    except ValueError:
        return {"error": "产物路径越界，拒绝读取"}
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"kind": kind, "path": str(path), "size": len(text.encode("utf-8")),
            "content": text[:200_000]}


def _recent_config_audit(path: Path, limit: int = 10) -> list[dict]:
    """读取最近配置审计条目（新→旧）；损坏行跳过，不阻塞配置页。"""
    if not path.is_file():
        return []
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    entries = []
    for line in lines[-limit:]:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(entries))


# ---------------- 控制台执行器（单线程串行，语义与 run_task.py 一致） ----------------

class ControlError(Exception):
    """控制台操作被拒（HTTP 状态码随异常携带）。"""

    def __init__(self, message: str, code: int = 409):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _requirement_text(ws: Path, db_path: Path, task_id: str) -> str:
    path = ws / task_id / "requirement.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))["requirement"]
    db = ro_connect(db_path)
    try:
        row = db.execute("SELECT requirement FROM tasks WHERE id=?", (task_id,)).fetchone()
    finally:
        db.close()
    if row is None:
        raise ControlError(f"任务不存在：{task_id}", 404)
    return row["requirement"]


def _ensure_approval(db_path: Path, task_id: str, gate: str,
                     artifacts: list[str | None]) -> int:
    """幂等登记待审批（复用 run_task 的哈希绑定语义；G2 同时绑定原型目录）。"""
    db = ledger.connect(db_path)
    try:
        row = db.execute(
            "SELECT id FROM approvals WHERE task_id=? AND gate_name=? AND decision='pending'",
            (task_id, gate)).fetchone()
        if row:
            return int(row["id"])
        paths = [a for a in artifacts if a and Path(a).exists()]
        if not paths:
            raise ControlError(f"{gate} 产物不存在：{artifacts!r}，无法登记审批")
        return ledger.request_approval(db, task_id, gate, ledger.artifact_digest(paths))
    finally:
        db.close()


class ControlRunner:
    """串行执行图动作：start / decide / rerun。

    单线程队列避免同一账本上的并发写与 checkpointer 竞争；HTTP 线程只入队。
    """

    def __init__(self, ws: Path, db_path: Path):
        self.ws = ws
        self.db_path = db_path
        self._app_obj = None
        self._rerun_apps: dict[str, object] = {}
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}
        self._seq = 0
        threading.Thread(target=self._loop, name="control-exec", daemon=True).start()

    # -- 对外 --
    def jobs(self) -> list[dict]:
        with self._lock:
            return [dict(j) for j in reversed(list(self._jobs.values()))][:20]

    def enqueue(self, task_id: str, action: str, *, decision: str = "",
                by: str = "console", entry: str = "") -> dict:
        row = _task_row(self.db_path, task_id)
        if row is None:
            raise ControlError(f"任务不存在：{task_id}", 404)
        if action == "start" and row["status"] != "pending":
            raise ControlError(f"任务状态为 {row['status']}，仅 pending 可启动", 409)
        if action == "start":
            limit = int(load_budget_config(self.ws / "project.yaml").get("max_inflight", 1))
            db = ro_connect(self.db_path)
            try:
                active = [r["id"] for r in db.execute(
                    "SELECT id FROM tasks WHERE status IN ('running','awaiting_human') AND id<>?",
                    (task_id,))]
            finally:
                db.close()
            if len(active) >= limit:
                raise ControlError(
                    f"并发上限 {limit} 已满（进行中 {len(active)} 个："
                    f"{', '.join(a[:8] for a in active)}）", 409)
        # CLI 启动后挂起但尚未登记审批时状态仍为 running，这里放行，由图状态做最终判定
        if action == "decide" and row["status"] not in ("awaiting_human", "running"):
            raise ControlError(f"任务状态为 {row['status']}，当前没有待审批闸门", 409)
        if action == "rerun" and not entry:
            raise ControlError("缺少重跑起始节点 from", 400)
        with self._lock:
            for job in self._jobs.values():
                if job["task_id"] == task_id and job["status"] in ("queued", "running"):
                    raise ControlError(
                        f"任务 {task_id[:8]} 已有排队/执行中的操作：{job['action']}", 409)
            self._seq += 1
            job = {"job_id": f"job-{self._seq}", "task_id": task_id, "action": action,
                   "decision": decision, "by": by, "entry": entry, "status": "queued",
                   "message": "", "queued_at": _now(), "started_at": "", "ended_at": ""}
            self._jobs[job["job_id"]] = job
            if len(self._jobs) > 60:
                for old in list(self._jobs)[:-60]:
                    self._jobs.pop(old, None)
        self._q.put(job["job_id"])
        return job

    # -- 内部 --
    def _get_app(self, entry: str | None = None):
        if entry:
            if entry not in self._rerun_apps:
                import graph as g
                g.RIVER_WS = self.ws
                g.USAGE_BUFFER = self.ws / "usage_buffer.jsonl"
                self._rerun_apps[entry] = g.build_app(
                    g.make_checkpointer(self.ws / "graph.db"), entry=entry)
            return self._rerun_apps[entry]
        if self._app_obj is None:
            import graph as g
            g.RIVER_WS = self.ws
            g.USAGE_BUFFER = self.ws / "usage_buffer.jsonl"
            self._app_obj = g.build_app(g.make_checkpointer(self.ws / "graph.db"))
        return self._app_obj

    def _loop(self):
        while True:
            job_id = self._q.get()
            with self._lock:
                job = self._jobs.get(job_id)
            if job is None:
                continue
            with self._lock:
                job.update(status="running", started_at=_now())
            try:
                message = self.execute(job["task_id"], job["action"],
                                       decision=job["decision"], by=job["by"],
                                       entry=job.get("entry", ""))
                status = "done"
            except ControlError as exc:
                message, status = str(exc), "failed"
            except Exception as exc:  # 执行器异常必须回传到界面，不得静默
                message, status = f"{type(exc).__name__}: {exc}"[:500], "failed"
            with self._lock:
                job.update(status=status, message=message, ended_at=_now())

    def execute(self, task_id: str, action: str, *, decision: str = "",
                by: str = "console", entry: str = "") -> str:
        """同步执行一次动作并返回摘要（测试可直接调用）。"""
        from langgraph.types import Command
        import graph as g
        if action == "rerun" and entry not in g.RERUN_ENTRY_NODES:
            raise ControlError(
                f"不支持从节点重跑：{entry!r}（可用：{', '.join(g.RERUN_ENTRY_NODES)}）", 400)
        app = self._get_app(entry if action == "rerun" else None)
        g.refresh_runtime_config()  # 配置页保存后无需重启进程即可生效
        cfg = {"configurable": {"thread_id": task_id}}
        snap = app.get_state(cfg)
        if action == "start":
            if snap.values:
                raise ControlError(
                    f"图状态已存在（挂起于 {list(snap.next) or 'END'}），无需重复启动", 409)
            app.invoke({"task_id": task_id,
                        "requirement": _requirement_text(self.ws, self.db_path, task_id)}, cfg)
        elif action == "decide":
            if decision not in ("approved", "rejected"):
                raise ControlError("decision 须为 approved 或 rejected", 400)
            if not snap.next:
                raise ControlError("当前没有挂起的闸门", 409)
            node = snap.next[0]
            if node not in GATE_KEYS:
                raise ControlError(f"挂起节点 {node} 不是人审闸门，无法审批", 409)
            gate, keys = GATE_KEYS[node]
            approval_id = _ensure_approval(self.db_path, task_id, gate,
                                           [snap.values.get(k) for k in keys])
            db = ledger.connect(self.db_path)
            try:
                ledger.decide_approval(db, approval_id, decision, by)
            finally:
                db.close()
            app.invoke(Command(resume=decision), cfg)
        elif action == "rerun":
            if not snap.values:
                raise ControlError("任务尚无图状态，无法重跑（先 start）", 409)
            if snap.next:
                raise ControlError(f"任务挂起于 {snap.next[0]}，请先审批而非重跑", 409)
            g.reset_task_for_rerun(task_id, entry)
            app.invoke(g.rerun_payload(task_id, entry), cfg)
        else:
            raise ControlError(f"未知动作：{action}", 400)

        snap = app.get_state(cfg)
        row = _task_row(self.db_path, task_id) or {}
        parts = [f"task={row.get('status')}", f"node={row.get('current_node') or '-'}"]
        pending = list(snap.next)
        if pending:
            node = pending[0]
            if node in GATE_KEYS:
                gate, keys = GATE_KEYS[node]
                try:
                    _ensure_approval(self.db_path, task_id, gate,
                                     [snap.values.get(k) for k in keys])
                    parts.append(f"挂起于 {gate}，待审批")
                except ControlError as exc:
                    parts.append(f"挂起于 {gate}，审批登记失败：{exc}")
            else:
                parts.append(f"挂起于 {node}")
        else:
            parts.append("流程结束")
        return " · ".join(parts)


HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>交付平台管理控制台</title>
<style>
:root{--bg:#0f1115;--panel:#171a21;--line:#262b36;--txt:#e6e9ef;--dim:#8b93a3;
--ok:#3fb950;--warn:#d29922;--bad:#f85149;--info:#58a6ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.5 -apple-system,"Segoe UI",system-ui,sans-serif}
header{display:flex;align-items:center;gap:16px;padding:14px 22px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:2}
h1{font-size:16px;margin:0;font-weight:600}
.tag{font-size:12px;color:var(--dim)}
.state{margin-left:auto;display:flex;align-items:center;gap:10px}
.badge{padding:3px 10px;border-radius:999px;font-size:12px;font-weight:600}
.NORMAL{background:rgba(63,185,80,.15);color:var(--ok)}
.ALERT{background:rgba(210,153,34,.15);color:var(--warn)}
.DEGRADED{background:rgba(210,105,34,.18);color:#e38d3a}
.PAUSED{background:rgba(248,81,73,.16);color:var(--bad)}
a{color:var(--info);text-decoration:none}
.newreq{display:inline-block;padding:5px 12px;border:1px solid rgba(88,166,255,.55);border-radius:8px;font-size:13px}
main{padding:18px 22px;display:grid;gap:18px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.kpi b{display:block;font-size:22px;font-weight:600;margin-top:4px}
.kpi span{color:var(--dim);font-size:12px}
.gauge{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}
.bar{height:10px;background:#0b0d11;border-radius:6px;overflow:hidden;margin-top:10px}
.fill{height:100%;width:0;background:linear-gradient(90deg,var(--info),var(--ok));transition:width .4s}
.fill.ALERT{background:linear-gradient(90deg,var(--info),var(--warn))}
.fill.DEGRADED{background:linear-gradient(90deg,var(--warn),#e38d3a)}
.fill.PAUSED{background:linear-gradient(90deg,#e38d3a,var(--bad))}
section{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
section h2{font-size:13px;margin:0;padding:11px 14px;border-bottom:1px solid var(--line);color:var(--dim);font-weight:600}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{text-align:left;padding:8px 14px;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--dim);font-weight:500;font-size:12px}
tr:last-child td{border-bottom:none}
td.req{max-width:340px;overflow:hidden;text-overflow:ellipsis;color:var(--dim)}
.s-running{color:var(--info)}.s-done,.p-1{color:var(--ok)}.s-awaiting_human{color:var(--warn)}
.s-failed,.s-gate_failed,.p-0{color:var(--bad)}.s-pending{color:var(--dim)}
.mono{font-family:ui-monospace,Consolas,monospace}
.empty{padding:14px;color:var(--dim);font-size:12px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.flow{display:flex;flex-wrap:wrap;align-items:center;gap:4px}
.chip{display:inline-block;padding:1px 7px;border-radius:6px;font-size:11px;border:1px solid var(--line);color:var(--dim)}
.c-running{color:var(--info);border-color:rgba(88,166,255,.55)}
.c-done{color:var(--ok);border-color:rgba(63,185,80,.45)}
.c-failed{color:var(--bad);border-color:rgba(248,81,73,.55)}
.c-awaiting_human{color:var(--warn);border-color:rgba(210,153,34,.6);background:rgba(210,153,34,.12)}
.arrow{color:#3a4150;font-size:11px}
button{background:#21262d;color:var(--txt);border:1px solid var(--line);border-radius:6px;
padding:4px 9px;font-size:12px;cursor:pointer}
button:hover{border-color:var(--info)}
button.ok{color:var(--ok);border-color:rgba(63,185,80,.5)}
button.bad{color:var(--bad);border-color:rgba(248,81,73,.5)}
.acts{display:flex;gap:6px;align-items:center}
.card-row{display:flex;align-items:center;gap:14px;padding:11px 14px;border-bottom:1px solid var(--line)}
.card-row:last-child{border-bottom:none}
.card-row .acts{margin-left:auto}
.gate{color:var(--info);font-family:ui-monospace,Consolas,monospace;font-size:12px}
.jobbar{padding:9px 14px;border-radius:8px;border:1px solid var(--line);background:#141821;font-size:12.5px}
.jobbar.jrun{border-color:rgba(88,166,255,.5)}
.jobbar.jok{border-color:rgba(63,185,80,.5)}
.jobbar.jbad{border-color:rgba(248,81,73,.55);color:#ffb3ae}
.overlay{position:fixed;inset:0;background:rgba(0,0,0,.62);display:flex;align-items:center;justify-content:center;z-index:10}
.overlay[hidden]{display:none}
.dialog{background:var(--panel);border:1px solid var(--line);border-radius:12px;width:min(1000px,94vw);max-height:88vh;display:flex;flex-direction:column}
.dialog-head{display:flex;align-items:center;gap:10px;padding:10px 14px;border-bottom:1px solid var(--line)}
.dialog-head b{font-size:13px}
.dialog-head button{margin-left:auto}
#m-body{padding:14px;overflow:auto}
#m-body h3{font-size:13px;color:var(--dim);margin:16px 0 8px}
pre.doc{white-space:pre-wrap;word-break:break-word;background:#0b0d11;border:1px solid var(--line);border-radius:8px;padding:12px;font-size:12.5px;margin:8px 0 0}
@media(max-width:900px){.grid2{grid-template-columns:1fr}}
</style></head><body>
<header>
  <h1>交付平台 · <span id="proj">-</span></h1>
  <span class="tag"><span id="mode">只读</span> · <span id="gen">-</span></span>
  <span id="intake-slot"></span><a class="newreq" href="/config">配置</a>
  <div class="state"><span class="tag" id="buf">缓冲 0</span><span id="state" class="badge">-</span></div>
</header>
<main>
  <div id="jobbar" class="jobbar" hidden></div>
  <div class="gauge">
    <div style="display:flex;justify-content:space-between"><span class="tag">月度用量（call_count 主单位）</span>
    <span class="mono" id="usage">-</span></div>
    <div class="bar"><div class="fill" id="fill"></div></div>
    <div class="tag" style="margin-top:8px" id="usagehint"></div>
  </div>
  <div class="kpis">
    <div class="kpi"><span>任务总数</span><b id="k-tasks">-</b></div>
    <div class="kpi"><span>运行中</span><b id="k-running">-</b></div>
    <div class="kpi"><span>待人工</span><b id="k-awaiting">-</b></div>
    <div class="kpi"><span>待审批</span><b id="k-approvals">-</b></div>
    <div class="kpi"><span>Retro 待处理</span><b id="k-retro">-</b></div>
    <div class="kpi"><span>累计 tokens</span><b id="k-tokens">-</b></div>
  </div>
  <section><h2>待审批 · 需求 G1 / 设计 G2 / staging G3</h2><div id="pending"></div></section>
  <section><h2>任务与节点流程</h2><div id="tasks"></div></section>
  <div class="grid2">
    <section><h2>审批记录</h2><div id="approvals"></div></section>
    <section><h2>门禁结果</h2><div id="gates"></div></section>
  </div>
  <section><h2>节点执行（最近 30 条）</h2><div id="runs"></div></section>
  <section><h2>Lane 计划（合并顺序）</h2><div id="lanes"></div></section>
  <div class="grid2">
    <section><h2>Worker 租约</h2><div id="leases"></div></section>
    <section><h2>Retro 投递（outbox）</h2><div id="outbox"></div></section>
  </div>
  <section><h2>按模型用量</h2><div id="models"></div></section>
</main>
<div id="modal" class="overlay" hidden>
  <div class="dialog">
    <div class="dialog-head"><b id="m-title">详情</b><button id="m-close">关闭</button></div>
    <div id="m-body"></div>
  </div>
</div>
<script>
const CONTROL = false;
const $ = id => document.getElementById(id);
const esc = s => (s ?? '').toString().replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const table = (head, rows, render) => rows.length
  ? `<table><thead><tr>${head.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.map(render).join('')}</tbody></table>`
  : '<div class="empty">暂无数据</div>';
const NODES = [['requirements','需求'],['review_g1','G1'],['design','设计'],['prototype','原型'],['review_g2','G2'],
  ['implement','实现'],['test','测试'],['deploy','部署'],['review_g3','G3'],['retro','retro']];
const GATE_OF = {review_g1:'G1_requirements',review_g2:'G2_design',review_g3:'G3_staging'};
const KIND_OF = {G1_requirements:'requirements',G2_design:'design',G3_staging:'deploy'};
let LAST = null;

function flow(t, d){
  const steps = (d.task_steps||{})[t.id] || {};
  const gates = (d.gate_states||{})[t.id] || {};
  return NODES.map(([key,label])=>{
    let st = 'pending';
    if (GATE_OF[key]) {
      const dec = gates[GATE_OF[key]];
      st = dec === 'approved' ? 'done' : dec === 'rejected' ? 'failed'
         : dec === 'pending' ? 'awaiting_human' : 'pending';
      if (st === 'pending' && t.current_node === key && t.status === 'awaiting_human') st = 'awaiting_human';
    } else {
      const s = steps[key];
      if (s) st = s.status;
      else if (t.current_node === key && t.status === 'running') st = 'running';
    }
    return `<span class="chip c-${esc(st)}" title="${esc(label)} · ${esc(st)}">${esc(label)}</span>`;
  }).join('<span class="arrow">›</span>');
}

function pendingRows(d){
  return d.pending_approvals || (d.approvals||[]).filter(a => a.decision === 'pending');
}

function renderPending(d){
  const rows = pendingRows(d);
  if (!rows.length) {
    $('pending').innerHTML = '<div class="empty">没有待审批的闸门。用「＋ 新建需求」创建任务并启动。</div>';
    return;
  }
  $('pending').innerHTML = rows.map(a => {
    const kind = KIND_OF[a.gate_name] || '';
    const art = ((d.gate_artifacts||{})[a.task_id]||{})[a.gate_name];
    const acts = [];
    if (kind) acts.push(`<button data-art="${esc(a.task_id)}" data-kind="${esc(kind)}">查看产物</button>`);
    if (CONTROL) {
      acts.push(`<button class="ok" data-decide="approved" data-task="${esc(a.task_id)}">批准</button>`);
      acts.push(`<button class="bad" data-decide="rejected" data-task="${esc(a.task_id)}">驳回</button>`);
    } else acts.push('<span class="tag">只读模式</span>');
    return `<div class="card-row">
      <div><b class="mono">${esc(a.task_id.slice(0,8))}</b> <span class="gate">${esc(a.gate_name)}</span>
      <div class="tag">提交于 ${esc(a.created_at)} · 产物 ${art ? art.size + ' B' : '缺失'}</div></div>
      <div class="acts">${acts.join('')}</div></div>`;
  }).join('');
}

function renderTasks(d){
  const waiting = new Set(pendingRows(d).map(a => a.task_id));
  $('tasks').innerHTML = table(['任务','需求','优先级','状态','当前节点','流程','SLA','calls','操作'], d.tasks, t => {
    const acts = [];
    if (CONTROL) {
      if (t.status === 'pending') acts.push(`<button data-start="${esc(t.id)}">启动</button>`);
      if (waiting.has(t.id)) {
        acts.push(`<button class="ok" data-decide="approved" data-task="${esc(t.id)}">批准</button>`);
        acts.push(`<button class="bad" data-decide="rejected" data-task="${esc(t.id)}">驳回</button>`);
      }
    }
    acts.push(`<button data-detail="${esc(t.id)}">详情</button>`);
    return `<tr><td class="mono">${esc(t.id.slice(0,8))}</td>
      <td class="req" title="${esc(t.requirement)}">${esc(t.requirement)}</td>
      <td>${esc(t.priority)}</td><td class="s-${esc(t.status)}">${esc(t.status)}</td>
      <td>${esc(t.current_node||'-')}</td><td><div class="flow">${flow(t,d)}</div></td>
      <td class="${t.sla_overdue?'s-failed':''}">${t.sla_wait_hours!=null?t.sla_wait_hours+'h/'+t.sla_hours+'h':'-'}</td>
      <td class="mono">${t.calls}</td><td><div class="acts">${acts.join('')}</div></td></tr>`;
  });
}

async function tick(){
  let d; try { d = await (await fetch('/api/overview',{cache:'no-store'})).json(); } catch(e){ return; }
  if (d.error){ $('gen').textContent = d.error; return; }
  LAST = d;
  $('proj').textContent = d.config.project;
  $('gen').textContent = d.generated_at;
  $('buf').textContent = `缓冲 ${d.buffer_pending}`;
  const st = $('state'); st.textContent = d.budget.state; st.className = 'badge ' + d.budget.state;
  const pct = Math.min(100, d.budget.ratio * 100).toFixed(1);
  const fill = $('fill'); fill.style.width = pct + '%'; fill.className = 'fill ' + d.budget.state;
  $('usage').textContent = `${d.budget.calls.toLocaleString()} / ${d.budget.quota.toLocaleString()} (${pct}%)`;
  $('usagehint').textContent =
    `阈值 alert ${(d.config.alert*100)|0}% · degraded ${(d.config.degraded*100)|0}% · paused ${(d.config.paused*100)|0}% · 单任务上限 U=${d.config.per_task}`;
  const set=(id,v)=>$(id).textContent=v;
  set('k-tasks',d.counts.tasks); set('k-running',d.counts.running); set('k-awaiting',d.counts.awaiting);
  set('k-approvals',d.counts.approvals); set('k-retro',d.counts.retro_pending);
  set('k-tokens',d.budget.tokens.toLocaleString());
  renderPending(d); renderTasks(d);
  $('approvals').innerHTML = table(['任务','闸门','结论','决策人','时间'], d.approvals, a =>
    `<tr><td class="mono">${esc(a.task_id.slice(0,8))}</td><td>${esc(a.gate_name)}</td>
     <td class="s-${a.decision==='rejected'?'failed':(a.decision==='approved'?'done':'awaiting_human')}">${esc(a.decision)}</td>
     <td>${esc(a.decided_by||'-')}</td><td class="tag">${esc(a.decided_at||a.created_at)}</td></tr>`);
  $('runs').innerHTML = table(['任务','节点','lane','尝试','状态','分支','开始','结束'], d.node_runs, r =>
    `<tr><td class="mono">${esc(r.task_id.slice(0,8))}</td><td>${esc(r.node_name)}</td><td>${esc(r.lane||'-')}</td>
     <td>${r.attempt}</td><td class="s-${esc(r.status)}">${esc(r.status)}</td><td class="mono">${esc(r.branch||'-')}</td>
     <td class="tag">${esc(r.started_at)}</td><td class="tag">${esc(r.ended_at||'-')}</td></tr>`);
  $('lanes').innerHTML = table(['任务','lane','顺序','分支','基线','状态','并入时间'], d.lanes || [], l =>
    `<tr><td class="mono">${esc(l.task_id.slice(0,8))}</td><td>${esc(l.name)}</td><td>${l.merge_order}</td>
     <td class="mono">${esc(l.branch||'-')}</td><td class="mono">${esc(l.base_branch||'-')}</td>
     <td class="s-${esc(l.status)}">${esc(l.status)}</td><td class="tag">${esc(l.merged_at||'-')}</td></tr>`);
  $('leases').innerHTML = table(['任务','节点','Worker','角色','host','epoch','心跳','到期','状态'], d.leases || [], l =>
    `<tr><td class="mono">${esc(l.task_id.slice(0,8))}</td><td>${esc(l.node_name)}</td>
     <td class="mono">${esc((l.worker_id||'').slice(0,8))}</td><td>${esc(l.worker_role||'-')}</td>
     <td>${esc(l.host||'-')}</td><td class="mono">${l.lease_epoch}</td>
     <td class="tag">${esc(l.heartbeat_at||'-')}</td><td class="tag">${esc(l.lease_expires_at||'-')}</td>
     <td class="${l.live ? 's-running' : 's-pending'}">${l.live ? '持有中' : '已释放/过期'}</td></tr>`);
  $('outbox').innerHTML = table(['任务','队列#','状态','尝试','下次重试','最近错误','投递时间'], d.outbox || [], o =>
    `<tr><td class="mono">${esc((o.task_id||'').slice(0,8))}</td><td class="mono">${o.id}</td>
     <td class="${o.status==='dead' ? 's-failed' : (o.status==='done' ? 's-done' : 's-pending')}">${esc(o.status)}</td>
     <td>${o.attempts}</td><td class="tag">${esc(o.next_attempt_at||'-')}</td>
     <td class="tag" title="${esc(o.last_error||'')}">${esc((o.last_error||'-').slice(0,40))}</td>
     <td class="tag">${esc(o.delivered_at||'-')}</td></tr>`);
  $('gates').innerHTML = table(['任务','门禁','结果','时间'], d.gates, g =>
    `<tr><td class="mono">${esc(g.task_id.slice(0,8))}</td><td>${esc(g.gate_name)}</td>
     <td class="p-${g.passed}">${g.passed ? 'PASS' : 'FAIL'}</td><td class="tag">${esc(g.checked_at)}</td></tr>`);
  $('models').innerHTML = table(['模型','执行器','calls','tokens'], d.usage_by_model, m =>
    `<tr><td class="mono">${esc(m.model)}</td><td>${esc(m.executor)}</td><td>${m.calls}</td><td>${m.tokens.toLocaleString()}</td></tr>`);
}

function toast(msg){
  const bar = $('jobbar'); bar.hidden = false; bar.className = 'jobbar jrun'; bar.textContent = msg;
}

async function act(url, payload){
  try {
    const res = await fetch(url, {method:'POST',
      headers:{'Content-Type':'application/json','X-Control':'1'}, body: JSON.stringify(payload)});
    const d = await res.json().catch(() => ({}));
    if (!res.ok) return toast('操作失败：' + (d.error || res.status));
    toast(`已提交 ${d.action} · ${d.task_id.slice(0,8)}（${d.job_id}），执行中…`);
    pollJobs();
  } catch (e) { toast('请求异常：' + e); }
}

async function showArtifact(taskId, kind){
  try {
    const res = await fetch(`/api/artifact?task_id=${encodeURIComponent(taskId)}&kind=${encodeURIComponent(kind)}`);
    const d = await res.json();
    if (!res.ok) return toast('读取失败：' + (d.error || res.status));
    $('m-title').textContent = `${kind} · ${taskId.slice(0,8)}`;
    if (kind === 'prototype_html') {
      // 静态原型直接在沙箱 iframe 内渲染（无 allow-same-origin，脚本无法触达控制台）
      $('m-body').innerHTML = `<div class="tag mono">${esc(d.path)} · ${d.size} B</div>
        <iframe sandbox="allow-scripts" style="width:100%;height:70vh;border:1px solid #2a2f3a;border-radius:8px;background:#fff"
          srcdoc="${esc(d.content)}"></iframe>`;
    } else {
      $('m-body').innerHTML = `<div class="tag mono">${esc(d.path)} · ${d.size} B</div>
        <pre class="doc">${esc(d.content)}</pre>`;
    }
    $('modal').hidden = false;
  } catch (e) { toast('请求异常：' + e); }
}

async function showDetail(taskId){
  try {
    const res = await fetch(`/api/task/detail?task_id=${encodeURIComponent(taskId)}`, {cache:'no-store'});
    const d = await res.json();
    if (!res.ok) return toast('读取失败：' + (d.error || res.status));
    const arts = Object.entries(d.artifacts || {}).map(([k, v]) =>
      `<tr><td>${esc(k)}</td><td class="mono">${esc(v.path || '-')}</td>
       <td>${v.exists ? v.size + ' B' : '缺失'}</td>
       <td>${v.exists ? `<button data-art="${esc(taskId)}" data-kind="${esc(k)}">查看</button>` : ''}</td></tr>`).join('');
    const runs = (d.node_runs || []).map(r =>
      `<tr><td>${esc(r.node_name)}</td><td>${r.attempt}</td><td class="s-${esc(r.status)}">${esc(r.status)}</td>
       <td>${esc(r.lane||'-')}</td><td class="tag">${esc(r.started_at)}</td><td class="tag">${esc(r.ended_at||'-')}</td></tr>`).join('');
    const apps = (d.approvals || []).map(a =>
      `<tr><td>${esc(a.gate_name)}</td><td class="s-${a.decision==='rejected'?'failed':(a.decision==='approved'?'done':'awaiting_human')}">${esc(a.decision)}</td>
       <td>${esc(a.decided_by||'-')}</td><td class="tag">${esc(a.decided_at||'-')}</td></tr>`).join('');
    $('m-title').textContent = `任务 ${taskId.slice(0,8)}`;
    $('m-body').innerHTML =
      `<div class="tag">状态 ${esc(d.task.status)} · 当前节点 ${esc(d.task.current_node||'-')} · 重试 ${d.task.retry_count} · 创建 ${esc(d.task.created_at)}</div>
       <div style="margin:10px 0"><button data-rerun="${esc(taskId)}">从节点重跑…</button>
       <span class="tag">失败或已结束后可从指定节点复跑（不影响已提交分支）</span></div>
       <h3>节点时间线</h3>${runs ? `<table><thead><tr><th>节点</th><th>尝试</th><th>状态</th><th>lane</th><th>开始</th><th>结束</th></tr></thead><tbody>${runs}</tbody></table>` : '<div class="empty">暂无节点记录</div>'}
       <h3>产物</h3><table><thead><tr><th>类型</th><th>路径</th><th>大小</th><th></th></tr></thead><tbody>${arts}</tbody></table>
       <h3>审批</h3>${apps ? `<table><thead><tr><th>闸门</th><th>结论</th><th>决策人</th><th>时间</th></tr></thead><tbody>${apps}</tbody></table>` : '<div class="empty">暂无审批记录</div>'}`;
    $('modal').hidden = false;
  } catch (e) { toast('请求异常：' + e); }
}

async function pollJobs(){
  if (!CONTROL) return;
  try {
    const d = await (await fetch('/api/jobs',{cache:'no-store'})).json();
    const j = (d.jobs || [])[0];
    const bar = $('jobbar');
    if (!j) { bar.hidden = true; return; }
    bar.hidden = false;
    bar.className = 'jobbar ' + (j.status === 'failed' ? 'jbad' : j.status === 'done' ? 'jok' : 'jrun');
    bar.innerHTML = `<b>${esc(j.action)}</b> ${esc(j.task_id.slice(0,8))} · ${esc(j.status)} · ${esc(j.message||'')}
      <span class="tag">${esc(j.queued_at)}</span>`;
  } catch (e) {}
}

document.addEventListener('click', e => {
  const btn = e.target.closest('button');
  if (!btn) return;
  if (btn.id === 'm-close') { $('modal').hidden = true; return; }
  if (btn.dataset.art) return showArtifact(btn.dataset.art, btn.dataset.kind);
  if (btn.dataset.start) return act('/api/task/start', {task_id: btn.dataset.start});
  if (btn.dataset.decide) return act('/api/task/decide',
    {task_id: btn.dataset.task, decision: btn.dataset.decide, by: 'console'});
  if (btn.dataset.detail) return showDetail(btn.dataset.detail);
  if (btn.dataset.rerun) {
    const from = prompt('从哪个节点重跑？可用：requirements / design / prototype / implement / review / test / deploy / retro', 'test');
    if (!from) return;
    return act('/api/task/rerun', {task_id: btn.dataset.rerun, from: from.trim(), by: 'console'});
  }
});

tick(); setInterval(tick, 3000);
if (CONTROL) { pollJobs(); setInterval(pollJobs, 2000); }
</script></body></html>"""


INTAKE_HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>新建需求 · 交付平台</title>
<style>
:root{--bg:#0f1115;--panel:#171a21;--line:#262b36;--txt:#e6e9ef;--dim:#8b93a3;
--ok:#3fb950;--bad:#f85149;--info:#58a6ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.6 -apple-system,"Segoe UI",system-ui,sans-serif}
header{display:flex;align-items:center;gap:16px;padding:14px 22px;border-bottom:1px solid var(--line)}
h1{font-size:16px;margin:0;font-weight:600}
a{color:var(--info);text-decoration:none}
main{max-width:900px;padding:22px;display:grid;gap:16px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;display:grid;gap:14px}
label{display:grid;gap:6px;font-size:13px;color:var(--dim)}
label b{color:var(--txt);font-weight:600;font-size:13px}
label .req{color:var(--bad)}
input,textarea,select{width:100%;background:#0b0d11;border:1px solid var(--line);border-radius:8px;
color:var(--txt);padding:9px 11px;font:13px/1.5 inherit;outline:none}
input:focus,textarea:focus,select:focus{border-color:var(--info)}
textarea{min-height:88px;resize:vertical}
.row{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.hint{font-size:12px;color:var(--dim)}
button{background:var(--info);color:#04101f;border:0;border-radius:8px;padding:10px 16px;font-weight:700;cursor:pointer}
button:disabled{opacity:.5;cursor:default}
#result{white-space:pre-wrap;font-family:ui-monospace,Consolas,monospace;font-size:12.5px}
#result.ok{color:var(--ok)} #result.err{color:var(--bad)}
@media(max-width:700px){.row{grid-template-columns:1fr}}
</style></head><body>
<header><h1>新建交付需求</h1>
  <span style="margin-left:auto"><a href="/">← 返回控制台</a></span></header>
<main>
<form class="card" id="f">
  <label><b>标题 <span class="req">*</span></b><input id="title" maxlength="120" placeholder="例：stats 接口增加 startedAt 字段"></label>
  <label><b>目标 <span class="req">*</span></b><textarea id="goal" placeholder="用户可观察的行为变化，一两句话说清"></textarea></label>
  <label><b>验收标准 <span class="req">*</span></b>
    <textarea id="ac" placeholder="一行一条，例：&#10;GET /api/stats 返回 ISO 8601 的 startedAt&#10;前端展示服务启动时间"></textarea>
  </label>
  <label>非目标 <textarea id="non" placeholder="明确不做什么（可空）"></textarea></label>
  <div class="row">
    <label>后端范围（be 路径，逗号分隔）<input id="be" placeholder="apps/api/src/**"></label>
    <label>前端范围（fe 路径，逗号分隔）<input id="fe" placeholder="apps/web/src/**"></label>
  </div>
  <label>优先级
    <select id="prio"><option value="normal">normal</option><option value="high">high</option><option value="low">low</option></select>
  </label>
  <label style="display:flex;align-items:center;gap:8px">
    <input type="checkbox" id="autostart" checked style="width:auto">
    <span>创建后立即启动（图跑到需求闸门 G1）</span>
  </label>
  <div class="hint">提交后任务写入账本并生成 requirement.json；启动由控制台执行器串行处理。</div>
  <div><button id="submit" type="submit">创建任务</button></div>
  <div id="result"></div>
</form>
</main>
<script>
const $ = id => document.getElementById(id);
const lines = s => s.split('\n').map(x => x.trim()).filter(Boolean);
const csv = s => s.split(',').map(x => x.trim()).filter(Boolean);
$('f').addEventListener('submit', async e => {
  e.preventDefault();
  const btn = $('submit'), out = $('result');
  const payload = {
    title: $('title').value.trim(), goal: $('goal').value.trim(),
    acceptance_criteria: lines($('ac').value), non_goals: $('non').value.trim(),
    scope: {be: csv($('be').value), fe: csv($('fe').value)}, priority: $('prio').value
  };
  btn.disabled = true; out.className = ''; out.textContent = '提交中…';
  try {
    const res = await fetch('/api/intake', {method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Intake': '1'},
      body: JSON.stringify(payload)});
    const data = await res.json();
    if (!res.ok) { out.className = 'err'; out.textContent = '创建失败：' + (data.error || res.status); }
    else {
      let msg = '已创建 ' + data.task_id;
      if ($('autostart').checked) {
        const r2 = await fetch('/api/task/start', {method: 'POST',
          headers: {'Content-Type': 'application/json', 'X-Control': '1'},
          body: JSON.stringify({task_id: data.task_id})});
        const d2 = await r2.json().catch(() => ({}));
        msg += r2.ok ? '\n已提交启动（' + d2.job_id + '），回控制台查看进度：/' : '\n启动失败：' + (d2.error || r2.status);
      } else {
        msg += '\n未启动。可在控制台任务表点「启动」。';
      }
      out.className = 'ok'; out.textContent = msg;
    }
  } catch (err) { out.className = 'err'; out.textContent = '请求异常：' + err; }
  btn.disabled = false;
});
</script></body></html>"""


CONFIG_HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>角色配置 · 交付平台</title>
<style>
:root{--bg:#0f1115;--panel:#171a21;--line:#262b36;--txt:#e6e9ef;--dim:#8b93a3;
--ok:#3fb950;--warn:#d29922;--bad:#f85149;--info:#58a6ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.6 -apple-system,"Segoe UI",system-ui,sans-serif}
header{display:flex;align-items:center;gap:16px;padding:14px 22px;border-bottom:1px solid var(--line)}
h1{font-size:16px;margin:0;font-weight:600}
a{color:var(--info);text-decoration:none}
main{padding:18px 22px;display:grid;gap:16px;max-width:1200px}
section{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
section h2{font-size:13px;margin:0;padding:11px 14px;border-bottom:1px solid var(--line);color:var(--dim);font-weight:600}
.fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px 16px;padding:14px}
.field{display:grid;gap:5px;font-size:12.5px;color:var(--dim)}
.field>span{font-weight:600;color:var(--txt)}
input,select,textarea{width:100%;background:#0b0d11;border:1px solid var(--line);border-radius:7px;
color:var(--txt);padding:7px 9px;font:12.5px/1.5 inherit;outline:none}
input:focus,select:focus,textarea:focus{border-color:var(--info)}
input[type=checkbox]{width:auto}
input:disabled,select:disabled,textarea:disabled{opacity:.72;cursor:not-allowed}
textarea{min-height:72px;resize:vertical;font-family:ui-monospace,Consolas,monospace}
.checks{display:flex;flex-wrap:wrap;gap:8px 14px}
.chk{display:flex;align-items:center;gap:6px;color:var(--txt);font-size:12.5px}
.full{grid-column:1/-1}
.savebar{display:flex;align-items:center;gap:12px;padding:4px 0}
button{background:var(--info);color:#04101f;border:0;border-radius:8px;padding:9px 18px;font-weight:700;cursor:pointer}
button:disabled{opacity:.5;cursor:default}
.hint{font-size:12px;color:var(--dim)}
.jobbar{padding:9px 14px;border-radius:8px;border:1px solid var(--line);background:#141821;font-size:12.5px}
.jobbar[hidden]{display:none}
.jobbar.jok{border-color:rgba(63,185,80,.5)}
.jobbar.jbad{border-color:rgba(248,81,73,.55);color:#ffb3ae}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{text-align:left;padding:8px 14px;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:500;font-size:12px}
tr:last-child td{border-bottom:none}
.empty{padding:14px;color:var(--dim);font-size:12px}
.mono{font-family:ui-monospace,Consolas,monospace}
@media(max-width:700px){.fields{grid-template-columns:1fr}}
</style></head><body>
<header><h1>角色配置 · <span id="proj">-</span></h1>
  <span class="hint mono" id="file">-</span>
  <span style="margin-left:auto"><a href="/">← 返回控制台</a></span></header>
<main>
  <div id="msg" class="jobbar" hidden></div>
  <div class="hint" id="modehint"></div>
  <form id="cfg">
    <section><h2>执行器 · executor</h2><div class="fields" id="f-executor"></div></section>
    <section><h2>模型路由 · model</h2><div class="fields" id="f-model"></div></section>
    <section><h2>预算边界 · budget</h2><div class="fields" id="f-budget"></div></section>
    <section><h2>测试门禁 · test / tech_stack.gate_commands</h2><div class="fields" id="f-test"></div></section>
    <section><h2>部署 staging · deploy.staging</h2><div class="fields" id="f-deploy"></div></section>
    <section><h2>审批闸门 · gates</h2><div class="fields" id="f-gates"></div></section>
    <section><h2>项目路径</h2><div class="fields" id="f-paths"></div></section>
    <div class="savebar"><button type="submit" id="save">保存配置</button>
      <span class="hint">保存后立即写入 project.yaml（定点替换、保留注释），并刷新运行时。</span></div>
  </form>
  <section><h2>最近配置变更 · config-audit.jsonl</h2><div id="audit"></div></section>
</main>
<script>
const EDITABLE = false;
const $ = id => document.getElementById(id);
const esc = s => (s ?? '').toString().replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const get = p => p.split('.').reduce((o, k) => (o == null ? undefined : o[k]), CFG);
let CFG = null, META = null;
const dis = EDITABLE ? '' : 'disabled';

function field(path, label, opts = {}){
  const v = get(path);
  const hint = opts.hint ? ` <span class="hint">${esc(opts.hint)}</span>` : '';
  if (opts.options) {
    return `<label class="field"><span>${esc(label)}${hint}</span>
      <select data-path="${path}" ${dis}>${opts.options.map(o =>
        `<option value="${esc(o)}" ${String(v) === String(o) ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select></label>`;
  }
  if (opts.type === 'lines') {
    return `<label class="field full"><span>${esc(label)}${hint}</span>
      <textarea data-path="${path}" data-type="list" rows="3" ${dis}>${esc((v || []).join('\n'))}</textarea></label>`;
  }
  if (opts.type === 'bool') {
    return `<label class="field"><span>${esc(label)}</span>
      <label class="chk"><input type="checkbox" data-path="${path}" data-type="bool" ${v ? 'checked' : ''} ${dis}> 启用</label></label>`;
  }
  const step = opts.step ? ` step="${opts.step}"` : '';
  return `<label class="field"><span>${esc(label)}${hint}</span>
    <input data-path="${path}" data-type="${opts.type || 'text'}" type="${opts.type === 'number' ? 'number' : 'text'}"${step}
      value="${esc(v ?? '')}" ${dis}></label>`;
}

function checks(group, label, options){
  const cur = get(group) || [];
  return `<div class="field full"><span>${esc(label)}</span><div class="checks">${options.map(o =>
    `<label class="chk"><input type="checkbox" data-group="${group}" value="${esc(o)}" ${cur.includes(o) ? 'checked' : ''} ${dis}> ${esc(o)}</label>`).join('')}</div></div>`;
}

function render(){
  $('proj').textContent = CFG.project || '-';
  $('file').textContent = META.file || '';
  $('modehint').textContent = EDITABLE
    ? '控制台模式：修改后点“保存配置”。校验失败不会写入文件。'
    : '只读模式：配置可查看；写入需以 --control 启动控制台。';
  $('f-executor').innerHTML =
    field('executor.primary', '主执行器', {options: META.executors}) +
    field('executor.fallback', '备用执行器', {options: META.executors}) +
    field('executor.codex_home', 'Codex 凭据目录', {hint: '须含 auth.json'});
  $('f-model').innerHTML =
    field('model.default', '默认模型') +
    field('model.degraded', '降级模型') +
    checks('model.strong_roles', '永不降级的角色', META.roles);
  $('f-budget').innerHTML =
    field('budget.monthly_quota', '月度配额（calls）', {type: 'number'}) +
    field('budget.max_usage_per_task', '单任务上限 U', {type: 'number'}) +
    field('budget.max_inflight_tasks', '并发上限 N', {type: 'number'}) +
    field('budget.thresholds.alert', 'alert 阈值', {type: 'number', step: '0.01'}) +
    field('budget.thresholds.degraded', 'degraded 阈值', {type: 'number', step: '0.01'}) +
    field('budget.thresholds.paused', 'paused 阈值', {type: 'number', step: '0.01'});
  $('f-test').innerHTML =
    checks('test.gates', '启用的测试门禁', META.gate_commands) +
    META.gate_commands.map(name =>
      field(`tech_stack.gate_commands.${name}`, `命令 · ${name}`)).join('');
  $('f-deploy').innerHTML =
    field('deploy.staging.required', 'required', {type: 'bool'}) +
    field('deploy.staging.timeout_seconds', '超时（秒）', {type: 'number'}) +
    field('deploy.staging.healthcheck', '健康检查（URL 或命令）') +
    field('deploy.staging.commands', '部署命令（一行一条）',
          {type: 'lines', hint: 'WSL 构建需在同一会话内完成'});
  $('f-gates').innerHTML = META.gates.map(g =>
    field(`gates.${g}.approver`, `${g} 审批人`) +
    field(`gates.${g}.sla_hours`, `${g} SLA（小时）`, {type: 'number'})).join('');
  $('f-paths').innerHTML =
    field('repo_path', '业务仓库路径 repo_path') +
    field('workspace', '平台工作区 workspace');
  $('audit').innerHTML = (META.audit || []).length ? `<table><thead><tr><th>时间</th><th>操作人</th><th>变更</th></tr></thead><tbody>${
    META.audit.map(e => `<tr><td class="mono">${esc(e.at)}</td><td>${esc(e.actor)}</td>
      <td class="hint">${esc((e.changes || []).map(c => c.path).join(', '))}</td></tr>`).join('')}</tbody></table>`
    : '<div class="empty">暂无配置变更记录</div>';
  $('save').style.display = EDITABLE ? '' : 'none';
}

function collect(){
  const updates = {};
  const put = (p, v) => { if (JSON.stringify(get(p)) !== JSON.stringify(v)) updates[p] = v; };
  document.querySelectorAll('[data-path]').forEach(el => {
    const type = el.dataset.type || 'text';
    let v;
    if (type === 'bool') v = el.checked;
    else if (type === 'number') {
      if (el.value.trim() === '') return;
      v = el.step && el.step.includes('.') ? parseFloat(el.value) : parseInt(el.value, 10);
    } else if (type === 'list') v = el.value.split('\n').map(s => s.trim()).filter(Boolean);
    else v = el.value.trim();
    put(el.dataset.path, v);
  });
  const groups = {};
  document.querySelectorAll('[data-group]').forEach(el => {
    (groups[el.dataset.group] = groups[el.dataset.group] || []).push(el);
  });
  Object.entries(groups).forEach(([g, els]) => put(g, els.filter(e => e.checked).map(e => e.value)));
  return updates;
}

async function load(){
  const res = await fetch('/api/config', {cache: 'no-store'});
  const d = await res.json().catch(() => ({}));
  if (!res.ok) { $('modehint').textContent = '配置读取失败：' + (d.error || res.status); return; }
  CFG = d.config; META = d.meta || {};
  META.file = d.file || '';
  META.audit = d.audit || [];
  render();
}

$('cfg').addEventListener('submit', async e => {
  e.preventDefault();
  if (!EDITABLE) return;
  const updates = collect();
  const keys = Object.keys(updates);
  const msg = $('msg'); msg.hidden = false;
  if (!keys.length) { msg.className = 'jobbar'; msg.textContent = '没有检测到变更'; return; }
  msg.className = 'jobbar'; msg.textContent = '保存中…';
  try {
    const res = await fetch('/api/config', {method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Control': '1'},
      body: JSON.stringify({updates, by: 'console'})});
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { msg.className = 'jobbar jbad'; msg.textContent = '保存失败：' + (d.error || res.status); return; }
    msg.className = 'jobbar jok';
    msg.textContent = `已保存 ${d.changed.length} 项：${d.changed.join(', ')}` +
      (d.runtime_refreshed ? '（运行时已刷新）' : '（运行时刷新失败，需重启）');
    await load();
  } catch (err) { msg.className = 'jobbar jbad'; msg.textContent = '请求异常：' + err; }
});

load();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    db_path: Path = DEFAULT_DB
    yaml_path: Path = DEFAULT_YAML
    intake_enabled: bool = False
    control_enabled: bool = False
    intake_ws: Path = DEFAULT_DB.parent
    runner: ControlRunner | None = None
    max_body: int = 64 * 1024

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, data: dict):
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        if path in ("/", "/index.html"):
            html = HTML
            if self.control_enabled:
                html = html.replace('<span id="mode">只读</span>', '<span id="mode">控制台</span>')
                html = html.replace('<span id="intake-slot"></span>',
                                    '<a class="newreq" href="/intake">＋ 新建需求</a>')
                html = html.replace("const CONTROL = false;", "const CONTROL = true;")
            elif self.intake_enabled:
                html = html.replace('<span id="mode">只读</span>', '<span id="mode">只读 + 录入</span>')
                html = html.replace('<span id="intake-slot"></span>',
                                    '<a class="newreq" href="/intake">＋ 新建需求</a>')
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/intake" and self.intake_enabled:
            self._send(200, INTAKE_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/config":
            html = CONFIG_HTML
            if self.control_enabled:
                html = html.replace("const EDITABLE = false;", "const EDITABLE = true;")
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/config":
            try:
                import config_store
                cfg = config_store.load(self.yaml_path)
            except Exception as exc:
                self._json(500, {"error": str(exc)})
                return
            self._json(200, {
                "file": str(self.yaml_path),
                "config": cfg,
                "editable": bool(self.control_enabled),
                "meta": {
                    "roles": list(config_store.KNOWN_ROLES),
                    "executors": list(config_store.EXECUTORS),
                    "gates": [name for name in config_store.GATES
                              if name in (cfg.get("gates") or {})],
                    "gate_commands": sorted(
                        ((cfg.get("tech_stack") or {}).get("gate_commands") or {})),
                },
                "audit": _recent_config_audit(self.yaml_path.parent / "config-audit.jsonl"),
            })
        elif path == "/healthz":
            self._send(200, b'{"ok":true}', "application/json")
        elif path == "/api/overview":
            data = snapshot(self.db_path, self.yaml_path)
            self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif path == "/api/jobs":
            jobs = self.runner.jobs() if self.runner else []
            self._json(200, {"jobs": jobs})
        elif path == "/api/task/detail":
            task_id = (query.get("task_id") or [""])[0].strip()
            if not task_id:
                self._json(400, {"error": "缺少 task_id"})
                return
            data = task_detail(self.db_path, task_id)
            self._json(404 if "error" in data else 200, data)
        elif path == "/api/artifact":
            task_id = (query.get("task_id") or [""])[0].strip()
            kind = (query.get("kind") or [""])[0].strip()
            if not task_id or not kind:
                self._json(400, {"error": "缺少 task_id 或 kind"})
                return
            data = artifact_content(self.db_path, task_id, kind)
            self._json(404 if "error" in data else 200, data)
        else:
            self._send(404, b'{"error":"not found"}', "application/json")

    def _write_allowed(self) -> bool:
        """跨站防护：自定义头（跨源需预检，本服务不应答）+ Origin 白名单。"""
        if self.headers.get("X-Control") != "1" and self.headers.get("X-Intake") != "1":
            return False
        origin = self.headers.get("Origin")
        if origin:
            port = self.server.server_address[1]
            if origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
                return False
        return True

    def _read_json(self) -> tuple[dict | None, int, str]:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            return None, 400, "Content-Length 非法"
        if length <= 0 or length > self.max_body:
            return None, 413, f"请求体须为 1~{self.max_body} 字节"
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return None, 400, f"JSON 解析失败：{exc}"
        if not isinstance(payload, dict):
            return None, 400, "请求体必须是 JSON 对象"
        return payload, 200, ""

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/config":
            if not self.control_enabled:
                self._json(404, {"error": "not found（配置写入需 --control 启动）"})
                return
            if not self._write_allowed():
                self._json(403, {"error": "缺少写入头或 Origin 非本站"})
                return
            payload, code, error = self._read_json()
            if payload is None:
                self._json(code, {"error": error})
                return
            import config_store
            actor = str(payload.get("by") or "console").strip()[:60] or "console"
            try:
                result = config_store.apply_updates(
                    self.yaml_path, payload.get("updates"), actor)
            except config_store.ConfigError as exc:
                self._json(400, {"error": str(exc)})
                return
            except Exception as exc:  # 写盘异常不得假装成功
                self._json(500, {"error": f"保存失败：{exc}"})
                return
            refreshed = False
            try:
                import graph
                graph.refresh_runtime_config()
                refreshed = True
            except Exception as exc:
                print(f"[config] runtime refresh failed: {exc}", file=sys.stderr)
            self._json(200, {
                "changed": result["changed"],
                "config": result["config"],
                "runtime_refreshed": refreshed,
                "next_step": "配置已写入 project.yaml；后续节点按新值执行",
            })
            return

        if path == "/api/intake":
            if not self.intake_enabled:
                self._json(404, {"error": "not found"})
                return
            if not self._write_allowed():
                self._json(403, {"error": "缺少写入头或 Origin 非本站"})
                return
            payload, code, error = self._read_json()
            if payload is None:
                self._json(code, {"error": error})
                return
            try:
                result = create_task_from_payload(self.db_path, self.intake_ws, payload)
            except IntakeError as exc:
                self._json(400, {"error": str(exc)})
                return
            except Exception as exc:  # 落库异常不得假装成功
                self._json(500, {"error": f"创建失败：{exc}"})
                return
            self._json(201, {
                "task_id": result["task_id"],
                "requirement_path": result["path"],
                "priority": result["priority"],
                "next_step": f"POST /api/task/start 或 python platform/run_task.py {result['task_id']} start",
            })
            return

        if path in ("/api/task/start", "/api/task/decide", "/api/task/rerun"):
            if not self.control_enabled or self.runner is None:
                self._json(404, {"error": "not found（需 --control 启动）"})
                return
            if not self._write_allowed():
                self._json(403, {"error": "缺少写入头或 Origin 非本站"})
                return
            payload, code, error = self._read_json()
            if payload is None:
                self._json(code, {"error": error})
                return
            task_id = str(payload.get("task_id") or "").strip()
            if not task_id:
                self._json(400, {"error": "缺少 task_id"})
                return
            action = ("start" if path.endswith("/start")
                      else "decide" if path.endswith("/decide") else "rerun")
            decision = str(payload.get("decision") or "").strip()
            entry = str(payload.get("from") or "").strip()
            by = str(payload.get("by") or "console").strip()[:60] or "console"
            if action == "decide" and decision not in ("approved", "rejected"):
                self._json(400, {"error": "decision 须为 approved 或 rejected"})
                return
            if action == "rerun" and not entry:
                self._json(400, {"error": "缺少重跑起始节点 from"})
                return
            try:
                job = self.runner.enqueue(task_id, action, decision=decision, by=by,
                                          entry=entry)
            except ControlError as exc:
                self._json(exc.code, {"error": str(exc)})
                return
            self._json(202, job)
            return

        self._json(404, {"error": "not found"})

    def do_OPTIONS(self):  # noqa: N802
        # 不应答 CORS 预检：跨源写请求（自定义头）将被浏览器拦截
        self._json(405, {"error": "preflight not allowed"})

    def log_message(self, fmt, *args):  # 降噪：只保留错误
        if str(args[1] if len(args) > 1 else "") >= "400":
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="平台管理控制台")
    ap.add_argument("--project", default=DEFAULT_PROJECT,
                    help="目标项目名（projects/<name>），默认 river，可由 AGENTCODE_PROJECT 覆盖")
    ap.add_argument("--db", default="",
                    help="账本路径；缺省为 projects/<project>/state.db")
    ap.add_argument("--project-yaml", default="",
                    help="项目配置；缺省为 projects/<project>/project.yaml")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8879,
                    help="默认 8879；本机 8766-8865/9005-9204 为 Windows 保留段（8787 会 WinError 10013）")
    ap.add_argument("--print", dest="print_once", action="store_true", help="打印一次快照后退出")
    ap.add_argument("--control", action="store_true",
                    help="管理控制台：提需求 + 审批 G1/G2/G3 + 启动任务（默认只读）")
    ap.add_argument("--intake", action="store_true", help="--control 的兼容别名")
    args = ap.parse_args(argv)

    project_dir = PROJECTS_DIR / args.project
    if args.project != DEFAULT_PROJECT and not (project_dir / "project.yaml").is_file():
        print(f"未知项目：{args.project!r}（缺少 {project_dir / 'project.yaml'}）", file=sys.stderr)
        return 3
    db_path = Path(args.db) if args.db else project_dir / "state.db"
    yaml_path = Path(args.project_yaml) if args.project_yaml else project_dir / "project.yaml"
    if args.print_once:
        print(json.dumps(snapshot(db_path, yaml_path), ensure_ascii=False, indent=2))
        return 0
    Handler.db_path, Handler.yaml_path = db_path, yaml_path
    Handler.intake_ws = db_path.parent
    control = args.control or args.intake
    if control:
        if args.host not in ("127.0.0.1", "localhost", "::1"):
            print("control mode requires a loopback host (127.0.0.1)", file=sys.stderr)
            return 3
        if db_path.name != "state.db":
            print("control mode requires --db <project>/state.db（图执行按工作区定位账本）",
                  file=sys.stderr)
            return 3
        try:
            import graph  # noqa: F401  控制台执行依赖 langgraph，开机即校验
        except Exception as exc:
            print(f"control mode dependencies unavailable: {exc}", file=sys.stderr)
            return 3
        Handler.control_enabled = True
        Handler.intake_enabled = True
        Handler.runner = ControlRunner(db_path.parent, db_path)
        print(f"control ENABLED: 提需求/审批/启动 · 任务目录 = {db_path.parent} · 串行执行",
              file=sys.stderr)
    srv = None
    for port in range(args.port, args.port + 6):  # 本机 8787 可能落在 Windows 保留端口段（WinError 10013）
        try:
            srv = ThreadingHTTPServer((args.host, port), Handler)
            break
        except OSError as exc:
            print(f"port {port} unavailable ({exc}); trying {port + 1}", file=sys.stderr)
    if srv is None:
        print("no available port", file=sys.stderr)
        return 2
    print(f"dashboard: http://{args.host}:{srv.server_address[1]}  db={db_path}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
