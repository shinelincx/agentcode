"""Finish a checkpoint only after its external effect is proven."""
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path

import ledger
import review_delivery as rd
from task_execution import ExecutionError


def manual(reason):
    return ExecutionError(f"需人工核查旧进程、产物和副作用。{reason}")


def staging_project(config):
    project = config.get("project")
    stage = (config.get("deploy") or {}).get("staging") or {}
    if (project in ("river", "proj-b") and stage.get("required")
            and stage.get("commands") == [f"{{project-run}} staging {project} {{delivery_head}}"]
            and not stage.get("smoke") and not stage.get("healthcheck")):
        return project
    return ""


def staging_config_digest(config):
    values = config.get("deploy", {}).get("staging", {})
    return sha256(json.dumps(values, sort_keys=True).encode("utf-8")).hexdigest()


def intent(db, task, node, kind):
    row = db.execute(
        "SELECT o.id AS intent_id, o.node_run_id, o.payload_hash, o.result_ref, "
        "r.status AS run_status, r.attempt, r.worker_id FROM operations o "
        "JOIN node_runs r ON r.id=o.node_run_id WHERE o.task_id=? AND r.node_name=? AND o.op_type=? "
        "ORDER BY r.rowid DESC LIMIT 1", (task, node, kind)
    ).fetchone()
    if row is None or row["run_status"] not in ("running", "done"):
        raise manual("当前检查点缺少可核验的副作用意图")
    return row


def finish(db, task, proof, key, op_type, head, lane=None):
    run = proof["node_run_id"]
    with db:
        db.execute("BEGIN IMMEDIATE")
        cursor = db.execute("UPDATE node_runs SET status='done', ended_at=COALESCE(ended_at,datetime('now')), "
                            "lease_expires_at=NULL WHERE id=? AND task_id=? AND status IN ('running','done')",
                            (run, task))
        if cursor.rowcount != 1:
            raise manual("节点状态已变更")
        existing = db.execute("SELECT node_run_id,payload_hash FROM operations WHERE operation_key=?", (key,)).fetchone()
        if existing and tuple(existing) != (run, head):
            raise manual("已登记的副作用与当前外部结果不同")
        if existing is None:
            db.execute("INSERT INTO operations (task_id,node_run_id,operation_key,op_type,payload_hash,status) "
                       "VALUES (?,?,?,?,?,'done')", (task, run, key, op_type, head))
        db.execute("UPDATE operations SET status='done' WHERE id=?", (proof["intent_id"],))
        db.execute("UPDATE workers SET status='retired', lease_expires_at=NULL WHERE id=?", (proof["worker_id"],))
        if lane:
            db.execute("UPDATE lanes SET status='done', merged_at=COALESCE(merged_at,datetime('now')), "
                       "updated_at=datetime('now') WHERE task_id=? AND name=?", (task, lane))
        db.execute("UPDATE tasks SET status='running', updated_at=datetime('now') WHERE id=?", (task,))


def recover_implementation(db_path, task, app, cfg, snap):
    import graph as graph
    import worker
    state = snap.values
    lanes = state.get("lanes") or state.get("approved_lane_plan") or []
    if (not state.get("approved_g2") or len(lanes) != 1 or state.get("review_lane_feedback")
            or state.get("delivery")):
        raise manual("当前仅支持有批准快照的单 lane 首次提交恢复")
    lane, = lanes
    repo = graph._repo_path()
    with closing(ledger.connect(db_path)) as db:
        proof = intent(db, task, lane["node"], "git_commit_intent")
        target = json.loads(proof["result_ref"])
        try:
            head = rd.revision(repo, "HEAD")
            tree = rd.git(repo, "rev-parse", "HEAD^{tree}").strip()
            branch = rd.git(repo, "symbolic-ref", "--short", "HEAD").strip()
            parents = rd.git(repo, "rev-list", "--parents", "-n", "1", head).split()
            clean = worker.git(repo, "status", "--porcelain", "-z") == (0, "")
        except ValueError as exc:
            raise manual(str(exc)) from exc
        if (not clean or branch != lane["branch"] or branch != target["branch"]
                or parents != [head, target["parent"]] or tree != proof["payload_hash"]):
            raise manual("当前 Git 分支、父提交或树与意图不符")
        part = {"lane": lane["name"], "commit": head, "run": proof["node_run_id"],
                "repaired": target["repaired"], "environment": ""}
        delivery = {"base": target["parent"], "commits": [part], "rechecks": []}
        try:
            rd.collect(repo, delivery, lanes, 0)
        except ValueError as exc:
            raise manual(str(exc)) from exc
        for command, gate in graph._implement_gate_specs(graph._project_config(), lane["name"]):
            row = db.execute("SELECT passed, output FROM gate_results WHERE node_run_id=? AND gate_name=? "
                             "ORDER BY id DESC LIMIT 1",
                             (proof["node_run_id"], f"final_{gate}" if target["repaired"] else gate)).fetchone()
            if not row or not row["passed"] or f"$ {worker.safe_gate_text(command)}" not in row["output"].splitlines():
                raise manual(f"无法绑定当前门禁 {gate}")
        finish(db, task, proof, f"{task}:{lane['node']}:git_commit:{proof['node_run_id']}",
               "git_commit", head, lane["name"])
    updated = app.update_state(cfg, {"implement_summary": f"{lane['name']}:reconciled", "delivery": delivery,
                                     "review_feedback": "", "review_lane_feedback": {}}, as_node="implement")
    app.invoke(None, updated)
    return True


def recover_deployment(db_path, task, app, cfg, snap):
    import graph as graph
    import worker
    from project_runs import staging_proof
    from retro_evidence import delivery_head, read_report, receipt

    current = graph._project_config()
    project = staging_project(current)
    repo = graph._repo_path()
    head = delivery_head(snap.values)
    if not project or not head or rd.revision(repo, "HEAD") != head or graph._lane_status(repo) != (0, ""):
        raise manual("部署配置或交付检出不符合恢复条件")
    with closing(ledger.connect(db_path)) as db:
        proof = intent(db, task, "deploy", "deploy_staging_intent")
        if proof["payload_hash"] != head or proof["result_ref"] != staging_config_digest(current):
            raise manual("部署输入或验收配置已变化")
        docs = Path(db_path).parent / task / "docs"
        tested, _ = read_report(db, task, docs, snap.values, "test", "TEST_REPORT.md", head)
        if not tested:
            raise manual("缺少绑定到当前 HEAD 的独立测试")
        probe = f"{{project-run}} check-staging {project} {head}"
        passed, out = worker.run_gate(probe, repo, timeout=90)
        if not passed:
            raise manual("运行镜像、归档凭证或 readiness 无法确认")
        try:
            release = staging_proof(out, project, head)
        except ValueError as exc:
            raise manual(str(exc)) from exc
        report = docs / "DEPLOY_REPORT.md"
        note = f"## 运行制品\n\n```text\nSTAGING PROOF {json.dumps(release)}\n```"
        report.write_text(graph._render_deploy_report(task, "PASS · 核对既有部署成功",
                          [("reconciled", probe, True, out)], note), encoding="utf-8")
        finish(db, task, proof, f"{task}:deploy_staging:{proof['attempt']}", "deploy_staging", head)
        run = proof["node_run_id"]
        ledger.record_gate_result(db, task, "deploy_reconciled", True, out, node_run_id=run)
    updated = app.update_state(cfg, {"deploy_report": str(report), "deploy_skipped": False,
                                     "deploy_receipt": receipt(report, head, run, skipped=False, release=release),
                                     "g3_receipt": {}}, as_node="deploy")
    app.invoke(None, updated)
    return True
