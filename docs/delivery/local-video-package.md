# 本地视频交付包

## 交付定义

当前系统不提供小程序接口、HTTP 下载接口或公共媒体 URL。一次正式交付是一个不可变本地目录：

```text
output/YYYY-MM-DD/{lifePackId}/delivery-r{deliveryRevision}/
  01-morning.mp4
  02-noon.mp4
  03-evening.mp4
  manifest.json
```

该目录可以直接复制、压缩、传给其他程序或由未来的存储适配器读取。后续如何展示视频不属于当前生产系统。

## 排序规则

| sortOrder | slot | 文件 |
| ---: | --- | --- |
| 1 | `morning` | `01-morning.mp4` |
| 2 | `noon` | `02-noon.mp4` |
| 3 | `evening` | `03-evening.mp4` |

排序属于 DailySlot：

- 主 Episode、content fallback 和重试结果共享同一个 sortOrder。
- 物理媒体资产不决定排序。
- 所有查询显式 `ORDER BY sort_order ASC`。
- 不依赖 JSON 对象顺序、创建时间、文件时间或 ID。

数据库约束：

```sql
CHECK (sort_order BETWEEN 1 AND 3)

CHECK (
  (slot = 'morning' AND sort_order = 1) OR
  (slot = 'noon' AND sort_order = 2) OR
  (slot = 'evening' AND sort_order = 3)
)

UNIQUE (daily_life_pack_id, slot)
UNIQUE (daily_life_pack_id, sort_order)
```

这些约束由 PostgreSQL `cat_video.daily_slots` 和 Alembic 迁移直接执行；不是只存在于应用层的约定。

## Manifest

`manifest.json` 必须通过 `content/schemas/delivery-manifest.schema.json`：

- `videos` 恰好三项。
- 数组顺序固定为 morning、noon、evening。
- 每项保存 Episode、文件、哈希、媒体规格、render revision、选择的 Variant 和 QC 状态。
- 只保存相对路径，不保存机器绝对路径。
- 不保存 Ark API Key、Base64、供应商 task 响应或24小时临时 URL。

示例见 `content/examples/delivery-manifest.example.json`。

## 构建过程

1. 确认三个 DailySlot 均为 ready。
2. 为每个 Slot 选择一个最终 EpisodeVariant 和 MediaAsset。
3. 在正式目录的同一父目录创建 `.building-{uuid}`。
4. 同盘优先硬链接资产，跨盘复制。
5. 使用固定文件名写入三个视频。
6. 按 `sort_order ASC` 生成 manifest。
7. 重新计算交付文件 SHA-256，与 MediaAsset 记录比对。
8. 完整后原子改名为 `delivery-rN`。
9. 数据库事务记录 DeliveryPackage 和 DeliveryItem。
10. 按1、2、3顺序提交所选 Variant 的 stateWrites。

正式目录存在但数据库提交失败时，恢复流程读取 manifest 并核对哈希后补交数据库，不重新创建媒体任务。

## 完整性策略

V1 正式交付要求三条齐全：

- 任一独立 Slot 失败时，其他 Slot 可以继续生成和保存，但不生成正式日交付包。
- continuation 的依赖缺失时使用同 Slot content fallback。
- fallback 也失败时，该日保持未交付。
- 未选中的 Variant、失败媒体和旧 render revision 永远不会进入 manifest。

如果将来业务允许部分交付，应新增显式 `PartialDeliveryManifest` 版本，不能放宽 V1 Schema 后让下游猜测缺失项。

## 交付后的状态

- `ready` 只表示某个 Slot 已有通过 QC 的本地资产。
- `delivered` 表示完整目录已经原子生成。
- 只有 delivered 包内所选内容可以产生 ContinuityEvent。
- 交付目录和 revision 不覆盖；修正结果创建新的 `delivery-rN`。
