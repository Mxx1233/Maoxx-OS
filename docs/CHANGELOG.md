# Changelog

本文档采用 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 风格。项目版本尚未正式发布，因此先按阶段记录真实完成内容。

## [Unreleased]

### Added

- 长期 AI 开发上下文文档、开发规则和架构决策记录。

### Known issues

- ORM 元数据与已执行的 `0001_core_foundation` 存在索引和唯一约束声明漂移。
- 飞书 tenant、sender 和 chat type 授权尚未完成。
- 敏感确认回复尚未改为不回显原文。
- 自动化测试和 Worker 可观测健康状态尚未建立。

## [Phase 1 baseline] - 2026-08-05

### Added

- Docker Compose 下的 PostgreSQL 16、FastAPI 和飞书 Worker。
- SQLAlchemy 与 Alembic 基础设施。
- `core.users`、`core.entities`、`core.raw_inputs`。
- API `/health`、`/health/db`、文字输入和最近输入查询。
- 飞书 `im.message.receive_v1` 文字接收、原始输入落库、外部消息 ID 去重和固定回复。
- 有效 Git 仓库、`main` 分支、阶段 1 基线提交和 `phase1-baseline` annotated tag。
