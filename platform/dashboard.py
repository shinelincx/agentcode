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
    - 前后端分离：前端为 platform/console/ 下的静态文件（HTML/CSS/JS），后端只提供
      JSON API 与静态资源；运行模式由 GET /api/mode 下发，前端 3s 轮询 /api/overview。
    - 接口字段名与账本表一致，P2 新增表可增量扩展。
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
CONSOLE_DIR = PLATFORM / "console"
PROJECTS_DIR = PLATFORM.parent / "projects"
DEFAULT_PROJECT = os.environ.get("AGENTCODE_PROJECT", "river")
DEFAULT_DB = PROJECTS_DIR / DEFAULT_PROJECT / "state.db"
DEFAULT_YAML = PROJECTS_DIR / DEFAULT_PROJECT / "project.yaml"

# 静态前端（前后端分离）：只服务 console/ 目录下的文件
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

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


def list_projects() -> list[dict]:
    """已注册项目：projects/<name>/ 下存在 project.yaml 或 state.db 即纳入。"""
    items = []
    if not PROJECTS_DIR.is_dir():
        return items
    for d in sorted(PROJECTS_DIR.iterdir(), key=lambda p: p.name):
        if not d.is_dir():
            continue
        yaml_path, db_path = d / "project.yaml", d / "state.db"
        if not (yaml_path.is_file() or db_path.is_file()):
            continue
        cfg = load_budget_config(yaml_path)
        items.append({"name": d.name, "label": cfg.get("project") or d.name,
                      "db": db_path.is_file(), "yaml": yaml_path.is_file()})
    return items


def project_paths(name: str) -> tuple[Path, Path] | None:
    """项目名 → (state.db, project.yaml)；仅接受单段目录名，禁止穿越。"""
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        return None
    base = PROJECTS_DIR.resolve()
    d = (base / name).resolve()
    try:
        d.relative_to(base)
    except ValueError:
        return None
    db_path, yaml_path = d / "state.db", d / "project.yaml"
    if not db_path.is_file() and not yaml_path.is_file():
        return None
    return db_path, yaml_path


def search_tasks(db_path: Path, keyword: str = "", status: str = "",
                 limit: int = 100) -> list[dict]:
    """按任务 id / 需求关键字与状态查询（只读，最多 limit 条）。"""
    if not db_path.exists():
        return []
    db = ro_connect(db_path)
    try:
        sql = ("SELECT t.id, t.requirement, t.priority, t.status, t.current_node, "
               "t.retry_count, t.created_at, t.updated_at, "
               "(SELECT COALESCE(SUM(calls),0) FROM cost_ledger c WHERE c.task_id=t.id) AS calls "
               "FROM tasks t WHERE (t.id LIKE ? OR t.requirement LIKE ?)")
        args: list = [f"%{keyword}%", f"%{keyword}%"]
        if status:
            sql += " AND t.status=?"
            args.append(status)
        sql += " ORDER BY t.created_at DESC LIMIT ?"
        args.append(max(1, min(int(limit), 500)))
        return _rows(db, sql, tuple(args))
    finally:
        db.close()


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


# 图执行依赖 graph 模块的全局 RIVER_WS / 预算，跨项目的动作必须串行
_GRAPH_LOCK = threading.Lock()


class ControlRunner:
    """串行执行图动作：start / decide / rerun。

    单线程队列避免同一账本上的并发写与 checkpointer 竞争；HTTP 线程只入队。
    多项目共用 _GRAPH_LOCK，保证同一时刻只有一个项目在跑图。
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
                with _GRAPH_LOCK:
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



class Handler(BaseHTTPRequestHandler):
    db_path: Path = DEFAULT_DB
    yaml_path: Path = DEFAULT_YAML
    default_project: str = DEFAULT_PROJECT
    intake_enabled: bool = False
    control_enabled: bool = False
    intake_ws: Path = DEFAULT_DB.parent
    runner: ControlRunner | None = None
    runners: dict[str, ControlRunner] = {}
    _runners_lock = threading.Lock()
    max_body: int = 64 * 1024

    def _project(self, query=None, payload=None) -> tuple[str, Path, Path] | None:
        """解析请求的目标项目；缺省用默认项目（保留 --db 覆盖与测试桩）。"""
        name = ""
        if payload is not None:
            name = str(payload.get("project") or "").strip()
        if not name and query is not None:
            name = (query.get("project") or [""])[0].strip()
        name = name or self.default_project
        if name == self.default_project:
            return self.default_project, self.db_path, self.yaml_path
        found = project_paths(name)
        if found is None:
            return None
        return name, found[0], found[1]

    def _runner_for(self, name: str, db_path: Path) -> ControlRunner:
        """按项目取执行器（各自单线程队列；图执行由 _GRAPH_LOCK 全局串行）。"""
        if name == self.default_project and self.runner is not None:
            return self.runner
        key = str(db_path.resolve())
        with Handler._runners_lock:
            runner = self.runners.get(key)
            if runner is None:
                runner = ControlRunner(db_path.parent, db_path)
                self.runners[key] = runner
            return runner

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
        target = self._project(query=query)
        if target is None and path not in ("/api/mode", "/api/projects", "/healthz"):
            self._json(404, {"error": "未知项目（projects/<name> 不存在）"})
            return
        if path == "/api/mode":
            mode = ("控制台" if self.control_enabled
                    else "只读 + 录入" if self.intake_enabled else "只读")
            self._json(200, {"control": self.control_enabled,
                             "intake": self.intake_enabled, "mode": mode,
                             "default_project": self.default_project})
        elif path == "/api/projects":
            self._json(200, {"projects": list_projects(),
                             "default": self.default_project})
        elif path == "/api/config":
            proj, _, yaml_path = target
            try:
                import config_store
                cfg = config_store.load(yaml_path)
            except Exception as exc:
                self._json(500, {"error": str(exc)})
                return
            self._json(200, {
                "project": proj,
                "file": str(yaml_path),
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
                "audit": _recent_config_audit(yaml_path.parent / "config-audit.jsonl"),
            })
        elif path == "/healthz":
            self._send(200, b'{"ok":true}', "application/json")
        elif path == "/api/overview":
            proj, db_path, yaml_path = target
            data = snapshot(db_path, yaml_path)
            data["project_name"] = proj
            self._json(200, data)
        elif path == "/api/jobs":
            proj, db_path, _ = target
            jobs = self._runner_for(proj, db_path).jobs() if self.control_enabled else []
            self._json(200, {"project": proj, "jobs": jobs})
        elif path == "/api/search":
            proj, db_path, _ = target
            keyword = (query.get("q") or [""])[0].strip()
            status = (query.get("status") or [""])[0].strip()
            try:
                limit = int((query.get("limit") or ["100"])[0])
            except ValueError:
                limit = 100
            self._json(200, {"project": proj,
                             "tasks": search_tasks(db_path, keyword, status, limit)})
        elif path == "/api/task/detail":
            task_id = (query.get("task_id") or [""])[0].strip()
            if not task_id:
                self._json(400, {"error": "缺少 task_id"})
                return
            _, db_path, _ = target
            data = task_detail(db_path, task_id)
            self._json(404 if "error" in data else 200, data)
        elif path == "/api/artifact":
            task_id = (query.get("task_id") or [""])[0].strip()
            kind = (query.get("kind") or [""])[0].strip()
            if not task_id or not kind:
                self._json(400, {"error": "缺少 task_id 或 kind"})
                return
            _, db_path, _ = target
            data = artifact_content(db_path, task_id, kind)
            self._json(404 if "error" in data else 200, data)
        elif path == "/intake" and not self.intake_enabled:
            self._json(404, {"error": "not found"})
        elif path.startswith("/api/"):
            self._json(404, {"error": "not found"})
        else:
            self._serve_static(path)

    def _serve_static(self, path: str):
        """静态前端：仅服务 console/ 内的文件（拒绝目录穿越），按扩展名取 Content-Type。"""
        rel = path.lstrip("/") or "index.html"
        if rel in ("intake", "config"):
            rel = "index.html"  # 兼容旧入口：统一由 SPA 按路径初始化对应视图
        try:
            root = CONSOLE_DIR.resolve()
            target = (root / rel).resolve()
            target.relative_to(root)
        except (ValueError, OSError):
            self._json(404, {"error": "not found"})
            return
        if not target.is_file():
            self._json(404, {"error": "not found"})
            return
        ctype = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        self._send(200, target.read_bytes(), ctype)

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
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
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
            target = self._project(query=query, payload=payload)
            if target is None:
                self._json(404, {"error": "未知项目"})
                return
            proj, _, yaml_path = target
            import config_store
            actor = str(payload.get("by") or "console").strip()[:60] or "console"
            try:
                result = config_store.apply_updates(yaml_path, payload.get("updates"), actor)
            except config_store.ConfigError as exc:
                self._json(400, {"error": str(exc)})
                return
            except Exception as exc:  # 写盘异常不得假装成功
                self._json(500, {"error": f"保存失败：{exc}"})
                return
            refreshed = False
            try:
                import graph
                with _GRAPH_LOCK:
                    graph.RIVER_WS = yaml_path.parent
                    graph.USAGE_BUFFER = yaml_path.parent / "usage_buffer.jsonl"
                    graph.refresh_runtime_config()
                refreshed = True
            except Exception as exc:
                print(f"[config] runtime refresh failed: {exc}", file=sys.stderr)
            self._json(200, {
                "project": proj,
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
            target = self._project(query=query, payload=payload)
            if target is None:
                self._json(404, {"error": "未知项目"})
                return
            proj, db_path, _ = target
            try:
                result = create_task_from_payload(db_path, db_path.parent, payload)
            except IntakeError as exc:
                self._json(400, {"error": str(exc)})
                return
            except Exception as exc:  # 落库异常不得假装成功
                self._json(500, {"error": f"创建失败：{exc}"})
                return
            self._json(201, {
                "project": proj,
                "task_id": result["task_id"],
                "requirement_path": result["path"],
                "priority": result["priority"],
                "next_step": f"POST /api/task/start 或 python platform/run_task.py {result['task_id']} start",
            })
            return

        if path in ("/api/task/start", "/api/task/decide", "/api/task/rerun"):
            if not self.control_enabled:
                self._json(404, {"error": "not found（需 --control 启动）"})
                return
            if not self._write_allowed():
                self._json(403, {"error": "缺少写入头或 Origin 非本站"})
                return
            payload, code, error = self._read_json()
            if payload is None:
                self._json(code, {"error": error})
                return
            target = self._project(query=query, payload=payload)
            if target is None:
                self._json(404, {"error": "未知项目"})
                return
            proj, db_path, _ = target
            runner = self._runner_for(proj, db_path)
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
                job = runner.enqueue(task_id, action, decision=decision, by=by,
                                     entry=entry)
            except ControlError as exc:
                self._json(exc.code, {"error": str(exc)})
                return
            self._json(202, {**job, "project": proj})
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
    Handler.default_project = args.project
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
