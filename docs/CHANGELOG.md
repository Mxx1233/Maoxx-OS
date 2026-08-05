# Changelog

本文档采用 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 风格。项目版本尚未正式发布，因此先按阶段记录真实完成内容。

## [Unreleased]

当前没有已完成但尚未归入阶段验收的功能。

## [Phase 1D] - 2026-08-05

### Added

- Worker 容器内连接 readiness、事件计数和 Compose healthcheck。
- 飞书回复限流与传输错误的有界指数退避、抖动和失败分类。
- Worker 健康、回复失败路径、日志隐私和隔离 PostgreSQL migration/integration 测试。

### Changed

- Worker 关键日志采用机器可读事件字段，并以不可逆指纹替代完整消息和记录标识。
- 运维手册补充 Worker readiness、回复策略和隔离数据库测试流程。

### Fixed

- 运行中 API 镜像已与 `phase1c-accepted` ORM 元数据重新对齐，`alembic check` 恢复无漂移。

### Known issues

- 回复重试不持久化，Worker 重启后不保证尚未完成的回复最终送达。
- Worker 连接观测依赖当前飞书 SDK 的内部连接生命周期方法，升级 SDK 时必须回归验证。
- API 尚无正式认证，Worker 运维状态因此只在容器内开放。

## [Phase 1C] - 2026-08-05

### Added

- 长期 AI 开发上下文文档、开发规则和架构决策记录。
- 飞书 tenant、sender type、sender `open_id` 和 chat type 的默认拒绝授权策略。
- 配置、飞书授权、安全回复和 ORM 元数据的标准库单元测试。
- PostgreSQL 自定义格式备份、SHA-256 校验和临时恢复演练。

### Changed

- SQLAlchemy 元数据已与 `0001_core_foundation` 的复合索引和消息去重约束对齐。
- 飞书成功确认固定为“已记录。”，不再拼接用户原始内容。

### Known issues

- Worker 可观测健康状态尚未建立。
- 完整数据库 integration/migration 测试尚未建立。
- 飞书回复尚无可靠 outbox 或补偿重试。
- API 尚无正式认证，当前依赖 localhost 网络边界。

## [Phase 1 baseline] - 2026-08-05

### Added

- Docker Compose 下的 PostgreSQL 16、FastAPI 和飞书 Worker。
- SQLAlchemy 与 Alembic 基础设施。
- `core.users`、`core.entities`、`core.raw_inputs`。
- API `/health`、`/health/db`、文字输入和最近输入查询。
- 飞书 `im.message.receive_v1` 文字接收、原始输入落库、外部消息 ID 去重和固定回复。
- 有效 Git 仓库、`main` 分支、阶段 1 基线提交和 `phase1-baseline` annotated tag。
