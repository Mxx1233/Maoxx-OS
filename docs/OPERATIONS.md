# Maoxx OS 运维手册

所有命令默认在 `/opt/maoxx-os` 执行。示例不包含真实密码、Secret 或 Token。

## 工作目录与 Git 基线

```bash
pwd
git status --short --branch
git log --oneline --decorate -10
git tag -n
```

## 服务状态与启动

```bash
docker compose config --quiet
docker compose ps
docker compose up -d
docker compose logs --tail=200
```

仅在变更已获批准且具备回滚点时启动或重建服务。避免无必要的 `--force-recreate`。

## 健康检查

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/health/db
docker compose exec db sh -c \
  'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

环境变量应由受控环境加载；不要在命令历史或报告中展开密码。

## Alembic 检查

```bash
docker compose exec api alembic current
docker compose exec api alembic heads
docker compose exec api alembic check
```

上线前要求 current 与预期 revision 一致、只有预期 head，且 check 无漂移。

## PostgreSQL 备份

在受限权限的 `backups/` 中创建自定义格式备份：

```bash
backup_file="backups/maoxx_os_YYYYMMDD_HHMMSS.dump"
docker compose exec -T db sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  > "${backup_file}"
test -s "${backup_file}"
sha256sum "${backup_file}" > "${backup_file}.sha256"
sha256sum --check "${backup_file}.sha256"
```

不要提交备份。复制到服务器外的加密存储前，先确认访问权限和校验值。

## PostgreSQL 恢复验证

恢复应优先在隔离的临时数据库进行，不覆盖生产库：

```bash
docker compose exec -T db sh -c \
  'createdb -U "$POSTGRES_USER" maoxx_os_restore_verify'
docker compose exec -T db sh -c \
  'pg_restore -U "$POSTGRES_USER" -d maoxx_os_restore_verify --no-owner' \
  < "backups/APPROVED_BACKUP.dump"
docker compose exec -T db sh -c \
  'psql -U "$POSTGRES_USER" -d maoxx_os_restore_verify \
  -c "SELECT version_num FROM alembic_version;"'
```

验证表、约束和关键行数后，删除临时数据库仍属于破坏性操作，必须确认目标名称并取得用户批准。生产恢复必须先停止相关写入、记录事故状态，并获得单独批准。

## 日志

```bash
docker compose logs --tail=200 api
docker compose logs --tail=200 feishu-worker
```

报告日志前应脱敏；不得复制 Secret、Token、完整身份标识或用户私密内容。

## 容器重启

```bash
docker compose restart api
docker compose restart feishu-worker
```

数据库重启需评估连接和写入影响。重启后检查 `docker compose ps`、API 健康、数据库健康和 Worker 日志。

## Git 回滚

先检查并保存用户未提交的工作：

```bash
git status --short
git log --oneline --decorate -10
git diff
```

已发布提交优先使用 `git revert <commit>` 生成可审计的反向提交。不要使用 `git reset --hard`、强制推送或覆盖用户改动，除非用户明确批准且已有备份。数据库回滚与代码回滚分开评估，禁止自动执行破坏性 Alembic downgrade。

## 故障回滚

1. 停止继续发布和非必要写入，不擅自删除容器或 volume。
2. 记录 Git commit、镜像、Alembic revision、容器状态和脱敏错误证据。
3. 判断故障属于代码、配置、schema 还是外部服务。
4. 代码故障优先部署已验证版本或使用 `git revert`；配置故障恢复受控配置。
5. schema 故障不得盲目 downgrade。若已有新格式数据，优先前向修复；生产恢复必须获得批准。
6. 恢复后验证 Compose、API、数据库、Alembic、Worker 和关键数据行数。

严禁通过删除 Docker volume 处理故障。

## 阶段上线前检查清单

- [ ] 当前阶段与 `ROADMAP.md` 一致，未跨阶段。
- [ ] `git status` 清晰，已有可恢复检查点。
- [ ] 生产备份非空、SHA-256 有效，并完成临时恢复验证。
- [ ] migration 经人工审查，没有意外 `drop_*` 或修改旧 revision。
- [ ] 自动化测试和迁移测试通过。
- [ ] `alembic current`、`heads`、`check` 通过。
- [ ] Compose 配置、容器状态、API 和数据库健康检查通过。
- [ ] 飞书允许/拒绝路径和去重验证通过。
- [ ] 端口、日志、响应和 Git 中没有敏感信息。
- [ ] 回滚判据、负责人和操作步骤已确认。
- [ ] `PROJECT_STATUS.md` 和相关文档已更新。
