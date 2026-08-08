# Phase 1D-E Feishu Supervision and Interactive Approval

Phase 1D-E 当前为 `accepted`。Production 0002、Worker rollout、interactive-card
批准/拒绝/两次等一下和 fail-closed runtime probes 已完成验收；无第二个真实 Feishu
身份的 unauthorized-human 路径保留为已声明外部限制，并有自动化证据。Phase 1D
保持 `in_progress`，Phase 2A 保持 `not_started`。本阶段只实现监督通知和人工审批
记录，不执行受保护动作。

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

Production 数据库已经位于 `0002_phase_1d_e_approvals`，文字 fallback 与卡片 Worker
均已完成独立 rollout 和真实验收。Phase 1D-F 仍须通过独立 governed workflow，且
不得把 Phase 1D-E 的决定本身视为部署授权消费或受保护动作执行。

## 固定通知与审批卡片

CLI 只接受以下事件：

- `pr_created`
- `ci_passed`
- `ci_failed`
- `staging_started`
- `staging_passed`
- `staging_failed`
- `approval_required`（只能由已持久化审批请求生成）

普通状态通知继续使用固定文字。`approval_required` 以交互卡片作为主要 UX，显示
action 摘要、repository、可选 PR、完整 SHA、environment、过期时间和 request
UUID，并提供 `批准`、`拒绝`、`等一下` 三个按钮。按钮 payload 只包含
`request_id` 和 `action`，不携带 repository、SHA、environment、命令、Secret 或
任何可执行动作。发送使用现有有界重试；永久错误立即失败，重试耗尽返回非零。

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

## 卡片回调、授权与文字 fallback

主要审批路径使用现有 Worker WebSocket 长连接接收新版
`card.action.trigger` 回调，并通过 SDK 的独立
`register_p2_card_action_trigger` handler 处理，不经过普通消息解析。回调必须包含
有效的 callback event ID、按钮 action/request UUID、operator `open_id` 和
`open_chat_id`。服务端只信任 request UUID，并从 PostgreSQL 重新加载 repository、
SHA、environment、expiry 和当前决定状态。

卡片动作语义：

- `approve`：复用既有事务记录 `approved`；
- `reject`：复用既有事务记录 `rejected`；
- `wait`：取得 request row lock 并使用数据库时间检查 expiry/decision 后返回
  “稍后处理 / 仍待审批”，不插入 `approval_decisions`、不消费 request、也不修改
  `expires_at`。

卡片 callback 要求 tenant allowlist、普通 sender allowlist、独立 approver
allowlist 和精确 `FEISHU_SUPERVISION_CHAT_ID` 全部匹配。缺少 callback 字段、错误
chat、未知/过期/已决定 request 或数据库不可用均 fail closed。callback event ID
继续使用 `approval_decisions.feishu_event_id` 唯一约束保证 approve/reject 重放幂等；
重复 `wait` 不产生决定或状态变更。日志只保存 outcome 和不可逆 callback 指纹。

飞书开发者后台必须将“事件与回调”的回调订阅方式设为长连接，并添加新版卡片
回传交互 `card.action.trigger`。应用仍需已有的机器人发消息能力；无需新增公网 HTTP
callback、端口或容器。配置变更应按飞书要求发布应用版本后再做真实验收。

既有严格文字命令保留为 fallback：

只接受严格文本命令：

```text
批准 <request-uuid>
拒绝 <request-uuid>
```

文字审批命令在普通 `core.raw_inputs` 持久化之前分流，因此有效或畸形的审批类命令
均不会作为普通输入保存。命令必须同时通过：

1. tenant allowlist；
2. `sender_type == user`；
3. 普通 sender allowlist；
4. chat type allowlist；
5. 独立 approver allowlist；
6. 精确监督 chat ID。

卡片点击和文字 fallback 都只记录决定。批准卡片显示“已批准”不代表 PR 已合并或
Production 已部署；这些受保护动作仍完全属于 Phase 1D-F。

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

## 验收结果与后续边界

CI 使用 fake transport、fake identity、fake card callback 和临时 PostgreSQL 验证
卡片 payload、approve/reject/wait、授权、重试、幂等、并发、约束、trigger、migration
和 Alembic drift。Production 已完成真实批准、拒绝、两次等一下、重复、过期和
append-only 验收；审批只产生记录，不触发受保护动作。Phase 1D-F 若消费 Production
部署或 rollback 审批，必须使用独立精确 binding 和一次性原子 consumption，并经
单独 PR、CI、review、migration 与 runtime approval。

失败时停止，不自动 downgrade。代码可使用可审计 revert；schema 优先前向修复，
审批审计表和已写入决定保留。
