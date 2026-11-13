---
type: concept
status: draft
updated: 2026-11-13
tags: [staging, docker, 镜像, digest, C4, 验证]
---
# C4 staging 镜像治理

## 结论

- **digest pin 已落地**：river 的 staging 基座镜像从浮动 tag 改为 `registry@sha256:…`，共 3 处：
  - river 仓库 `infra/docker/Dockerfile` 的 `NODE_IMAGE` / `NGINX_IMAGE`（node、nginx）
  - river 仓库 `docker-compose.staging.yml` 的 postgres `image`
  - 三个 digest 均与本地 `RepoDigests` 一致，`docker image inspect <ref>` 可解析，不触发拉取。
- **构建与运行已验证**：`docker compose config -q` 通过；`build` 成功（基座层 `CACHED`，web 镜像 `sha256:334522d04f08…`）；`up -d --wait` 三容器 healthy；`web=200`；`POST /api/guests` 返回 200 且由 postgres 生成 player id（`0d5bda30-…`）。
- **扫描已在 GitHub Actions 完成（2026-11-13）**：本地网络无法拉取 trivy 漏洞库（`mirror.gcr.io` / `registry-1.docker.io` 不可达，`ghcr.io` 大 blob 5 分钟超时），改为在 runner 上执行 `.github/workflows/image-scan.yml`（`workflow_dispatch`，矩阵覆盖 3 个 pin 的镜像，`severity=HIGH,CRITICAL`，`exit-code=0` 只记录不拦截）。运行 [43377737668](https://github.com/shinelincx/agentcode/actions/runs/43377737668) 三个 job 全 success，结果见下节。
- **结论修正**：上一版「扫描未完成（blocked）」仅指本地环境；扫描本身已可闭环。**但「扫描无高危」不成立**——基座镜像存在 5 个 CRITICAL、约 151 个 HIGH 级问题（以 trivy DB 当日数据为准），属上游镜像库存问题，不是 digest pin 引入的。
- **可复现性为部分证据**：基座 digest 固定 + `package-lock.json` + `npm ci` 构成确定性基础；但未做两次 `--no-cache` 构建的 bit-for-bit 比对。

## 扫描结果（2026-11-13，trivy v0.36.0 action，DB 取自 mirror.gcr.io）

| 镜像 | 目标 | HIGH | CRITICAL |
| --- | --- | --- | --- |
| postgres@cf78e766 | 2 个目标（含 Go `stdlib`） | 31 + 25 | 0 + 1 |
| nginx@65645c7b | 1 个目标（alpine 3.24.1） | 67 | 3 |
| node@fb4cd12c | 2 个目标（含 npm `package.json`） | 7 + 21 | 0 + 1 |

CRITICAL 明细（均已修复版本、可直接升级基座）：
- postgres：`CVE-2025-68121`（`stdlib` crypto/tls 证书校验）
- nginx：`CVE-2026-111497`（`brotli-libs` 越界写）、`CVE-2026-31789`（`libcrypto3` / `libssl3` 堆溢出，均给出 3.3.7-r0）
- node：`CVE-2026-59873`（`tar` 6.2.1，DoS）

处置建议（提案，未实施）：优先升级 nginx / node 基座到含修复版本的 tag 并重新 pin digest；postgres 的 `stdlib` 属镜像内构建产物，需等上游发布。阈值策略（`exit-code` 拦截）待升级后再定，避免当前直接红线。

## 来源

- 镜像 digest（本地 RepoDigests）：
  - node：`sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293`
  - nginx：`sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10`
  - postgres：`sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685`
- 命令：`docker image inspect <ref>` ×3（均返回 image ID）；`docker compose -f docker-compose.staging.yml config -q`；`build`；`up -d --wait`；`curl POST /api/guests`；`down`。
- 探针纠错：首次用 `GET /api/health` 得到 404，核查 `apps/api/src` 无该路由，404 属探针错误而非部署失败；改用真实写接口后 200。

## 冲突与未知

- **扫描口径**：结果取决于 trivy DB 快照；`exit-code=0` 表示只记录不拦截，尚未设置阈值门禁。HIGH/CRITICAL 数量会随上游修复与 DB 更新变动，需重跑 `image-scan` 刷新。
- **环境耦合**：digest 指向 `docker.m.daocloud.io` 镜像；换环境需同步该 registry 与 digest，否则构建会尝试拉取。
- **compose 内硬编码** staging 口令 `river_dev_password` 与 `INVITE_CREDENTIAL_KEY`：staging 专用，生产不可复用；不属 C4 范围，记为待处理。
- 未验证 bit-for-bit 可复现（两次 `--no-cache` 构建比对）。
- 未在 CI 运行 staging（C3 首跑仍待推送授权）。
- 扫描 artifact 未上传（GitHub artifact 下载域名在本机不可达），证据取自 job 日志。

## 关联

- [[wiki/P2规划]]：C4 行
- [[wiki/P1实施进度]]：staging Docker 验收
- [[wiki/C5-supervisor验证]]：门禁/容器的进程终止边界
