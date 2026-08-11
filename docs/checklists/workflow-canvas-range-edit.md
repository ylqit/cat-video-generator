# 顺序时段语义画布与视频区间重生成 Checklist

> 本清单是 `0012_minimal_director_contract` 之后的当前实施事实来源。
> PostgreSQL 仍是唯一工作流状态源；画布只投影语义节点，不创建第二套状态。
> Batch 0～10 完成并统一检查通过前，禁止真实 Ark 图片或视频调用。

## 已确认问答

- [x] Q：画布是否允许任意连接业务节点？  
  A：不允许，采用固定语义画布，节点与边由后端工作流投影。
- [x] Q：顺序单时段生产是否完整进入画布？  
  A：必须进入；当前时段显示实际节点，后续时段显示锁定占位节点。
- [x] Q：什么时候解锁下一个时段？  
  A：当前时段视频批准且 `AcceptedOutcome` 人工确认后。
- [x] Q：锁定节点是否提前创建数据库 Step？  
  A：不创建，只由查询层返回 `not_created` 投影。
- [x] Q：整条和局部视频能否重新生成？  
  A：都支持；整条生成新 attempt，局部生成新 sequence revision。
- [x] Q：局部区间如何选择？  
  A：时间轴拖拽，默认吸附镜头边界，可切换精确模式。
- [x] Q：区间替换后的音频如何处理？  
  A：保留原视频完整音轨。
- [x] Q：是否覆盖旧媒体？  
  A：不覆盖，所有候选采用非破坏性版本，审核通过后才切换正式资产。
- [x] Q：开发期间是否调用真实 Ark？  
  A：不调用；全部功能和统一检查完成后才执行真实验证。
- [x] Q：最终真实验证范围？  
  A：`guided_sequential` 钓鱼主题 Morning 单条，验证整条重生成和一次区间重生成。

## Batch 0：基线与边界

- [x] 保留当前 `codex/postgres-ark-runtime` 分支，不执行 reset。
- [x] 确认工作树实施前无未登记改动。
- [x] 确认数据库基线迁移为 `0012_minimal_director_contract`。
- [x] 冻结开发阶段真实 Ark 调用。
- [x] 登记新增公共类型、API、数据库迁移和Web依赖：`RenderOperation.EDIT`、
  `video_sequences`、regenerate/range-edit/select API、Vue Flow与Playwright。

## Batch 1：顺序模式语义节点

- [x] 为Graph投影增加稳定 `semanticNodeId`。
- [x] 分离 `availability` 与 `executionStatus`。
- [x] 返回锁定原因、解锁条件、下一动作和attempt历史。
- [x] 锁定节点不得创建 `workflow_steps` 记录。
- [x] `guided_sequential` 按结果卡顺序解锁。
- [x] `auto_day` 保持三个时段并行展示。

## Batch 2：数据库与非破坏性序列

- [x] 创建 `0013_workflow_canvas_video_sequences.py`，短Revision ID为
  `0013_canvas_video_sequences`（兼容Alembic 32字符版本列）。
- [x] 新增 `video_sequences`，不增加Clip明细表。
- [x] EDL JSON只保存有序来源区间和替换Step。
- [x] 初始视频、整条重生成和区间编辑均形成独立revision。
- [x] 未批准revision不得替换Episode正式视频。

## Batch 3：节点重执行

- [x] 新增节点级 `regenerate` 用例与API。
- [x] 成功节点重执行创建 `attempt+1`。
- [x] 原Prompt、Task ID、资产和审核永久保留。
- [x] retry、resume、reconcile与regenerate语义互斥。
- [x] 上游变化只标记下游stale，不删除历史。

## Batch 4：Seedance视频编辑

- [x] 增加 `RenderOperation.EDIT`。
- [x] 支持 `@视频1 + @图片1 + @图片2` 混合输入。
- [x] 编辑Prompt采用严格单点编辑句式。
- [x] 选区限制0.5～13秒且不得跨来源Clip。
- [x] 缺少可回查Ark HTTPS来源时收费前失败。
- [x] 不自动重试付费编辑任务。

## Batch 5：本地时间轴渲染

- [x] 提取边界帧和时间轴缩略图。
- [x] FFmpeg按EDL裁切并重组视频。
- [x] 最终视频保留原始完整音轨。
- [x] 输出规格、总时长和音画同步进入技术QC。
- [x] 新成片停在 `content_review`。

## Batch 6：固定语义画布

- [x] 引入Vue Flow并只读展示业务连线。
- [x] 使用阶段列和Morning/Noon/Evening泳道。
- [x] 后续时段展示锁定占位节点。
- [x] URL持久化run、stage、slot、node和sequence。
- [x] 轮询不覆盖视口、节点和未保存编辑内容。

## Batch 7：视频时间轴与版本比较

- [x] 视频节点选中后同屏展开底部时间轴。
- [x] 支持播放头、缩略图、镜头标记和双端选区。
- [x] 支持镜头吸附和精确模式。
- [x] 支持整条重生成与区间重生成。
- [x] 支持revision切换、对比、批准和回退。

## Batch 8：顺序结果卡联动

- [x] 视频revision批准后由当前正式视频诊断生成结果卡草稿。
- [x] 未确认结果卡允许随正式视频更新。
- [x] 已确认结果卡不可静默替换。
- [x] 撤销结果确认时正确锁定后续时段并标记stale。
- [x] 已成功的前序节点不因后续规划重新执行。

## Batch 9：清理与文档

- [x] 删除重复线性工作台入口和确认无调用的死代码。
- [x] 不新增只改名、格式化路径或转发参数的薄包装。
- [x] 更新API、ADR、完整工作流、Windows和Docker说明。
- [x] 静态检索确认旧入口和重复状态推导不存在。

## Batch 10：全部功能完成后的统一检查

- [x] `uv run ruff check .`
- [x] `uv run pytest -q`：65 passed，1个显式远程测试按预期跳过。
- [x] `CAT_VIDEO_POSTGRES_TEST_MODE=remote-schema uv run pytest -m postgres -q`：1 passed。
- [x] `npm --prefix web run build`
- [x] `npm --prefix web run test:e2e`：2 passed。
- [x] `git diff --check`（仅报告Windows行尾提示，无空白错误）。
- [x] `uv run cvg doctor`
- [x] Doctor确认正式Schema位于 `0013_canvas_video_sequences` 且ready=true。

## Batch 11：唯一一次真实验证

- [x] 确认Ark账户可用且无欠费阻断。
- [x] 创建钓鱼主题 `guided_sequential` Run：`8ee6d700-af9b-4a58-9819-fec046baefe7`。
- [x] 画布只解锁DayBrief与Morning路径。
- [x] 完成Morning导演、定妆图、开场锚点、语义审核和初始视频。
- [x] 对Morning视频执行一次整条重生成并保留为候选revision。
- [x] 对 `3.70s～7.40s` 稳定区间执行一次区间重生成。
- [x] 比较三个revision；批准无硬错误的初始版本，问题候选不覆盖正式视频。
- [x] 确认Morning结果卡后只解锁Noon Director。
- [x] 验证数据库只有Morning Episode，Noon和Evening未创建任何收费Step后停止。

### 真实验证问题与处置证据

- Day Director首个attempt把输入意图`adaptive`错误回显为输出档位；修复导演Prompt、规划元数据持久化和Day节点regenerate后，仅重做Day Director。
- Morning开场锚点前两次分别出现“人猫分处池塘两岸”和“鱼竿穿过猫脸/人物脚入水”；第三次锚点通过，旧attempt与审核证据完整保留。
- 整条重生成视频约7秒出现半透明叠影，约8秒猫咪抬前爪；该revision未批准。
- 区间编辑完成`@视频1 + 两张边界帧`生成、EDL替换、原音轨保留和QC，但选区外仍保留抬前爪问题，因此未批准。
- 恢复已落盘候选时曾触发`content_review -> media_qc`非法回退；恢复逻辑改为复用同Step不可变资产并重新进入诊断，不重复下载、不重复提交Ark。
- 时间轴尾边界抽帧曾落在媒体结束点；改为在末尾保留100ms内缩，并在诊断镜头不足时回退到计划镜头边界。
- 正式已批准视频现在决定主画布节点完成态；未批准重生成版本只显示在attempt/sequence历史中。

### 最终统一检查（真实验证后）

- [x] `uv run ruff check .`
- [x] `uv run pytest -q`：65 passed，1 skipped。
- [x] 远程PostgreSQL隔离Schema：1 passed。
- [x] Web production build通过。
- [x] Playwright E2E：2 passed。
- [x] `git diff --check`无空白错误。
- [x] `uv run cvg doctor`：ready=true，Schema=`0013_canvas_video_sequences`。

## 失败处理

- 自动化检查失败时只修复代码、迁移、测试或文档，不调用真实Ark。
- 真实验证失败时保留已成功收费节点，先记录Prompt、Task ID、媒体和时间点。
- 只修复通用原因，并仅对失败节点创建新attempt；不得重跑成功节点。
