# 从总导演到早中晚三条视频的完整流程

## 1. Canon与单视图参考

长期 Canon 只锁定同一个人物的主要面貌、发型和体型，以及同一只灰白猫的主要
脸型、体型和斑纹。服装、鞋帽、配饰和背包服从剧情：室内可以换拖鞋，场景不需要
背包就不出现；同一连续场景必须一致，相邻场景发生变化时说明天气、地点或事件原因。

人物主要面貌、发型和体型保持可辨识为同一个人；猫咪保持同一只灰白猫的脸型、
体型和主要斑纹。眼睛服从参考素材的整体画风，不再把某一种眼睛拓扑作为通用硬门。
只有Canon明确声明为IP不可变特征时，才将对应细节加入本集约束。

三视图原图保留审计，但运行输入优先采用确定性单视图裁剪：

```powershell
uv run cvg canon derive-crop `
  --source-asset-id <personCanonId> --role person `
  --semantic-key person:front --view front

uv run cvg canon derive-crop `
  --source-asset-id <catCanonId> --role cat `
  --semantic-key cat:front --view front

# style必须人工选择确实不含人物/动物的像素区域
uv run cvg canon derive-crop `
  --source-asset-id <styleCanonId> --role style `
  --box 0,0,1024,360 --subject-free `
  --semantic-key style:line_texture
```

裁剪只复制批准原图像素，不调用 Seedream，也不会创建新身份。

## 2. L1：近期记忆、事件种子与四次导演调用

`cvg plan-day` 固定执行：

```text
最近6个已批准/已交付Run摘要 + 最多3个确定性事件种子
→ Day Director Prompt → DayBrief
Morning Director Prompt(DayBrief) → Morning Episode
Noon Director Prompt(DayBrief + morning状态摘要) → Noon Episode
Evening Director Prompt(DayBrief + morning/noon状态摘要) → Evening Episode
```

不是用一个 Prompt 同时生成三条具体分镜。总导演只确定全天主题、背景、共享元素和
三个时段的叙事边界；每个时段导演只输出一个 `EpisodePlan`。每次输出和完整 Prompt
先写入 PostgreSQL，再调用下一阶段。

长期记忆只读取已经批准或交付的内容；候选、未生成和审核失败的剧情不会成为“发生
过的事实”。摘要只包含主题、主事件、地点、关键道具和构图类型，并对近期主事件、
地点和关键道具执行冷却。

`content/events/*.yaml`只提供生活观察、微事件和局部承接的触发条件、适用时段、
天气标签及活动方向。系统使用`seriesProfileHash + contentDate + planningRevision`
确定性筛选最多三条供总导演参考；种子为空时允许原创，不会阻断规划。事件种子不
预写完整剧本，也不建立第二份数据库状态。

某个时段结构不合格时，已经完成的 DayBrief 和其他 Episode 不重做：

```powershell
# 初次规划尚未形成完整三个Episode时，恢复缺失时段。
uv run cvg resume-planning <runId> --allow-paid-generation

# Episode已经存在、需要按人工意见重写时使用。
uv run cvg replan-episode <runId> `
  --slot evening `
  --reason "车厢边界与猫咪位置描述冲突" `
  --allow-paid-generation
```

旧导演步骤、Prompt和媒体保持审计，新尝试使用更高 attempt。`resume-planning`
只恢复失败或缺失的时段；若DayBrief本身失败，则必须创建新的Run，不能在缺少全天
约束的情况下继续生成Episode。

时段导演候选存在明确状态矛盾时，系统会把原候选和精确错误完整反馈给同一时段
导演，并自动重写一次。第二次仍不自洽时Run进入`planning_review`，不创建图片或
视频任务。人工执行`replan-episode --reason ...`修复该时段后，Run回到`draft`；
再执行`resume-planning`只补齐尚未完成的时段。

时段导演验收还会使用正式Seedance Prompt编译内核做一次“无媒体绑定预览”。超过
注意力预算时与世界状态矛盾使用同一个、最多一次的导演修复循环，因此不会在关键帧
已经付费后才发现视频Prompt不可提交。

## 3. L2：视觉档案、单条契约与可见世界

`DayBrief` 包含内容日期、全天主题、同一天背景、真正跨时段使用的共享元素，以及
morning/noon/evening 的叙事目的、场景方向、事件方向和外观意图。

每个 `EpisodePlan` 包含：

- 一个主事件和2～4个连续动作阶段；
- 8～15秒建议时长；
- 当前时段外观及相对前序变化原因；
- 1～3个镜头及结构化`dominantView`；
- `VisibleWorldPlan`中的实体、锚点、动作转换和切镜边界；
- 可见结尾结果，不能用站立互看或静止画面填时长。

早中晚是同一天的三个生活窗口，不强制形成“出发→完成→归家”。连续性只传递真正
已经发生、且后续需要读取的外观、共享元素终态和关键关系终态。

`VisibleWorldPlan`回答“画面里究竟有什么、在哪里、由谁支撑、如何变化”：

- `sceneAnchors`：桌面、地面、椅子、长椅、石墩和架子等稳定承重位置。
- `trackedEntities`：实体数量、颜色/形状/材质、初始位置和
  `persist/enter/exit/consume/transform`生命周期。
- `actionTransitions`：真实执行主体、目标物、动作前后位置、支撑、接触和包含关系。
- `shotBoundaryStates`：切镜前后必须继承的实体、服装层和空间状态。

任何物体使用前必须已经存在或明确进入；持久实体不能凭空消失、复制或变类；非抛掷
物始终需要人体、家具或地面支撑；坐下前必须已有座面；食物消失必须是`consume`，
材质或形状变化必须是`transform`。系统按动作顺序重放位置、支撑、接触、包含和
生命周期，动作前状态必须与上一动作结果一致。

交互次数较多、坐下后再站起、多个手部动作或跨镜头物理变化不会判定剧情非法。
它们只生成`low/medium/high`渲染风险诊断；只要世界状态自洽，仍按默认
`single_pass`进入Seedance。

## 4. 精确语义资产选择

资产不再按`role=element`选择“全局最新”，而按作用域和语义键精确寻址：

```text
person:front / person:side / person:back
cat:front / cat:side / cat:back
style:line_texture / style:outdoor / style:indoor
element:<elementId> / scene:<sceneId>
```

系统根据第一个镜头的`dominantView`选择人物和猫咪视角，并加载一张基础线条裁片、
一张室内或户外裁片，以及本集明确声明的一个元素/场景组合资产。默认总计3～5张。
只有最新已批准版本可选；`legacy:*`、候选、拒绝及旧Run私有资产不会自动进入新Run。

实际`semantic_key`、Prompt别名、输入顺序和SHA-256共同写入Step请求摘要，保证
数据库记录、Prompt编号和Ark输入顺序完全一致。

## 5. L3：多模态输入与关键帧审核

Episode先生成一份`VideoInputPlan`，Prompt素材别名和Ark content数组共同读取它：

- `multimodal_reference`：人物、猫咪、画风及必要的元素、动作视频或氛围音频。
- `strict_first_frame`：只发送一张严格首帧。
- `strict_first_last`：只发送严格首帧和尾帧。

严格帧模式不能混入其他参考媒体。需要同时维持身份、画风和元素时使用多模态参考，
并把关键帧作为`reference_image`在Prompt中声明为语义开场或结尾。动作视频只负责
运动和运镜，不能覆盖主体身份；参考音频只负责氛围和节奏。

`KEYFRAME_REVIEW_MODE`：

- `semantic_auto`（当前默认）：先检查图片可读、9:16误差不超过1%、无黑边/UI；
  首尾帧还必须尺寸完全一致。随后由`VisualReviewGateway`检查同一中性儿童、
  同一灰白猫、二维绘本风格、可见世界状态和场景拓扑。全部通过且置信度不低于
  0.80时自动批准；明确错误拒绝；低置信或模型异常转人工。明确拒绝不可被人工
  改写成批准；若判定误杀，只能显式重试原图片Step并保留旧结论。
- `manual`：图片成为 candidate，步骤停在 `awaiting_review`；批准首帧后才生成
  尾帧，批准尾帧后才创建视频任务。
- `technical_auto`：仅供实验。真实生成还必须显式传入
  `--allow-unverified-keyframes`，并明确记录语义未验证。

无论哪种模式，最终视频均停在 `content_review`，必须人工决定是否 ready。

## 6. 聚焦执行Prompt

完整导演上下文保存在数据库，Seedance只接收本集执行信息：

```text
【输出、画风与素材绑定】
【人物、猫咪、外观和空间】
【顺序动作】
【可见世界状态与切镜连续性】
【原生声音和最多6项硬禁止】
```

素材使用`@图片N/@视频N/@音频N`绑定，不出现数据库ID。每个动作使用真实
`actorId`，严格帧模式不会输出未定义的`<主体N>`。每个实体只有一个`entityId`
和显示名称；每个物理关系只写一次。镜头使用相对顺序，每镜只有一种运镜，不生成
绝对秒级时间码。

目标700～1200中文字符；超过1400警告，超过1600阻断收费调用，不截断Prompt。
长期记忆、评分、数据库状态和审核阈值都不会进入Seedance执行上下文。

## 7. Seedance、下载与技术QC

每个Episode由Seedance单次生成8～15秒完整视频和原生音频，动作阶段不是多个
供应商任务。总时长由API整数`duration`控制，Prompt只表达镜头顺序和相对节奏。

所有收费请求前先提交幂等 Step 与 Prompt。已有 task ID 只轮询和下载；
`submission_unknown` 冻结并人工对账，禁止重复 POST。

`run-day`遇到`FAILED/EXPIRED/CANCELLED`只返回失败Step和下一动作，不会隐式产生
第二次收费。相同剧本重渲染使用：

```powershell
uv run cvg retry-step <stepId> `
  --reason "说明供应商失败或人工拒绝后的重做原因" `
  --allow-paid-generation
```

新attempt以`operationKey`区分首帧、尾帧、单次视频、两个Segment、分辨率对比和
本地拼接；旧Step、Prompt、task ID和错误永久保留。纯本地拼接/QC重试无需付费
许可，`submission_unknown`仍然不允许创建新attempt。

成功 URL 立即流式下载到同盘 `.part`，计算 SHA-256、fsync并原子改名。ffprobe
检查 MP4、H.264、AAC、9:16、480p/720p、8～15秒和音轨。通过时保留供应商原
MP4，不强制 FFmpeg 重编码。

`VIDEO_SEMANTIC_REVIEW_MODE=diagnostic`时，系统从成片均匀抽取8帧，检查身份、
二维画风、世界状态、空间拓扑和时序连续性。诊断仅保存采样时间、帧哈希和结论，
不会自动把视频推进到`ready`。

## 8. 条件式双片段备用路径

`single_pass`始终是默认路径。只有一条单次成片已经被人工拒绝，并且Episode明确定义
两个天然硬切、每段至少4秒、总长仍为8～15秒时，才可重新付费运行：

```powershell
uv run cvg run-day <runId> --slot evening `
  --allow-paid-generation --allow-multi-clip
```

每段独立保存Step、Prompt、Ark task ID、媒体和审核。需要连续空间时，第一段必须
先审核通过，再抽取真实尾帧作为第二段首帧；尾帧错误或物理状态未完成时停止。两个
片段都ready后，依次尝试stream copy、仅音频转码和完整转码。累计时长超过Episode
计划或15秒上限时在本地规范化并记录`durationTrimmed=true`；明显偏短、缺段或任一
片段未批准时不会生成成片。Finalize失败可创建新QC attempt，不锁死已经付费且批准的
片段。

`multi_clip`不是自动重试，也不能用来掩盖跨切镜的高风险物理过程。

## 9. 人工审核与交付

视频审核阻断明显换人、猫咪变种/消失、数量错误、空间越界及本集声明的关键物理
错误。眼睛随参考画风自然表现，不强制某一种拓扑；普通服装褶皱或轻微构图偏差
记录为警告。

三个 Episode 均 ready 后才构建：

```text
output/YYYY-MM-DD/{runId}/delivery-rN/
  01-morning.mp4
  02-noon.mp4
  03-evening.mp4
  manifest.json
```

交付目录先在 `.building-*` 完整生成并复核哈希，再原子改名；数据库随后一次事务
写入包与三个排序项。
