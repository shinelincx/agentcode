"""LangGraph 编排骨架 · V2.2 §2.4（kill 语义修订版）

双账本分离（V2.1 核验结论）：图执行位置存 graph.db（checkpointer），
业务事实存 state.db（ledger）——两者不合并。
人审闸门用 interrupt()：图挂起时状态已持久化，等数天再批准不丢进度。
P1 骨架节点为轻量占位（Worker/codex 调用接入为下一步）；本骨架验证
interrupt + checkpointer + 崩溃恢复语义。
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

sys.path.insert(0, str(Path(__file__).parent))
import ledger
import intake
import meter
import prompt_output
import retro_evidence
import rules as rulebook
from templates import render as tmpl_render, snapshot as tmpl_snapshot

PLATFORM = Path(__file__).parent
PROJECTS_DIR = PLATFORM.parent / "projects"
RIVER_WS = PROJECTS_DIR / "river"
# 预算边界控制（V2.2 §8.4）：节点边界评估 + 单任务上限 U；计量失败落盘缓冲（AC-14）
# 运行时由 refresh_runtime_config() 从 project.yaml 刷新（环境变量优先，控制台可配置）
_DEFAULT_QUOTA = 20000
_DEFAULT_TASK_CALL_LIMIT = 200
QUOTA = int(os.environ.get("RIVER_QUOTA", str(_DEFAULT_QUOTA)))
TASK_CALL_LIMIT = int(os.environ.get("RIVER_TASK_CALL_LIMIT", str(_DEFAULT_TASK_CALL_LIMIT)))
USAGE_BUFFER = RIVER_WS / "usage_buffer.jsonl"


def set_project(name: str) -> Path:
    """切换当前项目工作区（RIVER_WS）并同步 USAGE_BUFFER。

    供 run_task.py --project / dashboard --project / 测试使用。
    _project_config() 会自动读 RIVER_WS/project.yaml，故切换即生效。
    返回新项目工作区路径。
    """
    return bind_workspace(PROJECTS_DIR / name)


def bind_workspace(workspace: Path) -> Path:
    """同步图节点使用的工作区和计量缓冲路径。"""
    global RIVER_WS, USAGE_BUFFER
    RIVER_WS = workspace
    USAGE_BUFFER = RIVER_WS / "usage_buffer.jsonl"
    return RIVER_WS


def list_projects() -> list[str]:
    """扫描 projects/*/project.yaml，返回已注册项目名列表（字母序）。"""
    if not PROJECTS_DIR.is_dir():
        return []
    return sorted(p.parent.name for p in PROJECTS_DIR.glob("*/project.yaml"))
# A2 codex 输出解析锚点（空/残缺产物一律判失败）；长度阈值已外置到 rules.yaml
_TOKENS_MARK_RE = re.compile(r"^\s*tokens\s+used", re.MULTILINE | re.IGNORECASE)
# codex stderr 日志行（形如 "2026-11-12T03:37:12.342466Z  INFO ..."），混入正文时须截断
_LOG_LINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*\n([\s\S]*?)\n```\s*$")
# 原型/UI 角色触发判定（V2.2 §06「界面变化需验证」）：按归属表表头定位 lane 列
_LANE_ALIASES = {"前端": "fe", "frontend": "fe", "front-end": "fe",
                 "后端": "be", "backend": "be", "back-end": "be"}
_UI_EN_RE = re.compile(r"\b(ui|frontend|front-end|css|html|tsx|jsx|vue)\b", re.IGNORECASE)
# 契约核对：AC 编号归一化（AC-1 / AC 1 / AC1 → AC-1）
_AC_ITEM = r"^\s*(?:(?:[-+*]|\d+[.)])\s+)?(?:`|\*\*)?AC[- ]?([1-9]\d*)(?:`|\*\*)?\s*"
_AC_CRITERION = re.compile(_AC_ITEM + r"[:：]\s*\S", re.IGNORECASE)
_AC_MAPPING = re.compile(_AC_ITEM + r"(?:→|->)\s*(\S.*)", re.IGNORECASE)
_DESIGN_ORDER = re.compile(r"(?m)^[ \t]*(?:-[ \t]+)?执行顺序[ \t]*[:：][ \t]*([^\r\n]+)$")


def _rule_int(section: str, key: str) -> int:
    """读 rules.yaml 的整型规则（缺文件/坏值回退默认值）。"""
    return rulebook.int_rule(RIVER_WS, section, key)


class DeliveryState(TypedDict, total=False):
    """total=False：允许部分键缺席（interrupt 恢复场景）。

    根因修复（task-003）：未声明于 schema 的键（lanes/base_branch 等）会被
    LangGraph 在 invoke 时静默丢弃 → implement 收到空 prompt。
    所有经 state 传递的键必须在此声明。
    """
    task_id: str
    requirement: str
    requirements_doc: str
    design_doc: str
    prototype_dir: str
    prototype_feedback: str
    approved_g1: bool
    approved_g2: bool
    approved_g3: bool
    rejection_reason: str  # 最近一次人工驳回的理由（R1：回传给上游 Agent 判断是否修订）
    revises_g1: int  # 各闸门驳回回环次数（防无限循环，超过上限判 rejected 终态）
    revises_g2: int
    revises_g3: int
    base_branch: str
    lanes: list  # [{name, node, branch, scope, prompt, commit_msg}]
    approved_lane_plan: list[dict]
    approved_requirements_text: str
    approved_design_text: str
    delivery: dict
    review_lane_feedback: dict[str, str]
    review_receipt: dict
    review_deferred_tests: dict[str, str]
    test_receipt: dict
    deploy_receipt: dict
    g3_receipt: dict
    implement_summary: str
    review_report: str
    review_feedback: str
    previous_review: str
    review_rework_count: int  # 本轮连续审查失败次数；手动重跑时清零
    test_report: str
    deploy_report: str
    deploy_skipped: bool
    approved_g3: bool


def _ledger():
    db = ledger.connect(RIVER_WS / "state.db")
    return db


def _base_codex_home() -> Path:
    """凭据基目录：环境变量 > project.yaml executor.codex_home > 工作区默认。"""
    env = os.environ.get("RIVER_CODEX_HOME")
    if env:
        return Path(env)
    configured = (_project_config().get("executor") or {}).get("codex_home")
    if configured:
        return Path(str(configured))
    return RIVER_WS / "codex-home"


def _worker_codex_home(task_id: str, worker_id: str) -> Path:
    """B1 凭据隔离（AC-04）：每个 Worker 使用独立 CODEX_HOME，互不可读。

    首次使用从平台基目录复制 auth.json / config.toml；基目录缺文件时不造空壳，
    交由 worker.run_codex 明确报错（C-007：缺 config.toml 会变成只读空跑）。
    """
    home = RIVER_WS / task_id / "workers" / worker_id / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    base = _base_codex_home()
    for name in ("auth.json", "config.toml"):
        src, dst = base / name, home / name
        if src.is_file() and not dst.is_file():
            shutil.copy2(src, dst)
    return home


def _start_leased_run(db, task_id: str, node_name: str, *, with_codex_home: bool = True, **kw):
    """登记 node_run → Worker 身份 → 租约 epoch。返回 (run, worker_id, epoch, codex_home)。

    test/deploy 不调用 codex，传 with_codex_home=False 跳过凭据复制（租约仍照常发放）。
    """
    run = ledger.start_node_run(db, task_id, node_name, **kw)
    worker_id = ledger.register_worker(db, role=node_name)
    epoch = ledger.acquire_lease(db, worker_id, run)
    home = _worker_codex_home(task_id, worker_id) if with_codex_home else None
    return run, worker_id, epoch, home


def _close(db, run: str, status: str, worker_id: str | None = None,
           epoch: int | None = None) -> bool:
    """带栅栏收尾。返回 False 表示本 Worker 的 epoch 已失效——不得再写任务状态。"""
    if epoch is None:  # 兼容路径（旧用例 / 未接租约的调用）
        return ledger.finish_node_run(db, run, status)
    try:
        ledger.finish_node_run(db, run, status, worker_id=worker_id, epoch=epoch)
    except ledger.StaleLeaseError as exc:
        print(f"[lease] stale write rejected: {exc}")
        return False
    try:
        ledger.release_lease(db, worker_id, run, epoch)
    except ledger.StaleLeaseError:
        pass
    return True


def _extract_markdown(output: str) -> str:
    """从 codex exec 原始输出抽取 Markdown 正文（A2 文档生成用）。

    codex stdout 结构：会话头/思考日志 → 最终报告 → "tokens used" 统计。
    抽取：截断统计段 → 自首个一级标题起 → 去整篇包裹的代码围栏。
    抽不到返回空串——由 _doc_gate 判失败，绝不静默放行（C-005/C-007 同族防线）。
    """
    text = output or ""
    m = _TOKENS_MARK_RE.search(text)
    if m:
        text = text[:m.start()]
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("# ")), None)
    if start is None:
        return ""
    # stdout/stderr 拼接后顺序可能交错：正文末尾可能跟着 codex INFO 日志行，截断之
    end = next((j for j in range(start, len(lines)) if _LOG_LINE_RE.match(lines[j])), len(lines))
    body = "\n".join(lines[start:end]).strip()
    fence = _FENCE_RE.match(body)
    if fence:
        body = fence.group(1).strip()
    return body


def _doc_gate(db, task_id: str, node_name: str, run: str, doc: str,
              required_headings: tuple[str, ...], *,
              draft_title: str = "",
              worker_id: str | None = None, epoch: int | None = None) -> bool:
    """文档产出校验：空/过短/缺章节均判失败并留痕（不进入人审）。"""
    min_len = _rule_int("doc", "min_len")
    max_len = _rule_int("doc", "max_len")
    if not doc:
        reason = "Agent 未产出可解析的 Markdown（无一级标题）"
    elif draft_title and doc.splitlines()[0] != draft_title:
        reason = f"首行须为 {draft_title}"
    elif len(doc) < min_len:
        reason = f"Agent 产物过短（{len(doc)} 字符 < {min_len}）"
    elif len(doc) > max_len:
        reason = f"Agent 产物异常超长（{len(doc)} 字符 > {max_len}，疑似日志噪声混入）"
    else:
        sections = _sections(doc)
        missing = [h for h in required_headings if h not in sections or (draft_title and not sections[h])]
        reason = f"缺少或未填写必需章节：{missing}" if missing else ""
    if reason:
        ledger.record_gate_result(db, task_id, f"{node_name}_doc", False, reason, node_run_id=run)
        if _close(db, run, "failed", worker_id, epoch):  # 租约失效则不覆盖任务状态
            ledger.set_task_status(db, task_id, "gate_failed", node=node_name)
        return False
    ledger.record_gate_result(db, task_id, f"{node_name}_doc", True, f"{len(doc)} 字符",
                              node_run_id=run)
    return True


def _project_context() -> str:
    """渲染期注入的工程上下文：技术栈/目录/门禁命令 + 项目记忆（AGENTS.md）。

    单一来源原则：这些事实由 project.yaml 与 projects/<proj>/AGENTS.md 维护，
    模板不再硬编码副本（C-001/C-002 曾以文案形式与记忆条目重复）。
    """
    config = _project_config()
    tech = config.get("tech_stack") or {}
    lines: list[str] = []
    if tech.get("type"):
        lines.append(f"- 工程类型：{tech['type']}")
    if tech.get("web"):
        lines.append(f"- 前端目录：{tech['web']}")
    if tech.get("api"):
        lines.append(f"- 后端目录：{tech['api']}")
    commands = tech.get("gate_commands") or {}
    if commands:
        lines.append("- 门禁命令（编排层沙箱外执行）：")
        lines += [f"  - {name}: `{cmd}`" for name, cmd in commands.items()]
    memory_path = RIVER_WS / "AGENTS.md"
    if memory_path.is_file():
        memory = memory_path.read_text(encoding="utf-8", errors="replace").strip()
        if memory:
            lines += ["", "项目记忆（AGENTS.md，含历史坑与约定）：",
                      memory[:_rule_int("memory", "context_max")]]
    return "\n".join(lines) if lines else "- （project.yaml 未配置 tech_stack，且无项目记忆条目）"


def _sections(text: str) -> dict[str, str]:
    """读取独立的二级标题及其正文，忽略代码围栏内的内容和重复标题。"""
    sections: dict[str, list[str]] = {}
    duplicates = set()
    heading, fence = "", ""
    for line in text.splitlines():
        stripped = line.strip()
        if fence:
            if len(stripped) >= len(fence) and set(stripped) == {fence[0]}:
                fence = ""
            continue
        opening = re.match(r"`{3,}|~{3,}", stripped)
        if opening:
            fence = opening.group()
            continue
        if re.match(r"^ {0,3}## \S", line):
            heading = stripped
            if heading in sections:
                duplicates.add(heading)
            sections[heading] = []
        elif heading:
            sections[heading].append(line)
    return {key: "\n".join(body).strip() for key, body in sections.items() if key not in duplicates}


def _section(text: str, heading: str) -> str:
    return _sections(text).get(heading, "")


def _review_verdict(doc: str) -> tuple[bool, str]:
    sections = _sections(doc)
    required = ("## 结论", "## 问题", "## 建议")
    missing = [heading for heading in required if not sections.get(heading)]
    if missing:
        return False, f"缺少或未填写必需章节：{missing}"
    lines = [line.strip() for line in sections["## 结论"].splitlines() if line.strip()]
    if (len(lines) < 2 or lines[0] not in ("PASS", "FAIL")
            or any(line in ("PASS", "FAIL") for line in lines[1:])):
        return False, "## 结论须在独立首行给出唯一的 PASS 或 FAIL，下一行说明原因"
    if lines[0] == "FAIL":
        return False, "Agent 判定 FAIL"
    if sections["## 问题"].lstrip("-*+ ").strip() != "无":
        return False, "Agent 判定 PASS，但 ## 问题 仍列有待修正项"
    return True, "Agent 判定 PASS"


def _ac_ids(text: str, pattern: re.Pattern) -> list[str]:
    return [f"AC-{match.group(1)}" for line in text.splitlines()
            if (match := pattern.match(line))]


def _requirements_ac(requirements: str) -> tuple[bool, str]:
    wanted = _ac_ids(_section(requirements, "## 验收标准"), _AC_CRITERION)
    if not wanted:
        return False, "## 验收标准 至少须有一条 AC-n: 可验证条件"
    if len(wanted) != len(set(wanted)):
        return False, "## 验收标准 存在重复 AC 编号"
    return True, f"{len(wanted)} 条 AC"


def _ac_coverage(design: str, requirements: str) -> tuple[bool, str]:
    """核对每条验收标准均有一条实际映射，映射内容由 G2 审阅。"""
    ok, reason = _requirements_ac(requirements)
    if not ok:
        return ok, reason
    wanted = _ac_ids(_section(requirements, "## 验收标准"), _AC_CRITERION)
    covered = _ac_ids(_section(design, "## 验收映射"), _AC_MAPPING)
    if len(covered) != len(set(covered)):
        return False, "## 验收映射 存在重复 AC 编号"
    covered_ids = set(covered)
    missing = [ac for ac in wanted if ac not in covered_ids]
    if missing:
        return False, f"验收映射缺少：{', '.join(missing)}（需求共 {len(wanted)} 条）"
    extra = covered_ids - set(wanted)
    if extra:
        return False, f"验收映射含未知 AC：{', '.join(sorted(extra))}"
    return True, f"{len(wanted)} 条 AC 均有验收映射"


def _run_doc_agent(state: DeliveryState, *, node_name: str, template: str,
                   params: dict, filename: str,
                   required_headings: tuple[str, ...],
                   post_check=None, post_check_name: str = "",
                   images: list[str] | None = None) -> str:
    """A2：Agent 生成文档（requirements/design 共用）。返回文档路径，失败返回空串。

    失败语义：预算拒发 → awaiting_human；执行异常/产物不合法 → gate_failed。
    两种情况都返回空串，由条件边终止图（不进入 G1/G2 人审）。
    B1：节点执行绑定 Worker 身份 + 租约 epoch，凭据目录按 Worker 隔离。
    images：参考图片绝对路径，仅需求节点使用（codex -i 附加到初始 prompt）。
    post_check：契约级机械核对（doc, previous -> (ok, reason)），在收尾前执行并入账。
    """
    import worker as w

    db = _ledger()
    task_id = state["task_id"]
    docs_dir = RIVER_WS / task_id / "docs"
    doc_path = docs_dir / filename
    # 模板版本快照（幂等：仅首次写）
    if not (RIVER_WS / task_id / "templates.lock").exists():
        try:
            tmpl_snapshot(RIVER_WS / task_id)
        except Exception as exc:  # 快照失败不阻断生成
            print(f"[templates] snapshot failed: {exc}")
    meter.flush_buffer(db, USAGE_BUFFER)
    budget = ledger.evaluate_budget(db, QUOTA)
    attempt = int(db.execute(
        "SELECT COUNT(*) AS n FROM node_runs WHERE task_id=? AND node_name=?",
        (task_id, node_name)).fetchone()["n"]) + 1
    try:
        run, worker_id, epoch, codex_home = _start_leased_run(
            db, task_id, node_name, attempt=attempt, output_path=str(doc_path))
    except PermissionError as exc:  # PAUSED / 低优先级排队（AC-13）
        ledger.record_gate_result(db, task_id, "budget_paused", False, str(exc))
        ledger.set_task_status(db, task_id, "awaiting_human", node=node_name)
        return ""
    model = route_model(budget["state"], node_name)
    repo = _repo_path()
    # G-01：文档节点只读执行。矩阵要求需求/设计/Retro 对项目仓库只读（V2.2 §6.4），
    # 沙箱层用 read-only；收尾前再比对脏文件，防止沙箱配置缺失导致静默降级（C-007 教训）。
    dirty_before = _repo_dirty(repo)
    previous = None
    try:
        if state.get("rejection_reason"):
            previous = doc_path.read_bytes().decode("utf-8")
            params = {**params, "previous_document": previous}
        prompt = tmpl_render(template, task_id=task_id[:8],
                             project_context=_project_context(), **params)
        r = w.run_codex(prompt, repo, model=model, codex_home=str(codex_home),
                        log_dir=str(RIVER_WS / task_id / "logs"), sandbox="read-only",
                        images=images, output_schema=prompt_output.REVISION_SCHEMA if previous is not None else None)
    except Exception as exc:  # 异常必须收尾，避免孤儿 running（P2-0 教训）
        ledger.record_gate_result(db, task_id, f"{node_name}_exec", False,
                                  str(exc)[:500], node_run_id=run)
        if _close(db, run, "failed", worker_id, epoch):
            ledger.set_task_status(db, task_id, "gate_failed", node=node_name)
        return ""
    meter.record_usage_safe(db, buffer_path=USAGE_BUFFER, task_id=task_id,
                            agent_role=node_name, executor="codex", model=model,
                            output_text=r.output, node_run_id=run)
    newly_dirty = sorted(set(_repo_dirty(repo)) - set(dirty_before))
    if newly_dirty:
        ledger.record_gate_result(
            db, task_id, "doc_repo_clean", False,
            "文档节点改动了项目仓库：" + ", ".join(newly_dirty[:10]), node_run_id=run)
        if _close(db, run, "failed", worker_id, epoch):
            ledger.set_task_status(db, task_id, "gate_failed", node=node_name)
        return ""
    ledger.record_gate_result(db, task_id, "doc_repo_clean", True,
                              "文档节点未改动项目仓库", node_run_id=run)
    decision = revision_reason = ""
    draft_title = f"# {Path(filename).stem} · task {task_id[:8]}" if previous is None else ""
    try:
        if not r.ok:
            raise ValueError(f"Worker 执行失败（退出码 {r.returncode}）")
        if previous is None:
            doc = r.message.rstrip() if draft_title else _extract_markdown(r.message)
        else:
            decision, revision_reason, doc = prompt_output.apply_revision(r.message, previous)
            if doc_path.read_bytes() != previous.encode("utf-8"):
                raise ValueError("原稿在修订期间已变化，请重新核对")
    except (ValueError, OSError) as exc:
        gate = f"{node_name}_exec" if previous is None or not r.ok else f"{node_name}_revision_decision"
        ledger.record_gate_result(db, task_id, gate, False, str(exc)[:500], node_run_id=run)
        if _close(db, run, "failed", worker_id, epoch):
            ledger.set_task_status(db, task_id, "gate_failed", node=node_name)
        return ""
    if not _doc_gate(db, task_id, node_name, run, doc, required_headings,
                     draft_title=draft_title, worker_id=worker_id, epoch=epoch):
        return ""
    if post_check is not None:
        ok, check_reason = post_check(doc, previous)
        ledger.record_gate_result(db, task_id, post_check_name or f"{node_name}_contract",
                                  ok, check_reason, node_run_id=run)
        if not ok:
            if _close(db, run, "failed", worker_id, epoch):
                ledger.set_task_status(db, task_id, "gate_failed", node=node_name)
            return ""
    try:
        if decision != "KEEP":
            docs_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".doc-", dir=docs_dir) as temp:
                candidate = Path(temp) / filename
                candidate.write_bytes((doc if decision else doc + "\n").encode("utf-8"))
                candidate.replace(doc_path)
    except (OSError, UnicodeError) as exc:
        ledger.record_gate_result(db, task_id, f"{node_name}_publish", False,
                                  str(exc)[:500], node_run_id=run)
        if _close(db, run, "failed", worker_id, epoch):
            ledger.set_task_status(db, task_id, "gate_failed", node=node_name)
        return ""
    if decision:
        ledger.record_gate_result(db, task_id, f"{node_name}_revision_decision", True,
                                  f"{decision} · {revision_reason[:500]}", node_run_id=run)
    if not _close(db, run, "done", worker_id, epoch):
        return ""  # 租约被接管：旧 Worker 不得宣告完成
    return str(doc_path)


def _task_images(task_id: str) -> list[str]:
    return [str(path) for path, _ in intake.reference_images(
        RIVER_WS, task_id, intake.read_original(RIVER_WS, task_id))]


def requirements_node(state: DeliveryState) -> dict:
    """需求节点（A2）：原始需求（文本 + 参考图片）→ codex(requirements-gen) → REQUIREMENTS.md。"""
    db = _ledger()
    raw = (state.get("requirement") or "").strip()
    if not raw:
        # 输入边界校验：无原始需求不得进入生成，直接失败留痕
        ledger.record_gate_result(db, state["task_id"], "requirement_input", False,
                                  "state.requirement 为空，无法生成需求文档")
        ledger.set_task_status(db, state["task_id"], "gate_failed", node="requirements")
        return {"requirements_doc": "", "rejection_reason": ""}
    task_id = state["task_id"]
    images = _task_images(task_id)
    attachments = f"已附 {len(images)} 张参考图。" if images else "无"
    # R1：被人工驳回过 → 把驳回理由交给需求 Agent，由它判断是否按反馈修订
    feedback = (state.get("rejection_reason") or "").strip()
    if feedback:
        ledger.record_gate_result(db, state["task_id"], "requirements_revise", True,
                                  f"按驳回理由修订第 {int(state.get('revises_g1') or 0)} 轮：{feedback[:200]}")
    doc = _run_doc_agent(state, node_name="requirements", template="requirements-gen",
                         params={"raw_requirement": raw, "attachments": attachments,
                                 "rejection_feedback": feedback or "无"},
                         filename="REQUIREMENTS.md",
                         required_headings=("## 需求", "## 验收标准", "## 非目标"),
                         post_check=lambda doc, _: _requirements_ac(doc), post_check_name="requirements_ac",
                         images=images)
    return {"requirements_doc": doc, "rejection_reason": ""}


# 人工驳回 → 上游 Agent 修订的回环上限（R1）：超过即判 rejected 终态，防止无限回环烧预算
MAX_REVISES = 5


def review_g1(state: DeliveryState) -> Command:
    """人工闸门 G1：interrupt 挂起，批准后前进；驳回须带理由，回需求节点按理由修订（R1）。

    修订轮次超过 MAX_REVISES 时终止为 rejected 终态，防止无限回环烧预算。
    """
    decision = interrupt({"gate": "G1_requirements", "artifact": state["requirements_doc"]})
    if decision == "approved":
        db = _ledger()
        ledger.set_task_status(db, state["task_id"], "running")
        requirements = Path(state["requirements_doc"]).read_text(encoding="utf-8")
        return Command(goto="design", update={"approved_g1": True, "rejection_reason": "",
                                               "approved_requirements_text": requirements})
    db = _ledger()
    reason = ledger.latest_rejection_reason(db, state["task_id"], "G1_requirements")
    revises = int(state.get("revises_g1") or 0) + 1
    if revises > MAX_REVISES:
        ledger.record_gate_result(db, state["task_id"], "revise_limit_g1", False,
                                  f"G1 驳回修订已达 {MAX_REVISES} 次上限，终止为 rejected")
        ledger.set_task_status(db, state["task_id"], "rejected", node="requirements")
        return Command(goto=END, update={"revises_g1": revises})
    ledger.set_task_status(db, state["task_id"], "running")
    return Command(goto="requirements",
                   update={"approved_g1": False, "rejection_reason": reason,
                           "revises_g1": revises})


def _lane_table(design: str) -> tuple[list[str] | None, list[list[str]]]:
    """解析「## 文件归属表」的首个 Markdown 表格（表头 + 数据行）。"""
    section = _sections(design).get("## 文件归属表", design)
    lines = [ln.strip() for ln in section.splitlines()]
    for i, line in enumerate(lines):
        if not line.startswith("|") or i + 1 >= len(lines):
            continue
        if not re.match(r"^\|[\s:|-]+\|$", lines[i + 1]):
            continue
        header = [c.strip() for c in line.strip("|").split("|")]
        rows: list[list[str]] = []
        for row in lines[i + 2:]:
            if not row.startswith("|"):
                break
            rows.append([c.strip() for c in row.strip("|").split("|")])
        return header, rows
    return None, []


def _design_path(path: str) -> str:
    return path.strip("` ").replace("\\", "/").removeprefix("./")


def _lane_ownership(design: str) -> dict[str, list[str]]:
    header, rows = _lane_table(design)
    if header is None:
        return {}
    labels = [item.strip("`").lower() for item in header]
    lane_idx = next((i for i, name in enumerate(labels) if "lane" in name or "角色" in name), None)
    path_idx = next((i for i, name in enumerate(labels)
                     if name in ("路径", "文件", "文件路径", "path", "file", "file path")), None)
    if lane_idx is None or path_idx is None:
        raise ValueError(f"文件归属表须含路径和 lane 列（表头：{', '.join(header)}）")
    lanes: dict[str, list[str]] = {}
    owners = {}
    for row in rows:
        if len(row) <= max(lane_idx, path_idx):
            raise ValueError("文件归属表有缺列的记录")
        path = _design_path(row[path_idx])
        lane = row[lane_idx].strip("` ").lower()
        lane = _LANE_ALIASES.get(lane, lane)
        if not path or not lane or path.startswith("/") or ":" in path or ".." in path.split("/"):
            raise ValueError("文件归属表须给出非空的仓库相对路径和 lane")
        prior = owners.setdefault(os.path.normcase(path), lane)
        if prior != lane:
            raise ValueError(f"文件归属重复：{path}")
        if path not in lanes.setdefault(lane, []):
            lanes[lane].append(path)
    if not lanes:
        raise ValueError("文件归属表无有效数据行")
    return lanes


def _design_contract(design: str, requirements: str, *, require_order: bool = False) -> tuple[bool, str]:
    if not _section(design, "## 文件归属表"):
        return False, "## 文件归属表 缺少文件范围"
    try:
        lanes = _lane_ownership(design)
    except ValueError as exc:
        return False, str(exc)
    if not lanes or set(lanes) - {"be", "fe"}:
        return False, "文件归属表须至少有一个有效的 be 或 fe lane"
    strategy = _section(design, "## 实现策略")
    if not strategy:
        return False, "## 实现策略 须填写实现和验收方式"
    order_notes = _DESIGN_ORDER.findall(strategy)
    expected = " → ".join(lanes)
    if require_order or order_notes:
        if len(order_notes) != 1 or re.split(r"\s*(?:→|->)\s*", order_notes[0].strip()) != list(lanes):
            return False, f"执行顺序须与归属表首次出现一致：{expected}"

    tech = _project_config().get("tech_stack") or {}
    roots = {lane: os.path.normcase(_design_path(tech[key]).rstrip("/"))
             for lane, key in (("be", "api"), ("fe", "web"))
             if isinstance(tech.get(key), str) and tech[key].strip()}
    files = set()
    for lane, paths in lanes.items():
        for path in paths:
            normalized = os.path.normcase(path)
            if any(mark in path for mark in "*?[]") or set(path.split("/")) & {"", "."} or normalized in roots.values():
                return False, f"文件归属表须列具体文件路径：{path}"
            matches = {owner: len(root) for owner, root in roots.items()
                       if root and normalized.startswith(root + os.sep)}
            if matches and matches.get(lane) != max(matches.values()):
                return False, f"文件归属与项目目录配置不一致：{path} 不能归给 {lane}"
            files.add(path)

    ac_ok, ac_reason = _ac_coverage(design, requirements)
    if not ac_ok:
        return False, ac_reason
    for line in _section(design, "## 验收映射").splitlines():
        if not line.strip():
            continue
        match = _AC_MAPPING.fullmatch(line)
        if not match or _design_path(match[2]) not in files:
            return False, f"验收映射须指向归属表中的一个文件：{line.strip()}"
    return True, f"{ac_reason}，lane 顺序 {expected}"


def _needs_prototype(design: str, requirements: str) -> tuple[bool | None, str]:
    """V2.2 §06：界面变化需验证；小型后端任务可跳过 UI。

    有明确决定时使用设计中的生成／跳过理由。无决定时，含 fe 生成，全为 be 跳过。
    历史产物缺表或含未知 lane 时回退关键词。存在但无法解析的表返回 None。
    """
    try:
        lanes = _lane_ownership(design)
    except ValueError as exc:
        return None, str(exc)
    strategy = _section(design, "## 实现策略")
    declarations = re.findall(r"(?m)^[ \t]*(?:-[ \t]+)?原型[ \t]*[:：][ \t]*(.*)$", strategy)
    if declarations:
        declared = re.fullmatch(r"(生成|跳过)[ \t]*\|[ \t]*(\S.*)", declarations[0].strip())
        if len(declarations) != 1 or not declared:
            return None, "原型决定须为唯一的「原型：生成或跳过 | 理由」"
        return declared[1] == "生成", f"设计注明{declared[1]}：{declared[2]}"
    if lanes:
        unknown = set(lanes) - {"fe", "be"}
        if not unknown:
            if "fe" in lanes:
                return True, "DESIGN.md 文件归属表含 fe lane"
            return False, "DESIGN.md 归属表 lane 列为 be，无 fe lane"
        why_prefix = f"归属表含未识别 lane（{', '.join(sorted(unknown))}），回退关键词"
    else:
        why_prefix = "无归属表，命中界面关键词"
    merged = f"{design or ''}\n{requirements or ''}"
    hits = [k for k in (rulebook.load(RIVER_WS)["prototype"]["ui_keywords"] or []) if k in merged]
    if hits:
        return True, f"{why_prefix}：{', '.join(hits[:3])}"
    m = _UI_EN_RE.search(merged)
    if m:
        return True, f"{why_prefix}：{m.group(0)}"
    return False, "归属表无 fe lane，且需求/设计未出现界面关键词"


def _render_prototype_skipped(task_id: str, why: str) -> str:
    return (f"# PROTOTYPE · task {task_id[:8]}\n\n## 原型说明\n"
            f"本轮跳过原型生成。判定依据：{why}。\n\n"
            "## 验收映射\n各项 AC 继续按 DESIGN.md 所列方式验收。\n\n"
            "## 局限\n未生成原型，G2 仅审阅 DESIGN.md。\n")


def _prototype_errors(contents: dict[str, str], requirements: str) -> str:
    """发布前校验原型和 G2 映射。"""
    from prototype_artifact import PrototypeHTML, mapping_errors

    problems: list[str] = []
    html_min = _rule_int("prototype", "html_min")
    html_max = _rule_int("prototype", "html_max")
    md_min = _rule_int("prototype", "md_min")
    html = contents["index.html"]
    md = contents["PROTOTYPE.md"]
    if not html_min <= len(html) <= html_max:
        problems.append(f"index.html 长度 {len(html)} 不在 {html_min}–{html_max} 字符范围")
    parsed = PrototypeHTML()
    parsed.feed(html)
    parsed.close()
    if not re.match(r"\s*<!doctype html\s*>", html, re.IGNORECASE) or not {"html", "body"} <= parsed.tags:
        problems.append("index.html 缺少 DOCTYPE、<html> 或 <body> 骨架")
    if not parsed.visible_body:
        problems.append("index.html 缺少可演示正文")
    problems.extend(dict.fromkeys(parsed.errors))
    sections = _sections(md)
    missing = [h for h in ("## 原型说明", "## 验收映射", "## 局限") if not sections.get(h)]
    if len(md) < md_min:
        problems.append(f"PROTOTYPE.md 过短（{len(md)} < {md_min} 字符）")
    if missing:
        problems.append(f"PROTOTYPE.md 缺少或未填写章节：{missing}")
    ok, reason = _requirements_ac(requirements)
    if ok:
        wanted = _ac_ids(_section(requirements, "## 验收标准"), _AC_CRITERION)
        problems.extend(mapping_errors(sections.get("## 验收映射", ""), wanted, parsed.ids))
    else:
        problems.append(reason)
    return "；".join(problems)


def _prototype_references(task_id: str) -> tuple[list[str], str, list[str]]:
    original = intake.read_original(RIVER_WS, task_id)
    arrived = intake.reference_images(RIVER_WS, task_id, original)
    received = {meta["rel"] for _, meta in arrived}
    missing = [Path(item["rel"]).name for item in (original.get("attachments") or [])
               if isinstance(item, dict) and isinstance(item.get("rel"), str) and item["rel"] not in received]
    labels = [f"- 参考图 {index}：{meta['name']}" for index, (_, meta) in enumerate(arrived, 1)]
    if missing:
        labels.append("已声明但未传入：" + "、".join(missing))
    return [str(path) for path, _ in arrived], "\n".join(labels) or "无", missing


def prototype_node(state: DeliveryState) -> dict:
    """原型/UI 角色（V2.2 §06）：读设计资产，写专属原型目录。

    触发：设计中的明确决定或归属表回退规则。跳过写说明供 G2 核对。
    原型产物：<ws>/<task_id>/prototype/{index.html,PROTOTYPE.md}，
    随 G2 一并人工审阅（审批哈希同时绑定设计文档与原型目录）。
    """
    import worker as w

    db = _ledger()
    task_id = state["task_id"]
    design_path = state.get("design_doc") or ""
    proto_dir = RIVER_WS / task_id / "prototype"
    if not design_path or not Path(design_path).is_file():
        ledger.record_gate_result(db, task_id, "prototype_input", False,
                                  f"设计文档不可读：{design_path!r}")
        ledger.set_task_status(db, task_id, "gate_failed", node="prototype")
        return {"prototype_dir": ""}
    design = Path(design_path).read_text(encoding="utf-8")
    req_path = state.get("requirements_doc") or ""
    req = Path(req_path).read_text(encoding="utf-8") if req_path and Path(req_path).is_file() else ""
    needed, why = _needs_prototype(design, req)
    if needed is None:  # 归属表缺 lane 列/无数据行：门禁失败，不猜测
        ledger.record_gate_result(db, task_id, "prototype_scope", False, why)
        ledger.set_task_status(db, task_id, "gate_failed", node="prototype")
        return {"prototype_dir": ""}

    meter.flush_buffer(db, USAGE_BUFFER)
    budget = ledger.evaluate_budget(db, QUOTA)
    attempt = int(db.execute(
        "SELECT COUNT(*) AS n FROM node_runs WHERE task_id=? AND node_name=?",
        (task_id, "prototype")).fetchone()["n"]) + 1
    try:
        run, worker_id, epoch, codex_home = _start_leased_run(
            db, task_id, "prototype", attempt=attempt,
            output_path=str(proto_dir / "PROTOTYPE.md"), with_codex_home=needed)
    except PermissionError as exc:  # PAUSED / 低优先级排队（AC-13）
        ledger.record_gate_result(db, task_id, "budget_paused", False, str(exc))
        ledger.set_task_status(db, task_id, "awaiting_human", node="prototype")
        return {"prototype_dir": ""}

    proto_dir.mkdir(parents=True, exist_ok=True)
    # 新一轮生成前清空专属原型目录：否则 G2 驳回后本轮少产出文件，上一轮残留会让
    # prototype_files 门禁假通过。G2 审阅发生在节点返回之后，不会与清理并发。
    for stale in proto_dir.iterdir():
        if stale.is_dir():
            shutil.rmtree(stale, ignore_errors=True)
        else:
            stale.unlink(missing_ok=True)
    repo = Path(_repo_path())
    if not needed:
        (proto_dir / "PROTOTYPE.md").write_text(
            _render_prototype_skipped(task_id, why), encoding="utf-8")
        ledger.record_gate_result(db, task_id, "prototype_skipped", True, why, node_run_id=run)
        if not _close(db, run, "done", worker_id, epoch):
            return {"prototype_dir": ""}
        ledger.set_task_status(db, task_id, "running", node="prototype")
        return {"prototype_dir": str(proto_dir), "prototype_feedback": ""}

    model = route_model(budget["state"], "prototype")
    gate = "prototype_references"
    try:
        images, attachments, missing = _prototype_references(task_id)
        requires_image = bool(re.search(r"(?m)^[ \t]*(?:-[ \t]+)?参考图[ \t]*[:：][ \t]*必需[ \t]*$",
                                        _section(design, "## 实现策略")))
        if requires_image and (missing or not images):
            raise ValueError("设计要求参考图，但输入缺失：" + ("、".join(missing) or "未收到图片"))
        ledger.record_gate_result(db, task_id, gate, True, attachments[:500], node_run_id=run)
        gate = "prototype_exec"
        prompt = tmpl_render("prototype-gen", task_id=task_id[:8], requirements_content=req,
                             design_content=design, attachments=attachments,
                             prototype_feedback=state.get("prototype_feedback") or "无",
                             project_context=_project_context())
        r = w.run_codex(prompt, str(repo), model=model, codex_home=str(codex_home),
                        log_dir=str(RIVER_WS / task_id / "logs"), images=images,
                        sandbox="read-only", output_schema=prompt_output.PROTOTYPE_SCHEMA)
        meter.record_usage_safe(db, buffer_path=USAGE_BUFFER, task_id=task_id,
                                agent_role="prototype", executor="codex", model=model,
                                output_text=r.output, node_run_id=run)
        if not r.ok:
            raise ValueError(f"原型 Worker 执行失败（退出码 {r.returncode}）")
        gate = "prototype_files"
        contents = prompt_output.prototype_contents(r.message)
        error = _prototype_errors(contents, req)
        if error:
            raise ValueError(error)
        gate = "prototype_publish"
        with tempfile.TemporaryDirectory(prefix=".prototype-", dir=proto_dir.parent) as temp:
            staged = Path(temp)
            for name, content in contents.items():
                (staged / name).write_text(content, encoding="utf-8")
            proto_dir.rmdir()
            staged.rename(proto_dir)
    except Exception as exc:  # 异常必须收尾，避免孤儿 running（P2-0 教训）
        ledger.record_gate_result(db, task_id, gate, False, str(exc)[:500], node_run_id=run)
        if _close(db, run, "failed", worker_id, epoch):
            ledger.set_task_status(db, task_id, "gate_failed", node="prototype")
        return {"prototype_dir": ""}
    ledger.record_gate_result(db, task_id, "prototype_files", True,
                              "index.html + PROTOTYPE.md", node_run_id=run)
    if not _close(db, run, "done", worker_id, epoch):
        return {"prototype_dir": ""}
    ledger.set_task_status(db, task_id, "running", node="prototype")
    return {"prototype_dir": str(proto_dir), "prototype_feedback": ""}


def design_node(state: DeliveryState) -> dict:
    """设计节点（A2）：REQUIREMENTS 全文 → codex(design-gen) → DESIGN.md 落盘。"""
    db = _ledger()
    req_path = state.get("requirements_doc") or ""
    if not req_path or not Path(req_path).is_file():
        ledger.record_gate_result(db, state["task_id"], "design_input", False,
                                  f"需求文档不可读：{req_path!r}")
        ledger.set_task_status(db, state["task_id"], "gate_failed", node="design")
        return {"design_doc": "", "rejection_reason": ""}
    content = Path(req_path).read_text(encoding="utf-8")
    # R1：被人工驳回过 → 把驳回理由交给设计 Agent，由它判断是否按反馈修订
    feedback = (state.get("rejection_reason") or "").strip()
    if feedback:
        ledger.record_gate_result(db, state["task_id"], "design_revise", True,
                                  f"按驳回理由修订第 {int(state.get('revises_g2') or 0)} 轮：{feedback[:200]}")
    doc = _run_doc_agent(state, node_name="design", template="design-gen",
                         params={"requirements_content": content,
                                 "rejection_feedback": feedback or "无"},
                         filename="DESIGN.md",
                         required_headings=("## 文件归属表", "## 实现策略", "## 验收映射"),
                         post_check=lambda text, previous: _design_contract(
                             text, content, require_order=previous is None or bool(
                                 _DESIGN_ORDER.search(_section(previous, "## 实现策略")))),
                         post_check_name="design_ac_coverage")
    return {"design_doc": doc, "rejection_reason": ""}


def _route_after_doc(state: DeliveryState, key: str) -> str:
    """文档生成成功才进入人审；失败（已记 gate_failed）直接终止图。"""
    return "continue" if state.get(key) else "end"


def _route_after_review(state: DeliveryState) -> str:
    """审查通过 → test；本轮失败且未超返工上限 → implement；否则终止。"""
    if state.get("review_report"):
        return "test"
    if not state.get("review_feedback"):
        return "end"
    rework_count = int(state.get("review_rework_count") or 0)
    return "implement" if 0 < rework_count <= _rule_int("review", "rework_attempts") else "end"


def review_g2(state: DeliveryState) -> Command:
    """人工闸门 G2：批准后前进；驳回须带理由，回设计节点按理由修订（R1），上限同 G1。"""
    decision = interrupt({"gate": "G2_design", "artifact": state["design_doc"]})
    if decision == "approved":
        db = _ledger()
        design_path, requirements_path = _design_input_paths(state)
        try:
            design_text = design_path.read_text(encoding="utf-8")
            requirements_text = requirements_path.read_text(encoding="utf-8")
            pinned = state.get("approved_requirements_text")
            if (pinned != requirements_text if pinned is not None else
                    not ledger.approval_valid(db, state["task_id"], "G1_requirements",
                                              ledger.artifact_digest([requirements_path]))):
                raise ValueError("需求已偏离 G1 批准版本，请从需求重新审批")
        except (OSError, ValueError) as exc:
            ledger.record_gate_result(db, state["task_id"], "approval_snapshot", False, str(exc))
            ledger.set_task_status(db, state["task_id"], "gate_failed", node="review_g2")
            return Command(goto=END)
        approved_plan = []
        if not state.get("lanes"):
            design_path, requirements_path = _design_input_paths(state)
            try:
                approved_plan = _lanes_from_design(design_path, requirements_path, state["task_id"])
                if not approved_plan:
                    raise ValueError("批准的设计缺少可执行的 lane 计划")
            except (OSError, ValueError) as exc:
                ledger.record_gate_result(db, state["task_id"], "lane_plan", False, str(exc))
                ledger.set_task_status(db, state["task_id"], "gate_failed", node="review_g2")
                return Command(goto=END)
        ledger.set_task_status(db, state["task_id"], "running")
        return Command(goto="implement", update={"approved_g2": True, "rejection_reason": "",
                                                 "approved_lane_plan": approved_plan,
                                                 "approved_requirements_text": requirements_text,
                                                 "approved_design_text": design_text})
    db = _ledger()
    reason = ledger.latest_rejection_reason(db, state["task_id"], "G2_design")
    revises = int(state.get("revises_g2") or 0) + 1
    if revises > MAX_REVISES:
        ledger.record_gate_result(db, state["task_id"], "revise_limit_g2", False,
                                  f"G2 驳回修订已达 {MAX_REVISES} 次上限，终止为 rejected")
        ledger.set_task_status(db, state["task_id"], "rejected", node="design")
        return Command(goto=END, update={"revises_g2": revises})
    ledger.set_task_status(db, state["task_id"], "running")
    return Command(goto="design",
                   update={"approved_g2": False, "rejection_reason": reason,
                           "prototype_feedback": reason,
                           "revises_g2": revises})


def route_model(budget_state: str, role: str | None = None) -> str:
    """按角色选模型；缺省继承默认档，DEGRADED 时非强角色仍强制降级。

    model.roles.<role> 是 NORMAL/ALERT 及强角色的独立配置；旧项目没有该映射时
    行为与原先一致。模型名、角色覆盖与强角色集均来自 project.yaml。
    """
    model = _project_config().get("model") or {}
    default = str(model.get("default") or "gpt-6.1-terra").strip() or "gpt-6.1-terra"
    degraded = str(model.get("degraded") or "gpt-6.1-sol").strip() or "gpt-6.1-sol"
    role_models = model.get("roles")
    configured = role_models.get(role) if isinstance(role_models, dict) and role else None
    selected = str(configured or default).strip() or default
    strong = model.get("strong_roles")
    strong_roles = ([str(item) for item in strong] if isinstance(strong, list)
                    else ["requirements", "design", "review"])
    if budget_state == "DEGRADED" and role not in strong_roles:
        return degraded
    return selected


def _design_input_paths(state: DeliveryState) -> tuple[Path, Path]:
    docs = RIVER_WS / state["task_id"] / "docs"
    return (Path(state.get("design_doc") or docs / "DESIGN.md"),
            Path(state.get("requirements_doc") or docs / "REQUIREMENTS.md"))


def _lanes_from_design(design_path: Path, requirements_path: Path | None,
                       task_id: str) -> list[dict]:
    """从 DESIGN.md 的「文件归属表」推导 lanes（intake 任务无需手写任务脚本）。

    缺少历史设计文件时返回空列表。已有设计文件中的无效归属表应阻断实现。
    """
    if not design_path.is_file():
        return []
    design = design_path.read_text(encoding="utf-8")
    rows = _lane_ownership(design)
    if not rows or set(rows) - {"be", "fe"}:
        raise ValueError("DESIGN.md 缺少有效的 be/fe 文件归属")
    requirements = ""
    if requirements_path and requirements_path.is_file():
        requirements = requirements_path.read_text(encoding="utf-8")
    req_sections, design_sections = _sections(requirements), _sections(design)
    context: list[str] = []
    if requirements:
        confirmed = re.sub(r"(?ms)^ {0,3}### 待确认[ \t]*\n.*?(?=^ {0,3}### \S|\Z)", "",
                           req_sections.get("## 需求", ""))
        context.append(requirements.splitlines()[0])
        if confirmed:
            context.append(f"## 需求\n{confirmed[:3000].rstrip()}")
        elif not req_sections:
            context.append(requirements[:3000])  # 历史无章节产物
        for heading in ("## 验收标准", "## 非目标"):
            if req_sections.get(heading):
                context.append(f"{heading}\n{req_sections[heading]}")
    for heading in ("## 实现策略", "## 验收映射"):
        if design_sections.get(heading):
            context.append(f"{heading}\n{design_sections[heading]}")
    approved_context = "\n\n".join(context)
    mapping = [(f"AC-{m[1]}", _design_path(m[2]))
               for line in design_sections.get("## 验收映射", "").splitlines()
               if (m := _AC_MAPPING.fullmatch(line))]
    test_updates = [_design_path(path) for path in re.findall(
        r"(?m)^[ \t]*-[ \t]+既有测试调整[：:][ \t]*([^|\r\n]+)\|[ \t]*\S.*$",
        design_sections.get("## 实现策略", ""))]
    lanes = []
    for lane, paths in rows.items():
        primary = [ac for ac, path in mapping if not _out_of_scope([path], paths)]
        other = [f"{ac} → {path}" for ac, path in mapping if _out_of_scope([path], paths)]
        predecessor = lanes[-1]["name"] if lanes else "无"
        lanes.append({
            "name": lane,
            "node": f"implement_{lane}",
            "branch": f"feat/{task_id[:8]}-{lane}",
            "scope": paths,
            "primary_acs": primary,
            "test_updates": [path for path in test_updates if path in paths],
            "lane_instruction": (
                f"按 G2 批准的设计实现 {lane} lane 的归属文件。完整 AC 与映射供跨 lane 协作核对。"
                "待确认事项仅用于理解设计影响，按已确认行为实施。\n\n"
                f"## 本 lane 主责 AC\n{'、'.join(primary) or '未单独映射，按已批准实现策略'}\n\n"
                f"## 跨 lane 协作\n前序 lane：{predecessor}；其他 lane 映射（只读参照）："
                f"{'、'.join(other) or '无'}。\n\n" + approved_context
            ),
        })
    return lanes


def _changed_files(status_output: str) -> list[str]:
    """解析 `git status --porcelain` 的路径列（重命名取新路径）。"""
    paths: list[str] = []
    for line in (status_output or "").splitlines():
        body = line[3:].strip() if len(line) > 3 else ""
        if " -> " in body:  # R  old -> new / C  old -> new
            body = body.split(" -> ", 1)[1]
        body = body.strip().strip('"')
        if body:
            paths.append(body)
    return paths


def _out_of_scope(files: list[str], scope: list[str]) -> list[str]:
    """返回不属于 lane scope 的文件（scope 为空表示不限定，跳过校验）。"""
    if not scope:
        return []
    return [f for f in files if not any(fnmatch.fnmatch(f, pat) for pat in scope)]


def _lane_status(repo: str) -> tuple[int, str]:
    import worker as w

    return w.git(repo, "status", "--porcelain", "-z", "--no-renames", "--untracked-files=all")


def _fail_lane(db, task_id: str, lane: dict, gate: str, reason: str, run: str | None = None,
               worker_id: str | None = None, epoch: int | None = None) -> str:
    ledger.set_lane_status(db, task_id, lane["name"], "failed")
    ledger.record_gate_result(db, task_id, gate, False, reason, node_run_id=run)
    if run is None or _close(db, run, "failed", worker_id, epoch):
        ledger.set_task_status(db, task_id, "gate_failed", node=lane["node"])
    return f"lane {lane['name']} FAILED: {reason}"


def _check_lane_scope(db, task_id: str, run: str, lane: dict, prefix: str,
                      git_status: tuple[int, str], worker_id: str, epoch: int) -> str:
    from lane_delivery import status_paths

    rc, output = git_status
    try:
        if rc:
            raise ValueError(f"无法确认工作区改动，git status 退出码 {rc}")
        changed = status_paths(output)
    except ValueError as exc:
        return _fail_lane(db, task_id, lane, "lane_scope", str(exc), run, worker_id, epoch)
    nested = _lane_prefix_violations(changed, prefix)
    outside = _out_of_scope(changed, [str(item) for item in (lane.get("scope") or [])])
    if nested:
        return _fail_lane(db, task_id, lane, "lane_workdir_prefix",
                          f"工作目录已切到 lane 根 {prefix}/，但出现嵌套路径：{', '.join(nested[:10])}",
                          run, worker_id, epoch)
    if outside:
        return _fail_lane(db, task_id, lane, "lane_scope", f"越界文件：{', '.join(outside)}",
                          run, worker_id, epoch)
    return ""


def _repo_dirty(repo: str) -> list[str]:
    """项目仓库当前脏文件列表（G-01 只读节点校验用）。

    git 不可用时返回空列表：只读守卫是第二层防线，不得因取不到状态阻断文档节点。
    """
    import worker as w

    rc, out = w.git(repo, "status", "--porcelain")
    return _changed_files(out) if rc == 0 else []


def _lane_workdir(repo: str, scope: list[str]) -> tuple[str, str]:
    """按 lane scope 推导 codex 工作目录（G-02 写入前置限制）。

    仅当 scope 的所有模式共享同一个真实存在的顶层目录时，把该目录作为 codex 的 cwd：
    workspace-write 下 cwd 是默认可写范围，写入因此被限制在 lane 子树内。
    多根、含通配顶层或目录不存在时回退仓库根（(repo, "")），保持旧行为。
    """
    roots: set[str] = set()
    for pat in scope:
        p = str(pat).replace("\\", "/")
        while p.startswith("./"):
            p = p[2:]
        head = p.split("/", 1)[0]
        if not head or any(ch in head for ch in "*?["):
            return repo, ""
        roots.add(head)
    if len(roots) == 1:
        root = roots.pop()
        if root not in (".", "") and (Path(repo) / root).is_dir():
            return str(Path(repo) / root), root
    return repo, ""


def _lane_prefix_violations(changed: list[str], prefix: str) -> list[str]:
    """识别“cwd 已是 lane 根却又写成 repo 相对路径”的嵌套前缀文件。

    例如 lanes cwd=frontend 时误写 frontend/src/a.ts → 实际落到 frontend/frontend/src/a.ts。
    这类路径仍匹配 lane scope（事后 scope 门禁放行），必须单独拦截。
    """
    if not prefix:
        return []
    nested = f"{prefix}/{prefix}/"
    return [f for f in changed if f.startswith(nested)]


def _workdir_hint(repo: str, workdir: str, prefix: str) -> str:
    """渲染给 Worker 的工作目录说明，避免 lane 相对路径与仓库相对路径混用。"""
    if not prefix:
        return f"Your working directory is the repository root `{repo}`."
    return (
        f"Your working directory is the lane root `{workdir}` "
        f"(i.e. `{prefix}/` inside the repository root `{repo}`).\n"
        f"Treat every path as relative to your working directory. When the design or lane "
        f"instruction uses repository-relative paths starting with `{prefix}/`, strip that "
        f"prefix (e.g. `{prefix}/src/a.ts` → `src/a.ts`). Never create nested paths like "
        f"`{prefix}/{prefix}/...`."
    )


def _lane_base_matches(repo: str, lane: dict, head: str) -> bool:
    import worker as w

    rc, branch = w.git(repo, "symbolic-ref", "--short", "HEAD")
    return not rc and branch.strip() == lane["branch"] and w.git(repo, "rev-parse", "HEAD") == (0, head)


def _gate_observation(result):
    import worker as w

    if isinstance(result, w.GateResult):
        return result
    # Preserve the two-item result accepted from existing gate adapters.
    passed, output = result
    return w.gate_result(0 if passed else 1, output)


def _run_lane_gates(db, task_id, lane, run, worker_id, epoch, *, repo, workdir, prefix, head,
                    codex_home, logs, gate_specs, fix_limit, approved_tests) -> tuple[bool, str]:
    import worker as w
    from lane_delivery import patch_fingerprint, status_paths, test_patch_error
    from prompt_output import GATE_FIX_SCHEMA, gate_fix_decision

    repaired = False
    for command, gate in gate_specs:
        run_number = 0
        attempt = 0
        retried_transient = False
        previous_failure = None
        previous_attempt = ""
        checked_patch = ""
        safe_command = w.safe_gate_text(command)

        def run_check():
            nonlocal run_number, checked_patch
            run_number += 1
            status_rc, status = _lane_status(repo)
            try:
                checked_patch = patch_fingerprint(repo, status) if status_rc == 0 else ""
            except ValueError:
                checked_patch = ""
            result = _gate_observation(w.run_gate(command, repo))
            if result.ok:
                after_rc, after = _lane_status(repo)
                try:
                    unchanged = bool(checked_patch) and after_rc == 0 and checked_patch == patch_fingerprint(repo, after)
                except ValueError:
                    unchanged = False
                if not unchanged:
                    result = w.GateResult("unknown", None, result.seconds, "门禁改动了受检补丁", result.tail)
            ledger.record_gate_result(db, task_id, "gate_attempt", result.ok,
                f"{gate} run {run_number}, repair {attempt}, patch {checked_patch[:12] or 'unknown'}\n"
                f"cwd: {repo}\n$ {safe_command}\n{result.detail}",
                node_run_id=run)
            return result

        def stop(code: str, reason: str) -> tuple[bool, str]:
            ledger.record_gate_result(db, task_id, gate, False,
                                      f"$ {safe_command}\n{result.detail}", node_run_id=run)
            return repaired, _fail_lane(db, task_id, lane, code, reason, run, worker_id, epoch)

        def validate_patch():
            status = _lane_status(repo)
            if error := _check_lane_scope(db, task_id, run, lane, prefix, status, worker_id, epoch):
                return status[1], "", error
            reason = ("lane 的工作分支或基准提交发生变化" if not _lane_base_matches(repo, lane, head)
                      else test_patch_error(repo, status[1], approved_tests))
            if reason:
                return status[1], "", stop("gate_fix_patch", reason)[1]
            try:
                return status[1], patch_fingerprint(repo, status[1]), ""
            except ValueError as exc:
                return status[1], "", stop("gate_fix_patch", str(exc))[1]

        result = run_check()
        while not result.ok:
            if result.kind == "transient" and not retried_transient:
                _, digest, error = validate_patch()
                if error:
                    return repaired, error
                if not checked_patch or digest != checked_patch:
                    return stop("gate_fix_patch", f"{gate} 运行后补丁发生变化，停止重跑。")
                retried_transient = True
                result = run_check()
                continue
            if result.kind != "failed":
                return stop("gate_fix_handoff", f"{gate} · {result.kind} · 人工排查执行条件。\n{result.detail}")
            failure = (result.returncode, result.diagnostic)
            if failure == previous_failure:
                return stop("gate_fix_no_progress", f"{gate} 修正后仍为同一失败，保留工作区。\n{result.detail}")
            if attempt >= fix_limit:
                return stop("gate_fix_exhausted", f"{gate} 已达修正上限 {fix_limit}。\n{result.detail}")
            if not lane.get("scope"):
                return stop("gate_fix_handoff", f"{gate} · EVIDENCE · 历史 lane 缺少明确文件归属，人工核对后重跑。")
            status, original_patch, error = validate_patch()
            if error:
                return repaired, error
            meter.flush_buffer(db, USAGE_BUFFER)
            budget = ledger.evaluate_budget(db, QUOTA)
            calls = db.execute("SELECT COALESCE(SUM(calls), 0) FROM cost_ledger WHERE task_id=?", (task_id,)).fetchone()[0]
            if budget["state"] == "PAUSED" or calls >= TASK_CALL_LIMIT:
                return stop("gate_fix_handoff", f"{gate} · BUDGET · 预算暂停或已达任务调用上限，人工处理后重跑。")
            attempt += 1
            model = route_model(budget["state"], "implement")
            prompt = tmpl_render(
                "gate-fix", gate_cmd=safe_command, gate_name=gate, gate_cwd=repo,
                gate_output_tail=result.detail + previous_attempt,
                failed_files=status_paths(status) or ["（工作区无改动）"],
                attempt=attempt, attempt_limit=fix_limit, scope=lane["scope"],
                project_context=_project_context(), workdir_hint=_workdir_hint(repo, workdir, prefix),
                lane_instruction=lane.get("lane_instruction") or lane.get("prompt") or "历史 lane 未声明批准的 AC",
                approved_tests=approved_tests or ["（无批准的既有测试调整）"],
            )
            try:
                reply = w.run_codex(prompt, workdir, model=model,
                                    codex_home=str(codex_home), log_dir=logs, output_schema=GATE_FIX_SCHEMA)
            except Exception as exc:
                reason = w.safe_gate_text(str(exc))[:500]
                ledger.record_gate_result(db, task_id, "gate_fix_attempt", False, f"{gate} #{attempt} · {reason}", node_run_id=run)
                return stop("gate_fix_exec", reason)
            meter.record_usage_safe(db, buffer_path=USAGE_BUFFER, task_id=task_id,
                                    agent_role=lane["node"], executor="codex", model=model,
                                    output_text=reply.output, node_run_id=run)
            if not reply.ok:
                reason = f"修正 Worker 退出码 {reply.returncode}：{w.safe_gate_text(reply.report)[-300:]}"
                ledger.record_gate_result(db, task_id, "gate_fix_attempt", False, f"{gate} #{attempt} · {reason}", node_run_id=run)
                return stop("gate_fix_exec", reason)
            try:
                report = gate_fix_decision(reply.message)
            except ValueError as exc:
                ledger.record_gate_result(db, task_id, "gate_fix_attempt", False, f"{gate} #{attempt} · {exc}", node_run_id=run)
                return stop("gate_fix_output", str(exc))
            evidence = w.safe_gate_text(report["evidence"])[:1000]
            verification = w.safe_gate_text(report["verification"])[:500]
            decision = report["decision"]
            attempt_detail = (f"{gate} #{attempt} · {decision} · {report['blocker']}\n"
                              f"Worker 自述：{evidence}\n验证：{verification}")
            if decision == "BLOCKED":
                ledger.record_gate_result(db, task_id, "gate_fix_attempt", False, attempt_detail, node_run_id=run)
                return stop("gate_fix_handoff", f"{gate} · {report['blocker']} · 人工接手。\n{evidence}")
            _, changed_patch, error = validate_patch()
            valid_patch = not error and decision == "PATCH_READY" and changed_patch != original_patch
            ledger.record_gate_result(db, task_id, "gate_fix_attempt", valid_patch,
                f"{attempt_detail}\n补丁：{original_patch[:12]} → {changed_patch[:12] or '校验失败'}", node_run_id=run)
            if error:
                return repaired, error
            if not valid_patch:
                return stop("gate_fix_no_progress", f"{gate} 修正未生成有效补丁。\n{evidence}")
            repaired = True
            previous_failure = failure
            previous_attempt = f"\n\n上轮 #{attempt} 首段诊断：\n{result.diagnostic[:500]}\n修正 Worker 自述：{evidence[:400]}"
            result = run_check()
        ledger.record_gate_result(db, task_id, gate, True, f"$ {safe_command}\n{result.tail}", node_run_id=run)
    return repaired, ""


def implement_node(state: DeliveryState) -> dict:
    """实现节点（Worker 化 · P1 Week 2）：双 lane 串行（be → fe，N=1 语义）。

    每 lane：分支创建 → node_run → codex 实现 → 编排层门禁 → 按配置尝试修正
    → git commit（幂等键）→ 计量入账 → finish。
    门禁在编排层执行（AC-06 / C-001：Worker 沙箱内 vitest EPERM）。
    """
    import worker as w
    import review_delivery as rd
    from lane_delivery import status_paths, test_patch_error

    db = _ledger()
    repo = _repo_path()
    base = state.get("base_branch", "main")
    task_id = state["task_id"]
    status = db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()["status"]
    if status in ("awaiting_human", "gate_failed", "failed", "done"):
        return {"implement_summary": ""}  # 上游未就绪/已失败，不得重复实施
    # 审查失败返工：把 REVIEW 的问题/建议附到本 lane 指令尾部（只在编排层拼接，
    # 不写回 lane.prompt，避免多次返工时重复堆积）
    feedback = str(state.get("review_feedback") or "").strip()
    by_lane = state.get("review_lane_feedback") or {}
    # 显式 lanes 优先。通常使用 G2 快照，历史任务可从文档恢复。
    try:
        design_path, requirements_path = _design_input_paths(state)
        lanes = state.get("lanes") or state.get("approved_lane_plan") or _lanes_from_design(
            design_path, requirements_path, task_id)
        for lane in lanes:
            if lane.get("prompt"):
                continue
            scope = lane.get("scope")
            if (not isinstance(scope, (list, tuple)) or not scope
                    or any(not isinstance(path, str) or not path.strip() for path in scope)):
                raise ValueError(f"lane {lane['name']} 缺少有效的文件范围")
    except ValueError as exc:
        ledger.record_gate_result(db, task_id, "lane_plan", False, str(exc))
        ledger.set_task_status(db, task_id, "gate_failed", node="implement")
        return {"implement_summary": ""}
    lanes = lanes or [
        {"name": "be", "node": "implement_api", "branch": "feat/task-002-uptime-be",
         "scope": ["apps/api/src/**"], "prompt": "(default lane: 请在任务脚本中用 lane_instruction 走模板或用 prompt 显式覆盖)"},
        {"name": "fe", "node": "implement_web", "branch": "feat/task-002-uptime-fe",
         "scope": ["apps/web/src/**"], "prompt": "(default lane: 请在任务脚本中用 lane_instruction 走模板或用 prompt 显式覆盖)"},
    ]
    summary = []
    old_delivery = state.get("delivery") or {}
    try:
        if by_lane:
            if not old_delivery.get("commits") or rd.revision(repo, "HEAD") != old_delivery["commits"][-1]["commit"]:
                raise ValueError("返工前的 HEAD 已偏离审查版本")
            pinned_base = old_delivery["base"]
            receipts = list(old_delivery["commits"])
            prev_branch = receipts[-1]["commit"]
        else:
            pinned_base = rd.revision(repo, base)
            receipts = []
            prev_branch = pinned_base
    except ValueError as exc:
        ledger.record_gate_result(db, task_id, "delivery_snapshot", False, str(exc))
        ledger.set_task_status(db, task_id, "gate_failed", node="implement")
        return {"implement_summary": ""}
    affected = next((i for i, lane in enumerate(lanes) if lane["name"] in by_lane), len(lanes))
    config = _project_config()
    reuse_inputs = (config.get("test") or {}).get("reuse_environment_files") or []
    # B3：lane 计划落独立表（merge_order + base_branch 链），执行状态随节点推进
    ledger.register_lanes(db, task_id, lanes, base_branch=base)
    # A1 模板追溯：幂等快照 manifest 版本到 task-XXX/templates.lock
    # 只在首次 implement_node 调用时 snapshot（图恢复重试不重复）
    _task_dir = RIVER_WS / task_id
    _lock_path = _task_dir / "templates.lock"
    if not _lock_path.exists():
        try:
            tmpl_snapshot(_task_dir)
        except Exception as _e:
            # snapshot 失败不阻断实现（非关键路径）
            print(f"[templates] snapshot failed: {_e}")
    for idx, approved_lane in enumerate(lanes):
        lane = dict(approved_lane)
        if by_lane and lane["name"] not in by_lane:
            continue
        clean_rc, existing = _lane_status(repo)
        if clean_rc or existing:
            return {"implement_summary": _fail_lane(db, task_id, lane, "lane_preexisting_changes",
                    "无法确认工作区干净" if clean_rc else "工作区存在已有改动，保留现场并停止该 lane")}
        # G-02：模板渲染的 lane 以 lane 根为 codex 工作目录（workspace-write 下 cwd 即可写范围）；
        # 逃生口 prompt 假定仓库根，保持旧行为。多根/通配/目录不存在时 _lane_workdir 回退仓库根。
        from_template = not lane.get("prompt")
        workdir, wd_prefix = (
            _lane_workdir(repo, [str(s) for s in (lane.get("scope") or [])])
            if from_template else (repo, ""))
        # A1 模板层 · escape hatch：lane.prompt 显式非空 → 直接用（逃生口）；
        # 为空则从 implement-lane 模板渲染（需要 lane.lane_instruction 字段）。
        if not lane.get("prompt"):
            if not lane.get("lane_instruction"):
                raise ValueError(
                    f"lane '{lane['name']}' has neither 'prompt' nor 'lane_instruction' — "
                    f"provide one to either bypass or use the template."
                )
            lane["prompt"] = tmpl_render(
                "implement-lane",
                branch=lane["branch"],
                lane_instruction=lane["lane_instruction"],
                scope=lane.get("scope", []),
                task_id=task_id[:8],
                project_context=_project_context(),
                workdir_hint=_workdir_hint(repo, workdir, wd_prefix),
            )
        prompt = lane["prompt"]
        lane_feedback = by_lane.get(lane["name"], feedback if not by_lane else "")
        if lane_feedback:
            prompt = f"{prompt}\n\n## 上轮审查未通过（必须逐条修正）\n{lane_feedback}"
        # C-006+C-007 扩展：强制重建 lane 分支（checkout -B）
        # 原因：如果前一次空运行留下了 lane 分支（但 base 错误），
        # 简单的 checkout 会复用错误的 base，导致 fe 分支拿不到 be 的改动。
        # -B 无论分支是否已存在都基于 prev_branch 重建。
        crc, cout = w.git(repo, "checkout", "-B", lane["branch"], prev_branch)
        if crc != 0:
            # 分支重建失败发生在 start_node_run 之前——没有 run_id 可传
            ledger.set_lane_status(db, task_id, lane["name"], "failed")
            ledger.record_gate_result(db, task_id, "branch_setup", False, cout[-500:])
            ledger.set_task_status(db, task_id, "gate_failed", node=lane["node"])
            return {"implement_summary": f"lane {lane['name']} FAILED: 分支重建失败 rc={crc}"}
        head_rc, head = w.git(repo, "rev-parse", "HEAD")
        if head_rc:
            return {"implement_summary": _fail_lane(db, task_id, lane, "branch_setup", "无法确认 lane 基准提交")}
        if idx > 0:
            # B3 合并顺序：本 lane 基于前驱分支重建成功 ⇒ 前驱已被吸收
            ledger.mark_lane_merged(db, task_id, lanes[idx - 1]["name"])
        prev_branch = lane["branch"]
        # 节点边界控制（V2.2 §8.4 / AC-13）：补记缓冲 → 评估全局预算 → 单任务上限 U
        meter.flush_buffer(db, USAGE_BUFFER)
        budget = ledger.evaluate_budget(db, QUOTA)
        task_calls = int(db.execute(
            "SELECT COALESCE(SUM(calls), 0) AS n FROM cost_ledger WHERE task_id=?", (task_id,)
        ).fetchone()["n"])
        if task_calls >= TASK_CALL_LIMIT:
            ledger.request_approval(db, task_id, "budget_task_cap", f"{task_calls}/{TASK_CALL_LIMIT}")
            ledger.set_task_status(db, task_id, "awaiting_human", node=lane["node"])
            return {"implement_summary":
                    f"lane {lane['name']} 暂停：任务用量 {task_calls} ≥ U {TASK_CALL_LIMIT}，已转人审"}
        try:
            run, worker_id, epoch, codex_home = _start_leased_run(
                db, task_id, lane["node"], lane=lane["name"],
                file_scope=lane["scope"], branch=lane["branch"])
        except PermissionError as exc:
            # PAUSED：门禁层拒发新 node_run（在途跑完、人审照常；修订 2）
            ledger.record_gate_result(db, task_id, "budget_paused", False, str(exc))
            ledger.set_task_status(db, task_id, "awaiting_human", node=lane["node"])
            return {"implement_summary": f"预算 {budget['state']}：节点 {lane['node']} 未启动"}
        ledger.set_lane_status(db, task_id, lane["name"], "running")
        # 角色降级路由：DEGRADED 切降级档模型（AC-13 ②）
        model = route_model(budget["state"], "implement")
        # Worker 执行（隔离 home + 按任务落盘日志）
        worker_logs = str(RIVER_WS / task_id / "logs")
        try:
            r = w.run_codex(prompt, workdir, model=model,
                            codex_home=str(codex_home), log_dir=worker_logs)
        except Exception as exc:
            return {"implement_summary": _fail_lane(db, task_id, lane, "worker_exec", str(exc)[:500],
                                                       run, worker_id, epoch)}
        meter.record_usage_safe(db, buffer_path=USAGE_BUFFER, task_id=task_id,
                                agent_role=lane["node"], executor="codex", model=model,
                                output_text=r.output, node_run_id=run)
        if not r.ok:
            return {"implement_summary": _fail_lane(db, task_id, lane, "worker_exec",
                    f"实现 Worker 退出码 {r.returncode}：{r.report[-300:]}", run, worker_id, epoch)}
        if r.message.strip():
            ledger.record_gate_result(db, task_id, "worker_evidence", True,
                                      "Worker 自述，须独立核验。\n" + r.message.strip()[:4000], node_run_id=run)
        # C-007 防回归：Worker 空交付检测——无改动时门禁会在旧代码上假阳性全绿。
        # 证据：task-004 隔离 home 缺信任配置 → codex 只读空跑 → 门禁全绿 → 空提交被吞。
        # 注意：不能对整体输出 strip()——首行 " M path" 的前导空格是状态列，去掉后
        # 路径解析会左移一位（真实任务 066eb5ec 曾把 apps/... 解析成 pps/...）。
        # 多项目：不硬编码 apps/（river 结构），改为全仓库检测；越界由下方 _out_of_scope 拦截。
        initial_status = _lane_status(repo)
        if initial_status == (0, ""):
            ledger.set_lane_status(db, task_id, lane["name"], "failed")
            ledger.record_gate_result(db, task_id, "worker_no_changes", False,
                                       f"Worker 未产生任何工作区改动。codex 报告尾部：{r.report[-300:]}",
                                       node_run_id=run)
            if _close(db, run, "failed", worker_id, epoch):
                ledger.set_task_status(db, task_id, "gate_failed", node=lane["node"])
            return {"implement_summary": f"lane {lane['name']} FAILED: Worker 空交付（无工作区改动）"}
        # 范围硬校验（A 层）：改动必须落在 lane scope 内，越界不得进入门禁/提交。
        # 背景：此前只看 apps 下是否有改动，fe lane 改 api 文件同样放行（契约形同建议）。
        error = _check_lane_scope(db, task_id, run, lane, wd_prefix,
                                  initial_status, worker_id, epoch)
        if error:
            return {"implement_summary": error}
        approved_tests = (lane.get("test_updates") or [] if state.get("approved_g2")
                          and state.get("approved_lane_plan") and not state.get("lanes") else [])
        gate_specs = _implement_gate_specs(config, lane["name"])
        env_before = rd.environment(repo, config, reuse_inputs) if reuse_inputs else ""
        repaired, error = _run_lane_gates(
            db, task_id, lane, run, worker_id, epoch, repo=repo, workdir=workdir, prefix=wd_prefix,
            head=head, codex_home=codex_home, logs=worker_logs, gate_specs=gate_specs,
            fix_limit=max(0, _rule_int("implement", "gate_fix_attempts")), approved_tests=approved_tests,
        )
        if error:
            return {"implement_summary": error}
        # 门禁命令和修正可能改动工作区，暂存前重新校验最终文件范围。
        final_status = _lane_status(repo)
        error = _check_lane_scope(db, task_id, run, lane, wd_prefix,
                                  final_status, worker_id, epoch)
        if error:
            return {"implement_summary": error}
        files = status_paths(final_status[1])
        if not files:
            return {"implement_summary": _fail_lane(db, task_id, lane, "worker_no_changes",
                    "门禁后没有可交付改动", run, worker_id, epoch)}
        if not _lane_base_matches(repo, lane, head):
            return {"implement_summary": _fail_lane(db, task_id, lane, "branch_changed",
                    "lane 的工作分支或基准提交发生变化", run, worker_id, epoch)}
        stage_rc, stage_log = w.git(repo, "add", "-A", "--", *(f":(literal){path}" for path in files))
        index_rc, indexed = w.git(repo, "diff", "--cached", "--name-only", "--no-renames", "-z")
        if stage_rc or index_rc or set(filter(None, indexed.split("\0"))) != set(files):
            return {"implement_summary": _fail_lane(db, task_id, lane, "git_stage",
                    f"暂存清单与已核对的文件不一致：{stage_log[-250:]}", run, worker_id, epoch)}
        if reason := test_patch_error(repo, final_status[1], approved_tests, staged=True):
            return {"implement_summary": _fail_lane(db, task_id, lane, "test_preservation", reason,
                                                       run, worker_id, epoch)}
        tree_rc, tree = w.git(repo, "write-tree")
        if tree_rc:
            return {"implement_summary": _fail_lane(db, task_id, lane, "git_stage", "无法记录最终补丁",
                                                       run, worker_id, epoch)}
        if repaired:
            for gate_cmd, gate_name in gate_specs:
                result = _gate_observation(w.run_gate(gate_cmd, repo))
                evidence = f"cwd: {repo}\n$ {w.safe_gate_text(gate_cmd)}\n{result.detail}"
                if not result.ok:
                    return {"implement_summary": _fail_lane(db, task_id, lane, f"final_{gate_name}", evidence,
                                                               run, worker_id, epoch)}
                ledger.record_gate_result(db, task_id, f"final_{gate_name}", True, evidence, node_run_id=run)
            checked_rc, checked = _lane_status(repo)
            if (checked_rc or w.git(repo, "write-tree") != (0, tree)
                    or any(row[1] != " " for row in checked.split("\0") if len(row) >= 3)):
                return {"implement_summary": _fail_lane(db, task_id, lane, "final_patch_changed",
                        "最终门禁后补丁发生变化，停止提交", run, worker_id, epoch)}
        intent_key = f"{task_id}:{lane['node']}:git_intent:{run}"
        try:
            ledger.record_operation(db, task_id, intent_key, "git_commit_intent", tree.strip(),
                                    node_run_id=run, status="pending", worker_id=worker_id, lease_epoch=epoch,
                                    result_ref=json.dumps({"branch": lane["branch"], "parent": head.strip(),
                                                           "repaired": repaired}))
        except ledger.StaleLeaseError:
            return {"implement_summary": ""}
        # 副作用发生前已登记核对过的 Git 树和父提交。
        crc, cout = w.git(repo, "commit", "-m", lane.get("commit_msg", f"feat({lane['name']}): {task_id[:8]}"))
        if crc != 0:
            ledger.set_lane_status(db, task_id, lane["name"], "failed")
            ledger.record_gate_result(db, task_id, "git_commit", False, cout[-500:], node_run_id=run)
            if _close(db, run, "failed", worker_id, epoch):
                ledger.set_task_status(db, task_id, "gate_failed", node=lane["node"])
            return {"implement_summary": f"lane {lane['name']} FAILED: git commit rc={crc}"}
        try:  # B1 栅栏：epoch 已失效则副作用与收尾一并拒写
            commit = rd.revision(repo, "HEAD")
            if w.git(repo, "rev-parse", "HEAD^{tree}") != (0, tree):
                raise ValueError("提交中的树与已核对的暂存树不一致")
            ledger.record_operation(
                db, task_id, f"{task_id}:{lane['node']}:git_commit:{run}",
                "git_commit", commit, node_run_id=run,
                worker_id=worker_id, lease_epoch=epoch)
            with db:
                db.execute("UPDATE operations SET status='done' WHERE operation_key=?", (intent_key,))
        except ValueError as exc:
            return {"implement_summary": _fail_lane(db, task_id, lane, "delivery_snapshot", str(exc),
                                                       run, worker_id, epoch)}
        except ledger.StaleLeaseError as exc:
            print(f"[lease] stale operation rejected: {exc}")
            return {"implement_summary": f"lane {lane['name']} 租约失效：副作用被拒（可能已被接管）"}
        if not _close(db, run, "done", worker_id, epoch):
            return {"implement_summary": f"lane {lane['name']} 租约失效：完成写入被拒（可能已被接管）"}
        ledger.set_lane_status(db, task_id, lane["name"], "done")
        receipts.append({"lane": lane["name"], "commit": commit, "run": run, "repaired": repaired,
                         "environment": env_before if reuse_inputs and env_before == rd.environment(repo, config, reuse_inputs) else ""})
        summary.append(f"{lane['name']}:done")
    if lanes:  # 末 lane 无后继，收尾即视为已并入交付分支
        ledger.mark_lane_merged(db, task_id, lanes[-1]["name"])
    rechecks = [item for item in old_delivery.get("rechecks", [])
                if by_lane and item["lane"] in {lane["name"] for lane in lanes[:affected]}]
    if by_lane and receipts:
        final = receipts[-1]["commit"]
        for lane in lanes[affected:]:
            if any(item["commit"] == final and item["lane"] == lane["name"] for item in receipts):
                continue
            try:
                run, worker_id, epoch, _ = _start_leased_run(db, task_id, lane["node"], lane=lane["name"],
                                                              with_codex_home=False)
            except PermissionError as exc:
                ledger.record_gate_result(db, task_id, "budget_paused", False, str(exc))
                ledger.set_task_status(db, task_id, "awaiting_human", node=lane["node"])
                return {"implement_summary": ""}
            for command, name in _implement_gate_specs(config, lane["name"]):
                observation = _gate_observation(w.run_gate(command, repo))
                evidence = f"$ {w.safe_gate_text(command)}\n{observation.detail}"
                ledger.record_gate_result(db, task_id, name, observation.ok, evidence, node_run_id=run)
                if not observation.ok or _lane_status(repo) != (0, "") or rd.revision(repo, "HEAD") != final:
                    return {"implement_summary": _fail_lane(db, task_id, lane, "review_recheck", evidence,
                                                               run, worker_id, epoch)}
            ledger.record_gate_result(db, task_id, "review_rechecked_head", True, final, node_run_id=run)
            if not _close(db, run, "done", worker_id, epoch):
                return {"implement_summary": ""}
            rechecks.append({"lane": lane["name"], "commit": final, "run": run})
    return {"implement_summary": "; ".join(summary), "review_feedback": "", "review_lane_feedback": {},
            "delivery": {"base": pinned_base, "commits": receipts, "rechecks": rechecks}}


def _project_config() -> dict:
    """读取项目配置（RIVER_WS/project.yaml）；缺失/损坏按空配置处理（由调用方显式留痕）。"""
    path = RIVER_WS / "project.yaml"
    if not path.is_file():
        return {}
    try:
        import yaml
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        print(f"[config] project.yaml 解析失败：{exc}")
        return {}
    if config.get("project") == "proj-b" and os.environ.get("AGENTCODE_PROJECT_PROFILE") == "acceptance":
        profile = yaml.safe_load(path.with_name("project.acceptance.yaml").read_text(encoding="utf-8"))
        if not isinstance(profile, dict):
            raise ValueError("proj-b 验收配置必须是映射")

        def overlay(original, overrides):
            merged = dict(original)
            for key, value in overrides.items():
                if isinstance(value, dict) and isinstance(merged.get(key), dict):
                    merged[key] = overlay(merged[key], value)
                else:
                    merged[key] = value
            return merged

        config = overlay(config, profile)
    return config


def _repo_path() -> str:
    """业务仓库路径：project.yaml repo_path 优先，缺省回退 <平台上级>/river。

    RIVER_WS 被重定向（临时项目/测试）时回退到 <工作区>/repo：既避免
    RIVER_WS.parents[2] 在浅路径（如 /tmp/ac-x）上越界，也避免向
    不可写路径（容器根目录）建目录。
    """
    configured = _project_config().get("repo_path")
    if configured:
        return str(configured)
    if RIVER_WS.parent == PROJECTS_DIR:
        return str(PROJECTS_DIR.parent.parent / "river")
    return str(RIVER_WS / "repo")


def refresh_runtime_config() -> dict:
    """从 project.yaml 刷新运行时预算参数（环境变量优先，缺失回默认值）。

    控制台保存配置后与 build_app() 时调用，避免必须重启进程才生效（C-016）。
    """
    global QUOTA, TASK_CALL_LIMIT
    config = _project_config()
    budget = config.get("budget") or {}
    env_quota = os.environ.get("RIVER_QUOTA")
    env_limit = os.environ.get("RIVER_TASK_CALL_LIMIT")
    try:
        QUOTA = int(env_quota if env_quota else budget.get("monthly_quota", _DEFAULT_QUOTA))
    except (TypeError, ValueError):
        QUOTA = _DEFAULT_QUOTA
    try:
        TASK_CALL_LIMIT = int(
            env_limit if env_limit else budget.get("max_usage_per_task", _DEFAULT_TASK_CALL_LIMIT))
    except (TypeError, ValueError):
        TASK_CALL_LIMIT = _DEFAULT_TASK_CALL_LIMIT
    # 阈值真正参与状态判定（此前只做校验/展示，属于“配置不生效”）
    ledger.configure_thresholds(budget.get("thresholds") or {})
    return config


def _node_attempt(db, task_id: str, node_name: str) -> int:
    return int(db.execute(
        "SELECT COUNT(*) FROM node_runs WHERE task_id=? AND node_name=?",
        (task_id, node_name)).fetchone()[0]) + 1


# 可从指定节点重跑（人审闸门不在此列——挂起时应用 approve/reject）
RERUN_ENTRY_NODES = ("requirements", "design", "prototype", "implement",
                     "review", "test", "deploy", "retro")
# 重跑前清掉的旧产物键：避免上一次的 truthy 结果让条件边误放行/误跳过
_RERUN_RESET_KEYS = {
    "requirements": ("requirements_doc",),
    "design": ("design_doc",),
    "prototype": ("prototype_dir",),
    "implement": ("implement_summary",),
    "review": ("review_report",),
    "test": ("test_report",),
    "deploy": ("deploy_report", "deploy_skipped"),
    "retro": (),
}


def rerun_payload(task_id: str, entry: str) -> dict:
    """重跑输入：清空该节点上次产物，并开启新的审查返工轮次。"""
    if entry not in RERUN_ENTRY_NODES:
        raise ValueError(f"不支持从节点重跑：{entry}（可用：{', '.join(RERUN_ENTRY_NODES)}）")
    payload: dict = {"task_id": task_id, "review_rework_count": 0, "previous_review": "",
                     "review_lane_feedback": {}, "review_feedback": ""}
    for key in _RERUN_RESET_KEYS[entry]:
        payload[key] = False if key == "deploy_skipped" else ""
    stages = ("requirements", "design", "prototype", "implement", "review", "test", "deploy", "retro")
    for stage in ("review", "test", "deploy"):
        if stages.index(entry) <= stages.index(stage):
            payload[f"{stage}_receipt"] = {}
    if stages.index(entry) <= stages.index("deploy"):
        payload["g3_receipt"] = {}
    if stages.index(entry) <= stages.index("review"):
        payload["review_deferred_tests"] = {}
    return payload


def reset_task_for_rerun(task_id: str, entry: str) -> None:
    """把任务从失败/终态复原为 running，供图从 entry 重新执行。

    gate_failed/failed/done 直接放行；awaiting_human 由调用方先确认没有挂起闸门
    （有闸门时应 approve/reject，而不是重跑）。
    """
    if entry not in RERUN_ENTRY_NODES:
        raise ValueError(f"不支持从节点重跑：{entry}（可用：{', '.join(RERUN_ENTRY_NODES)}）")
    db = _ledger()
    row = db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row is None:
        raise ValueError(f"任务不存在：{task_id}")
    ledger.set_task_status(db, task_id, "running", node=entry)


def _implement_gate_specs(config: dict, lane_name: str) -> list[tuple[str, str]]:
    """实现节点门禁：命令来自 project.yaml 的 tech_stack.gate_commands。

    未配置时回退旧硬编码命令（be/fe 分 lane 的 unit 命令保持原样），避免换项目
    必须改代码；返回 [(命令, 门禁名)]。
    """
    tech = config.get("tech_stack") or {}
    commands = dict(tech.get("gate_commands") or {})
    commands.update((tech.get("lane_gate_commands") or {}).get(lane_name) or {})
    defaults = {
        "typecheck": "npm run typecheck",
        "unit": ("npm run test:unit -w @river/api" if lane_name == "be"
                 else "npm run test -w @river/web"),
    }
    return [(str(commands.get(name) or defaults[name]), name) for name in defaults]


def _test_gate_specs(config: dict) -> list[tuple[str, str]]:
    """测试角色门禁：test.gates 引用 tech_stack.gate_commands，未配置时回退 typecheck/unit。"""
    names = (config.get("test") or {}).get("gates") or ["typecheck", "unit"]
    commands = dict(((config.get("tech_stack") or {}).get("gate_commands") or {}))
    fallbacks = {"typecheck": "npm run typecheck", "unit": "npm run test"}
    specs = []
    for name in names:
        command = commands.get(name) or fallbacks.get(name)
        if command:
            specs.append((str(name), str(command)))
    return specs


def _delivery_test_specs(config: dict, delivery: dict) -> list[tuple[str, str]]:
    specs = _test_gate_specs(config)
    overrides = (config.get("tech_stack") or {}).get("lane_gate_commands") or {}
    for lane in sorted({row["lane"] for row in delivery.get("commits", [])} & overrides.keys()):
        specs.extend((f"{lane}.{name}", command) for command, name in _implement_gate_specs(config, lane))
    return specs


def _read_doc(docs: Path, name: str) -> str:
    path = docs / name
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def review_node(state: DeliveryState) -> dict:
    """Validate the delivery and route actionable defects to their owning lanes."""
    import review_delivery as rd
    import worker as w

    db = _ledger()
    task_id = state["task_id"]
    status = db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()["status"]
    if status in ("awaiting_human", "gate_failed", "failed", "done"):
        return {"review_report": "", "review_lane_feedback": {}}
    config = _project_config()
    repo = _repo_path()
    docs = RIVER_WS / task_id / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    report_path = docs / "REVIEW.md"
    try:
        run, worker_id, epoch, _ = _start_leased_run(db, task_id, "review", with_codex_home=False,
            attempt=_node_attempt(db, task_id, "review"), output_path=str(report_path))
    except PermissionError as exc:
        ledger.record_gate_result(db, task_id, "budget_paused", False, str(exc))
        ledger.set_task_status(db, task_id, "awaiting_human", node="review")
        return {"review_report": "", "review_lane_feedback": {}}

    verdict, reason, fixes = "BLOCKED", "", {}
    evidence, model_report = [], ""
    deferred_tests = {}
    try:
        requirements = state.get("approved_requirements_text", "")
        design = state.get("approved_design_text", "")
        if not state.get("approved_g2") or not requirements or not design:
            raise ValueError("缺少 G1/G2 批准快照，请重新取得审批")
        ok, reason = _ac_coverage(design, requirements)
        ledger.record_gate_result(db, task_id, "review_ac_coverage", ok, reason, node_run_id=run)
        if not ok:
            raise ValueError(reason)
        criteria = {f"AC-{match[1]}": _design_path(match[2])
                    for line in _section(design, "## 验收映射").splitlines()
                    if (match := _AC_MAPPING.fullmatch(line))}
        lanes = state.get("approved_lane_plan") or state.get("lanes") or []
        delivery = state.get("delivery") or {}
        visible_head = {}
        base, head, files, diff, sources, gaps = rd.collect(repo, delivery, lanes, _rule_int("review", "diff_max"),
                                                            criteria.values(), visible_head=visible_head)
        ledger.record_gate_result(db, task_id, "review_commits", True, f"{base}..{head}", node_run_id=run)
        ledger.record_gate_result(db, task_id, "review_lane_scope", True, "各提交均符合批准范围", node_run_id=run)
        gate_records = rd.gates(db, task_id, delivery, lambda lane: _implement_gate_specs(config, lane))
        downstream = dict(_delivery_test_specs(config, delivery))
        if not downstream:
            raise ValueError("缺少独立测试门禁")
        gate_results = "\n".join(f"- {key} · {r['commit']} · {r['run']} · PASS\n{r['output']}"
                                 for key, r in gate_records.items())
        evidence = [f"Base: {base}", f"HEAD: {head}",
                    f"G1 需求 SHA-256: {hashlib.sha256(requirements.encode('utf-8')).hexdigest()}",
                    f"G2 设计 SHA-256: {hashlib.sha256(design.encode('utf-8')).hexdigest()}",
                    *(f"- {file}" for file in files),
                    *(f"- 取证缺口：{gap}" for gap in gaps)]
        g2_citation, prototype_evidence = rd.approved_prototype(
            db, task_id, state.get("design_doc") or "", state.get("prototype_dir"), design)
        if g2_citation:
            evidence.append(g2_citation)
        if _lane_status(repo) != (0, ""):
            raise ValueError("交付工作区有未提交改动，确认干净后重新审查")
        sensitive = (len({r["lane"] for r in delivery["commits"]}) > 1 or any(
            re.search(r"(^|[/_.-])(api|authz?|permissions?|migrations?|schema|security|rbac|roles?|lock|sql)([/_.-]|$)", path, re.I)
            for path in files))
        strategy = _section(design, "## 实现策略")
        approved_gates = dict(re.findall(r"(?m)^- 门禁证据[：:]\s*(AC-\d+)\s*\|\s*(\S+)\s*$", strategy))
        approved_tests = dict(re.findall(r"(?m)^- 独立验收[：:]\s*(AC-\d+)\s*\|\s*(\S+)\s*$", strategy))
        light = re.search(r"(?m)^- 审查[：:]轻量\s*\|\s*(\S+)\s*$", strategy)
        light_gate = gate_records.get(light[1]) if light else None
        lightweight = (not sensitive and len(files) <= 3 and len(diff.splitlines()) <= 80 and not gaps
            and set(criteria.values()) <= set(files) and light_gate and light_gate["commit"] == head
            and all(approved_gates.get(ac) == light[1] for ac in criteria)
            and light_gate["command"] in downstream.values())
        run_model = (config.get("review") or {}).get("codex", False)
        if lightweight:
            verdict, reason = "PASS", f"G2 批准轻量审查，{light[1]} 已绑定交付版本，独立测试继续执行"
        elif not run_model:
            if gaps or (sensitive and (config.get("review") or {}).get("require_for_sensitive")):
                raise ValueError("当前交付需要独立审查或补全证据")
            verdict, reason = "PASS", "项目关闭模型审查，机械核对通过"
        else:
            prompt = tmpl_render(
                "review-diff", task_id=task_id[:8], requirements_content=requirements, design_content=design,
                diff_content=diff, files_content=sources, project_context=_project_context(),
                delivery_context="\n".join(evidence), gate_results=gate_results,
                                 prototype_evidence=prototype_evidence,
                downstream_gates=", ".join(downstream),
                previous_review=state.get("previous_review") or "无")
            codex_home = _worker_codex_home(task_id, worker_id)
            model = route_model(ledger.evaluate_budget(db, QUOTA)["state"], "review")
            r = w.run_codex(prompt, repo, model=model, codex_home=str(codex_home),
                            log_dir=str(RIVER_WS / task_id / "logs"), sandbox="read-only", output_schema=rd.schema())
            meter.record_usage_safe(db, buffer_path=USAGE_BUFFER, task_id=task_id, agent_role="review",
                                    executor="codex", model=model, output_text=r.output, node_run_id=run)
            if not r.ok or _lane_status(repo) != (0, "") or rd.revision(repo, "HEAD") != head:
                raise ValueError(f"审查 Worker 执行失败或交付版本发生变化（退出码 {r.returncode}）")
            result, fixes = rd.validate(repo, r.message, base, head, files, criteria, gate_records, downstream, lanes, gaps,
                                       approved_gates=approved_gates, approved_tests=approved_tests,
                                       visible_head=visible_head)
            verdict, reason = result["verdict"], result["reason"]
            if verdict == "PASS":
                deferred_tests = {row["ref"]: downstream[row["ref"]] for row in result["criteria"]
                                  if row["status"] == "DEFERRED"}
            model_report = "\n".join([
                f"# REVIEW · {task_id[:8]}", "", "## 结论", verdict, reason, "", "## AC 核对",
                *(f"- {row['ac']} {row['status']} · {row['source']} {row['ref']} {row['line'] or ''}"
                  for row in result["criteria"]),
                "", "## 问题",
                *(f"- [{f['severity']}] {f['path']} {f['side']}:{f['line']}-{f['end_line']} · "
                  f"{f['ac'] or f['rule']} · {f['evidence']} · {f['fix']}" for f in result["findings"]
                  if f["severity"] != "low"),
                "", "## 建议",
                *(f"- {f['path']} {f['side']}:{f['line']} · {f['evidence']} · {f['fix']}"
                  for f in result["findings"] if f["severity"] == "low"),
            ])
    except Exception as exc:
        verdict, reason, fixes = "BLOCKED", str(exc)[:600], {}
        ledger.record_gate_result(db, task_id, "review_evidence", False, reason, node_run_id=run)
    agent_path = docs / "REVIEW-agent.md"
    if model_report or agent_path.is_file():
        agent_path.write_text((model_report or f"# REVIEW · {task_id[:8]}\n\n## 结论\n{verdict}\n{reason}") + "\n",
                              encoding="utf-8")
    ledger.record_gate_result(db, task_id, "review_agent", verdict == "PASS", reason, node_run_id=run)
    count = int(state.get("review_rework_count") or 0)
    limit = max(0, _rule_int("review", "rework_attempts"))
    if verdict == "FAIL":
        count += 1
    can_rework = bool(fixes) and count <= limit
    if verdict != "PASS":
        ledger.record_gate_result(db, task_id, "review_rework", can_rework,
                                  f"{verdict} · 返工 {count}/{limit} · {reason}", node_run_id=run)
    feedback = "\n\n".join(f"{lane}\n{notes}" for lane, notes in fixes.items())
    mechanical = [f"- {row['gate_name']} · {'PASS' if row['passed'] else 'FAIL'} · {row['output']}"
                  for row in db.execute("SELECT gate_name, passed, output FROM gate_results WHERE node_run_id=? "
                                        "AND gate_name NOT IN ('review_agent','review_rework') ORDER BY id", (run,))]
    lines = [f"# REVIEW · {task_id[:8]}", "", "## 结论", verdict, reason, "",
             "## 机械核对", *mechanical, "", "## 交付证据", *evidence]
    if deferred_tests:
        lines.extend(["", "## 待独立验收", *(f"- {name}" for name in deferred_tests)])
    lines.extend(["", "## 返工", feedback or "无"])
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not _close(db, run, "done" if verdict == "PASS" else "failed", worker_id, epoch):
        return {"review_report": "", "review_lane_feedback": {}}
    ledger.set_task_status(db, task_id, "running" if verdict == "PASS" or can_rework else "gate_failed", node="review")
    review_receipt = retro_evidence.receipt(report_path, head, run,
        agent_digest=hashlib.sha256(agent_path.read_bytes()).hexdigest() if model_report else "") if verdict == "PASS" else {}
    return {"review_report": str(report_path) if verdict == "PASS" else "", "review_receipt": review_receipt,
            "review_deferred_tests": deferred_tests if verdict == "PASS" else {},
            "review_feedback": feedback if can_rework else "", "review_lane_feedback": fixes if can_rework else {},
            "previous_review": model_report if can_rework else "", "review_rework_count": count if can_rework else 0}


def _render_test_report(task_id: str, results: list[tuple], commits: list[str]) -> str:
    passed = sum(1 for _, _, ok, _ in results if ok)
    all_ok = bool(results) and passed == len(results)
    lines = [f"# TEST REPORT · {task_id[:8]}", "", "## 结果",
             f"{'PASS' if all_ok else 'FAIL'} · {passed}/{len(results)} 门禁通过", "",
             "## 门禁明细", "| 门禁 | 命令 | 结果 |", "| --- | --- | --- |"]
    for name, command, ok, _ in results:
        lines.append(f"| {name} | `{command}` | {'PASS' if ok else 'FAIL'} |")
    failures = [(name, out) for name, _, ok, out in results if not ok]
    if failures:
        lines += ["", "## 失败输出"]
        for name, out in failures:
            lines += [f"### {name}", "```text", out[-1500:], "```"]
    lines += ["", "## 实现提交"]
    lines += [f"- `{c}`" for c in commits] if commits else ["- （无 git_commit 记录）"]
    return "\n".join(lines) + "\n"


def test_node(state: DeliveryState) -> dict:
    """测试角色（独立验证）：编排层跑全量测试门禁 → TEST_REPORT.md。

    不调用 codex：测试 Agent 自己写测试/自己判通过会形成自证；门禁在编排层执行，
    产物与失败输出全部入账（AC-06 结论）。任一必跑门禁失败即 gate_failed。
    """
    import worker as w
    import review_delivery as rd

    db = _ledger()
    task_id = state["task_id"]
    status = db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()["status"]
    if status in ("awaiting_human", "gate_failed", "failed", "done"):
        return {"test_report": "", "test_receipt": {}}  # 上游未成功，测试角色不得启动（也不得掩盖失败）

    config = _project_config()
    delivery = state.get("delivery") or {}
    gates = _delivery_test_specs(config, delivery)
    docs_dir = RIVER_WS / task_id / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    report_path = docs_dir / "TEST_REPORT.md"
    try:
        run, worker_id, epoch, _ = _start_leased_run(
            db, task_id, "test", with_codex_home=False,
            attempt=_node_attempt(db, task_id, "test"),
            output_path=str(report_path))
    except PermissionError as exc:  # AC-13：预算 PAUSED 时拒发新节点
        ledger.record_gate_result(db, task_id, "budget_paused", False, str(exc))
        ledger.set_task_status(db, task_id, "awaiting_human", node="test")
        return {"test_report": "", "test_receipt": {}}

    results: list[tuple[str, str, bool, str]] = []
    deferred_tests = state.get("review_deferred_tests") or {}
    if deferred_tests.items() - dict(gates).items():
        reason = "审查移交的独立验收门禁缺失或命令已变更，请重新审查"
        ledger.record_gate_result(db, task_id, "test_config", False, reason, node_run_id=run)
        report_path.write_text(_render_test_report(task_id, [("config", "", False, reason)], []), encoding="utf-8")
        if _close(db, run, "failed", worker_id, epoch):
            ledger.set_task_status(db, task_id, "gate_failed", node="test")
        return {"test_report": "", "test_receipt": {}}
    if not gates and "contracts" not in config:
        ledger.record_gate_result(db, task_id, "test_config", False,
                                  "未配置可用测试门禁（test.gates / tech_stack.gate_commands）",
                                  node_run_id=run)
    repo = _repo_path()
    records = {}
    if delivery.get("commits"):
        try:
            if rd.revision(repo, "HEAD") != delivery["commits"][-1]["commit"] or _lane_status(repo) != (0, ""):
                raise ValueError("独立测试工作区与交付版本不一致")
            if (config.get("test") or {}).get("reuse_implementation_gates"):
                records = rd.gates(db, task_id, delivery, lambda lane: _implement_gate_specs(config, lane))
        except (ValueError, OSError) as exc:
            ledger.record_gate_result(db, task_id, "test_delivery", False, str(exc), node_run_id=run)
            if _close(db, run, "failed", worker_id, epoch):
                ledger.set_task_status(db, task_id, "gate_failed", node="test")
            return {"test_report": "", "test_receipt": {}}
    for name, command in gates:
        if name not in deferred_tests and name in ("typecheck", "unit") and rd.reusable(repo, config, delivery, records, command):
            passed, out = True, "同一提交与环境的仓库级实现门禁已通过"
        else:
            passed, out = w.run_gate(command, repo)
        stable = True
        if delivery.get("commits"):
            try:
                stable = _lane_status(repo) == (0, "") and rd.revision(repo, "HEAD") == delivery["commits"][-1]["commit"]
            except ValueError:
                stable = False
        if not stable:
            passed, out = False, "门禁运行期间无法确认交付版本"
        ledger.record_gate_result(db, task_id, f"test_{name}", passed, out, node_run_id=run)
        results.append((name, command, passed, out))
        if not stable:
            break

    contract = {}
    if "contracts" in config and all(ok for _, _, ok, _ in results):
        import contract_delivery
        import subprocess
        import tarfile

        try:
            contract, checks = contract_delivery.verify(config, repo, retro_evidence.delivery_head(state))
        except (ValueError, OSError, subprocess.SubprocessError, tarfile.TarError) as exc:
            checks = [("contract_config", "", False, str(exc))]
        for name, command, passed, out in checks:
            ledger.record_gate_result(db, task_id, f"test_{name}", passed, out, node_run_id=run)
            results.append((name, command, passed, out))

    commits = [part["commit"] for part in delivery.get("commits", [])] or [r["operation_key"] for r in db.execute(
        "SELECT operation_key FROM operations WHERE task_id=? AND op_type='git_commit' "
        "ORDER BY id", (task_id,))]
    report_path.write_text(_render_test_report(task_id, results, commits), encoding="utf-8")

    all_ok = bool(gates or contract) and all(ok for _, _, ok, _ in results)
    if all_ok:
        if not _close(db, run, "done", worker_id, epoch):
            return {"test_report": ""}
        ledger.set_task_status(db, task_id, "running", node="test")
        extra = {"contract": contract} if contract else {}
        return {"test_report": str(report_path),
                "test_receipt": retro_evidence.receipt(report_path, retro_evidence.delivery_head(state), run, **extra)}
    if _close(db, run, "failed", worker_id, epoch):
        ledger.set_task_status(db, task_id, "gate_failed", node="test")
    return {"test_report": "", "test_receipt": {}}


def _healthcheck(target: str, repo: str, timeout: int) -> tuple[bool, str]:
    """staging 健康检查：http(s) URL 轮询，否则按命令执行。"""
    if not target:
        return True, "未配置 healthcheck"
    if target.startswith(("http://", "https://")):
        import time
        import urllib.parse
        import urllib.request
        host = urllib.parse.urlparse(target).hostname or ""
        # 本机健康检查绕过系统代理（Windows 代理配置常导致 localhost 被转发）
        if host in ("localhost", "127.0.0.1", "::1"):
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        else:
            opener = urllib.request.build_opener()
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            try:
                with opener.open(target, timeout=5) as resp:
                    status = getattr(resp, "status", 200)
                    if 200 <= status < 400:
                        return True, f"HTTP {status}"
            except Exception as exc:
                last = str(exc)
            time.sleep(2)
        return False, f"健康检查未通过：{last}"
    import worker as w
    passed, output = w.run_gate(target, repo, timeout=timeout)
    return passed, output


def _render_deploy_report(task_id: str, status: str, steps: list[tuple], note: str = "") -> str:
    lines = [f"# DEPLOY REPORT · {task_id[:8]}", "", "## 结果", status, ""]
    if note:
        lines += [note, ""]
    lines += ["## 步骤", "| 步骤 | 命令 | 结果 |", "| --- | --- | --- |"]
    lines += [f"| {name} | `{command}` | {'PASS' if ok else 'FAIL'} |"
              for name, command, ok, _ in steps] or ["| - | （未执行） | - |"]
    failures = [(name, out) for name, _, ok, out in steps if not ok]
    if failures:
        lines += ["", "## 失败输出"]
        for name, out in failures:
            lines += [f"### {name}", "```text", out[-1500:], "```"]
    return "\n".join(lines) + "\n"


def deploy_node(state: DeliveryState) -> dict:
    """部署角色：按 project.yaml deploy.staging 执行部署 → 冒烟/健康检查 → DEPLOY_REPORT.md。

    - 未配置 commands 且 required=false：显式跳过并留痕（deploy_skipped 门禁 + 报告）；
    - required=true 但未配置：转人审（不静默跳过）；
    - 命令失败：gate_failed，报告保留失败输出。
    """
    import worker as w

    db = _ledger()
    task_id = state["task_id"]
    status = db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()["status"]
    if status in ("awaiting_human", "gate_failed", "failed", "done"):
        return {"deploy_report": "", "deploy_skipped": False}

    config = _project_config()
    staging = (config.get("deploy") or {}).get("staging") or {}
    required = bool(staging.get("required", False))
    commands = [str(c) for c in (staging.get("commands") or [])]
    smoke = str(staging.get("smoke") or "")
    health = str(staging.get("healthcheck") or "")
    timeout = int(staging.get("timeout_seconds", 900))
    docs_dir = RIVER_WS / task_id / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    report_path = docs_dir / "DEPLOY_REPORT.md"
    import contract_delivery

    try:
        if "contracts" in config or (state.get("test_receipt") or {}).get("contract"):
            contract_delivery.validate(db, task_id, docs_dir, state, config, _repo_path())
    except (ValueError, OSError) as exc:
        ledger.record_gate_result(db, task_id, "deploy_contract", False, str(exc))
        report_path.write_text(_render_deploy_report(task_id, f"BLOCKED · {exc}", []), encoding="utf-8")
        ledger.set_task_status(db, task_id, "gate_failed", node="deploy")
        return {"deploy_report": "", "deploy_skipped": False, "deploy_receipt": {}, "g3_receipt": {}}
    if deferred_tests := state.get("review_deferred_tests"):
        tested, _ = retro_evidence.read_report(db, task_id, docs_dir, state, "test", "TEST_REPORT.md",
                                               retro_evidence.delivery_head(state))
        passed_tests = {row["gate_name"] for row in db.execute(
            "SELECT gate_name FROM gate_results WHERE task_id=? AND node_run_id=? AND passed=1",
            (task_id, tested.get("run")))}
        if not {f"test_{name}" for name in deferred_tests} <= passed_tests:
            reason = "审查延期的独立验收尚未在当前交付提交通过，请先重跑测试"
            ledger.record_gate_result(db, task_id, "deploy_deferred_tests", False, reason)
            report_path.write_text(_render_deploy_report(task_id, "BLOCKED · " + reason, []), encoding="utf-8")
            ledger.set_task_status(db, task_id, "gate_failed", node="deploy")
            return {"deploy_report": "", "deploy_skipped": False, "deploy_receipt": {}, "g3_receipt": {}}

    if not commands:
        if required:
            ledger.record_gate_result(
                db, task_id, "deploy_config", False,
                "deploy.staging.required=true 但未配置 commands")
            ledger.set_task_status(db, task_id, "awaiting_human", node="deploy")
            report_path.write_text(_render_deploy_report(
                task_id, "BLOCKED · 未配置 staging.commands（required=true）", [],
                "请补充 project.yaml 的 deploy.staging.commands 后重跑。"), encoding="utf-8")
            return {"deploy_report": "", "deploy_skipped": False}
        ledger.record_gate_result(
            db, task_id, "deploy_skipped", True,
            "deploy.staging.commands 未配置且 required=false（显式跳过）")
        skip_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        report_path.write_text(_render_deploy_report(
            task_id, "SKIPPED · 未配置 staging.commands（required=false）", [],
            "这是显式跳过而非部署成功；staging 就绪后配置 commands 并设 required=true。"), encoding="utf-8")
        return {"deploy_report": str(report_path), "deploy_skipped": True, "g3_receipt": {},
                "deploy_receipt": retro_evidence.receipt(report_path, retro_evidence.delivery_head(state), "",
                                                        skipped=True, gate_id=skip_id)}

    try:
        attempt = _node_attempt(db, task_id, "deploy")
        run, worker_id, epoch, _ = _start_leased_run(
            db, task_id, "deploy", with_codex_home=False, attempt=attempt,
            output_path=str(report_path))
    except PermissionError as exc:
        ledger.record_gate_result(db, task_id, "budget_paused", False, str(exc))
        ledger.set_task_status(db, task_id, "awaiting_human", node="deploy")
        return {"deploy_report": "", "deploy_skipped": False}

    repo = _repo_path()
    import review_delivery as rd
    head = retro_evidence.delivery_head(state)
    steps: list[tuple[str, str, bool, str]] = []
    try:
        ok = not head or (rd.revision(repo, "HEAD") == head and _lane_status(repo) == (0, ""))
    except (ValueError, OSError):
        ok = False
    if not ok:
        ledger.record_gate_result(db, task_id, "deploy_delivery", False, "部署工作区与交付版本不一致", node_run_id=run)
        steps.append(("delivery", "", False, "部署工作区与交付版本不一致"))
    import external_recovery
    project = external_recovery.staging_project(config)
    release = {}
    intent_key = f"{task_id}:deploy_staging_intent:{attempt}"
    if ok and head and project:
        try:
            ledger.record_operation(db, task_id, intent_key, "deploy_staging_intent", head,
                                    node_run_id=run, status="pending", worker_id=worker_id, lease_epoch=epoch,
                                    result_ref=external_recovery.staging_config_digest(config))
        except ledger.StaleLeaseError:
            return {"deploy_report": "", "deploy_skipped": False}
    for index, command in enumerate(commands, start=1):
        if not ok:
            break
        if "{delivery_head}" in command:
            if not head:
                ok = False
                reason = "部署命令要求已验收的交付提交"
                ledger.record_gate_result(db, task_id, "deploy_delivery", False, reason, node_run_id=run)
                steps.append(("delivery", "", False, reason))
                break
            command = command.replace("{delivery_head}", head)
        passed, out = w.run_gate(command, repo, timeout=timeout)
        if passed and project:
            from project_runs import staging_proof
            try:
                release = staging_proof(out, project, head)
            except ValueError as exc:
                passed, out = False, str(exc)
        ledger.record_gate_result(db, task_id, f"deploy_step_{index}", passed, out, node_run_id=run)
        steps.append((f"step{index}", command, passed, out))
        if not passed:
            ok = False
            break
    if ok and smoke:
        passed, out = w.run_gate(smoke, repo, timeout=timeout)
        ledger.record_gate_result(db, task_id, "deploy_smoke", passed, out, node_run_id=run)
        steps.append(("smoke", smoke, passed, out))
        ok = passed
    if ok and health:
        passed, out = _healthcheck(health, repo, timeout)
        ledger.record_gate_result(db, task_id, "deploy_healthcheck", passed, out, node_run_id=run)
        steps.append(("healthcheck", health, passed, out))
        ok = passed
    if ok and head:
        try:
            ok = rd.revision(repo, "HEAD") == head
        except (ValueError, OSError):
            ok = False
        if not ok:
            ledger.record_gate_result(db, task_id, "deploy_delivery", False, "部署期间交付版本发生变化", node_run_id=run)
            steps.append(("delivery", "", False, "部署期间交付版本发生变化"))

    release_note = f"## 运行制品\n\n```text\nSTAGING PROOF {json.dumps(release)}\n```" if ok and release else ""
    report_path.write_text(_render_deploy_report(
        task_id, "PASS · staging 部署与检查通过" if ok else "FAIL · staging 部署/检查失败", steps, release_note),
        encoding="utf-8")
    if ok:
        try:  # B1 栅栏：epoch 已失效则部署副作用不登记、任务状态不落 done
            ledger.record_operation(db, task_id, f"{task_id}:deploy_staging:{attempt}",
                                    "deploy_staging", head if project and head else "staging", node_run_id=run,
                                    worker_id=worker_id, lease_epoch=epoch)
            if project:
                with db:
                    db.execute("UPDATE operations SET status='done' WHERE operation_key=?", (intent_key,))
        except ledger.StaleLeaseError as exc:
            print(f"[lease] stale operation rejected: {exc}")
            return {"deploy_report": "", "deploy_skipped": False}
        if not _close(db, run, "done", worker_id, epoch):
            return {"deploy_report": "", "deploy_skipped": False}
        ledger.set_task_status(db, task_id, "running", node="deploy")
        return {"deploy_report": str(report_path), "deploy_skipped": False, "g3_receipt": {},
                "deploy_receipt": retro_evidence.receipt(report_path, head, run, skipped=False, release=release)}
    if _close(db, run, "failed", worker_id, epoch):
        ledger.set_task_status(db, task_id, "gate_failed", node="deploy")
    return {"deploy_report": "", "deploy_skipped": False}


def review_g3(state: DeliveryState) -> Command:
    """人工闸门 G3：staging 验收（部署报告/环境）。驳回须带理由，回实现修复（R1），上限同 G1。"""
    decision = interrupt({"gate": "G3_staging", "artifact": state.get("deploy_report")})
    if decision == "approved":
        db = _ledger()
        path = Path(state.get("deploy_report") or "")
        deployed = state.get("deploy_receipt") or {}
        approved = {}
        if deployed.get("head") and not deployed.get("skipped") and path.is_file():
            match = db.execute("SELECT id FROM approvals WHERE task_id=? AND gate_name='G3_staging' "
                               "AND decision='approved' AND artifact_hash=? ORDER BY id DESC LIMIT 1",
                               (state["task_id"], ledger.artifact_digest([path]))).fetchone()
            if match and hashlib.sha256(path.read_bytes()).hexdigest() == deployed["digest"]:
                approved = {"head": deployed["head"], "approval": match["id"]}
        ledger.set_task_status(db, state["task_id"], "running")
        return Command(goto="retro", update={"approved_g3": True, "rejection_reason": "", "g3_receipt": approved})
    db = _ledger()
    reason = ledger.latest_rejection_reason(db, state["task_id"], "G3_staging")
    revises = int(state.get("revises_g3") or 0) + 1
    if revises > MAX_REVISES:
        ledger.record_gate_result(db, state["task_id"], "revise_limit_g3", False,
                                  f"G3 驳回修订已达 {MAX_REVISES} 次上限，终止为 rejected")
        ledger.set_task_status(db, state["task_id"], "rejected", node="implement")
        return Command(goto=END, update={"revises_g3": revises})
    ledger.set_task_status(db, state["task_id"], "running")
    return Command(goto="implement",
                   update={"approved_g3": False, "rejection_reason": reason,
                           "revises_g3": revises})


def _generate_retro(state: DeliveryState, db, task_id: str, run: str, codex_home: Path) -> None:
    import json
    import memory
    import worker as w

    docs = RIVER_WS / task_id / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    report_path = docs / "RETRO.md"
    snapshot = docs / f"RETRO-evidence-{run}.json"
    try:
        config = _project_config()
        bundle = retro_evidence.collect(db, task_id, docs, state, _repo_path(),
            lambda lane: _implement_gate_specs(config, lane), config.get("project") or RIVER_WS.name)
        prompt = tmpl_render("retro-gen", task_id=task_id[:8],
                             evidence_bundle=retro_evidence.model_context(bundle))
        model = route_model(ledger.evaluate_budget(db, QUOTA)["state"], "retro")
        r = w.run_codex(prompt, _repo_path(), model=model, codex_home=str(codex_home),
                        log_dir=str(RIVER_WS / task_id / "logs"), sandbox="read-only",
                        output_schema=retro_evidence.schema(bundle))
        meter.record_usage_safe(db, buffer_path=USAGE_BUFFER, task_id=task_id,
                                agent_role="retro", executor="codex", model=model,
                                output_text=r.output, node_run_id=run)
        if not r.ok:
            raise ValueError(f"复盘 Worker 执行失败（退出码 {r.returncode}）")
        result = retro_evidence.validate(r.message, bundle)
        bundle["candidates"] = result["candidates"]
        raw = (json.dumps(bundle, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        snapshot.write_bytes(raw)
        draft = report_path.with_name(f".RETRO-{run}.tmp")
        draft.write_text(retro_evidence.render(bundle, result, snapshot.name), encoding="utf-8")
        draft.replace(report_path)
        ledger.record_gate_result(db, task_id, "retro_doc", True, digest, node_run_id=run)
    except Exception as exc:
        reason = w.safe_gate_text(str(exc))[:500]
        ledger.record_gate_result(db, task_id, "retro_doc", False, reason, node_run_id=run)
        try:
            report_path.write_text(f"# RETRO · task {task_id[:8]}\n\n## 结果\n复盘证据校验失败，详见本轮 retro_doc。\n",
                                   encoding="utf-8")
        except OSError:
            pass
        return
    try:
        candidates = memory.enqueue(db, bundle, run, digest, result["candidates"])
        if candidates:
            ledger.record_gate_result(db, task_id, "retro_candidates", True,
                                      "待人工审核：" + ", ".join(candidates), node_run_id=run)
    except (ValueError, OSError, sqlite3.Error) as exc:
        ledger.record_gate_result(db, task_id, "retro_candidates", False, w.safe_gate_text(str(exc))[:500], node_run_id=run)


def retro_node(state: DeliveryState) -> dict:
    db = _ledger()
    task_id = state["task_id"]
    status = db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()["status"]
    if status in ("awaiting_human", "gate_failed", "failed", "rejected"):
        return {}
    generate = bool((_project_config().get("retro") or {}).get("generate"))
    try:
        run, worker_id, epoch, codex_home = _start_leased_run(db, task_id, "retro", with_codex_home=generate)
    except PermissionError as exc:
        ledger.record_gate_result(db, task_id, "retro_doc", False, str(exc)[:500])
        ledger.set_task_status(db, task_id, "done", node="retro")
        return {}
    if generate:
        _generate_retro(state, db, task_id, run, codex_home)
    if _close(db, run, "done", worker_id, epoch):
        ledger.set_task_status(db, task_id, "done", node="retro")
    return {}


def build_app(checkpointer, entry: str | None = None):
    """构建图。entry 非空时从该节点开始（失败/终态任务重跑），否则走完整链路。"""
    refresh_runtime_config()  # 读 YAML 刷新预算/模型配置（控制台保存后无需重启进程）
    if entry is not None and entry not in RERUN_ENTRY_NODES:
        raise ValueError(f"不支持从节点重跑：{entry}（可用：{', '.join(RERUN_ENTRY_NODES)}）")
    g = StateGraph(DeliveryState)
    g.add_node("requirements", requirements_node)
    g.add_node("review_g1", review_g1)
    g.add_node("design", design_node)
    g.add_node("prototype", prototype_node)
    g.add_node("review_g2", review_g2)
    g.add_node("implement", implement_node)
    g.add_node("review", review_node)
    g.add_node("test", test_node)
    g.add_node("deploy", deploy_node)
    g.add_node("review_g3", review_g3)
    g.add_node("retro", retro_node)
    g.add_edge(START, entry or "requirements")
    # A2：文档生成失败（空/残缺/执行异常）时直接终止，不进入人审
    g.add_conditional_edges("requirements", lambda s: _route_after_doc(s, "requirements_doc"),
                            {"continue": "review_g1", "end": END})
    g.add_conditional_edges("design", lambda s: _route_after_doc(s, "design_doc"),
                            {"continue": "prototype", "end": END})
    # 原型/UI → G2（设计 + 原型一并审）；无界面变化时节点留痕跳过，仍进入 G2
    g.add_conditional_edges("prototype",
                            lambda s: "continue" if s.get("prototype_dir") else "end",
                            {"continue": "review_g2", "end": END})
    # 实现 → 自动审查（C 层）→ 独立测试 → 部署 → G3 staging 验收 → retro
    g.add_conditional_edges("implement",
                            lambda s: "continue" if s.get("implement_summary") else "end",
                            {"continue": "review", "end": END})
    g.add_conditional_edges("review", _route_after_review,
                            {"test": "test", "implement": "implement", "end": END})
    g.add_conditional_edges("test", lambda s: "continue" if s.get("test_report") else "end",
                            {"continue": "deploy", "end": END})
    g.add_conditional_edges(
        "deploy",
        lambda s: ("review" if not s.get("deploy_skipped") else "retro") if s.get("deploy_report") else "end",
        {"review": "review_g3", "retro": "retro", "end": END})
    g.add_edge("retro", END)
    return g.compile(checkpointer=checkpointer)


def make_checkpointer(graph_db: Path | None = None):
    """直接持有 sqlite3 连接构造 SqliteSaver（官方支持方式，避免上下文管理器生命周期问题）。"""
    import sqlite3
    path = graph_db or (RIVER_WS / "graph.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    return SqliteSaver(conn)
