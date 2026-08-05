# 故事板优先的三时段完整生产流程

## 1. 唯一生产链路

```text
Day Director
→ Morning / Noon / Evening Director
→ EpisodeScript + SceneContinuity
→ 日内定妆图（外观相同则复用）
→ 每条一次Seedream故事板组图
→ 故事板整组审核
→ Seedance single-pass
→ 技术QC与人工内容审核
→ 01 / 02 / 03本地交付
```

一次`ProductionRun`固定包含`morning=1`、`noon=2`、`evening=3`三个Episode。生成时间与内容日期解耦；三条共享持续角色和当天背景，但不强制组成“出发—完成—归家”的因果链。

## 2. 三层创作上下文

- `SeriesContext`：同一个中性儿童、同一只灰白猫、二维彩铅/蜡笔画风、长期性格和幽默表达。
- `DayBrief`：当天主题、天气、地点范围、时段边界、共享元素和换装理由。
- `EpisodeScript`：本时段一个主事件、2～4个动作、默认2～3个镜头、结尾和关键连续性。镜头优先采用“人物活动建立→猫咪独立反应/探索→人猫关系回报”，动作只服务于镜头叙事。

规划固定进行四次独立调用：总导演只生成`DayBrief`，随后三个时段导演各自生成一条完整脚本。剧情模式池按日期和系列档案稳定选择三个不同软先验；近期已交付模式优先冷却，池不足时允许回退或原创，绝不作为业务硬门。脚本不重复保存slot、渲染输入模式或供应商字段。

## 3. SceneContinuity与单一语义校验

`SceneContinuity`只追踪三类可见对象：

- 人物和猫咪。
- 被拿取、放置、包含、食用、变形或跨镜头延续的关键道具。
- 实际参与坐靠、承重或交互的桌面、椅子、长椅等锚点。

普通植物、屋檐、远山和装饰不进入状态账本。每个`ActionStage`只保存执行者、动作和可见结果；停步、转头、眨眼、蹲下、嗅闻和走动都是导演动作，不创建世界状态。

关键实体只声明`startState`、`endState`、`lifecycle`和稳定`formKey`。起终状态允许相同；只有`transform`允许物体类别发生变化。`anchor`只指向锚点，`held_by`只指向实体，`inside`可指向容器实体或长椅缝隙、柜格等锚点。生成前只硬阻断真正未知的Actor/目标、关键实体缺少起终位置、无原因出现消失或变类、共享逻辑实体键冲突及供应商输入不兼容。动作较多、坐下后站起、镜头切换、近期重复和Prompt较长均只作为诊断。

Pydantic只负责类型、顺序、唯一性和引用存在性；`EpisodeValidator`只检查轻量起终态语义，不执行逐动作状态重放，也不通过中文关键词猜测座椅。座位、服装和道具在真实画面中的连续性由整组故事板审核判断。

## 4. 日内定妆与故事板组图

人物Canon固定使用`person:headshot`和`person:fullbody`，猫咪保留三视图。每个Episode在故事板前确定本时段外观签名：Run内已有相同可见外观时复用已批准定妆图，否则创建一次`image:look`单图任务。定妆图审核只检查同一人物、二维画风和本时段服饰是否完整；它不会写回长期Canon。故事板参考按“大头照 + 已批准定妆图 + 猫咪视角 + 两张画风”确定性排序。

每个Episode只创建一个`image:storyboard` WorkflowStep，并调用一次Seedream：

```text
sequential_image_generation = auto
max_images = 3或4
```

- 2个动作：开场、主要变化、结尾，共3张。
- 3个动作：开场、两个进展、结尾，共4张。
- 4个动作：开场、动作1、动作2～3连续进展、动作4结尾，共4张。

返回内容必须是按顺序的独立9:16图片，不含文字、编号、边框或九宫格。每张面板独立保存SHA-256和`panelOrdinal`，但共用一个Step、Prompt和审核决定。

故事板面板由`ShotPlan`编译：2镜头生成3张（镜头1主要状态、镜头2进入、镜头2回报），3镜头生成4张（镜头1、镜头2、镜头3、最终回报）。每张写入景别、画面方向、主体位置和唯一运镜，不再把多个动作摘要机械拼成同一画面。故事板先检查数量、序号、可读取性、9:16比例、统一尺寸和黑边。随后一次语义请求检查整组身份、二维画风、镜头顺序与景别差异、猫咪独立反应、关系回报、关键道具、实际座位、服装连续性和结尾。角色数量、关键道具复制/消失/变类、动作错序和结尾未兑现属于硬失败；普通背景、植物、轻微姿势、构图或面貌差异只保存为警告。明确失败则整组原子拒绝；低置信转人工；少图、部分下载失败或未批准均不得创建视频任务。

## 5. Seedance单次成片

默认`storyboard_reference`按顺序传入3～4张批准面板，Prompt使用`@图片1`、`@图片2`等引用开场、进展与结尾。若`ending.visualCritical=true`，只把第一张和最后一张映射为`first_frame`与`last_frame`，中间面板只参与规划和审核。

人物、猫咪、画风和关键元素Canon只用于Seedream，不与故事板重复传入Seedance。执行Prompt只包含：

```text
【输出和画风】
【人物、猫咪与场景】
【按故事板顺序发生的动作】
【关键实体连续性】
【原生声音和少量禁止项】
```

本地不以任意字符数阻断或截断Prompt。系统只检查Prompt非空、素材别名与输入一致、顺序无冲突和模型支持；字符数与UTF-8字节数仅供Web查看。供应商若返回真实长度错误，步骤失败并保存原始Prompt。

## 6. 下载、审核与交付

Seedance成功后立即下载临时URL，经`.part`写入、SHA-256、原子落盘和ffprobe技术QC后进入`content_review`。最终视频必须人工批准，不会自动交付。

三个时段都ready后才能构建：

```text
output/YYYY-MM-DD/{runId}/delivery-rN/
  01-morning.mp4
  02-noon.mp4
  03-evening.mp4
  manifest.json
```

通过QC的供应商MP4直接保存，不使用固定分段或FFmpeg拼接。

## 7. 幂等、失败与当前边界

- 每次Ark调用前在同一个PostgreSQL短事务中提交WorkflowStep和完整Prompt；
  Prompt约束、正文或父子关系失败时两者一起回滚，不留下孤立Step。
- Prompt用途固定为`director / look / look_review / storyboard / storyboard_review / video / review`。
  故事板审核Prompt关联故事板生成Prompt，视频诊断Prompt关联视频生成Prompt。
- 相同输入复用相同故事板或视频Step，不重复收费。
- Seedance `submission_unknown`冻结并通过任务列表人工对账，已有Task ID只恢复查询。
- Seedream同步接口超时无法找回原任务；默认600秒等待后最多自动创建一次新attempt，旧未知attempt和潜在重复计费标记永久保留。
- 故事板失败只能通过`retry-step`显式新建attempt，旧组图与证据保留。
- JSON解析或必填字段缺失最多自动结构修复一次；已经成功解析但语义不合格时进入`planning_review`，不会自动产生第二次导演收费调用。
- 剧情或连续性需要修改时由用户显式使用`replan-episode`。
- 历史生成记录已按用户要求清理；新系统不承担旧契约恢复。
- 不包含multi-clip、分辨率对比、自动发布、小程序、对象存储或消息队列。

## 8. 统一Web生产工作台

后端`currentStage`只描述工作流真实阶段，不代表用户当前必须浏览的页面。
工作台把浏览定位保存到`/studio?run=<runId>&stage=<tab>&slot=<slot>&node=<nodeId>`：首次进入且没有`stage`时才使用后端阶段决定默认页签；轮询、保存设置和刷新Graph均不再覆盖用户选择。刷新、复制链接及浏览器前进后退会恢复同一Run、页签和节点抽屉。旧`/runs/:id`不再维护重复操作页面，只跳转到工作台。

固定五级视图为“总导演→三集导演→故事板→视频成片→审核交付”。每个节点可查看内容结果、实际调用Prompt、输入素材、Provider任务、审核证据和全部attempt。失败、过期、取消、继续查询、对账和审核动作由后端`availableActions`返回，前端不再复制状态机判断。

后台任务错误携带已知的`runId / episodeId / slot / operationKey`，页面使用持久
Alert展示并可定位到失败节点。故事板整组未批准时，视频按钮保持禁用并说明原因。
