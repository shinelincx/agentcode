-- P1 最小协议子集 + P2 B1 租约（V2.2 §4.2 / §4.6）
-- 目标：tasks / node_runs / lanes / operations / approvals / gate_results / cost_ledger / workers
-- B1：workers 登记 + node_runs 租约（worker_id / lease_epoch / lease_expires_at / heartbeat_at）
-- B3：lanes 独立表（branch / file_scope / merge_order），node_runs.lane 保留为执行快照
-- 环境：SQLite 3.50+（WAL 模式），结构与 Postgres 兼容方向设计

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- 1. 任务主表：全生命周期状态
CREATE TABLE IF NOT EXISTS tasks (
  id            TEXT PRIMARY KEY,                       -- uuid
  requirement   TEXT NOT NULL,
  priority      TEXT NOT NULL DEFAULT 'normal'
                CHECK (priority IN ('high','normal','low')),  -- ALERT/DEGRADED 时低优先级排队
  status        TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','running','awaiting_human','gate_failed','done','failed','cancelled')),
  current_node  TEXT,
  retry_count   INTEGER NOT NULL DEFAULT 0,             -- Worker 重试计数（V2.2 §9.2）
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 1b. Worker 登记（B1）：租约 epoch 的持有者身份 + 独立凭据目录归属
CREATE TABLE IF NOT EXISTS workers (
  id            TEXT PRIMARY KEY,                       -- worker 实例 id（每次节点执行登记一条）
  role          TEXT NOT NULL,                          -- 节点角色（requirements|design|implement_api|...）
  host          TEXT,
  pid           INTEGER,
  status        TEXT NOT NULL DEFAULT 'alive'
                CHECK (status IN ('alive','dead','retired')),
  lease_epoch   INTEGER NOT NULL DEFAULT 0,             -- 最近一次领到的栅栏令牌
  lease_expires_at TEXT,
  last_heartbeat_at TEXT,
  registered_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_workers_status ON workers(status, last_heartbeat_at);

-- 2. 节点执行尝试（lane 信息以 JSON 并入：file_scope / branch / worktree）
CREATE TABLE IF NOT EXISTS node_runs (
  id            TEXT PRIMARY KEY,
  task_id       TEXT NOT NULL REFERENCES tasks(id),
  node_name     TEXT NOT NULL,                          -- requirements|design|implement_fe|implement_be|test|deploy|retro
  attempt       INTEGER NOT NULL DEFAULT 1,
  status        TEXT NOT NULL DEFAULT 'running'
                CHECK (status IN ('running','done','failed','awaiting_human','cancelled')),
  lane          TEXT,                                   -- lane 标识（如 fe / be），可空
  file_scope    TEXT,                                   -- JSON：允许改动路径清单（文件归属表）
  branch        TEXT,
  worktree_path TEXT,
  output_path   TEXT,                                   -- 产物落盘目录/文件（恢复判定依据）
  worker_id     TEXT REFERENCES workers(id),            -- B1：租约持有者
  lease_epoch   INTEGER,                                -- B1：栅栏令牌（接管后旧 epoch 写被拒）
  lease_expires_at TEXT,                                -- B1：租约到期时间（过期可被接管）
  heartbeat_at  TEXT,                                   -- B1：最近心跳
  started_at    TEXT NOT NULL DEFAULT (datetime('now')),
  ended_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_node_runs_task ON node_runs(task_id, node_name);

-- 2b. Lane 计划（B3）：从 node_runs 的 JSON 字段升级为独立表
-- 串行合并顺序 = merge_order 升序；base_branch 记录前驱 lane 的分支（首个为任务 base 分支），
-- 后继 lane 从该分支重建，等价于按序合并。node_runs.lane 保留为执行快照，历史数据可回填。
CREATE TABLE IF NOT EXISTS lanes (
  id            TEXT PRIMARY KEY,                       -- <task_id>:<name>
  task_id       TEXT NOT NULL REFERENCES tasks(id),
  name          TEXT NOT NULL,                          -- be / fe / ...
  node_name     TEXT,                                   -- implement_be / implement_fe
  branch        TEXT,                                   -- lane 分支
  base_branch   TEXT,                                   -- 合并顺序依据：前驱 lane 分支
  file_scope    TEXT,                                   -- JSON：允许改动路径清单
  merge_order   INTEGER NOT NULL DEFAULT 0,             -- 合并顺序（0 起）
  status        TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','running','done','failed','skipped')),
  merged_at     TEXT,                                   -- 被后继分支吸收的时间（末 lane 为收尾时间）
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lanes_task_name ON lanes(task_id, name);
CREATE INDEX IF NOT EXISTS idx_lanes_order ON lanes(task_id, merge_order);

-- 3. 副作用操作（幂等键去重——崩溃恢复重放的根基，P1 不可简化项）
CREATE TABLE IF NOT EXISTS operations (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id       TEXT NOT NULL REFERENCES tasks(id),
  node_run_id   TEXT REFERENCES node_runs(id),
  operation_key TEXT NOT NULL UNIQUE,                   -- 幂等键（如 task_id:node:git-commit:lane-fe）
  op_type       TEXT NOT NULL,                          -- git_commit|pr_create|deploy_staging|...
  payload_hash  TEXT NOT NULL,                          -- 参数摘要（重放一致性校验）
  status        TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','done','failed','skipped')),
  result_ref    TEXT,                                   -- 结果引用（commit sha / PR url）
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 4. 审批（结论绑定产物哈希——AC-09 的根基，P1 不可简化项）
CREATE TABLE IF NOT EXISTS approvals (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id       TEXT NOT NULL REFERENCES tasks(id),
  gate_name     TEXT NOT NULL,                          -- G1_requirements|G2_design|G3_staging
  artifact_hash TEXT NOT NULL,                          -- 被审产物哈希（批准后变更即失效）
  decision      TEXT NOT NULL DEFAULT 'pending'
                CHECK (decision IN ('pending','approved','rejected')),
  decided_by    TEXT,
  decided_at    TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_approvals_active
  ON approvals(task_id, gate_name) WHERE decision = 'pending';

-- 5. 门禁结果（失败输出全文 = Retro 原料，P1 不可简化项）
CREATE TABLE IF NOT EXISTS gate_results (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id       TEXT NOT NULL REFERENCES tasks(id),
  node_run_id   TEXT REFERENCES node_runs(id),
  gate_name     TEXT NOT NULL,                          -- typecheck|unit|coverage|e2e|scope_check|...
  passed        INTEGER NOT NULL CHECK (passed IN (0,1)),
  output        TEXT,                                   -- 失败输出全文
  checked_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 6. 用量账本（事后计量：V2.2 §8.2，只记录不拦截）
CREATE TABLE IF NOT EXISTS cost_ledger (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id       TEXT,
  node_run_id   TEXT,
  agent_role    TEXT NOT NULL,
  executor      TEXT NOT NULL,                          -- codex|claude
  model         TEXT,
  tokens_used   INTEGER,                                -- codex 输出解析（发现 C）
  calls         INTEGER NOT NULL DEFAULT 1,             -- 计量主单位（订阅模式）
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cost_task ON cost_ledger(task_id);
CREATE INDEX IF NOT EXISTS idx_cost_time ON cost_ledger(created_at);

-- 附表 A（B2 升级：至少一次投递 outbox）：Retro 候选投递队列
-- 语义：先记 attempts 再投递；失败留 pending + 退避；超 max_attempts 转 dead（死信）；
--       AGENTS.md 的 [id] 标记保证重放不重复（崩溃在「已写、未标 done」之间也能收敛）。
CREATE TABLE IF NOT EXISTS retro_queue (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id       TEXT NOT NULL,
  payload       TEXT NOT NULL,                          -- 候选经验 JSON（id/text/type/scope/risk/source/evidence）
  retries       INTEGER NOT NULL DEFAULT 0,             -- P1 兼容列（B2 起用 attempts）
  status        TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','done','dropped','dead')),
  attempts      INTEGER NOT NULL DEFAULT 0,             -- B2：投递尝试次数
  next_attempt_at TEXT,                                 -- B2：退避窗口（NULL=立即可投）
  last_error    TEXT,                                   -- B2：最近一次失败原因
  delivered_at  TEXT,                                   -- B2：成功投递时间
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 附表 B：预算状态机（当前态 + 变迁留痕，V2.2 §8.4）
CREATE TABLE IF NOT EXISTS budget_state (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  project       TEXT NOT NULL,
  state         TEXT NOT NULL CHECK (state IN ('NORMAL','ALERT','DEGRADED','PAUSED')),
  reason        TEXT,                                   -- 触发依据（累计 calls / 阈值）
  changed_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_budget_current ON budget_state(project, id);
