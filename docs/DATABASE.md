# Maoxx OS 数据库

## 当前数据库

业务 schema 为 `core`，当前 Alembic revision 为 `0001_core_foundation`。

- `core.users`：用户资料和用户级默认设置。
- `core.entities`：跨模块通用实体骨架。
- `core.raw_inputs`：来自 API 或飞书的原始输入，使用 `(channel_code, external_message_id)` 去重。

## 阶段 2 设计范围

### 阶段 2A：媒体与原始输入

- `core.code_definitions`
- `core.media_assets`
- `core.raw_input_media`

### 阶段 2B：识别流水线

- `core.extraction_jobs`
- `core.extraction_records`
- `core.extracted_fields`

一张截图或其他媒体可产生多条 `extraction_records`，以支持多对象识别、重新提取和不同模型版本。`extracted_fields` 应同时支持 AI 原始值、人工修正值、置信度和来源定位。

### 阶段 2C：公共业务能力

- `core.tags`
- `core.entity_tags`
- `core.entity_media`
- `core.entity_links`
- `core.tasks`
- `core.reminders`
- `core.audit_logs`
- `core.metric_definitions`

## 数据库原则

- 主键统一使用 UUID。
- 所有用户数据包含 `user_id`，即使当前只有一个用户。
- 时间点统一使用 `TIMESTAMPTZ`，业务展示再转换用户时区。
- 核心可查询字段使用结构化列，扩展字段使用 JSONB。
- PostgreSQL 5432 不暴露公网。
- 多用户隔离优先使用数据库级约束，而非只依赖应用过滤。
- 关联表必须防止跨用户关联；必要时使用包含 `user_id` 的复合唯一键和外键。
- 审计日志只追加，不允许普通更新或删除路径。
- API Key、App Secret 和 Token 不写入业务数据库。
- 文件不保存宿主机绝对路径，使用 `storage_backend + storage_key`。
- 存储后端支持从 `local_fs` 迁移到 `tencent_cos`，迁移不改变业务引用。
- 删除策略必须显式设计：核心事实优先保留或软删除，纯关联可按评审结果级联。
- 每次 schema 变更都必须通过 Alembic migration、模型一致性检查和备份/回滚评审。
- PostgreSQL 是 source of truth；聊天、文件和 AI 输出本身不是正式事实源。
- AI 识别和提取结果必须版本化，重跑不得覆盖历史。
- 报告使用版本化快照，不覆盖历史报告。

## 后续阶段规划表

以下均为规划，不代表已经创建：

- 阶段 3 项目管理：项目类型、`projects`、项目阶段、里程碑、进展、指标和项目任务相关表。
- 阶段 4 模型网关：`model_runs`、`prompt_versions` 及必要 provider 元数据；API Key 不入库。
- 阶段 5 生活：`life_entries`。
- 阶段 5 运动：`sport_types`、`sport_activities`、`activity_metrics`、`strength_exercises`、`strength_sets`。
- 阶段 5 健康：`health_events`、`health_measurements`、`health_goals`。
- 阶段 6 学习：`courses`、`topics`、`study_sessions`、`study_reviews` 及复习计划结构。
- 阶段 7 求职：`organizations`、`job_positions`、`job_applications`、`application_events`、`interviews`、`application_documents`。
- 阶段 8 论文：`thesis_profiles`、`thesis_chapters`、`writing_sessions`、`literature_items`、`thesis_feedback`；论文属于项目模型。
- 阶段 9 报告：`analysis_runs`、`analysis_results`、`report_snapshots`。
- 阶段 10 知识库：`documents`、`document_chunks`、`embeddings`、`collections`、`citations` 及 Zotero 同步结构。

## 当前迁移注意事项

阶段 1C 已让 ORM 准确声明 `0001_core_foundation` 创建的索引与唯一约束，且当前工作树的 `alembic check` 无漂移。未修改已执行的 `0001` 文件；阶段 1C 验收前仍不得创建阶段 2 表。
