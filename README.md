# Maoxx OS

> 真正属于你自己的 Personal AI Operating System —— 一个主要通过飞书使用的个人 AI 操作系统。

Maoxx OS 不是聊天机器人，不是简单知识库，也不是简单待办工具。它以**用户拥有的数据**为基础，统一管理项目、论文、职业、学习、生活、运动、健康、任务、分析与知识。AI 是助手，不是数据所有者，也不是事实数据源；重要业务记录在正式入库前支持确认与修正，来源始终可追溯。

系统以**持续运行十年以上**为设计尺度：数据可导出、可迁移、可备份、可恢复、可追溯，不绑定单一模型供应商、不绑定单一云存储供应商。

## 当前状态

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 1A-1D | 基础架构：飞书入口、FastAPI、PostgreSQL、CI/CD、Staging | ✅ 已验收 |
| 1D-E | 飞书监督与审批控制面（交互卡片审批） | ✅ 已验收 |
| 1D-F | 受控部署（13 态状态机 + 生产 Supervisor） | 🔄 评审通过，待生产验收 |
| 1D-G | Intent-Driven 多 Agent 编排 | ⏳ 规划中（Hermes Kanban 探索已完成） |
| Phase 2+ | 媒体输入、识别流水线、业务能力、模型网关、知识库 | ⏳ 未开始 |

## 功能特性

- **飞书为主交互界面**：文字消息接收与入库、语音转文字、交互卡片审批（批准/拒绝/稍后处理）
- **PostgreSQL 事实数据源**：SQLAlchemy + Alembic 迁移链，审计表 append-only，关键约束 DB 级强制
- **受控部署闭环**：意图 → 工件 → Staging → 人工审批 → Production → 健康检查/回滚 的 13 态状态机
- **生产 Supervisor**：唯一被授权执行 Docker mutation 的主体，带 fencing 锁、持久化 journal（11 事件 fsync）、120 秒健康窗口、不可变工件验证
- **安全防线**：GitHub CI 同 run 核验、生产备份 + canary 恢复验证、资源门禁、审批防自批（SoD）、身份指纹审计
- **运维工具**：监督 CLI（通知/审批/身份/备份）+ 部署编排 CLI（全状态机命令）

## 架构概览

```
飞书 (日常入口)
   │  WebSocket
   ▼
feishu-worker ──► PostgreSQL 16 (事实数据源)
   │
api (FastAPI) ───► /health /health/db
   │
   ▼
生产 Supervisor ──► Docker Compose (唯一 mutation 主体)
```

三个 Docker Compose 服务：`db`（PostgreSQL 16）、`api`（FastAPI）、`feishu-worker`（飞书 WebSocket）。

```
/opt/maoxx-os
├── app/
│   ├── api/            # FastAPI 路由
│   ├── services/       # 业务服务：审批、部署、监督、身份、备份、资源
│   ├── feishu_worker.py  # 飞书消息处理
│   ├── supervision_cli.py # 监督/审批/身份/备份 CLI
│   └── deployment_cli.py  # 部署编排 CLI
├── alembic/            # 数据库迁移（0001 → 0004）
├── tests/              # 154 个单元测试 + PostgreSQL 集成测试
├── docs/               # 完整项目文档（VISION/ARCHITECTURE/ROADMAP/...）
└── docker-compose.yml  # db + api + feishu-worker
```

## 快速开始

### 依赖

- Docker + Docker Compose
- 飞书自建应用（App ID / App Secret / 消息与卡片权限）

### 启动

```bash
# 1. 配置环境变量（飞书应用凭证、数据库等）
cp .env.example .env
# 编辑 .env 填入 FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_VERIFICATION_TOKEN 等

# 2. 启动三件套
docker compose up -d

# 3. 初始化数据库
docker compose exec -T api alembic upgrade head

# 4. 验证
docker compose ps                # 三服务 healthy
docker compose exec -T api alembic current   # 应为 0004_approval_card_message_id
curl http://127.0.0.1:8000/health
```

### 测试

```bash
# 单元测试（需要 CI 同款假值环境变量，见 .github/workflows/ci.yml）
python -m unittest discover -s tests

# PostgreSQL 集成测试（需要一次性 postgres 容器 + RUN_DATABASE_INTEGRATION_TESTS=1）
```

## 开发指南

- **必读**：`AGENTS.md`（仓库最高级工程规则）+ `docs/` 下的 VISION / ARCHITECTURE / ROADMAP / SECURITY / OPERATIONS
- 所有阶段变更遵循 `docs/ROADMAP.md`，阶段须经用户验收后才进入下一阶段
- 代码库保持"不依赖过往聊天记录即可理解"：重要背景、决策、规则都在仓库内
- CI：GitHub Actions 五项检查（Quality Gate / Docker Build / Unit Tests / PostgreSQL Integration / Quality）
- 项目状态实时登记册：服务器 `/home/ubuntu/progress-registry/maoxx-os.md`

## 安全与数据主权

- PostgreSQL 是事实数据源，AI 不持有最终数据
- 生产部署全流程 fail-closed：任何不确定状态 → 拒绝继续
- 审计表（审批、部署、身份映射）append-only，DB 触发器强制
- 备份 = pg_dump 自定义格式 + 目录校验 + canary 恢复验证，30 分钟内完成才算有效
- 生产 Docker mutation 只能由 Supervisor 执行，杜绝绕过

## 路线图

- **1D-F** 生产验收（备份 → migration → build once → Staging → 飞书审批 → Production）
- **1D-G** 多 Agent 编排自动化（Hermes Kanban + delegation + cron 替代手工协调）
- **Phase 2** 媒体与原始输入、识别流水线、公共业务能力（tasks/reminders/audit）
- **Phase 4** 模型网关（Model Gateway，多 provider 路由）
- **Phase 10** 知识库 / 向量检索 / RAG / Zotero

## License

私有项目，保留所有权利。
