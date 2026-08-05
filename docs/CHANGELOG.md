# Changelog

本文档采用 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 风格。项目版本尚未正式发布，因此先按阶段记录真实完成内容。

## [Unreleased]

### Added

- 长期 AI 开发上下文文档、开发规则和架构决策记录。
- 飞书 tenant、sender type、sender `open_id` 和 chat type 的默认拒绝授权策略。
- 配置、飞书授权、安全回复和 ORM 元数据的标准库单元测试。
- PostgreSQL 自定义格式备份、SHA-256 校验和临时恢复演练。

### Changed

- SQLAlchemy 元数据已与 `0001_core_foundation` 的复合索引和消息去重约束对齐。
- 飞书成功确认固定为“已记录。”，不再拼接用户原始内容。

### Known issues

- 飞书真实白名单尚未写入服务器环境，新增授权代码尚未部署和实测。
- Worker 可观测健康状态尚未建立。
- 完整数据库 integration/migration 测试尚未建立。

## [Phase 1 baseline] - 2026-08-05

### Added

- Docker Compose 下的 PostgreSQL 16、FastAPI 和飞书 Worker。
- SQLAlchemy 与 Alembic 基础设施。
- `core.users`、`core.entities`、`core.raw_inputs`。
- API `/health`、`/health/db`、文字输入和最近输入查询。
- 飞书 `im.message.receive_v1` 文字接收、原始输入落库、外部消息 ID 去重和固定回复。
- 有效 Git 仓库、`main` 分支、阶段 1 基线提交和 `phase1-baseline` annotated tag。
