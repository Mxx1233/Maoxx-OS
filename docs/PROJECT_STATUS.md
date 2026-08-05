# Maoxx OS 当前状态

更新时间：2026-08-05

## 当前阶段

阶段 1C：安全、版本和迁移基线（已验收）。阶段 1D 仅为 `planned`，尚未开始实施。

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

2026-08-05 最近一次只读检查确认 `db` 与 `api` healthy、`feishu-worker` running、Compose 配置有效。该结果是时间点快照，不代替每次工作前检查。

## 已知技术债务

- Worker 和 API 入口逻辑尚未抽取到统一 Service Layer。
- 当前测试使用标准库 `unittest`，尚无独立临时 PostgreSQL 的完整 migration 测试套件。
- Worker 缺少 healthcheck、结构化监控和可靠回复重试机制。
- 依赖和基础镜像使用版本范围或移动标签，构建尚未完全锁定。
- 当前 API 尚无正式认证机制。

## 当前风险与 Phase 1D 范围

- Worker 缺少独立健康检查，容器 running 不代表 WebSocket 正常消费。
- 飞书回复失败尚无可靠 outbox 或补偿重试。
- API 尚无正式认证；当前依赖 localhost 网络边界。
- 依赖和基础镜像尚未完全锁定。

## 下一步

等待用户批准是否进入阶段 1D。阶段 1D 只做可观测性与质量加固，不得创建阶段 2 表，也不得自动进入阶段 2A。

阶段 1D 验收后，才可规划进入阶段 2A：媒体与原始输入。

## 最近一次验收

阶段 1 基线已于 2026-08-05 建立：提交 `adfe64e`，annotated tag 为 `phase1-baseline`。阶段 1C 于 2026-08-05 完成最终运行验收；验收提交和 tag 见当前 Git 历史。

运行状态可能随部署变化。代理开始工作时必须以实际只读检查为准，并报告与本文档的差异。

## 仓库内容规则

禁止提交密钥、Token、数据库备份、用户媒体、运行日志或缓存。
