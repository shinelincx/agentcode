"""CLI 与控制台共享的人审登记、产物校验及恢复。"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path

import ledger


GATES = {
    "review_g1": ("G1_requirements", ("requirements_doc",)),
    "review_g2": ("G2_design", ("design_doc", "prototype_dir")),
    "review_g3": ("G3_staging", ("deploy_report",)),
}


class ApprovalError(ValueError):
    pass


def _gate(snap) -> tuple[str, str, str]:
    if not snap.next or snap.next[0] not in GATES:
        raise ApprovalError("当前没有挂起的人工审批闸门")
    gate, keys = GATES[snap.next[0]]
    document = snap.values.get(keys[0])
    directories = [Path(snap.values[key]) for key in keys[1:] if snap.values.get(key)]
    if not document or not Path(document).is_file() or any(not path.is_dir() for path in directories):
        raise ApprovalError(f"{gate} 产物缺失，无法审批")
    digest = ledger.artifact_digest([Path(document), *directories])
    return gate, snap.config["configurable"]["checkpoint_id"], digest


def _current(db, task_id: str, gate: str, checkpoint: str, digest: str):
    row = db.execute(
        "SELECT * FROM approvals WHERE task_id=? AND gate_name=? "
        "AND (decision='pending' OR checkpoint_id=?) "
        "ORDER BY (decision='pending') DESC, id DESC LIMIT 1",
        (task_id, gate, checkpoint),
    ).fetchone()
    if row is None:
        aid = ledger.request_approval(db, task_id, gate, digest,
                                      checkpoint_id=checkpoint, commit=False)
        return db.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()
    if row["checkpoint_id"] not in (None, checkpoint):
        raise ApprovalError(f"{gate} 的待审记录与当前挂起轮次不一致")
    if row["checkpoint_id"] is None:
        db.execute("UPDATE approvals SET checkpoint_id=? WHERE id=?", (checkpoint, row["id"]))
    return row


def register(db_path: Path, task_id: str, snap) -> None:
    gate, checkpoint, digest = _gate(snap)
    with closing(ledger.init_db(db_path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        _current(db, task_id, gate, checkpoint, digest)


def revision(row) -> str:
    return f"{row['id']}:{row['artifact_hash']}"


def pending_revision(db_path: Path, task_id: str) -> str:
    with closing(ledger.connect(db_path)) as db:
        row = db.execute(
            "SELECT id, artifact_hash FROM approvals WHERE task_id=? AND decision='pending' "
            "ORDER BY id DESC LIMIT 1", (task_id,),
        ).fetchone()
    return revision(row) if row else ""


def resume(db_path: Path, task_id: str, app, cfg: dict, snap, decision: str,
           by: str, reason: str = "", expected_revision: str = "") -> None:
    from langgraph.types import Command

    if decision not in ("approved", "rejected"):
        raise ApprovalError("decision 须为 approved 或 rejected")
    reason = " ".join(reason.split()) if decision == "rejected" else ""
    if decision == "rejected" and not 1 <= len(reason) <= 2000:
        raise ApprovalError("驳回必须填写理由（1~2000 字）")
    gate, checkpoint, digest = _gate(snap)
    stale = False
    with closing(ledger.init_db(db_path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = _current(db, task_id, gate, checkpoint, digest)
        if row["artifact_hash"] != digest:
            if row["decision"] == "pending":
                db.execute("UPDATE approvals SET artifact_hash=? WHERE id=?", (digest, row["id"]))
            else:
                ledger.request_approval(db, task_id, gate, digest,
                                        checkpoint_id=checkpoint, commit=False)
            stale = True
        elif expected_revision and expected_revision != revision(row):
            raise ApprovalError(f"{gate} 审批轮次或产物已更新，请刷新并重新评审")
        elif row["decision"] == "pending":
            if not ledger.decide_approval(db, row["id"], decision, by, reason):
                raise ApprovalError(f"{gate} 已被处理，请刷新")
        elif row["decision"] != decision or row["reason"] != reason:
            raise ApprovalError(f"{gate} 已登记不同结论，恢复时须沿用原结论和理由")
    if stale:
        raise ApprovalError(f"{gate} 产物已更新，请重新查看后再提交审批")
    app.invoke(Command(resume=decision), cfg)
