# 知识库日志

## [2026-11-11] setup | 初始化
- 确认 Obsidian 已注册此 Vault，使用共享本地文件维护。
- 建立索引、规则、模板与项目导航；保留欢迎笔记。
- 保存两份 HTML 快照，仅完成结构级阅读，全文摄取待完成。
- 未安装插件、未提交 Git、未更改 Issue。

## [2026-11-11] maintenance | 文档与决策规范
- 按用户要求扩展根 AGENTS.md，加入任务内文档沉淀、实施前后记录及独立判断要求。
- 明确权威来源、重复问答处理和批准证据；文档保存授权不等于后续实施批准。
- 新增规则调整记录并更新索引；验收结果见 [[wiki/文档与决策规范]]。

## [2026-11-11] plan | V2.1 文档修订启动
- 更新 [[wiki/方案演进]]，在改写前记录背景、范围、约束、拟议调整、授权证据和未知项。
- 已读取 Vault 规则、索引、相关知识页及来源；原 V2.0 已在本对话全文阅读。
- 使用 gh 查询 origin 全部状态 Issue，结果为空；未创建或修改 Issue。
- 本轮目标是更新方案文档；系统实现与运行验收不在本次授权范围。

## [2026-11-11] revision | V2.1 方案修订与验收
- 更新 [[wiki/方案演进]] 为 V2.1 中文综合提案，包含十五章、任务协议及十二项故障验收场景；保留历史建议的替代原因，状态为 draft。
- 同步 [[wiki/项目概览]]、[[wiki/待核实问题]]，将 V2.0 阅读范围更新为全文，保留原评审 HTML 尚未全文摄取的限制；记录 Issue 查询为空及待确认事项。
- 新增 [[sources/V2.1技术核验]]，记录七项一手资料、核验范围和用户文档授权；更新 [[sources/项目资料]] 的阅读版、快照、哈希、生成脚本及验证证据。
- 更新 [[index]]，复用已有方案主题；生成仓库 multi-agent-scheme-v2.1 下 HTML、render.ps1、validation.json 及桌面/手机预览。
- 仅新增 raw 快照，不覆盖已有快照。首轮预览保留，最终 r2 快照与最终 HTML 字节一致，V2.0 原文及其快照哈希未变化。
- 验收：严格 UTF-8 解码、Wiki 双链和相对文件链接通过；十五个目录目标、十七张表、十二项 AC 编号完整；桌面与手机无页面横向溢出，表格无纵向裁切，无浏览器脚本错误。详情见 [验证报告](../../multi-agent-scheme-v2.1/validation.json)。
- 已解决检查失败：自动标题 ID 与章节重名；旧样式限制长表高度；链接巡检命中欢迎页教学占位符，核对后明确排除，保留用户欢迎笔记。
- 未验证：AC-01～12 运行验收、平台实现、打印成品及其他浏览器。未完成人工方案核验，未修改 Issue 或发布部署。

## [2026-11-11] review | V2.1 方案评审
- 应用户要求全文评审 [[wiki/方案演进]]（V2.1）：确认十二项修正（LangGraph 中断/AND 语义、SemVer、撤包、inotify、预算预占、账本分离、租约、Retro 候选化、指标修正、渐进扩展、AC 场景）均有核验依据。
- 新增 [[wiki/V2.1评审]]：结论「有条件通过」，发现 F1（预算执行点，P0 阻塞）～F8 共八项，含各自建议；更新 [[index]]。
- 本轮仅评审与文档沉淀，未实现平台、未执行运行验收、未修改 Issue。

## [2026-11-11] revision | V2.2 修订（F1～F8 落实 + 预算事后计量）
- 应用户指令按评审建议重新生成方案：落实 F1～F8 全部八项；核心变更为预算模型按用户决策重写——不预占、事后记录用量，以边界控制（任务准入/节点边界/单任务上限/日对账）对冲，超支有界（≤ 在途数 N × 单任务上限 U）。
- 生成 [multi-agent-scheme-v2.2.html](../../multi-agent-scheme-v2.2/multi-agent-scheme-v2.2.html)（15 章完整方案，预算章重写，新增 AC-13/14 与 M1+ 里程碑）；新增 [[wiki/V2.2修订]] 记录决策与落实对照；更新 [[index]]。
- V2.1 及其快照未改动。本轮仅文档修订，未实现平台、未执行运行验收、未修改 Issue。

## [2026-11-11] rollout | P0a 启动确认（自动检测部分）
- 用户授权启动 P0a（V2.2 §13.2）。执行本地自动检测：OS=Windows 11（NT 10.0.26200）；执行器 codex 0.137.0 与 claude 2.1.197 已安装（dsh、docker 未装，均不阻塞）；git 2.54.0、node v24.16.0、Python 3.14.3 + sqlite3 3.50.4 就绪。
- 新增 [[wiki/P0a确认]]：五项退出标准进度（自动可验证项全过，剩余为人工输入）；U/N 初始值建议（N=1；订阅模式 200 calls/任务、API-key 模式 $5/任务）；待人工确认五项清单。更新 [[index]]。
- 未验证：kill/重跑最小验证（②）待 P1 首个真实任务执行时注入；认证模式、对账源、审批人、技术栈、配额待团队输入。未修改 Issue。
- 用户确认两项决策：目标 OS = Windows 本机；计量/认证模式 = 全部订阅登录（计量单位 call_count/token_count，配额语义转为订阅资源保护）。⑤ 关闭（N=1，U=200 calls/任务）；① 剩对账源可访问性验证。
- 生成 [p0a-forms.html](../../p0a-forms/p0a-forms.html)：表单 A 审批人登记（4 审批位 + 值守约定）、表单 B 凭据确认（三层模型 + 红线清单 + 平台层凭据盘点，含订阅登录特别说明）。打印友好浅色版式。更新 [[wiki/P0a确认]] 待确认项链接。
- P0a 收尾确认（用户答复）：审批人值守 = 单人值守（③ 实质完成，待表单 A 归档）；配额 = 20,000 calls/月（⑤ 补全）。提供对账源自查指引（①）与 kill/重跑手工最小验证步骤（②）。剩余：对账源验证结果、表单 A/B 签字归档、试点项目与技术栈输入。
- AC-02 执行（用户授权"先做①和②"）：test-ac02 任务 15:38:05 kill 注入（3/10 文件）→ codex supervisor 自愈续跑 → 15:41:44 完成 10/10，note04（中断时写入中的文件）完整无截断，② 通过。六项发现入 [[wiki/P0a确认]]：A npm 版 codex 0.137.0 订阅通道全模型不可用、须用桌面版 0.166.0-alpha.6（阻塞级）；B 执行器有 supervisor 进程自愈（kill 语义需修订 V2.2 §9.2/§8.4）；C codex exec 输出自带 tokens used（本地计量源）；D exec resume 不接受 -s；E 冷启动 2 分钟/热 36 秒；F harness 须按 PID 精确 kill。证据目录 test-ac02/ 保留。
- ①（对账源）：浏览器自动化验证 ChatGPT usage 页面——浏览器实例未登录（codex CLI 的订阅认证本身有效），待用户配合登录或自查；claude.ai 同待。
- P0a 关闭：用户回复「已验证」（① 关闭）；归档表单生成 [p0a-forms-filled.html](../../p0a-forms/p0a-forms-filled.html)（表单 A/B 全部已决策内容填入，电子确认依据注明，CI/staging 凭据标注按方案节奏补录）；③④ 关闭。试点项目经候选探测（6 个活跃项目的栈标识/规模比对）后用户确认 **proj-a = river**（TS monorepo，双 lane 天然对齐，门禁现成）。**P0a 五项退出标准全部关闭，正式关闭**。P1 前置待办：V2.2 §9.2/§8.4 kill 语义修订（发现 B）、桌面版执行器路径固化、凭据扫描接入。
- 环境更新（用户指出发现 A 归因有误——npm 版 codex CLI 版本过旧导致模型不可选）：`npm install -g @openai/codex@latest` 更新至 **0.167.1**；复测 **gpt-6.1-terra ✅**（OK，tokens 18,505）与 **gpt-6.1-sol ✅**（OK，tokens 20,962）。修正 [[wiki/P0a确认]] 发现 A 与 P1 执行器决策：npm 0.167.1 为主执行器，桌面版 0.166.0-alpha.6 为备选。

## [2026-11-11] rollout | P1 启动（Week 1 · 基础设施就绪）
- 前置修订完成：kill 语义（发现 B）修订入 [[wiki/V2.2修订]]——修订 1（L1 由执行器 supervisor 自愈承担，编排器改为检测；停止意图分软停/硬停）、修订 2（PAUSED 改为门禁层拒发 node_run，不依赖进程 kill）；待验证项三项列入 P0b/P1 实验。
- 最小协议基础设施落地（platform/）：[schema.sql](../../platform/schema.sql)（6 张必须表 + retro_queue/budget_state 附表，WAL）、[ledger.py](../../platform/ledger.py)（账本：任务/节点/幂等操作/审批哈希绑定/门禁/预算状态机 + PAUSED 拒发）、[meter.py](../../platform/meter.py)（事后计量：codex 输出 tokens 解析 → cost_ledger）。
- 试点配置：[projects/river/project.yaml](../../projects/river/project.yaml)（执行器 codex 0.167.1 主 / claude 备；模型 terra 默认 + sol 降级档；预算 20,000 calls/月、U=200、N=1；三闸门 + L1 单人值守；river 门禁命令映射）。
- 冒烟验证：[smoke_test.py](../../platform/smoke_test.py) **20/20 全过**——表创建、任务/node_run 生命周期、operations 幂等去重、审批哈希绑定、门禁失败留存、tokens 解析（9,663）、预算状态机四态推进（留痕含 NORMAL 起点）、PAUSED 拒发新 node_run（修订 2 行为验证）。
- river 正式账本初始化：projects/river/state.db（8 表就绪）。状态：**已实现**（未经历故障注入验证）；AC-02/09/13 的平台级脚本化验证待 P1 后续。未做：P0b 沙箱越界矩阵、双 lane 真实任务闭环、LangGraph 图接入。未修改 Issue。

## [2026-11-11] delivery | task-001 双 lane 闭环达成（M1 雏形）
- **task-001「牌桌状态统计」端到端闭环完成**：需求→[G1 人审]→设计→[G2 人审]→双 lane 实现（codex 0.167.1）→编排层门禁→[G3 人审浏览器验收]→retro。全程无人工代写码。
- 产物：lane-be `feat/task-001-stats-be`@1a64718（GET /api/stats 只读路由 + RiverStore.stats()，+67 行）；lane-fe `feat/task-001-stats-fe`@8821b36（fetchStats + StatsPanel 10s 轮询含 loading/error 态，+100 行）。双 lane 均一次通过、0 返工，改动与 DESIGN.md 文件归属表完全一致。
- 验收证据：typecheck 全绿；api 单测 68/68、web 单测 20/20（含新测试）；GET /api/stats 返回 200 + JSON（编排层 curl 实测）；**用户浏览器实测轨迹留存**（登录→建房→进桌，StatsPanel 每 10s 轮询全部 200，见 dev server 日志）；G3 口头确认「验收通过」。
- 账本终局：5 个 node_run 全 done（requirements/design/implement_api/implement_web/retro）；3 审批 approved（G1/G2/G3，哈希绑定）；4 门禁全 passed；用量 2 calls / 86,996 tokens（预算 NORMAL 2/20000）。
- Retro 候选 4 条入池（task-001 执行记录）：C-001 沙箱内 vitest EPERM→门禁须编排层执行；C-002 river 门禁命令映射（web 是 test 非 test:unit）；C-003 说明书驱动双 lane 0 返工（正面）；C-004 后台 shell cwd 不生效 workaround。
- P1 简化记录：staging 部署（docker 未装）以浏览器验收替代 G3——M1 的「到 staging」待 docker 就绪补验；lane-fe 自 lane-be 分支切出（获取接口类型上下文），偏离 DESIGN「自 main」一条，已记录。
- 剩余 P1 项：P0b 沙箱越界矩阵、LangGraph 图接入、Retro L0 自动采纳层实现。未修改 Issue。

## [2026-11-11] verify | P0b 沙箱越界矩阵（AC-06）+ L0 自动采纳层
- **AC-06 执行**（codex 0.167.1 workspace-write，test-ac06/，8,818 tokens）：越界写（绝对路径）BLOCKED（OS 级拒绝）；父目录写 BLOCKED；**网络访问 ALLOWED**（HTTP 200，沙箱 network 限制在 Windows unelevated 模式未生效）；**凭据文件 ALLOWED**（~/.codex/auth.json 可读 3,987 字节）；symlink 不可用（非提权环境无法创建，攻击面天然受限）。
- **方案影响（V2.2 修正项）**：① §6.4 权限矩阵「非白名单网络出阻断（OS 层）」不成立——防线退化为 prompt 约束 + 审计，标注为已知缺口；② §15.2「Worker 零长期凭据」受挑战：执行器自管会话凭据物理可读——缓解候选为独立 CODEX_HOME（同时解决 kill 语义待验证项 (c) 的 supervisor 隔离，一石二鸟，P1 内评估）。写边界（工作区外）经 OS 级验证成立。
- **L0 自动采纳层落地**：[memory.py](../../platform/memory.py)（分类 L0/L1、幂等去重、分段插入、条目带来源/证据/一键回滚标注）；执行采纳：C-001/C-002 → [projects/river/AGENTS.md](../../projects/river/AGENTS.md)（L0 自动生效）；C-003/C-004 → L1 留池待周会。修复过程中发现并修复分段插入 bug（条目未归属正确段落）。
- **LangGraph 图接入**：安装 langgraph + langgraph-checkpoint-sqlite；[graph.py](../../platform/graph.py)（双账本分离：图状态 graph.db / 业务事实 state.db；requirements→[G1 interrupt]→design→[G2 interrupt]→implement(占位)→retro；拒绝回环；节点与 ledger 联动）。[graph_smoke.py](../../platform/graph_smoke.py) **7/7 通过**：G1/G2 interrupt 挂起、**进程重启后状态存活（AC-03 图级语义）**、跑完终态 done 入账、拒绝路径回环。修复两处：SqliteSaver 需直接构造（非 from_conn_string 上下文管理器）、Command 导入。implement 节点为占位——双 lane Worker 调用接入为 P1 下一步（task-001 为手工执行基线）。
- **P1 Week 1 完成度**：最小协议 ✅ / 双 lane 闭环 ✅（task-001）/ P0b ✅（含两个方案修正项）/ L0 层 ✅ / LangGraph 骨架 ✅。待办：implement 节点 Worker 化、独立 CODEX_HOME 隔离评估（网络+凭据+supervisor 三合一）、docker 安装补 staging 验收、凭据扫描工具。未修改 Issue。

## [2026-11-11] delivery | P1 Week 2 · task-002（implement 节点 Worker 化）
- **Worker 化落地**：[worker.py](../../platform/worker.py)（codex 子进程封装 + 编排层门禁 + git 副作用）；graph.py implement 节点接入（双 lane 串行：分支创建→node_run→codex→门禁→修正循环(1次)→commit(幂等键)→计量→finish）。
- **task-002「uptime 字段」完成**：图驱动跑 G1/G2 interrupt 人审（用户批准）→ implement → G3 验收通过 → done。产物：lane-be @b534d6d（uptimeSeconds + 测试，+10/-3）、lane-fe @8fbc01e（uptime 展示 + 测试，+16/-3）。验收：typecheck 全绿、api 68/68、web 24/24、`GET /api/stats` 实测返回 uptimeSeconds:32。用量 4 calls / 50,449 tokens。
- **重要平台级发现 C-005（已修复）**：多行 prompt 作为 codex exec 命令行参数被截断（仅第一行生效），codex 以「需求不完整」正常退出且不报错 → 双 lane 空跑（各 ~7k tokens 无产物）。根因修复：worker.py 改 stdin 传 prompt（`-` 占位）。修复途中连带修复：Windows npm .cmd shim 须 shutil.which 解析；repo 路径计算错误；inspect.py 遮蔽标准库；implement_summary 未入 DeliveryState schema。C-005 已入 retro_queue（platform 级 → L1 人工审核）。
- **图驱动 vs 手工补完的诚实记录**：图首次 implement 因 C-005 空跑后以 retry 脚本补完（等效 attempt 重跑语义），账本补记齐全；下一次任务起图为修复后版本，端到端全图自动待 task-003 验证。
- P1 Week 2 剩余：独立 CODEX_HOME 隔离实验、docker/staging、gitleaks、task-003 全图自动验证。未修改 Issue。

## [2026-11-11] delivery | task-003（全图自动验证 · 两项平台缺陷修复）
- **task-003「version 字段」完成**（G1/G2/G3 全 approved，含一次授权复用的重放）：产物 lane-be @c4c7d4c（version 字段 + package.json 补 version 声明）、lane-fe @11a0367（版本徽标，链修复版）。验收：typecheck 全绿、api/web 单测通过、**接口实测 `{"...","uptimeSeconds":32,"version":"0.1.0"}`**。用量 3 calls / 45,819 tokens。
- **首跑空跑根因（C-005 同族）**：DeliveryState schema 未声明 `lanes`/`base_branch` 等键 → LangGraph invoke 时静默丢弃 → implement 收到空 prompt → codex "No prompt provided via stdin"（31 字节日志铁证）→ 空跑且门禁假绿。修复：schema 补全（total=False）+ 注释警示「所有经 state 传递的键必须声明」。
- **新发现 C-006（已修复）**：串行双 lane 分支链——fe 必须基于 be 分支（继承 commit）；原实现 be/fe 各自从 base 创建 → fe 缺后端改动、接口无 version 但门禁全绿（类型声明不校验运行时）。task-002 手工流程正确、图化时丢失。修复：graph.py prev_branch 链式创建；fe 补跑后端到端实测通过。**教训：端到端接口实测不可被门禁替代**。
- **M1 完全达成检验**：全图自动（requirements→[G1]→design→[G2]→implement 双 lane Worker→retro）在修复后跑通，summary 正确入 state；三次任务（001 手工 / 002 半自动 / 003 全图）构成编排能力递进证据链。
- L1 候选池现状：C-003/004/005/006（platform 级，待周会）。未修改 Issue。

## [2026-11-11] verify+review | CODEX_HOME 隔离实验 + gitleaks + L1 首次周会
- **CODEX_HOME 隔离实验**（platform/codex-home-iso/）：①独立 home 无凭据 → **401 快速失败**（Worker 无法读主会话凭据，AC-06 凭据缺口的可行缓解成立）；②+auth.json → 正常工作（OK，3,980 tokens），凭据副本验后即删。运维模式：Worker 启动注入专属凭据，泄漏面限于 Worker home。附观察：npm CLI + 独立 home 的 subprocess 同步管理未见桌面版 supervisor 干扰（kill 语义修订的补充证据，待正式验证）。待办转 P1 后续：worker.py 加 CODEX_HOME 参数。
- **gitleaks 接入**（winget 安装）：扫描 river 全部 10 commits / 888.92KB → **no leaks found**（exit 0）。表单 B 红线「凭据不落仓库」完成机器验证。扫描命令固化：`gitleaks detect --source <repo> --redact`。
- **L1 首次周会审核**（用户，AskUserQuestion）：C-003/004/005/006 **全采纳** → 写入 [shared/memory/platform.md](../../shared/memory/platform.md)（平台级记忆：2 条 Pitfalls + 1 条 Best Practice + 运维注意），retro_queue 全部归档（pending=0）。学习闭环 L0+L1 双层首次完整运转。
- P1 全景：三任务 done / CODEX_HOME 方案验证 / gitleaks 就位 / 双层学习闭环激活。剩余：docker/staging 补验（需用户参与安装）、worker.py CODEX_HOME 参数化。未修改 Issue。

## [2026-11-11] verify | Docker staging 验收 + Worker 凭据隔离参数化（P1 收尾）
- **staging 验收完成（5/5，已验证）**：Docker 实际位于 WSL Debian（Engine 26.1.5 / compose 2.26.1，Windows 经 `docker.cmd` 转发）。Docker Hub 在本机不可达（`registry-1.docker.io` 解析到 157.240.10.32，pull 超时），改用本地已有 `node:20-alpine`/`nginx:1.27-alpine`/`postgres:16-alpine` 构建，容器内 npm registry 正常。新增 river 交付物：`docker-compose.staging.yml`、`infra/docker/Dockerfile`（多阶段；api 运行时含 `infra/postgres/init.sql`，因 `persistence.migrate()` 按相对路径读取）、`infra/docker/nginx.conf`、`infra/docker/staging-smoke.sh`、`.dockerignore`。冒烟结论：静态页 200、`/api/stats` 返回 `version=0.1.0` 与 uptime、游客会话与建房成功、**api 重启后房间仍在**（Postgres 持久化）。修正两处测试自身缺陷：盲注取值须 ∈{10,20,50,100}；重启后轮询须判 HTTP 200（nginx 502 时 curl 仍退出 0）。环境约束记录：WSL 发行版在最后一个进程退出约 8s 后停机，staging 必须单会话内 build+up+冒烟。
- **Worker 隔离参数化（已实现 + 已验证）**：`worker.run_codex` 新增 `codex_home`/`log_dir`，注入 `CODEX_HOME`，缺 `auth.json` 时快速失败（不静默回退主 home）；`graph.py` 在 implement 两处调用传入隔离 home（`RIVER_CODEX_HOME` 可覆盖），日志改为 `<workspace>/<task_id>/logs/`；`project.yaml` 记录 `executor.codex_home`。正例实测 `rc=0 / tokens=3,379 / 125s`，隔离 home 内生成运行态文件证明 env 生效；负例返回明确 RuntimeError。`graph_smoke.py` 因 implement 已 Worker 化改为打桩，回归 7/7。
- **凭据扫描复核**：`gitleaks detect --source d:\project\AgentCode --no-git` 初扫 21 条 → 删除 `platform/codex-home-iso/.tmp`（2,449 个第三方插件缓存，17 条噪声来源）后仅剩 2 条，均为被 `.gitignore` 忽略的 `projects/river/codex-home/auth.json`。`git check-ignore` 确认排除生效；CI 接入需对 `projects/*/codex-home/` 加 allowlist（P2）。
- **新增知识页**：[[wiki/P1实施进度]]（含 staging 环境事实、验收表、未验证项）。P1 剩余：staging 编排待用户确认后提交 river 仓库；Windows 侧 8080 直连未验。未修改 Issue。

## [2026-11-11] verify | staging 提交 + Windows 直连复验（用户批准「执行吧」）
- **river 提交**：`81850f8 chore(deploy): docker staging compose, nginx proxy and smoke script (P1 M1 staging)`（5 文件 / +160 行；工作树干净）。分支 `feat/task-003-version-fe`。用户批准后提交，未推送远端。
- **Windows 侧直连复验（已验证）**：在 WSL 保持存活期间，`Invoke-WebRequest http://localhost:8080/api/stats` → **200**，`{"tables":0,"hands":0,"seatedPlayers":0,"uptimeSeconds":0,"version":"0.1.0"}`。结论：M1「到 staging」在 Windows 宿主可直接访问；常驻性受 WSL 空闲停机限制（8s 后 distro 停机 → 容器退出）。
- [[wiki/P1实施进度]] 已同步：交付物标注提交号、未验证项改为 WSL 生命周期约束、新增复验记录。未修改 Issue。

## [2026-11-11] implement | AC 回归 harness 固化（F6）+ 预算边界接入图
- **新增 `platform/ac_regression.py`**（AC-01/02/03/05/09/13/14 脚本化，AC-06/12 显式 SKIP 附手工命令）：首跑 40 条断言 → **PASS=37 FAIL=0 GAP=1 SKIP=2**（`python platform/ac_regression.py`，退出码 0）。每用例独立临时工作区与 state.db；崩溃按 FAIL 记录不中断其余用例。
- **为可自动化补的实现**：`ledger.approval_valid()`（AC-09 哈希判定）；`meter.record_usage_safe()` / `flush_buffer()`（AC-14 计量故障落盘缓冲 + 边界补记）；`graph.py` 节点边界控制（flush → `evaluate_budget(QUOTA=20000)` → 单任务上限 U=200 → start_node_run）、PAUSED 门禁层拒发转 `awaiting_human`、`route_model()` DEGRADED→`gpt-6.1-sol`；`retro_node` 守卫避免把预算暂停覆盖成 done。
- **修正的缺陷（由 harness 暴露）**：① 预算评估此前只存在于 CLI，真实图执行从不评估 → PAUSED 永不触发；② 单任务超 U 转人审后会被 retro 覆盖为 done，暂停不可观察；③ 计量写入失败会直接抛错中断主流程。
- **GAP**：AC-13 ①「80% 低优先级任务排队」需 `tasks.priority` 字段，未实现（建议随 P2 租约一起做）。**未覆盖**：AC-06（真实 codex，约 8k tokens，已有 P0b 手工矩阵）、AC-12（docker staging 构建）。
- 回归：`smoke_test.py` 20/20、`graph_smoke.py` 7/7 仍全绿。[[wiki/P1实施进度]] 新增第 6 节。未修改 Issue。

## [2026-11-11] implement | 关闭 AC-13 ①：tasks.priority + 预算紧张时低优先级排队
- **schema**：`tasks.priority TEXT NOT NULL DEFAULT 'normal' CHECK (high|normal|low)`；`init_db` 增加 `_migrate()`：旧库缺列时 `ALTER TABLE ... ADD COLUMN`（SQLite 无法补 CHECK，已在知识页标注）。river 正式账本已迁移，4 个历史任务均为 `normal`。
- **门禁**：`start_node_run` 在 ALERT/DEGRADED 且任务 `priority='low'` 时抛 PermissionError（排队），`normal/high` 不受影响；`create_task(priority=...)` 校验取值。
- **验收**：`ac_regression.py` 由 40 条扩为 **41 条断言，PASS=39 FAIL=0 GAP=0 SKIP=2**；AC-13 12/12 全过（含「ALERT 拒发低优先级」「ALERT 不影响普通任务」）。`smoke_test.py` 20/20、`graph_smoke.py` 7/7。
- [[wiki/P1实施进度]] 已同步：删除唯一 GAP，补充 priority 实现说明与迁移限制。未修改 Issue。

## [2026-11-11] implement | 常驻看板（P2 前移，用户指示「与功能同步实现」）
- **新增 `platform/dashboard.py`**：仅 stdlib（`http.server` + `sqlite3`），SQLite 以 `mode=ro` 只读打开，零第三方依赖；端点 `/`（内嵌单页，3s 轮询）、`/api/overview`（JSON 快照）、`/healthz`、`--print`（巡检/CI 取 JSON）。
- **数据面**：tasks / node_runs / approvals / gate_results / cost_ledger / budget_state / retro_queue / `usage_buffer.jsonl`（AC-14 观测口）；配额与阈值从 `project.yaml` 正则读取并回退默认。
- **端口事实（已验证）**：`netsh ... show excludedportrange` 显示本机保留段含 `8766-8865`、`9005-9204`；原计划 8787 及 8788–8792 全部 `WinError 10013`，**8879 可绑定**，故默认端口改 8879，并加「失败向后重试 6 个端口」。
- **验收**：`/api/overview` 200（10,251 B，state=NORMAL calls=11/20000 tasks=4 runs=21 buffer=0）、`/` 200（7,602 B）、`/healthz` `{"ok":true}`；`--print` 输出同源快照。
- **限制**：仅验证 HTTP 层，未做浏览器目视；无鉴权，依赖绑定 127.0.0.1；列表取最近 30 条不分页。**新增知识页** [[wiki/平台看板]]，index 已收录。未修改 Issue。

## [2026-11-12] plan | P2 规划落档（含用户批准的差距排入）
- **用户决策**：P1 末审计的三项差距（requirements/design 未 Agent 化、prompt 无模板层、`_templates/` 缺失）排入 P2；原定 P2 内容不变。
- **新增 [[wiki/P2规划]]**：四组任务——P2-0 入场验证（task-004 真实端到端）/ A 组模板层与需求侧 Agent 化（新增）/ B 组协议完整（租约 epoch、outbox、lanes 表、AC-07/08/11、看板扩展）/ C 组第二项目与运维（proj-b、权限清单、gitleaks CI、镜像治理、supervisor 自愈）。排期建议 P2-0→A→B→C，预计 2-3 周（原定 1 周，因范围扩大）。
- **开放问题**：proj-b 候选仓库待用户指定；需求 intake 形态待定；模板参数化边界（倾向保留覆写口）。index 已收录。未修改 Issue。

## [2026-11-12] implement | task-004 暴露 C-007 + C-006 扩展修复（静默空交付穿透三层防线）
- **根因链**：CODEX_HOME 隔离 Worker 仅复制 auth.json → 缺项目信任配置（codex 将 river 视为不受信目录、降级只读）+ 缺 `[windows] sandbox=unelevated`（powershell.exe 被 CreateProcess policy 拒绝）→ Worker 静默空交付（codex 报告 hallucination "Added startedAt..." 但文件不存在）→ 门禁在旧代码假阳性全绿 → `git commit` 空提交失败被 graph.py 忽略 → 账本记 done。**三层防线全穿透**。
- **C-007 修复（三重防线）**：① `projects/river/codex-home/config.toml`：最小配置（仅项目信任 + windows unelevated），**不继承**主 home 的 sandbox_mode=danger-full-access（Worker 权限上限由编排层 `-s workspace-write` 决定）。冒烟验证：`SMOKE_FILE=PASS content=ok`。② `platform/graph.py` 门禁前 `git status --porcelain` 空交付检测 → 记 `worker_no_changes` 门禁、lane 判 failed；③ `git commit` 返回值检查；④ retro 守卫扩展至 gate_failed/failed（终态失真掩盖交付失败）。新增 AC-02b（4 条断言固化防回归）。
- **C-006 扩展修复**：分支已存在（前次空运行残留）时不能复用错误 base。`git checkout -b` 改为 `git checkout -B`——无论分支是否已存在都强制基于 prev_branch 重建。
- **账本修正**：task-004（首跑）→ status=failed；implement_api/implement_web → failed；空 git_commit 操作 → failed；retro 保留 done（执行事实）。
- **回归**：`ac_regression.py` 45 断言 PASS=43 FAIL=0（AC-02b 4 条新增）；`graph_smoke.py` 7/7；`smoke_test.py` 20/20 全绿。
- **沉淀**：`shared/memory/platform.md` 新增 C-007、C-006 扩展。[[wiki/P2规划]] 已同步 P2-0 入场验证进展。未修改 Issue。

## [2026-11-12] verify | P2-0 task-004 终验通过（三轮迭代）
- **三轮执行**：首跑（3a6325e7，空交付被 C-007 guard 拦下前账本已记 done → 手动修正为 failed）→ retry（e9467903，be 成功但 fe 分支 base 错误 → C-006 checkout -B 修复）→ retry-v2（37e61996，**全链路跑通**）。
- **终验五维全过**：账本完整性（5 节点 done + 审批哈希绑定 + 4 门禁）；git 分支链（fe merge-base=be commit a989639，双 lane 各自 commit）；接口实测（startedAt ISO 8601 UTC + uptimeSeconds 自洽 delta=0）；看板可见（calls=17/20000）；预算门禁路径未触发（17/20000 远低于阈值）。
- **账本瑕疵修复**：2 条孤儿 running node_runs（进程中断未 finish）→ 修正为 failed + 补 ended_at。
- **遗留**：approvals 表无唯一约束导致重试时重复登记；CODEX_HOME config.toml 含项目路径明文未入 gitignore。
- [[wiki/P1实施进度]] 新增第 7 节 P2-0 终验。[[wiki/P2规划]] 标注 P2-0 已完成、A 组待启动。index 已同步。

## [2026-11-12] implement | A1 prompt 模板层落地（v1.0.0）
- **新增 `_templates/` 目录**：`manifest.yaml`（SemVer + 参数表 + required/optional 标记）+ 4 模板文件——`implement-lane.tmpl`（Worker 外壳：统一 vitest EPERM 约束、scope、report 结构）、`gate-fix.tmpl`（graph.py 修正循环用）、`requirements-gen.tmpl` / `design-gen.tmpl`（A2 预留，先建好模板基础设施）。
- **新增 `platform/templates.py`**：`render(name, **params)` 用 `string.Template.substitute`（**非 safe_substitute**——C-005 教训：缺参直接抛 KeyError，禁止静默丢 `$param`）；`snapshot(task_dir)` 幂等写 `task-XXX/templates.lock`（JSON：manifest 版本 + 各模板版本号 + 时间戳）；列表类型参数自动 join。
- **graph.py 改动三处**：① 顶部 import templates；② gate-fix prompt 从内联 f-string → `tmpl_render("gate-fix", ...)`；③ implement-lane escape hatch：`lane.prompt` 非空 → 直接用（逃生口，旧任务脚本不受影响），为空 → `lane.lane_instruction` 走模板渲染。同时在 implement_node 开头幂等 snapshot templates.lock。
- **默认 LANES 修正**：原 `prompt: state.get("be_prompt", "")` 空字符串占位导致新 escape hatch 触发 → 改为非空占位符（显式逃生口语义）。
- **回归全绿**：`ac_regression.py` PASS=43 FAIL=0 SKIP=2（无变动）、`graph_smoke.py` 7/7（默认 LANES 占位符兼容）、`smoke_test.py` 20/20（无变动）。templates.lock 实测生成正确（`lock exists: True` + 4 模板版本 JSON）。
- **迁移策略**：task-005 之后的新任务走模板（给 `lane_instruction` 而非写整段 prompt）；task-001~004 不回改（已有硬编码 prompt 继续生效，逃生口自动绕过模板）。
- **[[wiki/P2规划]]** A1 状态改为 ✅ 已完成，实施要点从草案更新为实际落地记录（含 substitute vs safe_substitute 修正）。index 已同步。

## [2026-11-12] implement | A2 requirements/design 节点 Agent 化
- **graph.py**：`requirements_node` 改为读 `state.requirement` 原始文本 → `requirements-gen` 模板渲染 → codex 生成 → 抽取落盘 `task-XXX/docs/REQUIREMENTS.md`；`design_node` 读需求文档全文 → `design-gen` → `DESIGN.md`。两节点共用 `_run_doc_agent`（预算 flush/evaluate/route_model、attempt 计数、node_run 收尾、模板快照）。
- **新增产物防线（C-008）**：`_extract_markdown` 三重截断——`tokens used` 统计段、首个 `# ` 一级标题、ISO 时间戳日志行；`_doc_gate` 校验长度区间 80~50k 与必需章节。失败即 `gate_failed` + node_run `failed` + 条件边直达 END，**不进人审**。空需求/需求文档不可读同样在边界拦下留痕。
- **真实缺陷发现（探针）**：真实 codex 输出并不干净——stdout/stderr 拼接后正文末尾混入大量 INFO 日志，首版抽取把 236KB 日志当文档写入。修复后同一份真实日志抽取为 392 字符合法文档。该教训入 L1 [C-008]。
- **行为边界**：预算 PAUSED/低优先级拒发 → `awaiting_human`；codex 抛错 → node_run `failed`，杜绝孤儿 running。
- **测试**：AC-02c 新增 13 条断言（空需求不调 Worker、垃圾产物不进 G1、node_run 不残留 running、合法产物落盘 + output_path + templates.lock、日志噪声截断、超长产物判失败）；ac_regression 58 条 PASS=56 FAIL=0 SKIP=2、graph_smoke 7/7、smoke_test 20/20。
- **[[wiki/P2规划]]** A2 标注 ✅ 已完成并补「A2 实际落地」小节；`shared/memory/platform.md` 新增 C-008。index 已同步。

## [2026-11-12] implement | A2 intake 结构化表单（网页表单 · 看板写模式）
- **决策**：用户在三种 intake 形态中选择「网页表单」。为避免破坏看板只读边界，写能力做成显式开关 `--intake`。
- **新增 `platform/intake.py`**：必填/枚举/长度校验（标题、目标、验收标准、非目标、be/fe 范围、优先级），失败抛 `IntakeError` 且不落库；`normalize()` 生成需求文本；`create_task_from_payload()` 建任务 + 写 `<task_id>/requirement.json`（结构化全量）；附 CLI `create payload.json`。
- **`platform/dashboard.py`**：`--intake` 开启 `GET /intake`（内嵌表单页）与 `POST /api/intake`；防护 = 绑 127.0.0.1 + 必带 `X-Intake: 1` + Origin 白名单 + 请求体 ≤64KB + `OPTIONS` 不应答预检；首页状态标签与「＋ 新建需求」入口随开关变化；默认仍 `mode=ro` 只读。
- **新增 `platform/run_task.py`**：通用执行器 `start/approve/reject/status`；挂起时按产物 sha256 登记 `approvals`，approve/reject 写入审批结论；线程 id = task_id。
- **`platform/graph.py`**：实现节点 lanes 缺省时解析 `DESIGN.md` 文件归属表生成 `feat/<task8>-<lane>`（intake 任务无需手写脚本），解析失败回退默认 lanes。
- **验证**：临时库 HTTP 冒烟——`GET /intake` 200、无头 POST 403、合法 201（requirement.json 存在、tasks=1）、缺标题 400；常驻实例重启为 `--intake`（8879）并核验 `/api/overview`（tasks=7）。AC-02d 新增 14 条断言（4 类拒绝 + 不落库 + 归一化 + json 落盘 + DESIGN→lanes）；回归 ac_regression 72 条 PASS=70 FAIL=0 SKIP=2、graph_smoke 7/7、smoke_test 20/20。
- **[[wiki/平台看板]]** 更新定位/端点/写模式/证据；**[[wiki/P2规划]]** 追加 intake 小节。index 已同步。

## [2026-11-12] implement | B1 workers 表 + 租约 epoch（AC-04 首验）
- **schema**：新增 `workers` 表（role/host/pid/status/lease_epoch/lease_expires_at/last_heartbeat_at）；`node_runs` 增 `worker_id / lease_epoch / lease_expires_at / heartbeat_at`。`ledger._migrate` 对旧库幂等 ALTER；river 库已迁移（`ledger.py init` → 9 张表含 workers，node_runs 新列齐全）。
- **租约 API（`platform/ledger.py`）**：`register_worker` / `acquire_lease` / `renew_lease` / `release_lease`。接管在 `BEGIN IMMEDIATE` 内原子完成：存活租约抛 `LeaseHeldError`，过期或同 Worker 可接管；epoch 取全表 MAX+1，接管者严格大于旧持有者。
- **栅栏写入**：`finish_node_run(worker_id, epoch)`、`record_operation(..., lease_epoch)` 在 epoch 不匹配或节点已结束时抛 `StaleLeaseError` 且**不落库**；旧 epoch 的续租/释放同样被拒。
- **graph 接入**：requirements / design / implement / retro 统一 `_start_leased_run` + `_close`；租约失效时旧 Worker 不再改写任务状态。凭据按 Worker 隔离：`<task>/workers/<worker_id>/codex-home` 复制 auth.json/config.toml，基目录缺文件不造空壳（C-007 防线）。
- **看板（B5 随 B1 上线）**：`/api/overview` 新增 `leases`；首页新增「Worker 租约」面板（持有者/角色/host/epoch/心跳/到期/状态）。
- **验证**：AC-02e 17 条断言全 PASS；回归 ac_regression 89 条 PASS=87 FAIL=0 SKIP=2、graph_smoke 7/7、smoke_test 20/20；常驻看板 `/api/overview` 含 `leases`（0 条）。**未验证**：真实双进程接管演练（AC-04 仍在 P1 范围外清单）。
- 文档：[[wiki/P2规划]] B1 标 ✅ 并补落地小节；[[wiki/平台看板]] 增租约面板章节；无新增页面，index 无需改动。

## [2026-11-12] implement | B2 完整 outbox（至少一次投递 / 可重放 / 不重复）
- **schema/迁移**：`retro_queue` 升级为 outbox——`status` 增 `dead`，新增 `attempts / next_attempt_at / last_error / delivered_at`。SQLite 不能改 CHECK，旧库由 `ledger._rebuild_retro_queue` 重建并保留数据（river 6 条 done 原样迁移，二次 init 幂等）；索引改在迁移后创建（旧表缺列时建索引会先失败）。
- **投递引擎（`platform/memory.py`）**：`deliver()` 先 `attempts+1` 提交再投递（至少一次）；失败记 `last_error` + 指数退避（30s→300s 封顶），`force=True` 立即重放；超 `max_attempts` 转 `dead` 死信；`[id]` 标记保证重放不重复，崩溃在「已写、未标 done」之间会补标 done。L1 保持 pending 留池。
- **CLI**：`memory.py deliver <db> <dir> [--force] [--dry-run] [--max-attempts N]`；`adopt` 保留为 `deliver(force=True)`；`pool` 展示 attempts/next/error。
- **看板（B5 随 B2 上线）**：`/api/overview` 新增 `outbox`；首页「Retro 投递（outbox）」面板（状态/尝试/退避/最近错误/投递时间）。
- **验证**：AC-02f 16 条断言全 PASS；回归 ac_regression 105 条 PASS=103 FAIL=0 SKIP=2、graph_smoke 7/7、smoke_test 20/20；river 库迁移后看板 outbox=6 条 done。**未验证**：多进程并发投递互斥（至少一次 + 幂等去重，非严格恰好一次）；磁盘满/权限位故障。
- 文档：[[wiki/P2规划]] B2 标 ✅ + 落地小节；[[wiki/平台看板]] 增 outbox 面板章节；记忆 [shared/memory/platform.md](../../shared/memory/platform.md) 追加 C-010。

## [2026-11-12] verify+fix | B2 outbox 跨进程并发投递互斥
- **方法**：两个独立 Python 进程在同一启动时刻向同一队列（120 条候选）投递，统计 AGENTS.md 唯一/重复/丢失条目与 `retro_queue.attempts`。
- **修复前**：C-CONC-000 被写 2 次（重复条目）；两进程 `attempts` 均为 1（后写覆盖，实际投递 2 次），证实读-改-写无互斥。
- **修复**：`memory.deliver` 逐条 `BEGIN IMMEDIATE` 写事务内完成「复核 → CAS 认领（`UPDATE ... WHERE attempts=旧值`）→ 文件读改写 → 标 done」；认领失败计 `claimed_by_other`，锁超时计 `locked` 并保持 pending。
- **修复后**：120/120 唯一、0 重复、0 丢失、attempts 全 1；竞争进程 `claimed_by_other=120`、`locked=0`。
- **回归**：新增 AC-02g（40 条 × 2 进程，5 条断言）；ac_regression 110 条 PASS=108 FAIL=0 SKIP=2、graph_smoke 7/7、smoke_test 20/20。
- 文档：[[wiki/P2规划]] B2 增「并发互斥（补验）」并更新边界；记忆 C-011。**未验证**：多机/共享盘（SQLite 写锁不跨主机）、磁盘满/权限位。


## [2026-11-12] implement | B3 lanes 独立表 + 合并顺序编排
- **schema**：新增 `lanes` 表（id=`<task_id>:<name>` / node_name / branch / base_branch / file_scope / merge_order / status / merged_at），唯一索引 `(task_id,name)`、顺序索引 `(task_id,merge_order)`；`node_runs.lane` 保留为执行快照。
- **迁移（`ledger._backfill_lanes`）**：`init_db` 幂等回填——按任务内 lane 首次出现定 `merge_order`，同 lane 多次执行取最后一次为代表，`base_branch` 串前驱分支链，已存在行不覆盖。
- **API**：`register_lanes`（幂等计划登记）、`set_lane_status`（非法状态 ValueError）、`mark_lane_merged`、`lane_merge_plan`。
- **graph**：实现节点登记 lane 计划；`_start_leased_run` 成功后 running，commit+收尾后 done，分支重建/空交付/门禁/commit 失败置 failed；后继 lane 基于前驱分支重建成功即标记前驱并入，末 lane 收尾时标记并入。
- **看板（B5 随 B3 上线）**：`/api/overview` 新增 `lanes`；首页「Lane 计划（合并顺序）」面板（任务/lane/顺序/分支/基线/状态/并入时间）。
- **验证**：AC-02h 8 条断言全 PASS；回归 ac_regression 118 条 PASS=116 FAIL=0 SKIP=2、graph_smoke 7/7、smoke_test 20/20。river 库迁移：16 条带 lane 的 node_runs → 14 条 lanes（7 任务 × be/fe），branch/file_scope 0 不匹配，二次 init 仍 14；常驻看板 `/api/overview` 返回 `lanes=14`。
- **未验证**：lane 真并行执行（当前串行 be→fe）；跨任务同名 lane 冲突；历史 lane 的 `merged_at` 不可回填。
- 文档：[[wiki/P2规划]] B3 标 ✅ + 落地小节；[[wiki/平台看板]] 增 Lane 计划面板章节；记忆 [shared/memory/platform.md](../../shared/memory/platform.md) 追加 C-012。

## [2026-11-12] implement | B4 半自动维护清单（AC-07/08/11）
- **模块**：新增 [platform/maintenance.py](../../platform/maintenance.py)，四项命令统一「先清单、后动作」：`worktrees`（AC-07）、`retry`（AC-08）、`memory-audit` / `memory-rollback`（AC-11）；默认只读，移动/删除需显式执行。
- **AC-07 孤儿 worktree**：保留判定 = 任务 pending/running/awaiting_human + 节点 running/awaiting_human + lane pending/running + 存活租约 + 待审批 + 分支/lane 同名 + 24h 宽限期；孤儿默认只报告，`--apply` 移入 `.trash/<时间戳>/`（只移动不删除），`--purge` 才清理超过保留期的回收站。
- **AC-08 重试分类**：以最新门禁输出为证据按 V2.2修订 §9.2 分 L1/L2/L3（瞬时/执行器/人工），尝试达阈值升级 L3；任务已 done/cancelled 的历史失败不计入；每项附证据片段与建议动作。
- **AC-11 记忆审计与回滚**：审计 `missing_entry` / `unknown_source` / `stale_entry` / `format` / `duplicate`；回滚按 ID 删除条目及续行，队列记录置 `dropped` 并写 actor 留痕；人工条目补 dropped 记录。
- **验证**：AC-07/08/11 共 19 条断言全 PASS；回归 ac_regression 137 条 PASS=135 FAIL=0 SKIP=2、graph_smoke 7/7、smoke_test 20/20。
- **river 只读审计证据**：worktree 根 `projects/river/.worktrees` 不存在（0 孤儿）；retry 3×L1（gate_failed×1、failed×2）、0 L2/L3；memory-audit 4 条 missing_entry（C-003～C-006），按清单原则未自动回写，留待人工确认。
- **未验证**：worktree 命名约定仅覆盖分支/lane 替换名；跨盘/符号链接未测；重试分类为关键字规则；审计不递归子目录；回滚无二次确认。
- 文档：[[wiki/P2规划]] B4 标 ✅ + 落地小节；[[index]] 更新 B 组完成范围；记忆 [shared/memory/platform.md](../../shared/memory/platform.md) 追加 C-013。

## [2026-11-12] implement | 补全测试与部署角色（test / deploy / G3）
- **流程**：`implement → test → deploy → review_g3 → retro`；G3 拒绝退回实现，批准后 retro。
- **测试角色**：新增 `test` 节点，编排层按 `project.yaml test.gates` 跑全量门禁并产出 `TEST_REPORT.md`；不调用 codex，避免测试 Agent 自证；失败即 `gate_failed(node=test)` 且不进入部署。
- **部署角色**：新增 `deploy` 节点，按 `deploy.staging.commands` 执行 staging + 冒烟/健康检查，产出 `DEPLOY_REPORT.md`，成功登记 `deploy_staging` 操作；未配置时 `required=true` 转人审、`required=false` 显式跳过并留痕。
- **G3**：新增 `review_g3` 人工闸门（审批物为部署报告）。
- **配置**：river `project.yaml` 增 `test.gates` 与 `deploy.staging`（当前 required=false）。
- **验证**：ROLE-01 7 条 + ROLE-02 5 条断言 PASS；全量 ac_regression 149 条 PASS=147 FAIL=0 SKIP=2、graph_smoke 10/10、smoke_test 20/20。
- **未验证**：真实 docker/staging（AC-12 仍 SKIP）、e2e 默认未启用、部署回滚/蓝绿未实现、healthcheck URL 轮询未实跑。
- 文档：[[wiki/P2规划]] 角色补全小节；[[wiki/平台看板]] 测试/部署角色面板说明；记忆 [shared/memory/platform.md](../../shared/memory/platform.md) 追加 C-014。

## [2026-11-12] config | 启用 river staging 部署配置
- `projects/river/project.yaml` 的 `deploy.staging` 由 `required: false` 改为启用：单条命令在同一 WSL 会话内完成 `docker compose up -d --build` → `staging-smoke.sh`（`SMOKE_RESTART=1`），健康检查 `http://localhost:8080/api/stats`，超时 900s。
- **校验**：YAML 解析通过；用 `worker.run_gate` 原样执行命令的 `config --quiet` 变体 PASS（验证 cmd→wsl→bash 引号链）；`_healthcheck` 对运行中的看板 `127.0.0.1:8879/api/overview` 返回 HTTP 200 PASS。
- **未执行**：未触发真实 staging 构建/启动（避免未授权副作用）；首次部署由任务或手工触发。
- 文档：[[wiki/P2规划]] 配置与边界更新。

## [2026-11-12] implement | 看板升级为管理控制台（提需求 / 审批 / 节点监控）
- **入口**：`platform/dashboard.py --control`（`--intake` 兼容别名）；默认仍只读，控制台强制回环 + `--db <project>/state.db`，启动时校验 langgraph 依赖。
- **提需求**：`/intake` 表单新增「创建后立即启动」，创建后直接入队 `start`，无需切 CLI。
- **审批**：新增 `POST /api/task/start`、`POST /api/task/decide`（approved/rejected）；复用 `run_task.py` 的闸门映射与产物 sha256 绑定；HTTP 返回 202 + job_id，图执行在后台单线程队列串行完成；动作结束若挂起 G1/G2/G3，自动登记 pending 审批并转 `awaiting_human`。
- **监控**：`/api/overview` 新增 `task_steps` / `gate_states` / `gate_artifacts`；任务表渲染 9 步流程胶囊；`/api/task/detail` 返回节点时间线/门禁/审批/四类产物；`/api/artifact` 限任务目录读取报告正文；`/api/jobs` 展示执行队列。
- **防护**：`X-Control: 1`（intake 兼容 `X-Intake: 1`）+ Origin 白名单、64KB、400/403/409；不响应 OPTIONS 预检。
- **验证**：CONSOLE-01 16 条断言全 PASS（控制台启动→G1 驳回→G1/G2/G3 批准闭环 + HTTP 层）；全量 `ac_regression` 165 条 PASS=163 FAIL=0 SKIP=2、`graph_smoke` 10/10、`smoke_test` 20/20；常驻 `127.0.0.1:8879 --control` 页面 `control=true`，`/api/overview` 含 7 个任务的流程状态。
- **未验证**：浏览器目视/交互回归；与 CLI 混用的跨进程互斥；多任务并行执行。
- 文档：[[wiki/平台看板]] 定位/端点/控制台/限制更新；[[wiki/P2规划]] B5 标 ✅ + 管理控制台小节；记忆 [shared/memory/platform.md](../../shared/memory/platform.md) 追加 C-015。

## [2026-11-12] implement | 角色配置管理（控制台编辑 project.yaml）
- **新增 `platform/config_store.py`**：`load` / `validate` / `apply_updates`；用 `yaml.compose()` 节点 mark 定点替换标量/流式序列/块序列，保留中文注释与缩进；未知键/缺失节点/映射叶拒绝；合并后整体校验，失败不落盘；tempfile + `os.replace` 原子替换；变更追加 `<workspace>/config-audit.jsonl`。修复块序列 `end_mark` 含下一行换行/缩进导致键被拼接的问题。
- **`platform/graph.py`**：新增 `refresh_runtime_config()`（环境变量优先，刷新 `QUOTA` / `TASK_CALL_LIMIT`），在 `build_app()` 与控制台动作执行前调用；`route_model(state, role)` 读 `model.default/degraded/strong_roles`；`_repo_path()` 读 `repo_path`；`_base_codex_home()` 读 `executor.codex_home`（环境变量优先）。
- **`platform/dashboard.py`**：新增 `GET /config`（分区表单，只读模式禁用）+ `GET /api/config`（当前值/可选项/最近审计）+ `POST /api/config`（仅 `--control`，`X-Control: 1` + Origin 白名单，失败 400 不落盘）；页头常驻「配置」入口。
- **验证**：CONFIG-01 15 条断言（4 类非法更新被拒且文件不变；合法保存后注释保留；审计含 from→to；QUOTA/U、`route_model` 强弱角色、`_repo_path`、`_base_codex_home` 生效；HTTP 403/400/200）；全量 `ac_regression` 180 条 PASS=178 FAIL=0 SKIP=2、`graph_smoke` 10/10、`smoke_test` 20/20。
- **未验证**：仅编辑既有节点（新增配置块须手工改 YAML）；模型名不校验真实存在；`gates` approver/SLA 仅登记展示；同一 YAML 跨进程并发写未加锁；未做浏览器目视回归。
- 文档：新增 [[wiki/角色配置]]；[[wiki/平台看板]] 增端点表与角色配置章节；[[wiki/P2规划]] B5 范围扩展 + 落地小节；index 已同步；记忆 [shared/memory/platform.md](../../shared/memory/platform.md) 追加 C-016。

## [2026-11-12] fix | 部署节点失败：stale base 分支 + CRLF 脚本
- **诊断**：task b8c4104a deploy 报 `open /mnt/d/project/river/docker-compose.staging.yml: no such file or directory`。根因 ① implement 默认 base 硬编码 `feat/task-001-stats-fe`，staging 资产（`81850f8`）只存在于 task-003/004 链，不在该分支与 `main`；② 补齐后 smoke 脚本因 CRLF 在 WSL bash 下语法失败（`core.autocrlf=true`，仓库无 `.gitattributes`）。
- **修复**：river `main` 快进到 `feat/task-004-startedat-fe` 并合入任务分支；新增 `.gitattributes`（`*.sh text eol=lf`，`e223837`）并重新检出；`platform/graph.py` implement 默认 base 改 `main`。
- **恢复**：deploy `update_state(as_node="test")` → `invoke(None)`；attempt=2 `done`，`deploy_step_1` / `deploy_healthcheck` PASS，DEPLOY_REPORT PASS；G3（id=15）经控制台批准（09:24:53），retro 完成，任务 `done`。
- **回归**：`ac_regression` 180 条 PASS=178 FAIL=0 SKIP=2；`graph_smoke` 10/10；`smoke_test` 20/20。
- **遗留**：控制台/CLI 无失败节点重试入口（本次用脚本恢复）；`.gitattributes` 仅约束 `*.sh`。
- 文档：[[wiki/P1实施进度]] §8；记忆 [shared/memory/platform.md](../../shared/memory/platform.md) 追加 C-017。

## [2026-11-12] qa | G3 批准后的执行路径与 retro 作用
- 代码核对（[platform/graph.py](../../platform/graph.py)）：G3 批准 → `tasks.status=running` → `goto retro`；驳回 → `goto implement` 重跑 test/deploy/G3；`deploy_skipped=true` 直接进 retro；deploy 失败无报告则 `END` 并停 `gate_failed`。
- `retro_node` 现状：仅做终态守卫（`awaiting_human`/`gate_failed`/`failed` 不改写）、登记租约化 `node_runs` 并关闭 `done`、把任务置 `done`（`current_node='retro'`）；**不生成复盘内容、不写 `retro_queue`**。V2.2 §10.3 的 L0 采纳 / outbox 投递由 `platform/memory.py adopt|deliver` 独立触发。
- 结论：retro 是流程盖章点；记忆回写尚未接入图节点，属已知未接入项。
- 文档：[[wiki/平台看板]] 新增「G3 批准之后与 retro 的现状」小节。

## [2026-11-12] qa | 设计节点（design）的产出范围
- 核对 `design-gen` 模板（[_templates/prompts/design-gen.tmpl](../../_templates/prompts/design-gen.tmpl)）与 `design_node`：产出为 `DESIGN.md` 三节——文件归属表（路径 → lane）、实现策略、验收映射，用于驱动 implement 的分 lane 与串行顺序。
- 结论：该节点是**技术实现设计**，不含原型/线框图/视觉设计稿；G2 审批物即这份 Markdown，UI 改动由 `fe` lane 在 implement 阶段直接落地。
- 文档：[[wiki/P2规划]] A2 小节补充设计范围说明。

## [2026-11-12] qa | 原型/UI 角色在实现中的落点
- 核对三处：① `REQUIREMENTS.md` 模板与实例只有「需求 / 验收标准 / 非目标」，无原型章节；② `/intake` 字段为标题 / 目标 / 验收标准 / 非目标 / lane / 优先级（[platform/intake.py](../../platform/intake.py)），无原型输入；③ [[wiki/方案演进]] §06 角色表确有「原型/UI」职责（界面变化需验证 → 原型及设计说明 → 写专属原型目录），并允许小型后端任务跳过 UI。
- 结论：方案定义了该职责，实现未落地为节点；`KNOWN_ROLES` 中没有原型/UI，`design` 是架构设计（文件归属表），`review` 是独立审查。UI 类需求当前无原型可审，属已知差距（P2 及以上候选），非本次缺陷。
- 文档：[[wiki/P2规划]] 角色补全小节新增「与方案角色表的差异」条目。

## [2026-11-12] feat | 原型/UI 角色落地：prototype 节点 + 专属原型目录
- 图：`design → prototype → review_g2`；触发为 `DESIGN.md` 归属表含 fe lane 或命中界面关键词，纯后端写 `PROTOTYPE.md` 并登记 `prototype_skipped` 继续到 G2。
- 产物：`projects/river/<task_id>/prototype/{index.html,PROTOTYPE.md}`；门禁 `prototype_files` 校验 HTML 骨架/长度与说明三节（原型说明/验收映射/局限）。
- 审批：`ledger.artifact_digest` 支持目录，G2 摘要同时绑定 `DESIGN.md` 与原型目录；控制台流程 10 步，产物槽新增 `prototype`/`prototype_html`（iframe 仅 allow-scripts）。
- 边界：codex 写入先落仓库内 `.agentcode-prototype/<task8>/` 再移入专属目录，`finally` 清理暂存。
- 验证：PROTOTYPE-01 10 条断言；`graph_smoke` 11/11；`ac_regression` 190 条 PASS=188 FAIL=0 SKIP=2；`smoke_test` 20/20；真实 codex 单节点冒烟 rc=0（index.html 2,638 B / PROTOTYPE.md 298 B / 18,073 tokens），清理后 river 工作树干净。
- 未验证：原型内容质量未人工评审；跳过规则可能误判；未跑完整 intake→G3 的 UI 需求；进程被 kill 时暂存目录可能残留。
- 文档：[[wiki/P1实施进度]] §9、[[wiki/平台看板]]、[[wiki/P2规划]]、[[wiki/角色配置]]。

## [2026-11-12] verify | prototype 节点真实验证与按 lane 判定修复
- **环境**：临时工作区 `%TEMP%\proto_verify`（复制 river `project.yaml`，独立 `state.db`/`graph.db`，不触碰正式账本），真实 codex `gpt-6.1-terra`，两跳闸门用 `Command(resume="approved")`。
- **UI 主路径**（深色模式切换按钮）：requirements 128s → design 158s → prototype 191s（attempt=1 done）；`prototype_files` passed；`index.html` 4,201 字符、含「深色/dark/theme/主题」；`PROTOTYPE.md` 260 字符三节齐全；控制台登记 G2 审批 id=1 且 `artifact_hash == ledger.artifact_digest([DESIGN.md, prototype 目录])`；任务停在 `review_g2`；3 次 provider 调用、无孤儿 running。
- **纯后端路径暴露并修复真实缺陷**：DESIGN 归属表只有 `be` lane，但需求/设计文本含「界面」「前端」→ 旧关键词规则仍生成原型（误判）。修复 `_needs_prototype`：归属表存在时以 lane 列为准（`fe` → 生成，仅 `be` → 跳过），关键词仅在归属表缺失时兜底；此前「或命中界面关键词」的规则表述被本条取代，旧记录保留。
- **修复后复验**：纯后端重跑 `prototype` 0 秒完成（不调 codex），`prototype_skipped` passed，无 `index.html`，仍正常进入 G2。
- **回归**：PROTOTYPE-01 增至 12 条（新增否定表述不误判、fe lane 优先 2 条）；`ac_regression` 192 条 PASS=190 FAIL=0 SKIP=2；`graph_smoke` 11/11。
- **仍未验证**：原型内容质量无人工评审；端到端止于 G2（未跑 intake → implement → test → deploy → G3 的 UI 需求）；进程被 kill 时 `.agentcode-prototype/` 可能残留（未演练，也未加入 `.gitignore`）。
- 文档：[[wiki/P1实施进度]] §9 新增「真实端到端验证 / 剩余未验证」，触发行改为 lane 优先；[[wiki/平台看板]] 触发行同步。

## [2026-11-12] qa | 节点逻辑规则梳理与优化候选
- 回答「各节点规则怎么设置、怎么优化、能否可视化」，核对 `graph.py` / `ledger.py` / `run_task.py` / `config_store.py` / `project.yaml`。
- 结论：图结构与节点内部规则均为硬编码；仅预算配额/U、模型路由、`test.gates`、`deploy.staging` 真正由配置驱动；`budget.thresholds`、`max_inflight_tasks`、`gates.*.sla_hours` 仅校验/展示，无消费方。
- 发现待修问题：① `prototype_node` 重跑前不清空原型目录，G2 驳回后旧 `index.html` 可能令门禁假通过；② `implement` 门禁命令硬编码，未用 `tech_stack.gate_commands`；③ `review` 角色无对应图节点；④ `test`/`deploy` 未接租约。
- 新增 [[wiki/节点规则]]（逐节点规则表 + 优化候选 + 可视化建议），更新 [[index]]；建议未实施，属提案。

## [2026-11-12] qa | Prompt 覆盖与升级方案
- 核对 5 个模板（`requirements-gen` / `design-gen` / `prototype-gen` / `implement-lane` / `gate-fix`，均 1.0.0）与 `manifest.yaml`：有 prompt 的是四个 codex 角色 + 门禁修正循环；test/deploy/G1-G3/retro 为确定性编排，无 prompt；`review` 角色名无对应节点。
- 发现：① prompt 契约与门禁不对齐（非目标、实现策略、验收映射未校验）；② 缺项目上下文注入，C-001 文案与 `projects/river/AGENTS.md` 重复；③ lane scope 只提示不校验（脏文件检查只看 apps 目录）；④ gate-fix 缺失败 diff；⑤ 无产出质量评测。
- 结论写入 [[wiki/节点规则]] §五（A 结构 / B 机制 / C 角色扩展），未实施方案。

## [2026-11-12] feat | Prompt A 层实施：六段式 + 契约校验 + 上下文注入 + 范围硬校验
- 模板：五个模板统一为 Role/Context/Task/Constraints/Output contract/Self-check 六段式，版本 1.0.0 → 1.1.0；新增必填参数 `project_context`，`gate-fix` 增 `failed_files`/`attempt`。
- 上下文注入：`graph.py` 新增 `_project_context()`，渲染期从 `project.yaml`（技术栈/目录/门禁命令）与 `projects/river/AGENTS.md`（前 1,500 字符）拼装；模板内 C-001 硬编码副本删除，改由记忆单一来源注入。
- 契约即校验：`requirements` 门禁增 `## 非目标`；`design` 门禁增 `## 实现策略`/`## 验收映射`；新增 `design_ac_coverage`（`_ac_coverage`），把 REQUIREMENTS 的 AC-n 与 DESIGN 验收映射做机械核对，缺失即 `gate_failed`。
- 范围硬校验：`implement` 在空交付检测后解析 `git status --porcelain -- apps`（含 rename 取新路径），用 `fnmatch` 对比 lane `scope`；越界记 `lane_scope` FAIL 且不进门禁、不提交。
- 测试：两个桩的 DESIGN 输出补 `## 验收映射`（AC-1/AC-2），`_fake_git` 改为按 `checkout -B` 分支返回 web/api 改动；新增 PROMPT-01（14 条断言）覆盖六段式、上下文注入、契约门禁、AC 未覆盖、越界失败。
- 验证：`ac_regression` 206 条 PASS=204 FAIL=0 SKIP=2；`graph_smoke` 11/11；`smoke_test` 20/20。
- 边界（未实施）：B 层评测机制、C 层角色扩展（retro/review prompt）、节点规则 P0（原型目录残留、implement 门禁命令走配置）不在本轮范围。
- 文档：[[wiki/节点规则]] §二 与 §五 更新为已实施状态。

## [2026-11-12] feat | Prompt B 层评测机制：黄金用例 + 版本漂移 + 失败/成本观测
- 黄金用例：新增 `_templates/evals/golden.yaml`（15 条，5 模板 × 3）与 `platform/prompt_eval.py`；渲染层断言六段结构、契约章节、scope/约束文案与 prompt 长度，报告含通过率与 `chars`/`est_tokens`。
- 产出层校验：`--live` 仅运行带 `output_assert` 的用例，调用真实 codex 后抽取 Markdown，断言章节完整与 AC 数量；默认不跑，需 `--codex-home`。
- 版本与回滚：`templates.snapshot()` 增记每个模板 sha256，新增 `lock_drift()`；`prompt_eval.py --task-dir` 可发现同版本内内容漂移并定位文件，按输出提示 git checkout 回退。
- 失败驱动迭代：新增 `platform/prompt_report.py`，按 `gate_results.gate_name` 聚合失败率并列出最近失败原因。
- 成本观察：同脚本聚合 `cost_ledger`（角色 calls/tokens/均值）与 `node_runs`（节点平均/最大耗时）。
- 验证：EVAL-01 11/11；`ac_regression` 217 条 PASS=215 FAIL=0 SKIP=2；`graph_smoke` 11/11；`smoke_test` 20/20；`prompt_eval.py` 15/15 PASS（prompt 1,160–1,926 字符）。
- 未验证：`--live` 本轮未真实执行（需凭据与 token 预算）；评测未接入图节点，按需手动或 CI 运行。
- 文档：[[wiki/节点规则]] §五。

## [2026-11-12] feat | Prompt C 层 + 节点规则 P0 + CI 接入
- C 层：新增 `review` 节点（implement 之后、test 之前），确定性门禁 `review_commits`/`review_lane_scope`/`review_ac_coverage` + 可选 `review-diff` codex 审查，结论写 `REVIEW.md`/`REVIEW-agent.md`，失败 → `gate_failed`；`retro` 可选 `retro-gen` 生成 `RETRO.md`（失败只留痕，不改终态）。
- 配置：`project.yaml` 增 `review.codex` / `retro.generate`（默认 false，不改变既有成本）；manifest 顶层 `version: 1.2.0`，新增 2 模板，`manifest_version()` 优先顶层版本；黄金用例 15 → 17。
- P0：`implement` 的 typecheck/unit 命令改读 `tech_stack.gate_commands`（缺省回退旧命令）；`prototype_node` 每轮生成前清空任务专属原型目录，并清理仓库 `.agentcode-prototype/*` 崩溃残留（原型正本仍在任务目录）。
- 测试：新增 REVIEW-01（7 条：PASS 放行、FAIL 拦截、REVIEW/REVIEW-agent 落盘、retro_doc）；PROTOTYPE-01 增「清空上一轮残留」；`_write_role_config` 把实现门禁与测试门禁拆开（实现跑 typecheck/unit，测试跑 coverage）；`graph_smoke` 增审查断言（12/12）。
- CI：新增 `.github/workflows/agentcode-checks.yml`，push/PR 跑 `prompt_eval`（零 token）、`ac_regression`、`graph_smoke`、`smoke_test`，依赖固定为 langgraph 1.2.4 / checkpoint-sqlite 3.1.3 / pyyaml 6.0.3。
- live 实测：`prompt_eval --only req-basic,design-basic --live --workdir d:\project\river` 2/2 PASS，tokens 6,503 + 7,161（gpt-6.1-terra）；临时目录不受 codex 信任，须在 `config.toml` 声明 trust 的路径执行；评测后 river 工作区无残留。
- 验证：`ac_regression` 225 条 PASS=223 FAIL=0 SKIP=2；`graph_smoke` 12/12；`smoke_test` 20/20；`prompt_eval` 17/17。
- 未验证：`review.codex`/`retro.generate` 的完整真实任务、CI 远端首跑；按用户要求本轮未 git commit。
- 文档：[[wiki/节点规则]] §一/§二/§三/§五 更新为已实施状态。

## [2026-11-12] verify+fix | review.codex 真实任务验证（含 lane 路径解析 bug 修复）
- 首次任务 `066eb5ec`（取消首页副标题）在 implement 阶段 `gate_failed`：`dirty = git status --porcelain` 输出整体 `.strip()` 吃掉首行 ` M ` 的状态列前导空格，`apps/web/...` 被解析为 `pps/web/...`，lane_scope 误判越界。修复为 `.rstrip()`，并在 PROMPT-01 增加「未暂存格式（` M `）」回归（断言无 `lane_scope passed=0`、`git_commit` 操作数=1、状态非 `gate_failed`）。
- 重跑任务 `649f569f`：requirements 4,465 / design 6,973 / prototype 21,458 / implement_fe 20,142 / review 5,250 tokens，共 58,288 tokens、calls=5；implement 成功并在 `feat/649f569f-fe` 提交（`apps/web/src/App.tsx`）。
- review 确定性门禁 3/3 PASS（commits / lane_scope / ac_coverage），codex（`review-diff` 1.2.0，2m12s）判 **FAIL**：DESIGN 要求同时移除品牌副标题与主标题下说明段落，diff 只移除前者，AC-2 无证据 → 任务 `gate_failed`，未进入 `test`。拦截链路真实生效。
- 执行边界：按要求临时将 `deploy.staging.required` 设为 false 跳过 staging，运行后已恢复 true；`review.codex` 保持 true，`retro.generate` 保持 false。
- 回归：`ac_regression` 226 条 PASS=224 FAIL=0 SKIP=2；`graph_smoke` 12/12；`smoke_test` 20/20；`prompt_eval` 17/17。
- 未验证：真实任务中 `review.codex` 的 PASS 放行路径、`retro.generate` 的真实产出、CI 远端首跑。按用户要求未 git commit；river 工作区保留 stash `task-066eb5ec failed lane (uncommitted)` 及分支 `feat/649f569f-fe`。
- 文档：[[wiki/节点规则]] §五 增「真实任务验证」小节，更新「仍待做」与「未验证」段落。

## [2026-11-12] feat | 节点规则一致性收口（rules.yaml + test/deploy 租约 + 从节点重跑）
- 规则外置：新增 [rules.py](../../platform/rules.py) 与 `projects/river/rules.yaml`，接管 `doc.min_len/max_len`、`prototype.md_min/html_min/html_max/ui_keywords`、`implement.gate_fix_attempts`、`review.diff_max`、`memory.context_max`；按 mtime 缓存，缺文件/坏键回退默认值。`graph.py` 删除对应硬编码常量。
- 租约统一：`test`/`deploy` 改走 `_start_leased_run` / `_close`，部署操作登记带 `worker_id + lease_epoch` 栅栏；两节点不调 codex，跳过凭据目录复制。
- 从节点重跑：`build_app(checkpointer, entry=...)` 把 `START` 指向指定节点；`rerun_payload` 清空该节点上次产物，`reset_task_for_rerun` 把 `gate_failed/failed/done` 复原为 `running`。入口：`run_task.py <id> rerun --from <node>`、控制台 `POST /api/task/rerun` 与任务详情按钮；挂起闸门时拒绝重跑。
- 回归：新增 `NODE-01`（10 条：租约、重跑、入口校验、rules 覆盖与回退）。全套 `ac_regression` 236 条 PASS=234 FAIL=0 SKIP=2；`graph_smoke` 12/12；`smoke_test` 20/20；`prompt_eval` 17/17；控制台只读页 200，`/api/task/rerun` 在非 `--control` 下 404。
- 未验证：重跑未在真实项目失败任务上演练；控制台 `/api/task/rerun` 的写模式路径仅静态检查，未做实际入队演练。

## [2026-11-12] verify | 真实失败任务重跑验证（649f569f · review）
- 对象：`649f569f`（取消首页副标题，此前在 `review` 因 codex 判 FAIL 而 `gate_failed`，无挂起闸门）。
- 执行：`run_task.py 649f569f… rerun --from review`。`reset_task_for_rerun` 置 `running`，`rerun_payload` 清空 `review_report`，图从 `review` 重跑。
- 证据：`node_runs` 中 review 两次均 `status=failed`、带 `worker_id`，epoch 18（12:15:18–12:17:30，5,250 tokens）→ epoch 19（12:32:14–12:34:21，5,231 tokens）；`REVIEW.md`/`REVIEW-agent.md` 于 20:34:21 重写，结论仍 FAIL；`test`/`deploy` 的 node_runs 仍为 0；任务终态 `gate_failed`。
- 结论：真实失败任务的重跑链路（状态复原、产物清空、租约重建、失败复现）通过；未覆盖「重跑 `implement` 重建分支」与「重跑后 PASS 继续到 G3」两种路径。
- 文档：[[wiki/节点规则]] 未验证段更新为重跑已实测。

## [2026-11-12] verify | review.codex PASS 放行链路（d89a7cc3）
- 任务 `d89a7cc3`（移除品牌副标题，含 AC-3「其他页面内容保持不变」）。临时关闭 `deploy.staging.required`，跑完恢复。
- 前 3 次 implement 均产出相同 diff（删共享 `<header>` 的 `<span>受邀制私人牌局</span>`），review 三次判 FAIL，理由：该 header 同时用于主页与房间页，删除会改变房间页内容（违反 AC-3）。证明审查对共享组件跨页面影响与否定式 AC 能正确拦截。
- 为隔离验证「PASS 放行链路」，在分支 `feat/d89a7cc3-fe` 手动提交作用域正确的改动：`{selectedRoom && <span>受邀制私人牌局</span>}`（主页隐藏、房间页保留），从 `review` 重跑。
- 结果：review 第 4 次执行（14:00:28–14:02:42，5,402 tokens）判 **PASS**，任务继续进入 `test`；`test` 因项目既有覆盖率阈值（49.39%<90%）失败，与 review 无关。`REVIEW.md` 结论 PASS、`REVIEW-agent.md` 「无问题/无建议」。
- 消耗：共 72,182 tokens（requirements 4,547 / design 7,155 / prototype 18,890 / implement×3=19,890 / review×4=21,700）。
- 结论：review.codex 的 FAIL 拦截（共享组件/否定式 AC）与 PASS 放行（→test）两条链路均在真实任务上验证通过；implement 代理精细度不足，无法自行产出作用域正确的改动。
- 文档：[[wiki/节点规则]] 未验证段更新为 PASS 链路已验证。

## [2026-11-12] feat | review 失败自动返工到 implement（审查意见注入）
- 背景：`d89a7cc3` 实测 implement 连续 3 次产出相同错误 diff，review 判 FAIL 后任务直接终止，代理拿不到审查意见。
- 实现：新增状态 `review_feedback`；`review` 失败且 `attempt ≤ rules.review.rework_attempts`（默认 1）时保持 `running` 并由条件边回到 `implement`，超限才 `gate_failed`。反馈取 `REVIEW-agent.md`「问题/建议」+ 机械门禁失败项（≤2000 字符），追加到 lane 指令尾部「## 上轮审查未通过（必须逐条修正）」；Agent 未列问题时用兜底文案。每次失败记 `review_rework` 门禁。`implement` 增加任务状态守卫，防止 `gate_failed/done` 下被重复触发。
- 语义说明：返工按 `base_branch` 重建 lane 分支（全量重做，非增量补丁）；`git_commit` operation key 幂等，账本不重复登记。
- 回归：新增 `REVIEW-02`（5 条）；`REVIEW-01` 显式 `rework_attempts: 0` 保持 FAIL 终止语义。全套 `ac_regression` 241 条 PASS=239 FAIL=0 SKIP=2；`graph_smoke` 12/12；`smoke_test` 20/20。
- 未验证：返工路径未在真实任务上演练（真实 codex 是否能按反馈纠正仍未知）；控制台未新增返工相关入口（自动发生）。

## [2026-11-12] feat | 配置项接线：阈值 / 并发上限 / 闸门 SLA 真正生效
- 背景：`budget.thresholds`、`budget.max_inflight_tasks`、`gates.*.sla_hours` 此前只做校验与展示，属“配置了但不生效”。
- 实现：① `ledger.configure_thresholds()` 由 `graph.refresh_runtime_config()` 调用，阈值进入预算状态判定；缺省或倒挂回退 0.8/0.9/1.0（不回退上一项目值）。② `ledger.inflight_tasks()` + `run_task.py start` 与控制台 `ControlRunner.enqueue(start)` 的并发拦截（`running+awaiting_human`，排除自身；CLI 非 0 / HTTP 409）。③ 看板任务列表新增「SLA」列，`awaiting_human` 按 `updated_at` 计算等待小时，超过 `gates.*.sla_hours` 标红；`load_budget_config` 解析 `max_inflight_tasks` 与各闸门 SLA。
- 回归：`CONFIG-01` 新增 5 条断言。全套 `ac_regression` 245 条 PASS=243 FAIL=0 SKIP=2；`graph_smoke` 12/12；`smoke_test` 20/20。
- 未验证：并发上限仅单线程队列场景（未做真实并发压力）；SLA 只驱动展示，不触发超时告警/自动升级。
- 文档：[[wiki/节点规则]] P1-52 标记已解决并新增「配置项消费」；[[wiki/平台看板]] 补充 SLA 列与并发拦截。

## [2026-11-12] fix | 原型触发判定改为按表头解析（P2-56）
- 问题：`_needs_prototype` 用 `_LANE_ROW_RE` 假定 lane 在表格第二列，列序变化即失效；归属表存在但无 be/fe 时静默回退关键词。
- 实现：新增 `_lane_table()` 解析「## 文件归属表」首个 Markdown 表格；`_needs_prototype` 按表头定位含 `lane`/`角色` 的列，支持 `前端/后端/frontend/backend` 别名，列序无关；缺 lane 列或无有效数据行返回 `None`，`prototype_node` 记 `prototype_scope` 门禁失败并 `gate_failed`，不生成原型、不进入 G2；未识别 lane 值时回退关键词并说明原因。
- 回归：`PROTOTYPE-01` 由 15 条增至 18 条（列序无关、缺列 None、未识别值回退关键词、缺列节点级阻断与无产物）。全套 `ac_regression` 250 条 PASS=248 FAIL=0 SKIP=2；`graph_smoke` 12/12；`smoke_test` 20/20。
- 未验证：真实 DESIGN 若把归属表写成非 Markdown 表格（如列表）仍会走关键词回退，属预期降级。

## [2026-11-13] verify | 真实返工验证暴露历史审查计数缺陷（实施前记录）
- 对象：`d89a7cc3` 从 `implement` 重跑，配置 `review.codex=true`、`review.rework_attempts=1`；终态 `gate_failed@review`。
- 证据：本轮仅新增 implement_fe 1 次（22:22:15–22:24:45，6,565 tokens）与 review 1 次（22:24:45–22:26:48，5,482 tokens）；review 为历史第 5 次，`review_rework=0`（“第 5 次审查失败；返工上限 1”），没有触发自动返工。最终 diff 仍直接删除共享页头 `<span>受邀制私人牌局</span>`，Agent 正确判 FAIL（违反 AC-3）。
- 根因与拟议修复：现有逻辑用全任务历史 review 次数消耗返工预算，导致老任务重跑无法返工。拟改为图状态按本轮计数、手动重跑清零，并增加跨轮回归；完成前不宣称真实返工链路通过。
- 文档：[[wiki/节点规则]]「审查失败自动返工」已记录问题、范围与方案；本条目仅记录实施前证据。

## [2026-11-13] fix | review 返工预算改为按重跑轮次隔离
- 实现：`DeliveryState` 新增 `review_rework_count`；`review` 每次失败递增本轮计数，条件边按该计数与 `review.rework_attempts` 判断是否回到 implement，PASS 清零。`rerun_payload` 对任意手动重跑入口清零，避免历史 review `node_runs` 数阻断新轮次；节点全局 attempt 仍保留用于审计。
- 回归：`REVIEW-02` 增加“首轮返工超限后，再从 implement 手动重跑”的场景，断言新一轮仍执行两次 implement/review，门禁留痕为 `[1,0,1,0]`；定向 7/7 通过。全套 `ac_regression` 252 条 PASS=250 FAIL=0 SKIP=2；`graph_smoke` 12/12；`smoke_test` 20/20；`prompt_eval` 17/17。
- 真实复验：同一 `d89a7cc3` 任务已从 implement 再次启动，运行结果与 token 消耗待完成后补记；本条目不提前宣称真实链路通过。
- 文档：[[wiki/节点规则]] 更新触发口径、计数修复、回归数字与重跑边界。按用户要求未 git commit。

## [2026-11-13] verify | review 计数修复真实复验完成，自动回边仍未覆盖
- 真实结果：`d89a7cc3` 修复后从 implement 重跑，仅新增 implement_fe 1 次（23:45:11–23:47:56，7,792 tokens）与 review 1 次（23:47:56–23:49:58，5,363 tokens）；合计 13,155 tokens。review 全局 attempt=6，本轮计数已清零，未再被历史 5 次审查阻断。
- 代码与审查：implement 直接采用上轮保留的审查反馈，将共享页头改为 `{selectedRoom && <span>受邀制私人牌局</span>}`；机械门禁与 Agent 均 PASS，进入 test，最终代码符合首页隐藏、房间页保留与其他内容不变的要求。
- 边界：本轮没有发生 review FAIL，故真实 `FAIL → 自动 implement → PASS` 回边仍未得到运行证据；仅跨轮计数修复、反馈在手动重跑中的复用和 PASS 放行得到验证。test typecheck/unit PASS，coverage 因既有 49.39% < 90% 失败，终态 `gate_failed@test`，与 review 无关。
- 文档：[[wiki/节点规则]] 已补实际结果并保留未验证项；未将本次结果夸大为完整自动返工验证通过。按用户指示继续下一项 retro 真实生成验证。

## [2026-11-13] verify | retro.generate 真实 codex 产出验证通过
- 范围：选用已完成的真实任务 `b8c4104a`，临时开启 `retro.generate` 后从 `retro` 重跑；避免把当前 `gate_failed@test` 的 `d89a7cc3` 错标为 done。本项验证真实节点的 codex 生成，不等同于新任务全链路复验。
- 结果：新 retro node_run（lease epoch 36，23:57:27–23:59:47）完成，`retro_doc=PASS`（514 字符），真实 `RETRO.md` 含「结果/偏差/经验/后续动作」；`cost_ledger` 记录 1 次调用、5,590 tokens，任务终态保持 `done@retro`。产物对缺失的具体 diff 证据作了显式保留说明，没有编造偏差。
- 配置：验证后已恢复 `retro.generate: false`；未改模板与平台代码，未 git commit。
- 文档：[[wiki/节点规则]]「未验证」已改为节点级真实验证通过，并保留新任务全链路与 live 模板评测边界。

## [2026-11-13] implement+verify | C3 gitleaks CI 接入（本地验证通过）
- 实现：新增 `.gitleaks.toml`，allowlist 路径正则 `projects/.*/codex-home/.*`；`.github/workflows/agentcode-checks.yml` 新增 `gitleaks` job（`fetch-depth: 0` + `gitleaks/gitleaks-action@v2`），自动读取仓库根配置。
- 本地验证：`gitleaks detect --redact` 扫描 git 跟踪内容 → `no leaks found`、exit 0。用 GitHub PAT 形态假密钥双向验证：根目录 `_leak_probe.txt` 检出 1 条（exit 1），放入 `projects/river/codex-home/` 后整仓扫描放行（exit 0），说明 allowlist 生效且未削弱检测能力；探针与基线 JSON 已删除。
- 边界：CI 远端首跑仍需提交/推送后才能触发；本次未提交 git。`.gitignore` 是首层防线，allowlist 是兜底，避免未来误提交 codex 凭据时 CI 红。
- 文档：[[wiki/节点规则]] C 层表新增凭据扫描 CI 行；[[wiki/P2规划]] C3 行更新为已实施+本地验证通过。

## [2026-11-13] implement+verify | C1 第二项目（proj-b）接入
- 背景：P2 规划 C1 要求第二个项目走 A1 模板 + project.yaml 复制接入，验收「proj-b 完成一次双 lane 任务闭环」。仓库内仅 `d:\project\river` 是 npm-workspaces 双 lane（apps/web + apps/api），其余仓库均为单 lane，故采用「复用 river 仓库 + lane 分支前缀隔离」的方式证明平台多项目能力，不消耗真实 codex token。
- 实现：
  - `platform/graph.py`：新增 `PROJECTS_DIR` 常量、`set_project(name)`（切换 `RIVER_WS` 并同步 `USAGE_BUFFER`）、`list_projects()`（扫 `projects/*/project.yaml`）。`_project_config()` 本就读 `RIVER_WS/project.yaml`，切换即生效。
  - `platform/run_task.py`：新增 `--project <name>` 参数（默认 `river`，env `AGENTCODE_PROJECT` 可覆盖），dispatch 前 `_select_project()` 校验项目存在并重算 `WS`/`DB`。
  - `projects/proj-b/` 骨架：`project.yaml`（复制 river，改 `project`/`workspace`，复用 river 双 lane 仓库，省略 `executor.codex_home` 回退到 `<workspace>/codex-home`）、`rules.yaml`（复制 river）、`AGENTS.md`（简化版）、`state.db`（`ledger.init_db` 建立）。`config_store.validate(proj-b)` 通过（0 errors）。
  - `platform/ac_regression.py`：新增 `MULTI-01` 用例（9 条断言）——项目发现、`set_project` 配置消费、双临时工作区各自 stub 双 lane 到 done、state.db 互不泄漏、`max_inflight_tasks` 按各自配置消费。
- 验收证据：
  - `MULTI-01` 9/9 PASS；CLI 烟测 `run_task.py --project proj-b <id> status` 查到任务，默认 river 查不到同 ID（隔离生效）。
  - 全套回归：`ac_regression` 261 条 PASS=259 FAIL=0 SKIP=2；`graph_smoke` 12/12；`smoke_test` 20/20；`prompt_eval` 17/17。
- 边界：proj-b 真实 codex 凭据尚未接入（`codex_home` 省略），双 lane 闭环由 stub codex 回归证明平台支持；`dashboard.py` 暂未加 `--project`（低优先级）。未 git commit。
- 文档：[[wiki/节点规则]] 新增「多项目接入（C1）」节；[[wiki/P2规划]] C1 行更新为已实施+验收通过。

## [2026-11-13] fix | C1 proj-b 改为接入独立仓库 pdca-erp
- 原因：上一轮 proj-b 复用 `d:\project\river` 仓库，只验证了「同一仓库挂两个项目记录」，不符合「接入第二个代码仓库」的语义，且会污染 river 的分支。经复核候选仓库（poker 单仓 TS、cw-agent Python、frame Go 均为单 lane），最终按用户指定改用 `d:\project\pdca-erp`（frontend Vue3+Vite / backend Spring Maven 多模块），这是真实双 lane 仓库。
- 配置：`projects/proj-b/project.yaml` 的 `repo_path` 改为 `d:\project\pdca-erp`，`tech_stack` 为 `web: frontend` / `api: backend`；门禁命令 `typecheck: npm --prefix frontend run type-check`、`unit: node --test frontend/scripts/*.test.mjs`、`build: npm --prefix frontend run build`；`codex_home` 共享 river 只读凭据基目录（仅作 Worker 复制源），`config_store.validate` 0 errors；`state.db` 重建。
- dashboard：新增 `--project <name>`，`--db`/`--project-yaml` 缺省由项目推导，未知项目退出码 3；`dashboard.py --project proj-b --print` 正常显示 proj-b 快照。
- 验收证据：
  - `MULTI-01` 10/10 PASS（新增断言：`repo_path=pdca-erp`、frontend+backend 双 lane 配置）。
  - `npm ci` 安装 410 包后真实门禁：`unit` 22/22 PASS；`typecheck` 真实运行但暴露 pdca-erp **既有** 13 处 TS 错误（TS7006/TS2339/TS2367/TS2349，`src/views/sal|scm|system`），门禁为红。`_implement_gate_specs` 固定消费 typecheck+unit，故真实闭环会被阻断；该问题属仓库既有状态，非接入缺陷，待用户决定是否修复业务代码。
- 阻塞：pdca-erp 的 git 仓库无任何提交（master 无 commits），平台无法基于基线创建 lane 分支；用户确认将自行完成初始提交后再跑真实闭环。
- 未做：后端 Maven 门禁（本机 `mvn` 不在 PATH，集成测试依赖 Postgres/Redis）；真实 codex 双 lane 闭环（待初始提交与 typecheck 决策）。未 git commit。
- 文档：[[wiki/节点规则]]「多项目接入（C1）」节改为 pdca-erp 方案并记录真实门禁证据与阻塞；[[wiki/P2规划]] C1 行同步修正。

## [2026-11-13] fix+verify | pdca-erp typecheck 类型债归零
- 背景：proj-b 接入 pdca-erp 后，完整运行 `npm --prefix frontend run type-check` 发现约 150+ 处 TS 错误（此前因输出截断误判为 13 处，已纠正），分布在 30+ 文件；平台 `_implement_gate_specs` 固定消费 typecheck+unit，故必须归零才能跑真实闭环。
- 根因修复（低风险）：
  - `frontend/src/api/index.ts`：`Page`/`TableDataInfo` 原从 `@/types/models` 导入（实际定义在 `@/types/api`），且 13 个类型（PdmBom/PdmBomLine/PdmCustomerMaterialMap/WmsWarehouse/WmsInventory/WmsStockIn/WmsStockOut/PmmJig/PmmJigLog/ScmDeliveryNoteLine/ScmPurchaseReturnLine/SalForecast/SalForecastVersion）未 import。修正导入后错误 150+ → 102。
  - `frontend/src/types/api.d.ts`：`TableDataInfo` 改为泛型 `TableDataInfo<T = any>`，消除 `TableDataInfo<T>` 用法报错。
- 剩余 102 处修复：对照 `backend/pcba-erp-*` 的 Java 实体，在 `frontend/src/types/models.d.ts` 补齐 `FasProductStock`(docNo/docStatus/flowQty/sourceType/auditBy/auditTime/rejectReason)、`PdmBom`(customerId/kitModel)、`PmProject`(categoryId)、`PmmJig`(specification/thicknessMm/maker/vendorId/vendorName/attachmentUrl)、`ScmDeliveryNote`(bizCategory)；并修复视图侧 TS7006 隐式 any、TS18047/18048 可空、TS1117 重复键(MenuTree)、TS2484 重复导出(stores/user)、TS2353 persist `pick→paths`、TS2349 缺分号、TS2367 `String()` 统一比较、TS7053 `Record<number,number>`、TS2345 null→undefined、TS2869 多余 `??`、Vue3 `$index` 改插槽解构、`FormInstance/FormRules` 改从 element-plus 导入。
- 验证（独立复跑，均通过）：`type-check` 0 error；`node --test frontend/scripts/*.test.mjs` 22/22 PASS；`npm --prefix frontend run build` 成功（`✓ built in 23.80s`）。全程未使用 `@ts-ignore`，未修改 `backend/`，未做 git 写操作。
- 待业务确认：`SalSalesOrder.deliveryAddress`、`PdmMaterialCategory.materialScope` 后端实体不存在，已按前端 UI 状态做局部类型扩展；`materialScope` 当前恒为空，需确认是否应改为按分类/路径推导。
- 阻塞未变：pdca-erp 无任何 git 提交，真实 codex 闭环待用户完成初始提交（本次修复在工作区，需一并提交）。
- 文档：[[wiki/节点规则]] 补充「类型债修复」「待业务确认」；[[wiki/P2规划]] C1 行更新为 typecheck 已归零。

## [2026-11-13] c1-closed-loop | proj-b 真实双 lane 闭环成功 + review 模板修复
- 背景：pdca-erp 初始提交 `6aed5dc` + 类型修复提交 `b7f3e95` 后，任务 `bf9f6acf`「登录页用户名明文显示，移除查看/隐藏交互」启动真实 codex 双 lane 闭环。
- 闭环结果：终态 `done@retro`，全链路成功。链路：requirements → design → prototype → implement_be（AuthController 登录响应增 `username`）→ implement_fe（store 返回 username、登录页写 localStorage、移除记住账号复选框）→ review PASS → test（typecheck 0 error、unit 22/22 PASS）→ deploy（`required:false` 显式跳过）→ retro。
- 期间修复 review 模板缺陷（1.2.0 → 1.2.1）：首轮 review 连续 4 次 FAIL，根因——① diff-only 无法核验未改动但正确的代码（用户名输入框本就是 `type="text"`，不在 diff 中）；② test 节点在 review 之后，AC-5「门禁通过」无证据。修复：`review-diff.tmpl` 新增 `$files_content`（变更文件当前完整内容，`_changed_files_content()` 读取，受 `review.diff_max` 截断）与 `$gate_results`（implement 阶段门禁结果，`_implement_gate_results()` 从 `gate_results` 表取 `implement_%` node_run 最新一条去重，渲染 Markdown 表格）。第 5 次 review 即 PASS。
- 代码修复：返工 implement 误将「记住账号」复选框加回，手动移除（login/index.vue 删 `el-checkbox` + `remember` 状态），提交 `ff1dea9`；`user.ts` 的 `login` 返回 `data: { username }`，由登录页写 localStorage，符合设计。
- 回归：ac_regression 262 条 PASS=260 FAIL=0 SKIP=2；graph_smoke 12/12；smoke_test 20/20；prompt_eval 17/17。pdca-erp 门禁：typecheck 0 error、unit 22/22。
- 文档：[[wiki/节点规则]]「多项目接入（C1）」节补充真实闭环证据与 review 模板改进；[[wiki/P2规划]] C1 行标记 ✅ 已完成。
- 清理：删除 `projects/proj-b/.intake-payload.json`。未提交 AgentCode 平台仓库 git。

## [2026-11-13] audit | C2 权限矩阵复查：矩阵转配置清单
- 来源：`multi-agent-scheme-v2.2/multi-agent-scheme-v2.2.html` §6.4 权限矩阵（8 角色 × 项目仓库/共享资产/数据库/staging/生产/CI-CD + 实现层），逐格对照 `platform/worker.py`、`platform/graph.py`、`platform/memory.py`、`projects/river|proj-b/project.yaml`、`.github/workflows/agentcode-checks.yml`、`.gitleaks.toml`、`platform/ac_regression.py`。
- 结论（审计，非整改）：清单与实际配置一一对应；生产红线①成立；红线②（共享资产 PR 例外）因两项目均无 `_contracts`/`_ds` 而不适用；红线③（AGENTS.md 唯一写入）实质成立但形态不同——写入路径唯一为 `platform/memory.py` 的 `deliver/adopt` CLI，retro 节点只写 `RETRO.md`。
- 6 项缺口登记：G-01 需求/设计/Retro 与 implement 共用 `-s workspace-write`，矩阵声称的「沙箱只读」未落地（高）；G-02 lane scope 仅事后 `_out_of_scope` 校验，无 `writable_roots` 前置限制（中）；G-03 CI workflow 未声明 `permissions`，无 token 白名单（中）；G-04 无 DB/测试库/staging 只读账户（中，随后端接入）；G-05 test/deploy 命令以宿主权限执行属 C-001 已知权衡（低）；G-06 无共享资产与 PR 通路（低，资产出现前不适用）。
- 未验证：AC-06 沙箱越界矩阵仍为手工 SKIP（`ac_regression.py:1101–1102`），OS 级结论沿用 `P0a确认.md` 历史手工记录；本页 draft，未经人工复核。本轮未改代码、未跑回归（无代码变更），未提交 git。
- 文档：新增 [[wiki/权限矩阵配置清单]]；更新 [[wiki/P2规划]] C2 行、[[index]]、本日志。

## [2026-11-13] fix | G-01 整改：文档节点只读沙箱 + doc_repo_clean 守卫
- 背景：C2 审计 G-01——需求/设计/Retro 与 implement 共用 `-s workspace-write`，矩阵要求的「沙箱只读挂载」未落地，仅靠提示词约束。
- 改动：`_run_doc_agent` 与 retro 的 `run_codex` 传 `sandbox="read-only"`；新增 `_repo_dirty()`（`w.git status --porcelain` 解析），文档节点执行前后比对脏文件，新增即 `doc_repo_clean` FAIL → `gate_failed`、不进 G1/G2；git 不可用时不阻断（守卫为第二层防线）。三节点均以 stdout 返回 Markdown、由编排层写任务 `docs/`，改只读不影响正常产物。
- 测试：`ac_regression` AC-02c 新增场景 6（断言 `sandbox=read-only`、`doc_repo_clean` PASS 留痕）与场景 7（模拟脏文件 → 不进入 G1、终态 `gate_failed`），共 5 条断言。全量结果：ac_regression 267 条 PASS=265 FAIL=0 SKIP=2；graph_smoke 12/12；smoke_test 20/20；prompt_eval 17/17。
- 未验证：未跑真实 codex 双 lane 闭环复验（read-only 对真实 codex 的行为待下次任务自然覆盖）；AC-06 仍手工 SKIP。
- 文档：[[wiki/权限矩阵配置清单]] 更新映射表/差异说明/G-01 状态并新增「整改记录」；[[wiki/节点规则]] requirements/design/retro 行标注 `-s read-only` 与 `doc_repo_clean`；[[wiki/P2规划]] C2 行补整改状态。未提交 git。

## [2026-11-13] audit | G-02 评估：codex writable_roots 不能做 lane 写入前限制
- 目的：验证 C2 缺口 G-02 的候选方案——按 lane 注入 `sandbox_workspace_write.writable_roots` 以在写入前限制范围。
- 方法：临时工作区（`%TEMP%\g02-ws`，git 初始化）以 `codex exec -s workspace-write -c 'sandbox_workspace_write.writable_roots=[]'` 运行，要求创建 `b/probe.txt`。
- 结果：文件被创建（tokens 7,199）⇒ `writable_roots` 相对 workdir 是**增量授权**，cwd 及其子目录默认可写，无法把写入限制到 lane 子目录。原方案被证伪，未改代码。
- 可行替代（均属结构改造，待决策）：① 接受现状，事后 `_out_of_scope` 作为等价确定性门禁；② per-lane `git worktree` + 稀疏检出；③ codex cwd 设为 lane 根（需同步改造 prompt 与设计路径解析）。
- 文档：[[wiki/权限矩阵配置清单]] G-02 行与差异说明改为评估结论，整改记录新增一行；[[wiki/P2规划]] C2 行同步。临时工作区已清理，未提交 git。

## [2026-11-13] fix | G-02 整改：lane 根 cwd 写入前置限制
- 决策：用户选择「codex cwd 设为 lane 根」。
- 改动（[graph.py](../../platform/graph.py)）：新增 `_lane_workdir()`（scope 共享唯一真实顶层目录 → repo/<root>，否则回退仓库根）、`_lane_prefix_violations()`、`_workdir_hint()`；模板渲染的 lane 以 lane 根为 codex workdir（workspace-write 下 cwd 即可写范围）；新增 `lane_workdir_prefix` 门禁，拦截 cwd=lane 根后误写 repo 相对路径产生的 `prefix/prefix/` 嵌套（该路径仍匹配 lane scope，事后 scope 门禁发现不了）；gate-fix 同用 lane 根 cwd。逃生口 `lane.prompt`、多根 scope、通配顶层、目录不存在均回退仓库根，`_out_of_scope` 保留为第二层。
- 模板：`implement-lane` 与 `gate-fix` 升 1.2.0，新增 `## Working directory` / `$workdir_hint`；`manifest.yaml` 升 1.3.0（注明 1.3.0 变更）。
- 测试：`ac_regression` 新增 LANE-01（8 条断言：单根→lane 根、多根/顶层文件/目录缺失→仓库根、嵌套前缀识别与不误报、提示文案）。全量：275 条 PASS=273 FAIL=0 SKIP=2；graph_smoke 12/12；smoke_test 20/20；prompt_eval 17/17。
- 未验证：真实 codex 双 lane 闭环未复跑（cwd 切换对真实模型路径行为的验证留待下次任务）；MULTI-01 用逃生口 lane，未覆盖 cwd=lane 根链路；多根/逃生口回退场景仍只靠事后门禁。
- 文档：[[wiki/权限矩阵配置清单]] 映射表前端/后端升级为 ✅、差异说明改写、G-02 标记已整改、整改记录追加实现行、冲突与未知补回退面；[[wiki/节点规则]] implement 行补 cwd 推导与 `lane_workdir_prefix`；[[wiki/P2规划]] C2 行同步。未提交 git。

## [2026-11-13] fix | review 节点只读化（G-01 同类项）
- 背景：C2 矩阵只覆盖 8 个角色，review 属矩阵外角色，此前与 implement 共用默认 `workspace-write` + 仓库根 cwd，理论上可改写仓库。
- 改动（[graph.py](../../platform/graph.py)）：review 的 `run_codex` 传 `sandbox="read-only"`；执行前后用 `_repo_dirty()` 比对，新增脏文件即 `review_agent` FAIL（`REVIEW-agent.md` 仍落盘供审计），任务 `gate_failed`、不进 test。
- 测试：REVIEW-01 新增断言「review 以 read-only 沙箱执行」。全量：ac_regression 276 条 PASS=274 FAIL=0 SKIP=2；graph_smoke 12/12；smoke_test 20/20。prompt_eval 未重跑（本改动不涉及模板）。
- 未验证：未跑真实 codex 审查复验（read-only 对真实审查行为留待下次任务自然覆盖）。
- 文档：[[wiki/权限矩阵配置清单]] 结论补「review 统一只读」、整改记录新增一行；[[wiki/节点规则]] review 行补 `-s read-only` 与守卫，断言数 7→8。未提交 git。

## [2026-11-13] fix | G-03 整改：CI 显式最小权限
- 背景：`.github/workflows/agentcode-checks.yml` 未声明 `permissions`，沿用 `GITHUB_TOKEN` 默认权限（可能含 write）。
- 改动：workflow 顶层加 `permissions: contents: read`（checkout/gitleaks 仅需读仓库）；未授 `pull-requests: write`（gitleaks 只检测、不发评论）。新增 `ac_regression` CI-01 静态断言（存在 permissions 块、仅 contents: read、无 write）。
- 验证：YAML `yaml.safe_load` 通过；`ac_regression` 278 条 PASS=276 FAIL=0 SKIP=2；`graph_smoke` 12/12；`smoke_test` 20/20。prompt_eval 未重跑（不涉及模板）。
- 未验证：工作流仍未在 GitHub Actions 远端运行（C3 首跑），gitleaks 在 read-only token 下的行为需首跑确认。
- 文档：[[wiki/权限矩阵配置清单]] G-03 标记已整改、结论与整改记录同步；[[wiki/节点规则]] CI 节补最小权限说明；[[wiki/P2规划]] C2 行同步。未提交 git。

## [2026-11-13] verify+fix | C5 supervisor 自愈边界
- 实验（真实 codex，Windows）：探针 1 杀进程树（node shim + codex.exe）后 60s 无重启、无新增产物 → 平台 `codex exec` 无 supervisor，软停不自愈；探针 2 只杀直接子进程后，codex 子进程成为孤儿并在 5s 内写完 40 个文件 → 旧 `subprocess.run(timeout=)` 存在超时孤儿写仓库风险。
- 修复（[worker.py](../../platform/worker.py)）：新增 `_kill_tree()`（Windows `taskkill /F /T`，POSIX `killpg(SIGKILL)`）；`run_codex` 改 Popen + `communicate(timeout)`，超时树杀并返回 `-9`；`run_gate` 同样改为超时树杀。
- 测试：新增 KILL-01 4 条断言（codex / 门禁超时返回失败且心跳停止）。全量：ac_regression 282 条 PASS=280 FAIL=0 SKIP=2；graph_smoke 12/12；smoke_test 20/20；prompt_eval 17/17。
- 未验证：真实 1200s 超时路径（探针用 12s 人工击杀、KILL-01 用 2s 超时 + 假 shim）；POSIX `killpg` 未在 Linux 实测。
- 文档：新增 [[wiki/C5-supervisor验证]]（结论/来源/冲突与未知/关联）；[[wiki/P2规划]] C5 标记完成；[[index]] 增加条目。未提交 git。

## [2026-11-13] chore | C4 staging 镜像 digest pin（扫描阻塞）
- 背景：staging 基座沿用浮动 tag（`node:20-alpine` / `nginx:1.27-alpine` / `postgres:16-alpine`），重建可能拿到不同内容。
- 改动（river 仓库，未提交）：`infra/docker/Dockerfile` 用 `ARG NODE_IMAGE/NGINX_IMAGE` 固定 digest；`docker-compose.staging.yml` 的 postgres 固定 digest。三个 digest 与本地 `RepoDigests` 一致（daocloud 镜像），可离线解析。
- 验证：`docker image inspect` ×3 命中；`compose config -q` OK；`build` 成功（基座 CACHED，web 镜像 `sha256:334522d0…`）；`up -d --wait` 三容器 healthy；`web=200`；`POST /api/guests` 200 且由 postgres 生成 player id。首次用不存在的 `/api/health` 探测得 404，属探针错误，已改为真实写接口。
- 未完成：镜像扫描无工具（Windows/WSL 均无 trivy/grype/docker-scout，Docker Hub 不可达），「扫描无高危」未验证；未做两次 `--no-cache` 的 bit-for-bit 比对。
- 文档：新增 [[wiki/C4-staging镜像治理]]；[[wiki/P2规划]] C4 标记部分完成；[[index]] 增加条目。river 仓库改动未提交。

## [2026-11-13] verify+fix | C3 CI 远端首跑
- 推送：仓库首次推送到 shinelincx/agentcode（公开，默认分支 main），入库 80 文件 / 1.13 MB；推送前收紧 .gitignore（projects/*/*/、codex-home、*.db、__pycache__）。本机 github.com:443 被阻断，改用 ssh://git@ssh.github.com:443；origin 仍为 HTTPS。
- 首跑（43374644039）失败：checks 的 AC regression 12 个用例 IndexError: 2（`_repo_path` 用 `RIVER_WS.parents[2]`，/tmp 下只有 2 层父目录）；MULTI-01 双 lane gate_failed（回退路径 /river 落在不可写根目录）+ pdca-erp 断言按反斜杠取名在 PosixPath 上失败；gitleaks-action 生成 `bb2a711^..496949a` 增量 log-opts 报 ambiguous argument。
- 修复：graph._repo_path 改按 PROJECTS_DIR / 工作区推导（不再越界、不写根目录）；MULTI-01 断言分隔符归一化；gitleaks job 改固定版本 v8.24.3 二进制全历史扫描。
- 验证：本地浅路径复跑（TEMP=C:\acsim）ac_regression 282 条 PASS=280 FAIL=0 SKIP=2；prompt_eval 17/17、graph_smoke 12/12、smoke_test 20/20；本地 gitleaks git 全历史 no leaks。第 2 次远端运行 43375084226（c3f7947）checks + gitleaks 双 success。
- 未验证/未完成：KILL-01 在 Linux 跳过，POSIX killpg 无 CI 覆盖；未核查 GitHub Secret Scanning / 推送保护；origin HTTPS 直连仍需网络放行。
- 文档：新增 [[wiki/C3-CI首跑]]；[[wiki/P2规划]] C3 标完成；[[wiki/节点规则]] 凭据扫描 CI 行与 CI 接入节更新；[[index]] 增加条目。

## [2026-11-13] verify | C4 镜像扫描改由 Actions 执行并出结果
- 本地阻断：winget 装 trivy 成功，但漏洞库拉取失败（mirror.gcr.io / registry-1.docker.io 不可达，ghcr.io 133MB blob 5 分钟超时）；gh release download 也因 objects.githubusercontent.com 不可达失败。
- 新增 .github/workflows/image-scan.yml（workflow_dispatch，矩阵覆盖 3 个 pin 镜像，severity=HIGH,CRITICAL，exit-code=0 只记录）。首跑失败：trivy-action 标签 0.28.0 不存在，改 v0.36.0。
- 运行 43377737668 三 job success。结果：postgres 31H/0C + 25H/1C（CVE-2025-68121 stdlib）、nginx 67H/3C（CVE-2026-111497 brotli-libs、CVE-2026-31789 libcrypto3/libssl3）、node 7H/0C + 21H/1C（CVE-2026-59873 tar）；合计 151 HIGH / 5 CRITICAL，均给出修复版本。
- 结论修正：C4「扫描无高危」不成立（上游基座库存问题，非 digest pin 引入）；基座升级与阈值策略为后续项，当前 exit-code=0 不设门禁。
- 未验证：artifact 未上传（下载域名不可达，证据取自 job 日志）；未做两次 --no-cache 构建的 bit-for-bit 比对。
- 文档：更新 [[wiki/C4-staging镜像治理]]（扫描结果表、CRITICAL 明细、冲突与未知）、[[wiki/P2规划]] C4 行、[[index]] 条目。

## [2026-11-13] chore+verify | C4 基座升级与复扫
- 触发：首轮扫描得 5 CRITICAL，处置建议第一项为升级 nginx/node 基座并重新 pin。
- 选型（事实）：`node:20-alpine` 远端 digest 与本地一致、2025-04 后未重建（EOL 停更，无可用修复）；nginx 1.27 主线退役，`stable-alpine` 与 `1.30-alpine` 同 digest `583b7f2f…`；postgres 仅取 16 大版本内最新重建（2026-11-12 构建）。node 取 22 而非 24：river `.github/workflows/verify.yml` 已用 `node-version: 22`，保持镜像与验证运行时一致。
- 改动：river `infra/docker/Dockerfile` 的 `NODE_IMAGE`/`NGINX_IMAGE`、`docker-compose.staging.yml` 的 postgres `image` 改为 `tag@digest`；平台 `.github/workflows/image-scan.yml` 矩阵同步同一组 pin（commit `bf5f974`，已推送 `main`）。river 仓库按既有约定未提交。
- 验证：`config -q` OK；`build` 成功且 `docker history river-api:latest` 各层为本次新建（非复用旧缓存）；`up -d --wait` 三容器 healthy；`GET /` 200；`POST /api/guests` 200（player id `7676990d-…` 由 postgres 生成）；api 日志无 error；`down` 收尾。镜像内实测：node v22.23.5 / npm 10.9.9 / tar 7.5.22、nginx 1.30.6 / openssl 3.5.9-r0 / brotli 1.2.1-r0、postgres 16.16，三者 alpine 3.24.4。
- 复扫（运行 43380314435，三 job success）：**CRITICAL 5 → 1、HIGH 151 → 39**；nginx 3H/0C、node 10H/0C、postgres 25H/1C。三个镜像 OS 层清零。
- 残留（均为上游阻塞）：postgres `CVE-2025-68121`（Go `stdlib` 1.24.6，需 1.24.13+）；nginx 3H 为 `libxslt 1.1.43-r3`（修复版 r4 已发布，待镜像重建）；node 10H 为 npm CLI 自带依赖（build 阶段，运行时不加载）。
- 未验证：未跑 river 全量测试（`npm run verify`，含 Playwright/testcontainers）；未做两次 `--no-cache` 的 bit-for-bit 比对；阈值门禁未启用（`exit-code=0`）；扫描 artifact 未上传。
- 纠错：PowerShell 会剥掉单引号字符串内的双引号，`curl -d '{"nickname":…}'` 实际发出 `{nickname:…}` 得 400，属探针构造问题；改 `Invoke-WebRequest -UseBasicParsing` 后 200。
- 文档：更新 [[wiki/C4-staging镜像治理]]（升级 pin 对照、复扫对比、残留与未知）、[[wiki/P2规划]] C4 行、[[index]] 条目。

## [2026-11-13] feat+refactor | 管理控制台重设计（亮色 / 布局 / 前后端分离）
- 触发：用户指示「重新设计管理控制台，包括界面布局优化、使用亮色模式，前后端分离」。
- 改动：新增 `platform/console/`（`index.html`、`styles.css`、`app.js`、`intake.html/js`、`config.html/js`）；`platform/dashboard.py` 删除三段内联 HTML 常量（约 35KB），改为静态资源服务 + `GET /api/mode`（`{control, intake, mode}`）。静态路由用 `resolve` + `relative_to` 限定在 `console/` 内，按扩展名返回 Content-Type。
- 设计：统一浅色 CSS 令牌；顶部固定栏（品牌/项目/模式/操作/预算态）；首屏「月度用量 + 6 KPI」两栏；待审批面板置顶带计数；表格固定表头 + 行悬浮；宽屏成对并排（审批/门禁、Lane/模型、租约/outbox）；1080/860/620px 三档折叠。
- 行为保持：`/api/*` 契约不变；写请求仍要求 `X-Control: 1`（intake 兼容 `X-Intake: 1`）+ Origin 白名单；`OPTIONS` 405；请求体 ≤64KB；只读默认与控制台开关不变。
- 验证：`ac_regression` 283 条 PASS=281 FAIL=0 SKIP=2（CONSOLE-01 新增静态资源/`/api/mode` 断言，CONFIG-01 改为校验 `/config.js`）；`graph_smoke` 12/12、`smoke_test` 20/20、`prompt_eval` 17/17。只读实例 8899 浏览器目视：亮色布局正常、无重叠、无未捕获 JS 错误。
- 目视修复：`.btn` 的 `display:inline-flex` 覆盖 `hidden`，只读模式仍露出「＋ 新建需求」；加 `[hidden]{display:none!important}` 后复核通过。
- 未验证：控制台模式（按钮/审批/弹窗）未做浏览器自动化交互回归；未引入前端构建链与单元测试。
- 文档：更新 [[wiki/平台看板]]（实现/端点/界面重设计/验证证据/限制）；无新增页面，index 条目描述仍适用。

## [2026-11-13] feat | 控制台改左右菜单并支持多项目
- 触发：用户指示「继续优化改为左右菜单的web管理界面模式，总览页、操作页、设置、查询等页面、另外当前是多项目，应该按多项目进行设计」。
- 前端：`platform/console/index.html` 重写为侧边栏外壳（品牌 + 项目下拉 + 六项导航 + 模式/账本状态），右侧为页头 + 视图区；`app.js` 改为 hash 路由单页应用（`#/overview`、`#/tasks`、`#/approvals`、`#/actions`、`#/query`、`#/settings`），`settings.js` 承载设置页 7 分区表单；删除已并入单页的 `intake.html/js`、`config.html/js`，`/intake`、`/config` 由后端回落到 `index.html`。轮询只在总览/任务/待审批重建视图，操作页仅刷新动态片段以免清空表单。
- 后端：新增 `list_projects()` / `project_paths()`（单段目录名 + `resolve()` + `relative_to(PROJECTS_DIR)` 防穿越）与 `GET /api/projects`、`GET /api/search`；所有 API 支持 `?project=` / 请求体 `project`，`/api/mode` 增 `default_project`；`POST /api/config`、`/api/intake` 与任务动作都写到目标项目，控制台按项目缓存 `ControlRunner`，图执行统一加进程级 `_GRAPH_LOCK`（`graph.RIVER_WS` 是全局状态）。
- 安全：项目名与静态路径仍做越界校验；未知/非法项目 404；写请求防护（`X-Control: 1` + Origin 白名单 + ≤64KB）不变。
- 验证：`ac_regression` 286 条 PASS=284 FAIL=0 SKIP=2（新增项目列表、非法项目 404、关键字查询三条断言；CONSOLE-01/CONFIG-01 文案断言随迁）；`graph_smoke` 12/12、`smoke_test` 20/20、`prompt_eval` 17/17。`--control` 实例 8891 浏览器目视：六项导航与项目下拉正常，设置页表单与「保存配置」可见，切到 proj-b 后任务数 12→1、状态栏显示 proj-b/state.db、总览同步刷新，控制台无 JS 错误。
- 未验证：「保存配置」提交、审批弹窗与产物 iframe 的浏览器自动化交互；两个项目并行跑图（当前串行）；与 CLI 混用的跨进程互斥。
- 文档：更新 [[wiki/平台看板]]（信息架构、多项目路由与隔离、端点、验证证据、限制/同步演进修正）；[[index]] 条目描述更新。

## [2026-11-13] feat | 图文需求 + 需求 Agent 标准化 + G1 原始/标准对比评审
- 触发：用户指示「在新增需求改为 文本+图片 填写，然后在通过需求角色agent转化为标准规范的格式输出评审，评审时需要展示原需求以及agent生成的标准需求」。
- 录入：`platform/intake.py` 新增 `images`（≤4 张、单张 ≤2MB、仅 PNG/JPEG，校验 base64 与文件头），落盘 `<ws>/<task_id>/attachments/`，`requirement.json` 记录附件元数据，归一化文本追加 `## 参考图片`；`/api/intake` 请求体上限单独放宽到 12MB，其余写接口仍 64KB。
- 生成：需求节点扫描任务 `attachments/`，`worker.run_codex` 支持 `images` 并以 `codex exec -i` 送入模型；`requirements-gen` 模板升 1.2.0，新增 `$attachments` 与图文冲突处理约束；manifest 同步。
- 评审：`/api/task/detail` 增 `original`/`attachments`；新增 `GET /api/attachment`（限任务目录内 PNG/JPEG）；前端「操作」页加参考图片多选与缩略图预览，G1 待审批卡片与任务详情新增「原始 vs 标准需求」按钮，双栏展示结构化原始需求+图片与 `REQUIREMENTS.md`。
- 验证：`ac_regression` 291 条 PASS=289 FAIL=0 SKIP=2（新增带图 201、详情原始字段+附件、图片回读、越界 404、非法类型 400 不落库）；`graph_smoke` 12/12、`smoke_test` 20/20、`prompt_eval` 17/17；`codex exec -i` 真模型冒烟返回码 0；8879 浏览器目视参考图片控件与对比模态，无 JS 错误。
- 未验证：真实带图任务的完整 G1 生成质量；WebP/GIF 在录入层被拒。
- 文档：更新 [[wiki/平台看板]]（图文需求与对比评审小节、端点、体积/字段约束、验证证据）；[[index]] 条目描述更新。

## [2026-11-13] feat | 人工驳回必填理由 + 上游 Agent 按理由判断修订
- 触发：用户指示「人工审核节点，用户拒绝时要添加理由，然后上一步的agent判断要不要根据退回原因生成新的方案」。
- 账本：`schema.sql` approvals 增 `reason` 列（旧库 `ALTER TABLE` 迁移）；`decide_approval(..., reason)` 写入；新增 `latest_rejection_reason()`。
- 图：review_g1/g2/g3 驳回时取最近驳回理由写入图状态 `rejection_reason` 并转 `running` 回上游节点（原有回环语义不变）；需求/设计节点把理由作为 `$rejection_feedback` 注入模板并留痕 `requirements_revise`/`design_revise` 门禁结果；每闸门回环上限 `MAX_REVISES=5`，超限记 `revise_limit_*` 并终态 rejected。DeliveryState 补声明 `approved_g3`（此前 review_g3 写入被静默丢弃的隐患一并修复）。
- 模板：requirements-gen 1.3.0 / design-gen 1.2.0 增 `$rejection_feedback` 与「先判断反馈是否成立：成立则修订，不成立则保留原文并注明分歧」约束；manifest 同步。
- 控制台：`/api/task/decide` 驳回必填 `reason`（1~2000 字，缺/超 400），reason 贯通 enqueue/execute/decide_approval；`run_task.py reject --reason` 必填；审批 SELECT 与任务详情带出 `reason`；前端驳回按钮弹理由模态（空理由不放行），已决审批表与详情新增「理由」列。
- 验证：`ac_regression` 295 条 PASS=293 FAIL=0 SKIP=2（新增驳回缺理由 400、带理由 202、理由入账+requirements_revise 留痕）；`graph_smoke` 12/12、`smoke_test` 20/20、`prompt_eval` 17/17；8879 浏览器核对驳回模态与无 JS 错误。
- 未验证：真模型在真实驳回理由下「判断不修订」的分支；G3 驳回回实现节点时理由未注入实现 prompt（实现走设计产物，超出本轮范围）。
- 文档：更新 [[wiki/平台看板]]（R1 小节、端点、防护、验证证据）；[[index]] 条目描述更新。
