"""Build and validate retrospectives against the recorded delivery revision."""
from hashlib import sha256
import json

import ledger
import review_delivery as review
from worker import safe_gate_text


def delivery_head(state):
    commits = (state.get("delivery") or {}).get("commits") or []
    return commits[-1]["commit"] if commits else ""


def receipt(path, head, run, **extra):
    return {"head": head, "run": run, "digest": sha256(path.read_bytes()).hexdigest(), **extra} if head else {}


def read_report(db, task, docs, state, key, filename, head):
    record = state.get(f"{key}_receipt") or {}
    try:
        path = docs / filename
        data = path.read_bytes()
    except OSError:
        return {}, ""
    if not head or record.get("head") != head or record.get("digest") != sha256(data).hexdigest():
        return {}, ""
    if key == "deploy" and record.get("skipped"):
        row = db.execute("SELECT 1 FROM gate_results WHERE id=? AND task_id=? AND gate_name='deploy_skipped' AND passed=1",
                         (record.get("gate_id"), task)).fetchone()
        return (record, "") if row else ({}, "")
    run = db.execute("SELECT status FROM node_runs WHERE id=? AND task_id=? AND node_name=?",
                     (record.get("run"), task, key)).fetchone()
    if not run or run["status"] != "done":
        return {}, ""
    return record, safe_gate_text(data.decode("utf-8", errors="replace"))


def collect(db, task, docs, state, repo, specs, project):
    head = delivery_head(state)
    delivery = state.get("delivery") or {}
    bundle = {"task": task, "project": project, "head": head, "lanes": [], "evidence": {}}
    evidence = bundle["evidence"]

    def add(key, status, detail, locator="", lane=""):
        evidence[key] = dict(status=status, detail=safe_gate_text(detail)[:1800], locator=locator, lane=lane)

    try:
        if not head or review.revision(repo, head) != head:
            raise ValueError("当前任务缺少可验证的交付提交")
        base = review.revision(repo, delivery["base"])
        parent = base
        for part in delivery["commits"]:
            commit = part["commit"]
            if review.git(repo, "rev-list", "--parents", "-n", "1", commit).split() != [commit, parent]:
                raise ValueError("交付提交链不连续")
            parent = commit
        files = review.paths(repo, base, head)
        if not files:
            raise ValueError("交付 diff 为空")
        display = ", ".join(path.replace("\r", "\\r").replace("\n", "\\n") for path in files)
        add("delivery", "RECORDED", "文件：" + display, f"{base}..{head}")
        bundle["lanes"] = sorted({part["lane"] for part in delivery["commits"]})
    except (OSError, ValueError, KeyError) as exc:
        head = ""
        bundle["head"] = ""
        add("delivery", "UNKNOWN", str(exc))

    for name, state_key in (("g1_plan", "approved_requirements_text"), ("g2_plan", "approved_design_text")):
        text = state.get(state_key)
        if text and head:
            add(name, "PLAN", text[:1200], f"SHA-256 {sha256(text.encode('utf-8')).hexdigest()}")

    if head:
        try:
            records = review.gates(db, task, delivery, specs)
        except (ValueError, KeyError) as exc:
            add("implementation", "UNKNOWN", str(exc))
        else:
            for name, row in records.items():
                add("gate." + name.replace("/", "."), "PASS", row["output"],
                    f"{row['commit']} · {row['run']}", row["lane"])

    rev, report = read_report(db, task, docs, state, "review", "REVIEW.md", head)
    if rev:
        row = db.execute("SELECT passed, output FROM gate_results WHERE node_run_id=? AND task_id=? "
                         "AND gate_name='review_agent' ORDER BY id DESC LIMIT 1", (rev["run"], task)).fetchone()
        if row and row["passed"]:
            add("review", "PASS", row["output"] + "\n" + report, f"REVIEW.md · {rev['run']} · {rev['digest']}")
        try:
            agent = (docs / "REVIEW-agent.md").read_bytes()
        except OSError:
            agent = b""
        if row and row["passed"] and agent and rev.get("agent_digest") == sha256(agent).hexdigest():
            add("review.findings", "RECORDED", safe_gate_text(agent.decode("utf-8", errors="replace")),
                f"REVIEW-agent.md · {rev['run']} · {rev['agent_digest']}")
    if "review" not in evidence:
        add("review", "UNKNOWN", "缺少绑定当前交付的审查记录")

    tested, _ = read_report(db, task, docs, state, "test", "TEST_REPORT.md", head)
    if tested:
        rows = list(db.execute("SELECT id,gate_name,passed,output FROM gate_results WHERE node_run_id=? AND task_id=? "
                               "AND gate_name GLOB 'test_*' ORDER BY id", (tested["run"], task)))
        reused = 0
        for row in rows:
            was_reused = row["passed"] and row["output"] == "同一提交与环境的仓库级实现门禁已通过"
            reused += int(was_reused)
            status = "REUSED" if was_reused else "PASS" if row["passed"] else "FAIL"
            add("test." + row["gate_name"][5:], status, row["output"],
                f"TEST_REPORT.md · {tested['run']} · gate:{row['id']}")
        if rows:
            passed = sum(row["passed"] for row in rows)
            add("test", "PASS" if passed == len(rows) else "FAIL", f"{passed}/{len(rows)} 道测试门禁通过（复用 {reused}）",
                f"TEST_REPORT.md · {tested['run']} · {tested['digest']}")
    if "test" not in evidence:
        add("test", "UNKNOWN", "缺少绑定当前交付的测试记录")

    deployed, _ = read_report(db, task, docs, state, "deploy", "DEPLOY_REPORT.md", head)
    if deployed.get("skipped"):
        add("staging", "SKIPPED", "本轮显式跳过 staging", f"gate:{deployed['gate_id']}")
        add("g3", "NOT_RUN", "部署跳过，未进入 G3")
    elif deployed:
        rows = list(db.execute("SELECT id,passed FROM gate_results WHERE task_id=? AND node_run_id=? "
                               "AND gate_name GLOB 'deploy_*' ORDER BY id", (task, deployed["run"])))
        if rows:
            ok = all(r["passed"] for r in rows)
            add("staging", "PASS" if ok else "FAIL", f"部署门禁 {sum(r['passed'] for r in rows)}/{len(rows)}",
                f"DEPLOY_REPORT.md · {deployed['run']} · {deployed['digest']}")
            approved = state.get("g3_receipt") or {}
            if ok and approved.get("head") == head:
                match = db.execute("SELECT decision, artifact_hash FROM approvals WHERE id=? AND task_id=? "
                                   "AND gate_name='G3_staging'", (approved.get("approval"), task)).fetchone()
                if match and match["decision"] == "approved" and match["artifact_hash"] == ledger.artifact_digest([docs / "DEPLOY_REPORT.md"]):
                    add("g3", "APPROVED", "账本已批准当前部署报告", f"approval:{approved['approval']}")
    if "staging" not in evidence:
        add("staging", "UNKNOWN", "缺少绑定当前交付的部署记录")
    if "g3" not in evidence:
        add("g3", "UNKNOWN", "缺少绑定当前部署报告的 G3 决议")
    return bundle


def model_context(bundle):
    """Expose citable summaries without duplicating the signed provenance."""
    summaries = {}
    for key, item in bundle["evidence"].items():
        detail = item["detail"]
        if key == "review":
            # The first line is the accepted review verdict, before the report body.
            detail = detail.partition("\n")[0]
        elif key in {"g1_plan", "g2_plan", "review.findings"}:
            if key == "review.findings":
                detail = detail.partition("\n## 建议\n")[2].strip() or detail
            lines = [line for line in (line.strip() for line in detail.splitlines())
                     if line and not line.startswith("#")]
            if key != "review.findings":
                lines = [line for line in lines if "AC-" in line.upper()] or lines
            detail = " / ".join(lines[:3])
        else:
            detail = " ".join(detail.split())
        if len(detail) > 160:
            detail = detail[:159] + "…"
        summaries[key] = [item["status"], detail] if detail else [item["status"]]
    return json.dumps(summaries, ensure_ascii=False, separators=(",", ":"))


def schema(bundle=None):
    from prompt_output import _object

    text = {"type": "string"}
    ids = list(bundle["evidence"]) if bundle else []
    ref = {"type": "string", "enum": ids} if ids else text
    refs = {"type": "array", "items": ref}
    passing = [key for key in ids if key.startswith(("gate.", "test."))
               and bundle["evidence"][key]["status"] == "PASS"]
    candidate_refs = {"type": "array", "items": {"type": "string", "enum": passing}} if passing else refs
    note = {"type": "array", "items": _object({"text": text, "refs": refs})}
    return _object({
        "inferences": note, "pending": note, "actions": note,
        "candidates": {"type": "array", "items": _object({
            "type": {"type": "string", "enum": ["pitfall", "convention"]},
            "scope": {"type": "string", "enum": ["project", *bundle["lanes"]]} if bundle else text,
            "text": text, "applies_when": text, "refs": candidate_refs,
        })},
    })


def check_candidate(candidate, bundle):
    scope = candidate["scope"]
    if scope not in {"project", *bundle["lanes"]} or candidate["type"] not in {"pitfall", "convention"}:
        raise ValueError("记忆候选超出当前项目或 lane")
    for key in ("text", "applies_when"):
        value = candidate[key]
        if not isinstance(value, str) or not value.strip() or len(value) > 240 or "\n" in value or "\r" in value:
            raise ValueError("记忆候选须提供单行做法与适用条件")
        if safe_gate_text(value) != value:
            raise ValueError("记忆候选含敏感字段")
    refs = candidate["refs"]
    evidence = bundle["evidence"]
    if not bundle["head"] or not refs or any(ref not in evidence or
            evidence[ref]["status"] != "PASS" or not ref.startswith(("gate.", "test.")) or
            scope != "project" and evidence[ref].get("lane") not in ("", scope) for ref in refs):
        raise ValueError("记忆候选没有当前范围内通过的独立证据")


def validate(message, bundle):
    result = json.loads(message)
    if not isinstance(result, dict) or set(result) != set(schema()["properties"]):
        raise ValueError("复盘结构无效")
    if any(not isinstance(items, list) or len(items) > 24 for items in result.values()):
        raise ValueError("复盘条目列表无效")
    evidence = bundle["evidence"]

    def refs_exist(refs):
        return isinstance(refs, list) and all(isinstance(ref, str) and ref in evidence for ref in refs)

    for group in ("inferences", "pending", "actions"):
        for line in result[group]:
            if (not isinstance(line, dict) or set(line) != {"text","refs"} or not refs_exist(line["refs"])
                    or not isinstance(line["text"], str) or not line["text"].strip() or len(line["text"]) > 400
                    or any(char in line["text"] for char in "\r\n") or group == "inferences" and not line["refs"]):
                raise ValueError(f"{group} 的文本或引用无效")
    for candidate in result["candidates"]:
        if (not isinstance(candidate, dict) or set(candidate) != {"type","scope","text","applies_when","refs"}
                or not refs_exist(candidate["refs"]) or not isinstance(candidate["scope"], str)
                or not isinstance(candidate["type"], str)):
            raise ValueError("记忆候选的文本或引用无效")
        check_candidate(candidate, bundle)
    return result


def render(bundle, result, evidence_file):
    evidence = bundle["evidence"]
    observed = list(dict.fromkeys(["delivery", "review", "test", "staging", "g3",
                                 *(key for key in evidence if key.startswith(("gate.", "test.", "review.")))]))
    lines = [f"# RETRO · task {bundle['task'][:8]}", "", f"交付提交 {bundle['head'] or '未知'}",
             f"[证据快照]({evidence_file})", "", "## 结果"]
    for key in observed:
        if key in evidence and evidence[key]["status"] not in ("UNKNOWN", "PLAN"):
            item = evidence[key]
            summary = item["detail"] if key in ("delivery", "test", "staging", "g3") else item["locator"]
            lines.append(f"- {key} {item['status']} · {summary} [{key}]")
    deviations = ["- staging 显式跳过，G3 未执行 [staging, g3]"] if evidence["staging"]["status"] == "SKIPPED" else ["无"]
    lines.extend(["", "## 偏差", *deviations])
    for title, group in (("推断", "inferences"), ("待验证", "pending"), ("后续动作", "actions")):
        entries = [f"- {safe_gate_text(row['text'])} [{', '.join(row['refs'])}]" for row in result[group]]
        if group == "pending":
            entries.extend(f"- {key} UNKNOWN · {row['detail']}" for key, row in evidence.items() if row["status"] == "UNKNOWN")
        lines.extend(["", f"## {title}", *(entries or ["无"])])
    lines.extend(["", "## 经验", *(f"- 待人工审核 · {c['scope']} · {c['applies_when']} · {c['text']}"
                                  for c in result["candidates"])] if result["candidates"] else ["", "## 经验", "无"])
    return "\n".join(lines) + "\n"
