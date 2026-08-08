# Phase 1D-D Staging

Phase 1D-D 和 Phase 1D-E 当前均为 `accepted`，Phase 1D-F 为 `implemented_pending_verification`，Phase 1D 保持 `in_progress`，Phase 2A 保持 `not_started`。精确批准 SHA 的 immutable build、独立 DB/storage/network、retained volume 重试、Alembic 幂等、DB/API 健康、HTTP、port/mount/image 隔离及 Production 前后不变量均已通过真实运行验收。Staging `db` 和 `api` 保持运行，Staging Worker 关闭；Phase 1D-F 实现工作未修改该 accepted runtime。

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

批准 SHA 是部署内容的唯一身份。部署前必须 freshly fetch `origin/main`，当前 `HEAD` 必须精确等于完整 40 位批准 SHA，且该 SHA 位于最新 `origin/main` 历史；整个工作树必须没有 tracked 修改或 untracked 文件。脚本不会 checkout、reset、clean 或删除本地文件来修复不一致，而是 fail closed。部署脚本、共享库、preflight/verify/stop、Compose、Dockerfile、应用、Alembic 与 requirements 都必须存在于同一批准 tree，因此镜像 tag `maoxx-os-staging-api:<SHA>` 对应实际执行和构建的内容。

镜像内容不直接读取 mutable checkout。部署从批准 Git object 归档且只提取 `Dockerfile`、`app/`、`alembic/`、`alembic.ini` 和 `requirements.txt` 到唯一 `mktemp -d` context；验证必需文件、拒绝 symlink，并在 build 前将 context 设为只读。Dockerfile 与 context 参数都指向该 archive。`.git`、`.env.staging`、`storage/` 以及所有 ignored/untracked worktree 文件不会进入 context；archive 创建后的 worktree 变化也不能改变镜像输入。context 在成功或失败 EXIT 时安全清理。

第一次 mutating Compose 调用前已注册失败清理。即使 `compose up` 部分创建 container、network 或 volume 后返回非零，EXIT 清理也只用精确 `com.docker.compose.project=maoxx-staging` labels 判断对象并执行普通 Compose `down`；不会使用 `-v`、`--volumes`、`--remove-orphans`、volume 删除、prune 或 downgrade，PostgreSQL volume 保留，Production project 不在清理范围内。

preflight 只接受两种 fail-closed 资源状态：完全没有 `maoxx-staging` container、network 或 volume 的首次部署状态；或没有 container/network，且唯一 volume 同时具有 Compose project `maoxx-staging`、volume identity `postgres_data` 并精确命名为 `maoxx-staging_postgres_data` 的失败重试状态。额外对象、未知/Production/unlabeled volume、错误 label 或 Docker 查询失败均拒绝。合法 retained volume 由 Compose 自然复用，不删除、重建或手工读取其内容；再次执行 `alembic upgrade head` 用于验证 forward migration 幂等性。

本阶段不启用 Registry，不修改 Production Compose，不部署 Production。Phase 1D-F 后续同 digest 演练必须另行批准，且不得把本阶段验收授权解释为可自动修改 accepted Staging 或进入 Phase 2A。
