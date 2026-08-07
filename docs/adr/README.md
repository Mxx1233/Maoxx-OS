# Architecture Decision Records

ADR 用于保存对 Maoxx OS 长期架构有影响、未来开发者需要理解的决策。ADR 记录决策发生时的背景和权衡，不替代实施文档或项目状态。

## 状态

- `Proposed`：提议中，尚未生效。
- `Accepted`：已接受，后续实现应遵守。
- `Superseded`：已被另一 ADR 替代，必须链接替代记录。
- `Deprecated`：不再建议使用，但未被单一决策替代。

ADR 一经接受不应重写历史。若决策改变，创建新 ADR 并将旧 ADR 标记为 `Superseded`。

## 文件命名

使用四位序号和简短标题：`NNNN-short-title.md`。

## 模板

```markdown
# ADR NNNN: Title

- Status: Proposed
- Date: YYYY-MM-DD

## Context

## Decision

## Consequences

## Alternatives considered
```

## 当前 ADR

- [0001: PostgreSQL as source of truth](0001-postgresql-as-source-of-truth.md)
- [0002: Single-user first, multi-user ready](0002-single-user-first-multi-user-ready.md)
- [0003: Local storage before Tencent COS](0003-local-storage-before-tencent-cos.md)
- [0004: No Redis before clear need](0004-no-redis-before-clear-need.md)
- [0005: Feishu as primary user interface](0005-feishu-as-primary-user-interface.md)
- [0006: AI output requires traceability](0006-ai-output-requires-traceability.md)
- [0007: Feishu approvals are audited decisions, not executors](0007-feishu-approvals-are-audited-decisions.md)
