# 火山引擎 Agent Plan 与标准 Ark 媒体生成接入

## 职责

- Seedream：仅在视觉风险规则要求时生成或编辑场景首帧、尾帧。
- Seedance：根据已批准视觉输入和完整时间线 Prompt，一次生成8至15秒音视频。

Seedance 不作为生图模型，Seedream 不负责异步视频任务。

## 双访问模式

运行时使用同一个 Ark Python SDK，但必须显式选择一套完整的访问配置。
当前默认是标准按量 Ark：

- `ARK_ACCESS_MODE=standard`
- `ARK_AGENT_PLAN_TIER` 为空。
- `ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3`
- Seedream：`doubao-seedream-5.0-lite`
- Seedance：`doubao-seedance-2-0-mini-260615`

标准模式的 Model ID 必须已在当前账号开通，也可以替换成对应 Endpoint
ID。当前所有 Episode 默认10秒，仍允许内容显式使用8至15秒。
标准接口返回 `ModelNotOpen` 时表示当前 Key 所属账号尚未开通该模型；
该错误不可自动重试，必须先在控制台开通服务。

切换到 Agent Plan 时必须整组替换：

- `ARK_ACCESS_MODE=agent_plan`
- `ARK_AGENT_PLAN_TIER=large` 或 `max`。
- `ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/plan/v3`
- Seedream：`doubao-seedream-5.0-lite`
- Seedance：`doubao-seedance-2.0-mini`

Agent Plan 的视频模型按模型单独校验套餐：

- `doubao-seedance-2.0-mini`：Large或Max，作为产品默认值。
- `doubao-seedance-1.5-pro` 或
  `doubao-seedance-1.5-pro-即将下线`：Medium、Large或Max，仅在操作者
  显式选择时允许，不作为失败后的自动降级。

套餐不满足时必须在创建数据库任务和供应商任务前失败。代码不会根据一次
`UnsupportedModel` 自动猜测或切换模型，避免重复收费和把套餐问题误判为
模型效果问题。

`ARK_AGENT_PLAN_TIER` 是操作者声明，不是供应商权益查询结果。静态
`doctor` 只能确认 Mode、URL、模型和声明档位彼此兼容，会输出
`agentPlanTierVerification=declared_only` 和
`providerEntitlementVerification=not_performed`。首次真实请求返回
`UnsupportedModel` 时，应核对 Key 所属账号的实际套餐，不得通过修改本地
声明或猜测其他模型别名绕过。

两种模式不能混用 URL、模型标识或凭据。Agent Plan 专属 Key、标准 Ark
Key 与 Coding Plan Key 不是可互换配置；Key 类型无法靠静态格式可靠识别，
实际请求返回鉴权错误时立即终止且不自动重试。

两个 Base URL 都会先删除末尾 `/`，然后按上述精确值校验。切换访问模式
不会迁移 PostgreSQL，也不会移动已经下载的本地媒体，但新模式会进入任务
请求摘要与幂等哈希，不能复用另一模式的任务。

## 通用媒体配置

开发基线：

- 图片：2K PNG、关闭供应商水印。
- 视频：9:16、720p、默认10秒、最长15秒、启用原生音频、关闭供应商水印。
- 生成策略：默认 `single_pass`；`multi_clip` 仅作为特殊降级。
- 视觉输入：使用显式风险规则，低风险默认 `direct_references`，直接参考最多9张图片。
- 音频策略：默认 `native`，禁止角色对白、旁白和歌词。
- 媒体策略：默认 `validate_then_passthrough`，合格原始 MP4 直接落盘保存。

Agent Plan 模式只使用套餐公开支持的上述模型别名，不在该模式下进行普通
Ark 日期版本模型的 A/B。标准模式后续若要更换 Model ID 或 Endpoint ID，
仍需使用同一人物、猫咪和画风测试集独立验收。身份一致性优先于风格匹配。

## Seedream

接口：

- `POST /images/generations`
- Python SDK：`client.images.generate(...)`

输入原则：

- 人物本体、猫咪本体和风格参考作为独立参考图。
- Prompt 明确图一约束人物身份、图二约束猫咪身份、图三只约束风格与构图。
- 本地路径和 `file://` 不作为 API 输入；先转为可访问 URL 或 Base64 data URL。
- 风格参考不得覆盖发型、服装、猫咪斑纹和身体比例。

输出原则：

- 使用 `url` 响应时必须立即下载，不能长期保存 24 小时临时 URL。
- 下载后计算 SHA-256，原子写入长期存储。
- 当前正式文档未将 `seed` 和 `guidance_scale` 列为稳定身份控制能力，不在公共配置中暴露。

## Seedance

异步任务接口：

- 创建：`POST /contents/generations/tasks`
- 查询：`GET /contents/generations/tasks/{id}`
- 列表：`GET /contents/generations/tasks`
- 删除：`DELETE /contents/generations/tasks/{id}`
- Python SDK：`client.content_generation.tasks.create/get/list/delete`

当前 Agent Plan Seedance 2.0-mini 默认基线：

- 模型：`doubao-seedance-2.0-mini`。
- 时长：模型支持4至15秒；本产品只使用8至15秒。
- 交付：9:16、720p，默认10秒。
- 音频：请求显式设置 `generate_audio=true`，生成环境声、动作音效和可选轻音乐；Prompt 明确禁止对白、旁白和歌词。
- Seedance 2.0 不配置 `seed`、`frames`、`camera_fixed` 或 `service_tier`。

显式选择上述任一1.5名称时沿用相同的异步任务、首帧输入、原生音频、
下载、QC和幂等边界，但这只用于受控验证或迁移，不改变默认2.0-mini
配置。1.5-pro 被供应商返回 `UnsupportedModel` 时同样按权益错误终止，
不再回退到其他视频模型。

### 输入模式

以下三种模式互斥，渲染层必须根据 `RenderPlan.visualInputMode` 只选择一种：

- `direct_references`：按人物、猫咪、画风资产顺序提交3至9张 `role=reference_image`，不创建 Seedream 任务。
- `generated_first_frame`：只提交1张已批准场景关键帧，使用 `role=first_frame` 或省略 role。
- `generated_first_last_frames`：只提交2张已批准场景关键帧，依次使用 `role=first_frame` 和 `role=last_frame`。

直接参考的固定编号为图1人物、图2猫咪、图3及以后画风。Prompt 必须明确每张图片只负责自己的资产角色，场景依据文字建立，不照搬本体图背景。

生成场景关键帧时，人物本体、猫咪本体和画风资产作为 Seedream 输入与来源记录；提交 Seedance 时只发送场景首帧或首尾帧，不再混传为 `reference_image`。`reference_audio` 只作为多模态参考，不承诺原样复制为最终音轨；固定品牌音乐和必须精确落点的音效使用 `external` 或 `hybrid` 后期策略。

视觉输入由 EpisodeSpec 的显式风险字段确定：精确结尾优先首尾帧；精确开场、复杂主体互动或关键道具状态使用首帧；其余使用直接参考。直接参考成片因身份或构图漂移失败时，下一 `renderRevision` 升级为首帧模式，不改变 `planRevision`。

### 单次成片

- Episode 的2至4个 beat 被编译成一个完整 Prompt 中的语义时间段，不拆成等数量的视频任务。
- 时间码用于约束动作顺序和大致节奏，不作为精确到帧的剪辑指令。
- 默认一次任务生成完整8至15秒成片。
- 只有重大场景跳转、超过单次能力边界或单次生成连续失败时，才使用 `multi_clip`。
- 多片段降级任务使用独立 `clipIndex`，不得与主单次任务共用幂等键。

### 原生音频

- `native`：`generate_audio=true`，由 Seedance 生成环境声、动作音效和可选轻音乐。
- `external`：`generate_audio=false`，使用批准的外部音轨替换静音成片。
- `hybrid`：保留 Seedance 原生环境声，再叠加品牌音或必须精确卡点的音效。
- 原生音频提示属于生成意图，不保证像传统音轨时间线一样精确。
- 缺少非关键建议音效不强制重生视频；意外人声、歌词、爆音或关键音效严重错位不得进入交付包。

每个视频任务必须保存：

- 内部 `generationJobId`
- 火山任务 ID
- `episodeId`
- `slot`
- `planRevision`
- `renderRevision`
- 模型 ID 和规范化参数快照
- 人物、猫咪、画风和可选场景关键帧资产 ID 与哈希
- 视觉输入模式及决策原因
- 创建、轮询、完成和下载时间
- 成本和错误分类

请求摘要额外保存 `accessMode`、`providerProfile`，Agent Plan 还保存
`agentPlanTier`。`generation_jobs.provider` 分别记录
`volcengine-agent-plan` 或 `volcengine-ark-standard`。摘要和幂等输入不
保存 Base URL、API Key、完整 Base64 或签名下载 URL。

创建任务接口没有可依赖的客户端幂等字段。调用前必须先保存本地 `submitting` 记录；取得火山任务 ID 后立即提交数据库。如果 POST 已经发送但响应读取失败且没有 task ID，状态改为 `submission_unknown`，通过任务列表和时间窗口人工对账，不得盲目再次创建，以免重复计费。

供应商任务状态标准化为：

- `queued`
- `running`
- `succeeded`
- `failed`
- `expired`
- `cancelled`

任务记录只保留7天。成功响应中的 `video_url` 和可选 `last_frame_url` 只有24小时有效，必须立即下载、计算 SHA-256 并持久化，不能作为长期媒体地址。

## 重试策略

可自动重试：

- 已有 task ID 的查询网络错误。
- HTTP 429 中可等待恢复的 RPM、TPM、IPM 限流、`ServerOverloaded`、`RequestBurstTooFast` 和排队任务上限。
- HTTP 500 `InternalServiceError`。

使用带 jitter 的指数退避，且受每时段最多两次收费视频尝试限制。

不得自动重试：

- 内容安全拒绝。
- 参数错误。
- 鉴权失败。
- 账户欠费。
- 模型未开通或无权限。
- HTTP 429 中需要充值、降并发或修改配额的 `QuotaExceeded`、`SetLimitExceeded` 和 `InflightBatchsizeExceeded`。
- Create 请求发送后未取得 task ID 的网络错误；该情况必须标记 `submission_unknown` 并人工对账。

不能仅按 HTTP 状态判断是否重试，必须优先读取供应商 `error.code`。视觉合格但音频失败时优先替换或混合音轨，不默认重新执行收费视频生成。

## 媒体验证与条件式封装

每个成功任务先下载到本地临时文件并执行只读媒体检查：

- MP4 容器、H.264 视频、AAC 音频。
- 9:16、目标分辨率、8至15秒和文件大小。
- ffprobe 可读取音轨、时长与基础编码信息。
- 黑帧、水印、UI、爆音、异常静音、意外对白、旁白、歌词和明显音画错位由人工内容审核确认。

下载流程要求工作目录与资产目录位于同一卷：先写 `.part`，边下载边计算 SHA-256，并限制最大字节数，再原子进入内容寻址资产目录。随后运行 ffprobe；检查失败的资产保留 QC 报告但不能进入人工批准或交付，下载完成前不得把数据库状态标记为 ready。

全部通过并经人工批准时，原始 MP4 直接参与本地交付。以下是保留的条件式后期策略，当前 V1 尚未自动执行：

- 容器元数据问题：remux，不重新编码。
- 编码或尺寸问题：transcode。
- 原生音频问题：replace audio。
- 需要保留环境声并补精确音效：hybrid mix。
- 多片段降级：concat。

ffprobe 是每条视频的只读 QC 工具；FFmpeg 仅是条件式后期工具，不是每个 Episode 的必经步骤。有批准场景关键帧时可以复用为封面；直接参考模式不为封面强制生成图片或调用 FFmpeg。

## 本地交付边界

- 外部调用只有按需 Seedream 图片生成、Seedance Create/Get 和成功结果 URL 的 HTTPS 下载；`direct_references` 不调用 Seedream。
- List Tasks 仅用于 `submission_unknown` 对账；Delete/Cancel 只用于明确需要取消的排队任务。
- 当前不调用小程序接口、CDN/TOS 上传或公开视频发布 API。
- `video_url`、`last_frame_url`、API Key、完整 Base64 和机器绝对路径不得写入交付 manifest。
- 最终对外产物是本地 `delivery-rN` 目录中的三条 MP4 和 `manifest.json`。

## 安全与配置

- API Key 只从 `ARK_API_KEY` 环境变量读取。
- 不读取无前缀的 `API_KEY` 或 `BASE_URL`。
- `ARK_ACCESS_MODE`、Base URL、套餐和模型必须通过兼容性检查后才允许创建
  `GenerationJob`。
- Windows CLI 直接通过 API 接入，不依赖 Ark Helper 或 Agent Plan Skill。
- 日志不得打印 Key、完整 Base64 图片或带签名的临时 URL。
- 模型能力由代码内 registry 定义，配置文件不能伪造 `supportsStream`、参考图上限等能力。
- 内容 Schema 不包含供应商模型和参数；渲染层负责从 `RenderPlan` 映射到 Ark 请求。

## 官方资料

- Agent Plan 视觉模型 API 接入：https://www.volcengine.com/docs/82379/2375486?lang=zh
- Agent Plan 套餐与模型范围：https://www.volcengine.com/docs/82379/2366394?lang=zh
- Seedance 2.0能力说明：https://www.volcengine.com/activity/seedance2
- 图片生成 API：https://www.volcengine.com/docs/82379/1541523
- 模型列表：https://www.volcengine.com/docs/82379/1330310
- Seedream 指南：https://www.volcengine.com/docs/82379/1829186
- 错误码：https://www.volcengine.com/docs/82379/1299023
- Seedance 创建任务：https://www.volcengine.com/docs/82379/1520757
- Seedance 查询任务：https://www.volcengine.com/docs/82379/1521309
- Seedance 查询任务列表：https://www.volcengine.com/docs/82379/1521675
- Seedance 取消或删除任务：https://www.volcengine.com/docs/82379/1521720
- Seedance 2.0 提示词指南：https://www.volcengine.com/docs/82379/2222480
- Python SDK：https://github.com/volcengine/volcengine-python-sdk
