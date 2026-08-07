# ADR 0007: Feishu approvals are audited decisions, not executors

- Status: Accepted
- Date: 2026-08-07

## Context

Phase 1D needs a low-friction human supervision interface while preserving GitHub governance, exact-SHA Staging gates and a separate Production deployment boundary. A chat command that directly merges or deploys would couple an external messaging platform to protected mutations and make retries, expiry and attribution unsafe.

## Decision

Feishu Phase 1D-E uses strict text commands and records only time-bounded, action/SHA-scoped decisions in PostgreSQL. Requests are persisted before notification; decisions use tenant/sender/chat/approver authorization, database time, row locking, unique event/request constraints and append-only database protection. GitHub remains source-code authority. Phase 1D-E never approves or merges a PR and never deploys Production.

Phase 1D-F must separately verify and atomically consume an eligible decision before executing a protected deployment. Interactive cards, webhooks and automatic monitoring are deferred.

## Consequences

- Approval remains attributable, idempotent, auditable and fail closed when Feishu or GitHub evidence is unavailable.
- A delivered message cannot reference an unpersisted approval request.
- Feishu failure cannot mutate GitHub or Production.
- Production rollout needs an additive migration and separately approved real Feishu verification.
- Text commands are less polished than interactive cards but preserve the existing WebSocket event and permission boundary.

## Alternatives considered

- Direct deployment from an approval message: rejected because it collapses Phase 1D-E and 1D-F and makes retries dangerous.
- Interactive cards: deferred because they require additional callback configuration and runtime permissions.
- GitHub webhook or polling service: deferred because minimal server-side read-only `gh` verification is sufficient for this phase.
