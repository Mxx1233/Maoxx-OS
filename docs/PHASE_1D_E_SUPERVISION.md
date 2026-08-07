# Phase 1D-E Feishu Supervision and Manual Approval

Phase 1D-E 当前为 `implemented_pending_verification`。Phase 1D-D 已通过真实
Staging 运行验收并标记为 `accepted`；Phase 1D 保持 `in_progress`，Phase 2A
保持 `not_started`。本阶段只实现监督通知和人工审批记录，不执行受保护动作，
不进入 Phase 1D-F。

## 边界

Phase 1D-E 复用现有 Production Feishu WebSocket Worker，不增加公开 API、容器、
Staging Worker、GitHub webhook、poller 或自动 PR/CI 监控。GitHub PR、required
`CI / Quality Gate`、人工 Squash merge、精确 merge SHA 和 Staging 验收继续是
代码与部署事实来源。

飞书审批只向 PostgreSQL 写入可审计决定。它不会：

- approve 或 merge GitHub Pull Request；
- push `main` 或绕过 CI；
- 部署或重启 Production；
- 执行 migration、备份、回滚或其他受保护动作。

这些执行能力属于 Phase 1D-F，届时必须独立设计审批消费和单次使用语义。

## 配置

配置只通过环境注入：

- `FEISHU_SUPERVISION_CHAT_ID`：固定监督单聊 ID；
- `FEISHU_APPROVER_OPEN_IDS`：独立审批人 `open_id` allowlist；
- `FEISHU_APPROVAL_TTL_SECONDS`：默认 1800，允许范围 1–86400 秒。

缺失 chat、空审批人 allowlist 或越界 TTL 均 fail closed。App Secret、Token、
完整身份标识和 `DATABASE_URL` 不得进入 Git、日志、PR、测试或审批表。

Production 飞书应用的主动发消息权限尚未验证。本 PR 不读取 Production 凭据、
不发送真实消息；主动消息能力只能在合并、Production post-merge 检查、备份和
migration 独立批准后验证。

## 固定通知

CLI 只接受以下事件：

- `pr_created`
- `ci_passed`
- `ci_failed`
- `staging_started`
- `staging_passed`
- `staging_failed`
- `approval_required`（只能由已持久化审批请求生成）

消息字段仅包括固定事件、repository、完整 SHA、必要的 PR、environment，以及
审批请求的 UUID/action/过期时间。不接受任意 URL、日志、stack trace、异常正文
或扩展字段。发送使用现有有界重试；永久错误立即失败，重试耗尽返回非零。

## 本地监督 CLI

查看参数：

```bash
python -m app.supervision_cli --help
```

固定状态通知示例（值为无效示例）：

```bash
python -m app.supervision_cli notify \
  --event ci_passed \
  --repository Example/Repository \
  --sha aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --pr-number 1 \
  --verify-github
```

创建审批请求示例：

```bash
python -m app.supervision_cli request-approval \
  --action production_deploy \
  --repository Example/Repository \
  --sha aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --pr-number 1 \
  --environment production \
  --idempotency-key production-1-aaaaaaaa \
  --verify-github
```

`--verify-github` 只执行 `gh` 读取：确认 commit、必要的 PR head，以及 CI 事件的
精确 `CI / Quality Gate`。它没有 GitHub mutation 权限。`approval_required` 流程
始终先提交 `core.approval_requests`，随后才发送包含请求 UUID 的消息；发送失败时
请求仍保留，可使用相同 idempotency key 安全重试。

## 审批命令与授权

只接受严格文本命令：

```text
批准 <request-uuid>
拒绝 <request-uuid>
```

审批命令在普通 `core.raw_inputs` 持久化之前分流，因此有效或畸形的审批类命令
均不会作为普通输入保存。命令必须同时通过：

1. tenant allowlist；
2. `sender_type == user`；
3. 普通 sender allowlist；
4. chat type allowlist；
5. 独立 approver allowlist；
6. 精确监督 chat ID。

未知、畸形、未授权、过期、冲突或数据库不可用均返回固定安全响应并 fail
closed。日志仅记录结果代码和短消息指纹，不记录命令正文或身份原值。

## 数据库与事务

`0002_phase_1d_e_approvals` 增加：

- `core.approval_requests`：UUID、用户、固定 action、repository、可选 PR、完整
  SHA、environment、唯一 idempotency key、数据库请求时间和过期时间；
- `core.approval_decisions`：每请求唯一决定、approved/rejected、actor user、
  完整 SHA-256 身份指纹、唯一飞书 event ID 和数据库决定时间。

固定 action/environment 映射为：`development_plan -> development`、
`merge_pr -> staging`（且 PR 必填）、`production_deploy -> production`。

决定事务对请求执行 `SELECT ... FOR UPDATE`，使用数据库时间判断过期，每请求
只允许一个决定。相同飞书事件和相同决定幂等；冲突事件或并发 approve/reject
只有一个能写入。数据库 trigger 禁止 UPDATE/DELETE 决策行，应用不提供修改或
删除路径。两个外键均使用 `ON DELETE RESTRICT`。

## 验证与上线边界

CI 使用 fake transport、fake identity 和临时 PostgreSQL，验证格式、授权、
重试、幂等、并发、约束、trigger、migration 和 Alembic drift，不使用真实飞书
凭据。

合并后仍需单独人工批准：

1. 同步精确 merge SHA 并要求其 `CI / Quality Gate` 成功；
2. 确认 Production/Staging 不变量和备份恢复点；
3. 在隔离目标验证 `0002`，再批准 Production forward migration 和 Worker
   rollout；
4. 使用非敏感固定通知验证主动消息权限；
5. 验证允许/拒绝、未授权、重复和过期审批，不输出身份或 Secret；
6. 确认审批只产生记录，不触发受保护动作。

失败时停止，不自动 downgrade。代码可使用可审计 revert；schema 优先前向修复，
审批审计表和已写入决定保留。
