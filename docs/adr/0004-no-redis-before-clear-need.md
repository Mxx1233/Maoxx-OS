# ADR 0004: No Redis before clear need

- Status: Accepted
- Date: 2026-08-05

## Context

当前服务器内存约 2 GB，系统规模和并发有限。提前部署 Redis 会增加常驻资源、备份边界、故障模式和运维成本。

## Decision

在出现明确且经过测量的性能、并发或独立缓存需求前，不引入 Redis。早期任务队列、幂等状态和租约优先使用 PostgreSQL 的事务、索引及 `FOR UPDATE SKIP LOCKED` 等能力。

## Consequences

- 基础设施更少，事实和队列状态可统一备份。
- PostgreSQL 队列必须设计合理索引、领取租约和失败恢复。
- 需要监测数据库负载；达到明确阈值时重新评估。

## Alternatives considered

- 立即使用 Redis/Celery：能力成熟，但当前资源和复杂度成本高于收益。
- 仅使用进程内队列：重启丢失状态，不适合可靠任务。
