"""模型最终消息的 JSON 契约。编排器负责确定性应用修订和发布原型。"""
from __future__ import annotations

import json


def _object(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


_STRING = {"type": "string"}
REVISION_SCHEMA = _object({
    "decision": {"type": "string", "enum": ["KEEP", "REVISE"]},
    "reason": _STRING,
    "edits": {"type": "array", "items": _object({
        "old_line": _STRING,
        "new_lines": {"type": "array", "items": _STRING},
    })},
})
PROTOTYPE_SCHEMA = _object({"index_html": _STRING, "prototype_md": _STRING})
GATE_FIX_SCHEMA = _object({
    "decision": {"type": "string", "enum": ["PATCH_READY", "NO_CHANGE", "BLOCKED"]},
    "blocker": {"type": "string", "enum": ["NONE", "G2_CONFLICT", "DEPENDENCY", "ENVIRONMENT", "EVIDENCE"]},
    "evidence": _STRING,
    "verification": _STRING,
})


def gate_fix_decision(message: str) -> dict:
    report = json.loads(message)
    fields = GATE_FIX_SCHEMA["properties"]
    if (not isinstance(report, dict) or set(report) != set(fields)
            or any(not isinstance(report[name], str) or not report[name].strip()
                   or ("enum" in field and report[name] not in field["enum"])
                   for name, field in fields.items())
            or (report["decision"] == "BLOCKED") == (report["blocker"] == "NONE")):
        raise ValueError("修正 Worker 的结构化决定无效")
    return report


def apply_revision(message: str, original: str) -> tuple[str, str, str]:
    """应用按原稿唯一行定位的编辑，逐字保留未编辑的行。"""
    response = json.loads(message)
    if not isinstance(response, dict) or set(response) != {"decision", "reason", "edits"}:
        raise ValueError("修订最终消息须包含 decision、reason 和 edits")
    decision, reason, edits = response["decision"], response["reason"], response["edits"]
    if (decision not in ("KEEP", "REVISE") or not isinstance(reason, str)
            or not reason.strip() or not isinstance(edits, list)):
        raise ValueError("修订决定、理由或 edits 格式无效")
    if decision == "KEEP" and not edits:
        return decision, reason.strip(), original
    if decision != "REVISE" or not edits:
        raise ValueError("KEEP 必须无编辑，REVISE 必须提供编辑")

    lines = original.splitlines(keepends=True)
    targets: dict[str, list[int]] = {}
    for i, line in enumerate(lines):
        targets.setdefault(line.rstrip("\r\n"), []).append(i)
    default_separator = "\r\n" if "\r\n" in original else "\n"
    replacements: dict[int, str] = {}
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"old_line", "new_lines"}:
            raise ValueError("每项编辑须包含 old_line 与 new_lines")
        old, new = edit["old_line"], edit["new_lines"]
        if not isinstance(old, str) or not old.strip() or "\n" in old or "\r" in old:
            raise ValueError("old_line 须是原文中一条非空行")
        if (not isinstance(new, list)
                or any(not isinstance(line, str) or "\n" in line or "\r" in line for line in new)):
            raise ValueError("new_lines 须为若干独立行")
        indices = targets.get(old, [])
        if len(indices) != 1 or indices[0] in replacements:
            raise ValueError("每条编辑须唯一匹配原稿的一行，且不能重复匹配")
        index = indices[0]
        line_end = lines[index][len(old):]
        replacements[index] = (line_end or default_separator).join(new) + (line_end if new else "")
    revised = "".join(replacements.get(i, line) for i, line in enumerate(lines))
    if revised == original:
        raise ValueError("REVISE 未生成实际改动")
    return decision, reason.strip(), revised


def prototype_contents(message: str) -> dict[str, str]:
    data = json.loads(message)
    if not isinstance(data, dict) or set(data) != {"index_html", "prototype_md"}:
        raise ValueError("原型最终消息须包含 index_html 和 prototype_md")
    if any(not isinstance(content, str) or not content.strip() for content in data.values()):
        raise ValueError("两个原型内容均须为非空字符串")
    return {"index.html": data["index_html"], "PROTOTYPE.md": data["prototype_md"]}
