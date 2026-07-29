# Windows PowerShell运行手册

## 安装与预检

```powershell
cd D:\soft\code\OpenGit\cat-video-generator
uv sync --extra test
uv run ruff check src scripts alembic tests
uv run pytest -q
uv run cvg doctor
```

需要 Python 3.12/3.13、ffmpeg/ffprobe、可访问的 PostgreSQL 和 Ark Key。
ffprobe 可以位于 `PATH`，也可配置 `FFPROBE_PATH`。

## 环境变量

复制 `.env.example` 为被 Git 忽略的 `.env`，填入：

```text
ARK_API_KEY
ARK_ACCESS_MODE
ARK_BASE_URL
ARK_IMAGE_MODEL
ARK_VIDEO_MODEL
ARK_PLANNING_MODEL
KEYFRAME_REVIEW_MODE

CAT_VIDEO_DB_HOST
CAT_VIDEO_DB_PORT
CAT_VIDEO_DB_NAME
CAT_VIDEO_DB_USER
CAT_VIDEO_DB_PASSWORD
CAT_VIDEO_DB_SSLMODE
CAT_VIDEO_DB_SCHEMA
```

PowerShell 会话变量优先于 `.env`：

```powershell
$env:ARK_VIDEO_RESOLUTION = "720p"
```

不要在命令行打印密码或 Key。当前临时明文数据库还要求：

```text
CAT_VIDEO_ALLOW_INSECURE_RUNTIME=true
```

切换 TLS 或隧道后将 `CAT_VIDEO_DB_SSLMODE` 改为 `require`，并关闭该许可。

## 初始化数据库

全新环境执行：

```powershell
uv run alembic upgrade head
```

正常运行只允许迁移到最新 head，不自动 downgrade 或删除 Schema。

本仓库曾执行一次旧 V5 归档与八表重建。维护脚本
`scripts/rebuild_compact_schema.py` 仅用于已完成的迁移审计，不是日常命令。

## 日常操作

```powershell
uv run cvg canon import --role person --file "主题示例\人物本体.png"
uv run cvg canon import --role cat --file "主题示例\猫咪本体.png"
uv run cvg canon import --role style --file "画风示例\任一批准示例.png"

uv run cvg plan-day `
  --target-date 2026-08-01 `
  --context "当天环境、天气和希望探索的生活方向" `
  --allow-paid-generation

uv run cvg replan-episode <runId> `
  --slot noon `
  --reason "说明需要修正的剧情、物理或节奏问题" `
  --allow-paid-generation

# 仅在剧情确实需要时，为具体Episode导入动作、声音、场景或元素参考。
uv run cvg reference import --episode-id <episodeId> `
  --role motion --file "references\gentle-motion.mp4"

uv run cvg run-day <runId> --slot morning --allow-paid-generation
uv run cvg resume <runId>
uv run cvg status <runId>
uv run cvg show-prompt <promptId>
uv run cvg review <assetId> --approve --reason "人工观看通过"
uv run cvg deliver <runId>
```

内容日期可以早于、等于或晚于执行日期。morning/noon/evening 仅表示内容顺序，
不是三个定时执行点。

## 启动本机只读API

```powershell
uv run cvg api --port 8765
```

默认仅监听 `127.0.0.1`。打开 `http://127.0.0.1:8765/docs` 查看接口。

## 故障恢复

| 现象 | 处理 |
| --- | --- |
| 缺少 Ark Key | 补充 `.env`；不会创建收费 Step |
| 数据库不可达 | 修复连接；`status` 不根据本地文件猜状态 |
| 迁移版本不匹配 | 先执行 `uv run alembic upgrade head` |
| `submission_unknown` | 停止重复运行，人工对账 Ark 任务 |
| 已有 task ID | 使用 `resume`，只轮询和下载 |
| 下载中断 | `.part` 不会成为正式资产，可恢复下载 |
| 技术QC失败 | 查看 Step 错误和本地媒体，不自动再生成 |
| 内容审核失败 | 明确记录原因，下一次付费必须重新显式授权 |

`DAILY_PLAN_CANDIDATE_COUNT` 在分层导演模式固定为 `1`。一次 `plan-day` 内部仍会
产生四次独立文本模型调用：DayBrief、morning、noon、evening。中午和傍晚会读取
此前时段的已发生状态摘要，但不会把完整旧 Prompt 重复塞入上下文。

关键帧审核：

- `KEYFRAME_REVIEW_MODE=technical_auto`：技术QC通过后继续，审核证据标记
  `autoApprovedUnverified=true`，不冒充人工语义批准。
- `KEYFRAME_REVIEW_MODE=manual`：图片停在 `awaiting_review`，只有执行
  `cvg review <assetId> --approve ...` 后才能生成尾帧或视频。

## 本地文件

- `var/assets/`：不可变 Canon 和生成媒体。
- `var/work/`：下载和检查临时文件。
- `var/archive/`：旧 V5 只读迁移归档。
- `output/`：最终 1/2/3 交付包。

V1 不自动删除当前生产媒体。磁盘清理由人工根据数据库哈希和归档清单执行。
