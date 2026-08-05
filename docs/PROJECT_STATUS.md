# Maoxx OS 当前状态

更新时间：2026-08-05

## 当前阶段

阶段 1 和阶段 1C 已验收。阶段 1D：Cloud Development Control Plane 为 `in_progress`；其中 Phase 1D-0 已完成，Phase 1D-A 至 1D-F 尚未完成验收。阶段 2A 仍为 `not_started`。

GitHub 仓库已设为 Public。Active `Protect main` Ruleset 已禁止删除、force push 和直接 push，要求通过 PR 并解决 conversations；required status checks 等待 Phase 1D-B，Phase 1D-A 治理文件仍待独立 PR 实施和验证。

## 当前 Alembic revision

`0001_core_foundation`，同时也是当前唯一 head。ORM 已准确声明现有复合索引和唯一约束；2026-08-05 使用当前工作树实测 `alembic check` 无漂移。

## 已完成

- Ubuntu 24.04 服务器环境。
- Docker 与 Docker Compose。
- PostgreSQL 16。
- FastAPI、SQLAlchemy 和 Alembic。
- `core.users`、`core.entities`、`core.raw_inputs`。
- 默认单用户 Maoxx，并保留未来多用户扩展结构。
- 飞书 WebSocket 长连接。
- 飞书文字消息入库。
- 基于外部 `message_id` 的数据库级去重。
- 固定确认回复。
- 有效 Git 仓库、`main` 分支、阶段 1 基线提交和 `phase1-baseline` annotated tag。
- PostgreSQL 自定义格式备份、SHA-256 校验和临时数据库恢复演练。
- ORM 与 `0001_core_foundation` 的索引和唯一约束元数据对齐。
- 飞书 tenant、sender `open_id`、sender type 和 chat type 的默认拒绝授权代码。
- 成功确认回复已改为固定“已记录。”，不再拼接原文。
- 13 项标准库单元测试，覆盖配置解析、授权、隐私回复和 ORM 元数据。
- 真实飞书 p2p 白名单已配置并在重建后的 Worker 上完成入库及“已记录。”回复验收。
- 飞书 SDK 日志级别已调整为 `ERROR`，避免记录 WebSocket 连接凭据。
- 一次性身份诊断代码和 `/tmp/feishu_identity.txt` 已删除。
- 阶段 1D 已恢复运行中 API 镜像与 `phase1c-accepted` 源码的一致性，`alembic check` 无漂移。
- Worker 已增加基于实际 SDK 连接生命周期的容器内 readiness、事件计数和 Compose healthcheck。
- 飞书回复已增加限流/传输错误分类、有上限指数退避与抖动；永久错误不盲目重试。
- 消息与数据库记录日志已改用不可逆指纹，异常日志不输出异常正文。
- 自动化基线扩展为 21 项默认执行测试及 1 项显式启用的隔离 PostgreSQL migration/integration 测试。
- Phase 1D 部署前备份非空且 SHA-256 校验通过，并完成隔离数据库恢复验证；备份 revision 为 `0001_core_foundation`，关键行数为 users=1、entities=0、raw_inputs=9。

## Phase 1D-0 完成结果

- Compose 配置有效，`db`、`api` 和 `feishu-worker` 均为 healthy。
- Worker readiness 为 `connected=true`、`status=ready`；真实飞书测试后事件计数为 received=1、succeeded=1、failed=0，最近事件和成功时间均已更新。
- 授权用户的真实 p2p 文字消息只新增 1 条 `core.raw_inputs`，总数由备份快照的 9 增至 10，并收到固定“已记录。”回复。
- 最近 Worker 日志与最新消息正文、tenant、open_id 和 App Secret 完成精确不回显扫描，并通过 Token/凭据模式扫描；未发现敏感原值。
- API `/health`、`/health/db` 和 PostgreSQL readiness 正常。
- Alembic current/head 均为 `0001_core_foundation`，`alembic check` 无漂移。
- 21 项默认自动化测试通过；显式启用的隔离 PostgreSQL migration/integration 测试通过。
- 部署前备份、SHA-256 和隔离恢复演练通过；未修改 schema、创建 migration 或执行 downgrade。

上述结果只证明 Server Observability and Execution Foundation 已完成，不证明 GitHub 治理、CI、Codex Cloud、Staging、飞书监督审批或受控部署闭环已完成，因此不能作为完整 Phase 1D 验收依据。

## 阶段 1C 验收结果

- Compose 配置有效，`db` 与 `api` healthy，`feishu-worker` running。
- API `/health` 与 `/health/db` 正常。
- Alembic current/head 均为 `0001_core_foundation`，`alembic check` 无漂移。
- 13 项自动化测试通过。
- 数据行数为 users=1、entities=0、raw_inputs=8；最终飞书测试只增加预期的 1 条输入。
- PostgreSQL 自定义格式备份非空，SHA-256 通过，临时数据库恢复后的 revision 和关键行数一致。
- 重建后的授权 p2p 消息可入库并回复“已记录。”。
- 日志扫描未发现最新消息正文、tenant/open_id 原值、App Secret 或连接 Token。

## 后续未完成

- 模型网关尚未接入，当前回复为固定逻辑。
- 阶段 2 公共数据底座尚未建立。
- 项目、生活、运动、健康、学习、求职、论文、分析和知识库模块尚未建立。

## 当前运行基线

- 当前 Alembic revision：`0001_core_foundation`。
- Compose 服务：`db`、`api`、`feishu-worker`。
- API 绑定：`127.0.0.1:8000`。
- PostgreSQL 不暴露宿主机端口。

2026-08-05 Phase 1D-0 运行验收确认 `db`、`api` 和 `feishu-worker` healthy；Worker 返回 `connected=true`、`status=ready`。Compose 配置有效，Alembic current/head 均为 `0001_core_foundation` 且无漂移。该结果是服务器执行基础的时间点快照，不代表完整 Phase 1D 已验收。

## 已知技术债务

- Worker 和 API 入口逻辑尚未抽取到统一 Service Layer。
- 当前测试使用标准库 `unittest`；隔离 PostgreSQL migration 测试需要显式启用，尚未纳入持续集成平台。
- 依赖和基础镜像使用版本范围或移动标签，构建尚未完全锁定。
- 当前 API 尚无正式认证机制。
- Worker 连接观测适配飞书 SDK 的内部连接生命周期方法；SDK 升级时必须运行连接、断线和重连回归测试。
- 回复重试仅在进程内执行，没有持久化 outbox，跨重启最终送达不受保证。

## 当前风险与 Phase 1D 缺口

- Worker 已能区分连接 ready 与进程 running；外部网络长时间中断演练尚未自动化。
- 飞书回复已有界补偿重试，但尚无可靠 outbox，跨重启投递仍是已知限制。
- API 尚无正式认证；当前依赖 localhost 网络边界。
- 依赖和基础镜像尚未完全锁定。
- GitHub 分支治理、PR 强制、CODEOWNERS、模板和 Actions CI 尚未在仓库中建立；网页设置当前无法验证。
- Codex Cloud 连接、权限、非生产环境、只读任务、测试分支和测试 PR 无可验证证据。
- Staging 隔离环境、飞书监督审批闭环和受控 Production 部署尚未建立。

## 下一步

按 `docs/PHASE_1D_CONTROL_PLANE.md` 从 Phase 1D-A 开始逐子阶段设计、批准、实施和验收。完整 Phase 1D accepted 前不得规划或进入 Phase 2A。

## 最近一次验收

阶段 1 基线已于 2026-08-05 建立：提交 `adfe64e`，annotated tag 为 `phase1-baseline`。阶段 1C 已完成验收并标记 `phase1c-accepted`。此前误建的本地 `phase1d-accepted` tag 已删除；对应服务器功能提交保留，不回滚已验证能力。

运行状态可能随部署变化。代理开始工作时必须以实际只读检查为准，并报告与本文档的差异。

## 仓库内容规则

禁止提交密钥、Token、数据库备份、用户媒体、运行日志或缓存。
