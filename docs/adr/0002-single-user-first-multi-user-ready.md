# ADR 0002: Single-user first, multi-user ready

- Status: Accepted
- Date: 2026-08-05

## Context

当前系统只服务用户 Maoxx。完全按多租户产品建设会增加不必要复杂度，但省略用户边界会让未来扩展代价高昂，并可能造成数据混淆。

## Decision

第一版采用单用户产品体验，但所有核心用户数据保留 `user_id`。数据库约束、关联表和 API 身份上下文不得阻碍未来多用户隔离；客户端不得任意指定他人 `user_id`。

## Consequences

- 当前交互保持简单，同时数据模型保留演进路径。
- 新表和关联必须评审用户边界及跨用户关联风险。
- 必要时使用包含 `user_id` 的复合唯一键和外键，而不能只依赖 Python 查询过滤。

## Alternatives considered

- 完全单用户且无 `user_id`：短期简单，未来迁移风险过高。
- 立即建设完整多租户 UI 和计费体系：当前没有明确需求，复杂度不合理。
