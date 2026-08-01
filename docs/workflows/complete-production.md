# 三时段视频完整生产流程

## 1. 生产对象

一次 `ProductionRun` 对应一个内容日期，固定包含三个 Episode：

| slot | sortOrder | 含义 |
| --- | ---: | --- |
| morning | 1 | 上午生活片段 |
| noon | 2 | 中午生活片段 |
| evening | 3 | 傍晚生活片段 |

生成时间不受内容日期限制。三条视频共享同一个持续存在的人物、灰白猫和当天背景，但不强制形成“出发—完成—归家”因果链。

## 2. L1：导演规划

`cvg plan-day` 先创建收费意图和 Prompt，再依次调用：

1. Day Director：全天主题、天气、地点范围、共享元素、三个时段边界和换装原因。
2. Morning Director：一个完整上午 EpisodeScript。
3. Noon Director：读取 DayBrief 与上午重放终态，生成中午脚本。
4. Evening Director：读取 DayBrief 与前两个时段摘要，生成傍晚脚本。

每个 Episode 只包含一个主事件、2～4个同目标动作阶段、1～3个镜头和一个自然结果。Director 直接输出 `EpisodeScript`，本地只注入固定 slot，避免 Draft 与正式 Plan 重复保存同名字段。

每个时段返回后立即检查：

- Pydantic 结构与三个 slot 排序。
- 中性人物、灰白猫和剧情换装原因。
- `VisibleWorld` 状态重放。
- Seedance 执行 Prompt 预算。
- 与近期已批准/交付内容的结构化冷却键。

明确矛盾最多自动要求导演完整重写一次；再次失败进入 `planning_review`。这一步只修复剧本，不调用 Seedream 或 Seedance。

## 3. L2：可见世界与素材

`VisibleWorld` 包含：

- anchors：桌面、地面、座椅、架子等稳定空间锚点。
- entities：人物、猫咪、服装层、关键道具和初始状态。
- actions.transitions：实体完整 before/after 状态与变化原因。

重放器从初始状态按动作顺序应用变化，自动得到终态与切镜继承。以下矛盾会阻断收费媒体任务：

- 未声明或已经离场的实体被再次使用。
- 非抛掷物没有人物、家具或地面支撑。
- 容器离场后仍包含物体。
- 无原因复制、消失、消耗或改变外观类别。
- 坐下前不存在可坐锚点。
- 动作 before 与当前重放状态不一致。

动作次数多、手部交互复杂或跨镜头只记为渲染风险，不作为固定创意预算。

素材通过 `semantic_key` 精确选择：人物、猫咪和画风由系统固定加入；Episode 中真正出现的共享元素通过实体的 `semanticKey` 关联已批准资产。不再按 role 获取“全局最新元素”。

## 4. L3：按需图片与 single-pass 视频

视觉准备根据 Episode 选择：

- `multimodal_reference`：直接使用身份、画风和必要元素。
- `strict_first_frame`：需要准确开场时生成并审核首帧。
- `strict_first_last`：起点与结果都必须明确时生成并审核首尾帧。

关键帧先过可读性、9:16、尺寸和黑边技术门，再按配置进行 `semantic_auto` 或人工审核。明确语义错误不会创建 Seedance Step；低置信转人工。

所有 Episode 最终都由 Seedance 单次生成 8～15 秒、9:16、原生音频视频。视频 Prompt 固定为：

```text
【输出、画风与素材绑定】
【人物、猫咪、外观和空间】
【顺序动作】
【可见世界状态与切镜连续性】
【原生声音和硬禁止】
```

规划理由、评分、数据库状态、审核阈值和绝对秒级时间码不会进入 Seedance Prompt。素材顺序、哈希、输入模式、分辨率和时长保存在类型化 Video 输入快照中。

## 5. L4：下载、QC、审核与交付

Ark 成功后立即下载临时 URL：

1. 同盘写入 `.part`。
2. 校验文件完整性并计算 SHA-256。
3. 原子移动到不可变资产目录。
4. ffprobe 检查容器、视频/音频轨、分辨率、比例、时长和可播放性。
5. 可选语义诊断给出人工审核证据。
6. Episode 进入 `content_review`。

QC 通过的供应商 MP4 直接保存，不经过默认 FFmpeg 拼接或转码。人工批准后三个 Episode 才能进入交付：

```text
output/YYYY-MM-DD/{runId}/delivery-rN/
  01-morning.mp4
  02-noon.mp4
  03-evening.mp4
  manifest.json
```

交付先在临时目录构建，校验三个哈希后原子改名；数据库交付包与条目在一个事务中提交。

## 6. 幂等与恢复

- 每个外部调用前先提交 WorkflowStep、Prompt 和收费意图。
- 幂等键含 Episode、StepKind、operationKey、attempt 与规范化输入哈希。
- 已有 task ID 时只轮询和下载。
- `submission_unknown` 不自动重复 POST。
- `run-day` 不会为终态失败隐式创建下一次收费 attempt。
- 相同剧本重渲染使用 `retry-step`；剧情改变使用 `replan-episode`。
- 所有旧 Step、Prompt、task ID、错误和资产永久保留以供审计。

## 7. 当前不包含的能力

- 分辨率自动对比实验。
- 分段视频生成与 FFmpeg 拼接。
- 旧版本 Run 导入或恢复执行。
- 自动批准最终视频或自动发布。
- 小程序、对象存储、CDN 和消息队列。
