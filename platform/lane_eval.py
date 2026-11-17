"""Run implementation golden cases against isolated Git fixtures."""
from contextlib import closing, nullcontext
from dataclasses import replace
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

import yaml


def _model_patch_worker(real_worker, repo: Path, plan: list[dict], known_paths: set[str]):
    """Apply model-returned file contents inside the disposable fixture only."""
    import worker as w
    from prompt_output import _object

    string = {"type": "string"}
    scope_by_branch = {lane["branch"]: set(lane["scope"]) & known_paths for lane in plan}

    def invoke(prompt: str, workdir: str, **kwargs):
        evidence_schema = kwargs.pop("output_schema", None) or string
        schema = _object({"files": {"type": "array", "items": _object({"path": string, "content": string})},
                          "evidence": evidence_schema})
        rc, branch = w.git(str(repo), "branch", "--show-current")
        if rc or branch.strip() not in scope_by_branch:
            raise ValueError("评测无法确认当前 lane 的文件范围")
        sources = {rel: (repo / rel).read_text(encoding="utf-8") for rel in sorted(scope_by_branch[branch.strip()])
                   if (repo / rel).is_file() and (repo / rel).resolve().is_relative_to(repo)}
        result = real_worker(prompt + "\n\n## 临时评测的交付方式\n"
            "当前运行不需要调用工具。基于以下给出的完整原文返回 JSON。files 只列实际修改的文件，"
            "path 保持给出的仓库相对路径，content 给出完整替换文本。evidence 按输出契约填写。"
            "如未运行验证则如实标注。评测驱动会落盘并独立运行 Git 和测试。\n"
            + json.dumps(sources, ensure_ascii=False), workdir, sandbox="read-only", output_schema=schema, **kwargs)
        if not result.ok:
            return result
        try:
            response = json.loads(result.message)
            contents = {item["path"]: item["content"] for item in response["files"]}
            if (len(contents) != len(response["files"]) or contents.keys() - sources.keys()
                    or any(not isinstance(content, str) for content in contents.values())
                    or not isinstance(response["evidence"], dict if evidence_schema is not string else str)):
                raise ValueError("修改文件超出评测范围或交付字段无效")
            for rel, content in contents.items():
                (repo / rel).write_bytes(content.encode("utf-8"))
        except (OSError, KeyError, TypeError, ValueError) as exc:
            return replace(result, returncode=1, report=f"评测补丁失败：{exc}")
        evidence = response["evidence"]
        message = evidence if isinstance(evidence, str) else json.dumps(evidence, ensure_ascii=False)
        return replace(result, report=message, final_message=message)

    return invoke


def evaluate(fixture: dict, workdir: str | None, codex_home: str | None, *, model_patch: bool = False) -> dict:
    import graph as g
    import ledger
    import worker as w

    def run(repo: Path, *args: str) -> str:
        rc, out = w.git(str(repo), *args)
        if rc:
            raise ValueError(f"Git eval fixture: {args[0]} ({rc}): {out[-400:]}")
        return out

    def check(desc: str, ok: bool, detail: str = "") -> dict:
        return {"desc": desc, "ok": ok, "detail": detail}

    parent = Path(workdir) if workdir else None
    if parent:
        parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lane-live-", dir=parent, ignore_cleanup_errors=True) as temp:
        ws = Path(temp)
        repo = ws / "repo"
        repo.mkdir()
        for rel, content in fixture["files"].items():
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8"))
        run(repo, "init", "-q", "-b", "main")
        for key, value in (("user.name", "AgentCode eval"), ("user.email", "fixture@example.invalid"),
                           ("commit.gpgsign", "false"), ("core.autocrlf", "false"), ("core.quotePath", "true")):
            run(repo, "config", key, value)
        run(repo, "add", ".")
        run(repo, "commit", "-qm", "fixture baseline")
        runner = subprocess.list2cmdline if sys.platform == "win32" else shlex.join
        verify = ws / "verify.py"
        verify_cmd = runner([sys.executable, "-B", str(verify)])
        unit_cmd = runner([sys.executable, "-B", "-m", "unittest", "discover", "-s", "checks"])
        if fixture.get("unit_script"):
            unit_cmd = runner([sys.executable, "-B", fixture["unit_script"]])
        syntax_cmd = runner([sys.executable, "-B", "-c", "import ast,pathlib; [ast.parse(p.read_text(encoding='utf8')) for p in pathlib.Path('apps').rglob('*.py')]"])
        config = {"repo_path": str(repo), "tech_stack": {"api":"apps/api", "web":"apps/web",
                  "gate_commands": {"typecheck": syntax_cmd, "unit": unit_cmd}}}
        fixture_home = ws / "eval-codex-home"
        if codex_home:
            fixture_home.mkdir()
            shutil.copy2(Path(codex_home)/"auth.json", fixture_home/"auth.json")
            windows_config = '[windows]\nsandbox = "unelevated"\n\n' if sys.platform == "win32" else ""
            (fixture_home/"config.toml").write_text(windows_config +
                f'[projects.{json.dumps(str(repo))}]\ntrust_level = "trusted"\n', encoding="utf-8")
            config["executor"] = {"codex_home": str(fixture_home)}
        (ws/"project.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        if fixture.get("seed_patch"):
            (ws/"rules.yaml").write_text("implement:\n  gate_fix_attempts: 2\n", encoding="utf-8")
        req, design = ws/"REQUIREMENTS.md", ws/"DESIGN.md"
        req.write_text(fixture["requirements"], encoding="utf-8")
        design.write_text(fixture["design"], encoding="utf-8")
        prior = g.RIVER_WS
        checks = []
        try:
            g.bind_workspace(ws)
            with closing(ledger.init_db(ws/"state.db")) as db:
                task = ledger.create_task(db, "implementation golden fixture")
                plan = g._lanes_from_design(design, req, task)
                worker = (_model_patch_worker(w.run_codex, repo, plan, set(fixture["files"]))
                          if model_patch else w.run_codex)
                repair_messages = []

                def seeded_worker(prompt, workdir, **kwargs):
                    if "You are the Fix Worker" in prompt:
                        response = worker(prompt, workdir, **kwargs)
                        repair_messages.append(response.message)
                        return response
                    for rel, content in fixture["seed_patch"].items():
                        (repo / rel).write_bytes(content.encode("utf-8"))
                    return w.WorkerResult(0, "", 0, "fixture seed", final_message="fixture seed")

                selected_worker = (patch.object(w, "run_codex", seeded_worker) if fixture.get("seed_patch")
                                   else patch.object(w, "run_codex", worker) if model_patch else nullcontext())
                with selected_worker:
                    result = g.implement_node({"task_id":task,"base_branch":"main", "approved_g2":True,
                        "requirements_doc":str(req), "design_doc":str(design), "approved_lane_plan":plan})
                commit_paths = run(repo,"diff","--name-only","--no-renames","-z","main..HEAD").split("\0")
                commit_paths = [p for p in commit_paths if p]
                commits = run(repo, "log", "--reverse", "--format=%h %s", "main..HEAD").splitlines()
                gates = [dict(row) for row in db.execute("SELECT gate_name,passed,output FROM gate_results WHERE task_id=? ORDER BY id",(task,))]
                usage = [dict(row) for row in db.execute(
                    "SELECT agent_role, model, tokens_used FROM cost_ledger WHERE task_id=? ORDER BY id", (task,))]
                task_status = db.execute("SELECT status FROM tasks WHERE id=?", (task,)).fetchone()[0]
                tokens = sum(row["tokens_used"] or 0 for row in usage)
                expected = set(fixture["paths"])
                try:
                    # Create the independent assertion after the Worker and observe the committed revision.
                    verify.write_text(fixture["verify"], encoding="utf-8")
                    run(repo, "worktree", "add", "--detach", str(ws/"verified"), "HEAD")
                    behavior, details = w.run_gate(verify_cmd,str(ws/"verified"))
                except ValueError as exc:
                    behavior, details = False, str(exc)
                checks.append(check("实际提交包含精确范围内的文件", set(commit_paths) == expected, repr(commit_paths)))
                checks.append(check("提交版本通过独立行为断言", behavior, details))
                if fixture.get("repair_outcome") == "blocked":
                    handoffs = [row["output"] for row in gates if row["gate_name"] == "gate_fix_handoff"]
                    checks.append(check("G2 冲突交接给人工", any("G2_CONFLICT" in out for out in handoffs),
                                        result.get("implement_summary", "")))
                    checks.append(check("受阻只调用一次修正 Worker", len(repair_messages) == 1,
                                        f"调用次数 {len(repair_messages)}"))
                else:
                    checks.append(check("Lane 运行完成", bool(plan) and result.get("implement_summary") ==
                                        "; ".join(f"{item['name']}:done" for item in plan), result.get("implement_summary", "")))
                if diagnostic := fixture.get("repair_diagnostic"):
                    checks.append(check("保留失败首条诊断与后续修正记录",
                                        any(row["gate_name"] == "gate_attempt" and diagnostic in row["output"] for row in gates)
                                        and any(row["gate_name"] == "gate_fix_attempt" for row in gates), diagnostic))
                return {"checks": checks,"tokens":tokens,"usage":usage,"changed":commit_paths,"gate_results":gates,
                        "commits":commits,"summary":result.get("implement_summary", ""),
                        "worker_mode":"model-patch" if model_patch else "direct", "task_status":task_status,
                        "repair_messages":repair_messages}
        finally:
            g.bind_workspace(prior)
            (fixture_home/"auth.json").unlink(missing_ok=True)
            for credential in ws.glob("*/workers/*/codex-home/auth.json"):
                credential.unlink(missing_ok=True)
