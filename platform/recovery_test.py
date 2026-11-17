"""Process-level recovery acceptance, using real CLI, HTTP and SQLite."""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import intake
import ledger


THIS = Path(__file__).resolve()


def wait_for(probe, timeout=18):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = probe()
        if result:
            return result
        time.sleep(0.03)
    raise AssertionError("Timed out waiting for child process or checkpoint")


def pause(workspace, name):
    (workspace / (name + ".ready")).touch()
    wait_for((workspace / (name + ".go")).exists, timeout=45)


def child(workspace, mode, args):
    db_path = workspace / "state.db"
    if mode == "model-wait":
        pause(workspace, "design")
        return 0
    if mode == "intake":
        create = ledger.create_task

        def interrupted(db, *pos, **kw):
            # Hold an uncommitted INSERT after the complete directory was published.
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO tasks (id, requirement) VALUES (?, ?)", (kw["task_id"], "orphan"))
            pause(workspace, "intake")
            return create(db, *pos, **kw)

        ledger.create_task = interrupted
        intake.create_task_from_payload(db_path, workspace, {"content": "orphan"})
        return 0

    if mode == "lease":
        action, task, run, worker_id, epoch = args
        epoch = int(epoch)
        with closing(ledger.connect(db_path)) as db:
            if action == "claim":
                new_worker = ledger.register_worker(db, "takeover")
                try:
                    new_epoch = ledger.acquire_lease(db, new_worker, run)
                except ledger.LeaseHeldError:
                    print("live lease rejected")
                    return 5
                print(new_epoch)
                pause(workspace, "claimed")
                ledger.record_operation(db, task, f"{task}:result", "git_commit", "new",
                                        node_run_id=run, worker_id=new_worker, lease_epoch=new_epoch)
                ledger.finish_node_run(db, run, "done", worker_id=new_worker, epoch=new_epoch)
                return 0
            if action in ("holder", "write-race"):
                worker_id = ledger.register_worker(db, "original")
                epoch = ledger.acquire_lease(db, worker_id, run,
                                             ttl=0 if action == "write-race" else ledger.LEASE_TTL)
                (workspace / "lease.owner").write_text(f"{worker_id} {epoch}", encoding="ascii")
                if action == "holder":
                    pause(workspace, "holder")
                else:
                    assert_lease = ledger._assert_lease

                    def checked(*a):
                        assert_lease(*a)
                        pause(workspace, "checked")

                    ledger._assert_lease = checked
                    ledger.record_operation(db, task, f"{task}:before", "git_commit", "before",
                                            node_run_id=run, worker_id=worker_id, lease_epoch=epoch)
                    return 0
            for write in (
                lambda: ledger.record_operation(db, task, f"{task}:stale", "git_commit", "old",
                                                node_run_id=run, worker_id=worker_id, lease_epoch=epoch),
                lambda: ledger.finish_node_run(db, run, "done", worker_id=worker_id, epoch=epoch),
            ):
                try:
                    write()
                except ledger.StaleLeaseError:
                    print("stale rejected")
                else:
                    return 6
        return 0

    import graph
    import dashboard
    import run_task
    import worker
    from ac_regression import _stub_worker
    from langgraph.types import Command
    from langgraph.pregel import Pregel

    graph.PROJECTS_DIR = dashboard.PROJECTS_DIR = workspace.parent
    graph._base_codex_home = lambda: workspace / "dummy-model-home"
    _stub_worker()
    if mode == "start-race":
        require = intake.requirement_text

        def delayed(*a):
            pause(workspace, "start")
            return require(*a)

        intake.requirement_text = delayed
    elif mode == "approval-crash":
        invoke = Pregel.invoke

        def delayed(self, value, *a, **kw):
            if isinstance(value, Command):
                pause(workspace, "approval")
            return invoke(self, value, *a, **kw)

        Pregel.invoke = delayed
    elif mode == "design-crash":
        run_codex = worker.run_codex

        def delayed(prompt, *a, **kw):
            if "You are the Design Agent" in prompt:
                with subprocess.Popen([sys.executable, str(THIS), "--child", str(workspace), "model-wait"]) as proc:
                    (workspace / "model.pid").write_text(str(proc.pid), encoding="ascii")
                    rc = proc.wait(timeout=50)
                if rc == 0:
                    raise AssertionError("Model fixture should have been killed")
                pause(workspace, "interrupted")
            return run_codex(prompt, *a, **kw)

        worker.run_codex = delayed
    elif mode == "implement-crash":
        def interrupted(state):
            with closing(ledger.connect(db_path)) as db:
                graph._start_leased_run(db, state["task_id"], "implement_fe", with_codex_home=False)
            pause(workspace, "implement")
            raise AssertionError("Implement fixture should have been killed")

        graph.implement_node = interrupted
    elif mode == "revision-crash":
        def interrupted(*_args, **_kw):
            pause(workspace, "revision")
            raise AssertionError("Revision fixture should have been killed")

        worker.run_codex = interrupted
    if mode == "http":
        server = dashboard.ThreadingHTTPServer

        def bound(address, handler):
            http = server(address, handler)
            (workspace / "port").write_text(str(http.server_port), encoding="ascii")
            return http

        dashboard.ThreadingHTTPServer = bound
        return dashboard.main(["--control", "--port", "0", "--project", workspace.name,
                               "--db", str(db_path), "--project-yaml", str(workspace / "project.yaml")])
    return run_task.main([*args, "--project", workspace.name])


class Recovery(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="agentcode-recovery-")
        self.addCleanup(temp.cleanup)
        self.ws = Path(temp.name) / "acceptance"
        self.ws.mkdir()
        self.db_path = self.ws / "state.db"
        (self.ws / "project.yaml").write_text("project: acceptance\n", encoding="utf-8")
        with closing(ledger.init_db(self.db_path)):
            pass

    def process(self, mode, *arguments):
        proc = subprocess.Popen([sys.executable, "-u", str(THIS), "--child", str(self.ws), mode, *arguments],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"})
        self.addCleanup(self.reap, proc)
        return proc

    @staticmethod
    def reap(proc):
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=10)

    def cli(self, task, action, *arguments, mode="cli"):
        proc = self.process(mode, task, action, *arguments)
        output, _ = proc.communicate(timeout=25)
        return proc.returncode, output

    def serve(self):
        self.process("http")
        port_file = self.ws / "port"
        wait_for(lambda: port_file.exists() and port_file.stat().st_size)
        self.url = f"http://127.0.0.1:{port_file.read_text(encoding='ascii')}"
        wait_for(lambda: self.request("/api/jobs")[0] == 200)

    def request(self, path, **payload):
        data = json.dumps(payload).encode() if payload else None
        request = Request(self.url + path, data, {"X-Control": "1", "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=4) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            with exc:
                return exc.code, json.load(exc)
        except URLError:
            return 0, {}

    def finished_job(self, job_id):
        return wait_for(lambda: next((job for job in self.request("/api/jobs")[1]["jobs"]
                                     if job["job_id"] == job_id and job["status"] in ("done", "failed")), None))

    def rows(self, query, *args):
        with closing(ledger.connect(self.db_path)) as db:
            return [tuple(row) for row in db.execute(query, args)]

    def task(self):
        return intake.create_task_from_payload(self.db_path, self.ws, {"content": "recovery"})["task_id"]

    def test_cli_http_start_race_dispatches_once(self):
        task = self.task()
        self.serve()
        cli = self.process("start-race", task, "start")
        wait_for((self.ws / "start.ready").exists)
        status, job = self.request("/api/task/start", task_id=task)
        if status == 202:
            result = self.finished_job(job["job_id"])
            self.assertEqual(result["status"], "failed", result)
            self.assertIn("已有 CLI 或控制台", result["message"])
        else:
            self.assertEqual(status, 409, (status, job))
        (self.ws / "start.go").touch()
        output, _ = cli.communicate(timeout=20)
        self.assertEqual(cli.returncode, 0, output)
        self.assertEqual(self.rows("SELECT node_name, status FROM node_runs WHERE task_id=?", task),
                         [("requirements", "done")])

    def test_one_approval_after_crash_between_ledger_and_graph(self):
        task = self.task()
        code, output = self.cli(task, "start")
        self.assertEqual(code, 0, output)
        aid, digest = self.rows("SELECT id, artifact_hash FROM approvals WHERE task_id=?", task)[0]
        revision = f"{aid}:{digest}"
        self.serve()
        approver = self.process("approval-crash", task, "approve", "--by", "original")
        wait_for((self.ws / "approval.ready").exists)
        status, job = self.request("/api/task/decide", task_id=task, decision="approved", revision=revision)
        if status == 202:
            result = self.finished_job(job["job_id"])
            self.assertEqual(result["status"], "failed", result)
            self.assertIn("已有 CLI 或控制台", result["message"])
        else:
            self.assertEqual(status, 409, (status, job))
        self.reap(approver)
        status, job = self.request("/api/task/decide", task_id=task, decision="approved", revision=revision)
        self.assertEqual(status, 202, job)
        result = self.finished_job(job["job_id"])
        self.assertEqual(result["status"], "done", result)
        self.assertEqual(self.rows("SELECT decision, decided_by FROM approvals "
                                   "WHERE task_id=? AND gate_name='G1_requirements'", task),
                         [("approved", "original")])
        self.assertEqual(self.rows("SELECT status FROM node_runs WHERE task_id=? AND node_name='design'", task),
                         [("done",)])
        status, job = self.request("/api/task/decide", task_id=task, decision="approved", revision=revision)
        self.assertEqual(status, 202, job)
        self.assertEqual(self.finished_job(job["job_id"])["status"], "failed")
        self.assertEqual(self.rows("SELECT decision FROM approvals WHERE task_id=? AND gate_name='G2_design'", task),
                         [("pending",)])

    def test_worker_interruption_at_design_checkpoint(self):
        task = self.task()
        code, output = self.cli(task, "start")
        self.assertEqual(code, 0, output)
        crashed = self.process("design-crash", task, "approve")
        self.addCleanup((self.ws / "design.go").touch)
        wait_for((self.ws / "design.ready").exists)
        pid_path = self.ws / "model.pid"
        wait_for(lambda: pid_path.exists() and pid_path.stat().st_size)
        model_pid = int(pid_path.read_text(encoding="ascii"))
        os.kill(model_pid, signal.SIGTERM)
        wait_for((self.ws / "interrupted.ready").exists)
        old_run, old_worker, old_epoch = self.rows(
            "SELECT id, worker_id, lease_epoch FROM node_runs WHERE task_id=? AND node_name='design'", task)[0]
        self.reap(crashed)
        self.serve()
        status, job = self.request("/api/task/rerun", **{"task_id": task, "from": "design"})
        self.assertEqual(status, 202, job)
        result = self.finished_job(job["job_id"])
        self.assertEqual(result["status"], "done", result)
        runs = self.rows("SELECT status, lease_epoch FROM node_runs WHERE task_id=? AND node_name='design' "
                         "ORDER BY lease_epoch", task)
        self.assertEqual([row[0] for row in runs], ["cancelled", "done"])
        self.assertGreater(runs[1][1], old_epoch)
        old = self.process("lease", "old", task, old_run, old_worker, str(old_epoch))
        output, _ = old.communicate(timeout=12)
        self.assertEqual(old.returncode, 0, output)
        self.assertEqual(output.count("stale rejected"), 2)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM operations WHERE task_id=?", task), [(0,)])
        self.assertEqual(self.rows("SELECT gate_name, decision FROM approvals WHERE task_id=? ORDER BY id", task),
                         [("G1_requirements", "approved"), ("G2_design", "pending")])

    def test_two_process_lease_takeover(self):
        task = self.task()
        with closing(ledger.connect(self.db_path)) as db:
            run = ledger.start_node_run(db, task, "implement")
        old = self.process("lease", "holder", task, run, "", "0")
        wait_for((self.ws / "holder.ready").exists)
        old_worker, old_epoch = (self.ws / "lease.owner").read_text(encoding="ascii").split()
        self.assertEqual(self.rows("SELECT pid FROM workers WHERE id=?", old_worker), [(old.pid,)])
        claim = self.process("lease", "claim", task, run, old_worker, str(old_epoch))
        output, _ = claim.communicate(timeout=12)
        self.assertEqual(claim.returncode, 5, output)
        with closing(ledger.connect(self.db_path)) as db, db:
            db.execute("UPDATE node_runs SET lease_expires_at=datetime('now','-1 second') WHERE id=?", (run,))
        claim = self.process("lease", "claim", task, run, old_worker, str(old_epoch))
        wait_for((self.ws / "claimed.ready").exists)
        [new_epoch, state] = self.rows("SELECT lease_epoch, status FROM node_runs WHERE id=?", run)[0]
        self.assertGreater(new_epoch, int(old_epoch))
        self.assertEqual(state, "running")
        (self.ws / "holder.go").touch()
        output, _ = old.communicate(timeout=12)
        self.assertEqual(old.returncode, 0, output)
        self.assertEqual(output.count("stale rejected"), 2)
        (self.ws / "claimed.go").touch()
        output, _ = claim.communicate(timeout=12)
        self.assertEqual(claim.returncode, 0, output)
        self.assertEqual(self.rows("SELECT payload_hash FROM operations WHERE task_id=?", task), [("new",)])
        self.assertEqual(self.rows("SELECT status FROM node_runs WHERE id=?", run), [("done",)])

    def test_takeover_cannot_interleave_operation_check_and_write(self):
        task = self.task()
        with closing(ledger.connect(self.db_path)) as db:
            run = ledger.start_node_run(db, task, "implement")
            contender = ledger.register_worker(db, "takeover")
            # The old holder has passed the check and has not inserted its operation yet.
            db.execute("PRAGMA busy_timeout=100")
            crash = self.process("lease", "write-race", task, run, "", "0")
            wait_for((self.ws / "checked.ready").exists)
            with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                ledger.acquire_lease(db, contender, run)
            (self.ws / "checked.go").touch()
            output, _ = crash.communicate(timeout=12)
            self.assertEqual(crash.returncode, 0, output)
        self.assertEqual(self.rows("SELECT payload_hash FROM operations WHERE task_id=?", task), [("before",)])

    def test_interrupted_implementation_requires_reconciliation(self):
        task = self.task()
        for action in ("start", "approve"):
            code, output = self.cli(task, action)
            self.assertEqual(code, 0, output)
        crashed = self.process("implement-crash", task, "approve")
        wait_for((self.ws / "implement.ready").exists)
        self.reap(crashed)
        code, output = self.cli(task, "rerun", "--from", "implement")
        self.assertNotEqual(code, 0, output)
        self.assertIn("需人工核查旧进程、产物和副作用", output)
        self.assertEqual(self.rows("SELECT node_name FROM node_runs WHERE task_id=? AND node_name LIKE 'implement%'",
                                   task), [("implement_fe",)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM operations WHERE task_id=?", task), [(0,)])

    def test_changed_rejected_artifact_blocks_revision_replay(self):
        task = self.task()
        code, output = self.cli(task, "start")
        self.assertEqual(code, 0, output)
        crashed = self.process("revision-crash", task, "reject", "--reason", "revise this")
        wait_for((self.ws / "revision.ready").exists)
        self.reap(crashed)
        artifact = self.ws / task / "docs" / "REQUIREMENTS.md"
        original = artifact.read_bytes()
        updated = original + b"\nPreviously published revision.\n"
        artifact.write_bytes(updated)
        code, output = self.cli(task, "rerun", "--from", "requirements")
        self.assertNotEqual(code, 0, output)
        self.assertIn("原稿摘要无法核对", output)
        self.assertEqual(artifact.read_bytes(), updated)
        self.assertEqual(self.rows("SELECT decision FROM approvals WHERE task_id=?", task), [("rejected",)])
        self.assertEqual(self.rows("SELECT status FROM node_runs WHERE task_id=? ORDER BY lease_epoch", task),
                         [("done",), ("running",)])
        artifact.write_bytes(original)
        code, output = self.cli(task, "rerun", "--from", "requirements")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.rows("SELECT decision FROM approvals WHERE task_id=? ORDER BY id", task),
                         [("rejected",), ("pending",)])

    def test_forced_exit_after_intake_rename_cannot_start_orphan(self):
        process = self.process("intake")
        wait_for((self.ws / "intake.ready").exists)
        self.reap(process)
        [record] = list(self.ws.glob("*/requirement.json"))
        orphan = record.parent.name
        self.assertEqual(self.rows("SELECT id FROM tasks"), [])
        code, output = self.cli(orphan, "start")
        self.assertNotEqual(code, 0, output)
        self.assertIn("任务不存在", output)
        self.serve()
        self.assertEqual(self.request("/api/task/start", task_id=orphan)[0], 404)
        self.assertEqual(self.rows("SELECT id FROM node_runs"), [])
        fresh = self.task()
        self.assertNotEqual(orphan, fresh)
        code, output = self.cli(fresh, "start")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.rows("SELECT id FROM tasks"), [(fresh,)])


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        sys.exit(child(Path(sys.argv[2]), sys.argv[3], sys.argv[4:]))
    unittest.main()
