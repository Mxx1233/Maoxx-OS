# Maoxx OS 架构

## 当前架构

```text
飞书
  -> Feishu Worker（WebSocket 事件接收）
  -> SQLAlchemy
  -> PostgreSQL

内部调用者
  -> FastAPI
  -> SQLAlchemy
  -> PostgreSQL
```

当前文件运行目录挂载为 Local Storage；文字链路已经运行，媒体链路尚未实现。

## 当前容器与网络

- `db`：PostgreSQL 16，仅连接 internal 网络，不发布宿主机端口。
- `api`：FastAPI，同时连接 internal/public 网络，仅发布 `127.0.0.1:8000`。
- `feishu-worker`：WebSocket Worker，同时连接 internal/public 网络，不发布宿主机端口。
- 命名卷保存 PostgreSQL 数据；宿主机 `storage/` 挂载给 API 作为本地文件目录。

## 目标架构

```text
飞书（主界面）
  -> Feishu Worker
  -> FastAPI / Service Layer
       -> PostgreSQL（事实数据源）
       -> Local Storage -> 后续 Tencent COS
       -> Model Gateway -> 外部模型 API
```

FastAPI 是后台接口，Service Layer 承载可复用业务规则，Worker 不应长期复制业务逻辑。模型网关位于业务服务层和外部模型 API 之间，统一处理供应商差异、结构化输出、超时、重试、降级和调用记录。

## 目标数据分层

1. **Raw Input Layer**：不可丢失地保存用户原始文字和事件元数据。
2. **Media Layer**：保存可迁移对象引用、MIME、大小、哈希及输入关联。
3. **Extraction Layer**：保存任务、候选记录、字段、模型来源和置信度。
4. **Confirmation Layer**：保存用户确认、修正或拒绝结果及审计信息。
5. **Business Domain Layer**：保存项目、健康、学习等正式业务事实。
6. **Analytics Layer**：基于正式事实生成有版本、可追溯的指标和报告。
7. **Knowledge Layer**：后期保存文档、分块、嵌入与引用，不替代业务事实。

AI 不能直接成为最终数据源。任何会改变正式业务状态的 AI 输出必须经过结构校验，并按风险要求进行人工确认。原始输入、AI 提取、人工确认和正式业务记录应分别保存并可追溯。

## 边界与原则

- 飞书是日常主界面；服务器用于系统维护。
- PostgreSQL 是事实数据源，文件存储只保存二进制对象。
- API 与 Worker 使用相同的权限和业务规则。
- 当前单用户实现不得以牺牲未来多用户隔离为代价。
- 外部系统、模型和存储均通过适配层接入，业务表不保存绝对文件路径或密钥。

## 关键数据流

### 飞书事件流

```text
im.message.receive_v1
  -> tenant / sender / chat authorization
  -> message_id idempotency check
  -> raw input persistence
  -> safe acknowledgement
```

授权校验已在阶段 1C 代码中实现，但真实白名单与新 Worker 尚未部署验收，不应被视为当前生产已生效能力。

### API 流

```text
localhost caller -> FastAPI -> validation / service rules -> SQLAlchemy -> PostgreSQL
```

### 文件流

```text
Feishu file metadata -> authorized download -> validation and hash
  -> local_fs object -> media_assets reference
  -> future verified copy to Tencent COS
  -> storage_backend/storage_key switch without changing business identity
```

文件流属于阶段 2A，当前尚未实现。

### AI、确认和正式记录流

```text
Raw Input + Media
  -> Extraction Job
  -> Model Gateway / mock extractor
  -> versioned extraction records and fields
  -> Feishu confirmation or correction
  -> validated Business Domain record
  -> Analytics
```

模型网关位于业务服务与外部 OpenAI-compatible provider 之间。任何重跑产生新提取记录，不覆盖历史；人工确认后才能进入要求确认的正式业务表。
