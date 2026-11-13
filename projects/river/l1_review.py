"""L1 审核执行：4 条候选采纳归档（用户 2026-11-11 周会全采纳）。"""
import sqlite3
from pathlib import Path

db = sqlite3.connect(r"d:\project\AgentCode\projects\river\state.db")
cur = db.execute(
    "UPDATE retro_queue SET status='done' WHERE status='pending' AND "
    "(payload LIKE '%C-003%' OR payload LIKE '%C-004%' OR payload LIKE '%C-005%' OR payload LIKE '%C-006%')")
db.commit()
print(f"adopted/archived: {cur.rowcount} candidates")
rest = db.execute("SELECT COUNT(*) FROM retro_queue WHERE status='pending'").fetchone()[0]
print(f"remaining pending: {rest}")
