# Phase 1D Cloud Development Control Plane

## 状态和边界

Phase 1D 当前状态为 `in_progress`。Phase 1D-0：Server Observability and Execution Foundation 已完成，但它只覆盖服务器端可观测性、运行健康、测试和恢复基础，不等同于完整 Cloud Development Control Plane。

Phase 1 和 Phase 1C 保持 `accepted`。Phase 2A 保持 `not_started`；完整 Phase 1D 验收前不得进入。

本审计只使用：`configured`、`partially_configured`、`not_configured`、`cannot_verify`。仓库已由用户确认设为 Public；`Protect main` Ruleset 已启用。其他 GitHub 和 Codex Cloud 网页设置在没有证据时仍标记 `cannot_verify`。

## 35 项缺口审计

| # | 核验项目 | 状态 | 证据或缺口 |
|---:|---|---|---|
| 1 | GitHub repository sync | configured | Public 仓库和 `origin` 已配置；PR #2、#3、#4 已独立合并，本地 main 与 `origin/main` 同步。 |
| 2 | main branch protection | configured | 用户已确认 Active `Protect main` Ruleset：禁止删除和 force push，bypass 为空。 |
| 3 | Pull Request mandatory workflow | configured | Ruleset 要求 PR 和 conversation resolution；required approvals 为 0，status checks 等待 Phase 1D-B。 |
| 4 | PR template | configured | `.github/pull_request_template.md` 已实现，等待 PR 页面展示验证。 |
| 5 | Codex task Issue template | configured | `.github/ISSUE_TEMPLATE/codex-task.yml` 和 `config.yml` 已实现。 |
| 6 | CODEOWNERS | configured | `.github/CODEOWNERS` 由 `@Mxx1233` 覆盖全仓库和高风险路径；Ruleset 尚未强制 owner review。 |
| 7 | GitHub Actions CI | not_configured | 不存在 `.github/workflows/`。 |
| 8 | Python lint / format | not_configured | 无 Ruff/Black 等配置和自动执行入口。 |
| 9 | unit and API tests in CI | not_configured | 本地测试存在，但没有 CI。 |
| 10 | Alembic check in CI | not_configured | 仅有本地/服务器手工检查。 |
| 11 | migration upgrade test in CI | not_configured | 隔离测试已存在，但未接入 CI。 |
| 12 | destructive migration scan | not_configured | 无自动扫描规则或脚本。 |
| 13 | Docker image build in CI | not_configured | 无 CI build job。 |
| 14 | docker compose config check | not_configured | 仅有人工运维命令。 |
| 15 | secret scanning | not_configured | 无仓库工作流；GitHub 网页 secret scanning 设置无法验证。 |
| 16 | Codex Cloud GitHub connection | cannot_verify | 只能在 Codex Cloud/GitHub 网页确认。 |
| 17 | Codex Cloud repository permission | cannot_verify | 只能在 Codex Cloud/GitHub App 权限页确认。 |
| 18 | Codex Cloud environment | cannot_verify | 仓库和服务器无云环境证据。 |
| 19 | Codex Cloud read-only validation task | cannot_verify | 无可核验任务记录。 |
| 20 | Codex Cloud test branch | cannot_verify | 远端状态不可验证，仓库无任务证据。 |
| 21 | Codex Cloud test Pull Request | cannot_verify | 无可核验 PR 证据。 |
| 22 | Staging environment | not_configured | 当前只有 Production Compose 运行环境。 |
| 23 | staging database isolation | not_configured | 无独立 Staging 数据库配置。 |
| 24 | staging storage isolation | not_configured | 无独立 Staging storage 配置。 |
| 25 | staging deployment workflow | not_configured | 无 Staging 部署脚本或工作流。 |
| 26 | Feishu development-plan approval | not_configured | 只有文字输入授权，没有计划审批流程。 |
| 27 | Feishu PR notification | not_configured | 无 GitHub/飞书通知集成。 |
| 28 | Feishu merge approval | not_configured | 无合并审批动作或状态机。 |
| 29 | Feishu staging-result notification | not_configured | 无 Staging 和通知链路。 |
| 30 | Feishu production-deployment approval | not_configured | 无 Production 部署审批流程。 |
| 31 | approval identity verification | partially_configured | 已有 tenant/open_id/chat type 授权原语，但尚未用于审批动作。 |
| 32 | approval audit record | not_configured | 无审批审计模型或只追加记录。 |
| 33 | controlled production deployment | not_configured | 当前为服务器人工 Compose 操作，无受控部署工作流。 |
| 34 | deployment rollback | partially_configured | 运维手册有人工回滚原则，但无自动化、版本绑定和演练闭环。 |
| 35 | production backup before migration | partially_configured | 有备份/恢复规则和已验证流程，但无部署门禁自动强制。 |

## 子阶段真实状态与验收标准

### Phase 1D-A：GitHub Development Governance

- 当前阶段状态：`accepted`；审计项 1–6 均为 `configured`，用户已完成最终验收。
- 实施：任务分支、PR/Codex Issue 模板、CODEOWNERS 和 migration review checklist 已建立；main Ruleset 已禁止直接 push、force push和删除，并要求 PR。required status checks 留待 Phase 1D-B。
- 验收：受保护 main 无法直接 push；测试分支只能通过 PR 合并；force push/删除被拒绝；migration PR 明确触发人工审查。

### Phase 1D-B：GitHub Actions CI

- 当前状态：`not_configured`。
- 实施：增加固定权限和固定版本的 workflow，覆盖语法、format、lint、单元/API 测试、Alembic drift、空库 upgrade、破坏性 migration 扫描、Docker build、Compose config 和 secret scan。
- 验收：测试 PR 的所有 job 可重复通过；故意引入格式错误、secret 和破坏性 migration 时对应 job 必须失败；branch protection 要求这些检查通过。

### Phase 1D-C：Codex Cloud

- 当前状态：`cannot_verify`。
- 实施：用户连接 `Mxx1233/Maoxx-OS`，授予最小仓库权限，建立不含生产密钥的环境；运行读取 AGENTS/docs 的只读任务，再创建测试分支和测试 PR。
- 验收：任务日志证明规则已读取；无生产凭据；Codex 不能直接写 main；测试 PR 经过相同 CI 和审查流程。

### Phase 1D-D：Staging

- 当前状态：`not_configured`。
- 实施：设计按需启动的独立 Compose/project、数据库、volume/storage 和非生产配置，限制资源适应 2 CPU/2 GB RAM。
- 验收：Staging 无法连接 Production 数据库或 storage；migration、健康检查和测试通过；验收后可关闭且 Production 不受影响。

### Phase 1D-E：Feishu Supervision and Approval

- 当前状态：`partially_configured`。
- 实施：复用现有授权原语，设计任务/计划/CI/PR/Staging 通知和计划、合并、Production 部署审批；增加审批身份、幂等和只追加审计。任何新表先单独评审。
- 验收：允许用户可批准/退回；未授权用户、群聊、重复和过期审批均被拒绝或幂等处理；每次审批可追溯且不泄露敏感内容。

### Phase 1D-F：Controlled Deployment

- 当前状态：`partially_configured`。
- 实施：将 main SHA、构建镜像、Staging 结果、Production 独立审批、备份校验、migration 风险检查、健康检查、回滚和飞书结果通知串成受控流程。
- 验收：未经独立审批不能部署 Production；镜像可追溯到 Git SHA；migration 前备份和风险检查是强制门禁；失败可回到已验证版本；不删除 volume、不执行破坏性 downgrade。

## 缺失文件

- `.github/workflows/ci.yml`
- Python format/lint 配置（建议 `pyproject.toml`）
- destructive migration scan 脚本及测试
- secret scan 配置
- Staging Compose/env example、启动/关闭/验证脚本
- 受控部署与回滚脚本/工作流
- 飞书监督审批服务、状态模型、审计设计和测试

## 需要用户手动完成的网页操作

1. GitHub：Public 仓库和 main Ruleset 已配置；Phase 1D-B 后补充 required status checks，Phase 1D-A 验证后再评审是否启用 CODEOWNERS review 和 required approvals。
2. GitHub：确认 GitHub secret scanning/Push Protection 可用状态；不得把生产 Secret 放入 Actions。
3. Codex Cloud：连接仓库并授予最小权限；创建无生产密钥环境；执行只读任务、测试分支和测试 PR。
4. Feishu：审批后续应用权限、事件/卡片配置及真实允许/拒绝/重复审批验收。
5. Production：在受控部署设计评审后确认审批人、备份位置、维护窗口和回滚责任人。

## 推荐实施顺序

1. Phase 1D-A：已验收 GitHub 变更治理和保护边界。
2. Phase 1D-B：下一子阶段；让 PR 具备可强制执行的质量门禁，尚未开始实施。
3. Phase 1D-C：让 Codex Cloud 在相同治理和 CI 下完成最小权限验证。
4. Phase 1D-D：建立与 Production 隔离的验证目标。
5. Phase 1D-E：在明确 PR、CI、Staging 状态后建立飞书监督审批。
6. Phase 1D-F：最后把已验证能力串成受控生产部署闭环。

## 最小可执行的下一个任务

等待用户单独批准 Phase 1D-B 设计与实施；不得自动开始 CI 工作，不得进入 Phase 2A。
