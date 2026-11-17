"""Use the actual retro node with fixed Git and ledger inputs on both revisions."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
from unittest.mock import patch

import yaml


def _measure(worker, launch, prompt, workdir, **kwargs):
    """Collect CLI event totals without changing the model or its instructions."""
    import worker as runtime

    def json_launch(args, *positional, **options):
        return launch([*args[:2], "--json", *args[2:]], *positional, **options)

    with patch.object(runtime.subprocess, "Popen", json_launch):
        result = worker(prompt, workdir, **kwargs)
    usage = {}
    tool_items = {}
    for line in result.output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "turn.completed":
            for key, value in event.get("usage", {}).items():
                if isinstance(value, int):
                    usage[key] = usage.get(key, 0) + value
        if event.get("type") in ("item.started", "item.completed"):
            item = event.get("item") or {}
            if item.get("type") in ("command_execution", "mcp_tool_call", "web_search", "file_change"):
                tool_items[item.get("id")] = item["type"]
    schema = kwargs.get("output_schema")
    schema_bytes = len(json.dumps(schema, ensure_ascii=False).encode("utf-8")) if schema else 0
    if usage:
        total = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        result = replace(result, tokens=total, output=result.output + f"\ntokens used\n  {total}\n")
    return result, {"model": kwargs.get("model"), "prompt_chars": len(prompt),
                    "prompt_bytes": len(prompt.encode("utf-8")), "schema_bytes": schema_bytes,
                    "response_chars": len(result.message), "usage": usage,
                    "tool_items": sorted(tool_items.values())}


def evaluate(fixture, workdir, codex_home):
    import graph as g
    import ledger
    import review_delivery as rd
    import worker as w

    def check(desc, ok, detail=""):
        return {"desc": desc, "ok": bool(ok), "detail": detail}

    parent = Path(workdir) if workdir else None
    if parent:
        parent.mkdir(parents=True, exist_ok=True)
    workspace = g.RIVER_WS
    with tempfile.TemporaryDirectory(prefix="retro-live-", dir=parent, ignore_cleanup_errors=True) as tmp:
        ws = Path(tmp)
        repo = ws / "repo"
        repo.mkdir()
        for command in (("init","-q","-b","main"), ("config","user.name","Fixture"),
                        ("config","user.email","test@example.invalid"), ("config","commit.gpgsign","false"),
                        ("config","core.autocrlf","false")):
            rd.git(str(repo), *command)
        path = repo / "export.py"
        path.write_bytes(b"def name(): return 'old'\n")
        rd.git(str(repo), "add", ".")
        rd.git(str(repo), "commit", "-qm", "base")
        base = rd.revision(str(repo), "HEAD")
        path.write_bytes(b"def name(): return 'current'\n")
        rd.git(str(repo), "commit", "-qam", "delivery")
        head = rd.revision(str(repo), "HEAD")
        config = {"repo_path": str(repo), "project": "retro-eval", "retro": {"generate": True},
                  "tech_stack": {"gate_commands": {"typecheck":"fixture compile", "unit":"fixture unit"}}}
        home = ws / "codex-home"
        if codex_home:
            home.mkdir()
            shutil.copy2(Path(codex_home) / "auth.json", home / "auth.json")
            (home / "config.toml").write_text('[windows]\nsandbox="unelevated"\n', encoding="utf-8")
        (ws / "project.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        db = ledger.init_db(ws / "state.db")
        try:
            g.bind_workspace(ws)
            task = ledger.create_task(db, fixture["label"])
            docs = ws / task / "docs"
            docs.mkdir(parents=True)
            requirements = "## 验收标准\n- AC-1: 导出函数返回 current。"
            design = "## 实现策略\n- 按补丁核对返回值。"
            for name, text in {"REQUIREMENTS.md": requirements, "DESIGN.md": design,
                               "TEST_REPORT.md": "## 结果\nPASS · 1/1\n## 门禁明细\nunit PASS",
                               "REVIEW.md": "## 结论\nPASS\n当前交付 AC-1 审查通过。",
                               "DEPLOY_REPORT.md": fixture["deploy_report"]}.items():
                (docs / name).write_text(text, encoding="utf-8")

            def run(node, lane=None):
                rid = ledger.start_node_run(db, task, node, lane=lane)
                db.execute("UPDATE node_runs SET status='done' WHERE id=?", (rid,))
                db.commit()
                return rid

            def receipt(name, rid, **extra):
                return {"head": head, "run": rid, "digest": sha256((docs/name).read_bytes()).hexdigest(), **extra}

            old = run("implement_be", "be")
            ledger.record_gate_result(db, task, "final_unit", False, "OLD-ONLY-FAILURE", node_run_id=old)
            current = run("implement_be", "be")
            ledger.record_operation(db, task, task+":fixture", "git_commit", head, node_run_id=current)
            ledger.record_gate_result(db, task, "typecheck", True, "$ fixture compile", node_run_id=current)
            ledger.record_gate_result(db, task, "unit", True, "$ fixture unit", node_run_id=current)
            review, test = run("review"), run("test")
            ledger.record_gate_result(db, task, "review_agent", True, "当前交付 AC-1 审查通过", node_run_id=review)
            ledger.record_gate_result(db, task, "test_unit", True, "当前提交独立断言通过", node_run_id=test)
            state = {"task_id":task,"base_branch":"main","approved_requirements_text":requirements,
                     "approved_design_text":design,"review_receipt":receipt("REVIEW.md",review),
                     "test_receipt":receipt("TEST_REPORT.md",test),"deploy_skipped":fixture["skipped"],
                     "delivery":{"base":base,"commits":[{"lane":"be","run":current,"commit":head}]}}
            if fixture["skipped"]:
                ledger.record_gate_result(db,task,"deploy_skipped",True,"当前任务明确跳过 staging")
                state["deploy_receipt"] = receipt("DEPLOY_REPORT.md","",skipped=True,
                    gate_id=db.execute("SELECT last_insert_rowid()").fetchone()[0])
            else:
                dep = run("deploy")
                ledger.record_gate_result(db, task, "deploy_command_1", True, "staging fixture",node_run_id=dep)
                state["deploy_receipt"] = receipt("DEPLOY_REPORT.md", dep, skipped=False)
                aid = ledger.request_approval(db,task,"G3_staging",ledger.artifact_digest([docs/"DEPLOY_REPORT.md"]))
                ledger.decide_approval(db,aid,"approved","fixture reviewer")
                ledger.set_task_status(db,task,"running")
                state["g3_receipt"] = {"head":head,"approval":aid}
            if fixture.get("stale"):
                state["test_receipt"]["head"] = base
                state["g3_receipt"] = {"head":base,"approval":0}
            measurement = {}
            if codex_home:
                execute, launch = w.run_codex, w.subprocess.Popen

                def measured(prompt, workdir, **kwargs):
                    response, data = _measure(execute, launch, prompt, workdir, **kwargs)
                    measurement.update(data)
                    return response

                with (patch.object(g, "_worker_codex_home", return_value=home),
                      patch.object(w, "run_codex", measured)):
                    g.retro_node(state)
            else:
                g.retro_node(state)
            dest = docs / "RETRO.md"
            output = dest.read_text(encoding="utf-8") if dest.is_file() else ""
            gates = list(db.execute("SELECT passed,output FROM gate_results WHERE task_id=? AND gate_name='retro_doc'",(task,)))
            metric = db.execute("SELECT COALESCE(SUM(tokens_used),0) FROM cost_ledger WHERE task_id=?", (task,)).fetchone()[0]
            checks = [
                check("复盘报告通过生成门禁", bool(gates and gates[-1][0] and output), gates[-1][1] if gates else ""),
                check("审查、门禁和独立测试可追溯到本次交付",
                      head in output and all(k in output for k in ("review", "gate.be.unit", "test.unit"))),
                check("staging 与 G3 状态匹配实际证据",
                      ("staging SKIPPED" in output and "g3 NOT_RUN" in output) if fixture["skipped"]
                      else ("staging PASS" in output and "g3 APPROVED" in output)),
                check("历史失败与未经证实的根因未列为事实",
                      "OLD-ONLY-FAILURE" not in output and "## 推断" in output and "## 待验证" in output),
            ]
            result = db.execute("SELECT status FROM tasks WHERE id=?", (task,)).fetchone()[0]
            return {"checks":checks,"tokens":metric,"output":output,"head":head,"task_status":result,
                    "measurement":measurement}
        finally:
            db.close()
            g.bind_workspace(workspace)
            (home / "auth.json").unlink(missing_ok=True)
