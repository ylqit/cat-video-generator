# 正式 PostgreSQL 运行验收记录

## 环境

- 验收日期：2026-07-27。
- 数据库：`vedio-appdb`。
- Schema：`cat_video`。
- PostgreSQL：16.13。
- Alembic：`0003_slot_retry_events`。
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

首次迁移创建 `0001_postgresql` 和 `0002_content_and_reviews`；本次真实
烟测前新增 `0003_slot_retry_events`，用于审计供应商终态任务的人工恢复。
重复 `db upgrade` 为 no-op，证明迁移幂等。

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

当前 Schema 指纹：

```text
fa35a1bc9e9f800b1974bde927b7fa6d907235b3c11b5f5164012cf3ed32f847
```

## 已准备的正式烟测数据

已导入并批准：

- `person-v1`：固定人物三视图。
- `cat-v1`：固定灰白猫三视图。
- `storybook-pencil-v1`：裁剪后的彩铅绘本画风。
- `life-2026-07-24-seaside-travel`：三时段旅游 LifePack。

`planRevision=2` 的 morning Episode 为8秒、`observation`、
`generated_first_frame`。两张历史首帧均已批准；三次视频 Create 分别使用
Seedance 2.0-mini、被截断的1.5名称和用户确认的完整1.5名称，均由供应商
返回 `UnsupportedModel`，没有 task ID 或 MP4。数据库已保留 GenerationJob、
MediaAsset、审核与 SlotRetryEvent 历史，noon/evening 仍未生成。

当前不得直接重复运行付费命令。必须先在供应商控制台确认当前 Key 所属
账号的实际 Agent Plan 套餐与视频模型权益，然后执行带人工原因的恢复：

```powershell
uv run cvg retry-slot life-2026-07-24-seaside-travel `
  --slot morning `
  --reason "Provider video entitlement verified after UnsupportedModel"
```

恢复后才可再次显式执行 `run-pack --allow-paid-generation`。完整烟测明细
见[Agent Plan morning 真实烟测记录](../validation/agent-plan-morning-smoke-2026-07-27.md)。

随后新增并批准 `planRevision=3`，三条 Episode 均为10秒，访问模式切换为
标准 Ark。标准图片模型返回 `InvalidEndpointOrModel.NotFound`；系统复用
语义一致的历史批准首帧后，视频模型
`doubao-seedance-2-0-mini-260615` 返回 `ModelNotOpen`。当前仍无 MP4，
模型开通后已成功创建视频 task，但 task 立即以 `SetLimitExceeded`
终态失败；只读任务列表没有其他 queued/running 任务。当前仍无 MP4，
下一步是在同一 API Key 账号处理视频任务限额或配额。详见
[标准 Ark morning 真实烟测记录](../validation/standard-ark-morning-smoke-2026-07-27.md)。

限额解除后，revision 3 render revision 4 复用批准首帧并成功生成10.08秒
MP4；GenerationJob、Provider task ID、SHA-256、H.264/AAC媒体属性和
`qc_status=passed` 已写入正式 `cat_video`。视频当前保持
`review_status=pending`，等待操作者完成最终音画审核。
