# ADR 0003: Local storage before Tencent COS

- Status: Accepted
- Date: 2026-08-05

## Context

第一版需要接收和保存用户媒体，但当前规模不需要立即引入云对象存储。长期运行又要求文件可迁移，不能让业务记录依赖宿主机绝对路径。

## Decision

第一版使用 `local_fs`。数据库只保存 `storage_backend` 和 `storage_key`，不保存不可迁移的绝对路径。存储访问通过适配层完成，后续可将对象迁移到 `tencent_cos` 并更新存储引用。

## Consequences

- 初期部署和调试更简单。
- 本地媒体必须纳入备份、容量、权限和路径穿越防护。
- 业务代码不能直接拼接宿主机路径。
- COS 迁移需要校验哈希、对象数量和引用一致性。

## Alternatives considered

- 第一版直接使用 Tencent COS：增加外部依赖和凭据管理复杂度。
- 数据库保存绝对路径：迁移和容器布局变化时易失效。
- 将二进制直接存 PostgreSQL：会放大数据库备份和性能负担。
