# Maoxx OS 当前状态

更新时间：2026-08-08

## 当前阶段

阶段 1 和阶段 1C 已验收。阶段 1D：Cloud Development Control Plane 为 `in_progress`；Phase 1D-0 已完成，Phase 1D-A 至 Phase 1D-E 为 `accepted`，Phase 1D-F 为 `implemented_pending_verification`。Phase 2A 仍为 `not_started`。

GitHub 仓库已设为 Public。Active `Protect main` Ruleset 已禁止删除、force push 和直接 push，要求通过 PR 并解决 conversations；`CI / Quality Gate` 已配置为唯一 required status check。

Phase 1D-A 当前为 `accepted`。PR #2、#3、#4 已通过独立分支和 Pull Request 合并；PR/Issue 模板、CODEOWNERS、治理文档和 migration 人工审查规则均已进入 main。Phase 1D-B 也为 `accepted`，`CI / Quality Gate` 是唯一 configured required status check；独立 API 测试、secret scanning 和破坏性 migration scanning 尚未配置。

## 当前 Alembic revision

运行中 Production 为 `0002_phase_1d_e_approvals`；accepted Staging 保持其已验收 revision 且未被本实现修改。本 Phase 1D-F 分支新增尚未部署的兼容扩展 head `0003_phase_1d_f_deployment`；空库 upgrade、current/head/check、事务、并发、锁、rollback 和 append-only 已在 disposable PostgreSQL 验证。Production 尚未执行 0003。

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
- Phase 1D-D 真实 Staging 运行验收通过：immutable SHA build、retained DB volume 复用、Alembic 幂等、DB/API health、HTTP、network/volume/port/mount/image 隔离与 Production 前后不变量均通过；Staging db/api 保持运行且 Worker 关闭。
- Phase 1D-E 已 accepted：Production 0002、interactive approval card Worker、批准/拒绝/两次等一下和 fail-closed runtime probes 已通过；unauthorized-human 因无第二真实身份保留为外部限制，自动化路径通过。
- Phase 1D-F 已实现不可变 deployment intent/artifact、同 digest Staging acceptance、精确 approval binding/一次消费、separation of duty、backup/migration/resource gates、Production lease、受限 executor、health/reconciliation、独立 rollback、Feishu reference 与 append-only audit；等待 PR/CI/Agent C review 和真实演练。

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

- 当前 Production Alembic revision：`0002_phase_1d_e_approvals`；分支 code head 为尚未部署的 `0003_phase_1d_f_deployment`。
- Compose 服务：`db`、`api`、`feishu-worker`。
- API 绑定：`127.0.0.1:8000`。
- PostgreSQL 不暴露宿主机端口。

2026-08-05 Phase 1D-0 运行验收确认 `db`、`api` 和 `feishu-worker` healthy；Worker 返回 `connected=true`、`status=ready`。Compose 配置有效，Alembic current/head 均为 `0001_core_foundation` 且无漂移。该结果是服务器执行基础的时间点快照，不代表完整 Phase 1D 已验收。

## 已知技术债务

- Worker 和 API 入口逻辑尚未抽取到统一 Service Layer。
- 当前测试使用标准库 `unittest`；隔离 PostgreSQL migration 测试已纳入 CI，但独立 API 测试尚未配置。
- 依赖和基础镜像使用版本范围或移动标签，构建尚未完全锁定。
- Ruff formatter 仍精确排除 4 个沿用早期格式的既有文件；它们继续接受 Python compile 和基础 lint，批量格式化留待独立评审。
- 当前 API 尚无正式认证机制。
- Worker 连接观测适配飞书 SDK 的内部连接生命周期方法；SDK 升级时必须运行连接、断线和重连回归测试。
- 回复重试仅在进程内执行，没有持久化 outbox，跨重启最终送达不受保证。

## 当前风险与 Phase 1D 缺口

- Worker 已能区分连接 ready 与进程 running；外部网络长时间中断演练尚未自动化。
- 飞书回复已有界补偿重试，但尚无可靠 outbox，跨重启投递仍是已知限制。
- API 尚无正式认证；当前依赖 localhost 网络边界。
- 依赖和基础镜像尚未完全锁定。
- GitHub Ruleset 和治理文件已通过 Phase 1D-A 验收；Phase 1D-B Actions CI 五个 job 已通过，required `CI / Quality Gate` 的失败阻断和恢复已验证。
- Phase 1D-C 仓库工作流为 `accepted`：Codex Cloud GitHub 连接仅限 `Mxx1233/Maoxx-OS`，非生产 Cloud 环境禁用 agent internet access，Secrets 为 none 且不含生产凭据；只读验证任务成功且为 zero file diff。Cloud 分支 `codex/implement-phase-1d-c-codex-cloud-validation` 已发布，Pull Request #8 已成功创建和更新。
- PR #8 最新 `pull_request` synchronize event 自动触发五个 CI job，全部通过，包括 required `CI / Quality Gate`。没有绕过 CI 要求、直接写 `main`、自动批准或合并，也没有访问 Production。
- Phase 1D-D 和 Phase 1D-E 已 accepted。Phase 1D-F 实现已完成但尚未合并、迁移或执行真实同-digest Staging→Production/rollback 演练。

## 下一步

Phase 1D-E 已验收。Phase 1D-F 为 `implemented_pending_verification`，等待 PR 人工审查、合并、精确 merge-SHA CI、0003 migration/backup 评审和单独批准的真实同-digest Staging→Production/rollback 演练。不得自动批准、合并或部署，不得进入 Phase 2A。详细边界见 [Phase 1D-F Controlled Deployment](PHASE_1D_F_CONTROLLED_DEPLOYMENT.md)。

## 最近一次验收

阶段 1 基线已于 2026-08-05 建立：提交 `adfe64e`，annotated tag 为 `phase1-baseline`。阶段 1C 已完成验收并标记 `phase1c-accepted`。此前误建的本地 `phase1d-accepted` tag 已删除；对应服务器功能提交保留，不回滚已验证能力。

运行状态可能随部署变化。代理开始工作时必须以实际只读检查为准，并报告与本文档的差异。

## 仓库内容规则

禁止提交密钥、Token、数据库备份、用户媒体、运行日志或缓存。
