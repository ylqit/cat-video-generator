# Cat Video Generator

面向“固定中性儿童 + 固定灰白猫”的三时段本地视频生产系统。系统可在任意时间生成某个内容日期的 morning、noon、evening 三条视频，最终只交付本地 MP4 与 `manifest.json`。

当前只有一条生产链路：

```text
Day Director
→ Morning / Noon / Evening Director
→ EpisodeScript + VisibleWorld
→ 按需 Seedream
→ Seedance single-pass
→ 技术 QC + 语义诊断
→ 人工审核
→ 01 / 02 / 03 本地交付
```

系统不实现小程序、CDN、自动发布、分辨率对比或分段视频拼接。

## 技术选择

- Python 3.12/3.13、Pydantic、SQLAlchemy 2、Alembic。
- PostgreSQL 是工作流、Prompt 和媒体元数据的唯一事实来源。
- Ark Responses 负责导演，Seedream 按需生成关键帧，Seedance 单次生成 8～15 秒音视频。
- 媒体以 SHA-256 内容寻址方式不可变地保存在本地，不写入数据库。
- Typer 提供 Windows CLI；FastAPI + Vue 提供本机创作台。
- 不使用 LangGraph、AgentScope、Celery、Redis 或第二套工作流状态。

## Windows 快速开始

```powershell
uv sync --extra test
uv run cvg doctor

uv run cvg canon import --role person --semantic-key person:front `
  --view front --file "主题示例\人物本体.png"
uv run cvg canon import --role cat --semantic-key cat:front `
  --view front --file "主题示例\猫咪本体.png"
uv run cvg canon import --role style --semantic-key style:line_texture `
  --file "画风示例\已批准线条裁片.png"

uv run cvg plan-day --target-date 2026-08-01 --allow-paid-generation
uv run cvg run-day <runId> --allow-paid-generation
uv run cvg status <runId>
uv run cvg review <assetId> --approve --reason "人工观看通过"
uv run cvg deliver <runId>
```

规划固定为四次独立导演调用：总导演只确定全天边界，三个时段导演分别输出一个 `EpisodeScript`。脚本由本地状态重放检查未知实体、悬空、无原因消失、容器断链和前后状态矛盾；动作多或渲染较难只形成诊断，不作为统一创意预算。

失败的 Ark 步骤不会由 `run-day` 隐式重试。相同剧本需要重新渲染时必须显式执行：

```powershell
uv run cvg retry-step <stepId> `
  --reason "说明重试原因" `
  --allow-paid-generation
```

`submission_unknown` 必须先人工对账，不得自动重复 POST。剧情或世界状态需要修改时使用 `replan-episode`，而不是重试媒体任务。

## 角色、画风和关键帧

- 人物只锁定主要面貌、发型、体型和中性儿童定位；服装、鞋帽、背包服从剧情。
- 猫咪锁定同一只灰白猫的脸型、体型和主要斑纹。
- 画风使用已批准的二维儿童绘本、彩铅/蜡笔素材，排除明显 3D/CG/PBR 倾向。
- 同一连续场景中的外观与道具必须连续；跨时段变化只需有合理剧情原因。
- `KEYFRAME_REVIEW_MODE` 只支持 `semantic_auto` 或 `manual`。
- 关键帧明确失败时不会创建 Seedance 任务；低置信结果转人工审核。
- 最终视频始终进入 `content_review`，不会自动批准或交付。

Episode 需要动作、声音或环境参考时，可导入经批准的多模态素材：

```powershell
uv run cvg reference import --episode-id <episodeId> `
  --role motion --semantic-key motion:gentle-walk `
  --file "references\gentle-walk.mp4"
```

所有 Episode 均由 Seedance 单次完整成片。通过 QC 的供应商 MP4 直接保存，不强制 FFmpeg 重编码。

## 本机 Web 创作台

```powershell
# 终端一
uv run cvg api

# 终端二
cd web
npm install
npm run dev
```

生产模式可执行 `npm run build` 后运行 `uv run cvg api --static-dir web/dist`。后台 JobRegistry 只负责 HTTP 进程内异步执行，真正状态仍以 PostgreSQL 为准。

## 文档

- [文档索引](docs/README.md)
- [架构决策](docs/architecture/ADR-001-explicit-workflow.md)
- [完整生产流程](docs/workflows/complete-production.md)
- [Windows 运行手册](docs/workflows/windows-runbook.md)
- [本机 HTTP 接口](docs/http-api.md)
- [核心收敛 Checklist](docs/checklists/core-simplification.md)

## 安全边界

- `.env`、数据库密码、Ark Key、签名 URL 和 Base64 不进入 Git、日志或 Manifest。
- 每次 Ark 调用前必须先持久化 Step 和完整 Prompt。
- Ark 轮询、下载和 ffprobe 期间不保持数据库事务。
- 当前明文 PostgreSQL 仅在 `CAT_VIDEO_ALLOW_INSECURE_RUNTIME=true` 时显式放行。
