"""Bind registered consumer checks to the producer's committed contract."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tempfile

import retro_evidence
import review_delivery
import worker


def specification(config, repo, head):
    spec = config.get("contracts")
    if not isinstance(spec, dict):
        raise ValueError("contracts 配置须为映射")
    paths, consumers = spec.get("paths"), spec.get("consumers")
    if not isinstance(paths, list) or not paths or not isinstance(consumers, dict) or not consumers:
        raise ValueError("contracts 需要文件路径和已登记的消费者命令")
    if not all(isinstance(name, str) and name.strip() and isinstance(cmd, str) and cmd.strip()
               for name, cmd in consumers.items()):
        raise ValueError("消费者名称和命令不能为空")
    if not head or review_delivery.revision(repo, "HEAD") != head:
        raise ValueError("契约验收需要当前交付提交")
    files = {}
    for path in paths:
        if not isinstance(path, str) or not path or any(c in path for c in "\\:\r\n\0"):
            raise ValueError("契约路径须为仓库相对文件")
        name = PurePosixPath(path)
        if name.is_absolute() or name.as_posix() != path or ".." in name.parts:
            raise ValueError("契约路径须为仓库相对文件")
        entry = review_delivery.git(repo, "ls-tree", "--full-tree", head, "--", f":(literal){path}")
        mode, kind, blob = (entry.partition("\t")[0].split() or ("", "", ""))
        if mode not in ("100644", "100755") or kind != "blob":
            raise ValueError(f"契约路径须为 Git 文件：{path}")
        files[path] = blob
    digest = hashlib.sha256(json.dumps([files, consumers], sort_keys=True).encode()).hexdigest()
    return {"head": head, "digest": digest, "consumers": sorted(consumers)}, files


def verify(config, repo, head):
    evidence, files = specification(config, repo, head)
    with tempfile.TemporaryDirectory(prefix="agentcode-contract-") as directory:
        workspace = Path(directory)
        archive = workspace / "source.tar"
        source = workspace / "source"
        subprocess.run(["git", "-C", repo, "archive", "--output", str(archive), head],
                       capture_output=True, check=True, timeout=90)
        with tarfile.open(archive) as packed:
            packed.extractall(source, filter="data")
        for name, blob in files.items():
            if review_delivery.git(repo, "hash-object", "--no-filters", "--", str(source / name)).strip() != blob:
                raise ValueError(f"归档契约与提交不一致：{name}")
        results = [(f"contract:{name}", command, *worker.run_gate(command, str(source)))
                   for name, command in config["contracts"]["consumers"].items()]
    return evidence, results


def validate(db, task, docs, state, config, repo):
    previous = (state.get("test_receipt") or {}).get("contract")
    if "contracts" not in config:
        if previous is not None:
            raise ValueError("登记的契约已移除，请重跑独立测试")
        return
    head = retro_evidence.delivery_head(state)
    expected, _ = specification(config, repo, head)
    record, _ = retro_evidence.read_report(db, task, docs, state, "test", "TEST_REPORT.md", head)
    passed = {row["gate_name"] for row in db.execute(
        "SELECT gate_name FROM gate_results WHERE task_id=? AND node_run_id=? AND passed=1",
        (task, record.get("run")))}
    if record.get("contract") != expected or not {f"test_contract:{name}" for name in expected["consumers"]} <= passed:
        raise ValueError("当前交付缺少通过的契约消费者凭证，请先重跑独立测试")
