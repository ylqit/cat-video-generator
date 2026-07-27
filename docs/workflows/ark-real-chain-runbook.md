# 真实 Ark 链路运行手册

本手册用于在 Windows PowerShell 中完成从 Canon 本体到本地 MP4 交付的真实链路。运行时代码只支持火山方舟 Ark，不提供 Mock Provider。未配置 Key 时可以完成安装、Schema 校验和单元测试，但不会创建任何收费任务。

## 1. 付费烟测前的硬门槛

同时满足以下条件后才允许运行 `run-pack` 或 `run-next`：

1. 远程 PostgreSQL 已启用 SSL，或本机通过安全隧道连接；`CAT_VIDEO_DB_SSLMODE` 为 `require`、`verify-ca` 或 `verify-full`。
2. 对话中曾暴露的旧数据库密码已经轮换。
3. `cvg db upgrade` 已将正式 `cat_video` Schema 升级到最新 Alembic head。
4. ffprobe 可以从 `PATH` 或 `FFPROBE_PATH` 找到；只有启用条件式媒体修复时才要求 ffmpeg。
5. 人物、猫咪和画风 Canon 均已导入并人工批准。
6. `ARK_API_KEY` 只在当前 PowerShell 会话中注入。
7. 每次可能创建任务的命令都显式带 `--allow-paid-generation`。

现有无 SSL 的公网 PostgreSQL 只允许 `doctor --allow-insecure-readonly-smoke` 和随机临时 Schema 写测，不能保存正式 LifePack、Ark task ID 或视频元数据。

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

## 3. 安全数据库配置与迁移

在当前 PowerShell 会话设置数据库环境变量。密码不要写入仓库、Markdown 或命令历史文件：

```powershell
$env:CAT_VIDEO_DB_HOST = "<secure-host-or-tunnel>"
$env:CAT_VIDEO_DB_PORT = "5432"
$env:CAT_VIDEO_DB_NAME = "vedio-appdb"
$env:CAT_VIDEO_DB_USER = "postgres"
$env:CAT_VIDEO_DB_PASSWORD = "<rotated-password>"
$env:CAT_VIDEO_DB_SSLMODE = "require"

uv run cvg db upgrade
uv run cvg doctor
```

`doctor` 必须报告正确数据库名、PostgreSQL 14+、SSL 已启用、Schema 权限通过和 Alembic revision 匹配。

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

## 6. 配置 Ark 并执行第一条真实烟测

```powershell
$env:ARK_API_KEY = "<your-ark-api-key>"
$env:ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
$env:ARK_IMAGE_MODEL = "doubao-seedream-5-0-pro-260628"
$env:ARK_VIDEO_MODEL = "doubao-seedance-2-0-260128"

uv run cvg doctor
uv run cvg run-pack life-2026-07-24-seaside-travel --slot morning --allow-paid-generation
```

旅游 morning 的四项视觉风险均为 false，所以第一条烟测走 `direct_references`：

1. 数据库先写入唯一 `GenerationJob(submitting)`。
2. 本地三张 Canon 图片转为内存中的 Base64 data URL；完整 Base64 不写日志或数据库。
3. Seedance Create 使用 `reference_image`、720p、9:16、10秒、`generate_audio=true`、`watermark=false`。
4. 请求不发送 `frames`、`camera_fixed`、`service_tier` 或 `seed`。
5. 获得 task ID 后短事务写入 `queued`，随后轮询 `queued/running/succeeded`。
6. 成功 URL 立即流式下载到 `.part`，计算 SHA-256 后原子进入 `var/assets/generated/sha256/`。
7. ffprobe 检查 MP4、H.264、AAC、720×1280、8～15秒和音轨。
8. 通过后进入 `content_review`，不会自动交付。

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

`resume` 只处理已经存在的 keyframe/video job、轮询、下载和 QC。它不会为 `planned` Slot 创建新收费任务，也不会重新 POST `submission_unknown`。幂等键由 `episodeId + renderRevision + jobType + clipIndex + normalizedInputHash` 派生；重复执行会恢复已有记录。

若 Create 请求已经发送但没有拿到 task ID，状态变为 `submission_unknown`。此时必须在 Ark 控制台或任务列表人工对账，不能直接重跑付费 POST。

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
