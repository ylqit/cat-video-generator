# 每日三段视频本地生产与交付工作流

## 生产单位

每天生产一个 `DailyLifePack`：

- 统一的 `dayContext`。
- `morning` Episode，固定 `sortOrder=1`。
- `noon` Episode，固定 `sortOrder=2`。
- `evening` Episode，固定 `sortOrder=3`。
- continuation 引用的独立 content fallback。

Slot 表达内容顺序，不表示系统负责在某个时刻发布。当天背景和三个 Episode 必须在付费媒体生成前冻结。

## 阶段0：按需触发与内容日期

- `DailyLifePack.date` 表示视频内容属于哪一天，不是开始生成的预约时间。
- 已批准内容允许提前、当天或延后生成，不存在“前一晚20:00以后才可执行”的门槛。
- `cvg run-next` 领取日期最早的 approved/frozen 内容包。
- `cvg run-next --target-date YYYY-MM-DD` 只领取指定内容日期。
- 后续 `cvg run-pack <lifePackId>` 精确执行指定内容包，不依赖当前日期。
- `Asia/Shanghai` 用于内容日期默认值、日志和展示；不会决定是否允许创建任务。
- Windows PowerShell 可随时手动触发。Task Scheduler、cron 或 systemd 只是在需要自动化时调用相同命令。

内容包执行得早或晚都不会修改 `planRevision`。连续性读取内容日期、显式依赖和已经交付生效的状态，不读取墙上时间推断前置事件。

## 阶段1：规划输入

读取：

- Series、Character、Relationship 和 World Bible。
- 可选 LifeChapter 与已经交付生效的 ContinuityEvent。
- 最近30天的地点、活动、笑点、构图和模板指纹。
- 日期、天气和节日。
- 已审核且未过期的 TrendSignal。

输出三个文本级 DailyLifePack 候选，不调用图片或视频模型。运行实现 V1 先接受人工准备和批准的 DailyLifePack JSON，自动文本规划器不阻塞媒体链路落地。

## 阶段2：候选检查

按以下顺序检查：

1. 固定 Canon 与内容安全。
2. `dayContext` 是否清楚且不过度限制活动。
3. 三个地点是否属于 `locationCluster`。
4. 天气、服装和物品状态是否一致。
5. 每条视频能否独立理解。
6. 只有 continuation 才允许依赖其他 Episode。
7. 依赖是否只指向排序更早的 Episode。
8. continuation 是否具有同 Slot、同背景且不依赖前情的 fallback。
9. 双主角参与、重复冷却、原创转换和视觉可行性。

不检查强制的早中傍晚因果链。

## 阶段3：计划导入与冻结

导入器执行：

1. 使用 Draft 2020-12 校验 DailyLifePack。
2. 检查 morning、noon、evening 三个 Slot 齐全。
3. 生成固定 `sort_order=1/2/3`，内容作者不手工填写排序。
4. 将主 Episode 和 fallback 保存为同 Slot 下的不同 Variant。
5. 保存候选时不执行收费调用。
6. 人工批准后固定 `dayContext`、Canon 快照、EpisodeSpec 和 `planRevision`。
7. 渲染重试只增加 `renderRevision`，不得改写 `planRevision`。

## 阶段4：分镜与视觉输入决策

每条8至15秒，默认10秒，使用2至4个 beat 描述一次完整视频内的语义时间线。时间码用于约束事件顺序和节奏，不是精确到帧的剪辑时间轴。

EpisodeSpec 的 `visualControl` 显式保存：

- `requiresExactOpening`
- `requiresExactEnding`
- `complexSubjectInteraction`
- `criticalPropState`

编译 RenderPlan 时使用固定优先级：

1. `requiresExactEnding=true`：选择 `generated_first_last_frames`。
2. 其余任一视觉风险为 true：选择 `generated_first_frame`。
3. 四项均为 false：选择 `direct_references`。
4. 直接参考成片因人物、猫咪身份或构图漂移失败时，下一 `renderRevision` 升级为 `generated_first_frame`。

RenderPlan 保存 `visualInputMode`、固定人物和猫咪资产、1至7张画风资产、0至2张场景关键帧、决策原因、完整 Prompt、音频策略和交付规格。升级渲染模式不修改 EpisodeSpec 或 `planRevision`。

人物、猫咪和画风资产在进入每日生产前完成一次长期 Canon 审核。`direct_references` 不创建每日 Seedream 任务；另外两种模式生成场景关键帧，并在人物、猫咪、服装、道具和构图审核通过后才提交 Seedance。

## 阶段5：Seedance 任务

渲染层根据 RenderPlan 创建 Seedance 任务：

- 9:16、720p。
- 默认10秒，允许8至15秒。
- `generate_audio=true`。
- 禁止对白、旁白和歌词。
- 关闭供应商水印。

任务必须在请求前保存本地幂等记录：

```text
sha256(
  episodeId
  + ":" + renderRevision
  + ":" + jobType
  + ":" + clipIndex
  + ":" + normalizedInputHash
)
```

相同幂等键只恢复已有任务。创建请求已经发送但没有取得 task ID 时，标记 `submission_unknown` 并人工对账，不自动重复 POST。

供应商状态标准化为：

- `queued`
- `running`
- `succeeded`
- `failed`
- `expired`
- `cancelled`

## 阶段6：立即下载与原子保存

Seedance 返回成功后立即处理临时 URL：

1. 在目标文件同一磁盘创建 `.part`。
2. HTTPS 流式下载并限制连接与读取超时。
3. 边写入边计算 SHA-256。
4. 检查成功 HTTP 响应和非空文件。
5. 同步文件后运行 ffprobe。
6. 校验容器、编码、720×1280、8至15秒和音轨完整性。
7. 人工检查黑帧、异常静音、爆音、人物、猫咪、动作、构图、水印、UI、意外对白、歌词和明显音画错位。
8. 基础 QC 通过后进入 `content_review`；人工批准后才标记 Slot ready。

供应商 URL 不能写入交付 manifest，也不能当作长期视频地址。

## 阶段7：媒体验证与条件式封装

- 全部合格：`passthrough_ready`，原始 MP4 直接进入不可变资产库。
- 仅容器元数据问题：后续可用 remux，不重新编码。
- 编码、尺寸或比例问题：后续可用 transcode。
- 原生音频问题：后续可用 replace audio。
- 保留环境声并补充精确音效：后续可用 hybrid mix。
- 多片段降级：后续可用 concat。

FFmpeg 不是每条视频必经步骤。当前 V1 对不合格媒体先阻断并保留 QC 报告，不自动改写文件；上述修复策略属于后续条件式实现。

## 阶段8：依赖与 fallback 选择

- shared_context Slot 可以独立生成。
- continuation 等待依赖 Slot 进入终态。
- 如果所有依赖资产 ready 且将进入同一交付包，选择主 continuation。
- 如果依赖失败或缺失，激活该 Slot 的 content fallback。
- fallback 继承原 Slot 和 `sort_order`，不新增交付位置。
- fallback 不得引用缺失依赖产生的物品或事件。

钓鱼示例中，中午成功时第三条可以带鱼获离开；中午失败时第三条改为收拾空鱼具看夕阳，仍然是 `evening/sortOrder=3`。

## 阶段9：交付包

只有三个 Slot 都有最终 ready 资产时才构建正式交付：

```text
output/YYYY-MM-DD/{lifePackId}/delivery-r{deliveryRevision}/
  01-morning.mp4
  02-noon.mp4
  03-evening.mp4
  manifest.json
```

构建顺序：

1. 按 `sort_order ASC` 读取三个 Slot。
2. 在目标父目录创建 `.building-*`。
3. 同盘优先硬链接，跨盘复制不可变资产。
4. 写入有序 manifest。
5. 重新校验三个文件的 SHA-256。
6. 完整后将临时目录原子改名为 `delivery-rN`。
7. 数据库事务记录 delivery items 和 selected Variant。
8. 按1、2、3顺序应用 selected Variant 的 stateWrites。

如果目录已完成但数据库事务失败，恢复流程通过 manifest 哈希幂等补交记录，不重新生成媒体。

## 可选自动化

自动化不是每日生产的必经阶段。需要无人值守时，可由 Windows Task Scheduler、Linux cron/systemd 或其他运行器在任意时间调用 `cvg run-next`；需要指定某天时调用 `cvg run-next --target-date YYYY-MM-DD`。调度器不能直接改数据库状态，也不能绕过内容批准、SSL、Alembic revision 或收费任务幂等检查。

## 失败处理

| 失败点 | 处理 |
| --- | --- |
| 无可用外部信号 | 使用 LifeChapter 或常青主题 |
| 三个候选均失败 | 等待人工提供已批准 DailyLifePack |
| 场景关键帧身份失败 | 最多重生一次，仍失败则停止该 Slot |
| 直接参考成片身份或构图漂移 | 下一 renderRevision 升级为合成首帧模式 |
| Seedance 第一次视觉失败 | 相同 Episode 增加 renderRevision 后重试 |
| Seedance 第二次视觉失败 | 标记 Slot 失败，等待人工处理 |
| 原生音频缺少非关键音效 | 允许通过，不强制重生视频 |
| 意外人声、歌词、爆音或关键错位 | 替换或混合音轨；无法修复则失败 |
| 容器或编码不符合规格 | 条件式 remux 或 transcode |
| 独立 Slot 失败 | 其他独立 Slot 继续生成，但不构建完整日交付 |
| continuation 依赖失败 | 生成并选择自己的 content fallback |
| fallback 也失败 | Slot 失败，不修改连续状态 |
| 创建请求结果未知 | `submission_unknown`，人工对账，不重复 POST |

鉴权、欠费、内容安全、参数错误和模型未开通不得自动重试。

## 人工检查

- 人物发型、服装、身体比例与本体一致。
- 猫咪斑纹、脸部、耳朵、尾巴和体型一致。
- 地点属于当天范围，天气和服装合理。
- 道具来自当天背景或已经交付生效的状态。
- Episode 没有引用本次交付中缺失的事件。
- observation 没有被强行加入冲突或反转。
- 没有黑边、关闭按钮、页码、水印或第三方标识。
- 没有意外对白、旁白、歌词、爆音或明显音画错位。
- 未复制外部视频和评论表达。

## 运营指标

- 三个 Slot 的生成成功率和完整交付率。
- 人物、猫咪和画风 Canon 参考审核通过率。
- 直接参考直通率、场景关键帧触发率和身份漂移升级率。
- 场景关键帧审核通过率。
- Seedance 单次成片成功率和每条成功视频成本。
- 原始成片直通率与条件式音频修复率。
- continuation 依赖失败率和 fallback 使用率。
- 下载成功率、临时 URL 超时率和任务恢复率。
- 地点、活动、笑点和构图重复率。
