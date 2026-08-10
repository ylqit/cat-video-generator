# Windows 运行手册

## 1. 环境与配置

```powershell
uv sync --extra test
uv run cvg doctor
```

需要Python 3.12/3.13、uv、ffmpeg、ffprobe、PostgreSQL 14+和Ark标准API Key。
`.env`保存本机配置，PowerShell环境变量优先；不要提交`.env`。

```text
CAT_VIDEO_DB_HOST / PORT / NAME / USER / PASSWORD / SSLMODE / SCHEMA
CAT_VIDEO_ALLOW_INSECURE_RUNTIME
ARK_API_KEY
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
ARK_PLANNING_MODEL
ARK_IMAGE_MODEL=doubao-seedream-5-0-260128
ARK_VIDEO_MODEL
ARK_REVIEW_MODEL
ARK_VIDEO_RESOLUTION=480p|720p
ARK_DIRECTOR_REQUEST_TIMEOUT_SECONDS=240
ARK_IMAGE_REQUEST_TIMEOUT_SECONDS=600
ARK_IMAGE_TIMEOUT_AUTO_RETRIES=1
ARK_IMAGE_RETRY_DELAY_SECONDS=15
ARK_REVIEW_REQUEST_TIMEOUT_SECONDS=240
ARK_VIDEO_API_TIMEOUT_SECONDS=120
ARK_TASK_TIMEOUT_SECONDS=1800
ARK_POLL_INTERVAL_SECONDS=10
IMAGE_REVIEW_MODE=semantic_auto|manual
MEDIA_WORK_ROOT / MEDIA_ASSET_ROOT / DELIVERY_OUTPUT_ROOT
```

## 2. 迁移到0012

0012启用极简混合导演契约版本2，且不转换旧Episode JSON，因此要求旧生产Run为空。
先预览并保存诊断清单：

```powershell
uv run python scripts/clear_production_history.py
```

核对`var/diagnostics/`中的Run、资产路径和SHA-256，再使用程序打印的精确口令：

```powershell
uv run python scripts/clear_production_history.py --confirm DELETE-<Run数量>-RUNS-AND-<旧Canon数量>-OLD-CANON
uv run alembic upgrade head
uv run cvg doctor
```

清理删除带Run外键的业务记录和已验证位于媒体根下的历史文件，同时只保留当前正式
语义键的最新批准Canon；仍被保留Canon引用的文件路径永远不会被删除。
Doctor最终必须报告`0012_minimal_director_contract`。

## 3. Canon

生产至少需要当前批准版本：

```text
person:headshot
person:front / person:side / person:back（按现有系列档案）
cat:front / cat:side / cat:back
style:line_texture
style:indoor
style:outdoor
```

新Run只选择精确semantic_key的最新已批准资产；`legacy:*`、候选和拒绝资产不会自动复用。

## 4. 本地Web

开发模式使用两个进程：

```powershell
# 终端一
uv run cvg api

# 终端二
npm --prefix web install
npm --prefix web run dev
```

访问`http://localhost:5173/studio`。生产构建可由同一个后端托管：

```powershell
npm --prefix web run build
uv run cvg api --static-dir web/dist
```

## 5. 创建与生成

Web新建Run时配置全天默认活动焦点和早中晚覆盖；每个时段选择short、medium、long
或adaptive。总导演只解析全天容量，时段导演在档位内确定精确秒数。

“三集导演”默认编辑极简混合脚本：完整长剧情、关系弧、1～3段完整镜头描述和少量
关键硬约束。每个镜头段落自然表达镜头目的、机位、运镜、人物与猫咪站位、动作路径、
接触结果和稳定切点。页面实时编译定妆图、开场锚点及视频Prompt；预览不会保存数据
或调用Ark。高级Prompt覆盖必须显式开启，脚本变化后会标记为过期，重新确认前不会
进入下一次收费请求。

CLI核心入口仍可使用：

```powershell
uv run cvg plan-day --target-date 2026-08-10 --allow-paid-generation
uv run cvg status <runId>
uv run cvg run-day <runId> --slot morning --allow-paid-generation
```

`run-day`依次确保定妆图、开场锚点和RenderPlan视频任务。8～15秒一个任务；中长
视频按模型能力使用官方延展。延展返回的新增尾段会在各自QC通过后，通过FFmpeg
`stream copy`与前序区段顺序封装；不会生成故事板组图、逐镜视频，也不会转码。

点击任一工作流节点会按需读取Trace。Prompt链路依次展示输入摘要、当前结构化结果、
当前编译Prompt、各attempt实际调用Prompt、Ark原始结构化JSON、可选归一化结果和警告；
Provider、媒体、审核与尝试历史分别展示，页面轮询不会改变当前stage、slot或node。

## 6. 恢复、审核和交付

```powershell
uv run cvg retry-step <stepId> --reason "明确原因" --allow-paid-generation
uv run cvg resume <runId>
uv run cvg replan-episode <runId> --slot noon --reason "调整剧情原因" --allow-paid-generation
uv run cvg review <assetId> --approve --reason "人工观看通过"
uv run cvg deliver <runId>
```

- `retry-step`创建新attempt，不覆盖原Prompt、Task ID或错误。
- 已有视频Task ID使用resume继续查询，不产生第二次POST。
- 视频`submission_unknown`在Web节点列出候选并人工对账。
- 图片同步超时按配置最多自动重试一次，可能重复计费。
- 最终视频必须人工批准；三条都ready后才能交付1/2/3。

## 7. 常见故障

| 现象 | 处理 |
| --- | --- |
| 迁移落后 | 先生成清理Manifest并按口令清理，再`alembic upgrade head` |
| 长视频收费前失败 | 当前模型不支持官方延展；换用已开通完整模型或把该时段改为short |
| 图片语义失败 | 在具体图片Step显式重试，不覆盖拒绝结论 |
| 视频监看窗口结束 | 继续查询已有Task ID |
| `submission_unknown` | 视频人工对账；图片需确认重复计费后新attempt |
| 交付不可用 | 确认早中晚最终`video`资产均已人工批准 |

## 8. 最终验证

```powershell
uv run ruff check .
uv run pytest -q
$env:CAT_VIDEO_POSTGRES_TEST_MODE='remote-schema'
uv run pytest -m postgres -q
Remove-Item Env:CAT_VIDEO_POSTGRES_TEST_MODE
npm --prefix web run build
git diff --check
uv run cvg doctor
```

远程PostgreSQL测试只创建唯一临时Schema，禁止在正式`cat_video`中执行破坏性测试。
