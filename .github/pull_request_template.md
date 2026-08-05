## Summary

<!-- What changes, why it is needed, and the intended outcome. -->

## Related issue

<!-- Use "Closes #..." when appropriate. -->

## Current phase

<!-- Name the roadmap phase/subphase and confirm it is approved. -->

## Change type

- [ ] Documentation / governance
- [ ] Bug fix
- [ ] Feature
- [ ] Refactor
- [ ] Infrastructure / operations
- [ ] Database / migration

## Files changed

<!-- List important files and explain non-obvious changes. -->

## Impact review

### Database impact

<!-- Tables, constraints, indexes, data, deletion policy, or "None". -->

### Migration impact

<!-- Revision, upgrade behavior, compatibility, or "None". -->

### Destructive operations

<!-- List every destructive operation and approval, or "None". -->

### API impact

<!-- Endpoints, schemas, authentication, compatibility, or "None". -->

### Feishu UX impact

<!-- Allow/reject/idempotency/reply/card behavior, or "None". -->

### Security/privacy impact

<!-- Identity, permissions, secrets, logging, sensitive data, or "None". -->

## Validation

### Test results

<!-- Commands and summarized results; never paste secrets or user content. -->

### Alembic status

<!-- current, heads, check, and migration review result. -->

### Docker status

<!-- compose config, service health, and relevant runtime checks. -->

### Backup and rollback

<!-- Backup evidence, rollback trigger, exact safe rollback path. -->

### Documentation updated

<!-- PROJECT_STATUS, ROADMAP, CHANGELOG, OPERATIONS, ADR as applicable. -->

## Risk level

- [ ] Low
- [ ] Medium
- [ ] High

<!-- Explain the rating and remaining risks. -->

## Author checklist

- [ ] 未直接修改已执行 migration
- [ ] 未包含未经批准的 `drop_table`
- [ ] 未包含未经批准的 `drop_column`
- [ ] 未包含未经批准的 `drop_constraint`
- [ ] 未包含未经批准的 destructive alter
- [ ] 未提交 `.env`、Token、Secret、备份或用户媒体
- [ ] `alembic check` 通过
- [ ] 自动化测试通过
- [ ] `docker compose config --quiet` 通过
- [ ] `PROJECT_STATUS`、`ROADMAP`、`CHANGELOG` 已按需更新

## Reviewer checklist

- [ ] Scope matches the approved phase and related issue
- [ ] Database and migration changes were human-reviewed
- [ ] No cross-user, authorization, privacy, or secret exposure regression
- [ ] API and Feishu behavior remain compatible and fail closed
- [ ] Tests cover success, rejection, idempotency, and failure paths as applicable
- [ ] Backup and rollback are proportionate and executable
- [ ] Documentation describes the actual code and runtime state
- [ ] No unapproved destructive operation or cross-phase work is present
