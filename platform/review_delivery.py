"""Git and ledger evidence for the exact delivery being reviewed."""
import fnmatch
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import worker as w
import ledger


def approved_prototype(db, task, design_doc, prototype_dir, approved_design):
    """Read the same artifact bytes used to verify the G2 approval digest."""
    if not prototype_dir:
        return "", "无已登记的原型产物。"
    design, proto = Path(design_doc), Path(prototype_dir)
    contents = {}
    digest = ledger.artifact_digest([design, proto], contents=contents)
    approval = db.execute("SELECT id, artifact_hash, checkpoint_id FROM approvals WHERE task_id=? "
                          "AND gate_name='G2_design' AND decision='approved' ORDER BY id DESC LIMIT 1",
                          (task,)).fetchone()
    plan = contents.get(design, b"").decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    note = contents.get(proto / "PROTOTYPE.md")
    if not approval or approval["artifact_hash"] != digest or plan != approved_design or not note:
        raise ValueError("G2 批准的设计或原型缺失、已变更或无法绑定审批")
    citation = f"G2 审批 #{approval['id']} · 组合 SHA-256: {digest}"
    if approval["checkpoint_id"]:
        citation += f" · 检查点 {approval['checkpoint_id']}"
    if proto / "index.html" not in contents:
        run = db.execute("SELECT id, status FROM node_runs WHERE task_id=? AND node_name='prototype' "
                         "ORDER BY attempt DESC, rowid DESC LIMIT 1", (task,)).fetchone()
        if not run or run["status"] != "done" or not db.execute("SELECT 1 FROM gate_results WHERE node_run_id=? "
                                     "AND gate_name='prototype_skipped' AND passed=1", (run["id"],)).fetchone():
            raise ValueError("G2 缺少原型页面或已登记的跳过结果")
        return citation, "原型已显式跳过。"
    return citation, w.safe_gate_text(note.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n"))


def git(repo, *args):
    rc, out = w.git(repo, *args)
    if rc:
        raise ValueError(f"Git {args[0]} 取证失败：{out.strip()[-300:]}")
    return out


def revision(repo, ref):
    value = git(repo, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").strip()
    if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", value):
        raise ValueError("Git 未返回有效的提交标识")
    return value


def environment(repo, config, inputs=()):
    paths = [Path(repo) / path for path in inputs]
    try:
        files = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    except OSError:
        return ""
    payload = [repo, sys.executable, sorted(os.environ.items()), config, files]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def paths(repo, base, head):
    raw = git(repo, "diff", "--name-only", "--no-renames", "-z", base, head, "--")
    if raw and not raw.endswith("\0"):
        raise ValueError("Git 路径清单不完整")
    return list(filter(None, raw.split("\0")))


def collect(repo, delivery, lanes, budget, context_paths=()):
    if not delivery or not delivery.get("commits"):
        raise ValueError("缺少交付提交快照，请从实现节点重跑")
    base, parts = delivery["base"], delivery["commits"]
    head = parts[-1]["commit"]
    if revision(repo, "HEAD") != head or revision(repo, base) != base:
        raise ValueError("当前 HEAD 与交付快照不一致")
    parent = base
    scopes = {lane["name"]: lane.get("scope") or [] for lane in lanes}
    if set(scopes) != {item["lane"] for item in parts}:
        raise ValueError("交付没有覆盖全部批准 lane")
    for item in parts:
        commit, lane = item["commit"], item["lane"]
        if git(repo, "rev-list", "--parents", "-n", "1", commit).strip().split() != [commit, parent]:
            raise ValueError(f"交付提交链不连续：{commit}")
        owned = scopes.get(lane) or []
        for path in paths(repo, parent, commit):
            if not any(fnmatch.fnmatchcase(path, pat) for pat in owned):
                raise ValueError(f"{lane} 的提交包含未批准路径：{path}")
        parent = commit
    files = paths(repo, base, head)
    if not files:
        raise ValueError("交付 diff 为空")
    context = list(dict.fromkeys([*files, *context_paths]))
    shares = max(0, budget // len(context))
    diff, source, gaps = [], [], []
    for path in context:
        patch = ""
        if path in files:
            patch = git(repo, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", base, head, "--", f":(literal){path}")
            if not patch or "Binary files " in patch or len(patch) > shares:
                gaps.append(f"{path}：补丁缺失、二进制或超出该文件预算")
            diff.append(f"--- {path} ---\n" + (patch[:shares] or "（无补丁）"))
        rc, blob = w.git(repo, "show", f"{head}:{path}")
        if rc:
            rc_base, _ = w.git(repo, "show", f"{base}:{path}")
            if not rc_base and re.search(r"(?m)^deleted file mode ", patch):
                source.append(f"{path}：已删除，请查看 BASE")
                continue
            gaps.append(f"{path}：无法读取 HEAD 源码")
        elif "\0" in blob or "\ufffd" in blob or len(blob) > shares:
            gaps.append(f"{path}：源码为二进制、编码不可用或内容已截断")
        source.append(f"--- {path} ---\n" + (blob[:shares] if not rc and "\0" not in blob else "（源码不可用）"))
    return base, head, files, "\n".join(diff), "\n".join(source), gaps


def gates(db, task, delivery, specs):
    records = {}
    required = {}
    for item in [*delivery["commits"], *delivery.get("rechecks", [])]:
        if item in delivery["commits"]:
            proof = db.execute("SELECT payload_hash FROM operations WHERE task_id=? AND node_run_id=? "
                               "AND op_type='git_commit' AND status='done'", (task, item["run"])).fetchone()
        else:
            proof = db.execute("SELECT output AS payload_hash FROM gate_results WHERE task_id=? AND node_run_id=? "
                               "AND gate_name='review_rechecked_head' AND passed=1", (task, item["run"])).fetchone()
        if not proof or proof["payload_hash"] != item["commit"]:
            raise ValueError(f"{item['lane']} 的门禁运行无法绑定交付提交")
        required[item["lane"]] = item
    for lane, item in required.items():
        run = db.execute("SELECT status FROM node_runs WHERE id=? AND task_id=? AND lane=?",
                         (item["run"], task, lane)).fetchone()
        if not run or run["status"] != "done":
            raise ValueError(f"{lane} 缺少当前交付的成功运行")
        rows = {r["gate_name"]: r for r in db.execute(
            "SELECT gate_name, passed, output FROM gate_results WHERE task_id=? AND node_run_id=? ORDER BY id",
            (task, item["run"]))}
        for command, name in specs(lane):
            row = rows.get(f"final_{name}") if item.get("repaired") else rows.get(name)
            if not row or not row["passed"] or f"$ {w.safe_gate_text(command)}" not in row["output"].splitlines():
                raise ValueError(f"{lane} 缺少当前版本通过的门禁：{name}")
            records[f"{lane}/{name}"] = dict(item, command=command, output=row["output"][:450])
    return records


def reusable(repo, config, delivery, records, command):
    options = config.get("test") or {}
    inputs = options.get("reuse_environment_files") or []
    commits = delivery.get("commits") or []
    if not options.get("reuse_implementation_gates") or not inputs or len({r["lane"] for r in commits}) != 1:
        return False
    head = delivery["commits"][-1]["commit"]
    profile = environment(repo, config, inputs)
    return bool(profile and any(row["commit"] == head and row["command"] == command
                                and row.get("environment") == profile for row in records.values()))


_TEXT = {"type": "string"}
_LINE = {"type": "integer"}


def schema():
    from prompt_output import _object
    enum = lambda *values: {"type": "string", "enum": list(values)}
    return _object({
        "verdict": enum("PASS", "FAIL", "BLOCKED"), "reason": _TEXT,
        "criteria": {"type": "array", "items": _object({
            "ac": _TEXT, "status": enum("VERIFIED", "VIOLATED", "MISSING", "DEFERRED"),
            "source": enum("HEAD", "GATE", "TEST", "NONE"), "ref": _TEXT, "line": _LINE,
        })},
        "findings": {"type": "array", "items": _object({
            "severity": enum("high", "medium", "low"), "path": _TEXT, "side": enum("BASE", "HEAD", "MISSING"),
            "line": _LINE, "end_line": _LINE, "ac": _TEXT, "rule": _TEXT, "evidence": _TEXT, "fix": _TEXT,
        })},
    })


def validate(repo, message, base, head, files, criteria, records, downstream, lanes, gaps,
             *, approved_gates=None, approved_tests=None):
    report = json.loads(message)
    def shape(value, spec):
        kind = spec["type"]
        if kind == "object":
            return isinstance(value, dict) and set(value) == set(spec["properties"]) and all(
                shape(value[k], sub) for k, sub in spec["properties"].items())
        if kind == "array":
            return isinstance(value, list) and len(value) <= 200 and all(shape(v, spec["items"]) for v in value)
        if kind == "integer":
            return type(value) is int and value >= 0
        return isinstance(value, str) and len(value) <= 2000 and ("enum" not in spec or value in spec["enum"])
    if not shape(report, schema()) or not report["reason"].strip():
        raise ValueError("审查结果不符合结构化契约")

    def in_blob(commit, path, start, end):
        rc, blob = w.git(repo, "show", f"{commit}:{path}")
        return rc == 0 and 0 < start <= end <= len(blob.splitlines())

    rows = {row["ac"]: row for row in report["criteria"]}
    if rows.keys() != criteria.keys() or len(rows) != len(report["criteria"]):
        raise ValueError("逐项覆盖所有批准 AC，且编号必须唯一")
    for ac, row in rows.items():
        source, ref, line = row["source"], row["ref"], row["line"]
        status = row["status"]
        valid = (
            source == "HEAD" and ref == criteria[ac] and in_blob(head, ref, line, line)
            or source == "GATE" and (approved_gates or {}).get(ac) == ref and ref in records and not line
               and any(lane["name"] == records[ref]["lane"] and any(
                   fnmatch.fnmatchcase(criteria[ac], pat) for pat in lane.get("scope") or []) for lane in lanes)
        )
        if not (status == "VERIFIED" and valid
                or status == "DEFERRED" and source == "TEST" and (approved_tests or {}).get(ac) == ref
                   and ref in downstream and not line
                or status in ("VIOLATED", "MISSING") and source == "NONE" and not ref and not line):
            raise ValueError(f"{ac} 的证据引用无效")
    work = {}
    violated = set()
    for finding in report["findings"]:
        path, ac, side = finding["path"], finding["ac"], finding["side"]
        start, end = finding["line"], finding["end_line"]
        if (ac and ac not in criteria or not ac and not finding["rule"].strip()
                or not finding["fix"].strip() or not finding["evidence"].strip()):
            raise ValueError("返工意见缺少批准 AC 或适用规范及修正要点")
        if side == "MISSING":
            valid = bool(ac and path == criteria[ac] and start == end == 0)
        else:
            commit = base if side == "BASE" else head
            valid = path in files and end - start <= 20 and in_blob(commit, path, start, end)
            if valid:
                patch = git(repo, "diff", "--no-ext-diff", "--no-renames", "-U0", base, head, "--", f":(literal){path}")
                hunks = re.findall(r"(?m)^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", patch)
                i = 0 if side == "BASE" else 2
                valid = any(int(h[i]) <= start <= end < int(h[i]) + int(h[i+1] or 1) for h in hunks)
        owners = [lane["name"] for lane in lanes if any(fnmatch.fnmatchcase(path, pat)
                                                        for pat in lane.get("scope") or [])]
        if not valid or len(owners) != 1:
            raise ValueError(f"返工定位无效：{path} {side}:{start}")
        if finding["severity"] != "low":
            if ac:
                violated.add(ac)
            where = f"{path} {side}:{start}-{end}" if start else f"{path}（缺失行为）"
            work.setdefault(owners[0], []).append(
                f"- [{finding['severity']}] {where} · {ac or finding['rule']}\n"
                f"  证据：{finding['evidence']}\n  修正与复验：{finding['fix']}")
    status = report["verdict"]
    missing = any(row["status"] == "MISSING" for row in rows.values())
    violations = {ac for ac, row in rows.items() if row["status"] == "VIOLATED"}
    if (status == "PASS" and (work or missing or violations or gaps or
                              re.search(r"缺少.{0,40}证据|无法验证|insufficient evidence|cannot verify", report["reason"], re.I))
            or status == "FAIL" and (not work or missing or violations != violated)
            or status == "BLOCKED" and (not missing and not gaps)):
        raise ValueError("审查结论与 AC 或缺陷证据冲突")
    return report, {lane: "\n".join(notes) for lane, notes in work.items()} if status == "FAIL" else {}
