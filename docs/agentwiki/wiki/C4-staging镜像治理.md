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
- **扫描未完成（blocked）**：Windows 与 WSL 内均无 `trivy` / `grype` / `docker-scout`，Docker Hub 域名不可达也无法安装，因此「扫描无高危」**未验证**。
- **可复现性为部分证据**：基座 digest 固定 + `package-lock.json` + `npm ci` 构成确定性基础；但未做两次 `--no-cache` 构建的 bit-for-bit 比对。

## 来源

- 镜像 digest（本地 RepoDigests）：
  - node：`sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293`
  - nginx：`sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10`
  - postgres：`sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685`
- 命令：`docker image inspect <ref>` ×3（均返回 image ID）；`docker compose -f docker-compose.staging.yml config -q`；`build`；`up -d --wait`；`curl POST /api/guests`；`down`。
- 探针纠错：首次用 `GET /api/health` 得到 404，核查 `apps/api/src` 无该路由，404 属探针错误而非部署失败；改用真实写接口后 200。

## 冲突与未知

- **扫描缺口**：仍需在具备网络的 CI 中引入 trivy/grype（或离线漏洞库），否则 C4 的「扫描无高危」无法闭环。
- **环境耦合**：digest 指向 `docker.m.daocloud.io` 镜像；换环境需同步该 registry 与 digest，否则构建会尝试拉取。
- **compose 内硬编码** staging 口令 `river_dev_password` 与 `INVITE_CREDENTIAL_KEY`：staging 专用，生产不可复用；不属 C4 范围，记为待处理。
- 未验证 bit-for-bit 可复现（两次 `--no-cache` 构建比对）。
- 未在 CI 运行 staging（C3 首跑仍待推送授权）。

## 关联

- [[wiki/P2规划]]：C4 行
- [[wiki/P1实施进度]]：staging Docker 验收
- [[wiki/C5-supervisor验证]]：门禁/容器的进程终止边界
