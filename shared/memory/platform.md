<!-- 平台级 Agent 记忆（跨项目通用）· L1 人工审核采纳 · 2026-11-11 周会
     审核人：用户（单人值守）· 来源：task-001/002/003 Retro 候选池 -->

## Platform Pitfalls

- [C-005] 多行 prompt 作为 codex exec 命令行参数会被截断（仅第一行生效），codex 以「需求不完整」正常退出且不报错。
  规避：必须经 stdin 传递（`-` 占位）。已固化于 platform/worker.py。
  来源: task-002 | 证据: codex 报告原文 + 31 字节失败日志
- [C-006] 串行双 lane 的后继 lane 若不基于前驱分支创建，将缺失后端改动且门禁假绿（TS 类型声明不校验运行时）。
  规避：分支链 prev_branch 继承（已固化于 platform/graph.py）；**扩展修复（2026-11-12）**：分支已存在时不能复用——必须 `git checkout -B lane prev_branch` 强制重建，否则前一次空运行残留的分支会携带错误 base。
  来源: task-003 + task-004 retry | 证据: fe 分支 HEAD=81850f8（base commit）无任何改动，codex 报告 hallucination "Added startedAt..." 但文件不存在
- [C-007] CODEX_HOME 隔离 Worker 沙箱配置缺失：仅复制 auth.json 不足以让 `codex exec -s workspace-write` 正常工作；缺项目信任配置时 codex 将 workspace 视为不受信目录、降级只读；缺 Windows sandbox=unelevated 时 powershell.exe 被 CreateProcess 层 policy 拒绝。两者同时缺失导致 Worker 静默空交付。
  规避：隔离 home 必须带最小 config.toml（项目信任 + `[windows] sandbox=unelevated`）；Worker 权限上限由编排层 `-s workspace-write` 决定，不继承主 home 的 sandbox_mode=danger-full-access。**三重防线**：① graph.py 在门禁前 `git status --porcelain` 空交付检测；② `git commit` 返回值检查（空提交 rc≠0 判失败）；③ retro 守卫扩展至 gate_failed/failed。
  来源: task-004（首跑空交付 13 tokens）| 证据: codex 日志 "environment is read-only... blocked by policy" + 冒烟 `SMOKE_FILE=PASS` 证明配置修复后可写 + AC-02b 4 条断言固化防回归
- [C-008] codex exec 的 stdout/stderr 拼接后顺序会交错：生成的 Markdown 正文末尾可能紧跟大量 `2026-...Z INFO ...` 运行日志，直接整体当作文档落盘会把日志混入产物（实测 236KB，其中正文仅 392 字符）。另注意 codex 的 `tokens used` 统计段位置不固定。
  规避：文档抽取必须①先按 `tokens used` 截断；②自首个 `# ` 一级标题起；③遇 ISO 时间戳日志行立即截断；④设产物长度上限（50k）超限判失败而非静默截断；⑤空/过短/缺必需章节一律记 gate 失败、不得进入人审。已固化于 platform/graph.py `_extract_markdown` / `_doc_gate`，AC-02c 5 场景防回归。
  来源: A2 真实探针（2026-11-12）| 证据: 探针输出 doc_len=236300 → 修复后 392；AC-02c「日志行噪声被截断」「超长产物判失败」
- [C-009] 租约栅栏的 epoch 若按 Worker 自增，接管者的 epoch 可能小于旧持有者（旧 Worker 已领到 5，新 Worker 才从 1 起），旧 epoch 写入仍会通过守卫。epoch 必须全局单调（`SELECT MAX(lease_epoch)+1` 跨全部 node_runs），接管与抬升在 `BEGIN IMMEDIATE` 单事务内完成；所有状态/副作用写入（finish / operation / renew）必须带 epoch，不匹配或节点已结束时抛 `StaleLeaseError` 且不落库。
  来源: B1 租约实现（2026-11-12）| 证据: AC-02e「过期接管 epoch 严格递增」「旧 epoch 收尾/副作用/续租被拒且未落库」
- [C-010] 升级 SQLite 既有表的状态枚举（如 `retro_queue.status` 增 `dead`）时，`ALTER TABLE` 改不了 CHECK；且旧表缺新列时 `executescript(schema)` 里的 `CREATE INDEX IF NOT EXISTS` 会直接报 no such column。正确顺序：先 `ALTER TABLE ... RENAME` 旧表 → 重跑 schema 建新表 → 按公共列拷贝 → DROP 旧表 → 再建索引；`init_db` 必须可重复执行（二次 init 行数不变）。
  来源: B2 outbox 迁移（2026-11-12）| 证据: 旧 river 库 6 行迁移前后一致；AC-02f「超上限转死信」在迁移后库可写入 dead
- [C-011] outbox 的「认领 + 文件读改写」若不在同一互斥区，必现两类故障：同一候选被两个进程各写一份（条目重复），且 `UPDATE attempts=attempts+1` 无条件覆盖导致 attempts 少计。规避：逐条 `BEGIN IMMEDIATE` 写事务内完成 复核 → CAS 认领（`WHERE attempts=旧值`）→ 文件读改写 → 标 done；认领失败跳过、锁超时留 pending。
  来源: B2 并发补验（2026-11-12）| 证据: 修复前 2 进程×120 条出现 1 条重复且 attempts 全为 1；修复后 120 条唯一/0 重复，AC-02g 固化
- [C-012] 把 JSON 字段升级为独立表时，迁移必须是「幂等 + 可去重 + 保留代表值」：lane 计划存在多次执行（failed→done），回填若按行插入会产生重复 lane 且状态取到旧值。正确做法：按 `(task_id, lane)` 去重取最后一次执行，`merge_order` 由首次出现时间定序，`base_branch` 用前驱分支重建链，已存在的行不覆盖；`init_db` 重复执行行数不变。旧执行表的原字段保留为快照，不删列，避免回滚与历史查询断层。
  来源: B3 lanes 迁移（2026-11-12）| 证据: river 库 16 条带 lane 的 node_runs → 14 条 lanes，0 条 branch/file_scope 不匹配，二次 init 仍 14；AC-02h 8 条断言
- [C-013] 维护类「清理」动作必须默认只读并多依据判定活跃引用：仅比对工作目录与账本路径会误删「任务挂起但 lane 仍 pending」「节点刚启动尚未写账本」「worktree 名按分支替换生成」三类目录。规避：任务/节点/lane 状态 + 存活租约 + 待审批 + 分支/lane 同名 + 宽限期多重保留；孤儿只移入 `.trash/<时间戳>`，加 `--apply` 才动，过期清理再加 `--purge`。重试分级须以最新失败输出为证据并按尝试阈值升级；记忆回滚须同时删文件条目与改队列状态，否则审计会留下 missing_entry / stale_entry。
  来源: B4 维护清单（2026-11-12）| 证据: AC-07 5 条 + AC-08 7 条 + AC-11 7 条断言；river 只读审计 4 条 missing_entry（C-003～C-006）
- [C-014] 角色补全的两个安全边界：①测试角色不要用同一执行器「写测试 + 判通过」，会形成自证；正确做法是编排层按 project.yaml 跑独立全量门禁并落 TEST_REPORT，失败即阻断部署。②部署角色不能静默跳过：未配置 staging.commands 时必须二选一并留痕——`required=true` 转人审（deploy_config 失败门禁），`required=false` 记 deploy_skipped 通过门禁 + 报告写 SKIPPED。上游失败（gate_failed/awaiting_human）时 test/deploy 必须直接终止，不得启动或覆盖状态。
  来源: 测试/部署角色补全（2026-11-12）| 证据: ROLE-01 7 条 + ROLE-02 5 条断言；graph_smoke 10/10；ac_regression 149 条 PASS=147 SKIP=2
- [C-015] 管理面写操作必须与图执行解耦：HTTP 同步跑图会被 codex 长节点（单节点上限 1200s）拖死浏览器/代理。正确做法是「入队 → 202 + job_id → 前端轮询」，执行器单线程串行，避免同账本并发写与 checkpointer 竞争。动作结束若停在人工 interrupt，必须立即按产物哈希登记 pending 审批并转 `awaiting_human`，否则看板没有待审批入口（CLI 原语义要等下一次 approve 才登记）。写端点须带自定义头（X-Control/X-Intake）+ Origin 白名单并强制回环；`--db` 必须是 `<ws>/state.db`，否则图执行按工作区定位账本，会与只读快照落到两个库。
  来源: 管理控制台（2026-11-12）| 证据: CONSOLE-01 16 条断言（403/202/全闸门闭环/详情与产物接口）；ac_regression 165 条 PASS=163 FAIL=0 SKIP=2；graph_smoke 10/10；smoke_test 20/20
- [C-016] 控制台修改 project.yaml 不能 `yaml.safe_dump` 整文件重写（会丢注释与顺序）；应基于 `yaml.compose()` 的节点 mark 做定点替换。注意块序列的 `end_mark` 落在下一 token 前（含换行与缩进），替换前必须回退尾随空白，否则会把下一行键拼接到上一行。只允许编辑已存在的标量/序列节点，未知键直接拒绝，避免控制台「造配置」掩盖真实差异。写入用 tempfile + `os.replace` 原子替换并追加审计（actor/from→to）；保存后必须调用 `refresh_runtime_config()` 刷新模块级预算参数（环境变量优先），否则要重启进程才生效。
  来源: 角色配置管理（2026-11-12）| 证据: CONFIG-01 15 条断言；首版块序列替换把 healthcheck 行拼接到 commands 末项，修复后 YAML 解析与注释校验通过
- [C-017] 部署失败的常见根因在任务分支基线：implement 默认 base 必须是 `main`（旧默认 `feat/task-001-stats-fe` 会让新分支缺少 main 后续提交的 staging 资产）。把 infra 提交合入 main 后，已开出的任务分支仍需 merge main 才能部署。Windows 检出 + `core.autocrlf=true` 会把 `.sh` 变 CRLF，WSL bash 执行报 `set: -: invalid option`；仓库需 `.gitattributes`：`*.sh text eol=lf`（改属性后要删除并重新检出文件才生效）。LangGraph 图已到 END 的失败节点不能直接重跑：先把任务状态从 gate_failed 改回 running，再用 `app.update_state(cfg, {...}, as_node=<父节点>)` 后 `app.invoke(None, cfg)`。
  来源: 部署节点失败诊断（2026-11-12）| 证据: task b8c4104a attempt1 failed → attempt2 done；deploy_step_1/healthcheck passed=1；G3 经控制台批准后 retro done；ac_regression 180/178/0/2
- [C-018] codex 的 `workspace-write` 只覆盖工作目录，产出目录不要直接指到仓库外。
  做法：Agent 先写 repo 内 `.agentcode-prototype/<task8>/`，节点校验前移入 `projects/<proj>/<task_id>/prototype/` 并在 `finally` 清理暂存；G2 审批哈希用 `ledger.artifact_digest([design_doc, prototype_dir])`（目录按相对路径排序累加），原型变动即旧审批失效。
  来源: 原型/UI 角色落地（2026-11-12）| 证据: 真实 codex 冒烟 rc=0（2,638 B HTML / 298 B MD）；PROTOTYPE-01 10 条；ac_regression 190/188/0/2；graph_smoke 11/11
- [C-004] Trae 后台 shell 的 cwd 参数未生效。
  规避：启动项目目录内服务须在命令内 Set-Location。
  来源: task-001 G3 验收环境 | 证据: npm ENOENT 报错路径

## Best Practices

- [C-003] 说明书驱动的 lane 隔离有效：DESIGN.md 文件归属表 + scope 交集检查 + 明确 prompt，codex 双 lane 0 返工（task-001/002/003 九次 lane 执行仅 1 次返工且因平台 bug 而非需求歧义）。
  来源: task-001~003 | 证据: git diff vs DESIGN.md 全一致
