---
type: concept
status: draft
updated: 2026-11-13
tags: [staging, docker, 镜像, digest, C4, 验证]
---
# C4 staging 镜像治理

## 结论

- **digest pin 已落地，并完成一次基座升级（2026-11-13）**：river 的 staging 基座镜像统一用 `tag@digest` 固定（tag 便于追溯、digest 防漂移），共 3 处：
  - river 仓库 `infra/docker/Dockerfile` 的 `NODE_IMAGE` / `NGINX_IMAGE`（node、nginx）
  - river 仓库 `docker-compose.staging.yml` 的 postgres `image`
  - `.github/workflows/image-scan.yml` 的矩阵同步同一组 pin（扫描对象 = 实际构建对象）
- **升级内容**：node `20-alpine` → `22-alpine`（EOL 停更），nginx `1.27.5-alpine` → `1.30.6-alpine`，postgres `16-alpine` → `16.16-alpine3.24`；三者基线均升到 alpine 3.24.4。
- **复扫结果：CRITICAL 5 → 1、HIGH 151 → 39**：运行 [43380314435](https://github.com/shinelincx/agentcode/actions/runs/43380314435) 三个 job 全 success。
- **构建与运行已验证（升级后重跑）**：`docker compose config -q` 通过；`build` 成功（`river-api` 各层均为本次新建，非复用旧缓存）；`up -d --wait` 三容器 healthy；`GET /` 200；`POST /api/guests` 200 且 player id（`7676990d-…`）由 postgres 生成；api 日志无 error。
- **残留 1 个 CRITICAL 属上游阻塞**：postgres 镜像内 Go 二进制 `stdlib v1.24.6` 的 `CVE-2025-68121`；当日 16-alpine 已是最新重建（2026-11-12），需等上游换 Go 工具链。
- **可复现性为部分证据**：基座 digest 固定 + `package-lock.json` + `npm ci` 构成确定性基础；但未做两次 `--no-cache` 构建的 bit-for-bit 比对。

## 基座升级（2026-11-13）

| 镜像 | 升级前 pin | 升级后 pin | 镜像内实测 |
| --- | --- | --- | --- |
| node | `node@fb4cd12c`（20-alpine，2025-04 前构建，远端 tag 已停更） | `node:22-alpine@1314fb30` | node v22.23.5、npm 10.9.9、alpine 3.24.4、npm 自带 tar 7.5.22 |
| nginx | `nginx@65645c7b`（1.27.5-alpine，2025-04-16 构建） | `nginx:1.30-alpine@583b7f2f` | nginx 1.30.6、alpine 3.24.4、openssl 3.5.9-r0、brotli-libs 1.2.1-r0 |
| postgres | `postgres@cf78e766`（16-alpine，2026-08 前构建） | `postgres:16-alpine@f377d590` | PostgreSQL 16.16、alpine 3.24.4 |

选型依据：

- node 选 22 而非 24：river 自身 CI（`.github/workflows/verify.yml`）已用 `node-version: 22`，镜像与验证运行时对齐；node 20 的远端 digest 与升级前本地一致（2025-04 后未重建），确认 EOL 停更，保留无修复可拿。
- nginx 1.27 主线已退役；`stable-alpine` 与 `1.30-alpine` 同 digest（`583b7f2f…`），即当前 stable。
- postgres 仅做同大版本（16）内的最新重建，不跨大版本，避免数据库升级风险。

## 扫描结果

### 首轮（升级前，运行 43377737668，trivy v0.36.0 action）

| 镜像 | 目标 | HIGH | CRITICAL |
| --- | --- | --- | --- |
| postgres@cf78e766 | 2 个目标（OS + Go `stdlib`） | 31 + 25 | 0 + 1 |
| nginx@65645c7b | 1 个目标（alpine 3.24.1） | 67 | 3 |
| node@fb4cd12c | 2 个目标（OS + npm `package.json`） | 7 + 21 | 0 + 1 |
| 合计 | | 151 | 5 |

CRITICAL 明细：`CVE-2025-68121`（postgres `stdlib`）、`CVE-2026-111497`（nginx `brotli-libs` 越界写）、`CVE-2026-31789`（nginx `libcrypto3`/`libssl3` 堆溢出）、`CVE-2026-59873`（node npm 自带 `tar` 6.2.1）。

### 复扫（升级后，运行 43380314435）

| 镜像 | 目标 | HIGH | CRITICAL |
| --- | --- | --- | --- |
| nginx 1.30.6 | 1 个目标（alpine 3.24.4） | 3 | 0 |
| node 22 | 1 个目标（npm `package.json`） | 10 | 0 |
| postgres 16.16 | 1 个目标（Go `stdlib`） | 25 | 1 |
| 合计 | | 39 | 1 |

- 已消除的 CRITICAL：`CVE-2026-111497`、`CVE-2026-31789`、`CVE-2026-59873`（随 npm 自带的 `tar` 升到 7.5.22，修复线为 7.5.19）。
- 三个镜像的 OS 层已无 HIGH/CRITICAL（首轮 OS 目标的 31H、7H、67H 全部清零），剩余问题都出在镜像内置的上游语言包/第三方库。
- 残留 `CVE-2025-68121`（postgres `stdlib v1.24.6`，修复需 1.24.13 / 1.25.7 / 1.26.0-rc.3）：属镜像内 Go 二进制，需上游重建。
- 残留 nginx 3 HIGH 均为 `libxslt 1.1.43-r3`（修复版 1.1.43-r4 已发布，但要等 nginx 镜像侧重建含新 apk 的层）。
- 残留 node 10 HIGH 全为 npm CLI 自带依赖（`brace-expansion`、`ip-address`、`pacote`、`picomatch`、`sigstore`），只存在于 build 阶段的 npm，运行时（`node dist/index.js`）不加载；彻底消除需 npm 11（node 24 镜像或显式升级 npm），本次未做以保持与 CI 运行时一致。
- 阈值策略仍未启用（`exit-code: "0"` 只记录不拦截）。当前 1 CRITICAL 来自上游镜像，若直接设拦截会常态红线；是否按「排除不可修复项 + 只拦新增」方式设门禁，待后续决定。

## 来源

- pin 来源：registry manifest 查询（`HEAD /v2/library/<repo>/manifests/<tag>` 的 `Docker-Content-Digest`，即镜像索引 digest），并核对 `docker buildx imagetools inspect` 的版本注释（base.name / created / version）。
- 镜像内版本：`docker run --rm --entrypoint sh <ref> -c 'node -v; npm -v; nginx -v; postgres --version; cat /etc/alpine-release; apk list --installed'` 实测。
- 构建/运行：`docker compose -f docker-compose.staging.yml config -q` → `build` → `up -d --wait` → `GET /` + `POST /api/guests` → `down`；`docker history river-api:latest` 确认各层为本次新建。
- 复扫：`gh workflow run image-scan.yml --repo shinelincx/agentcode`，运行 43380314435，日志取 `Total:` 与 `CVE-` 行。
- 探针纠错：PowerShell 会剥掉单引号字符串内的双引号，`curl -d '{"nickname":"…"}'` 实际发出 `{nickname:…}` 得到 400（解析错误）；改用 `Invoke-WebRequest -UseBasicParsing -Body '{"nickname":"…"}'` 后 200。该 400 是探针构造问题，不是服务异常。
- 首轮探针纠错（升级前）：`GET /api/health` 404 属探针错误（`apps/api/src` 无该路由），已改用真实写接口。

## 冲突与未知

- **扫描口径**：结果取决于 trivy DB 快照；HIGH/CRITICAL 数量会随上游修复与 DB 更新变动，需重跑 `image-scan` 刷新。
- **环境耦合**：digest 指向 `docker.m.daocloud.io` 镜像；换环境需同步 registry 与 digest，否则构建会尝试拉取。daocloud 已禁用 tag 列表接口（`/v2/.../tags/list` 返回 `disable-list-tags`），只能逐个 tag 探测。
- **compose 内硬编码** staging 口令 `river_dev_password` 与 `INVITE_CREDENTIAL_KEY`：staging 专用，生产不可复用；不属 C4 范围，记为待处理。
- 未验证 bit-for-bit 可复现（两次 `--no-cache` 构建比对）。
- 未在 CI 运行 staging（C3 首跑仅覆盖 checks/gitleaks，image-scan 为手动触发）。
- 扫描 artifact 未上传（GitHub artifact 下载域名在本机不可达），证据取自 job 日志。
- 未验证 node 22 下 river 全量测试（`npm run verify` 含 Playwright/testcontainers）：本次只验证了 Docker 构建与 staging 运行态；CI 侧 verify.yml 已用 node 22，可作为间接证据。

## 关联

- [[wiki/P2规划]]：C4 行
- [[wiki/P1实施进度]]：staging Docker 验收
- [[wiki/C5-supervisor验证]]：门禁/容器的进程终止边界
