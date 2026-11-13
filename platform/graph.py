"""LangGraph 编排骨架 · V2.2 §2.4（kill 语义修订版）

双账本分离（V2.1 核验结论）：图执行位置存 graph.db（checkpointer），
业务事实存 state.db（ledger）——两者不合并。
人审闸门用 interrupt()：图挂起时状态已持久化，等数天再批准不丢进度。
P1 骨架节点为轻量占位（Worker/codex 调用接入为下一步）；本骨架验证
interrupt + checkpointer + 崩溃恢复语义。
"""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import sys
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

sys.path.insert(0, str(Path(__file__).parent))
import ledger
import meter
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
    global RIVER_WS, USAGE_BUFFER
    RIVER_WS = PROJECTS_DIR / name
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
_AC_RE = re.compile(r"\bAC[-\s]?(\d+)\b", re.IGNORECASE)


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
    approved_g1: bool
    approved_g2: bool
    approved_g3: bool
    rejection_reason: str  # 最近一次人工驳回的理由（R1：回传给上游 Agent 判断是否修订）
    revises_g1: int  # 各闸门驳回回环次数（防无限循环，超过上限判 rejected 终态）
    revises_g2: int
    revises_g3: int
    base_branch: str
    lanes: list  # [{name, node, branch, scope, prompt, commit_msg}]
    implement_summary: str
    review_report: str
    review_feedback: str
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
              worker_id: str | None = None, epoch: int | None = None) -> bool:
    """文档产出校验：空/过短/缺章节均判失败并留痕（不进入人审）。"""
    min_len = _rule_int("doc", "min_len")
    max_len = _rule_int("doc", "max_len")
    if not doc:
        reason = "Agent 未产出可解析的 Markdown（无一级标题）"
    elif len(doc) < min_len:
        reason = f"Agent 产物过短（{len(doc)} 字符 < {min_len}）"
    elif len(doc) > max_len:
        reason = f"Agent 产物异常超长（{len(doc)} 字符 > {max_len}，疑似日志噪声混入）"
    else:
        missing = [h for h in required_headings if h not in doc]
        reason = f"缺少必需章节：{missing}" if missing else ""
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


def _section(text: str, heading: str) -> str:
    """取 Markdown 中单个二级章节正文（到下一个 ## 为止）；缺失返回空串。"""
    start = text.find(heading)
    if start < 0:
        return ""
    body = text[start + len(heading):]
    end = body.find("\n## ", 1)
    return body if end < 0 else body[:end]


def _ac_ids(text: str) -> list[str]:
    """提取并归一化验收标准编号（AC-1 / AC 1 / AC1 → AC-1），保持首次出现顺序。"""
    seen: list[str] = []
    for m in _AC_RE.finditer(text or ""):
        ac = f"AC-{int(m.group(1))}"
        if ac not in seen:
            seen.append(ac)
    return seen


def _ac_coverage(design: str, requirements: str) -> tuple[bool, str]:
    """机械核对：REQUIREMENTS 的验收标准须全部出现在 DESIGN 的「验收映射」。

    只核对编号（AC-n），不评判映射内容——内容质量仍由 G2 人审负责。
    """
    wanted = _ac_ids(_section(requirements, "## 验收标准") or requirements)
    if not wanted:
        return True, "REQUIREMENTS 未声明 AC 编号，跳过覆盖核对"
    mapping = _section(design, "## 验收映射")
    covered = set(_ac_ids(mapping))
    missing = [ac for ac in wanted if ac not in covered]
    if missing:
        return False, f"验收映射缺少：{', '.join(missing)}（需求共 {len(wanted)} 条）"
    return True, f"{len(wanted)} 条 AC 均出现在验收映射"


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
    post_check：契约级机械核对（doc -> (ok, reason)），在收尾前执行并入账。
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
    prompt = tmpl_render(template, task_id=task_id[:8],
                         project_context=_project_context(), **params)
    repo = _repo_path()
    # G-01：文档节点只读执行。矩阵要求需求/设计/Retro 对项目仓库只读（V2.2 §6.4），
    # 沙箱层用 read-only；收尾前再比对脏文件，防止沙箱配置缺失导致静默降级（C-007 教训）。
    dirty_before = _repo_dirty(repo)
    try:
        r = w.run_codex(prompt, repo, model=model, codex_home=str(codex_home),
                        log_dir=str(RIVER_WS / task_id / "logs"), sandbox="read-only",
                        images=images)
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
    doc = _extract_markdown(r.output)
    if not _doc_gate(db, task_id, node_name, run, doc, required_headings,
                     worker_id=worker_id, epoch=epoch):
        return ""
    if post_check is not None:
        ok, reason = post_check(doc)
        ledger.record_gate_result(db, task_id, post_check_name or f"{node_name}_contract",
                                  ok, reason, node_run_id=run)
        if not ok:
            if _close(db, run, "failed", worker_id, epoch):
                ledger.set_task_status(db, task_id, "gate_failed", node=node_name)
            return ""
    docs_dir.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(doc + "\n", encoding="utf-8")
    if not _close(db, run, "done", worker_id, epoch):
        return ""  # 租约被接管：旧 Worker 不得宣告完成
    return str(doc_path)


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
    attach_dir = RIVER_WS / state["task_id"] / "attachments"
    images: list[str] = []
    if attach_dir.is_dir():
        images = sorted(str(p) for p in attach_dir.iterdir()
                        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg"))
    attachments = "\n".join(f"- {p}" for p in images) or "无"
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
        return Command(goto="design", update={"approved_g1": True, "rejection_reason": ""})
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
    section = _section(design, "## 文件归属表") or design
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


def _needs_prototype(design: str, requirements: str) -> tuple[bool | None, str]:
    """V2.2 §06：界面变化需验证；小型后端任务可跳过 UI。

    归属表按**表头**定位 lane 列（列序无关）：含 fe → 生成；全为 be → 跳过；
    有无法识别的 lane 值或缺 lane 列时回退关键词；**缺 lane 列**返回 None，
    由调用方记门禁失败（不猜测列位）。
    """
    header, rows = _lane_table(design)
    if header is not None:
        idx = next((i for i, h in enumerate(header)
                    if "lane" in h.lower() or "角色" in h), None)
        if idx is None:
            return None, f"文件归属表缺少 lane 列（表头：{', '.join(header)}）"
        lanes: set[str] = set()
        for row in rows:
            if idx >= len(row) or not row[idx]:
                continue
            lanes.add(row[idx].lower())
        if not lanes:
            return None, "文件归属表无有效数据行（lane 列为空）"
        normalized = {_LANE_ALIASES.get(v, v) for v in lanes}
        unknown = normalized - {"fe", "be"}
        if not unknown:
            if "fe" in normalized:
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
            f"本次变更不涉及界面，跳过原型生成。判定依据：{why}。\n\n"
            "## 验收映射\n无界面验收项；界面相关 AC 不适用。\n\n"
            "## 局限\n未生成原型，G2 仅审阅 DESIGN.md。\n")


def _prototype_gate(db, task_id: str, run: str, proto_dir: Path, *,
                    worker_id: str | None = None, epoch: int | None = None) -> bool:
    """原型产物校验：HTML 骨架/长度 + 说明文档章节；任一不满足即失败留痕。"""
    problems: list[str] = []
    html_min = _rule_int("prototype", "html_min")
    html_max = _rule_int("prototype", "html_max")
    md_min = _rule_int("prototype", "md_min")
    html = proto_dir / "index.html"
    note = proto_dir / "PROTOTYPE.md"
    if not html.is_file():
        problems.append("缺少 index.html")
    else:
        text = html.read_text(encoding="utf-8", errors="replace")
        if len(text) < html_min:
            problems.append(f"index.html 过短（{len(text)} < {html_min} 字符）")
        elif len(text) > html_max:
            problems.append(f"index.html 异常超大（{len(text)} > {html_max} 字符）")
        elif "<html" not in text.lower():
            problems.append("index.html 缺少 <html> 骨架")
    if not note.is_file():
        problems.append("缺少 PROTOTYPE.md")
    else:
        md = note.read_text(encoding="utf-8", errors="replace")
        missing = [h for h in ("## 原型说明", "## 验收映射", "## 局限") if h not in md]
        if len(md) < md_min:
            problems.append(f"PROTOTYPE.md 过短（{len(md)} < {md_min} 字符）")
        elif missing:
            problems.append(f"PROTOTYPE.md 缺少章节：{missing}")
    reason = "；".join(problems)
    if reason:
        ledger.record_gate_result(db, task_id, "prototype_files", False, reason, node_run_id=run)
        if _close(db, run, "failed", worker_id, epoch):
            ledger.set_task_status(db, task_id, "gate_failed", node="prototype")
        return False
    ledger.record_gate_result(db, task_id, "prototype_files", True,
                              "index.html + PROTOTYPE.md", node_run_id=run)
    return True


def _clean_prototype_staging(repo: Path) -> list[str]:
    """清理仓库内的原型暂存副本（<repo>/.agentcode-prototype/*）。

    原型正本在 <ws>/<task>/prototype，随 G2 人审；仓库内目录只是 codex
    workspace-write 的临时落点。进程被杀时 finally 不执行 → 会残留，故在每次
    原型生成前清理（本项目 N=1 串行，不会误删在跑任务的暂存）。
    """
    root = repo / ".agentcode-prototype"
    removed: list[str] = []
    if not root.is_dir():
        return removed
    for child in root.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
            removed.append(child.name)
    try:
        root.rmdir()  # 仅当为空时成功
    except OSError:
        pass
    return removed


def prototype_node(state: DeliveryState) -> dict:
    """原型/UI 角色（V2.2 §06）：读设计资产，写专属原型目录。

    触发：DESIGN.md 文件归属表含 fe lane，或需求/设计出现界面关键词；否则留痕跳过
    （允许小型后端任务跳过 UI）。产物：<ws>/<task_id>/prototype/{index.html,PROTOTYPE.md}，
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
            output_path=str(proto_dir / "PROTOTYPE.md"))
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
    # 清理崩溃残留的仓库内暂存副本；原型正本在任务目录，不受影响
    _clean_prototype_staging(repo)
    if not needed:
        (proto_dir / "PROTOTYPE.md").write_text(
            _render_prototype_skipped(task_id, why), encoding="utf-8")
        ledger.record_gate_result(db, task_id, "prototype_skipped", True, why, node_run_id=run)
        if not _close(db, run, "done", worker_id, epoch):
            return {"prototype_dir": ""}
        ledger.set_task_status(db, task_id, "running", node="prototype")
        return {"prototype_dir": str(proto_dir)}

    model = route_model(budget["state"], "prototype")
    # Agent 写入须落在 codex workspace-write 范围内：先在仓库内暂存，再移入专属原型目录
    staging = repo / ".agentcode-prototype" / task_id[:8]
    prompt = tmpl_render("prototype-gen", task_id=task_id[:8],
                         requirements_content=req, design_content=design,
                         prototype_dir=str(staging),
                         project_context=_project_context())
    error = ""
    try:
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        r = w.run_codex(prompt, str(repo), model=model, codex_home=str(codex_home),
                        log_dir=str(RIVER_WS / task_id / "logs"))
        meter.record_usage_safe(db, buffer_path=USAGE_BUFFER, task_id=task_id,
                                agent_role="prototype", executor="codex", model=model,
                                output_text=r.output, node_run_id=run)
        for name in ("index.html", "PROTOTYPE.md"):
            src = staging / name
            if src.is_file():
                shutil.move(str(src), str(proto_dir / name))
    except Exception as exc:  # 异常必须收尾，避免孤儿 running（P2-0 教训）
        error = str(exc)[:500]
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    if error:
        ledger.record_gate_result(db, task_id, "prototype_exec", False, error, node_run_id=run)
        if _close(db, run, "failed", worker_id, epoch):
            ledger.set_task_status(db, task_id, "gate_failed", node="prototype")
        return {"prototype_dir": ""}
    if not _prototype_gate(db, task_id, run, proto_dir, worker_id=worker_id, epoch=epoch):
        return {"prototype_dir": ""}
    if not _close(db, run, "done", worker_id, epoch):
        return {"prototype_dir": ""}
    ledger.set_task_status(db, task_id, "running", node="prototype")
    return {"prototype_dir": str(proto_dir)}


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
                         post_check=lambda text: _ac_coverage(text, content),
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
        ledger.set_task_status(db, state["task_id"], "running")
        return Command(goto="implement", update={"approved_g2": True, "rejection_reason": ""})
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


def _lanes_from_design(design_path: Path, requirements_path: Path | None,
                       task_id: str) -> list[dict]:
    """从 DESIGN.md 的「文件归属表」推导 lanes（intake 任务无需手写任务脚本）。

    表格形态：| 路径 | lane |。解析失败/无数据行返回 []，由调用侧回退默认 lanes。
    """
    if not design_path.is_file():
        return []
    rows: dict[str, list[str]] = {}
    for line in design_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip().strip("`") for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        path, lane = cells[0], cells[1]
        if not path or not lane or set(path) <= set("-: ") or lane.lower() == "lane":
            continue  # 表头/分隔行
        rows.setdefault(lane, []).append(path)
    if not rows:
        return []
    excerpt = ""
    if requirements_path and requirements_path.is_file():
        excerpt = requirements_path.read_text(encoding="utf-8")[:3000]
    lanes = []
    for lane, paths in rows.items():
        lanes.append({
            "name": lane,
            "node": f"implement_{lane}",
            "branch": f"feat/{task_id[:8]}-{lane}",
            "scope": paths,
            "lane_instruction": (
                f"按 DESIGN.md 文件归属表实现 {lane} lane 所属文件，满足下列需求与验收标准：\n\n"
                + excerpt
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


def implement_node(state: DeliveryState) -> dict:
    """实现节点（Worker 化 · P1 Week 2）：双 lane 串行（be → fe，N=1 语义）。

    每 lane：分支创建 → node_run → codex 实现 → 编排层门禁 → 失败修正循环(1次)
    → git commit（幂等键）→ 计量入账 → finish。
    门禁在编排层执行（AC-06 / C-001：Worker 沙箱内 vitest EPERM）。
    """
    import worker as w

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
    task_dir = RIVER_WS / task_id / "docs"
    # lanes 优先级：显式 state.lanes（旧任务脚本）→ DESIGN.md 归属表（intake 任务）→ 默认占位
    lanes = state.get("lanes") or _lanes_from_design(
        task_dir / "DESIGN.md", task_dir / "REQUIREMENTS.md", task_id) or [
        {"name": "be", "node": "implement_api", "branch": "feat/task-002-uptime-be",
         "scope": ["apps/api/src/**"], "prompt": "(default lane: 请在任务脚本中用 lane_instruction 走模板或用 prompt 显式覆盖)"},
        {"name": "fe", "node": "implement_web", "branch": "feat/task-002-uptime-fe",
         "scope": ["apps/web/src/**"], "prompt": "(default lane: 请在任务脚本中用 lane_instruction 走模板或用 prompt 显式覆盖)"},
    ]
    summary = []
    prev_branch = base  # C-006 修复：串行 lane 分支链——后继 lane 继承前驱 lane 的分支
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
    for idx, lane in enumerate(lanes):
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
        if feedback:
            prompt = f"{prompt}\n\n## 上轮审查未通过（必须逐条修正）\n{feedback}"
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
        r = w.run_codex(prompt, workdir, model=model,
                        codex_home=str(codex_home), log_dir=worker_logs)
        meter.record_usage_safe(db, buffer_path=USAGE_BUFFER, task_id=task_id,
                                agent_role=lane["node"], executor="codex", model=model,
                                output_text=r.output, node_run_id=run)
        # C-007 防回归：Worker 空交付检测——无改动时门禁会在旧代码上假阳性全绿。
        # 证据：task-004 隔离 home 缺信任配置 → codex 只读空跑 → 门禁全绿 → 空提交被吞。
        # 注意：不能对整体输出 strip()——首行 " M path" 的前导空格是状态列，去掉后
        # 路径解析会左移一位（真实任务 066eb5ec 曾把 apps/... 解析成 pps/...）。
        # 多项目：不硬编码 apps/（river 结构），改为全仓库检测；越界由下方 _out_of_scope 拦截。
        dirty = (w.git(repo, "status", "--porcelain")[1] or "").rstrip()
        if not dirty.strip():
            ledger.set_lane_status(db, task_id, lane["name"], "failed")
            ledger.record_gate_result(db, task_id, "worker_no_changes", False,
                                       f"Worker 未产生任何工作区改动。codex 报告尾部：{r.report[-300:]}",
                                       node_run_id=run)
            if _close(db, run, "failed", worker_id, epoch):
                ledger.set_task_status(db, task_id, "gate_failed", node=lane["node"])
            return {"implement_summary": f"lane {lane['name']} FAILED: Worker 空交付（无工作区改动）"}
        # 范围硬校验（A 层）：改动必须落在 lane scope 内，越界不得进入门禁/提交。
        # 背景：此前只看 apps 下是否有改动，fe lane 改 api 文件同样放行（契约形同建议）。
        changed = _changed_files(dirty)
        # G-02 守卫：cwd 已是 lane 根却按仓库相对路径写入 → 产生 prefix/prefix/ 嵌套目录。
        # 这类路径仍匹配 lane scope，_out_of_scope 放行，故必须单独拦截。
        nested = _lane_prefix_violations(changed, wd_prefix)
        if nested:
            ledger.set_lane_status(db, task_id, lane["name"], "failed")
            ledger.record_gate_result(
                db, task_id, "lane_workdir_prefix", False,
                f"工作目录已切到 lane 根 {wd_prefix}/，但出现嵌套路径：{', '.join(nested[:10])}",
                node_run_id=run)
            if _close(db, run, "failed", worker_id, epoch):
                ledger.set_task_status(db, task_id, "gate_failed", node=lane["node"])
            return {"implement_summary": f"lane {lane['name']} FAILED: 嵌套前缀路径 {nested[:3]}"}
        outside = _out_of_scope(changed, [str(s) for s in (lane.get("scope") or [])])
        if outside:
            ledger.set_lane_status(db, task_id, lane["name"], "failed")
            ledger.record_gate_result(
                db, task_id, "lane_scope", False,
                f"越界文件：{', '.join(outside)}；lane scope={lane.get('scope')}",
                node_run_id=run)
            if _close(db, run, "failed", worker_id, epoch):
                ledger.set_task_status(db, task_id, "gate_failed", node=lane["node"])
            return {"implement_summary": f"lane {lane['name']} FAILED: 越界改动 {outside}"}
        # 门禁（编排层）+ rules.yaml 配置的修正循环；命令读 project.yaml tech_stack.gate_commands
        fix_attempts = max(0, _rule_int("implement", "gate_fix_attempts"))
        for gate_cmd, gate_name in _implement_gate_specs(_project_config(), lane["name"]):
            passed, out = w.run_gate(gate_cmd, repo)
            for _ in range(fix_attempts):
                if passed:
                    break
                # 修正循环：失败输出丢回 Worker（V2.2 §9.2 attempt 自愈）
                fix_prompt = tmpl_render(
                    "gate-fix",
                    gate_cmd=gate_cmd,
                    gate_output_tail=out[-1200:],
                    failed_files=_changed_files(
                        w.git(repo, "status", "--porcelain")[1])
                    or ["（工作区无改动）"],
                    attempt=_node_attempt(db, task_id, lane["node"]),
                    project_context=_project_context(),
                    workdir_hint=_workdir_hint(repo, workdir, wd_prefix),
                )
                r2 = w.run_codex(fix_prompt, workdir, model=model,
                                 codex_home=str(codex_home), log_dir=worker_logs)
                meter.record_usage_safe(db, buffer_path=USAGE_BUFFER, task_id=task_id,
                                        agent_role=lane["node"], executor="codex", model=model,
                                        output_text=r2.output, node_run_id=run)
                passed, out = w.run_gate(gate_cmd, repo)
            ledger.record_gate_result(db, task_id, gate_name, passed, out, node_run_id=run)
            if not passed:
                ledger.set_lane_status(db, task_id, lane["name"], "failed")
                if _close(db, run, "failed", worker_id, epoch):
                    ledger.set_task_status(db, task_id, "gate_failed", node=lane["node"])
                return {"implement_summary": f"lane {lane['name']} gate {gate_name} FAILED"}
        # 副作用：commit（幂等键去重）；返回值必须检查——空提交/失败不得登记 done（C-007）
        # 多项目：不限定 apps/，越界已在上面拦截；.gitignore 负责排除构建产物。
        w.git(repo, "add", "-A")
        crc, cout = w.git(repo, "commit", "-m", lane.get("commit_msg", f"feat({lane['name']}): {task_id[:8]}"))
        if crc != 0:
            ledger.set_lane_status(db, task_id, lane["name"], "failed")
            ledger.record_gate_result(db, task_id, "git_commit", False, cout[-500:], node_run_id=run)
            if _close(db, run, "failed", worker_id, epoch):
                ledger.set_task_status(db, task_id, "gate_failed", node=lane["node"])
            return {"implement_summary": f"lane {lane['name']} FAILED: git commit rc={crc}"}
        try:  # B1 栅栏：epoch 已失效则副作用与收尾一并拒写
            ledger.record_operation(
                db, task_id, f"{task_id}:{lane['node']}:git_commit:{lane['name']}",
                "git_commit", lane["branch"], node_run_id=run,
                worker_id=worker_id, lease_epoch=epoch)
        except ledger.StaleLeaseError as exc:
            print(f"[lease] stale operation rejected: {exc}")
            return {"implement_summary": f"lane {lane['name']} 租约失效：副作用被拒（可能已被接管）"}
        if not _close(db, run, "done", worker_id, epoch):
            return {"implement_summary": f"lane {lane['name']} 租约失效：完成写入被拒（可能已被接管）"}
        ledger.set_lane_status(db, task_id, lane["name"], "done")
        summary.append(f"{lane['name']}:done")
    if lanes:  # 末 lane 无后继，收尾即视为已并入交付分支
        ledger.mark_lane_merged(db, task_id, lanes[-1]["name"])
    return {"implement_summary": "; ".join(summary), "review_feedback": ""}


def _project_config() -> dict:
    """读取项目配置（RIVER_WS/project.yaml）；缺失/损坏按空配置处理（由调用方显式留痕）。"""
    path = RIVER_WS / "project.yaml"
    if not path.is_file():
        return {}
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        print(f"[config] project.yaml 解析失败：{exc}")
        return {}


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
    payload: dict = {"task_id": task_id, "review_rework_count": 0}
    for key in _RERUN_RESET_KEYS[entry]:
        payload[key] = False if key == "deploy_skipped" else ""
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
    commands = dict(((config.get("tech_stack") or {}).get("gate_commands") or {}))
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


def _read_doc(docs: Path, name: str) -> str:
    path = docs / name
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def _delivery_diff(repo: str, base: str) -> tuple[list[str], str, str]:
    """收集相对 base 的交付 diff：返回 (变更文件, stat, 截断后的 diff 正文)。

    implement 结束后 HEAD 即末 lane 分支（lane 串行 checkout -B 链），故
    base..HEAD 覆盖全部 lane 改动。git 失败/分支不存在时返回空（由调用方留痕）。
    """
    import worker as w

    _, names = w.git(repo, "diff", "--name-only", f"{base}..HEAD")
    _, stat = w.git(repo, "diff", "--stat", f"{base}..HEAD")
    _, body = w.git(repo, "diff", f"{base}..HEAD")
    files = [line.strip() for line in (names or "").splitlines() if line.strip()]
    body = body or ""
    diff_max = _rule_int("review", "diff_max")
    diff = body if len(body) <= diff_max else body[:diff_max] + "\n...（diff 已截断）"
    return files, (stat or "").strip(), diff


def _changed_files_content(repo: str, changed: list[str], max_chars: int) -> str:
    """读取变更文件的当前完整内容，供审查 Agent 核验 diff 未覆盖的上下文行。

    每个文件用 ```<path>``` 围栏包裹；总长度不超过 max_chars（与 diff 共用预算），
    超出时截断并标注。二进制/读取失败的文件跳过。
    """
    if not changed:
        return "（无变更文件）"
    parts: list[str] = []
    total = 0
    repo_path = Path(repo)
    for rel in changed:
        fp = repo_path / rel
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        header = f"```{rel}\n"
        footer = "\n```\n"
        chunk = header + text + footer
        if total + len(chunk) > max_chars:
            room = max(0, max_chars - total - len(header) - len(footer) - 20)
            if room > 0:
                parts.append(header + text[:room] + "\n...（文件内容已截断）" + footer)
            break
        parts.append(chunk)
        total += len(chunk)
    return "\n".join(parts) if parts else "（变更文件均无法读取）"


def _implement_gate_results(db, task_id: str) -> str:
    """取 implement 节点最近一轮的门禁结果，供审查 Agent 核验「门禁通过」类 AC。

    test 节点在 review 之后运行，review 时只能依赖 implement 阶段的门禁记录。
    按 gate_name 去重保留最新一条，输出为 Markdown 表格 + 失败输出尾部。
    """
    rows = db.execute(
        "SELECT gr.gate_name, gr.passed, gr.output, gr.checked_at "
        "FROM gate_results gr "
        "JOIN node_runs nr ON gr.node_run_id = nr.id "
        "WHERE nr.task_id=? AND nr.node_name LIKE 'implement_%' "
        "ORDER BY gr.id DESC", (task_id,)).fetchall()
    if not rows:
        return "（implement 阶段无门禁记录）"
    # 按 gate_name 去重，保留最新
    latest: dict[str, dict] = {}
    for r in rows:
        if r["gate_name"] not in latest:
            latest[r["gate_name"]] = r
    lines = ["| 门禁 | 结果 | 检查时间 |", "| --- | --- | --- |"]
    failures = []
    for name in sorted(latest):
        r = latest[name]
        ok = "PASS" if r["passed"] else "FAIL"
        lines.append(f"| {name} | {ok} | {r['checked_at']} |")
        if not r["passed"]:
            failures.append((name, r["output"]))
    if failures:
        lines += ["", "### 失败输出"]
        for name, out in failures:
            lines += [f"#### {name}", "```text", (out or "")[-800:], "```"]
    return "\n".join(lines)


def _review_feedback(checks: list[tuple], agent_doc: str, limit: int = 2_000) -> str:
    """把审查失败原因压成下一轮 implement 的修正清单（Agent 问题/建议优先）。"""
    parts: list[str] = []
    problems = _section(agent_doc, "## 问题").strip() if agent_doc else ""
    suggestions = _section(agent_doc, "## 建议").strip() if agent_doc else ""
    if problems and problems != "无":
        parts.append("问题：\n" + problems)
    if suggestions and suggestions != "无":
        parts.append("建议：\n" + suggestions)
    failed = [f"- {name}: {reason}" for name, ok, reason in checks if not ok]
    if failed:
        parts.append("机械核对失败：\n" + "\n".join(failed))
    return "\n\n".join(parts)[:limit]


def review_node(state: DeliveryState) -> dict:
    """自动审查（C 层）：implement 全部成功后、test 之前。

    确定性门禁（零成本）：提交清单 / lane scope 覆盖 / AC 映射齐全；
    project.yaml `review.codex: true` 时追加 codex 审查，FAIL 一并判 gate_failed。
    结论写入 docs/REVIEW.md，供 G3 与复盘引用。
    """
    import worker as w

    db = _ledger()
    task_id = state["task_id"]
    status = db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()["status"]
    if status in ("awaiting_human", "gate_failed", "failed", "done"):
        return {"review_report": ""}  # 上游未成功，审查不得启动（也不得掩盖失败）

    config = _project_config()
    docs = RIVER_WS / task_id / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    report_path = docs / "REVIEW.md"
    attempt = _node_attempt(db, task_id, "review")
    try:
        run, worker_id, epoch, codex_home = _start_leased_run(
            db, task_id, "review", attempt=attempt,
            output_path=str(report_path))
    except PermissionError as exc:  # AC-13：预算 PAUSED 时拒发新节点
        ledger.record_gate_result(db, task_id, "budget_paused", False, str(exc))
        ledger.set_task_status(db, task_id, "awaiting_human", node="review")
        return {"review_report": ""}

    requirements = _read_doc(docs, "REQUIREMENTS.md")
    design = _read_doc(docs, "DESIGN.md")
    base = state.get("base_branch") or "main"
    changed, stat, diff = _delivery_diff(_repo_path(), base)
    commits = [r["operation_key"] for r in db.execute(
        "SELECT operation_key FROM operations WHERE task_id=? AND op_type='git_commit' "
        "ORDER BY id", (task_id,))]
    lanes = state.get("lanes") or _lanes_from_design(
        docs / "DESIGN.md", docs / "REQUIREMENTS.md", task_id)
    scope = [str(p) for lane in lanes for p in (lane.get("scope") or [])]

    # 变更文件的当前完整内容：diff 只展示改动行，AC 可能依赖未改动的上下文
    # （例如输入框 type 本就正确、无需改动），把完整文件提供给审查 Agent 核验。
    files_content = _changed_files_content(_repo_path(), changed,
                                           _rule_int("review", "diff_max"))

    # implement 节点已跑过门禁（typecheck/unit 等），把结果交给审查 Agent 核验
    # AC 中「门禁通过」类要求；test 节点在 review 之后，此处只能取 implement 的门禁记录。
    gate_results = _implement_gate_results(db, task_id)

    checks: list[tuple[str, bool, str]] = []
    checks.append(("commits", bool(commits),
                   f"{len(commits)} 次提交" if commits else "无 git_commit 记录"))
    # 与 implement 的 lane_scope 口径一致：只核对工程目录内的文件（apps/ 等），
    # 根级锁文件/配置不在 lane 归属表内，不因缺失 scope 而误判越界。
    tech = config.get("tech_stack") or {}
    roots = {str(p).split("/")[0] for p in (tech.get("web"), tech.get("api")) if p} or {"apps"}
    scoped = [f for f in changed if f.split("/")[0] in roots]
    outside = _out_of_scope(scoped, scope) if scoped else []
    if not changed:
        checks.append(("lane_scope", True, "diff 为空，跳过范围核对（分支未合并或 git 桩）"))
    elif outside:
        checks.append(("lane_scope", False, f"越界文件：{', '.join(outside)}"))
    else:
        checks.append(("lane_scope", True, f"{len(scoped)} 个工程文件均在 scope 内"))
    if requirements and design:
        ac_ok, ac_reason = _ac_coverage(design, requirements)
    else:
        ac_ok, ac_reason = False, "缺少 REQUIREMENTS.md 或 DESIGN.md，无法核对 AC 覆盖"
    checks.append(("ac_coverage", ac_ok, ac_reason))

    agent_ok, agent_reason = True, "未启用（review.codex=false）"
    agent_doc = ""
    if (config.get("review") or {}).get("codex"):
        agent_ok, agent_reason = False, "未执行"
        prompt = tmpl_render("review-diff", task_id=task_id[:8], diff_content=diff,
                             requirements_content=requirements,
                             design_content=design,
                             project_context=_project_context(),
                             files_content=files_content,
                             gate_results=gate_results)
        model = route_model(ledger.evaluate_budget(db, QUOTA)["state"], "review")
        # G-01 同类项：审查角色只读，不得改写仓库（矩阵外角色，防护一致）。
        review_repo = _repo_path()
        dirty_before = _repo_dirty(review_repo)
        try:
            r = w.run_codex(prompt, review_repo, model=model, codex_home=str(codex_home),
                            log_dir=str(RIVER_WS / task_id / "logs"), sandbox="read-only")
            meter.record_usage_safe(db, buffer_path=USAGE_BUFFER, task_id=task_id,
                                    agent_role="review", executor="codex", model=model,
                                    output_text=r.output, node_run_id=run)
            agent_doc = _extract_markdown(r.output)
            newly_dirty = sorted(set(_repo_dirty(review_repo)) - set(dirty_before))
            missing = [h for h in ("## 结论", "## 问题", "## 建议") if h not in agent_doc]
            if newly_dirty:
                agent_reason = "审查节点改动了项目仓库：" + ", ".join(newly_dirty[:10])
                if agent_doc:
                    (docs / "REVIEW-agent.md").write_text(agent_doc + "\n", encoding="utf-8")
            elif not agent_doc:
                agent_reason = "Agent 未产出可解析的 Markdown"
            elif missing:
                agent_reason = f"缺少章节：{missing}"
            else:
                m = re.search(r"\b(PASS|FAIL)\b", _section(agent_doc, "## 结论"), re.IGNORECASE)
                if not m:
                    agent_reason = "## 结论 未给出 PASS/FAIL"
                elif m.group(1).upper() == "FAIL":
                    agent_reason = "Agent 判定 FAIL"
                else:
                    agent_ok, agent_reason = True, "Agent 判定 PASS"
                (docs / "REVIEW-agent.md").write_text(agent_doc + "\n", encoding="utf-8")
        except Exception as exc:  # 审查异常不得静默放行
            agent_reason = f"审查执行异常：{str(exc)[:300]}"

    for name, ok, reason in checks:
        ledger.record_gate_result(db, task_id, f"review_{name}", ok, reason, node_run_id=run)
    ledger.record_gate_result(db, task_id, "review_agent", agent_ok, agent_reason,
                              node_run_id=run)

    all_ok = all(ok for _, ok, _ in checks) and agent_ok
    # 返工预算按本轮图状态计算，不能用全任务历史 review 数，否则老任务手动重跑后
    # 会因历史 attempt 已超过上限而永远跳过自动返工。
    rework_count = int(state.get("review_rework_count") or 0)
    if not all_ok:
        rework_count += 1
    rework_limit = max(0, _rule_int("review", "rework_attempts"))
    can_rework = (not all_ok) and rework_count <= rework_limit
    feedback = "" if all_ok else (_review_feedback(checks, agent_doc)
                                  or "审查未通过但未给出具体问题；请复核 AC 覆盖与 DESIGN 作用域后重新实现。")
    if not all_ok:
        ledger.record_gate_result(db, task_id, "review_rework", can_rework,
                                  f"本轮第 {rework_count} 次审查失败；返工上限 {rework_limit}",
                                  node_run_id=run)
    lines = [f"# REVIEW · {task_id[:8]}", "", "## 结论", "PASS" if all_ok else "FAIL", "",
             "## 机械核对", "| 检查 | 结果 | 说明 |", "| --- | --- | --- |"]
    lines += [f"| {name} | {'PASS' if ok else 'FAIL'} | {reason} |" for name, ok, reason in checks]
    lines += ["", "## 改动清单", "### 提交"]
    lines += [f"- `{c}`" for c in commits] if commits else ["- （无）"]
    lines += ["", "### 变更文件"]
    lines += ([f"- `{f}`" for f in changed] if changed
              else ["- （diff 为空：分支未合并或仓库为测试桩）"])
    if stat:
        lines += ["", "### diff stat", "```text", stat[-3000:], "```"]
    lines += ["", "## Agent 审查", agent_reason]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if all_ok:
        if not _close(db, run, "done", worker_id, epoch):
            return {"review_report": ""}
        ledger.set_task_status(db, task_id, "running", node="review")
        return {"review_report": str(report_path), "review_feedback": "", "review_rework_count": 0}
    if _close(db, run, "failed", worker_id, epoch):
        if can_rework:
            # 保持 running，条件边会回到 implement（不会终止图）
            ledger.set_task_status(db, task_id, "running", node="review")
        else:
            ledger.set_task_status(db, task_id, "gate_failed", node="review")
    return {"review_report": "", "review_feedback": feedback,
            "review_rework_count": rework_count}


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

    db = _ledger()
    task_id = state["task_id"]
    status = db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()["status"]
    if status in ("awaiting_human", "gate_failed", "failed", "done"):
        return {"test_report": ""}  # 上游未成功，测试角色不得启动（也不得掩盖失败）

    config = _project_config()
    gates = _test_gate_specs(config)
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
        return {"test_report": ""}

    results: list[tuple[str, str, bool, str]] = []
    if not gates:
        ledger.record_gate_result(db, task_id, "test_config", False,
                                  "未配置可用测试门禁（test.gates / tech_stack.gate_commands）",
                                  node_run_id=run)
    repo = _repo_path()
    for name, command in gates:
        passed, out = w.run_gate(command, repo)
        ledger.record_gate_result(db, task_id, f"test_{name}", passed, out, node_run_id=run)
        results.append((name, command, passed, out))

    commits = [r["operation_key"] for r in db.execute(
        "SELECT operation_key FROM operations WHERE task_id=? AND op_type='git_commit' "
        "ORDER BY id", (task_id,))]
    report_path.write_text(_render_test_report(task_id, results, commits), encoding="utf-8")

    all_ok = bool(gates) and all(ok for _, _, ok, _ in results)
    if all_ok:
        if not _close(db, run, "done", worker_id, epoch):
            return {"test_report": ""}
        ledger.set_task_status(db, task_id, "running", node="test")
        return {"test_report": str(report_path)}
    if _close(db, run, "failed", worker_id, epoch):
        ledger.set_task_status(db, task_id, "gate_failed", node="test")
    return {"test_report": ""}


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
    return w.run_gate(target, repo, timeout=timeout)


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
        report_path.write_text(_render_deploy_report(
            task_id, "SKIPPED · 未配置 staging.commands（required=false）", [],
            "这是显式跳过而非部署成功；staging 就绪后配置 commands 并设 required=true。"), encoding="utf-8")
        return {"deploy_report": str(report_path), "deploy_skipped": True}

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
    steps: list[tuple[str, str, bool, str]] = []
    ok = True
    for index, command in enumerate(commands, start=1):
        passed, out = w.run_gate(command, repo, timeout=timeout)
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

    report_path.write_text(_render_deploy_report(
        task_id, "PASS · staging 部署与检查通过" if ok else "FAIL · staging 部署/检查失败", steps),
        encoding="utf-8")
    if ok:
        try:  # B1 栅栏：epoch 已失效则部署副作用不登记、任务状态不落 done
            ledger.record_operation(db, task_id, f"{task_id}:deploy_staging:{attempt}",
                                    "deploy_staging", "staging", node_run_id=run,
                                    worker_id=worker_id, lease_epoch=epoch)
        except ledger.StaleLeaseError as exc:
            print(f"[lease] stale operation rejected: {exc}")
            return {"deploy_report": "", "deploy_skipped": False}
        if not _close(db, run, "done", worker_id, epoch):
            return {"deploy_report": "", "deploy_skipped": False}
        ledger.set_task_status(db, task_id, "running", node="deploy")
        return {"deploy_report": str(report_path), "deploy_skipped": False}
    if _close(db, run, "failed", worker_id, epoch):
        ledger.set_task_status(db, task_id, "gate_failed", node="deploy")
    return {"deploy_report": "", "deploy_skipped": False}


def review_g3(state: DeliveryState) -> Command:
    """人工闸门 G3：staging 验收（部署报告/环境）。驳回须带理由，回实现修复（R1），上限同 G1。"""
    decision = interrupt({"gate": "G3_staging", "artifact": state.get("deploy_report")})
    if decision == "approved":
        db = _ledger()
        ledger.set_task_status(db, state["task_id"], "running")
        return Command(goto="retro", update={"approved_g3": True, "rejection_reason": ""})
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


def _generate_retro(state: DeliveryState, db, task_id: str, run: str,
                    codex_home: Path) -> None:
    """可选：codex 生成 RETRO.md（project.yaml retro.generate=true）。

    复盘不是交付门禁：生成失败只留痕（gate retro_doc=false），不改变任务终态，
    避免一个辅助报告把已通过 G3 的交付判成失败。
    """
    import worker as w

    docs = RIVER_WS / task_id / "docs"
    changed, stat, _ = _delivery_diff(_repo_path(), state.get("base_branch") or "main")
    change_summary = ("\n".join(f"- {f}" for f in changed) if changed else "（未取到 diff）")
    if stat:
        change_summary += f"\n\n```text\n{stat[-2000:]}\n```"
    prompt = tmpl_render(
        "retro-gen", task_id=task_id[:8],
        requirements_content=_read_doc(docs, "REQUIREMENTS.md") or "（未生成）",
        design_content=_read_doc(docs, "DESIGN.md") or "（未生成）",
        test_report=_read_doc(docs, "TEST_REPORT.md") or "（未生成）",
        deploy_report=_read_doc(docs, "DEPLOY_REPORT.md") or "（未生成）",
        change_summary=change_summary,
        project_context=_project_context())
    model = route_model(ledger.evaluate_budget(db, QUOTA)["state"], "retro")
    try:
        r = w.run_codex(prompt, _repo_path(), model=model, codex_home=str(codex_home),
                        log_dir=str(RIVER_WS / task_id / "logs"), sandbox="read-only")
        meter.record_usage_safe(db, buffer_path=USAGE_BUFFER, task_id=task_id,
                                agent_role="retro", executor="codex", model=model,
                                output_text=r.output, node_run_id=run)
        doc = _extract_markdown(r.output)
        missing = [h for h in ("## 结果", "## 偏差", "## 经验") if h not in doc]
        if not doc:
            ledger.record_gate_result(db, task_id, "retro_doc", False,
                                      "Agent 未产出可解析的 Markdown", node_run_id=run)
        elif missing:
            ledger.record_gate_result(db, task_id, "retro_doc", False,
                                      f"缺少必需章节：{missing}", node_run_id=run)
        else:
            docs.mkdir(parents=True, exist_ok=True)
            (docs / "RETRO.md").write_text(doc + "\n", encoding="utf-8")
            ledger.record_gate_result(db, task_id, "retro_doc", True,
                                      f"{len(doc)} 字符", node_run_id=run)
    except Exception as exc:
        ledger.record_gate_result(db, task_id, "retro_doc", False,
                                  f"复盘生成异常：{str(exc)[:300]}", node_run_id=run)


def retro_node(state: DeliveryState) -> dict:
    db = _ledger()
    # 预算暂停/单任务超限时 implement 会转 awaiting_human 并提前返回：
    # 此时不得由 retro 覆盖成 done（AC-13 ⑤ 的「转人审」必须可观察）。
    status = db.execute("SELECT status FROM tasks WHERE id=?", (state["task_id"],)).fetchone()["status"]
    if status in ("awaiting_human", "gate_failed", "failed"):
        # C-007 扩展：gate_failed/failed 同样不得被 retro 覆盖成 done（终态失真会掩盖交付失败）
        return {}
    run, worker_id, epoch, codex_home = _start_leased_run(db, state["task_id"], "retro")
    if (_project_config().get("retro") or {}).get("generate"):
        _generate_retro(state, db, state["task_id"], run, codex_home)
    if _close(db, run, "done", worker_id, epoch):
        ledger.set_task_status(db, state["task_id"], "done", node="retro")
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
