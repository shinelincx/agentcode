"""节点规则外置（`<project>/rules.yaml`）：编排层判定口径。

与 project.yaml 的分工：project.yaml 管环境/凭据/门禁命令，rules.yaml 管
「产物多长算合格、原型多大算合格、界面关键词、门禁修正次数」这类纯判定参数。
改阈值不必改代码；文件或键缺失一律回退 DEFAULTS，保证未配置项目行为不变。
"""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

DEFAULTS: dict = {
    "doc": {"min_len": 80, "max_len": 50_000},
    "prototype": {
        "md_min": 80,
        "html_min": 200,
        "html_max": 300_000,
        "ui_keywords": ["界面", "页面", "首页", "按钮", "样式", "布局", "组件",
                        "交互", "前端", "弹窗", "表单", "导航"],
    },
    "implement": {"gate_fix_attempts": 1},
    "review": {"diff_max": 20_000, "rework_attempts": 1},
    "memory": {"context_max": 1_500},
}

# (path, mtime, rules) —— 规则文件按下发到每个节点读取，缓存避免反复解析 YAML
_cache: dict[str, tuple[float, dict]] = {}


def load(project_dir: Path) -> dict:
    """读取项目规则；缺文件/解析失败/未知键均不抛错（回退默认值）。"""
    path = Path(project_dir) / "rules.yaml"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return copy.deepcopy(DEFAULTS)
    cached = _cache.get(str(path))
    if cached and cached[0] == mtime:
        return copy.deepcopy(cached[1])

    merged = copy.deepcopy(DEFAULTS)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # 规则文件损坏不得阻断交付，按默认口径继续
        print(f"[rules] rules.yaml 解析失败，使用默认值：{exc}")
        data = {}
    if isinstance(data, dict):
        for section, values in data.items():
            if isinstance(values, dict) and isinstance(merged.get(section), dict):
                merged[section].update(
                    {key: value for key, value in values.items() if key in merged[section]})
    _cache[str(path)] = (mtime, copy.deepcopy(merged))
    return merged


def int_rule(project_dir: Path, section: str, key: str) -> int:
    """取整型规则；类型错误回退默认值（不让坏配置把节点打成异常）。"""
    value = (load(project_dir).get(section) or {}).get(key)
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(DEFAULTS[section][key])
