"""Single-host execution and checkpoint recovery shared by CLI and dashboard."""
from __future__ import annotations

from contextlib import closing, contextmanager
import errno
import os
from pathlib import Path

import ledger
from task_approval import GATES


REPLAYABLE_NODES = {"requirements": "review_g1", "design": "review_g2"}


class ExecutionError(RuntimeError):
    pass


@contextmanager
def exclusive(workspace: Path):
    """Hold an OS lock until the action or its owning process exits."""
    workspace.mkdir(parents=True, exist_ok=True)
    # Keep the same inode for all entrants, including after a crash.
    with (workspace / ".execution.lock").open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt

                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            raise ExecutionError("项目已有 CLI 或控制台动作执行中，请在结束后重试") from exc
        yield


def resume_interrupted(db_path: Path, task_id: str, app, cfg: dict, snap, entry: str) -> bool:
    """Replay a read-only document node from its checkpoint under the project lock."""
    if not snap.next:
        return False
    pending = snap.next[0]
    if pending in GATES:
        raise ExecutionError(f"任务挂起于 {pending}，请先审批")
    if snap.next != (entry,):
        raise ExecutionError(f"任务中断于 {pending}，请从该节点恢复")
    if entry not in REPLAYABLE_NODES:
        raise ExecutionError(f"任务中断于 {entry}，需人工核查旧进程、产物和副作用")

    with closing(ledger.connect(db_path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if snap.values.get("rejection_reason"):
            gate, artifact_keys = GATES[REPLAYABLE_NODES[entry]]
            rejected = db.execute(
                "SELECT artifact_hash FROM approvals WHERE task_id=? AND gate_name=? AND decision='rejected' "
                "ORDER BY id DESC LIMIT 1", (task_id, gate)
            ).fetchone()
            paths = [Path(snap.values[key]) for key in artifact_keys if snap.values.get(key)]
            if (rejected is None or not paths or not paths[0].is_file()
                    or any(not path.is_dir() for path in paths[1:])
                    or ledger.artifact_digest(paths) != rejected["artifact_hash"]):
                raise ExecutionError(f"{gate} 原稿摘要无法核对，需人工确认修订是否已经发布")
        running = db.execute("SELECT node_name, worker_id FROM node_runs "
                             "WHERE task_id=? AND status='running'", (task_id,)).fetchall()
        if any(row["node_name"] != entry for row in running):
            raise ExecutionError("存在其他未结束节点，需人工核查旧进程和产物")
        db.executemany("UPDATE workers SET status='dead', lease_expires_at=NULL WHERE id=?",
                       [(row["worker_id"],) for row in running if row["worker_id"]])
        db.execute("UPDATE node_runs SET status='cancelled', ended_at=datetime('now'), "
                   "lease_expires_at=NULL WHERE task_id=? AND status='running'", (task_id,))
    app.invoke(None, cfg)
    return True
