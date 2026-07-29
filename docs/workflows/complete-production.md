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
  --source-asset-id <personCanonId> --role person

uv run cvg canon derive-crop `
  --source-asset-id <catCanonId> --role cat

# style必须人工选择确实不含人物/动物的像素区域
uv run cvg canon derive-crop `
  --source-asset-id <styleCanonId> --role style `
  --box 0,0,1024,360 --subject-free
```

裁剪只复制批准原图像素，不调用 Seedream，也不会创建新身份。

## 2. 四次独立导演调用

`cvg plan-day` 固定执行：

```text
Day Director Prompt → DayBrief
Morning Director Prompt(DayBrief) → Morning Episode
Noon Director Prompt(DayBrief + morning状态摘要) → Noon Episode
Evening Director Prompt(DayBrief + morning/noon状态摘要) → Evening Episode
```

不是用一个 Prompt 同时生成三条具体分镜。总导演只确定全天主题、背景、共享元素和
三个时段的叙事边界；每个时段导演只输出一个 `EpisodePlan`。每次输出和完整 Prompt
先写入 PostgreSQL，再调用下一阶段。

某个时段结构不合格时，已经完成的 DayBrief 和其他 Episode 不重做：

```powershell
uv run cvg replan-episode <runId> `
  --slot evening `
  --reason "车厢边界与猫咪位置描述冲突" `
  --allow-paid-generation
```

旧导演步骤、Prompt和媒体保持审计，新尝试使用更高 attempt。

## 3. 全天和单条契约

`DayBrief` 包含内容日期、全天主题、同一天背景、真正跨时段使用的共享元素，以及
morning/noon/evening 的叙事目的、场景方向、事件方向和外观意图。

每个 `EpisodePlan` 包含：

- 一个主事件和2～4个连续动作阶段；
- 8～15秒建议时长；
- 当前时段外观及相对前序变化原因；
- 共享元素在本时段的用途、初态和终态；
- 关键数量、支撑、包含、边界或交接关系的初态与终态；
- 可见结尾结果，不能用站立互看或静止画面填时长。

早中晚是同一天的三个生活窗口，不强制形成“出发→完成→归家”。连续性只传递真正
已经发生、且后续需要读取的外观、共享元素终态和关键关系终态。

## 4. 多模态输入与关键帧审核

Episode先生成一份`VideoInputPlan`，Prompt素材别名和Ark content数组共同读取它：

- `multimodal_reference`：人物、猫咪、画风及必要的元素、动作视频或氛围音频。
- `strict_first_frame`：只发送一张严格首帧。
- `strict_first_last`：只发送严格首帧和尾帧。

严格帧模式不能混入其他参考媒体。需要同时维持身份、画风和元素时使用多模态参考，
并把关键帧作为`reference_image`在Prompt中声明为语义开场或结尾。动作视频只负责
运动和运镜，不能覆盖主体身份；参考音频只负责氛围和节奏。

`KEYFRAME_REVIEW_MODE`：

- `technical_auto`（当前默认）：图片技术QC通过便继续，同时写入
  `semanticReviewStatus=skipped` 与 `autoApprovedUnverified=true`。
- `manual`：图片成为 candidate，步骤停在 `awaiting_review`；批准首帧后才生成
  尾帧，批准尾帧后才创建视频任务。

无论哪种模式，最终视频均停在 `content_review`，必须人工决定是否 ready。

## 5. 聚焦执行Prompt

完整导演上下文保存在数据库。简单视频使用一个聚焦自然段；复杂视频使用三部分：

```text
【整体设定与素材绑定】
【镜头顺序】
【质量、物理与声音】
```

素材使用`@图片N/@视频N/@音频N`和`<主体N>`绑定，不出现数据库ID。镜头使用
镜头1、镜头2等相对顺序，每个镜头只有一种运镜，不再生成绝对秒级时间码。
目标不超过1400中文字符；1401～1600警告，超过1600阻断收费调用，超过2000重新
规划或拆分。

## 6. Seedance、下载与QC

每个Episode由Seedance单次生成8～15秒完整视频和原生音频，动作阶段不是多个
供应商任务。总时长由API整数`duration`控制，Prompt只表达镜头顺序和相对节奏。

所有收费请求前先提交幂等 Step 与 Prompt。已有 task ID 只轮询和下载；
`submission_unknown` 冻结并人工对账，禁止重复 POST。

成功 URL 立即流式下载到同盘 `.part`，计算 SHA-256、fsync并原子改名。ffprobe
检查 MP4、H.264、AAC、9:16、480p/720p、8～15秒和音轨。通过时保留供应商原
MP4，不强制 FFmpeg 重编码。

## 7. 视频审核与交付

视频审核阻断明显换人、猫咪变种/消失、眼睛拓扑改变、数量错误、空间越界及本集
声明的关键物理错误。普通服装褶皱或轻微构图偏差记录为警告。

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
