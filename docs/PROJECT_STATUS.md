# Maoxx OS 当前状态

更新时间：2026-08-05

## 当前阶段

阶段 1C：安全、版本和迁移基线（进行中）。

## 当前 Alembic revision

`0001_core_foundation`，同时也是当前唯一 head。2026-08-05 实测 `alembic check` 未通过，原因是 ORM 未准确声明现有复合索引和唯一约束，并隐式请求冗余单列索引。

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

## 阶段 1C 待完成与阻塞

- PostgreSQL 自定义格式备份、SHA-256 校验和临时恢复验证尚未完成。
- ORM 与 Alembic 元数据存在索引/唯一约束声明漂移，需修复并使 `alembic check` 通过。
- 飞书身份授权需增加 tenant、sender `open_id` 和 chat type 白名单。
- 敏感消息确认回复仍需改为不回显原文。
- 阶段 1C 自动化测试和最终验收尚未完成。
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
- 自动化测试目录、测试依赖和 migration 测试尚未建立。
- Worker 缺少 healthcheck、结构化监控和可靠回复重试机制。
- 依赖和基础镜像使用版本范围或移动标签，构建尚未完全锁定。
- 当前 API 尚无正式认证机制。

## 当前风险

- Alembic 元数据漂移可能导致错误的自动生成 migration。
- 未授权飞书发送者可能写入默认用户数据。
- 固定回复仍可能回显敏感原文。
- 尚未完成备份恢复演练，生产 schema 变更不具备验收条件。

## 下一步

仅完成阶段 1C：备份与恢复验证、ORM 元数据对齐、飞书授权、敏感回复修复、基础测试和最终验收。不得创建阶段 2 表。

完成阶段 1C 后，进入阶段 2A：媒体与原始输入。

## 最近一次验收

阶段 1 基线已于 2026-08-05 建立：提交 `adfe64e`，annotated tag 为 `phase1-baseline`。阶段 1C 尚未验收。

运行状态可能随部署变化。代理开始工作时必须以实际只读检查为准，并报告与本文档的差异。

## 仓库内容规则

禁止提交密钥、Token、数据库备份、用户媒体、运行日志或缓存。
