# 飞书集成

## 当前能力

- 使用飞书企业自建应用和机器人能力。
- Feishu Worker 通过 WebSocket 长连接接收事件。
- 订阅事件：`im.message.receive_v1`。
- 当前支持单聊文字消息接收。
- 使用飞书 `message_id` 作为外部消息标识，并由数据库唯一约束去重。
- 成功入库后发送固定确认回复。
- 当前固定回复不是 AI 生成。

## 授权基线

阶段 1C 已在代码中实现以下授权顺序：

1. 校验 `tenant_key` 白名单。
2. 校验 `sender_type == user`。
3. 校验 sender `open_id` 白名单。
4. 校验 `chat_type`，默认只允许 `p2p`。

白名单通过环境变量配置，不得把真实 `open_id`、`tenant_key`、App Secret 或 Token 写入源码、文档、测试夹具或 Git。拒绝事件不得入库或回显内容，日志只能记录脱敏标识和拒绝原因。

代码采用默认拒绝策略，chat type 默认仅允许 `p2p`。Production 真实白名单、Worker 和 interactive-card callback 已完成 Phase 1D-E runtime acceptance。成功确认固定回复“已记录。”，群聊和其他场景均不回显敏感原文。

## Phase 1D-E 监督审批

Phase 1D-E 复用现有 WebSocket Worker。审批请求以三按钮交互卡片作为主要 UX：

```text
[批准] [拒绝] [等一下]
```

Worker 通过独立 `card.action.trigger` 长连接 callback handler 处理点击，重新从
PostgreSQL 加载 request，并要求 tenant、普通 sender、独立 approver 和精确监督
chat 全部匹配。`批准/拒绝` 复用既有 append-only 决定事务；`等一下` 不写决定、
不消费 request 且不延长 TTL。卡片 payload 只有 action 和 request UUID，不包含
受保护动作或权威 repository/SHA/environment 数据。

飞书开发者后台需把回调订阅方式设为长连接，并订阅新版卡片回传交互
`card.action.trigger`。既有严格文本审批继续作为 fallback：

```text
批准 <request-uuid>
拒绝 <request-uuid>
```

审批命令在普通输入持久化前分流，并额外要求独立 approver allowlist 和固定 supervision chat。决定写入 append-only 审计表，不触发 GitHub 或部署动作。主动通知由服务器本地 CLI 发起，可选使用只读 `gh` 核实精确 commit、PR head 和 `CI / Quality Gate`。

该能力为 `accepted`；Production 已通过真实批准、拒绝、两次等一下和受控
fail-closed runtime probes。无第二个真实 Feishu 身份的 unauthorized-human 路径保留
为已声明外部限制并有自动化证据。完整说明见
[Phase 1D-E Supervision](PHASE_1D_E_SUPERVISION.md)。

## 后续能力

- 图片、视频和文件接收及安全下载。
- 通用业务交互卡片和动态 card workflow。
- AI 提取候选的确认、修正和拒绝。
- 任务、提醒、项目和报告的卡片操作。

飞书是日常操作界面；部署、数据库迁移、备份恢复和故障处理仍通过服务器完成。
