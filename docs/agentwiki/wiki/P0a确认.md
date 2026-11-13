---
type: knowledge
status: in-progress
updated: 2026-11-11
tags: [p0a, verification, rollout]
---
# P0a 确认

按 [[wiki/V2.2修订]] §13.2 启动 P0a。分两部分：自动检测（本页记录证据，2026-11-11 执行）与人工确认（待团队输入）。授权依据：用户指令「按这个方案启动 P0a 确认」（2026-11-11）。

## 自动检测结果（2026-11-11 执行）

| 检测项 | 结果 | 证据 | 对 P0a 的意义 |
| --- | --- | --- | --- |
| 目标 OS | Windows 11 家庭中文版（NT 10.0.26200） | Get-CimInstance Win32_OperatingSystem | 宿主即 Windows；方案 3.6 差异表生效（ReadDirectoryChangesW、文件锁、路径大小写、job object） |
| 执行器 codex | <span class="tag">已安装</span> codex-cli 0.137.0 | `codex --version` | 主执行器就位 |
| 执行器 claude | <span class="tag">已安装</span> 2.1.197 | `claude --version` | 备执行器就位；AC-02/重试 L2「换执行器」具备条件 |
| 执行器 dsh | 未安装 | Get-Command 未命中 | 不阻塞（选型中为第三备选） |
| git | 2.54.0.windows.1 | `git --version` | worktree / lane / 契约 tag 机制可用 |
| node | v24.16.0 | `node --version` | 模板层工具链可用 |
| Python + SQLite | Python 3.14.3，sqlite3 3.50.4 | `python -c "import sqlite3"` | 账本（P1 最小协议子集，V2.2 §4.6）实现环境就绪 |
| docker | 未安装 | Get-Command 未命中 | 不阻塞 P0a；为部署角色（后期阶段）前置依赖，P1 后半程需安装 |

## 决策记录（2026-11-11，用户确认）

- **目标 OS = Windows 本机**：3.6 差异表 Windows 列生效（ReadDirectoryChangesW、文件锁重试、路径大小写规范化、job object 进程管理）；AC harness 按 Windows 路径实现。
- **计量/认证模式 = 全部订阅登录**（codex、claude 均是）：计量单位 = call_count / token_count，金额仅月度参考；对账源 = 订阅 usage 报表（天级粒度）。U 初始值取订阅模式档（200 calls/任务）。
- 语义变化（订阅模式的配额含义）：配额从「金额预算」变为「订阅资源保护」——防单项目挤占订阅额度，超限动作照旧走状态机（ALERT/DEGRADED/PAUSED）。
- **审批人值守 = 单人值守**：4 个审批位（G1/G2/G3/L1）由用户本人承担（试点期）；表单 A 备份人栏留空，人员变更时重填。
- **试点项目月度配额 = 20,000 calls/月**（订阅资源保护档）：状态机 80%/90%/100% 阈值基于此值触发。

## 退出标准进度（5 项）· 全部关闭

| # | 退出标准（V2.2 §13.2） | 状态 |
| --- | --- | --- |
| ① | 每执行器的计量单位与对账源确认可用 | <span class="tag">已关闭</span> 订阅/调用数计量；本地计量源 = codex 输出 tokens used（发现 C）；usage 报表可访问性用户已验证（2026-11-11「已验证」） |
| ② | kill/重跑最小验证通过 | <span class="tag">已关闭</span> 见 AC-02 执行记录 |
| ③ | 全部审批人登记（含 L1 经验审核） | <span class="tag">已关闭</span> 单人值守；归档表单 A 已填写（[p0a-forms-filled.html](../../p0a-forms/p0a-forms-filled.html)，电子确认依据见表单脚注） |
| ④ | 凭据分层与轮换周期书面确认 | <span class="tag">已关闭</span> 归档表单 B 已填写（分层确认 + 红线确认 + 盘点：Git token 既有，CI/staging 按方案节奏创建后补录） |
| ⑤ | 单任务上限 U / 在途并发 N 初始值 | <span class="tag">已关闭</span> N=1；U=200 calls/任务；配额 20,000 calls/月 |

## 试点项目确认（2026-11-11，用户确认）

- **proj-a = river**（d:\project\river，river-table）：npm workspaces monorepo（apps/web + apps/api + packages/*），TypeScript 全栈，101 源文件。
- 选型依据：① apps/web 与 apps/api 天然对应 V2.2 双 lane 并行模型；② 门禁现成——build / typecheck / test:coverage / Playwright E2E / verify 一键全跑，P1 的 gate_results 直接接入；③ 规模适中（上下文可控、跑得快）且代码真实（含 .github CI）。
- 其余项目候选（P3 四项目阶段再定）：poker（Node，20k 文件）、frame（Go，8.7k 文件）、IBM-Platform / project-System（极小，真实度待评估）、rhea（文档/原型型，866 文件，含 AGENTS.md/CONTEXT.md）。

## P0a 关闭声明（2026-11-11）

五项退出标准全部关闭，试点项目已定。**P0a 正式关闭，具备进入 P1 的条件。**

P1 启动前置待办（不阻塞 P0a 关闭，源于 AC-02 发现）：
1. **修订 V2.2 §9.2 / §8.4 的 kill 语义**（发现 B：执行器有 supervisor 自愈，「真停」需杀 supervisor）——P1 Week 1 前完成方案修订。
2. 执行器路径：npm 版 codex CLI 已更新至 0.167.1（2026-11-11），gpt-6.1-terra 与 gpt-6.1-sol 复测通过，**定为主执行器**；桌面版 0.166.0-alpha.6 为备选（发现 A 修正后结论）。
3. 提交前凭据扫描工具接入（表单 B 红线项，P1 Week 1）。

## AC-02 执行记录（2026-11-11，② 的验收证据）

**流程**：test-ac02/ 目录 git init → codex exec 生成 10 篇技术随笔任务 → 15:38:05 强杀进程树（3/10 文件完成、note04 写入中）→ 任务经 supervisor 自愈自动续跑 → 15:41:44 全部完成。

**通过判定**：✅ 10/10 文件；主题顺序完全符合指令（git workflows → canary release）；词数 478-509（符合 ~500 要求）；**note04.md（被杀时正在写的文件）内容完整无截断**；无重复、无损坏。证据目录：`d:\project\AgentCode\test-ac02\`（保留归档）。

**执行中发现（对 P1 的直接输入）**：

- **发现 A（已修正归因）**：测试时 npm 版 codex CLI 0.137.0 订阅通道报所有模型 model_not_found；**根因是 CLI 版本过旧**（用户指出，2026-11-11）。更新至 **npm 0.167.1** 后复测：**gpt-6.1-terra ✅**（返回 OK，tokens 18,505）、**gpt-6.1-sol ✅**（返回 OK，tokens 20,962），均正常。桌面版 codex.exe 0.166.0-alpha.6 同样可用（AC-02 验证时使用）。→ P1 执行器路径决策：**npm 版 0.167.1 为主**（版本最新、路径干净），桌面版为备选。
- **发现 B（重试语义）**：kill 进程树后约 50 秒，任务被 **supervisor 自动重启续跑**（codex-code-mode-host 充当监督者）。→ V2.2 §9.2 的重试分级需修正假设：执行器自带进程级自愈；编排器要「真停」需杀 supervisor，仅杀 worker 反而触发自愈——对 AC-08（错误重试分类）与预算暂停（§8.4 PAUSED「在途跑完」的实现）均有影响。
- **发现 C（计量数据源）**：codex exec 输出末尾自带 `tokens used` 统计（如 9,663）。→ 订阅模式下本地计量可从执行器输出解析 token 数，无需调用前接口；对账源（usage 报表）仅作日级校准。
- **发现 D（resume 签名）**：`codex exec resume` 不接受 `-s` 沙箱参数（继承原会话设置）。→ P1 编排器 resume 调用注意。
- **发现 E（启动开销）**：codex exec 冷启动初始化约 2 分钟（插件同步 + MCP 启动 + websocket 超时后 fallback HTTP），热启动约 36 秒。→ P1 Worker 池化/保活的依据。
- **发现 F（harness 实现）**：Trae 后台 shell 子进程存活、Start-Process 脱离进程会被终端生命周期清理；kill 注入须按 PID 精确杀（机器上常驻用户自己的 codex 会话进程）。→ AC harness 进程注入实现注意。

**待办修正**：V2.2 §9.2 / §8.4 的「kill 语义」需按发现 B 修订（P1 启动前）。

## 初始值建议（⑤）

- N（在途任务并发）= **1**：P1 单项目试点串行。
- U（单任务用量上限）：订阅模式按调用数默认 **200 calls/任务**；API-key 模式按金额默认 **$5/任务**。
- 校准：P1 Week 2 起按 cost_ledger 实测 P95 × 2 更新（V2.2 §8.5）。

## 待人工确认项（关闭 P0a 所需输入）

1. 计量/认证模式：~~待确认~~ 已定——全部订阅登录（见决策记录）。
2. 对账源与权限：账单 API / usage 报表是否可访问、粒度与延迟。
3. 审批人登记：试点最小集 3 个闸门（需求/设计/staging 验收）+ L1 经验审核的责任人与响应时效（默认 4 小时）。**模板已生成：[p0a-forms.html](../../p0a-forms/p0a-forms.html) 表单 A**。
4. 四项目技术栈与跨项目依赖明细；试点项目（proj-a）选定。
5. 试点项目配额数值。
6. 凭据分层与轮换周期书面确认。**模板已生成：[p0a-forms.html](../../p0a-forms/p0a-forms.html) 表单 B**（含平台层凭据盘点表与红线确认清单）。

## 结论

- 自动可验证项全部通过；剩余未完成项均为人工输入类，无技术阻塞。
- 目标 OS 建议值（Windows 本机）与计量模式倾向待用户确认后，①⑤ 即可关闭；② 在 P1 首个真实任务执行时验证。

## 关联

[[wiki/V2.2修订]] · [[wiki/方案演进]] · [[wiki/待核实问题]]
