# 上午、中午、傍晚三时段生活流

## DailyLifePack

`DailyLifePack` 是一天的公共生活背景和三个内容 Slot，不是强制完整故事包，也不是发布时间表。

一个有效生活包必须具备：

- 明确的 `dayContext`。
- 三条分别可以独立观看的视频。
- 合理的地点范围、天气、服装、物品和共享情绪。
- 每条视频自己的 `clipKind` 和 `continuityMode`。
- continuation 所需的依赖和同背景 fallback。

它不需要统一愿望、跨三段因果链、傍晚解决问题或归家结尾。

## 三个 Slot 与排序

三个 Slot 只有内容时段和交付顺序差异，没有固定剧情职责：

| Slot | sortOrder | 文件名 |
| --- | ---: | --- |
| `morning` | 1 | `01-morning.mp4` |
| `noon` | 2 | `02-noon.mp4` |
| `evening` | 3 | `03-evening.mp4` |

任一 Slot 都可以使用 observation、micro_event 或符合依赖规则的 continuation。evening 可以继续留在旅行地、鱼塘、公园或奇境，不要求回家。

排序由系统根据 Slot 固定生成，内容作者不手工填写。主内容、content fallback 和重新渲染结果都继承所属 Slot 的排序。

## Clip 类型

### observation

纯生活观察，通常使用2至4个 beat：

- 建立地点。
- 角色进行活动。
- 发生自然互动。
- 停留在一个有陪伴感的画面。

不要求冲突、反转、结果或下一条钩子。

### micro_event

单条内部完成的小互动：

- 建立当前活动。
- 出现小发现或温和意外。
- 双主角产生可读反应。
- 在本条内完成结果。

不能把必要结果推迟到下一个 Slot。

### continuation

只在内容确实需要承接时使用：

- `continuityMode=follows_previous`。
- `dependsOnEpisodeIds` 至少包含一个排序更早的 Episode。
- 当前画面重新提供足够上下文，不能只依靠观看记忆。
- 必须配置不依赖前情的 `fallbackEpisodeId`。
- 只有依赖资产和当前内容将进入同一交付包时，主 continuation 才能被选择。

## Beat

每条8至15秒，默认约10秒，允许2至4个 beat：

- `establish`
- `activity`
- `interaction`
- `discovery`
- `reaction`
- `turn`
- `result`
- `linger`

beat 只描述可见动作、参与者、情绪、声音和时间段，不强制填写欲望、障碍或因果结果。时间段是 Seedance Prompt 中的语义节奏提示，不承诺精确到帧，也不等于多个视频生成任务。

## 生成与声音

- 默认由 Seedance 以 `single_pass` 一次生成完整 Episode。
- `multi_clip` 只作为重大场景跳转或连续失败后的特殊降级。
- 低风险 observation 和简单 micro_event 直接使用已批准人物、猫咪和画风参考。
- 精确开场、复杂互动或关键道具状态使用一张批准首帧；精确结尾使用批准首尾帧。
- 直接参考、首帧和首尾帧是互斥输入模式。
- 默认由 Seedance 原生生成环境声、动作音效和轻音乐。
- 不生成角色对白、旁白或歌词。
- 原生音视频通过媒体和内容 QC 后直接保存。
- 只有精确音效、固定品牌音、封装、编码或画面问题需要时才执行条件式后期。

## 连续性

所有视频强制保持：

- 人物、猫咪及绘本画风身份一致。
- 同一天服装版本一致。
- 天气变化合理。
- Episode 地点属于当天 `locationCluster`。
- 道具必须来自 `availableProps` 或已经交付生效的状态变化。
- 已获得、使用、丢失或带走的物品不能无解释恢复。
- 后续 Episode 不得引用本次交付中缺失的内容。

不强制保持：

- 三条发生在同一个具体地点。
- 三条围绕同一个任务。
- morning 引发 noon。
- evening 总结全天或回家。

## 示例

### 旅游生活流

- 第一条：海边看日出，observation。
- 第二条：市集接住滚落水果，micro_event。
- 第三条：坐缆车看城市灯光，observation。

三个 `dependsOnEpisodeIds` 均为空，只共享旅行背景。删除任一计划外依赖不会影响其他 Slot 的内容理解。

### 钓鱼部分承接

- 第一条：池边整理鱼具，observation。
- 第二条：成功钓鱼，micro_event，无需第一条前情。
- 第三条：带着鱼获离开，continuation，只依赖第二条。

如果第二条没有 ready 并进入本次交付，第三条改用“收好空鱼具、一起看夕阳”的 content fallback，不出现鱼获。fallback 仍然是 `sortOrder=3`。

## 双主角与重复控制

- 每条整体上必须让人物和猫咪都具有可读参与。
- 同一天允许不同角色分别主导不同活动。
- 不能把猫咪仅当道具或笑点来源。
- 相同具体活动、笑点或构图保持冷却。
- 旅行章节允许在同一地点集合内连续出现不同场景。
- 同一外部信号只能进入一个 Episode。
