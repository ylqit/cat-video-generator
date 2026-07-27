# 真实 Ark 链路运行手册

本手册用于在 Windows PowerShell 中完成从 Canon 本体到本地 MP4
交付的真实链路。运行时代码只支持火山方舟 API，不提供 Mock Provider；
可显式选择 Agent Plan 或标准按量 Ark。没有有效凭据时可以完成安装、
Schema 校验和单元测试，但不会创建收费任务。

## 1. 付费烟测前的硬门槛

同时满足以下条件后才允许运行 `run-pack` 或 `run-next`：

1. PostgreSQL 使用 TLS/隧道；或当前明确使用
   `CAT_VIDEO_DB_SSLMODE=disable`、`CAT_VIDEO_DB_SCHEMA=cat_video` 和
   `CAT_VIDEO_ALLOW_INSECURE_RUNTIME=true` 的临时明文例外。
2. 数据库密码只保存在被 Git 忽略的 `.env`；已暴露密码建议尽快轮换。
3. `cvg db upgrade` 已将正式 `cat_video` Schema 升级到最新 Alembic head。
4. ffprobe 可以从 `PATH` 或 `FFPROBE_PATH` 找到；只有启用条件式媒体修复时才要求 ffmpeg。
5. 人物、猫咪和画风 Canon 均已导入并人工批准。
6. 默认标准 Ark 必须使用 `/api/v3`、空套餐字段和自身已开通的
   Model ID/Endpoint ID；切换 Agent Plan 时才使用套餐 URL 与模型别名。
7. `ARK_API_KEY` 只保存在被 Git 忽略的 `.env` 或当前 PowerShell 会话。
8. 每次可能创建任务的命令都显式带 `--allow-paid-generation`。

当前 `vedio-appdb.cat_video` 已按用户明确授权允许保存正式 LifePack、Ark task ID 和视频元数据。该权限只匹配固定数据库与固定 Schema，不会使其他明文数据库自动获得运行权限；诊断始终返回 `transportSecurity=plaintext` 和架构债务警告。

## 2. Windows 环境准备

```powershell
cd D:\soft\code\OpenGit\cat-video-generator
uv sync --extra test

$env:FFPROBE_PATH = "C:\path\to\ffprobe.exe"
# 可选：只有启用条件式修复时才配置
$env:FFMPEG_PATH = "C:\path\to\ffmpeg.exe"

uv run pytest -q
uv run cvg --help
```

本机已验证的媒体基线是 FFmpeg/ffprobe 8.1.2。`pytest` 会用测试专用 SDK 对象和 HTTP transport 检查请求映射、下载和 QC；这些 fake 不会出现在运行时配置，也不能被 CLI 选为供应商。

## 3. 数据库配置与迁移

复制 `.env.example` 为被 Git 忽略的 `.env`，写入真实值。PowerShell
环境变量仍具有更高优先级：

```dotenv
CAT_VIDEO_DB_HOST=<database-host>
CAT_VIDEO_DB_PORT=5432
CAT_VIDEO_DB_NAME=vedio-appdb
CAT_VIDEO_DB_USER=postgres
CAT_VIDEO_DB_PASSWORD=<password>
CAT_VIDEO_DB_SSLMODE=disable
CAT_VIDEO_DB_SCHEMA=cat_video
CAT_VIDEO_ALLOW_INSECURE_RUNTIME=true
```

```powershell
uv run cvg db upgrade
uv run cvg db current
uv run cvg db validate-runtime
```

当前实际验收结果为 PostgreSQL 16.13、`sslInUse=false`、
`0003_slot_retry_events`，表结构、事务回滚、Slot约束、幂等、
`SKIP LOCKED` 和交付原子性全部通过。`validate-runtime` 只清理自身
UUID 标记的数据，保留正式表和既有业务记录。

未来启用 SSL 时将 `CAT_VIDEO_DB_SSLMODE` 改为 `require` 或验证模式，
删除/关闭 `CAT_VIDEO_ALLOW_INSECURE_RUNTIME`；无需迁移 Schema 和数据。

## 4. 导入并审核固定本体

业务示例使用的版本 ID 是 `person-v1`、`cat-v1` 和 `storybook-pencil-v1`，因此 Canon 的 `asset-id` 使用相同值：

```powershell
uv run cvg canon import --role person --asset-id person-v1 --file "主题示例\人物本体.png"
uv run cvg canon import --role cat --asset-id cat-v1 --file "主题示例\猫咪本体.png"
uv run cvg canon import --role style --asset-id storybook-pencil-v1 --file "画风示例\猫咪+人+划船.png" --crop-box "0,300,1206,2400"

uv run cvg review person-v1 --approve --reason "固定人物本体人工确认"
uv run cvg review cat-v1 --approve --reason "固定猫咪本体人工确认"
uv run cvg review storybook-pencil-v1 --approve --reason "绘本画风人工确认"
```

Canon 文件按 SHA-256 保存到本地不可变资产目录。画风示例是带顶部关闭按钮、页码和上下黑边的截图，因此示例命令用 `--crop-box` 在导入时生成干净的不可变 PNG；不要把带 UI 的原始截图直接批准为正式画风资产。低风险 Episode 会把三张批准图片按“人物、猫咪、画风”的固定顺序直接发送给 Seedance；高风险 Episode 才先调用 Seedream 合成场景关键帧。

当前人物和猫咪本体图均是正/侧/背三视图设定表。Prompt 编译器会明确告诉模型“每张设定表只代表一个角色，不能把三个视角生成成三个分身”。首次真实烟测仍需重点人工检查是否出现角色复制；如果发生，拒绝原因为 `identity_drift`，下一 render revision 会升级到合成首帧路径。

## 5. 导入并批准内容包

```powershell
uv run cvg validate-pack content\examples\daily-life-pack.travel.example.json
uv run cvg import-pack content\examples\daily-life-pack.travel.example.json
uv run cvg approve-pack life-2026-07-24-seaside-travel
uv run cvg status life-2026-07-24-seaside-travel
```

`validate-pack` 不连接模型也不写数据库。`approve-pack` 只冻结内容，不产生费用。
当前旅游示例是 `planRevision=3`：三条均为10秒，morning 强制合成首帧、
noon 强制首尾帧、evening 保留直接主体参考。数据库中的 revision 1和2
保持不可变；烟测必须使用
显式 `run-pack`，不要用 `run-next` 领取旧 revision。

## 6. 配置标准 Ark 并执行真实烟测

默认配置使用标准 Ark API。Windows CLI 直接调用 `/api/v3`：

```powershell
$env:ARK_API_KEY = "<your-standard-ark-api-key>"
$env:ARK_ACCESS_MODE = "standard"
Remove-Item Env:ARK_AGENT_PLAN_TIER -ErrorAction SilentlyContinue
$env:ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
$env:ARK_IMAGE_MODEL = "doubao-seedream-5.0-lite"
$env:ARK_VIDEO_MODEL = "doubao-seedance-2-0-mini-260615"

uv run cvg doctor
uv run cvg run-pack life-2026-07-24-seaside-travel --slot morning --allow-paid-generation
```

`doctor` 必须显示 `arkAccessMode=standard`、
`agentPlanTier=null`、`endpointProfile=standard` 和
`generationConfigurationValid=true`。报告不会显示 Key；真正请求时若
模型未开通、Key 类型不匹配或余额不足，系统立即终止且不自动重试。

如果以后切换回 Agent Plan，必须一次替换整组 Mode、Base URL、模型和
Key：

```powershell
$env:ARK_ACCESS_MODE = "agent_plan"
$env:ARK_AGENT_PLAN_TIER = "large"
$env:ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/plan/v3"
$env:ARK_IMAGE_MODEL = "doubao-seedream-5.0-lite"
$env:ARK_VIDEO_MODEL = "doubao-seedance-2.0-mini"
$env:ARK_API_KEY = "<your-agent-plan-api-key>"
```

Agent Plan Key、标准 Ark Key 与 Coding Plan Key 不可混用。配置层无法通过
Key 字符串判断类型；供应商鉴权失败时终止且不重试。切换模式不会迁移
PostgreSQL 或本地媒体。

当前10秒 smoke Episode 要求精确开场，所以先走
`generated_first_frame`：

1. 数据库先写入唯一 `GenerationJob(submitting)`。
2. Seedream 使用人物、猫咪和画风三张 Canon 生成一张海边合成首帧。
3. 首帧下载、QC 后进入 `keyframe_review`；未批准前不得创建 Seedance 任务。
4. 批准首帧并再次运行命令后，Seedance Create 只发送该首帧，使用配置的分辨率（当前默认480p）、
   9:16、10秒、`generate_audio=true`、`watermark=false`。
5. 请求不发送 `frames`、`camera_fixed`、`service_tier` 或 `seed`。
6. 获得 task ID 后短事务写入 `queued`，随后轮询 `queued/running/succeeded`。
7. 成功 URL 立即流式下载到 `.part`，计算 SHA-256 后原子进入
   `var/assets/generated/sha256/`。
8. ffprobe 检查 MP4、H.264、AAC、720×1280、目标10秒（允许1秒误差）和
   音轨；通过后进入
   `content_review`，不会自动交付。

执行后查看待审核资产：

```powershell
uv run cvg status life-2026-07-24-seaside-travel
uv run cvg review <video-asset-uuid> --approve --reason "人物猫咪身份、动作、构图、声音均通过"
```

如出现人物或猫咪身份漂移，拒绝原因中明确写 `identity_drift`；系统只增加 `renderRevision`，下一次显式运行会升级为合成首帧，不修改 Episode 剧本或 `planRevision`。

## 7. 按需关键帧与三时段

```powershell
uv run cvg run-pack life-2026-07-24-seaside-travel --allow-paid-generation
```

- `direct_references`：直接创建 Seedance 任务。
- `generated_first_frame`：Seedream 生成1张首帧，先停在 `keyframe_review`；批准后再次运行才创建 Seedance。
- `generated_first_last_frames`：Seedream 生成首尾2张图，两张都批准后才创建 Seedance。
- continuation 的依赖仍在审核时，后续 Slot 保持等待；依赖明确失败或缺失后才选择 fallback。
- shared_context 的某个 Slot 失败不会阻止其他独立 Slot 尝试。

审核关键帧和视频都使用同一个接口：

```powershell
uv run cvg review <asset-uuid> --approve --reason "人工检查通过"
uv run cvg status <lifePackId>
```

## 8. 恢复与防重复收费

```powershell
uv run cvg resume <lifePackId> --allow-paid-generation
```

`resume` 只处理已经存在的 keyframe/video job、轮询、下载和 QC。它不会为 `planned` Slot 创建新收费任务，也不会重新 POST `submission_unknown`。幂等键由 `episodeId + renderRevision + jobType + clipIndex + normalizedInputHash` 派生；规范化输入包含访问模式和供应商 profile，因此 Agent Plan 与标准 Ark 不会错误复用任务。

只有 `failed/expired/cancelled` 供应商终态允许人工创建新 render revision：

```powershell
uv run cvg retry-slot <lifePackId> --slot morning `
  --reason "operator confirmed terminal provider failure"
```

该命令不调用 Ark，也不需要付费开关。它保留旧 Job、资产与错误记录，在
`slot_retry_events` 写入原/新 revision、终态 Job 和人工原因，然后把
Slot/Variant 恢复到 `planned`。`submission_unknown` 必须继续使用
`reconcile-job`，不能通过 `retry-slot` 绕过。

若 Create 请求已经发送但没有拿到 task ID，状态变为 `submission_unknown`。此时必须在 Ark 控制台或任务列表人工对账，不能直接重跑付费 POST。

先列出当前模型最近100个任务的安全候选元数据；该操作不产生新的付费任务，也不会把临时下载 URL 写入终端或数据库：

```powershell
uv run cvg reconcile-job <generationJobUuid>
```

人工同时核对 Ark 控制台中的创建时间、模型、时长、分辨率、比例和音频选项。只有确认唯一匹配后才显式绑定：

```powershell
uv run cvg reconcile-job <generationJobUuid> `
  --provider-task-id <verifiedArkTaskId>
```

绑定使用行锁并只允许 `submission_unknown` 视频 Job；任务原始
`generation_jobs.provider` 必须与当前访问模式一致。跨模式绑定、重复绑定、
非视频任务或候选列表中不存在的 task ID 都会被拒绝。绑定成功后运行
`resume` 继续轮询或下载。Seedream 是同步接口，无法通过 Seedance List
Tasks 对账；其未知提交保持冻结并由人工检查账单后创建新的 render
revision。

实际提交给供应商的最终Prompt会同时保存在 RenderPlan 和 GenerationJob
请求快照中。可随时查看或导出，不会再次调用模型：

```powershell
uv run cvg show-prompt <lifePackId> `
  --slot noon `
  --plan-revision 3 `
  --output var\prompts\<lifePackId>\r3\02-noon.json
```

导出文件包含关键帧和视频Prompt、renderRevision、任务状态、task ID与请求
分辨率，不包含API Key、Base64图片或签名URL。目标文件已存在时拒绝覆盖。

若供应商返回的合法编码对齐尺寸被旧QC规则误判，可在修正规则后只复检
原始本地媒体：

```powershell
uv run cvg recheck-media <mediaAssetId>
```

该命令不调用Ark，只允许恢复 `media_qc_failed` 的最终视频。

## 9. 生成本地交付包

三条视频全部通过人工审核后：

```powershell
uv run cvg deliver <lifePackId>
```

输出固定为：

```text
output/YYYY-MM-DD/{lifePackId}/delivery-rN/
  01-morning.mp4
  02-noon.mp4
  03-evening.mp4
  manifest.json
```

构建过程先使用 `.building-*`，逐条复核 SHA-256 和 Manifest Schema，再原子改名。随后一个数据库事务写入 DeliveryPackage、DeliveryItem 和按1、2、3排序的 ContinuityEvent。供应商 URL、API Key、Base64 和数据库密码不会进入 manifest。

## 10. 当前实现边界

已实现的是视频生产和本地交付，不包含自动剧本生成、Web 后台、HTTP API、微信小程序、对象存储、CDN、定时发布或自动清理历史 revision。原生音频通过基础媒体属性和人工听审；黑帧、意外对白、歌词、爆音和精细音画同步仍以人工审核为最终门槛。FFmpeg 条件式修复策略保留在文档与配置中，当前首版代码对不合格媒体采取阻断，不自动重编码。
