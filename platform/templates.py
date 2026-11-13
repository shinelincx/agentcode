"""Prompt 模板渲染器 · A1 模板层

渲染入口：render(name, **params)
数据源：_templates/manifest.yaml（模板元数据 + 参数 required/optional）
缺参策略：required 参数缺失 → KeyError（C-005 教训：静默丢失导致空跑）；
         optional 参数缺失 → 默认值（空字符串或 manifest 声明的 default）。
逃生口：调用侧在 LANES[].prompt 显式给出时跳过本模块直接使用。

版本追溯：manifest_version() 返回当前 manifest 版本字符串；
        snapshot(task_dir) 把 manifest 版本写入 task-XXX/templates.lock。
"""
from __future__ import annotations

import re
from pathlib import Path
from string import Template
from typing import Any

import yaml

_TEMPLATES_DIR = Path(__file__).parent.parent / "_templates"
_MANIFEST_PATH = _TEMPLATES_DIR / "manifest.yaml"


def _load_manifest() -> dict[str, Any]:
    """加载 manifest.yaml；YAML 错误向上抛出（模板层不静默吞配置错误）。"""
    with open(_MANIFEST_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def manifest_version() -> str:
    """返回 manifest 版本：顶层 version 优先（1.2.0 起），否则取首个模板版本。"""
    m = _load_manifest()
    if m.get("version"):
        return f"templates-{m['version']}"
    first_version = next(iter(m["templates"].values()))["version"]
    return f"templates-{first_version}"


def snapshot(task_dir: Path) -> Path:
    """把 manifest 版本 + 模板内容哈希快照写入 task-XXX/templates.lock（追溯用）。

    只记版本号无法发现「同版本内改内容」的漂移；1.1.0 起同时记录每个模板的 sha256，
    lock_drift() 据此判断在跑任务是否仍对应生成它时的 prompt。
    """
    import json
    task_dir = Path(task_dir)
    task_dir.mkdir(parents=True, exist_ok=True)
    lock_path = task_dir / "templates.lock"
    payload = {
        "snapshot_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "manifest": manifest_version(),
        "templates": {name: meta["version"]
                      for name, meta in _load_manifest()["templates"].items()},
        "hashes": _template_hashes(),
    }
    lock_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return lock_path


def _template_hashes() -> dict[str, str]:
    """当前模板文件内容的 sha256（缺失文件记空串，便于漂移时定位）。"""
    import hashlib
    out: dict[str, str] = {}
    for name, meta in _load_manifest()["templates"].items():
        path = _TEMPLATES_DIR / meta["file"]
        out[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
    return out


def lock_drift(task_dir: Path) -> dict[str, Any]:
    """比对任务模板快照与当前 manifest：版本变化或同版本内容变化均算漂移。

    返回 {ok, reason, locked_manifest, current_manifest, changed, missing}。
    回退方式：按 changed/missing 列表 `git checkout <提交> -- _templates/prompts/<file>`。
    """
    import json
    lock_path = Path(task_dir) / "templates.lock"
    current_versions = {name: meta["version"]
                        for name, meta in _load_manifest()["templates"].items()}
    current_hashes = _template_hashes()
    if not lock_path.is_file():
        return {"ok": False, "reason": "缺少 templates.lock（任务从未快照模板）",
                "locked_manifest": None, "current_manifest": manifest_version(),
                "changed": [], "missing": [], "locked_at": None}
    data = json.loads(lock_path.read_text(encoding="utf-8"))
    locked_versions = data.get("templates") or {}
    locked_hashes = data.get("hashes") or {}
    changed: list[str] = []
    missing: list[str] = []
    for name, version in locked_versions.items():
        if name not in current_versions:
            missing.append(name)
        elif current_versions[name] != version:
            changed.append(name)
        elif locked_hashes.get(name) and current_hashes.get(name) != locked_hashes[name]:
            changed.append(name)
    ok = not changed and not missing
    reason = ""
    if not ok:
        parts = []
        if changed:
            parts.append(f"内容/版本已变：{', '.join(changed)}")
        if missing:
            parts.append(f"模板已删除：{', '.join(missing)}")
        reason = "；".join(parts)
    return {"ok": ok, "reason": reason,
            "locked_manifest": data.get("manifest"),
            "current_manifest": manifest_version(),
            "changed": changed, "missing": missing,
            "locked_at": data.get("snapshot_at")}


def render(name: str, **params: Any) -> str:
    """渲染指定模板；required 参数缺失抛 KeyError，optional 参数缺失用默认值。

    Args:
        name: 模板名（必须在 manifest.yaml 的 templates 键下登记）。
        **params: 模板参数。scope 等列表类型会自动 join 为逗号分隔字符串。

    Returns:
        渲染后的完整 prompt 字符串。

    Raises:
        KeyError: required 参数缺失。
        FileNotFoundError: 模板文件不存在。
        ValueError: name 不在 manifest 里。
    """
    manifest = _load_manifest()
    templates = manifest["templates"]

    if name not in templates:
        raise ValueError(
            f"Template '{name}' not in manifest. Known: {list(templates.keys())}"
        )

    meta = templates[name]
    tmpl_path = _TEMPLATES_DIR / meta["file"]
    if not tmpl_path.exists():
        raise FileNotFoundError(f"Template file missing: {tmpl_path}")

    # 1. 参数校验：required 缺失 → KeyError
    param_defs = meta.get("params", {})
    for pname, pdef in param_defs.items():
        if pdef.get("required") and pname not in params:
            raise KeyError(
                f"Template '{name}' missing required param '{pname}': {pdef.get('desc', '')}"
            )

    # 2. optional 缺失 → 默认值（空字符串）
    for pname, pdef in param_defs.items():
        if not pdef.get("required") and pname not in params:
            params[pname] = pdef.get("default", "")

    # 3. 列表类型自动 join（scope 常传 ["apps/api/src/**", ...]）
    for k, v in params.items():
        if isinstance(v, (list, tuple)):
            params[k] = ", ".join(str(x) for x in v)

    # 4. 渲染
    tmpl_text = tmpl_path.read_text(encoding="utf-8")
    # 保留 $$ 作为字面 $ 的 escape（Template.substitute 的标准行为）
    return Template(tmpl_text).substitute(**params)
