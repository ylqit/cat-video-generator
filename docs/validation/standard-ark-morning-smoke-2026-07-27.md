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
- 当前没有 MP4、下载或视频 QC 结果。

这与历史 Agent Plan 的 `UnsupportedModel` 不同。标准 Ark 已识别
`doubao-seedance-2-0-mini-260615`，但当前 API Key 所属账号尚未开通该
模型服务。继续修改 Base URL 或模型字符串不能解决该问题。

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

## 恢复步骤

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
