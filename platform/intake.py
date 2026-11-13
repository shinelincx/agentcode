"""结构化需求录入（A2 intake）· 校验 + 归一化 + 落库

两个入口：
    看板写模式  POST /api/intake（dashboard.py --intake）
    CLI        python platform/intake.py create payload.json [--db ...] [--ws ...]

存储约定：
    tasks.requirement     = 归一化后的需求文本（看板任务表可读）
    <ws>/<task_id>/requirement.json = 结构化全量（原始字段 + task_id + created_at）

失败语义：校验不通过抛 IntakeError，绝不写入半成品任务（与前序 C-005/C-007 同族防线）。
"""
from __future__ import annotations

import json
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
            "non_goals": non_goals, "scope": scope, "priority": priority}


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
    return "\n".join(lines)


def create_task_from_payload(db_path: str | Path, ws: str | Path, payload: dict) -> dict:
    """校验 → 建任务 → 写 requirement.json。返回 {task_id, requirement, path, priority}。"""
    clean = validate_payload(payload)
    text = normalize(clean)
    db = ledger.init_db(db_path)  # 幂等建表/迁移，避免半成品库
    try:
        task_id = ledger.create_task(db, text, clean["priority"])
    finally:
        db.close()

    task_dir = Path(ws) / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    record = {**clean, "task_id": task_id, "requirement": text,
              "created_at": datetime.now(timezone.utc).isoformat()}
    path = task_dir / "requirement.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"task_id": task_id, "requirement": text, "path": str(path),
            "priority": clean["priority"]}


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
