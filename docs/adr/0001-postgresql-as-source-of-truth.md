# ADR 0001: PostgreSQL as source of truth

- Status: Accepted
- Date: 2026-08-05

## Context

Maoxx OS 会同时接收聊天、文件和 AI 输出。若事实分散在聊天历史、模型上下文或文件名中，数据将无法可靠查询、迁移、恢复和审计。

## Decision

PostgreSQL 保存正式事实数据。聊天记录、原始文件和 AI 输出只是输入、证据或候选结果，不是正式事实源。文件二进制存储在对象存储层，PostgreSQL 保存其可迁移引用和业务关系。

## Consequences

- 正式状态变更必须落入受约束的数据库结构。
- 备份、恢复和 migration 成为关键运维能力。
- AI 或飞书不可用时，已有事实数据仍然完整。
- 写入链路需要验证、幂等和审计设计。

## Alternatives considered

- 以飞书聊天历史为事实源：难以结构化查询、版本化和迁移。
- 以文件目录为事实源：缺少关系约束和事务能力。
- 以 AI 上下文为事实源：不可确定、不可审计且绑定供应商。
