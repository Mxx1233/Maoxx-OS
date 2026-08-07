# Maoxx OS 路线图

状态枚举：`not_started`、`planned`、`in_progress`、`implemented_pending_verification`、`blocked`、`accepted`。只有当前阶段达到 `accepted` 且用户批准后才能进入下一阶段。

## 阶段 1：基础设施

- 目标：建立可运行的服务、数据库和飞书文字输入闭环。
- 数据库表：`core.users`、`core.entities`、`core.raw_inputs`。
- API：健康检查、数据库健康检查、文字输入和最近输入查询。
- 飞书交互：WebSocket、文字消息入库、`message_id` 去重和固定回复。
- 验收标准：Compose 服务健康，API/数据库可用，迁移到 head，飞书文字可幂等入库。
- 明确不包括：生产级 AI、媒体接收和任何业务领域模块。
- 当前状态：`accepted`。

## 阶段 1C：安全、版本和迁移基线

- 目标：建立可靠版本、备份、迁移一致性和飞书入口授权基线。
- 数据库表：不新增业务表；只对齐现有 ORM 元数据。
- API：保持现有 API，验证健康状态且不扩大暴露面。
- 飞书交互：校验 tenant、sender 和 chat type；确认回复不回显原文。
- 验收标准：有效 Git 基线；备份及恢复验证；`alembic check` 无漂移；授权测试通过；敏感回复不回显全文。
- 明确不包括：阶段 2 业务表、媒体下载和模型接入。
- 当前状态：`accepted`；Git、备份恢复、ORM 对齐、授权部署、安全回复、基础测试和真实飞书验收均已通过。

## 阶段 1D：Cloud Development Control Plane

- 目标：建立 GitHub 开发治理、自动化 CI、Codex Cloud 非生产协作、隔离 Staging、飞书监督审批和受控生产部署闭环。
- 数据库表：不新增业务表；审批审计或运维状态若需持久化，必须单独设计评审。
- 明确不包括：阶段 2 表、媒体下载、OCR、模型网关和领域业务功能。
- 当前状态：`in_progress`；Phase 1D-0 已完成，Phase 1D-A、Phase 1D-B 和 Phase 1D-C 为 `accepted`；Phase 1D-D 为 `implemented_pending_verification`，不得进入 Phase 1D-E 或 Phase 2A。

### Phase 1D-0：Server Observability and Execution Foundation

- 已完成：Worker WebSocket 连接状态、Worker healthcheck、API 健康状态、飞书事件计数、日志脱敏、幂等/重试/错误分类、自动化测试、数据库备份恢复验证和 Alembic 无漂移。
- 完成情况：已完成；这是服务器执行基础，不等同于完整 Cloud Development Control Plane。

### Phase 1D-A：GitHub Development Governance

- 目标：main 分支保护、禁止直接 push/force push、任务分支、PR 强制流程、PR 模板、Codex Issue 模板、CODEOWNERS 和 migration 人工审查。
- 当前状态：`accepted`；Public 仓库、Active `Protect main` Ruleset、强制 PR、治理模板、CODEOWNERS 和 migration 人工审查规则已通过实际 PR 流程验收。

### Phase 1D-B：GitHub Actions CI

- 目标：Python 语法、format、lint、默认单元测试、Alembic check、migration upgrade、Docker build、Compose config 和统一质量门禁；独立 API 测试、secret scanning 和破坏性 migration scanning 尚未配置。
- 当前状态：`accepted`；五个 job 已通过，`CI / Quality Gate` 是唯一 required check，且临时失败 commit 已证明失败门禁会阻止 PR 合并。

### Phase 1D-C：Codex Cloud

- 目标：连接仓库、读取开发规则、建立无生产密钥的非生产环境，并完成只读任务、测试分支和测试 PR。
- 当前状态：`accepted`；GitHub 连接仅授权 `Mxx1233/Maoxx-OS`，非生产 Cloud 环境禁用 agent internet access，Secrets 为 none 且不含生产凭据，只读验证成功并保持 zero file diff。Cloud 分支 `codex/implement-phase-1d-c-codex-cloud-validation` 和 Pull Request #8 已成功发布和更新；最新 `pull_request` synchronize event 自动触发的五个 CI job 全部通过，包括 required `CI / Quality Gate`。没有绕过门禁、直接写 `main`、自动批准或合并，也未访问 Production；不得进入 Phase 1D-D。
- 详细边界：[Codex Cloud repository workflow](CODEX_CLOUD.md)。

### Phase 1D-D：Staging

- 目标：建立与 Production 隔离、独立数据库和 storage、可按需启停且适应 2 CPU/2 GB RAM 的 Staging，并完成 migration 和健康验收。
- 当前状态：`implemented_pending_verification`；仅完成 Compose、配置示例、安全脚本、静态 CI 与文档代码实现。未部署 Staging、未执行 migration、未测试 Worker，也未修改 Production。首次部署须在 PR 合并和服务器 post-merge 验收后单独人工批准。
- 详细边界：[Phase 1D-D Staging](STAGING.md)。

### Phase 1D-E：Feishu Supervision and Approval

- 目标：通过飞书查看任务/计划/CI/PR/Staging，批准或退回计划、合并和 Production 部署，并具备身份校验、防重复审批和审计记录。

### Phase 1D-F：Controlled Deployment

- 目标：main 合并后部署 Staging，Production 独立审批，部署前备份和 migration 风险检查，Git SHA/镜像绑定、健康检查、失败回滚和飞书通知。

完整缺口、实施顺序和验收标准见 `docs/PHASE_1D_CONTROL_PLANE.md`。

## 阶段 2A：媒体与原始输入

- 目标：让原始输入安全关联本地媒体，并预留 COS 迁移能力。
- 数据库表：`core.code_definitions`、`core.media_assets`、`core.raw_input_media`。
- API：媒体上传、元数据查询、原始输入与媒体关联。
- 飞书交互：接收图片、视频和文件，返回安全的接收确认。
- 验收标准：文件类型/大小/哈希校验有效，路径不可越界，关联幂等，本地文件可迁移存储后端。
- 明确不包括：OCR/视觉识别、正式业务入库和 COS 生产迁移。
- 当前状态：`not_started`。

## 阶段 2B：识别流水线

- 目标：异步、可追踪地从原始输入和媒体提取结构化候选数据。
- 数据库表：`core.extraction_jobs`、`core.extraction_records`、`core.extracted_fields`。
- API：创建、查询、重试提取任务，读取和修正候选字段。
- 飞书交互：展示提取候选，允许确认、修正或拒绝。
- 验收标准：任务状态、重试和并发领取可靠；模型来源、置信度、AI 值和修正值可追踪。
- 明确不包括：正式多模型网关和领域业务表；第一版允许 mock extractor。
- 当前状态：`not_started`。

## 阶段 2C：公共业务能力

- 目标：提供跨业务模块复用的标签、关系、任务、提醒、审计和指标定义。
- 数据库表：`core.tags`、`core.entity_tags`、`core.entity_media`、`core.entity_links`、`core.tasks`、`core.reminders`、`core.audit_logs`、`core.metric_definitions`。
- API：标签和关系管理、任务与提醒 CRUD、指标定义查询、受控审计查询。
- 飞书交互：卡片管理标签、关系、任务、完成状态和提醒。
- 验收标准：用户隔离和跨用户关联约束有效，提醒幂等，审计只追加，指标定义可验证。
- 明确不包括：项目、健康、学习、求职等领域专用表。
- 当前状态：`not_started`。

## 阶段 3：项目管理

- 目标：管理项目类型、项目、阶段、里程碑、进展和项目指标。
- 数据库表：项目类型、projects、阶段、里程碑、项目进展和项目指标相关表，具体命名在阶段设计评审确定。
- API：项目、阶段、里程碑、进展和指标接口。
- 飞书交互：创建/更新项目，汇报进展，查看里程碑和待办卡片。
- 验收标准：完整项目闭环、权限隔离、审计和指标统计通过测试。
- 明确不包括：论文专用扩展、模型网关及其他领域模块。首批项目为 Maoxx OS 和硕士论文。
- 当前状态：`not_started`。

## 阶段 4：模型网关

- 目标：统一管理模型供应商、调用、Prompt、结构化输出、路由和降级。
- 数据库表：`model_runs`、`prompt_versions` 及必要的 provider 配置元数据；不保存 API Key。
- API：内部模型调用、运行记录和路由管理接口。
- 飞书交互：由意图识别和模块路由驱动自然语言操作。
- 验收标准：OpenAI-compatible 首个 provider 可用，超时、重试、错误、降级和结构化校验可观测。
- 明确不包括：首版多 provider 并行接入、复杂自治代理和知识库 RAG。
- 当前状态：`not_started`。

## 阶段 5：生活、运动和健康

- 目标：记录和分析生活、运动及健康信息。
- 数据库表：`life_entries`、`sport_types`、`sport_activities`、`activity_metrics`、`strength_exercises`、`strength_sets`、`health_events`、`health_measurements`、`health_goals`。
- API：记录、查询、趋势和受控导出接口。
- 飞书交互：快速记录、确认、提醒和隐私安全的摘要。
- 验收标准：敏感数据保护、单位校验、用户隔离和趋势准确性通过测试。
- 明确不包括：医疗诊断、处方建议或替代专业医疗服务。
- 当前状态：`not_started`。

## 阶段 6：学习管理

- 目标：管理学习计划、材料、进度和复习。
- 数据库表：`courses`、`topics`、`study_sessions`、`study_reviews` 及复习计划相关结构。
- API：计划、资源、记录和复习接口。
- 飞书交互：记录学习、查询计划、接收复习提醒。
- 验收标准：学习闭环、提醒和统计可验证。
- 明确不包括：完整 LMS、课程售卖和多人教学平台。
- 当前状态：`not_started`。

## 阶段 7：求职管理

- 目标：管理职位、公司、申请流程、面试和跟进。
- 数据库表：`organizations`、`job_positions`、`job_applications`、`application_events`、`interviews`、`application_documents`。
- API：求职流水线和跟进接口。
- 飞书交互：录入职位、更新申请状态、面试提醒和安全摘要。
- 验收标准：状态流转、敏感信息保护和提醒可靠性通过测试。
- 明确不包括：招聘方 ATS、多用户协作招聘和自动替用户投递。
- 当前状态：`not_started`。

## 阶段 8：论文项目扩展

- 目标：在项目管理之上支持论文研究、文献、实验和写作流程。
- 数据库表：`thesis_profiles`、`thesis_chapters`、`writing_sessions`、`literature_items`、`thesis_feedback`；论文仍属于 `projects.projects`。
- API：研究阶段、文献关联、实验和写作进度接口。
- 飞书交互：记录研究进展、关联材料、提示里程碑。
- 验收标准：与项目数据一致，来源可追踪，扩展不破坏通用项目模型。
- 明确不包括：知识库向量检索、Zotero 同步和自动代写定稿。
- 当前状态：`not_started`。

## 阶段 9：分析和报告

- 目标：跨模块生成可验证的统计、趋势和报告。
- 数据库表：`analysis_runs`、`analysis_results`、`report_snapshots`。
- API：报告生成、查询和导出接口。
- 飞书交互：请求日报/周报，使用卡片查看摘要和下钻链接。
- 验收标准：指标口径明确、结果可追溯、隐私边界和性能达标；日报保留 1 年，周报/月报保留 3 年，年报长期保留；报告版本化且不覆盖历史。
- 明确不包括：知识库问答和不可解释的 AI 自动决策。
- 当前状态：`not_started`。

## 阶段 10：知识库、向量检索、RAG、Zotero

- 目标：建立可追溯知识库、语义检索、RAG 和 Zotero 同步。
- 数据库表：`documents`、`document_chunks`、`embeddings`、`collections`、`citations` 及 Zotero 同步状态结构。
- API：导入、索引、检索、引用和同步接口。
- 飞书交互：知识问答、来源引用、文献保存与同步状态卡片。
- 验收标准：回答有来源、权限过滤先于检索、同步幂等、可删除和可重建索引。
- 明确不包括：替代 PostgreSQL 事实数据、无来源回答和绕过用户权限的全局检索。
- 当前状态：`not_started`。
