# Changelog

本文档采用 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 风格。项目版本尚未正式发布，因此先按阶段记录真实完成内容。

## [Unreleased]

### Corrected

- 将 Phase 1D 从错误的 `accepted` 纠正为 `in_progress`；已交付能力重新定义为 Phase 1D-0：Server Observability and Execution Foundation。
- 记录 Phase 1D-A 至 1D-F 的 Cloud Development Control Plane 缺口、实施顺序和验收标准。
- 删除未推送的错误本地 `phase1d-accepted` tag；保留已验证的服务器功能提交。
- 记录 Public GitHub 仓库和 Active `Protect main` Ruleset 的已确认状态；`CI / Quality Gate` 是唯一 configured required status check。

### Accepted

- Phase 1D-A GitHub Development Governance 已通过用户验收：PR #2、#3、#4 独立合并，main Ruleset、模板、CODEOWNERS 和 migration 人工审查规则均已验证。
- Phase 1D-B GitHub Actions CI 已通过验收：五个 job 全绿，`CI / Quality Gate` 已成为唯一 required check，临时失败与普通修复 commit 已验证失败阻断和恢复路径。
- Phase 1D-C Codex Cloud workflow 已通过验收：仓库连接和最小权限边界、非生产环境、zero-diff 只读任务、Cloud 分支和 PR #8 创建/更新均已验证；最新 synchronize event 自动触发的五个 CI job 全绿，required `CI / Quality Gate` 通过。

### Added

- Phase 1D-D 隔离 Staging 的 Compose、无效示例配置、共享安全库、preflight/deploy/verify/stop 脚本、静态测试和 CI 检查；当前仅为代码实现，未部署或执行 migration。
- Phase 1D-C Codex Cloud repository workflow 文档：GitHub 连接仅限 `Mxx1233/Maoxx-OS`，Cloud 环境为无 Secrets 或生产凭据且禁用 agent internet access 的非生产环境；成功的只读验证保持 zero file diff。
- Phase 1D-C Cloud 分支 `codex/implement-phase-1d-c-codex-cloud-validation` 已发布，Pull Request #8 已成功创建和更新；未绕过任何 CI 要求、直接写 `main`、自动批准或合并，也未访问 Production。
- Phase 1D-A Pull Request 模板、Codex task Issue 表单、Issue 配置和 CODEOWNERS。
- GitHub 开发治理、main Ruleset、任务分支、PR 和 migration 人工审查规则。
- Phase 1D-B 只读 GitHub Actions workflow：Python compile、Ruff、标准库测试、临时 PostgreSQL migration/integration、Compose config、Docker build 和统一 `CI / Quality Gate`。
- 固定 Ruff 0.16.1 的开发依赖和最小 Python 3.12 lint/format 基线。

### Changed

- Phase 1D-D 状态设为 `implemented_pending_verification`；Phase 1D 保持 `in_progress`，Phase 2A 保持 `not_started`。首次 Staging 部署需在 PR 合并和 post-merge 验收后单独人工批准。
- Phase 1D-A、Phase 1D-B 和 Phase 1D-C 为 `accepted`；Phase 1D 保持 `in_progress`，Phase 2A 保持 `not_started`。
- 文档-only Pull Request 同样受 required `CI / Quality Gate` 治理；PR #8 最新 synchronize event 已自动触发并通过全部五个 CI job。

### Known issues

- Phase 1D-D 尚无真实 Staging 部署、migration、健康、Worker 或 Production 前后不变量验收证据，因此不得标记为 `accepted`。
- Ruff formatter 精确排除 4 个既有格式文件，避免 Phase 1D-B 扩大为业务代码批量格式化；这些文件仍接受 compile 和 lint。
- 当前单 PR CI 范围尚未加入独立 API 测试、secret scanning 或破坏性 migration 自动扫描，对应审计项不得标记为 `configured`。

## [Phase 1D-0] - 2026-08-05

### Added

- Worker 容器内连接 readiness、事件计数和 Compose healthcheck。
- 飞书回复限流与传输错误的有界指数退避、抖动和失败分类。
- Worker 健康、回复失败路径、日志隐私和隔离 PostgreSQL migration/integration 测试。

### Changed

- Worker 关键日志采用机器可读事件字段，并以不可逆指纹替代完整消息和记录标识。
- 运维手册补充 Worker readiness、回复策略和隔离数据库测试流程。

### Fixed

- 运行中 API 镜像已与 `phase1c-accepted` ORM 元数据重新对齐，`alembic check` 恢复无漂移。

### Known issues

- 回复重试不持久化，Worker 重启后不保证尚未完成的回复最终送达。
- Worker 连接观测依赖当前飞书 SDK 的内部连接生命周期方法，升级 SDK 时必须回归验证。
- API 尚无正式认证，Worker 运维状态因此只在容器内开放。
- 本节仅代表服务器可观测性和执行基础完成；完整 Phase 1D 仍为 `in_progress`。

## [Phase 1C] - 2026-08-05

### Added

- 长期 AI 开发上下文文档、开发规则和架构决策记录。
- 飞书 tenant、sender type、sender `open_id` 和 chat type 的默认拒绝授权策略。
- 配置、飞书授权、安全回复和 ORM 元数据的标准库单元测试。
- PostgreSQL 自定义格式备份、SHA-256 校验和临时恢复演练。

### Changed

- SQLAlchemy 元数据已与 `0001_core_foundation` 的复合索引和消息去重约束对齐。
- 飞书成功确认固定为“已记录。”，不再拼接用户原始内容。

### Known issues

- Worker 可观测健康状态尚未建立。
- 完整数据库 integration/migration 测试尚未建立。
- 飞书回复尚无可靠 outbox 或补偿重试。
- API 尚无正式认证，当前依赖 localhost 网络边界。

## [Phase 1 baseline] - 2026-08-05

### Added

- Docker Compose 下的 PostgreSQL 16、FastAPI 和飞书 Worker。
- SQLAlchemy 与 Alembic 基础设施。
- `core.users`、`core.entities`、`core.raw_inputs`。
- API `/health`、`/health/db`、文字输入和最近输入查询。
- 飞书 `im.message.receive_v1` 文字接收、原始输入落库、外部消息 ID 去重和固定回复。
- 有效 Git 仓库、`main` 分支、阶段 1 基线提交和 `phase1-baseline` annotated tag。
