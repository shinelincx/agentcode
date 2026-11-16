---
title: Prompt 模板评审
type: 设计
status: draft
updated: 2026-11-16
sources: [_templates/manifest.yaml, _templates/prompts/, _templates/evals/golden.yaml, platform/templates.py, platform/graph.py, platform/ac_regression.py, wiki/角色配置.md]
---

# Prompt 模板评审（2026-11-16，提案未实施）

## 决策

用户要求分析 7 个 Prompt 模板并提出优化建议。本页为**提案记录**：所有建议未实施，实施需另行授权并按 SemVer bump 模板版本、同步 golden.yaml 断言、跑 PROMPT-01/EVAL-01 回归。

## 评审范围与依据

- 7 个模板：requirements-gen 1.4.0、design-gen 1.2.0、prototype-gen 1.1.0、implement-lane 1.2.0、gate-fix 1.2.0、review-diff 1.2.1、retro-gen 1.2.0。
- 渲染层 [templates.py](../../../platform/templates.py)：required 参数缺失抛 KeyError、optional 缺省、列表 join、`templates.lock` 哈希快照与漂移检测。
- 消费方门禁：`graph.py` 的 `_doc_gate`（必需标题）、`_lanes_from_design`（归属表/lane 解析）、`_ac_coverage`（AC 全覆盖）、review 结论 `PASS/FAIL` 解析。
- 黄金用例：`_templates/evals/golden.yaml` 17 例（渲染层断言；`--live` 产出断言未建立基线）。
- token 成本（prompt_eval 实测）：requirements ~723、design ~564、prototype ~430、implement ~355、gate-fix ~318、review ~580、retro ~430。

## 总体评价

结构统一（六段式）、输出契约基本可机检、治理机制（SemVer + lock 哈希 + 漂移检测 + 黄金用例）健全。主要缺口集中在：信息注入不完整（原型看不到参考图、复审看不到上轮结论）、个别硬约束未闭环（lane 值域、实现阶段弱化测试）、产出语言未声明。

## 发现与建议

### P1（有实际失败模式支撑）

| # | 模板 | 问题 | 依据 | 建议 |
| --- | --- | --- | --- | --- |
| F-1 | design-gen | lane 值域未显式约束：归属表示例只给 be/fe，Agent 写 `backend`/`frontend` 时 `_lanes_from_design` 无法识别 | PROTOTYPE-01 已有「未识别 lane 值回退关键词」测试，说明该路径真实存在；回退会绕过归属表精确推导 | Constraints 加一条：文件归属表 lane 列取值仅限 `be` / `fe` |
| F-2 | implement-lane | 缺「不弱化/删除既有测试」约束：gate-fix 有此约束，实现阶段没有；测试文件在 scope 内时 Agent 可能先改测试 | gate-fix.tmpl Self-check 第 3 条与 implement-lane 的约束集合对比 | Constraints 加一条：不得删除/弱化既有测试或断言来通过门禁 |
| F-3 | prototype-gen | 需求参考图片只进 requirements 节点，原型 Agent（最需要视觉参照的角色）看不到图 | `graph.py` `prototype_node` 未传 `images`，模板无 `$attachments` | 模板增 optional `$attachments`，`prototype_node` 扫描 `attachments/` 后以 `codex -i` 同步送入 |

### P2（契约与信息完整性）

| # | 模板 | 问题 | 建议 |
| --- | --- | --- | --- |
| F-4 | 全部 | 产出语言未声明：指令英文、章节标题中文，正文语言不稳定 | 各模板 Output contract 声明产出语言（建议：跟随原始需求语言，或固定中文） |
| F-5 | review-diff | severity 无枚举：`(severity)` 自由发挥，不可机检分级 | 定义 `high/medium/low` 三级并给一行判级标准 |
| F-6 | review-diff | 二轮复审无历史：返工后第二轮 review 只看新 diff，不知道上轮 FAIL 理由，可能重复报已修复问题 | 增 optional `previous_review` 参数，注入上轮 `## 问题` 摘要 |
| F-7 | 渲染层 | `scope` 空值渲染为空白：`Files you own (scope): ` 后空串，误导 Agent 无边界 | implement-lane 的 scope 改 required（图侧保证），或渲染层对空列表给「未声明（以 lane_instruction 为准）」占位 |

### P3（可选打磨）

| # | 模板 | 问题 | 建议 |
| --- | --- | --- | --- |
| F-8 | requirements-gen | 强制 ≥2 条 AC：一句话需求可能凑数 | 允许 1 条并说明原因（门禁只查标题，不查条数，改模板无风险） |
| F-9 | gate-fix | 未告知 attempt 语义 | 注明这是有限修复机会，再失败转人工 |
| F-10 | retro-gen | 「经验」条目与 retro_queue 摄取格式（C-xxx/type/scope）不对齐，自动记忆需人工转写 | 经验条目带 `[C-序号]` 前缀与 type/scope 标注，为 L0 自动摄取铺路 |
| F-11 | 流程 | golden 只有渲染层断言，`--live` 无基线报告 | 每次模板 bump 后跑一轮 `--live`（消耗真实额度，纳入预算），留存基线 |

## 实施约束（若获批）

- 任一模板内容改动须 bump：manifest 顶层 + 该模板版本（SemVer；F-3/F-6 加参数为 minor）。
- golden.yaml 同步补断言（如 F-1 的 `be` / `fe` 字面、F-5 的 severity 枚举）。
- 回归最低集：PROMPT-01 + EVAL-01 + 受影响节点专项（F-3 需 PROTOTYPE-01，F-6 需 REVIEW-01/02）。
- F-3/F-6/F-7 涉及 `graph.py` 调用侧改动，不只改模板。

## 未验证项

- 各建议对真实模型产出的实际改善幅度未验证（需 `--live` 对比）。
- F-6 二轮复审重复报问题的频率无运行证据，属推断（基于返工链路语义分析）。
- 产出语言不稳定（F-4）基于模板文本分析，未统计历史产物语言分布。
