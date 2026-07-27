# 正式 PostgreSQL 运行验收记录

## 环境

- 验收日期：2026-07-27。
- 数据库：`vedio-appdb`。
- Schema：`cat_video`。
- PostgreSQL：16.13。
- Alembic：`0002_content_and_reviews`。
- 传输：明文，`sslInUse=false`。
- 授权：`CAT_VIDEO_ALLOW_INSECURE_RUNTIME=true`。

该明文方式是用户明确接受的临时架构债务。未来启用 TLS 时保持同一个
数据库与 Schema，只调整 `CAT_VIDEO_DB_SSLMODE` 并关闭明文开关。

## 已执行验收

```powershell
uv run cvg doctor --allow-insecure-readonly-smoke
uv run cvg db upgrade
uv run cvg db current
uv run cvg db validate-runtime
```

首次迁移创建 `0001_postgresql` 和 `0002_content_and_reviews`。第二次
`db upgrade` 为 no-op，证明迁移幂等。

两次运行验证均通过：

- 业务表与 `alembic_version` 齐全。
- 字段名称、类型和 nullable 匹配 SQLAlchemy metadata。
- 主键、外键、唯一约束、CHECK 和索引匹配。
- 事务回滚通过。
- morning/1排序约束通过。
- GenerationJob 幂等并发通过。
- `FOR UPDATE SKIP LOCKED` 通过。
- 交付事务原子性通过。
- 本次验证 UUID 数据清理通过。

稳定 Schema 指纹：

```text
c19272059a31a5e5d1555abbbff406b7118a02d622b63b38a67e1538e5ad05c4
```

## 已准备的正式烟测数据

已导入并批准：

- `person-v1`：固定人物三视图。
- `cat-v1`：固定灰白猫三视图。
- `storybook-pencil-v1`：裁剪后的彩铅绘本画风。
- `life-2026-07-24-seaside-travel`：三时段旅游 LifePack。

morning Episode 已调整为8秒、`observation`、`direct_references`。当前
LifePack 保持 `approved`，三个 Slot 保持 `planned`，数据库中没有
GenerationJob 或 MediaAsset。缺少 `ARK_API_KEY` 的付费命令已经验证会在
创建任何任务前失败。

补充 Key 后的唯一下一步：

```powershell
uv run cvg run-pack life-2026-07-24-seaside-travel `
  --slot morning `
  --allow-paid-generation
```

该命令先在正式 PostgreSQL 写入幂等任务意图，再调用一次真实 Seedance。
