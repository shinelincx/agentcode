<!-- proj-b 项目级 Agent 记忆：C1 第二项目接入（2026-11-13，pdca-erp）。 -->

## Project Conventions
- proj-b 接入独立代码仓库 `d:\project\pdca-erp`（PCBA ERP）。
- 双 lane：`frontend/`（Vue 3 + Vite + TypeScript）与 `backend/`（Spring / Maven 多模块 pcba-erp-*）。
- 前端门禁（仓库根目录执行）：
  - typecheck：`npm --prefix frontend run type-check`（vue-tsc --noEmit，需先 npm install）
  - unit：`node --test frontend/scripts/*.test.mjs`（零依赖）
  - build：`npm --prefix frontend run build`
- 后端门禁：CI 用 `mvn -B -ntp verify -pl pcba-erp-admin -am`，但本机 mvn 不在 PATH，暂不入门禁。
- 类型检查状态（2026-11-13）：`type-check` 已从约 150+ 处错误修复至 0；`unit` 22/22；`build` 成功。
  修复方式为对照 `backend/pcba-erp-*` Java 实体补齐 `types/models.d.ts` 字段，并修正视图侧类型问题。
  待业务确认：`SalSalesOrder.deliveryAddress`、`PdmMaterialCategory.materialScope` 后端实体不存在，当前按 UI 状态局部扩展。
- lane 分支前缀 `proj-b-`，与 river 及 pdca-erp 日常分支隔离。
- 凭据基目录共享 river 的只读 `codex-home`，Worker 各自复制，不产生第二份密钥副本。
