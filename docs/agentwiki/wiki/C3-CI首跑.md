---
type: concept
status: draft
updated: 2026-11-13
tags: [CI, GitHub Actions, gitleaks, 最小权限, C3, 验证]
---
# C3 CI 远端首跑

## 结论

事实（2026-11-13，GitHub Actions 实测）：平台仓库首次推送至 `shinelincx/agentcode`（公开仓库），`agentcode-checks` 工作流两次运行均以 `checks` + `gitleaks` 双 job 收尾；**第 2 次（`c3f7947`）全绿**，C3「CI 全绿且不误报」达成。

- 第 1 次运行 [43374644039](https://github.com/shinelincx/agentcode/actions/runs/43374644039)：`checks` 在 AC regression 失败（Linux 专用路径问题），`gitleaks` 因 `ambiguous argument 'bb2a711^..496949a'` 失败——与是否泄漏无关。
- 第 2 次运行 [43375084226](https://github.com/shinelincx/agentcode/actions/runs/43375084226)（`c3f7947`）：`checks` = success、`gitleaks` = success，无泄漏告警。

## 首跑暴露的问题与修复

| 问题 | 证据 | 修复 |
| --- | --- | --- |
| `_repo_path()` 回退用 `RIVER_WS.parents[2]`，临时工作区（`/tmp/ac-xxx`）只有 2 层父目录 → `IndexError: 2`，12 个用例报「用例执行异常」 | 日志 traceback 指向 [graph.py](../../../platform/graph.py) `_repo_path` 与各用例 `g.RIVER_WS = ws` | 默认路径改为按 `PROJECTS_DIR` 推导 `<平台上级>/river`；`RIVER_WS` 被重定向时回退 `<工作区>/repo`，既不越界也不向不可写根目录建目录 |
| 同一回退在 3 层路径上得到 `/river`（容器根目录不可写），MULTI-01 两个临时项目 `status=gate_failed` | 本地用 `TEMP=C:\acsim` 复现：旧实现会把产物写到 `C:\river`（根目录），CI 上 `/` 不可写 | 同上；MULTI-01 双 lane 在浅路径下恢复 `done` |
| MULTI-01 断言 `Path("d:\\project\\pdca-erp").name == "pdca-erp"` 在 Linux 失败（PosixPath 不按反斜杠切分） | `repo_path=d:\project\pdca-erp` | 断言前把分隔符归一化为 `/` 再取末段 |
| `gitleaks-action@v2` 按事件 `before..after` 生成增量 `log-opts`；首次推送的根提交没有父提交，得到 `<root>^..HEAD` → git 报 `ambiguous argument`，job 失败 | 日志 `gitleaks cmd: ... --log-opts=...bb2a711^..496949a` | 改为下载固定版本二进制（v8.24.3）执行 `gitleaks git --redact --exit-code=2 --config .gitleaks.toml` 全历史扫描 |

## 推送方式说明（环境限制）

- 本机到 `github.com:443` 的 HTTPS 被阻断（`Connection was reset` / 300s 超时），但 `ssh.github.com:443`、`api.github.com` 可达。
- 因此首次推送使用 `git push ssh://git@ssh.github.com:443/shinelincx/agentcode.git master:main`（远端默认分支为 `main`，本地为 `master`）。`origin` 仍指向 HTTPS URL，直连推送不可用；后续推送需复用该 SSH 地址或在网络放行后改回 `origin`。
- 远端仓库公开（`private: false`），推送前已收紧 `.gitignore`：忽略 `projects/*/*/`（任务工作区，含 `workers/*/codex-home/auth.json`）、`projects/*/codex-home/`、`platform/codex-home-iso/`、`*.db*`、`__pycache__/`。实际入库 80 个文件、约 1.13 MB。

## 证据

- 提交：`496949a`（平台入库，80 文件 / 1.13 MB）、`c3f7947`（CI 修复）。
- 本地预检：`gitleaks git --redact --config .gitleaks.toml` → 2 commits、no leaks；`gitleaks git --staged` → no leaks。
- 本地回归（含 `TEMP=C:\acsim` 浅路径复跑）：`ac_regression` 282 条 PASS=280 FAIL=0 SKIP=2、`graph_smoke` 12/12、`smoke_test` 20/20、`prompt_eval` 17/17。
- 远端：`checks` 四步（prompt_eval / ac_regression / graph_smoke / smoke_test）全 PASS；`gitleaks` 全历史扫描无告警。

## 冲突与未知

- KILL-01 在 Linux 跳过（`taskkill /T` 与 `.cmd` 假 shim 为 Windows 专用），故 POSIX `killpg` 分支仍无 CI 覆盖，见 [[wiki/C5-supervisor验证]]。
- 工作流依赖固定版本（`langgraph==1.2.4` 等）在 runner 上安装成功，但未验证依赖上游变更后的兼容性。
- 仓库为公开仓库：历史中若曾提交凭据，gitleaks 会告警；当前两次扫描均无告警，但未做 GitHub Secret Scanning / 推送保护配置核查。
- `origin` 仍为不可直连的 HTTPS URL，后续推送方式待网络放行后再切换（属环境问题，非平台缺陷）。

## 关联

- [[wiki/P2规划]]：C3 行
- [[wiki/节点规则]]：CI 接入节
- [[wiki/权限矩阵配置清单]]：G-03 CI 最小权限
- [[wiki/C5-supervisor验证]]：KILL-01 的 Windows 依赖
