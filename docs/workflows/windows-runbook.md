# Windows 运行手册

## 1. 环境准备

```powershell
uv sync --extra test
uv run cvg doctor
```

需要：Python 3.12/3.13、uv、ffmpeg、ffprobe、可访问的 PostgreSQL 14+ 和 Ark 标准 API Key。

`.env` 是本机运行配置，PowerShell 会话环境变量优先。不要把 `.env`、数据库密码或 Ark Key 提交到 Git。

核心配置：

```text
CAT_VIDEO_DB_HOST
CAT_VIDEO_DB_PORT
CAT_VIDEO_DB_NAME
CAT_VIDEO_DB_USER
CAT_VIDEO_DB_PASSWORD
CAT_VIDEO_DB_SSLMODE
CAT_VIDEO_DB_SCHEMA=cat_video
CAT_VIDEO_ALLOW_INSECURE_RUNTIME

ARK_API_KEY
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
ARK_PLANNING_MODEL
ARK_IMAGE_MODEL
ARK_VIDEO_MODEL
ARK_REVIEW_MODEL
ARK_VIDEO_RESOLUTION=480p|720p

KEYFRAME_REVIEW_MODE=semantic_auto|manual
MEDIA_WORK_ROOT
MEDIA_ASSET_ROOT
DELIVERY_OUTPUT_ROOT
```

## 2. 预检与迁移

```powershell
uv run cvg doctor
uv run cvg db upgrade
uv run cvg doctor
```

正式 Schema 当前必须位于 `0006_core_simplification`。Doctor 还会检查标准 Ark 配置、ffmpeg/ffprobe、Canon 数量和事件种子目录。

当前明文数据库只有在以下配置同时成立时才允许正式运行：

```text
CAT_VIDEO_DB_SSLMODE=disable
CAT_VIDEO_ALLOW_INSECURE_RUNTIME=true
CAT_VIDEO_DB_SCHEMA=cat_video
```

这是临时风险许可；迁移到 TLS 或安全隧道后应关闭。

## 3. Canon 与参考素材

```powershell
uv run cvg canon import --role person --semantic-key person:front `
  --view front --file "主题示例\人物本体.png"
uv run cvg canon import --role cat --semantic-key cat:front `
  --view front --file "主题示例\猫咪本体.png"
uv run cvg canon import --role style --semantic-key style:line_texture `
  --file "画风示例\线条裁片.png"
```

新 Run 只自动选择精确 `semantic_key` 的最新已批准资产。候选、拒绝和 `legacy:*` 资产不会被复用。

## 4. 规划与生成

```powershell
uv run cvg plan-day --target-date 2026-08-01 --allow-paid-generation
uv run cvg status <runId>

uv run cvg run-day <runId> --slot morning --allow-paid-generation
uv run cvg run-day <runId> --allow-paid-generation
uv run cvg status <runId>
```

`plan-day` 调用一次总导演和三个时段导演。`run-day` 默认按1、2、3推进，但不会自动重试已经终态失败的收费任务。

需要单独修订某个时段：

```powershell
uv run cvg replan-episode <runId> --slot noon `
  --reason "说明剧情或世界状态修改原因" `
  --allow-paid-generation
```

## 5. 审核、重试与恢复

```powershell
uv run cvg review <assetId> --approve --reason "人工观看通过"
uv run cvg review <assetId> --reject --reason "说明身份、物理或内容错误"

uv run cvg retry-step <stepId> `
  --reason "同一剧本重新渲染" `
  --allow-paid-generation

uv run cvg resume <runId>
```

- `retry-step` 只接受 failed、expired、cancelled。
- `submission_unknown` 必须对账，不能重试。
- 被拒绝关键帧不会复用；新 attempt 保留旧结论。
- `resume` 只恢复已有 task ID 的轮询、下载或 QC，不重复创建收费 POST。
- 最终视频必须人工审核，不会自动交付。

## 6. 交付与本地文件

```powershell
uv run cvg deliver <runId>
```

目录：

```text
var/       工作文件、Prompt 导出和只读历史归档
assets/    按 SHA-256 保存的不可变媒体
output/    01-morning、02-noon、03-evening 与 manifest
```

系统不自动删除历史媒体。清理前必须以数据库路径、SHA-256 和归档 Manifest 精确对账。

## 7. 本机 Web

```powershell
# 终端一
uv run cvg api

# 终端二
cd web
npm install
npm run dev
```

生产前端：

```powershell
cd web
npm run build
cd ..
uv run cvg api --static-dir web/dist
```

## 8. 常见故障

| 现象 | 处理 |
| --- | --- |
| 迁移落后 | 先运行 `cvg db upgrade`，再执行 Doctor |
| 明文连接被拒绝 | 检查显式不安全许可；正式环境优先启用 TLS/隧道 |
| 缺少 Canon 或语义键 | 导入并批准精确人物、猫咪和画风资产 |
| `planning_review` | 查看矛盾后运行 `replan-episode` 或 `resume-planning` |
| 关键帧语义失败 | 使用 `retry-step` 创建新 attempt，不覆盖旧审核 |
| 视频 Step 失败 | 明确原因后显式付费重试；`run-day` 不代替重试 |
| `submission_unknown` | 人工对账 Ark 任务，禁止重复 POST |
| ffprobe 失败 | 检查路径、容器和文件完整性，不重新提交 Seedance |
| 交付被拒绝 | 确认三个 slot 都有已批准且 ready 的视频资产 |

## 9. 验证命令

```powershell
uv run ruff check .
uv run pytest -q
$env:CAT_VIDEO_POSTGRES_TEST_MODE='remote-schema'
uv run pytest -m postgres -q
Remove-Item Env:CAT_VIDEO_POSTGRES_TEST_MODE
git diff --check
uv run cvg doctor
```

远程 PostgreSQL 测试只创建唯一临时 Schema，并在结束时清理；不得在正式 `cat_video` 内运行破坏性测试。
