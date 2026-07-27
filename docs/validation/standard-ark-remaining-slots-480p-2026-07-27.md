# Standard Ark 剩余时段480p生成记录

## 结论

`life-2026-07-24-seaside-travel` 的 `planRevision=3` 已完成 noon 和
evening 两条10秒视频的真实生成、立即下载与媒体QC。两条均使用标准 Ark
`/api/v3`、`doubao-seedance-2-0-mini-260615`、原生音频和480p请求。

本次不覆盖此前 morning 的720p资产。Ark 对两条竖屏480p任务实际返回
496×864，这是供应商的编码对齐尺寸；QC按“目标短边±16像素、9:16比例
容差”验收，原始MP4保持直通，没有转码。

## 结果

| 顺序 | Slot | 视觉输入 | Ark task ID | 本地媒体 | 属性 | 当前状态 |
|---:|---|---|---|---|---|---|
| 2 | noon | 审核后的首帧+尾帧 | `cgt-20260727182807-gwx4r` | `var/assets/generated/sha256/d9/d9a51c1a298a728368814a508e8241e1077d2a5f9f90260fd2da6c92f31d25b3.mp4` | 496×864、10.08秒、H.264/AAC、4,803,270 bytes | `content_review` |
| 3 | evening | 人物+猫咪+画风直接参考 | `cgt-20260727181543-vjdhc` | `var/assets/generated/sha256/48/48d17fd519fe4d4dccf1ae622f17d836976233e4337d8cbb086622ac10ef0fa8.mp4` | 496×864、10.08秒、H.264/AAC、4,076,247 bytes | `content_review` |

媒体资产ID：

- noon：`d6d43d5e-97f7-44b8-b693-b104a1c611c4`
- evening：`b5b13162-0335-4b1f-a3ee-09a7a6758979`

两条都已经通过容器、编码、尺寸、时长和音轨存在性检查。抽帧检查通过，
但仍保留在 `content_review`，等待操作者实际播放并确认原生音频不存在
对白、旁白、歌词、爆音或明显音画错位。

## noon关键帧

标准API不能使用 Agent Plan 的 `doubao-seedream-5.0-lite` 别名。首次请求
以 `InvalidEndpointOrModel.NotFound` 终止，未创建视频任务。配置修正为
`doubao-seedream-5-0-lite-260128` 后，通过审计事件将 noon 从
renderRevision 1推进到2，并生成：

- 首帧资产 `5887fe66-db65-4874-a6fa-e7d40ccd5abc`
- 尾帧资产 `051fa8d5-1e73-414d-8cc5-4726939221dc`

两帧均通过人物、灰白猫、市场、服装、纸袋和黄色水果状态检查后才创建
Seedance视频任务。

## 实际视频Prompt

以下内容是供应商请求前持久化在
`episode_variants.render_plan_json.videoPrompt` 和
`generation_jobs.request_snapshot_json.videoPrompt` 的最终文本，不是重新
整理的剧情摘要。

### noon

```text
生成一条连续的10秒竖屏生活流短视频。图1是已审核的合成首帧，图2是已审核的合成尾帧；生成自然连续、物理合理的中间过程。主题：在老城市场，一颗圆水果从纸袋滚出，人物和猫咪从两侧轻松接住它。。语义节奏如下，时间码只表示动作顺序与大致节奏，不要求硬切：[0.0-2.0秒]establish：人物提着市场纸袋看摊位，猫咪在脚边观察彩色果篮。；情绪为悠闲。保持人物、猫咪、服装、道具和画风连续。 [2.0-5.0秒]discovery：一颗圆黄色水果从纸袋底部滚出，沿着缓坡穿过两位主角之间。；情绪为意外。保持人物、猫咪、服装、道具和画风连续。 [5.0-8.0秒]reaction：猫咪从前方用尾巴挡住水果，人物从后方蹲下伸手托住。；情绪为默契。保持人物、猫咪、服装、道具和画风连续。 [8.0-10.0秒]result：人物把水果放回加固后的纸袋，猫咪得意地翘起尾巴继续逛市场。；情绪为轻松。保持人物、猫咪、服装、道具和画风连续。使用原生环境声和自然动作音效；无角色对白、无旁白、无歌词、无字幕。禁止：identity-drift、dialogue、hard-cliffhanger、ui-elements、copied-external-story、水印、UI、身份漂移、额外肢体。
```

### evening

```text
生成一条连续的10秒竖屏生活流短视频。输入图片按固定顺序：图1仅锁定人物身份，图2仅锁定猫咪身份，图3起仅锁定画风；图1和图2如为同一角色的三视图设定表，每张设定表只代表一个角色，绝不能生成分身；场景由文字建立，不照搬参考图背景。主题：人物和猫咪乘坐缆车越过山坡，一起看海滨城市灯光逐渐亮起的风景。。语义节奏如下，时间码只表示动作顺序与大致节奏，不要求硬切：[0.0-3.0秒]establish：红色小缆车离开站台，人物和猫咪坐在窗边，远处能看见海岸线。；情绪为期待。保持人物、猫咪、服装、道具和画风连续。 [3.0-7.0秒]interaction：猫咪抬起前爪贴在玻璃上追随沿路移动的灯光，人物用手指向远方并与它同步。；情绪为默契。保持人物、猫咪、服装、道具和画风连续。 [7.0-10.0秒]linger：缆车缓慢越过山坡，两位主角保持不动，安静看向亮起的城市。；情绪为满足和安静。保持人物、猫咪、服装、道具和画风连续。使用原生环境声和自然动作音效；无角色对白、无旁白、无歌词、无字幕。禁止：identity-drift、dialogue、forced-return-home、forced-resolution、ui-elements、水印、UI、身份漂移、额外肢体。
```

## Prompt查看与导出

代码提供不收费的查询入口：

```powershell
uv run cvg show-prompt life-2026-07-24-seaside-travel `
  --slot noon `
  --plan-revision 3
```

加 `--output <path>` 可将去密后的完整Prompt记录以UTF-8 JSON独占创建到
本地；如果目标已存在，命令拒绝覆盖。当前已导出：

```text
var/prompts/life-2026-07-24-seaside-travel/r3/02-noon.json
var/prompts/life-2026-07-24-seaside-travel/r3/03-evening.json
```

导出包含关键帧Prompt、视频Prompt、renderRevision、任务状态、task ID和
请求分辨率，不包含API Key、Base64参考图或签名下载URL。

## QC恢复

evening 初次QC因旧规则硬性要求短边正好480而被误判。供应商实际返回
496×864后，系统增加 `cvg recheck-media <assetId>`：

- 只重新运行本地ffprobe，不调用Ark。
- 从原GenerationJob请求快照读取目标时长与分辨率。
- 只允许恢复 `media_qc_failed` 的最终视频。
- 通过后将原资产恢复到 `content_review`，不创建新revision或重复收费。
