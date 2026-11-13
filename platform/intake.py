"""结构化需求录入（A2 intake）· 校验 + 归一化 + 落库

两个入口：
    看板写模式  POST /api/intake（dashboard.py --intake）
    CLI        python platform/intake.py create payload.json [--db ...] [--ws ...]

存储约定：
    tasks.requirement     = 归一化后的需求文本（看板任务表可读；含参考图片的相对路径）
    <ws>/<task_id>/requirement.json = 结构化全量（原始字段 + 附件元数据 + task_id + created_at）
    <ws>/<task_id>/attachments/     = 参考图片正本（PNG/JPEG，需求节点以 `codex -i` 送入模型）

失败语义：校验不通过抛 IntakeError，绝不写入半成品任务（与前序 C-005/C-007 同族防线）。
"""
from __future__ import annotations

import base64
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ledger

PLATFORM = Path(__file__).parent
DEFAULT_WS = PLATFORM.parent / "projects" / "river"
DEFAULT_DB = DEFAULT_WS / "state.db"

MAX_TITLE = 120
MAX_GOAL = 2000
MAX_AC = 12
MAX_AC_LEN = 500
MAX_NON_GOALS = 2000
MAX_PATHS_PER_LANE = 20
MAX_PATH_LEN = 200
LANES = ("be", "fe")

# 参考图片：仅收 PNG/JPEG（codex -i 直接支持的格式），单张 ≤2MB、最多 4 张
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 2 * 1024 * 1024
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg"}
IMAGE_MAGIC = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"))


class IntakeError(ValueError):
    """表单校验失败（HTTP 400 / CLI 退出码 2）。"""


def _text(payload: dict, key: str, *, required: bool, limit: int, label: str) -> str:
    raw = payload.get(key)
    if raw is None:
        raw = ""
    if not isinstance(raw, str):
        raise IntakeError(f"{label} 必须是文本")
    value = raw.strip()
    if required and not value:
        raise IntakeError(f"{label} 不能为空")
    if len(value) > limit:
        raise IntakeError(f"{label} 超过 {limit} 字符（当前 {len(value)}）")
    return value


def _safe_image_name(raw: object, mime: str, index: int) -> str:
    """附件文件名：仅保留安全字符，去掉路径部分，强制使用声明的扩展名。"""
    base = Path(str(raw or "")).name
    stem = re.sub(r"[^0-9A-Za-z._-]+", "_", base).strip("._")[:60]
    if not stem:
        stem = f"image-{index}{IMAGE_TYPES[mime]}"
    if not stem.lower().endswith(IMAGE_TYPES[mime]):
        stem += IMAGE_TYPES[mime]
    return stem


def _images(payload: dict) -> list[dict]:
    """校验参考图片：类型/体积/文件头/数量；返回含原始字节与相对路径的列表。"""
    raw = payload.get("images")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise IntakeError("图片必须是列表")
    if len(raw) > MAX_IMAGES:
        raise IntakeError(f"最多 {MAX_IMAGES} 张图片（当前 {len(raw)}）")
    images = []
    for i, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise IntakeError(f"第 {i} 张图片必须是对象")
        mime = str(item.get("type") or "").strip().lower()
        if mime not in IMAGE_TYPES:
            raise IntakeError(f"第 {i} 张图片类型不支持（仅 image/png、image/jpeg）")
        data = item.get("data")
        if not isinstance(data, str) or not data.strip():
            raise IntakeError(f"第 {i} 张图片缺少 base64 内容")
        if data.startswith("data:"):  # 兼容 dataURL
            data = data.split(",", 1)[-1]
        try:
            blob = base64.b64decode(data, validate=True)
        except Exception:
            raise IntakeError(f"第 {i} 张图片 base64 解码失败")
        if not blob:
            raise IntakeError(f"第 {i} 张图片内容为空")
        if len(blob) > MAX_IMAGE_BYTES:
            raise IntakeError(f"第 {i} 张图片超过 {MAX_IMAGE_BYTES // (1024 * 1024)}MB"
                              f"（当前 {len(blob)} 字节）")
        actual = next((m for sig, m in IMAGE_MAGIC if blob.startswith(sig)), "")
        if actual != mime:
            raise IntakeError(f"第 {i} 张图片内容与声明类型不符"
                              f"（声明 {mime}，实际 {actual or '未知格式'}）")
        name = _safe_image_name(item.get("name"), mime, i)
        images.append({"name": name, "type": mime, "size": len(blob), "data": blob,
                       "rel": f"attachments/{i}-{name}"})
    return images


def validate_payload(payload: dict) -> dict:
    """校验并归一化表单字段；返回干净副本（未知字段丢弃）。"""
    if not isinstance(payload, dict):
        raise IntakeError("请求体必须是 JSON 对象")

    title = _text(payload, "title", required=True, limit=MAX_TITLE, label="标题")
    goal = _text(payload, "goal", required=True, limit=MAX_GOAL, label="目标")

    raw_ac = payload.get("acceptance_criteria")
    if isinstance(raw_ac, str):
        raw_ac = raw_ac.splitlines()
    if not isinstance(raw_ac, list):
        raise IntakeError("验收标准必须是列表或按行文本")
    ac = [str(item).strip() for item in raw_ac if str(item).strip()]
    if not ac:
        raise IntakeError("验收标准至少需要 1 条")
    if len(ac) > MAX_AC:
        raise IntakeError(f"验收标准最多 {MAX_AC} 条（当前 {len(ac)}）")
    for item in ac:
        if len(item) > MAX_AC_LEN:
            raise IntakeError(f"单条验收标准超过 {MAX_AC_LEN} 字符：{item[:40]}…")

    non_goals = _text(payload, "non_goals", required=False, limit=MAX_NON_GOALS, label="非目标")

    raw_scope = payload.get("scope") or {}
    if not isinstance(raw_scope, dict):
        raise IntakeError("涉及范围必须是 {lane: [路径]} 对象")
    scope: dict[str, list[str]] = {}
    for lane, paths in raw_scope.items():
        if lane not in LANES:
            raise IntakeError(f"未知 lane {lane!r}（仅支持 {LANES}）")
        if isinstance(paths, str):
            paths = [p.strip() for p in paths.split(",") if p.strip()]
        if not isinstance(paths, list):
            raise IntakeError(f"lane {lane} 的路径必须是列表或逗号分隔文本")
        clean = [str(p).strip() for p in paths if str(p).strip()]
        if len(clean) > MAX_PATHS_PER_LANE:
            raise IntakeError(f"lane {lane} 路径最多 {MAX_PATHS_PER_LANE} 条")
        for p in clean:
            if len(p) > MAX_PATH_LEN:
                raise IntakeError(f"路径过长（>{MAX_PATH_LEN}）：{p[:40]}…")
        if clean:
            scope[lane] = clean

    priority = (payload.get("priority") or "normal").strip()
    if priority not in ledger.PRIORITIES:
        raise IntakeError(f"优先级须为 {ledger.PRIORITIES} 之一，收到 {priority!r}")

    return {"title": title, "goal": goal, "acceptance_criteria": ac,
            "non_goals": non_goals, "scope": scope, "priority": priority,
            "images": _images(payload)}


def normalize(payload: dict) -> str:
    """结构化字段 → 归一化需求文本（喂给 requirements-gen 模板 / 存 tasks.requirement）。"""
    lines = [f"# {payload['title']}", "", "## 目标", payload["goal"], "", "## 验收标准"]
    lines += [f"- {item}" for item in payload["acceptance_criteria"]]
    if payload["non_goals"]:
        lines += ["", "## 非目标", payload["non_goals"]]
    if payload["scope"]:
        lines += ["", "## 涉及范围"]
        for lane, paths in payload["scope"].items():
            lines.append(f"- {lane}: " + ", ".join(paths))
    if payload.get("images"):
        lines += ["", "## 参考图片",
                  "以下图片随任务目录存放，需求节点会作为视觉参考一并送入模型："]
        lines += [f"- {img['rel']}（{img['type']}，{img['size']} 字节）"
                  for img in payload["images"]]
    return "\n".join(lines)


def create_task_from_payload(db_path: str | Path, ws: str | Path, payload: dict) -> dict:
    """校验 → 建任务 → 落附件 → 写 requirement.json。返回 {task_id, requirement, path, priority}。"""
    clean = validate_payload(payload)
    images = clean.pop("images", [])
    text = normalize({**clean, "images": images})
    db = ledger.init_db(db_path)  # 幂等建表/迁移，避免半成品库
    try:
        task_id = ledger.create_task(db, text, clean["priority"])
    finally:
        db.close()

    task_dir = Path(ws) / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    attachments = []
    for img in images:
        dest = Path(ws) / task_id / img["rel"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(img["data"])
        attachments.append({k: img[k] for k in ("name", "type", "size", "rel")})
    record = {**clean, "attachments": attachments, "task_id": task_id,
              "requirement": text, "created_at": datetime.now(timezone.utc).isoformat()}
    path = task_dir / "requirement.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"task_id": task_id, "requirement": text, "path": str(path),
            "priority": clean["priority"], "attachments": attachments}


def _cli(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] != "create":
        print(__doc__)
        return 1
    payload_path = Path(argv[1])
    db_path, ws = DEFAULT_DB, DEFAULT_WS
    if "--db" in argv:
        db_path = Path(argv[argv.index("--db") + 1])
    if "--ws" in argv:
        ws = Path(argv[argv.index("--ws") + 1])
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        result = create_task_from_payload(db_path, ws, payload)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"读取/解析失败：{exc}", file=sys.stderr)
        return 2
    except IntakeError as exc:
        print(f"校验失败：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
