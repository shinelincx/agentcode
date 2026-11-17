"""Prompt 黄金用例评测（B 层）

两个层次：
  1) 渲染层（默认，零成本）：按 _templates/evals/golden.yaml 渲染模板，
     断言六段结构、契约章节、scope/约束文案、prompt 长度（成本观察）。
  2) 产出层（`--live`，消耗真实 codex）：仅运行带 output_assert 的用例，
     核对 Markdown 或结构化修订后的文档。用例可附参考图片。

通过率写入 JSON 报告（--json 输出，--out 落盘）；任务模板漂移用 --task-dir 检查。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
import templates

PLATFORM = Path(__file__).parent
EVAL_DIR = PLATFORM.parent / "_templates" / "evals"
GOLDEN = EVAL_DIR / "golden.yaml"
# 评测用固定上下文，避免依赖本地 project.yaml/AGENTS.md（用例可显式覆盖）
DEFAULT_CONTEXT = (
    "- 工程类型：eval-fixture\n- 前端目录：apps/web\n- 后端目录：apps/api\n"
    "- 门禁命令（编排层沙箱外执行）：\n  - typecheck: `npm run typecheck`\n"
    "  - unit: `npm run test:unit`"
)


def load_cases(path: Path = GOLDEN) -> list[dict]:
    """读取黄金用例；YAML 错误向上抛（评测配置损坏不得静默跳过）。"""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return list(data.get("cases") or [])


def _check_text(text: str, spec: dict) -> list[dict]:
    """把 assert/output_assert 规格转成可核对的检查项列表。"""
    checks: list[dict] = []
    for needle in spec.get("contains") or []:
        checks.append({"desc": f"包含 {needle!r}", "ok": needle in text, "detail": ""})
    for needle in spec.get("not_contains") or []:
        checks.append({"desc": f"不包含 {needle!r}", "ok": needle not in text, "detail": ""})
    for pattern in spec.get("regex") or []:
        checks.append({"desc": f"匹配 /{pattern}/",
                       "ok": re.search(pattern, text) is not None, "detail": ""})
    for pattern, count in (spec.get("min_matches") or {}).items():
        found = len(re.findall(pattern, text))
        checks.append({"desc": f"/{pattern}/ 至少 {count} 次", "ok": found >= count,
                       "detail": f"实际 {found}"})
    limit = spec.get("max_chars")
    if limit:
        checks.append({"desc": f"长度 ≤ {limit} 字符", "ok": len(text) <= limit,
                       "detail": f"实际 {len(text)}"})
    return checks


def _check_design(doc: str, requirements: str, spec: dict, tech_stack: dict) -> tuple[list[dict], list[dict]]:
    """走设计消费者，核对实际顺序、范围、原型和传入实现的内容。"""
    import graph as g

    with tempfile.TemporaryDirectory(prefix="design-eval-", ignore_cleanup_errors=True) as temp:
        workspace = Path(temp)
        req_path, design_path = workspace / "REQUIREMENTS.md", workspace / "DESIGN.md"
        req_path.write_text(requirements, encoding="utf-8")
        design_path.write_text(doc, encoding="utf-8")
        (workspace / "project.yaml").write_text(yaml.safe_dump({"tech_stack": tech_stack}), encoding="utf-8")
        prior_workspace = g.RIVER_WS
        try:
            g.bind_workspace(workspace)
            ok, detail = g._design_contract(doc, requirements)
            checks = [{"desc": "实际设计门禁", "ok": ok, "detail": detail}]
            lanes = g._lanes_from_design(design_path, req_path, "eval") if ok else []
            prototype, why = g._needs_prototype(doc, requirements)
        finally:
            g.bind_workspace(prior_workspace)
    order = [lane["name"] for lane in lanes]
    scopes = {lane["name"]: lane["scope"] for lane in lanes}
    if "order" in spec:
        checks.append({"desc": "实际 lane 执行顺序", "ok": order == spec["order"], "detail": repr(order)})
    if "scope" in spec:
        checks.append({"desc": "实际 lane 文件范围", "ok": scopes == spec["scope"], "detail": repr(scopes)})
    if "prototype" in spec:
        checks.append({"desc": "实际原型触发", "ok": prototype is spec["prototype"], "detail": why})
    instructions = {lane["name"]: lane["lane_instruction"] for lane in lanes}
    for lane, lane_spec in (spec.get("lanes") or {}).items():
        for check in _check_text(instructions.get(lane, ""), lane_spec):
            checks.append({**check, "desc": f"{lane} 实现指令 {check['desc']}"})
    return checks, lanes


def evaluate_case(case: dict, *, live: bool = False, workdir: str | None = None,
                  codex_home: str | None = None, timeout: int = 600, lane_patch: bool = False) -> dict:
    """渲染 + 断言单个用例；live 且配置了 output_assert 时追加模型产出断言。"""
    params = dict(case.get("params") or {})
    params.setdefault("project_context", DEFAULT_CONTEXT)
    if requirements_file := case.get("requirements_file"):
        params["requirements_content"] = (EVAL_DIR / requirements_file).read_text(encoding="utf-8")
    prompt = templates.render(case["template"], **params)
    result = {
        "id": case["id"], "template": case["template"], "mode": "render",
        "chars": len(prompt), "est_tokens": len(prompt) // 4,
        "checks": _check_text(prompt, case.get("assert") or {}),
    }
    spec = case.get("output_assert")
    if live and (fixture := case.get("retrospective")):
        import retro_eval

        evidence = retro_eval.evaluate(fixture, workdir, codex_home)
        result.update(mode="live", tokens=evidence["tokens"], retrospective=evidence)
        result["checks"] += evidence["checks"]
        result["passed"] = all(c["ok"] for c in result["checks"])
        return result
    if live and (fixture := case.get("implementation")):
        import lane_eval

        evidence = lane_eval.evaluate(fixture, workdir, codex_home, model_patch=lane_patch)
        result.update(mode="live", tokens=evidence["tokens"], implementation=evidence)
        result["checks"] += evidence["checks"]
        result["passed"] = all(c["ok"] for c in result["checks"])
        return result
    if live and spec:
        import graph as g
        import prompt_output
        import worker as w
        work = Path(workdir or tempfile.mkdtemp(prefix="prompt-eval-"))
        work.mkdir(parents=True, exist_ok=True)
        images = [str(EVAL_DIR / path) for path in case.get("images", [])]
        previous = params.get("previous_document")
        is_prototype = case["template"] == "prototype-gen"
        schema = prompt_output.PROTOTYPE_SCHEMA if is_prototype else (
            prompt_output.REVISION_SCHEMA if previous else None)
        r = w.run_codex(prompt, str(work), codex_home=codex_home, timeout=timeout, sandbox="read-only",
                        images=images, output_schema=schema)
        checks = [{"desc": "codex 退出码 0", "ok": r.returncode == 0,
                   "detail": (r.report or "")[-200:]}]
        doc = ""
        if r.ok and is_prototype:
            try:
                contents = prompt_output.prototype_contents(r.message)
                error = g._prototype_errors(contents, params["requirements_content"])
                checks.append({"desc": "原型产物和 AC 门禁", "ok": not error, "detail": error})
                doc = contents["PROTOTYPE.md"]
                result["html"] = contents["index.html"]
                if not error and "preview" in spec:
                    from prototype_preview import check_preview

                    checks += check_preview(contents["index.html"], spec["preview"])
            except Exception as exc:
                checks.append({"desc": "原型文件或预览执行", "ok": False, "detail": str(exc)[:500]})
        elif r.ok:
            doc = r.message if case["template"] == "requirements-gen" else g._extract_markdown(r.message)
        if r.ok and previous:
            try:
                decision, reason, doc = prompt_output.apply_revision(r.message, previous)
            except ValueError as exc:
                checks.append({"desc": "应用结构化修订", "ok": False, "detail": str(exc)})
                doc = ""
            else:
                revision = spec.get("revision") or {}
                if "decision" in revision:
                    checks.append({"desc": "修订决定", "ok": decision == revision["decision"], "detail": decision})
                if "old_lines" in revision:
                    edits = json.loads(r.message)["edits"]
                    old_lines = [edit["old_line"] for edit in edits]
                    checks.append({"desc": "只编辑指定原稿行", "ok": old_lines == revision["old_lines"],
                                   "detail": repr(old_lines)})
                checks += _check_text(reason, revision.get("reason") or {})
        checks += _check_text(doc, spec)
        for heading, section_spec in (spec.get("sections") or {}).items():
            for check in _check_text(g._section(doc, heading), section_spec):
                checks.append({**check, "desc": f"{heading} {check['desc']}"})
        if design_spec := spec.get("design"):
            tech_stack = case.get("tech_stack", {"web": "apps/web", "api": "apps/api"})
            design_checks, lanes = _check_design(doc, params["requirements_content"], design_spec, tech_stack)
            checks += design_checks
            result["lane_plan"] = lanes
        result.update({"mode": "live", "checks": checks,
                       "tokens": r.tokens, "output_chars": len(doc), "output": doc})
        if previous:
            result["revision_message"] = r.message
    result["passed"] = all(c["ok"] for c in result["checks"])
    return result


def run_eval(only: str = "", *, live: bool = False, workdir: str | None = None,
             codex_home: str | None = None, lane_patch: bool = False) -> dict:
    """运行全部（或 --only 指定）用例，返回带通过率的报告字典。"""
    cases = load_cases()
    if only:
        wanted = {s.strip() for s in only.split(",") if s.strip()}
        cases = [c for c in cases if c["id"] in wanted]
    results = [evaluate_case(c, live=live, workdir=workdir, codex_home=codex_home, lane_patch=lane_patch)
               for c in cases]
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest": templates.manifest_version(),
        "mode": "live" if live else "render",
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "cases": results,
    }


def _print_report(report: dict, verbose: bool) -> None:
    for case in report["cases"]:
        status = "PASS" if case["passed"] else "FAIL"
        print(f"[{status}] {case['id']:<16} {case['template']:<16} "
              f"{case['chars']:>5} chars ≈{case['est_tokens']:>4} tokens")
        if not case["passed"] or verbose:
            for c in case["checks"]:
                mark = "ok" if c["ok"] else "FAIL"
                detail = f"  ({c['detail']})" if c["detail"] and not c["ok"] else ""
                print(f"    - [{mark}] {c['desc']}{detail}")
    print(f"\n合计 {report['total']} 条用例：PASS={report['passed']} "
          f"FAIL={report['failed']} 通过率={report['pass_rate']:.1%}")


def _print_drift(drift: dict) -> None:
    state = "OK" if drift["ok"] else "DRIFT"
    print(f"[{state}] 任务锁 {drift['locked_manifest']} → 当前 {drift['current_manifest']}")
    if drift["ok"]:
        print("模板与任务快照一致。")
        return
    for name in drift["changed"]:
        print(f"  - 已变更：{name}（git checkout <生成时的提交> -- _templates/prompts）")
    for name in drift["missing"]:
        print(f"  - 已删除：{name}")
    if not drift["changed"] and not drift["missing"]:
        print(f"  - {drift['reason']}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Prompt 黄金用例评测（B 层）")
    ap.add_argument("--only", default="", help="逗号分隔用例 id")
    ap.add_argument("--json", action="store_true", help="输出 JSON 报告")
    ap.add_argument("--verbose", action="store_true", help="打印每条断言")
    ap.add_argument("--live", action="store_true", help="调用真实 codex 校验产出结构")
    ap.add_argument("--lane-patch", action="store_true", help="实现黄金用例由模型返回补丁，评测写入临时仓库")
    ap.add_argument("--codex-home", default=None, help="live 模式的 CODEX_HOME（隔离凭据）")
    ap.add_argument("--workdir", default=None, help="live 模式的工作目录（默认临时目录）")
    ap.add_argument("--out", default=None, help="把 JSON 报告写到指定路径")
    ap.add_argument("--task-dir", default=None, help="检查该任务 templates.lock 的漂移")
    args = ap.parse_args(argv)

    if args.task_dir:
        drift = templates.lock_drift(Path(args.task_dir))
        if args.json:
            print(json.dumps(drift, ensure_ascii=False, indent=2))
        else:
            _print_drift(drift)
        return 0 if drift["ok"] else 1

    report = run_eval(args.only, live=args.live,
                      workdir=args.workdir, codex_home=args.codex_home, lane_patch=args.lane_patch)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告已写入 {out}")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report, args.verbose)
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
