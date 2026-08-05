# Maoxx OS 当前状态

更新时间：2026-08-05

## 当前阶段

阶段 1C：安全、版本和迁移基线（进行中）。

## 当前 Alembic revision

`0001_core_foundation`，同时也是当前唯一 head。ORM 已准确声明现有复合索引和唯一约束；2026-08-05 使用当前工作树实测 `alembic check` 无漂移。

## 已完成

- Ubuntu 24.04 服务器环境。
- Docker 与 Docker Compose。
- PostgreSQL 16。
- FastAPI、SQLAlchemy 和 Alembic。
- `core.users`、`core.entities`、`core.raw_inputs`。
- 默认单用户 Maoxx，并保留未来多用户扩展结构。
- 飞书 WebSocket 长连接。
- 飞书文字消息入库。
- 基于外部 `message_id` 的数据库级去重。
- 固定确认回复。
- 有效 Git 仓库、`main` 分支、阶段 1 基线提交和 `phase1-baseline` annotated tag。
- PostgreSQL 自定义格式备份、SHA-256 校验和临时数据库恢复演练。
- ORM 与 `0001_core_foundation` 的索引和唯一约束元数据对齐。
- 飞书 tenant、sender `open_id`、sender type 和 chat type 的默认拒绝授权代码。
- 成功确认回复已改为固定“已记录。”，不再拼接原文。
- 13 项标准库单元测试，覆盖配置解析、授权、隐私回复和 ORM 元数据。

## 阶段 1C 待完成与阻塞

- 服务器 `.env` 尚需由用户填写真实飞书 tenant 和 sender 白名单；真实值不得进入 Git 或文档。
- 新 Worker 镜像尚未构建和部署，因此授权与安全回复尚未完成生产运行验收。
- 飞书允许/拒绝路径仍需在真实单聊事件中做验收。
- 阶段 1C 最终验收尚未完成。
- Worker 缺少可观测的连接与消费健康状态。

## 后续未完成

- 模型网关尚未接入，当前回复为固定逻辑。
- 阶段 2 公共数据底座尚未建立。
- 项目、生活、运动、健康、学习、求职、论文、分析和知识库模块尚未建立。

## 当前运行基线

- 当前 Alembic revision：`0001_core_foundation`。
- Compose 服务：`db`、`api`、`feishu-worker`。
- API 绑定：`127.0.0.1:8000`。
- PostgreSQL 不暴露宿主机端口。

2026-08-05 最近一次只读检查确认 `db` 与 `api` healthy、`feishu-worker` running、Compose 配置有效。该结果是时间点快照，不代替每次工作前检查。

## 已知技术债务

- Worker 和 API 入口逻辑尚未抽取到统一 Service Layer。
- 当前测试使用标准库 `unittest`，尚无独立临时 PostgreSQL 的完整 migration 测试套件。
- Worker 缺少 healthcheck、结构化监控和可靠回复重试机制。
- 依赖和基础镜像使用版本范围或移动标签，构建尚未完全锁定。
- 当前 API 尚无正式认证机制。

## 当前风险

- 运行中的旧 Worker 尚未包含新授权逻辑；部署前仍存在未授权写入风险。
- 若直接部署但未配置白名单，默认拒绝策略会阻止所有飞书消息入库。
- Worker 缺少独立健康检查，容器 running 不代表 WebSocket 正常消费。

## 下一步

由用户在服务器 `.env` 中配置真实白名单后，构建并部署 Worker，执行真实飞书允许/拒绝验收及完整最终检查。不得创建阶段 2 表。

完成阶段 1C 后，进入阶段 2A：媒体与原始输入。

## 最近一次验收

阶段 1 基线已于 2026-08-05 建立：提交 `adfe64e`，annotated tag 为 `phase1-baseline`。阶段 1C 尚未验收。

运行状态可能随部署变化。代理开始工作时必须以实际只读检查为准，并报告与本文档的差异。

## 仓库内容规则

禁止提交密钥、Token、数据库备份、用户媒体、运行日志或缓存。
