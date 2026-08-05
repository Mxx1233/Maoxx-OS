# ADR 0006: AI output requires traceability

- Status: Accepted
- Date: 2026-08-05

## Context

模型输出具有不确定性，模型和 Prompt 也会持续变化。若 AI 无痕覆盖正式数据，错误将无法解释、纠正或重放。

## Decision

保存原始输入、模型调用、提取记录、字段置信度、来源定位和人工修正。AI 输出作为有版本的候选结果，不得直接无痕覆盖正式业务数据。每条由 AI 辅助产生的正式记录必须能够恢复其来源和确认过程。

## Consequences

- 数据模型需要分离 raw input、media、extraction、confirmation 和 business record。
- 重跑识别时新增记录而非覆盖历史。
- 存储量和流程复杂度增加，但审计、纠错和模型评估成为可能。
- 模型调用记录不得保存 API Key。

## Alternatives considered

- 只保留最终 AI 输出：实现简单，但不可解释和不可恢复。
- 用最新识别覆盖旧结果：节省空间，但破坏历史和评估能力。
- 把聊天记录当审计轨迹：结构不稳定，也可能被外部平台删除。
