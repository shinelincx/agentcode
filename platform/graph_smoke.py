"""graph.py 冒烟：interrupt 人审 ×2 + 跨进程崩溃恢复验证。

场景：
  1) 首次 invoke → 挂起在 G1（interrupt）
  2) resume(approved) → 挂起在 G2
  3) 【模拟崩溃】新建 checkpointer/app 实例（等效进程重启）
  4) 从 checkpoint 恢复 → 仍停在 G2（状态未丢）
  5) resume(approved) → 走完 implement → retro → END
"""
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from langgraph.types import Command

import ledger
import graph as g
import worker as w

PASS = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        raise SystemExit(1)


def main():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        ws = Path(td)
        # 隔离的业务账本 + 图检查点（双库分离）
        ledger.init_db(ws / "state.db")
        g.RIVER_WS = ws  # 重定向到临时工作区
        # implement 节点已 Worker 化：本冒烟只验证图语义（interrupt/恢复/回环），
        # 因此把 codex、门禁、git 全部打桩，避免真实执行与仓库路径依赖。
        # A2 起 requirements/design 也走 codex：桩须按 prompt 返回合法 Markdown 文档。
        def _stub_codex(prompt, workdir, **kw):
            if "Write REQUIREMENTS.md" in prompt:
                out = ("# REQUIREMENTS · task stub\n\n## 需求\n- stub requirement: add a sample field\n\n"
                       "## 验收标准\n- AC-1: stub acceptance criterion\n- AC-2: stub second criterion\n\n"
                       "## 非目标\n- nothing else\n")
            elif "Write DESIGN.md" in prompt:
                out = ("# DESIGN · task stub\n\n## 文件归属表\n| 路径 | lane |\n| --- | --- |\n"
                       "| apps/api/src/index.ts | be |\n| apps/web/src/app.tsx | fe |\n\n"
                       "## 实现策略\nserial be → fe\n\n"
                       "## 验收映射\n- AC-1 → apps/api/src/index.ts\n- AC-2 → apps/web/src/app.tsx\n")
            elif "You are the Prototype Agent" in prompt:
                target = Path(re.search(r"Prototype directory:\s*(.+)", prompt).group(1).strip())
                target.mkdir(parents=True, exist_ok=True)
                (target / "index.html").write_text(
                    "<!DOCTYPE html>\n<html lang=\"zh\">\n<head><meta charset=\"utf-8\">"
                    "<title>stub prototype</title></head>\n<body>\n<h1>stub prototype</h1>\n"
                    "<p>静态占位原型：用于验证原型节点产物校验与 G2 审批哈希绑定。</p>\n"
                    "<p>该页面不连接任何后端接口，仅覆盖最小长度与 HTML 骨架校验。</p>\n"
                    "<button>示例操作</button>\n</body>\n</html>\n", encoding="utf-8")
                (target / "PROTOTYPE.md").write_text(
                    "# PROTOTYPE · stub\n\n## 原型说明\n首页提示区域的静态占位原型，"
                    "包含标题与操作按钮，用于验证产物校验流程。\n\n"
                    "## 验收映射\n- AC-1 → h1 标题\n- AC-2 → button 操作\n\n"
                    "## 局限\n静态页面，无真实接口与状态流转。\n", encoding="utf-8")
                out = "prototype stub written"
            else:
                out = "stub implement report"
            return w.WorkerResult(0, out + "\ntokens used\n  10\n", 10, "stub")
        w.run_codex = _stub_codex
        w.run_gate = lambda command, workdir, timeout=900: (True, "stub gate passed")

        _branch = {"name": ""}

        def _fake_git(workdir, *args):
            # checkout -B 记录 lane 分支：scope 硬校验要求 fe 只改 web、be 只改 api
            if len(args) >= 3 and args[0] == "checkout" and args[1] == "-B":
                _branch["name"] = args[2]
                return (0, "")
            if args[:2] == ("status", "--porcelain"):  # C-007 空交付检测：桩须模拟有改动
                if _branch["name"].endswith("-fe"):
                    return (0, "M  apps/web/src/app.tsx\n")
                return (0, "M  apps/api/src/index.ts\n")
            return (0, "")
        w.git = _fake_git
        db = ledger.connect(ws / "state.db")
        # 角色流程桩配置：测试门禁 + staging 部署（命令由下方 run_gate 桩统一通过）
        (ws / "project.yaml").write_text(
            "tech_stack:\n"
            "  gate_commands:\n"
            "    typecheck: stub-typecheck\n"
            "    unit: stub-unit\n"
            "test:\n"
            "  gates: [typecheck, unit]\n"
            "deploy:\n"
            "  staging:\n"
            "    required: true\n"
            "    commands: [stub-deploy]\n"
            "    smoke: stub-smoke\n"
            "    healthcheck: stub-health\n", encoding="utf-8")
        task_id = ledger.create_task(db, "graph skeleton smoke")

        cp = g.make_checkpointer(ws / "graph.db")
        app = g.build_app(cp)
        cfg = {"configurable": {"thread_id": "smoke-1"}}

        # 1) 跑到 G1 挂起
        state = app.invoke({"task_id": task_id, "requirement": "smoke"}, cfg)
        snap = app.get_state(cfg)
        check("interrupted at G1", snap.next == ("review_g1",), str(snap.next))
        check("requirements done in ledger",
              db.execute("SELECT status FROM node_runs WHERE node_name='requirements'").fetchone()["status"] == "done")

        # 2) G1 批准 → 到 G2
        app.invoke(Command(resume="approved"), cfg)
        snap = app.get_state(cfg)
        check("interrupted at G2", snap.next == ("review_g2",), str(snap.next))
        check("原型角色已执行并落专属目录",
              db.execute("SELECT status FROM node_runs WHERE node_name='prototype'").fetchone()["status"] == "done"
              and (ws / task_id / "prototype" / "index.html").is_file()
              and (ws / task_id / "prototype" / "PROTOTYPE.md").is_file())
        # 3) 模拟崩溃：丢弃内存中的 app/checkpointer，重建（等效进程重启）
        cp2 = g.make_checkpointer(ws / "graph.db")
        app2 = g.build_app(cp2)

        # 4) 恢复后仍在 G2（checkpointer 持久化生效）
        snap2 = app2.get_state(cfg)
        check("state survives restart", snap2.next == ("review_g2",), str(snap2.next))

        # 5) G2 批准 → 跑完实现 + 测试 + 部署，挂起在 G3 staging 验收
        app2.invoke(Command(resume="approved"), cfg)
        snap3 = app2.get_state(cfg)
        check("interrupted at G3", snap3.next == ("review_g3",), str(snap3.next))
        check("测试角色已执行并落报告",
              db.execute("SELECT status FROM node_runs WHERE node_name='test'").fetchone()["status"] == "done"
              and (ws / task_id / "docs" / "TEST_REPORT.md").is_file())
        check("部署角色已执行并落报告",
              db.execute("SELECT status FROM node_runs WHERE node_name='deploy'").fetchone()["status"] == "done"
              and (ws / task_id / "docs" / "DEPLOY_REPORT.md").is_file())
        check("自动审查已执行并落 REVIEW.md",
              db.execute("SELECT status FROM node_runs WHERE node_name='review'").fetchone()["status"] == "done"
              and (ws / task_id / "docs" / "REVIEW.md").is_file()
              and "PASS" in (ws / task_id / "docs" / "REVIEW.md").read_text(encoding="utf-8"))

        # 6) G3 批准 → retro → END
        app2.invoke(Command(resume="approved"), cfg)
        snap4 = app2.get_state(cfg)
        check("graph finished after G3", snap4.next == (), str(snap4.next))
        check("task done in ledger",
              db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()["status"] == "done")

        # 打回路径验证（拒绝 → 回上游节点）
        task2 = ledger.create_task(db, "reject path")
        cfg2 = {"configurable": {"thread_id": "smoke-reject"}}
        app2.invoke({"task_id": task2, "requirement": "smoke2"}, cfg2)
        app2.invoke(Command(resume="rejected"), cfg2)  # G1 拒绝
        snapr = app2.get_state(cfg2)
        check("reject loops back to requirements", "requirements" in str(snapr.values.get("approved_g1")) or snapr.next == ("review_g1",), str(snapr.next))

        print(f"\nALL {len(PASS)} CHECKS PASSED")


if __name__ == "__main__":
    main()
