# 标准 Ark morning 真实烟测记录

## 结论

- 内容包：`life-2026-07-24-seaside-travel`，`planRevision=3`。
- 访问模式：`volcengine-ark-standard`。
- Base URL：`https://ark.cn-beijing.volces.com/api/v3`。
- morning 使用 `generated_first_frame`，目标时长10秒。
- 标准 Seedream 请求因图片模型不存在或未开通而失败。
- 系统随后按关键帧语义指纹复用历史批准首帧，没有再次付费生图。
- 标准 Seedance 请求到达正确模型，但供应商返回 `ModelNotOpen`。
- 模型开通后再次恢复，供应商成功创建 task ID，但任务立即以
  `SetLimitExceeded` 终态失败。
- 限额解除后再次恢复成功，已生成、下载并通过技术 QC 的10秒 MP4。
- 当前视频处于 `content_review`，尚未执行最终音画批准。

这与历史 Agent Plan 的 `UnsupportedModel` 不同。标准 Ark 已识别
`doubao-seedance-2-0-mini-260615`；模型开通和限额解除后，同一标准 API
配置成功完成异步任务、下载与 QC。

## 10秒内容 revision

revision 3 保留原 lifePackId、角色、猫咪、场景和剧情，只执行以下不可变
内容升级：

- `planRevision` 从2增加到3。
- morning 从8秒调整为10秒。
- morning 三个 beat 调整为0～2.5秒、2.5～6.5秒、6.5～10秒。
- noon 和 evening 继续保持10秒。

内容哈希：

```text
ae5e753152ae090ae3dad83992f6a23a96e7ebb1fc6e1d6403e7ef67b93adbfa
```

## 标准 Seedream 结果

- GenerationJob：`9a2ba317-ce39-49e9-89b6-91fe1b937bad`。
- render revision：1。
- 模型：`doubao-seedream-5.0-lite`。
- Provider：`volcengine-ark-standard`。
- 尝试次数：1。
- 状态：`failed`。
- 错误码：`InvalidEndpointOrModel.NotFound`。
- Provider task ID：无。

该图片错误没有触发视频请求。人工恢复后，系统对历史批准首帧执行语义
指纹比较；人物、猫咪、画风、地点、天气、服装、道具和首帧动作完全一致，
只有时长与 beat 时间码变化，因此安全复用首帧。若任一视觉语义字段变化，
必须生成并重新审核关键帧。

## 标准 Seedance 结果

- SlotRetryEvent：`e3ac1baf-4f1f-40db-96c6-8cb4bd33633c`。
- GenerationJob：`be50dd4a-508e-466c-aeb3-53740d667dbd`。
- render revision：2。
- 模型：`doubao-seedance-2-0-mini-260615`。
- Provider：`volcengine-ark-standard`。
- 请求时长：10,000毫秒。
- 尝试次数：1。
- 状态：`failed`。
- 错误码：`ModelNotOpen`。
- Provider task ID：无。
- 本地视频：无。

`ModelNotOpen` 属于账号模型开通状态错误，不是限流或临时服务端错误，
因此系统没有执行第二次 POST。

## 模型开通后的恢复结果

操作者确认模型开通生效后，系统直接恢复 revision 3，没有重新导入内容，
也没有调用 Seedream。render revision 3 继续复用语义一致的历史批准首帧。

- SlotRetryEvent：`1e8b93dc-2686-4bf7-895a-2fa4df5a5a46`。
- GenerationJob：`9d10908c-5729-4e66-ae2c-34ae6faf9069`。
- render revision：3。
- 模型：`doubao-seedance-2-0-mini-260615`。
- 请求时长：10,000毫秒。
- 尝试次数：1。
- Provider task ID：`cgt-20260727174626-lbmr8`。
- 供应商终态：`failed`。
- 错误码：`SetLimitExceeded`。
- 本地视频：无。

该结果证明模型开通已生效，因为标准 Ark 已成功创建任务。随后执行只读
任务列表检查：当前模型共返回1条任务，即上述失败任务；`queued` 和
`running` 均为0。因此不是本系统存在并发任务占用。公开资料未给出足以
支持自动重试的 `SetLimitExceeded` 定义，当前按账号任务限额或配额策略
阻断处理，不盲目创建新收费任务。

## 限额解除后的恢复成功

限额解除后直接恢复 revision 3，没有重新导入内容，也没有调用 Seedream：

- SlotRetryEvent：`ec87b170-602f-4cd5-a9ed-181599d77a00`。
- GenerationJob：`db3660e2-a19d-4016-afcb-101c82098433`。
- render revision：4。
- Provider task ID：`cgt-20260727175222-5dt4b`。
- Provider：`volcengine-ark-standard`。
- 模型：`doubao-seedance-2-0-mini-260615`。
- 尝试次数：1。
- 状态：`succeeded`。
- MediaAsset：`5ac0fd28-2f20-4dfa-99f6-cf1b0d3b51f0`。
- SHA-256：
  `4f14b5086b1805af0a0d2e74a9cc3fcc615784b58c039df593a3139fb3c54c2a`。
- 文件大小：7,158,919 bytes。
- 视频：H.264、720×1280。
- 音频：AAC、32 kHz、双声道。
- 时长：10,080毫秒，目标10,000毫秒。
- QC：`passed`，无失败项。
- 审核：`pending`。

任务从创建到供应商成功约144秒，成功 URL 返回后约1秒完成本地下载与
不可变落盘。数据库未保存签名 URL。

抽取每秒画面进行视觉检查：

- 全程只有同一女孩和同一只灰白猫，没有分身或额外角色。
- 发型、白色黄圆点上衣、蓝色短裤、灰白猫斑纹保持稳定。
- 海边日出、旅行背包、小相机、猫咪追逐沙滩光斑及结尾并肩坐下符合语义
  时间线。
- 保持绘本插画风格，没有变成真人实拍。
- 没有字幕、Logo、UI或供应商水印。

技术检查确认音轨存在、平均音量约-31.4 dB、峰值约-4.3 dB，没有削波
迹象。是否存在意外对白、旁白或歌词仍需操作者播放听审；因此不自动执行
内容批准。

## 最终审核与后续

播放本地 MP4，确认音频只有海浪、晨风、相机/动作声或轻音乐后执行：

```powershell
uv run cvg review 5ac0fd28-2f20-4dfa-99f6-cf1b0d3b51f0 `
  --approve `
  --reason "视觉身份、动作、画风和原生音频人工审核通过"
```

若发现意外对白、旁白、歌词或明显音画错位，应拒绝并记录具体原因，不自动
重新生成视频。morning 批准后只进入 `ready`；noon/evening 尚未生成，
不能提前构建三条交付包。

## 历史失败恢复命令

1. 在当前 API Key 所属账号的火山方舟控制台检查视频生成任务限额、并发
   配额和可用额度；必要时向方舟工单提供 task ID
   `cgt-20260727174626-lbmr8`。
2. 为未来需要新关键帧的内容开通标准 Ark 图片模型；如果控制台提供的
   Seedream Model ID 与当前配置不同，应把 `ARK_IMAGE_MODEL` 整体替换为
   已开通的 Model ID 或 Endpoint ID。
3. 限额问题确认解除后执行：

```powershell
uv run cvg retry-slot life-2026-07-24-seaside-travel `
  --slot morning `
  --reason "Standard Ark SetLimitExceeded quota issue confirmed resolved"

uv run cvg run-pack life-2026-07-24-seaside-travel `
  --slot morning `
  --allow-paid-generation
```

旧 Agent Plan 与标准 Ark Job、错误、关键帧和审核记录均不得删除或覆盖。
本记录不保存 API Key、Base64、签名 URL、数据库密码、账号编号或完整
连接串。
