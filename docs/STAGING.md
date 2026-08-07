# Phase 1D-D Staging

Phase 1D-D 当前为 `implemented_pending_verification`，Phase 1D 保持 `in_progress`，Phase 2A 保持 `not_started`。仓库只实现隔离 Staging 的 Compose、部署/验证/停止脚本、静态测试和运维边界。Staging 尚未部署，migration 尚未执行，Feishu Worker 尚未测试，Production 未部署新版本。

## 隔离边界

- 固定工作目录 `/opt/maoxx-os-staging`，Compose project 必须显式为 `maoxx-staging`。
- 默认仅启动独立 PostgreSQL 与 API；Worker 只能通过 `feishu-test` profile 人工启用，且不得使用 Production 飞书凭据。
- 数据库仅连接 internal network，不发布宿主端口；API 仅发布 `127.0.0.1:18000`。
- PostgreSQL volume 由 Compose 独立命名；storage 固定为 `/opt/maoxx-os-staging/storage`，不得指向 Production。
- `.env.staging` 必须是未跟踪的 0600 普通文件，不得复制、链接或读取 Production `.env`。

## 首次部署批准链

首次部署只能在本 PR 合并、服务器 post-merge 同步与验收完成后，由用户单独人工批准。批准前不得创建 `/opt/maoxx-os-staging`、构建镜像、创建 Docker 资源或执行 migration。

批准后，运维人员在固定目录安装已合并的仓库副本与独立 `.env.staging`，清理脚本拒绝的父进程环境变量，再以完整 40 位、位于最新 `origin/main` 历史且 required `CI / Quality Gate` 成功的 SHA 调用：

```bash
scripts/staging/preflight.sh APPROVED_40_CHARACTER_SHA
scripts/staging/deploy.sh APPROVED_40_CHARACTER_SHA
scripts/staging/verify.sh
scripts/staging/stop.sh
```

不要 `source` 或 `eval` `.env.staging`。脚本串行构建 immutable SHA 镜像，启动独立数据库、执行向前 migration、启动 API 并比较 Production 不变量。验证失败只执行普通 Compose `down`，保留数据库 volume；禁止 downgrade、prune 或删除 volume。

本阶段不启用 Registry，不修改 Production Compose，不部署 Production，不进入 Phase 1D-E、1D-F 或 Phase 2A。只有首次真实隔离部署、migration、健康与 Production 不变量验收完成后，Phase 1D-D 才可由用户标记为 `accepted`。
