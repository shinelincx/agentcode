"""角色配置存储：读取、校验与注释保留的定点更新（控制台配置管理）。

约束（C-016）：
- 只修改 project.yaml 中已存在的标量/序列节点；不整文件重写，中文注释与缩进原样保留。
- 未知配置项或缺失节点直接报错，要求先手工补基线（避免控制台"造配置"掩盖真实差异）。
- 写临时文件后 os.replace 原子替换；每次成功变更追加审计到 <workspace>/config-audit.jsonl。

调用方：
    platform/dashboard.py   GET /api/config（load）、POST /api/config（apply_updates）
    platform/graph.py       _project_config()/refresh_runtime_config() 消费同一份 YAML
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import yaml

KNOWN_ROLES = ("requirements", "design", "prototype", "review", "implement",
               "test", "deploy", "retro")
EXECUTORS = ("codex", "claude")
GATES = ("G1_requirements", "G2_design", "G3_staging")


class ConfigError(Exception):
    """配置读写被拒（HTTP 400）。"""


def load(path: Path) -> dict:
    """读取 project.yaml；缺失/非映射/解析失败均抛 ConfigError。"""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"配置文件不存在：{path}")
    try:
        data = yaml.safe_load(_read_text(path))
    except yaml.YAMLError as exc:
        raise ConfigError(f"project.yaml 解析失败：{exc}") from exc
    return data if isinstance(data, dict) else {}


def validate(config: dict) -> list[str]:
    """返回错误列表；空列表表示可保存。只校验已出现的字段（缺省由调用方回退默认值）。"""
    errors: list[str] = []
    if not isinstance(config, dict):
        return ["配置根节点必须是映射"]

    def _is_int(value) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)

    def _int_at(container: dict, key: str, lo: int, hi: int, label: str) -> None:
        if key not in container:
            return
        value = container[key]
        if not _is_int(value):
            errors.append(f"{label} 须为整数（当前 {value!r}）")
        elif not lo <= value <= hi:
            errors.append(f"{label} 须在 {lo}..{hi}（当前 {value}）")

    budget = config.get("budget") or {}
    _int_at(budget, "monthly_quota", 1, 10_000_000, "budget.monthly_quota")
    _int_at(budget, "max_usage_per_task", 1, 100_000, "budget.max_usage_per_task")
    _int_at(budget, "max_inflight_tasks", 1, 64, "budget.max_inflight_tasks")
    thresholds = budget.get("thresholds")
    if thresholds is not None:
        if not isinstance(thresholds, dict):
            errors.append("budget.thresholds 须为映射")
        else:
            values: dict[str, float] = {}
            for key in ("alert", "degraded", "paused"):
                value = thresholds.get(key)
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    errors.append(f"budget.thresholds.{key} 须为数值")
                else:
                    values[key] = float(value)
            if len(values) == 3 and not (0 < values["alert"] < values["degraded"]
                                         <= values["paused"] <= 1):
                errors.append(
                    "阈值须满足 0 < alert < degraded ≤ paused ≤ 1"
                    f"（当前 {values['alert']}/{values['degraded']}/{values['paused']}）")

    executor = config.get("executor") or {}
    for key in ("primary", "fallback"):
        value = executor.get(key)
        if value is not None and str(value) not in EXECUTORS:
            errors.append(f"executor.{key} 须为 {list(EXECUTORS)} 之一（当前 {value!r}）")
    home = executor.get("codex_home")
    if home:
        home_path = Path(str(home))
        if not home_path.is_dir():
            errors.append(f"executor.codex_home 目录不存在：{home}")
        elif not (home_path / "auth.json").is_file():
            errors.append(f"executor.codex_home 缺少 auth.json：{home}")

    model = config.get("model") or {}
    for key in ("default", "degraded"):
        value = model.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            errors.append(f"model.{key} 须为非空字符串")
    role_models = model.get("roles")
    if role_models is not None:
        if not isinstance(role_models, dict):
            errors.append("model.roles 须为映射（角色名 → 模型名）")
        else:
            unknown = [str(role) for role in role_models if str(role) not in KNOWN_ROLES]
            if unknown:
                errors.append(f"model.roles 含未知角色：{unknown}"
                              f"（已知 {list(KNOWN_ROLES)}）")
            for role, value in role_models.items():
                if role in KNOWN_ROLES and (not isinstance(value, str) or not value.strip()):
                    errors.append(f"model.roles.{role} 须为非空字符串")
    strong = model.get("strong_roles")
    if strong is not None:
        if not isinstance(strong, list):
            errors.append("model.strong_roles 须为列表")
        else:
            unknown = [str(role) for role in strong if str(role) not in KNOWN_ROLES]
            if unknown:
                errors.append(f"model.strong_roles 含未知角色：{unknown}"
                              f"（已知 {list(KNOWN_ROLES)}）")

    gates = config.get("gates") or {}
    for gate in GATES:
        spec = gates.get(gate)
        if spec is None:
            continue
        if not isinstance(spec, dict):
            errors.append(f"gates.{gate} 须为映射")
            continue
        if not str(spec.get("approver") or "").strip():
            errors.append(f"gates.{gate}.approver 不能为空")
        _int_at(spec, "sla_hours", 1, 720, f"gates.{gate}.sla_hours")

    commands = ((config.get("tech_stack") or {}).get("gate_commands") or {})
    test = config.get("test") or {}
    if "gates" in test:
        names = test.get("gates")
        if not isinstance(names, list) or not names:
            errors.append("test.gates 须为非空列表")
        else:
            unknown = [str(name) for name in names if str(name) not in commands]
            if unknown:
                errors.append(f"test.gates 引用未定义门禁：{unknown}"
                              f"（可用 {sorted(commands)}）")

    staging = ((config.get("deploy") or {}).get("staging") or {})
    if staging:
        cmds = staging.get("commands") or []
        if not isinstance(cmds, list):
            errors.append("deploy.staging.commands 须为列表")
        elif bool(staging.get("required", False)) and not [c for c in cmds if str(c).strip()]:
            errors.append("deploy.staging.required=true 时 commands 不能为空")
        _int_at(staging, "timeout_seconds", 30, 7200, "deploy.staging.timeout_seconds")

    repo = config.get("repo_path")
    if repo and not Path(str(repo)).is_dir():
        errors.append(f"repo_path 目录不存在：{repo}")
    return errors


def apply_updates(path: Path, updates: dict, actor: str = "console", *,
                  audit_path: Path | None = None) -> dict:
    """校验并定点写入配置；返回 {changed, config, audit}。

    只写实际变化的路径；校验失败不落盘（文件保持原样）。
    """
    path = Path(path)
    if not isinstance(updates, dict) or not updates:
        raise ConfigError("updates 须为非空对象（dotted path → 新值）")
    current = load(path)
    merged = copy.deepcopy(current)
    changes: list[dict] = []
    for dotted, value in updates.items():
        parts = [part for part in str(dotted).split(".") if part]
        if len(parts) < 2:
            raise ConfigError(f"配置路径不合法：{dotted!r}")
        old = _get(current, parts, str(dotted))
        if _unchanged(old, value):
            continue
        _set(merged, parts, value, str(dotted))
        changes.append({"path": str(dotted), "from": old, "to": value})
    if not changes:
        return {"changed": [], "config": current, "audit": None}

    errors = validate(merged)
    if errors:
        raise ConfigError("；".join(errors))
    text = _read_text(path)
    _atomic_write(path, _patch_text(text, changes))
    entry = _append_audit(audit_path or path.parent / "config-audit.jsonl",
                          path, changes, actor)
    return {"changed": [change["path"] for change in changes],
            "config": merged, "audit": entry}


# ---------------- 内部：配置读写 ----------------

def _read_text(path: Path) -> str:
    # newline="" 保留原行尾，避免把 CRLF 文件静默改成 LF
    with path.open("r", encoding="utf-8", newline="") as fh:
        return fh.read()


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _unchanged(old, new) -> bool:
    if isinstance(old, bool) != isinstance(new, bool):
        return False
    return old == new


def _get(config: dict, parts: list[str], dotted: str):
    node = config
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            raise ConfigError(f"配置项不存在：{dotted}（请先在 project.yaml 手工补基线）")
        node = node[part]
    return node


def _set(config: dict, parts: list[str], value, dotted: str) -> None:
    node = config
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            raise ConfigError(f"配置项路径无法解析：{dotted}")
        node = node[part]
    if parts[-1] not in node:
        raise ConfigError(f"配置项不存在：{dotted}（请先在 project.yaml 手工补基线）")
    node[parts[-1]] = value


def _patch_text(text: str, changes: list[dict]) -> str:
    """按 YAML 节点 mark 定点替换文本；不重排缩进、不丢注释。"""
    root = yaml.compose(text)
    if root is None:
        raise ConfigError("project.yaml 为空文档，无法定位配置项")
    spans: list[tuple[int, int, str]] = []
    for change in changes:
        node = _find_node(root, change["path"].split("."), change["path"])
        if isinstance(node, yaml.MappingNode):
            raise ConfigError(f"{change['path']} 是映射而非叶节点，控制台不支持整体编辑")
        start, end = node.start_mark.index, node.end_mark.index
        if isinstance(node, yaml.SequenceNode) and not node.flow_style:
            # 块序列的 end_mark 落在下一 token 前，含换行与缩进：回退空白以免吞掉下一行
            while end > start and text[end - 1] in " \t\r\n":
                end -= 1
        spans.append((start, end, _render(node, change["to"], change["path"])))
    spans.sort(key=lambda item: item[0], reverse=True)
    for prev, cur in zip(spans, spans[1:]):
        if cur[1] > prev[0]:
            raise ConfigError("配置项替换区间重叠，请逐项保存")
    for start, end, replacement in spans:
        text = text[:start] + replacement + text[end:]
    return text


def _find_node(root, parts: list[str], dotted: str):
    node = root
    for part in parts:
        if not isinstance(node, yaml.MappingNode):
            raise ConfigError(f"配置项路径无法解析：{dotted}")
        for key, value in node.value:
            if isinstance(key, yaml.ScalarNode) and key.value == part:
                node = value
                break
        else:
            raise ConfigError(f"配置项不存在：{dotted}（请先在 project.yaml 手工补基线）")
    return node


def _render(node, value, label: str) -> str:
    if isinstance(node, yaml.SequenceNode):
        if not isinstance(value, list):
            raise ConfigError(f"{label} 须为列表")
        items = [_scalar(item, label) for item in value]
        if node.flow_style or not items:
            return "[" + ", ".join(items) + "]"
        indent = " " * node.start_mark.column
        return ("\n" + indent).join("- " + item for item in items)
    return _scalar(value, label)


def _scalar(value, label: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        if "\n" in value or "\r" in value:
            raise ConfigError(f"{label} 不接受多行字符串")
        return "'" + value.replace("'", "''") + "'"
    raise ConfigError(f"{label} 仅支持标量及标量列表（当前 {type(value).__name__}）")


def _append_audit(target: Path, path: Path, changes: list[dict], actor: str) -> dict:
    entry = {
        "at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "actor": actor or "console",
        "file": str(path),
        "changes": changes,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry
