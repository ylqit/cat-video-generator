# Agent Plan morning 真实烟测记录

## 结论

- 内容包：`life-2026-07-24-seaside-travel`，`planRevision=2`。
- 访问模式：`volcengine-agent-plan`。
- morning 使用 `generated_first_frame`，目标视频8秒。
- 两个 render revision 的 Seedream 首帧均生成成功并通过人工审核。
- Seedance 2.0-mini、一次被错误截断名称的 Seedance 1.5-pro，以及用户
  确认的完整名称 `doubao-seedance-1.5-pro-即将下线` 均被供应商以
  `UnsupportedModel` 拒绝；用户随后再次确认
  `doubao-seedance-1.5-pro` 并重试，结果相同。四次调用都没有返回 task
  ID，也没有进入轮询、下载或视频媒体 QC。
- 当前没有 morning MP4；Slot 保持 `failed`，不得直接重复提交。

官方套餐表显示 `doubao-seedance-2.0-mini` 只对 Agent Plan Large/Max
开放，完整名称 `doubao-seedance-1.5-pro-即将下线` 对
Medium/Large/Max 开放，而
`doubao-seedream-5.0-lite` 对所有档位开放。本次“图片成功、两个视频模型
都不支持”的组合说明本地 `ARK_AGENT_PLAN_TIER=large` 只是声明，当前 Key
所属账号的真实视频权益未被验证，或 Key 与已升级套餐不属于同一账号。
模型别名不应改成控制台展示文案或普通 Ark 日期版本。

## 首帧结果

- GenerationJob：`2d89cd9c-040d-4335-9ab0-eee3e5d44900`。
- 尝试次数：1。
- 状态：`succeeded`。
- MediaAsset：`f19616ad-92d0-4adc-920f-aa416f8cb17c`。
- SHA-256：
  `9613c2d8f6894db073fd2eaa234dad12ebfd777bdd3d88f2c6d1fc76ddcd54df`。
- 媒体属性：PNG、1600×2848、4,085,120 bytes。
- QC：`passed`。
- 审核：`approved`。

人工审核确认只有一个固定女孩和一只灰白猫，人物发型、服装、相机与背包
正确；没有出现画风图中的男孩、橘白猫、荷塘、木船、文字、UI或多视图
分身。场景为竖屏海边日出，可作为视频开场。

## Seedance 2.0-mini 调用结果

- GenerationJob：`c4bdc489-3a05-4266-a0c7-5c52ecdd2806`。
- 模型：`doubao-seedance-2.0-mini`。
- 尝试次数：1。
- 状态：`failed`。
- 错误码：`UnsupportedModel`。
- Provider task ID：无。
- 本地视频：无。

该错误属于套餐、Key归属或模型权益问题，不是限流或服务端临时错误，因此
没有执行第二次自动重试。

## 用户选择 Seedance 1.5-pro 后的重试

程序曾错误截断用户提供的完整模型名称为
`doubao-seedance-1.5-pro`。该次失败任务保持不变，
`retry-slot` 创建了审计事件
`9f67e6c0-10d8-41c5-bf48-9e0e74a5dd7d`，render revision 从1增加到2。

新修订产生并批准了新的首帧：

- GenerationJob：`599b7bc5-c102-49af-b391-88c9ee01dcae`。
- MediaAsset：`f2f564bd-676e-445a-a767-b9779343acd6`。
- SHA-256：
  `a8826ab7ebbd96aadb0d1d8f0d77abda94fa111f2b04adfc6e27511c42e9661b`。
- 媒体属性：PNG、1600×2848、4,076,839 bytes。
- QC：`passed`。
- 审核：`approved`。

随后实际提交了新模型的视频请求：

- GenerationJob：`2bb78e9d-2086-4ebc-8cdf-5e711d732312`。
- 模型：`doubao-seedance-1.5-pro`。
- 尝试次数：1。
- 状态：`failed`。
- 错误码：`UnsupportedModel`。
- Provider task ID：无。
- 本地视频：无。

该错误同样不可自动重试。系统没有发起第二次相同模型 POST。
由于这次使用了错误截断的名称，它不能用于判断完整名称的套餐权益。

## 完整1.5名称重试

收到用户确认后，程序不再修改名称，使用
`doubao-seedance-1.5-pro-即将下线` 原样进行静态校验和实际请求。为避免
重复生图，render revision 3 复用了 revision 2 已批准首帧；数据库中没有
创建第三个 `keyframe_first` Job。

- SlotRetryEvent：`727e4360-ca81-4f70-b2e6-4dde37b3d873`。
- GenerationJob：`98176232-e878-452b-9de9-c01f14afc576`。
- render revision：3。
- 模型：`doubao-seedance-1.5-pro-即将下线`。
- 尝试次数：1。
- 状态：`failed`。
- 错误码：`UnsupportedModel`。
- Provider task ID：无。
- 本地视频：无。

完整名称仍被当前 Key 拒绝，因此不能再把失败归因于名称截断。系统按不可
恢复错误停止，没有执行相同请求的第二次 POST。

## 再次确认1.5短名称后的重试

用户将本地配置再次调整为 `doubao-seedance-1.5-pro` 并明确要求继续。
程序创建 render revision 4，复用已有批准首帧，没有创建新的
`keyframe_first` Job。

- SlotRetryEvent：`23cdbb87-c758-4ff0-a251-0a78a2423a5b`。
- GenerationJob：`e58563f4-682d-4fb7-a8c0-060c9b98ad4b`。
- render revision：4。
- 模型：`doubao-seedance-1.5-pro`。
- 尝试次数：1。
- 状态：`failed`。
- 错误码：`UnsupportedModel`。
- Provider task ID：无。
- 本地视频：无。

该结果与 revision 2 的同名模型调用一致，说明当前 Key 与 Agent Plan
端点仍不接受该视频模型。系统没有继续重试。

## 恢复步骤

1. 在火山方舟控制台确认当前 API Key 所属账号、Agent Plan 套餐档位以及
   视频模型权益。不要只修改本地 `ARK_AGENT_PLAN_TIER` 声明。
2. 若继续使用 Agent Plan，优先恢复
   `doubao-seedance-2.0-mini`，并确认实际套餐为 Large或Max。若使用普通
   按量 Ark，必须整组切换到 `/api/v3`、普通 Ark Key 与已开通的模型或
   Endpoint ID。
3. 权益确认后才能再次执行非付费恢复：

```powershell
uv run cvg retry-slot life-2026-07-24-seaside-travel `
  --slot morning `
  --reason "Provider video entitlement verified after four UnsupportedModel responses"
```

4. 检查 Slot 已进入 `planned`、render revision 再增加1，然后显式运行
   一次付费 `run-pack`。旧 Job、批准首帧和错误记录不得删除或覆盖。

本记录不保存 API Key、Base64、签名 URL、数据库密码或完整连接串。
