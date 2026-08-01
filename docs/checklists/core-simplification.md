# 核心收敛 Checklist

更新时间：2026-08-01。

## Batch 0：归档与基线

- [x] 记录旧工作流表数量和本地媒体路径。
- [x] 导出12个旧 Run、18个 Episode、76个 Step、78个 Prompt、55个媒体资产和43条审核。
- [x] 逐项验证55个媒体文件存在且 SHA-256 一致。
- [x] 生成归档 Manifest 与整体 SHA-256。
- [x] 保留11个最新已批准 Canon；不删除本地媒体。
- [x] 归档目录：`var/archive/core-20260801T081806Z`。
- [x] 归档整体 SHA-256：`911ba9959143a5779ac8157412c33a6108e75fb7f5e190353ee73ccc383e3c78`。

## Batch 1：领域契约

- [x] `EpisodePlan` 收敛为固定 slot + 单一 `EpisodeScript`。
- [x] 删除 Draft/Plan 平铺字段复制。
- [x] `VisibleWorld` 收敛为锚点、实体初态和完整前后状态转换。
- [x] 切镜状态由重放推导，不保存重复边界状态。
- [x] 删除 `CriticalRelation`、`SceneProp`、Segment 和 GenerationStrategy。
- [x] 未知实体、悬空、消失、容器断链和 before/after 矛盾仍为硬失败。
- [x] 复杂动作只形成诊断，不再使用固定物理次数预算。
- [x] `VideoInputPlan` 只保存模式、分辨率、时长和有序素材。

## Batch 2：应用与基础设施

- [x] 保留四次导演、按需关键帧、single-pass 视频、审核、重试和交付。
- [x] 删除分辨率对比、分段生成、拼接和旧归档运行时导入。
- [x] 下载、哈希、落盘和技术 QC 由 `VideoExecutionService` 统一拥有。
- [x] Provider 输出在 Ark Gateway 边界归一化。
- [x] `PlanningStore`、`ProductionStore`、`QueryStore` 按用例能力隔离。
- [x] CLI、API 与 Web 删除旧能力入口。
- [x] Keyframe 模式只允许 `semantic_auto | manual`。

## Batch 3：PostgreSQL

- [x] 正式库从 `0004_planning_review` 升到 `0005_episode_prompt_overrides`。
- [x] 使用归档 SHA 显式确认后精确清理非 Canon 业务记录。
- [x] 执行 `0006_core_simplification`。
- [x] StepKind 收敛为 director/image/video。
- [x] 增加正式 `operation_key` 与类型化 `input_snapshot_json`。
- [x] 去除 Run、Episode、Prompt 的重复/可推导关系字段。
- [x] 远程唯一临时 Schema 迁移测试通过并完成清理。
- [x] Doctor 显示正式数据库位于最新 Alembic head。

## Batch 4：配置、文档与清理

- [x] `.env.example` 只保留 Ark 标准 API 和安全占位符。
- [x] README、ADR、完整流程、Windows 手册、HTTP 文档同步 single-pass 核心链路。
- [x] 旧研究与真实测试记录明确作为历史证据。
- [x] 删除 `commands/`、`generation/` 中的旧运行时代码；缓存目录为 Git 忽略的瞬态文件。
- [x] 运行时为50个Python文件、8,779行；扣除8个包初始化文件后为42个实质模块。

## Batch 5：最终门禁

- [x] `uv run ruff check .`
- [x] `uv run pytest -q`：30项通过，远程标记项按预期跳过。
- [x] 远程 PostgreSQL 临时 Schema 测试：1项通过并清理 Schema。
- [x] `git diff --check`
- [x] `uv run cvg doctor`
- [x] `web/npm run build`
- [x] 正式数据库无旧 Run 残留，仅保留11个批准 Canon。
- [x] 正式库完成不调用 Ark 的 Run 创建、读取和精确删除烟测。
- [x] 本批次未创建真实 Seedream 或 Seedance 任务。
- [x] 已跟踪文件秘密模式扫描无结果。
