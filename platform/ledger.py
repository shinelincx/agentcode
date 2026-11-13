"""业务账本（P1 最小协议子集）· V2.2 §4 / §8

事实源 = SQLite 账本（与 LangGraph checkpoint 分离，V2.1 核验结论）。
预算模型 = 事后计量 + 边界控制（V2.2 §8，用户决策 2026-11-11）。
kill 语义 = 修订版：PAUSED 走门禁层拒发，不依赖进程 kill（V2.2修订.md 修订 2）。

用法：
    python ledger.py init <db_path>
    python ledger.py status <db_path> [--quota N]
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import sys
import uuid
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# 预算状态机阈值（V2.2 §8.4；评估时点 = 节点边界）
THRESHOLDS = {"alert": 0.80, "degraded": 0.90, "paused": 1.00}
_DEFAULT_THRESHOLDS = dict(THRESHOLDS)
_INFLIGHT_STATUSES = ("running", "awaiting_human")


def configure_thresholds(values: dict) -> dict:
    """应用 project.yaml 的 budget.thresholds（非法/倒挂时回退默认值）。"""
    global THRESHOLDS
    try:
        alert = float(values.get("alert", _DEFAULT_THRESHOLDS["alert"]))
        degraded = float(values.get("degraded", _DEFAULT_THRESHOLDS["degraded"]))
        paused = float(values.get("paused", _DEFAULT_THRESHOLDS["paused"]))
    except (TypeError, ValueError):
        THRESHOLDS = dict(_DEFAULT_THRESHOLDS)
        return dict(THRESHOLDS)
    if not 0 < alert <= degraded <= paused:
        THRESHOLDS = dict(_DEFAULT_THRESHOLDS)
        return dict(THRESHOLDS)
    THRESHOLDS = {"alert": alert, "degraded": degraded, "paused": paused}
    return dict(THRESHOLDS)


def inflight_tasks(db: sqlite3.Connection, *, exclude: str = "") -> list[str]:
    """进行中的任务（running / awaiting_human），供并发上限判定。"""
    sql = ("SELECT id FROM tasks WHERE status IN (?,?)")
    args: list = list(_INFLIGHT_STATUSES)
    if exclude:
        sql += " AND id<>?"
        args.append(exclude)
    return [r["id"] for r in db.execute(sql + " ORDER BY created_at", tuple(args))]


STATE_ORDER = ["NORMAL", "ALERT", "DEGRADED", "PAUSED"]

# B1 租约：默认 TTL（秒）。Worker 过期未续 → 允许其他 Worker 接管并抬升 epoch。
LEASE_TTL = int(os.environ.get("RIVER_LEASE_TTL", "60"))


class LeaseError(Exception):
    """租约错误基类。"""


class StaleLeaseError(LeaseError):
    """epoch 已失效（租约被接管/节点已结束），写入必须被拒。"""


class LeaseHeldError(LeaseError):
    """租约被其他存活 Worker 持有，暂不可接管。"""


def artifact_digest(paths) -> str:
    """产物摘要（AC-09 哈希绑定，支持目录）。

    文件：文件名 + 内容；目录：按相对路径排序后逐文件累加。
    G2 把设计文档与原型目录一起绑定，任一文件变化都会使旧审批失效。
    """
    digest = hashlib.sha256()
    for raw in paths or ():
        if not raw:
            continue
        p = Path(raw)
        if p.is_dir():
            files = sorted((q for q in p.rglob("*") if q.is_file()),
                           key=lambda q: q.relative_to(p).as_posix())
            for q in files:
                digest.update(q.relative_to(p).as_posix().encode("utf-8") + b"\0")
                digest.update(q.read_bytes())
        elif p.is_file():
            digest.update(p.name.encode("utf-8") + b"\0")
            digest.update(p.read_bytes())
    return digest.hexdigest()


def connect(db_path: str | Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db(db_path: str | Path) -> sqlite3.Connection:
    """建库建表（幂等：IF NOT EXISTS）。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = connect(path)
    db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _migrate(db)
    db.commit()
    return db


def _migrate(db: sqlite3.Connection):
    """已有库的增量迁移（SQLite 无法在 ALTER 中加 CHECK，旧库仅补列）。"""
    cols = {row["name"] for row in db.execute("PRAGMA table_info(tasks)")}
    if "priority" not in cols:
        db.execute("ALTER TABLE tasks ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal'")
    # B1：旧库补租约列（新库由 schema.sql 直接建出）
    run_cols = {row["name"] for row in db.execute("PRAGMA table_info(node_runs)")}
    for name, ddl in (
        ("worker_id", "ALTER TABLE node_runs ADD COLUMN worker_id TEXT"),
        ("lease_epoch", "ALTER TABLE node_runs ADD COLUMN lease_epoch INTEGER"),
        ("lease_expires_at", "ALTER TABLE node_runs ADD COLUMN lease_expires_at TEXT"),
        ("heartbeat_at", "ALTER TABLE node_runs ADD COLUMN heartbeat_at TEXT"),
    ):
        if name not in run_cols:
            db.execute(ddl)
    # B2：retro_queue 升级为 outbox（至少一次投递 + 退避 + 死信）
    retro_sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='retro_queue'").fetchone()
    if retro_sql is not None:
        retro_cols = {row["name"] for row in db.execute("PRAGMA table_info(retro_queue)")}
        need = {"attempts", "next_attempt_at", "last_error", "delivered_at"}
        if "'dead'" not in (retro_sql["sql"] or "") or not need <= retro_cols:
            _rebuild_retro_queue(db)
    # 索引在迁移后建：旧表缺 next_attempt_at 时 executescript 建索引会失败
    db.execute("CREATE INDEX IF NOT EXISTS idx_retro_outbox ON retro_queue(status, next_attempt_at)")
    # B3：lanes 独立表由 schema 建出；旧库从 node_runs 幂等回填（迁移不丢数据）
    _backfill_lanes(db)


def _rebuild_retro_queue(db: sqlite3.Connection):
    """重建 retro_queue：旧 CHECK 不含 'dead'（SQLite 不能改 CHECK），按公共列保留数据。"""
    db.execute("DROP INDEX IF EXISTS idx_retro_outbox")
    db.execute("ALTER TABLE retro_queue RENAME TO retro_queue_old")
    db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    old_cols = {row["name"] for row in db.execute("PRAGMA table_info(retro_queue_old)")}
    cols = [c for c in ("id", "task_id", "payload", "retries", "status",
                        "attempts", "next_attempt_at", "last_error", "delivered_at",
                        "created_at") if c in old_cols]
    db.execute(f"INSERT INTO retro_queue ({','.join(cols)}) "
               f"SELECT {','.join(cols)} FROM retro_queue_old")
    db.execute("DROP TABLE retro_queue_old")


# ---------- Lane 计划（B3）----------

LANE_STATUSES = ("pending", "running", "done", "failed", "skipped")
_LANE_STATUS_FROM_RUN = {"done": "done", "failed": "failed", "cancelled": "skipped",
                         "running": "running", "awaiting_human": "pending"}


def _backfill_lanes(db: sqlite3.Connection) -> int:
    """从 node_runs 回填 lanes（B3 迁移，幂等）。

    - 同一任务内按 lane 首次出现时间定 merge_order；
    - 同一 (task, lane) 取最后一次执行作为代表（状态 / 分支 / 路径清单）；
    - base_branch 取前驱 lane 的分支（回填场景只能重建这条链）。
    已存在的 lane 行不覆盖（由 register_lanes / set_lane_status 维护）。
    """
    rows = db.execute(
        "SELECT task_id, lane, node_name, branch, file_scope, status, started_at, id "
        "FROM node_runs WHERE lane IS NOT NULL AND lane <> '' "
        "ORDER BY task_id, started_at, id").fetchall()
    order: dict[str, list[str]] = {}
    latest: dict[tuple[str, str], sqlite3.Row] = {}
    for r in rows:
        key = (r["task_id"], r["lane"])
        if key not in latest:
            order.setdefault(r["task_id"], []).append(r["lane"])
        latest[key] = r
    inserted = 0
    for task_id, names in order.items():
        for i, name in enumerate(names):
            r = latest[(task_id, name)]
            base = latest[(task_id, names[i - 1])]["branch"] if i else None
            cur = db.execute(
                "INSERT OR IGNORE INTO lanes (id, task_id, name, node_name, branch, base_branch, "
                "file_scope, merge_order, status) VALUES (?,?,?,?,?,?,?,?,?)",
                (f"{task_id}:{name}", task_id, name, r["node_name"], r["branch"], base,
                 r["file_scope"], i, _LANE_STATUS_FROM_RUN.get(r["status"], "pending")))
            inserted += cur.rowcount
    return inserted


def register_lanes(db: sqlite3.Connection, task_id: str, lanes: list[dict], *,
                   base_branch: str | None = None) -> list[str]:
    """登记任务的 lane 计划（幂等）：merge_order 按传入顺序，base_branch 串成合并链。

    已存在的 lane 只补空字段并刷新顺序，不改状态（避免恢复重跑把已完成的 lane 打回）。
    """
    ids = []
    for i, lane in enumerate(lanes):
        lane_id = f"{task_id}:{lane['name']}"
        prev_branch = base_branch if i == 0 else lanes[i - 1].get("branch")
        scope = lane.get("scope")
        db.execute(
            "INSERT INTO lanes (id, task_id, name, node_name, branch, base_branch, file_scope, merge_order) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "node_name=COALESCE(excluded.node_name, lanes.node_name), "
            "branch=COALESCE(excluded.branch, lanes.branch), "
            "base_branch=COALESCE(excluded.base_branch, lanes.base_branch), "
            "file_scope=COALESCE(excluded.file_scope, lanes.file_scope), "
            "merge_order=excluded.merge_order, updated_at=datetime('now')",
            (lane_id, task_id, lane["name"], lane.get("node"), lane.get("branch"), prev_branch,
             json.dumps(scope, ensure_ascii=False) if scope else None, i))
        ids.append(lane_id)
    db.commit()
    return ids


def set_lane_status(db: sqlite3.Connection, task_id: str, name: str, status: str) -> bool:
    """推进 lane 状态（pending/running/done/failed/skipped）。"""
    if status not in LANE_STATUSES:
        raise ValueError(f"lane status 须为 {LANE_STATUSES} 之一，收到 {status!r}")
    cur = db.execute(
        "UPDATE lanes SET status=?, updated_at=datetime('now') WHERE task_id=? AND name=?",
        (status, task_id, name))
    db.commit()
    return cur.rowcount > 0


def mark_lane_merged(db: sqlite3.Connection, task_id: str, name: str) -> bool:
    """标记该 lane 已被后继分支吸收（串行合并完成）；末 lane 由收尾调用。"""
    cur = db.execute(
        "UPDATE lanes SET merged_at=COALESCE(merged_at, datetime('now')), updated_at=datetime('now') "
        "WHERE task_id=? AND name=?", (task_id, name))
    db.commit()
    return cur.rowcount > 0


def lane_merge_plan(db: sqlite3.Connection, task_id: str) -> list[dict]:
    """合并顺序编排：按 merge_order 升序返回 lane 计划与执行状态。"""
    rows = db.execute(
        "SELECT name, node_name, branch, base_branch, file_scope, merge_order, status, merged_at "
        "FROM lanes WHERE task_id=? ORDER BY merge_order, name", (task_id,))
    return [dict(r) for r in rows]


# ---------- 任务 ----------

PRIORITIES = ("high", "normal", "low")


def create_task(db: sqlite3.Connection, requirement: str, priority: str = "normal") -> str:
    if priority not in PRIORITIES:
        raise ValueError(f"priority 须为 {PRIORITIES} 之一，收到 {priority!r}")
    task_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO tasks (id, requirement, priority) VALUES (?, ?, ?)",
        (task_id, requirement, priority),
    )
    db.commit()
    return task_id


def set_task_status(db: sqlite3.Connection, task_id: str, status: str, node: str | None = None):
    db.execute(
        "UPDATE tasks SET status=?, current_node=COALESCE(?, current_node), updated_at=datetime('now') WHERE id=?",
        (status, node, task_id),
    )
    db.commit()


# ---------- 节点执行 ----------

def start_node_run(db: sqlite3.Connection, task_id: str, node_name: str, *,
                   attempt: int = 1, lane: str | None = None,
                   file_scope: list[str] | None = None,
                   branch: str | None = None, worktree: str | None = None,
                   output_path: str | None = None) -> str:
    """登记节点执行尝试。预算紧张时门禁层拒发（修订 2 + AC-13 ①）。

    PAUSED：全部拒发；ALERT/DEGRADED：低优先级任务排队（不启动新 node_run）。
    """
    state = budget_status(db, quota=None)  # 读当前态（由 meter/边界评估写入）
    if state["state"] == "PAUSED":
        raise PermissionError("budget PAUSED: 新 node_run 拒发（在途任务跑完自然停住）")
    if state["state"] in ("ALERT", "DEGRADED"):
        row = db.execute("SELECT priority FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is not None and row["priority"] == "low":
            raise PermissionError(
                f"budget {state['state']}: 低优先级任务排队，新 node_run 拒发（任务 {task_id}）"
            )
    run_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO node_runs (id, task_id, node_name, attempt, lane, file_scope, branch, worktree_path, output_path) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (run_id, task_id, node_name, attempt, lane,
         json.dumps(file_scope, ensure_ascii=False) if file_scope else None,
         branch, worktree, output_path),
    )
    db.execute("UPDATE tasks SET status='running', current_node=?, updated_at=datetime('now') WHERE id=?",
               (node_name, task_id))
    db.commit()
    return run_id


def finish_node_run(db: sqlite3.Connection, run_id: str, status: str, *,
                    worker_id: str | None = None,
                    epoch: int | None = None) -> bool:
    """节点收尾。带 epoch 时走栅栏守卫：旧 epoch 一律拒写（B1 / AC-04）。

    无 epoch 为兼容路径（P1 用例/冒烟未接租约）；接租约的调用方必须传 epoch。
    """
    if epoch is None:
        db.execute("UPDATE node_runs SET status=?, ended_at=datetime('now') WHERE id=?",
                   (status, run_id))
        db.commit()
        return True
    sql = ("UPDATE node_runs SET status=?, ended_at=datetime('now'), lease_expires_at=NULL "
           "WHERE id=? AND lease_epoch=? AND status='running'")
    args: list = [status, run_id, epoch]
    if worker_id is not None:
        sql += " AND worker_id=?"
        args.append(worker_id)
    cur = db.execute(sql, args)
    if cur.rowcount == 0:
        db.rollback()
        raise StaleLeaseError(
            f"epoch {epoch} 写入被拒：node_run {run_id} 已被接管或已结束（worker={worker_id}）")
    db.commit()
    return True


# ---------- Worker 登记 / 租约（B1）----------

def register_worker(db: sqlite3.Connection, role: str, *,
                    host: str | None = None, pid: int | None = None) -> str:
    """登记 Worker 实例，返回 worker_id。租约与凭据目录都以该 id 归属。"""
    worker_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO workers (id, role, host, pid, last_heartbeat_at) "
        "VALUES (?,?,?,?,datetime('now'))",
        (worker_id, role, host or socket.gethostname(),
         pid if pid is not None else os.getpid()),
    )
    db.commit()
    return worker_id


def acquire_lease(db: sqlite3.Connection, worker_id: str, node_run_id: str, *,
                  ttl: int = LEASE_TTL) -> int:
    """领取/接管节点租约，返回新的 epoch（全局单调递增的栅栏令牌）。

    规则：存活租约不可被他人抢占（LeaseHeldError）；过期或同一 Worker → 抬升 epoch 接管。
    epoch 取全表 MAX+1，保证接管者的 epoch 严格大于任何旧持有者。
    """
    db.execute("BEGIN IMMEDIATE")
    try:
        if db.execute("SELECT 1 FROM workers WHERE id=?", (worker_id,)).fetchone() is None:
            raise LeaseError(f"worker 未登记：{worker_id}")
        run = db.execute(
            "SELECT status, worker_id, lease_epoch, "
            "(lease_expires_at IS NOT NULL AND lease_expires_at > datetime('now')) AS live "
            "FROM node_runs WHERE id=?", (node_run_id,)).fetchone()
        if run is None:
            raise LeaseError(f"node_run 不存在：{node_run_id}")
        if run["status"] != "running":
            raise LeaseError(f"node_run 非 running（{run['status']}），不可领取租约")
        if run["worker_id"] and run["worker_id"] != worker_id and run["live"]:
            raise LeaseHeldError(
                f"租约被存活 Worker {run['worker_id']} 持有（epoch={run['lease_epoch']}），不可抢占")
        max_epoch = int(db.execute(
            "SELECT COALESCE(MAX(lease_epoch), 0) AS e FROM node_runs").fetchone()["e"])
        epoch = max_epoch + 1
        db.execute(
            "UPDATE workers SET status='alive', lease_epoch=?, "
            "lease_expires_at=datetime('now', ?), last_heartbeat_at=datetime('now') WHERE id=?",
            (epoch, f"+{ttl} seconds", worker_id))
        db.execute(
            "UPDATE node_runs SET worker_id=?, lease_epoch=?, "
            "lease_expires_at=datetime('now', ?), heartbeat_at=datetime('now') WHERE id=?",
            (worker_id, epoch, f"+{ttl} seconds", node_run_id))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return epoch


def renew_lease(db: sqlite3.Connection, worker_id: str, node_run_id: str, epoch: int, *,
                ttl: int = LEASE_TTL) -> None:
    """心跳续租。epoch 不匹配（已被接管）→ StaleLeaseError。"""
    cur = db.execute(
        "UPDATE node_runs SET heartbeat_at=datetime('now'), lease_expires_at=datetime('now', ?) "
        "WHERE id=? AND worker_id=? AND lease_epoch=? AND status='running'",
        (f"+{ttl} seconds", node_run_id, worker_id, epoch))
    if cur.rowcount == 0:
        db.rollback()
        raise StaleLeaseError(f"epoch {epoch} 续租被拒：租约已失效（node_run {node_run_id}）")
    db.execute(
        "UPDATE workers SET last_heartbeat_at=datetime('now'), lease_expires_at=datetime('now', ?) "
        "WHERE id=?", (f"+{ttl} seconds", worker_id))
    db.commit()


def release_lease(db: sqlite3.Connection, worker_id: str, node_run_id: str, epoch: int) -> None:
    """主动释放租约（节点正常收尾后调用）。旧 epoch 释放同样被拒。"""
    cur = db.execute(
        "UPDATE node_runs SET lease_expires_at=NULL WHERE id=? AND worker_id=? AND lease_epoch=?",
        (node_run_id, worker_id, epoch))
    if cur.rowcount == 0:
        db.rollback()
        raise StaleLeaseError(f"epoch {epoch} 释放被拒：租约已失效（node_run {node_run_id}）")
    db.execute("UPDATE workers SET status='retired', lease_expires_at=NULL WHERE id=?", (worker_id,))
    db.commit()


def _assert_lease(db: sqlite3.Connection, node_run_id: str | None,
                  epoch: int, worker_id: str | None) -> None:
    """写入前栅栏校验：epoch/worker 不匹配或节点已结束 → 拒写。"""
    if not node_run_id:
        raise StaleLeaseError("带 epoch 的写入必须绑定 node_run_id")
    row = db.execute("SELECT lease_epoch, worker_id, status FROM node_runs WHERE id=?",
                     (node_run_id,)).fetchone()
    if row is None:
        raise StaleLeaseError(f"node_run 不存在：{node_run_id}")
    if row["lease_epoch"] != epoch or row["status"] != "running":
        raise StaleLeaseError(
            f"epoch {epoch} 写入被拒：当前 epoch={row['lease_epoch']} status={row['status']}")
    if worker_id is not None and row["worker_id"] != worker_id:
        raise StaleLeaseError(f"worker {worker_id} 非当前租约持有者（{row['worker_id']}）")


def list_leases(db: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """租约视图（看板用）：持有者、epoch、到期、存活标记。"""
    rows = db.execute(
        "SELECT r.id AS node_run_id, r.task_id, r.node_name, r.status, r.worker_id, "
        "       w.role AS worker_role, w.host, w.pid, r.lease_epoch, "
        "       r.lease_expires_at, r.heartbeat_at, "
        "       (r.lease_expires_at IS NOT NULL AND r.lease_expires_at > datetime('now')) AS live "
        "FROM node_runs r LEFT JOIN workers w ON w.id = r.worker_id "
        "WHERE r.worker_id IS NOT NULL ORDER BY r.started_at DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


# ---------- 副作用（幂等键去重）----------

def record_operation(db: sqlite3.Connection, task_id: str, operation_key: str,
                     op_type: str, payload_hash: str, *,
                     node_run_id: str | None = None,
                     status: str = "done", result_ref: str | None = None,
                     worker_id: str | None = None,
                     lease_epoch: int | None = None) -> bool:
    """幂等写入：operation_key 已存在则跳过，返回 False。

    B1 栅栏：传 lease_epoch 时先校验租约，旧 epoch 直接 StaleLeaseError 且不落库。
    """
    if lease_epoch is not None:
        _assert_lease(db, node_run_id, lease_epoch, worker_id)
    cur = db.execute(
        "INSERT OR IGNORE INTO operations (task_id, node_run_id, operation_key, op_type, payload_hash, status, result_ref) "
        "VALUES (?,?,?,?,?,?,?)",
        (task_id, node_run_id, operation_key, op_type, payload_hash, status, result_ref),
    )
    db.commit()
    return cur.rowcount > 0


# ---------- 审批（哈希绑定）----------

def request_approval(db: sqlite3.Connection, task_id: str, gate_name: str,
                     artifact_hash: str) -> int:
    cur = db.execute(
        "INSERT INTO approvals (task_id, gate_name, artifact_hash) VALUES (?,?,?)",
        (task_id, gate_name, artifact_hash),
    )
    db.execute("UPDATE tasks SET status='awaiting_human', updated_at=datetime('now') WHERE id=?", (task_id,))
    db.commit()
    return cur.lastrowid


def decide_approval(db: sqlite3.Connection, approval_id: int, decision: str, decided_by: str) -> bool:
    """登记审批结论。decision ∈ approved|rejected。"""
    db.execute(
        "UPDATE approvals SET decision=?, decided_by=?, decided_at=datetime('now') WHERE id=? AND decision='pending'",
        (decision, decided_by, approval_id),
    )
    db.commit()
    return db.total_changes > 0


def approval_valid(db: sqlite3.Connection, task_id: str, gate_name: str, artifact_hash: str) -> bool:
    """审批是否对当前产物哈希有效（AC-09：批准后产物变更 → 旧审批失效）。"""
    row = db.execute(
        "SELECT 1 FROM approvals WHERE task_id=? AND gate_name=? AND artifact_hash=? AND decision='approved'",
        (task_id, gate_name, artifact_hash),
    ).fetchone()
    return row is not None


# ---------- 门禁 ----------

def record_gate_result(db: sqlite3.Connection, task_id: str, gate_name: str, passed: bool,
                       output: str = "", *, node_run_id: str | None = None):
    db.execute(
        "INSERT INTO gate_results (task_id, node_run_id, gate_name, passed, output) VALUES (?,?,?,?,?)",
        (task_id, node_run_id, gate_name, 1 if passed else 0, output),
    )
    db.commit()


# ---------- 预算（事后计量 + 边界控制）----------

def total_calls(db: sqlite3.Connection) -> int:
    row = db.execute("SELECT COALESCE(SUM(calls), 0) AS n FROM cost_ledger").fetchone()
    return int(row["n"])


def evaluate_budget(db: sqlite3.Connection, quota: int, *, project: str = "river") -> dict:
    """节点边界评估：读累计 → 定状态 → 留痕（含初始 NORMAL 起点）。返回 {state, used, quota, ratio}。"""
    used = total_calls(db)
    ratio = used / quota if quota > 0 else 0.0
    if ratio >= THRESHOLDS["paused"]:
        state = "PAUSED"
    elif ratio >= THRESHOLDS["degraded"]:
        state = "DEGRADED"
    elif ratio >= THRESHOLDS["alert"]:
        state = "ALERT"
    else:
        state = "NORMAL"
    prev_row = db.execute(
        "SELECT state FROM budget_state WHERE project=? ORDER BY id DESC LIMIT 1", (project,)
    ).fetchone()
    prev = prev_row["state"] if prev_row else None
    if prev != state:
        db.execute("INSERT INTO budget_state (project, state, reason) VALUES (?,?,?)",
                   (project, state, f"used={used} quota={quota} ratio={ratio:.3f}"))
        db.commit()
    return {"state": state, "used": used, "quota": quota, "ratio": round(ratio, 4)}


def latest_state(db: sqlite3.Connection, project: str = "river") -> str:
    row = db.execute(
        "SELECT state FROM budget_state WHERE project=? ORDER BY id DESC LIMIT 1", (project,)
    ).fetchone()
    return row["state"] if row else "NORMAL"


def budget_status(db: sqlite3.Connection, quota: int | None, *, project: str = "river") -> dict:
    """查询当前态（不评估不写状态机）——供 start_node_run 拒发判定用。"""
    state = latest_state(db, project)
    used = total_calls(db)
    return {"state": state, "used": used, "quota": quota}


# ---------- CLI ----------

def _cli(argv: list[str]):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd, db_path = argv[0], argv[1]
    if cmd == "init":
        db = init_db(db_path)
        tables = [r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        print(f"init ok: {db_path}")
        print(f"tables({len(tables)}): {', '.join(tables)}")
        return 0
    if cmd == "status":
        db = connect(db_path)
        quota = int(argv[2]) if len(argv) > 2 else 20000
        info = evaluate_budget(db, quota)
        print(json.dumps(info, ensure_ascii=False))
        tasks = db.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
        runs = db.execute("SELECT COUNT(*) AS n FROM node_runs").fetchone()["n"]
        print(f"tasks={tasks} node_runs={runs}")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
