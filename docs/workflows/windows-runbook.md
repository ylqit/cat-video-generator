# Windows PowerShell运行手册

## 安装与预检

```powershell
cd D:\soft\code\OpenGit\cat-video-generator
uv sync --extra test
uv run ruff check src scripts alembic tests
uv run pytest -q
uv run pytest -m postgres -q
git diff --check
uv run cvg doctor
```

需要 Python 3.12/3.13、ffmpeg/ffprobe、可访问的 PostgreSQL 和 Ark Key。
ffprobe 可以位于 `PATH`，也可配置 `FFPROBE_PATH`。

带`postgres`标记的约束、事务和并发测试默认启动一次性PostgreSQL 16
Testcontainer。Docker Desktop守护进程不可用时该组会明确显示skip；这不是
“已执行通过”。

在已经明确授权使用远程测试库时，也可运行隔离Schema模式：

```powershell
$env:CAT_VIDEO_POSTGRES_TEST_MODE = "remote-schema"
try {
    uv run pytest -m postgres -q
} finally {
    Remove-Item Env:CAT_VIDEO_POSTGRES_TEST_MODE -ErrorAction SilentlyContinue
}
```

该模式读取`.env`中的数据库连接，但只创建随机命名的
`cat_video_test_<runId>` Schema，并在测试结束后精确删除。它不会迁移、清空或
写入正式`cat_video` Schema；如果测试进程被强制终止，应先查询并人工确认同名测试
Schema后再清理。

## 环境变量

复制 `.env.example` 为被 Git 忽略的 `.env`，填入：

```text
ARK_API_KEY
ARK_ACCESS_MODE
ARK_BASE_URL
ARK_IMAGE_MODEL
ARK_VIDEO_MODEL
ARK_PLANNING_MODEL
ARK_REVIEW_MODEL
KEYFRAME_REVIEW_MODE
VIDEO_SEMANTIC_REVIEW_MODE
CAT_VIDEO_EVENT_SEED_ROOT

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
uv run cvg canon import --role person --file "主题示例\人物本体.png" `
  --semantic-key person:front --view front
uv run cvg canon import --role cat --file "主题示例\猫咪本体.png" `
  --semantic-key cat:front --view front
uv run cvg canon import --role style --file "画风示例\批准线条裁片.png" `
  --semantic-key style:line_texture

uv run cvg plan-day `
  --target-date 2026-08-01 `
  --context "当天环境、天气和希望探索的生活方向" `
  --allow-paid-generation

# 只有DayBrief已成功、后续时段导演失败时使用；不会重新调用总导演。
uv run cvg resume-planning <runId> --allow-paid-generation

uv run cvg replan-episode <runId> `
  --slot noon `
  --reason "说明需要修正的剧情、物理或节奏问题" `
  --allow-paid-generation

# 仅在剧情确实需要时，为具体Episode导入动作、声音、场景或元素参考。
uv run cvg reference import --episode-id <episodeId> `
  --role motion --semantic-key motion:gentle-motion `
  --file "references\gentle-motion.mp4"

uv run cvg run-day <runId> --slot morning --allow-paid-generation
uv run cvg resume <runId>
uv run cvg status <runId>
uv run cvg show-prompt <promptId>
uv run cvg review <assetId> --approve --reason "人工观看通过"
uv run cvg deliver <runId>
```

内容日期可以早于、等于或晚于执行日期。morning/noon/evening 仅表示内容顺序，
不是三个定时执行点。

新Run的自动参考只使用精确语义键的最新已批准资产。无法确认用途的历史资产标记为
`legacy:<assetId>`，不会参与自动选择。使用`cvg status <runId>`可查看每条
Episode实际使用的语义键、可见世界校验和下一动作。

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
| 图片/视频供应商终态失败 | 查看`status`给出的Step；显式执行`retry-step`并再次确认付费 |
| 本地QC或拼接失败 | 执行`retry-step`创建新QC attempt；不会重新调用Ark |
| 关键帧语义低置信 | 使用`status`读取证据，人工审核后再继续 |
| 关键帧被semantic_auto明确拒绝 | 结论不可翻案；显式重试原图片Step，生成新attempt |
| 找不到语义资产 | 导入或批准准确`semantic_key`，不得退回全局role选择 |
| `multi_clip`被拒绝 | 确认有人工拒绝的单次成片、两段合同和显式许可 |
| Run为`planning_review` | 查看`contradictions`，使用`replan-episode --reason`修复对应时段 |
| 内容审核失败 | 明确记录原因，下一次付费必须重新显式授权 |

终态步骤不会由`run-day`自动重试。状态中会返回精确的`failedStepId`、
`operationKey`和下一命令：

```powershell
# Ark图片、视频、分段或分辨率对比：必须重新确认付费。
uv run cvg retry-step <stepId> `
  --reason "供应商终态失败后按相同剧本重新渲染" `
  --allow-paid-generation

# 本地multi_clip拼接/QC：不需要付费许可。
uv run cvg retry-step <qcStepId> `
  --reason "修复本地封装工具后重新执行拼接"
```

只有`FAILED`、`EXPIRED`或`CANCELLED`可以重试。`submission_unknown`只能对账；
`SUCCEEDED`、`AWAITING_REVIEW`和运行中步骤均不能重试。旧Step、Prompt、task ID、
错误和资产不会被覆盖，新Step使用同一`operationKey`和递增attempt。

`DAILY_PLAN_CANDIDATE_COUNT` 在分层导演模式固定为 `1`。一次 `plan-day` 内部仍会
产生四次独立文本模型调用：DayBrief、morning、noon、evening。中午和傍晚会读取
此前时段的已发生状态摘要，但不会把完整旧 Prompt 重复塞入上下文。

关键帧审核：

- `KEYFRAME_REVIEW_MODE=semantic_auto`：当前默认。9:16、尺寸和黑边等技术硬门
  通过后，再检查身份、二维画风、世界状态和空间拓扑；置信度不足转人工。明确
  拒绝是不可变审计结论，只能通过`retry-step`生成新图片attempt，不能人工翻案。
- `KEYFRAME_REVIEW_MODE=manual`：图片停在 `awaiting_review`，只有执行
  `cvg review <assetId> --approve ...` 后才能生成尾帧或视频。
- `KEYFRAME_REVIEW_MODE=technical_auto`：实验模式。除付费许可外还必须添加
  `--allow-unverified-keyframes`。

视频默认一次完整成片。只有前序单次成片已被人工拒绝，且方案明确包含两个天然硬切
片段时，才可显式选择：

```powershell
uv run cvg run-day <runId> --slot evening `
  --allow-paid-generation --allow-multi-clip
```

这不是自动重试：每段都要独立审核，缺段不拼接。需要空间连续时，第一段通过后才会
抽取真实尾帧作为第二段首帧。

`cvg status <runId>`同时展示`worldConsistencyStatus`、`contradictions`、
`renderRiskLevel`、`renderRiskReasons`、`multiClipRecommended`和
`directorRepairAttempted`。渲染风险只供判断，不会自动阻断单次成片或创建分段任务。

## 本地文件

- `var/assets/`：不可变 Canon 和生成媒体。
- `var/work/`：下载和检查临时文件。
- `var/archive/`：旧 V5 只读迁移归档。
- `output/`：最终 1/2/3 交付包。

V1 不自动删除当前生产媒体。磁盘清理由人工根据数据库哈希和归档清单执行。
