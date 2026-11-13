<!-- river 项目级 Agent 记忆：L0 自动采纳层维护（V2.2 §10.3），人工可编辑，变更留痕于 retro_queue -->

## Known Pitfalls

## Project Conventions
- [C-002] river 门禁命令映射：web 测试是 `npm run test -w @river/web`（无 :unit 后缀），api 才是 test:unit。
  来源: task-001 lane-fe 门禁首次执行失败后修正 | 证据: package.json scripts | 回滚: 删除本行（一键回滚，V2.2 §10.3 安全网）
- [C-001] codex workspace-write 沙箱内 vitest 无法运行（esbuild spawn EPERM）——门禁必须由编排层在沙箱外执行；Worker 沙箱内仅 typecheck 级自检。
  来源: task-001 双 lane（be/fe 均复现） | 证据: codex 报告 + 编排层复跑成功 | 回滚: 删除本行（一键回滚，V2.2 §10.3 安全网）
