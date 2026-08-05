# ADR 0005: Feishu as primary user interface

- Status: Accepted
- Date: 2026-08-05

## Context

Maoxx OS 需要低摩擦、随时可用的日常入口。飞书已经提供聊天、机器人、图片、文件、通知和交互卡片能力。

## Decision

飞书作为第一版主要用户界面。聊天、图片、文件和交互卡片是核心交互方式；FastAPI 提供后台接口，服务器终端负责系统维护。复杂 Web 管理后台在出现明确需求后再评估。

## Consequences

- 业务流程必须设计飞书 UX、幂等事件和授权校验。
- 飞书平台故障不应损坏 PostgreSQL 中的事实数据。
- 复杂数据浏览能力早期可能有限。
- 后台 API 仍需保持界面无关，避免业务逻辑锁定飞书。

## Alternatives considered

- 优先开发完整 Web 前端：投入高且不符合当前日常交互习惯。
- 仅提供命令行：不适合移动和即时记录。
- 只提供通用聊天：无法承载可靠确认和结构化操作。
