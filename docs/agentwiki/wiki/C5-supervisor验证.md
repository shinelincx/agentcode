---
type: concept
status: draft
updated: 2026-11-13
tags: [supervisor, 进程管理, 超时, C5, 验证]
---
# C5 supervisor 自愈边界验证

## 结论

事实（2026-11-13，Windows 本地实测）：平台用 `codex exec` 同步子进程执行，**中间没有 supervisor**，因此：

- **软停（杀进程树）不自愈**：杀掉 node shim 及其子进程后，60 秒内未出现新 codex 进程，产物也不再增长。
- **只杀直接子进程会留下孤儿**：实测 codex 子进程继续运行，并在 5 秒内写完 40 个文件。旧实现 `subprocess.run(timeout=...)` 超时时正是这种行为——只终止 shim，codex.exe 仍在写仓库。
- **硬停语义**：树杀即终态；平台层用 ledger 记录节点失败，重启不会自动续跑（AC-02e 覆盖旧 epoch 拒写）。
- **与 V2.2 修订的关系**：「软停自愈」属 `codex-code-mode-host`（IDE 宿主 supervisor）行为，不能套用到本平台的 `codex exec`；本平台正确语义是「树杀 + 编排层不重启」。

建议并已实施：`worker._kill_tree()`（Windows `taskkill /F /T`，POSIX `killpg(SIGKILL)`），`run_codex` 与 `run_gate` 超时改为树杀，并记录 `TIMEOUT` 标记。

## 来源

- 探针 1（软停）：临时仓库 `%TEMP%\c5-probe`，codex 生成 `out/f01..f40.txt`。第 12 秒 snapshot 为 1 个文件，随后 `taskkill /F /T` 杀 PID 36900；codex 进程从 7 个降为 5 个（其余为 IDE 其它会话，PID 前后不变），此后 12 次采样（每 5 秒）文件数固定在 2（第 2 个文件是终止前的在途写入）。
- 探针 2（孤儿）：临时仓库 `%TEMP%\c5-probe2`，kill 时只有直接子进程 12236 被杀，其子进程 36664 存活；5 秒后 `out/` 出现 40 个文件并在随后 45 秒保持不变。
- 回归证据：`ac_regression` KILL-01 共 4 条断言（codex / 门禁超时均返回失败且心跳停止）；2026-11-13 全量 282 条 PASS=280 FAIL=0 SKIP=2。
- 代码：[worker.py](../../../platform/worker.py) `_kill_tree`（第 31 行）、`run_codex`（第 48 行）、`run_gate`（第 108 行）；[ac_regression.py](../../../platform/ac_regression.py) `kill01`。
- 上游依据：V2.2 修订中 kill 语义条目；P0a 启动确认中的宿主自愈观测。

## 冲突与未知

- P0a 记录的「约 50 秒自愈」属于 IDE 宿主 supervisor，与本页结论不冲突，但两者不可混用；若后续平台改为调用宿主 supervisor，需重新验证。
- Windows 无 SIGTERM 等价软停；POSIX 分支（`killpg`）未在 Linux 实测。
- 未验证真实 1200 秒超时路径：探针用 12 秒人工击杀模拟，KILL-01 用 2 秒超时 + 假 shim 验证树杀。
- 探针调用真实模型，只用于观察进程行为，未纳入自动化回归（成本原因）。

## 关联

- [[wiki/P2规划]]：C5 行
- [[wiki/V2.2修订]]：kill 语义修订
- [[wiki/权限矩阵配置清单]]：G-05「test/deploy 以宿主权限执行」与本页硬停边界相关
