# Phase 1D Cloud Development Control Plane

## 状态和边界

Phase 1D 当前状态为 `in_progress`。Phase 1D-0：Server Observability and Execution Foundation 已完成，但它只覆盖服务器端可观测性、运行健康、测试和恢复基础，不等同于完整 Cloud Development Control Plane。

Phase 1 和 Phase 1C 保持 `accepted`。Phase 1D-A 至 Phase 1D-E 为 `accepted`，Phase 1D-F 为 `implemented_pending_verification`，Phase 1D 整体保持 `in_progress`。Phase 2A 保持 `not_started`；不得进入 Phase 2A。

本审计只使用：`configured`、`partially_configured`、`not_configured`、`cannot_verify`。仓库已由用户确认设为 Public；`Protect main` Ruleset 已启用。其他 GitHub 和 Codex Cloud 网页设置在没有证据时仍标记 `cannot_verify`。

## 35 项缺口审计

| # | 核验项目 | 状态 | 证据或缺口 |
|---:|---|---|---|
| 1 | GitHub repository sync | configured | Public 仓库和 `origin` 已配置；PR #2、#3、#4 已独立合并，本地 main 与 `origin/main` 同步。 |
| 2 | main branch protection | configured | 用户已确认 Active `Protect main` Ruleset：禁止删除和 force push，bypass 为空。 |
| 3 | Pull Request mandatory workflow | configured | Ruleset 要求 PR 和 conversation resolution；required approvals 为 0，`CI / Quality Gate` 是唯一 required status check。 |
| 4 | PR template | configured | `.github/pull_request_template.md` 已实现，等待 PR 页面展示验证。 |
| 5 | Codex task Issue template | configured | `.github/ISSUE_TEMPLATE/codex-task.yml` 和 `config.yml` 已实现。 |
| 6 | CODEOWNERS | configured | `.github/CODEOWNERS` 由 `@Mxx1233` 覆盖全仓库和高风险路径；Ruleset 尚未强制 owner review。 |
| 7 | GitHub Actions CI | configured | 只读 `CI` workflow 的五个 job 已在 PR #7 运行通过；汇总 gate 已设为唯一 required check。 |
| 8 | Python lint / format | configured | Ruff compile/lint/format 门禁已运行通过；4 个既有格式文件被精确排除并已记录技术债务。 |
| 9 | unit and API tests in CI | partially_configured | 21 项默认测试已接入 CI；独立 API 测试尚未增加。 |
| 10 | Alembic check in CI | configured | PR #7 的临时 PostgreSQL job 已通过 heads/current/check。 |
| 11 | migration upgrade test in CI | configured | PR #7 已通过临时 PostgreSQL 空库 upgrade 和隔离 integration test。 |
| 12 | destructive migration scan | not_configured | 无自动扫描规则或脚本。 |
| 13 | Docker image build in CI | configured | PR #7 的本地 runner 镜像构建通过，未登录 registry 或 push。 |
| 14 | docker compose config check | configured | PR #7 使用临时安全 `.env` 完成 Compose config 验证。 |
| 15 | secret scanning | not_configured | 无仓库工作流；GitHub 网页 secret scanning 设置无法验证。 |
| 16 | Codex Cloud GitHub connection | configured | Codex Cloud 已连接 GitHub；授权范围仅为 `Mxx1233/Maoxx-OS`。 |
| 17 | Codex Cloud repository permission | configured | Repository access 限于 `Mxx1233/Maoxx-OS`，不授权其他仓库或 Production 资源。 |
| 18 | Codex Cloud environment | configured | 使用无 Secrets、无生产凭据的非生产 Cloud 环境；agent internet access disabled。 |
| 19 | Codex Cloud read-only validation task | configured | 只读任务成功读取 `AGENTS.md` 和强制文档，结果为 zero file diff。 |
| 20 | Codex Cloud test branch | configured | Cloud 分支 `codex/implement-phase-1d-c-codex-cloud-validation` 已发布，不直接写 `main`。 |
| 21 | Codex Cloud test Pull Request | configured | Pull Request #8 已成功创建和更新并指向 `main`；最新 synchronize event 自动触发的五个 CI job 全部通过，包括 required `CI / Quality Gate`。 |
| 22 | Staging environment | configured | 独立 Compose project 的 db/api 已通过真实运行验收，Worker 关闭。 |
| 23 | staging database isolation | configured | retained PostgreSQL volume、internal network、无宿主端口和 Alembic 幂等已验证。 |
| 24 | staging storage isolation | configured | 独立 storage realpath、mount 和 Production 不重叠已验证。 |
| 25 | staging deployment workflow | configured | immutable build、health、migration、验证、失败安全清理和成功保持运行均已验证。 |
| 26 | Feishu development-plan approval | configured | 文字 fallback 和 interactive-card 决定记录已部署并完成真实 runtime acceptance。 |
| 27 | Feishu PR notification | configured | 固定通知、只读 `gh` CLI 和主动消息能力已验收。 |
| 28 | Feishu merge approval | configured | merge action 可记录决定但不会 merge；受保护动作边界已验收。 |
| 29 | Feishu staging-result notification | configured | 固定 staging started/passed/failed 通知能力已实现并纳入已验收监督边界。 |
| 30 | Feishu production-deployment approval | partially_configured | production action 已真实记录决定且不部署；Phase 1D-F 一次性消费实现等待 PR/CI/运行验收。 |
| 31 | approval identity verification | configured | tenant/sender/chat、独立 approver、固定 supervision chat 和 card callback 已完成真实验收。 |
| 32 | approval audit record | configured | Production 0002、request/append-only decision、时限、幂等和并发约束已验收。 |
| 33 | controlled production deployment | partially_configured | 本分支已实现持久门禁和专用 executor，尚未合并、部署或真实演练。 |
| 34 | deployment rollback | partially_configured | 已实现独立 intent/approval/locking/health/state flow，尚未真实演练。 |
| 35 | production backup before migration | partially_configured | 既有备份恢复已验证，本分支新增 deployment evidence 门禁；尚未真实部署验收。 |

## 子阶段真实状态与验收标准

### Phase 1D-A：GitHub Development Governance

- 当前阶段状态：`accepted`；审计项 1–6 均为 `configured`，用户已完成最终验收。
- 实施：任务分支、PR/Codex Issue 模板、CODEOWNERS 和 migration review checklist 已建立；main Ruleset 已禁止直接 push、force push 和删除，并要求 PR。`CI / Quality Gate` 是唯一 configured required status check。
- 验收：受保护 main 无法直接 push；测试分支只能通过 PR 合并；force push/删除被拒绝；migration PR 明确触发人工审查。

### Phase 1D-B：GitHub Actions CI

- 当前状态：`accepted`。
- 实施：增加固定权限和固定版本的 workflow，覆盖语法、format、lint、默认单元测试、Alembic drift、空库 upgrade、Docker build、Compose config 和统一 quality gate。独立 API 测试、secret scanning 和破坏性 migration scanning 尚未配置。
- 验收：`CI / Quality`、`CI / Unit Tests`、`CI / PostgreSQL Integration` 和 `CI / Docker Build` 全部通过；汇总的 `CI / Quality Gate` 只在四项成功时通过，并已由 Ruleset 设为唯一 required check。临时失败 commit `d832430` 使 quality 和 gate 失败、PR 状态变为 `unstable`；普通修复 commit `911ef67` 恢复后五项全绿。当前单 PR 方案不包含独立 API、secret 或破坏性 migration 自动扫描，这些审计项不得标为 `configured`。

### Phase 1D-C：Codex Cloud

- 当前状态：`accepted`。
- 实施：Codex Cloud GitHub 连接仅授权 `Mxx1233/Maoxx-OS`；非生产 Cloud 环境禁用 agent internet access，Secrets 为 none，且不含生产凭据。成功的只读任务读取了 `AGENTS.md` 和强制文档并保持 zero file diff。Cloud 分支 `codex/implement-phase-1d-c-codex-cloud-validation` 已发布，Pull Request #8 已成功创建。
- 验收：PR #8 最新 `pull_request` synchronize event 自动触发 CI；`CI / Quality`、`CI / Unit Tests`、`CI / PostgreSQL Integration`、`CI / Docker Build` 和 `CI / Quality Gate` 五项全部通过，required gate 已满足。未绕过任何 CI 要求；Codex 未直接写 `main`，未自动批准或合并，也未访问 Production。详细记录见 [Codex Cloud repository workflow](CODEX_CLOUD.md)。

### Phase 1D-D：Staging

- 当前状态：`accepted`。
- 实施：独立 Compose project、数据库、network、volume、storage、端口和凭据边界及按需启动、migration、验证、停止脚本已经实现；默认 Worker 受 `feishu-test` profile 隔离。
- 验收：Staging 无法连接 Production 数据库或 storage；migration、健康检查和测试通过；验收后可关闭且 Production 不受影响。
- 精确批准 SHA 的 immutable build、retained DB volume 重用、Alembic upgrade 幂等、DB/API health、HTTP、network/volume/port/mount/image 隔离和 Production 不变量已通过真实验收。Staging db/api 保持运行，Worker 关闭。

### Phase 1D-E：Feishu Supervision and Approval

- 当前阶段状态：`accepted`；Production 0002、Worker rollout、interactive-card 批准/拒绝/两次等一下及受控 fail-closed probes 已完成 runtime acceptance。
- 实施：复用现有授权原语，设计任务/计划/CI/PR/Staging 通知和计划、合并、Production 部署审批；增加审批身份、幂等和只追加审计。任何新表先单独评审。
- 验收：允许用户可批准/退回；未授权用户、群聊、重复和过期审批均被拒绝或幂等处理；每次审批可追溯且不泄露敏感内容。
- 边界：审批只记录决定，不 mutate GitHub、不 merge、不部署；详见 [Phase 1D-E Supervision](PHASE_1D_E_SUPERVISION.md)。

### Phase 1D-F：Controlled Deployment

- 当前状态：`implemented_pending_verification`。
- 实施：本分支已将 main SHA、单次 immutable artifact、Staging 结果、Production 独立审批及原子一次性消费、备份校验、migration 风险、resource gate、exclusive lease、健康检查、reconciliation、独立 rollback 和飞书结果引用串成受控流程。
- 验收：未经独立审批不能部署 Production；镜像可追溯到 Git SHA；migration 前备份和风险检查是强制门禁；失败可回到已验证版本；不删除 volume、不执行破坏性 downgrade。

## 尚未配置的控制与文件

- 独立 API 测试
- destructive migration scan 脚本及测试
- secret scan 配置
- Phase 1D-F 真实同 digest Staging→Production 与 rollback 演练

## 需要用户手动完成的网页操作

1. GitHub：Public 仓库和 main Ruleset 已配置；`CI / Quality Gate` 是唯一 required status check。后续若评审 CODEOWNERS review、required approvals、secret scanning 或 Push Protection，不得把生产 Secret 放入 Actions。
2. Codex Cloud：PR #8 已创建和更新，最新 synchronize CI 五项全绿且 required `CI / Quality Gate` 通过；保持人工审查和合并边界，不得自动批准或合并。
3. Feishu：Phase 1D-E interactive-card 已验收；Phase 1D-F 通知和部署/rollback 审批消费仍须在合并后单独运行验收。
4. Production：Phase 1D-F PR 评审后，另行批准 0003、维护窗口、同 digest 演练和 rollback 责任人。

## 推荐实施顺序

1. Phase 1D-A：已验收 GitHub 变更治理和保护边界。
2. Phase 1D-B：已验收；PR 已具备可强制执行并验证过失败路径的单一汇总质量门禁。
3. Phase 1D-C：已验收 Codex Cloud 在相同治理和 CI 下完成的最小权限验证。
4. Phase 1D-D：已验收与 Production 隔离的 Staging 目标。
5. Phase 1D-E：已完成 Production interactive-card runtime acceptance。
6. Phase 1D-F：实现已进入 governed PR/CI/review，尚未部署或真实演练。

## 最小可执行的下一个任务

Phase 1D-E 已验收。Phase 1D-F 为 `implemented_pending_verification`，等待 PR 人工审查、合并、精确 merge-SHA CI、0003 migration/backup 评审和另行批准的同 digest Staging→Production/rollback 演练。不得自动批准、合并或部署，不得进入 Phase 2A。
