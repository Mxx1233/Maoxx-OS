# Phase 1D-F Controlled Deployment

Phase 1D-F 当前为 `implemented_pending_verification`。Phase 1D-E 已完成真实
interactive-card runtime acceptance 并为 `accepted`。本实现只建立受控部署的持久
状态、门禁和专用执行边界；本 PR 不执行 Production migration、Staging 部署或
Production 部署，也不开始 Phase 2A。

## 不可变工件原则

一次 deployment intent 绑定 repository、精确 protected-main SHA、Production
environment、action code、服务集合、migration risk、config fingerprint 和
`sha256:<64 hex>` artifact digest。CI evidence 必须是相同 SHA 的 `push`、
`completed/success` 和 `CI / Quality Gate=success`，并记录 protected-main 已核验。

构建产物只接受 `registry/repository@sha256:<digest>` 形式；`:latest`、release tag
或 tag+digest 不一致均 fail closed。同一 intent 只有一个 immutable artifact record。
Staging acceptance 和 Production validated execution 都引用这一条 digest，禁止
Staging 验证一个构建、Production 再构建另一个。

## 持久模型

`0003_phase_1d_f_deployment` 是在 0002 之上的兼容扩展：

- `core.deployment_intents`：不可变部署或 rollback 意图与 deployment UUID；
- `core.deployment_artifacts`：digest、immutable reference、CI 与 provenance；
- `core.deployment_state_events`：有序 append-only 状态历史；
- `core.staging_acceptances`：SHA/digest/config/suite/version/validity 验收；
- `core.staging_acceptance_invalidations`：显式失效事件；
- `core.deployment_approval_bindings`：approval request 与 deployment/digest 的精确绑定；
- `core.deployment_approval_consumptions`：一次性 approval consumption 与状态事件；
- `core.deployment_locks`：Production conflict domain 的当前 bounded lease；
- `core.deployment_execution_attempts`：每个 deployment 唯一的持久 execution
  identity；lost-ack retry 只作 reconciliation，不能重发 mutation；
- `core.deployment_evidence`：备份、resource、lock、executor、health、reconciliation
  和 Feishu reference；
- `core.deployment_rollbacks`：failed deployment、current revision 和 rollback target。

除当前 lease lock 外，上述运维审计表由通用 trigger 禁止 UPDATE/DELETE。0003 只扩展
Phase 1D-E approval action allowlist，增加独立 `rollback_production`；既有决定、时限、
幂等、row lock 和 append-only 语义不变。

## 状态机

正常部署只允许：

```text
INTENT_CREATED
→ ARTIFACT_RECORDED
→ STAGING_DEPLOYING
→ STAGING_VALIDATED
→ PRODUCTION_APPROVAL_PENDING
→ PRODUCTION_DEPLOYING
→ PRODUCTION_HEALTHY | PRODUCTION_FAILED
```

rollback 使用独立 intent 和 approval：

```text
INTENT_CREATED
→ ARTIFACT_RECORDED
→ STAGING_DEPLOYING
→ STAGING_VALIDATED
→ ROLLBACK_APPROVAL_PENDING
→ ROLLBACK_DEPLOYING
→ ROLLED_BACK | ROLLBACK_FAILED
```

非法跳跃、终态 replay 或同 sequence 并发写入均拒绝。当前状态只从最后一个 state
event 推导，不依赖可静默覆盖的 mutable state column。

## Staging acceptance

验收必须绑定 deployment UUID、repository、SHA、同一 artifact digest、`staging`
environment、validation suite/version、completion time、valid-until 与 config
fingerprint。下列情况不能进入 Production：

- 无验收、验收过期或显式 invalidation；
- SHA、digest、repository 或 config fingerprint 不一致；
- 工件重建产生不同 digest；
- validation suite evidence 不属于该 deployment。

本 PR 只在 disposable PostgreSQL 和 fake orchestration boundary 验证这些语义，不
修改 accepted Staging。

## 独立审批和一次性消费

Production approval request 由已 frozen 的 immutable intent 在同一事务创建，并通过
immutable binding 精确关联 deployment UUID、
repository、SHA、environment、action code 和 artifact digest。开始部署的数据库事务
会锁定 intent、approval request/decision 和 Production lease，并重新读取 PostgreSQL
`clock_timestamp()`。事务同时：

1. 验证 approved、未过期、未消费和精确 binding；
2. 通过 canonical trusted human identity 验证 approver 与 requester 不同；caller
   fingerprint 不能作为身份证据，缺失/歧义 identity fail closed；
3. 插入唯一 consumption；
4. 插入 `PRODUCTION_DEPLOYING` 或 `ROLLBACK_DEPLOYING` state event；
5. 记录 executor-start evidence。

任一步失败全部回滚。ack 丢失后的 retry 只读取 authoritative runtime reconciliation
status；不会第二次消费、生成第二个 deploying event 或再次调用 adapter。

## Production gates

进入 deploying 事务前全部要求：

- exact protected-main SHA 和 exact successful CI evidence；
- immutable image reference 与 intent digest 一致；
- 当前有效且未 invalidated 的 exact Staging acceptance；
- 独立、approved、unexpired、未消费 approval；
- separation of duty；
- passed predeploy custom-format backup evidence，含 SHA-256 和 catalog validation；
- migration risk 不是 HIGH；MEDIUM 含 revision 与 rollback runbook；
- accepted 7-sample resource evidence；
- 当前 owner 持有未过期 exclusive Production lease；
- service set 仅包含 `api`、`feishu-worker`，不包含 `db`；
- rollback 另有 failed-deployment/current-revision/known-good target linkage。

## Migration risk

- `NONE`：无 persistent schema/data change；
- `LOW`：向后兼容的 table/nullable column/non-blocking index 等 additive change；
- `MEDIUM`：显著但可逆，必须提供明确 migration revision 和 rollback runbook；
- `HIGH`：drop、destructive/irreversible、breaking type 或 exclusive-lock change。

未知 migration operation 按 HIGH 处理。HIGH 在正常 Phase 1D-F 没有 override。

## Resource gate 与锁

resource evidence 复用已接受的精确规则：7 个约五秒间隔样本、约 30 秒窗口、median
MemAvailable 至少 1 GiB、每个样本至少 960 MiB、每个 SwapFree 至少 1.25 GiB，
root/Docker data 每个样本至少 5 GiB；缺失或畸形 fail closed。

`core.deployment_locks` 使用 server-derived 的唯一 `production:global` conflict
domain；调用方不能选择另一 domain 绕过冲突。lease 有递增 fencing token，所有
mutation-capable adapter 调用都携带并验证该 token。过期 lease 不可直接接管：
authoritative runtime observer 必须先证明旧 actor 不在执行中，记录 prior owner/token、
reconciliation evidence/时间，再可写入 new owner/new token。旧 token 会被拒绝，两个
executor（包括 lease expiry 后）不能同时修改 Production。

Production wiring is concrete rather than caller-injected: authenticated `gh api` verifies
the protected-main push/Quality Gate, the fixed compose runtime is observed read-only, and the
restricted adapter holds the canonical DB lock while it validates the current fence and invokes
only `docker compose ... up -d --no-deps` for the canonical allowlisted service set. The card
displays the full immutable `sha256:` digest. Feishu identities are resolved through a one-to-one,
privacy-safe `core.external_identities` mapping; a deployment requester is resolved from that
authenticated external identity, not a caller-supplied user UUID.

## 专用执行边界和 reconciliation

`ControlledExecutionBoundary` 没有 caller-constructible authorization API。每次 adapter
调用前，它在同一 authoritative DB boundary 重新锁定/验证 intent、artifact、Staging
acceptance、approval/consumption、service/config、fresh CI/backup/resource evidence、
execution identity 和 fencing token；随后由 runtime observer 证明 mutation 尚未开始。
adapter 只收到 immutable image reference、digest、canonical allowlisted service set、
deployment UUID、目标 SHA、fencing token 和 rollback 的 expected current revision；它收
不到 shell、GitHub mutation、approval mutation 或可扩展 scope。

执行结果必须记录 before/after revision、observed digest 和 canonical required health
schema。该 schema 按 selected services 覆盖 service readiness/liveness、HTTP/dependency、
digest、revision、migration 和 smoke；partial all-true dictionary 永不能标记成功。digest
不同、任一 health failure 或 executor failure 都进入 failed state。进程在 deploying 后
中断时，reconciliation 使用 runtime observer 的实际状态，不信任 caller 结果，不会盲目
重跑部署。

## Rollback

rollback 必须使用独立 deployment UUID、`rollback_production` approval、已知良好 SHA
和 digest，并链接失败 deployment、当前 Production revision 与明确目标。原 deploy
approval 的 request/decision 已被唯一 consumption 使用，且 action/binding 不匹配，
不能授权 rollback。rollback 复用相同 Staging、backup、resource、lock、executor、
health 和 append-only audit controls。

rollback adapter 调用前由 runtime observer 读取实际 Production revision/digest，并与
rollback intent expected current revision 比较；不一致时在 mutation 前 fail closed。CI
证据来自 protected-main/CI verifier 而非 caller boolean：repository、full SHA、main
membership、push run、Quality Gate、provenance 和 verified_at 会持久化且在 Production
boundary 按 bounded freshness 再验证。backup/resource evidence 都 deployment-bound；前者
含 archive/catalog/recoverability，后者含 7-sample checkpoint/recorded-completed time，过期
即 fail closed。

## Feishu 与审计

固定 notification 包含 event、deployment UUID、repository、SHA、digest 和 environment，
不包含 Secret、命令或执行能力。sink 必须接受 idempotency key，返回的 Feishu reference
写入 evidence；同 evidence key replay 不重复发送。

完整审计可仅由 PostgreSQL 重建：intent、CI/provenance、Staging、approval/binding、
consumption、backup、migration risk、resource samples result、lock、executor、service
set、revision、state、health、failure/rollback 和 notification reference 全部以
deployment UUID 关联，无需聊天历史。

## 上线边界

本实现合并后仍需 Agent C review、精确 merged-main CI、0003 migration 安全复核、
Production backup、隔离 migration 验证、独立 runtime approval 和受控演练。失败时停止，
不自动 downgrade，不删除 audit。Phase 1D-F 在真实同-digest Staging→Production 与
rollback drill 完成前不得标记 `accepted`；Phase 2A 保持 `not_started`。
