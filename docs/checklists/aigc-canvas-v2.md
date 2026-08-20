# AIGC Canvas V2 实施与上线清单

## 已实现范围

- `/api/v2` 独立于现有 `/api/v1`，旧项目 ID 和旧生产路径保持兼容。
- `StoryBrief`、通用 `Subject`/Revision/Reference、三案 `StoryRevision`、八维评分、人工批准、场景、独立 `ShotBeat` 和主体状态均采用结构化领域模型。
- 第一次保存 V2 Brief 会把项目级 `canvas_v2_enabled` 置为 `true`；迁移后的旧项目默认保持关闭。
- 三案故事采用三次策划与三次独立评审。调用前先持久化 Prompt、输入快照和幂等键，结果保存策划与评审 Prompt ID。
- 未批准 StoryRevision 时，服务端拒绝分镜编译。分镜总时长由确定性分配器控制，不交给 LLM 自由决定。
- 分镜导演单独调用 LLM 生成动作、机位、对白和场景归属，再由确定性能力编译器把全部 Beat 限制在 Ark 的 8–15 秒范围并精确匹配总时长；每个 Beat 可回查该次分镜 Prompt。
- `prompt_records` 保存 system/user/final prompt、供应商请求、输入、响应、接受版本、差异、费用、耗时、哈希和重试链。
- `generation_attempts` 提供幂等记录、明确失败重试和 `submission_unknown` 禁止盲重试策略。
- `workflow_steps` 增加租约、心跳、重试时间和请求哈希；`DurableWorkflowQueue` 使用 `FOR UPDATE SKIP LOCKED` 领取任务。
- Vue Flow 画布提供类型化端口、自动布局、视口聚焦、显式保存状态、三案审批、Beat 编辑与统一 Prompt 详情抽屉。
- 上游 Brief、主体或 Beat 修改只标记相关下游结果 `stale`，不自动触发任何付费媒体调用。
- Alembic `0019_aigc_canvas_v2` 将旧原文、人物/猫视觉档案、ShotCard 和 Prompt 记录增量导入新模型；缺失审计字段写为 `legacy_unavailable`。
- Alembic `0020_universal_media_canvas` 新增持久业务节点/边/事件、媒体生成批次、视频重编配方/标注/参考、资产节点归属及三个项目级灰度开关，并提供完整 downgrade。
- 产品广告模板默认建立“产品/模特参考 → 4 候选批次 → 选中图片 → 图生视频 → 局部重编 → 审核 → 时间线”类型化骨架。
- `cvg-media-worker` 领取 `media:image:batch:*`、`video:edit-anchor:*` 与 `video:edit-recipe:*`。控制锚点、实际供应商输入和请求哈希均在调用前持久化。
- 视频重编支持 0.5–13 秒单区间、归一化矩形/画笔/箭头/文字/时间点标注、主体参考、直接/两阶段能力计划、供应商片段留存与 FFmpeg 原音轨回填。
- 技术 QC 不通过的供应商片段作为失败候选保留，不批准、不覆盖、不自动再次调用；批准的完整版本才通过 Review 节点进入 Timeline。

## 当前边界

- 故事策划和分镜导演仍由现有 HTTP 编排执行；媒体图片候选、控制锚点和视频重编已经由独立 PostgreSQL Worker 异步执行。
- Ark 仍是首期唯一真实媒体供应商；能力限制由 `provider_capabilities` 描述，未在页面中写死第二家供应商。
- SSE 由持久 `canvas_events` 投影节点、费用、Prompt、任务和资产状态，前端仍保留显式刷新回退。
- 画布边线持久化会执行服务端类型校验并创建明确领域绑定；坐标操作允许自动重基，业务连接冲突仍要求人工处理。

## 迁移前

1. 在 Ark、数据库和其他供应商侧吊销并轮换曾写入 Git 的凭据。
2. 备份当前 PostgreSQL 数据库和 `cat_video` schema。
3. 确认目标数据库启用受信任 TLS；项目按设计拒绝无法验证传输安全的离线迁移生成。
4. 在维护窗口执行 `uv run alembic upgrade head`。
5. 执行 `uv run cvg doctor`，确认 `alembicRevision` 与 `0020_universal_media_canvas` 一致。
6. 抽样核对一个旧项目：legacy Story、两个迁移主体、兼容 Beat、旧 Prompt 与素材均可查看。

当前开发环境数据库仍停留在 `0018_v5_shot_assistance`。本次实现没有擅自升级该外部数据库。

## 灰度与回滚

- 新建项目通过保存 Brief 显式开启 V2；旧项目默认继续走 V1。
- 先选择一个无付费媒体历史的项目验证故事与分镜，再逐项目灰度。
- 不要通过删除旧 Scene 来适配新故事；场景数量冲突会返回 409，要求复制项目或人工局部重排。
- 降级迁移会删除 V2 表和扩展字段。执行前必须导出 V2 数据；应用回滚优先关闭项目级 V2，而不是立即执行数据库 downgrade。

## 验证命令

```powershell
uv run ruff check .
uv run pytest -q
Set-Location web
npm run typecheck
npm test -- --run
npm run build
npm audit --json
```

稳定的视觉验收数据可通过以下命令运行；它不调用 Ark，也不写业务数据库：

```powershell
uv run python scripts/canvas_preview_server.py
Set-Location web
npm run dev -- --host 127.0.0.1 --port 4173
```

然后打开 `http://127.0.0.1:4173/canvas/project-demo`。
