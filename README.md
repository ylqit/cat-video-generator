# Cat Video Generator

面向“固定中性儿童 + 固定灰白猫”的三时段本地视频生产系统。系统可在任意时间生成某个内容日期的 morning、noon、evening 三条视频，最终只交付本地 MP4 与 `manifest.json`。

当前只有一条生产链路：

```text
Day Director
→ Morning / Noon / Evening Director
→ EpisodeScript + SceneContinuity
→ 按外观生成或复用一张日内定妆图
→ 每条一次 Seedream 故事板组图
→ 故事板整组语义审核
→ Seedance single-pass
→ 技术 QC + 语义诊断
→ 人工审核
→ 01 / 02 / 03 本地交付
```

系统不实现小程序、CDN、自动发布、分辨率对比或分段视频拼接。

## 技术选择

- Python 3.12/3.13、Pydantic、SQLAlchemy 2、Alembic。
- PostgreSQL 是工作流、Prompt 和媒体元数据的唯一事实来源。
- Ark Responses 负责导演，Seedream 为每条Episode生成3～4张故事板，Seedance单次生成8～15秒音视频。
- 媒体以 SHA-256 内容寻址方式不可变地保存在本地，不写入数据库。
- Typer 提供 Windows CLI；FastAPI + Vue 提供本机创作台。
- 不使用 LangGraph、AgentScope、Celery、Redis 或第二套工作流状态。

## Windows 快速开始

```powershell
uv sync --extra test
uv run cvg doctor

uv run cvg canon import --role person --semantic-key person:headshot `
  --view headshot --file "风格定稿\Canon-v1\人物-大头照.png"
uv run cvg canon import --role person --semantic-key person:front `
  --view front --file "风格定稿\Canon-v1\人物-正面.png"
uv run cvg canon import --role cat --semantic-key cat:front `
  --view front --file "风格定稿\Canon-v1\猫咪-正面.png"
uv run cvg canon import --role style --semantic-key style:line_texture `
  --file "风格定稿\Canon-v1\画风-线条材质.png"

uv run cvg plan-day --target-date 2026-08-01 --allow-paid-generation
uv run cvg run-day <runId> --allow-paid-generation
uv run cvg status <runId>
uv run cvg review <assetId> --approve --reason "人工观看通过"
uv run cvg deliver <runId>
```

规划固定为四次独立导演调用：总导演只确定全天边界，三个时段导演分别输出一个 `EpisodeScript`。系统从数据化剧情模式池为三个时段选择不同的软创意先验（目标挑战、意外麻烦、幻想奇遇、照顾治愈、比赛游戏、误会和解等），导演按当天环境改编，不把某一种三幕弧写死为模板。人物、猫咪性格和幽默方式可在Web新建主题时覆盖，并随Run冻结。`SceneContinuity`只保存人物、猫咪和关键道具的起点、终点与生命周期；停步、转头、蹲下、嗅闻等导演动作不进入状态账本，普通背景也不建账。`inside`既可指向盒子等实体，也可指向长椅缝隙等已登记锚点。

失败的 Ark 步骤不会由 `run-day` 隐式重试。相同剧本需要重新渲染时必须显式执行：

```powershell
uv run cvg retry-step <stepId> `
  --reason "说明重试原因" `
  --allow-paid-generation
```

Seedance 的 `submission_unknown` 必须先通过Ark任务列表人工对账，不得自动重复POST；已有Task ID的任务只继续查询。Seedream是同步接口，超时后无法按Task ID找回，系统默认等待600秒后最多自动创建一次新attempt，并在记录和页面明确标记潜在重复计费。剧情或连续性需要修改时使用 `replan-episode`，而不是重试媒体任务。

## 角色、画风和故事板

- 人物只锁定主要面貌、发型、体型和中性儿童定位；服装、鞋帽、背包服从剧情。
- 猫咪锁定同一只灰白猫的脸型、体型和主要斑纹。
- 唯一生产画风为 `风格定稿/Canon-v1` 的日系二维治愈生活插画：细腻手绘线条、
  柔和哑光水彩式数字绘制、清新自然色、温和自然光和克制景深。
- 旧 `主题示例`、`画风示例` 以及任意 `style:source_*` 不再是运行时资产来源。
- 人物Canon使用独立大头照和全身照，避免把人物多视图拼图直接送入生图模型；猫咪继续使用正面、侧面和背面参考。
- 每个时段先按实际服饰生成一张日内定妆图；后续时段外观完全相同则复用，外观变化才新增一次Seedream调用和轻量审核。
- 故事板生成只发送“定妆图 + 猫咪匹配视角 + 室内/户外定稿画风”，必要时追加一张关键道具图；人物单视图和线条材质图只用于定妆，不与故事板人物基准竞争。
- 同一连续场景中的外观与道具必须连续；跨时段变化只需有合理剧情原因。
- `STORYBOARD_REVIEW_MODE` 只支持 `semantic_auto` 或 `manual`。
- 每条Episode只创建一个Seedream组图步骤；面板由镜头而非动作数量编译，2镜头返回3张、3镜头返回4张独立、无文字、9:16故事板。
- 故事板少图、技术失败或整组语义失败时不会创建Seedance任务；低置信结果转人工审核。
- 普通背景、轻微表情、眼睛画法和明确切镜后的合理重新构图只记为故事板警告；换人、复制、严重头身或猫尾结构失衡、同镜头空间跳变、整层服装消失、关键道具变类和结尾未兑现仍是硬门。
- 最终视频始终进入 `content_review`，不会自动批准或交付。

Episode需要更精确地固定关键道具或场景时，可选导入图片素材供Seedream使用；普通场景没有专用参考图不会阻断生成：

```powershell
uv run cvg reference import --episode-id <episodeId> `
  --role element --semantic-key element:blue-pinwheel `
  --file "references\blue-pinwheel.png"
```

Seedance默认按顺序接收全部故事板；结尾画面必须精确时只接收首张与末张作为严格首尾帧。Canon不再与故事板重复传给Seedance。通过QC的供应商MP4直接保存，不强制FFmpeg重编码。

## 本机 Web 生产工作台

```powershell
# 终端一
uv run cvg api

# 终端二
cd web
npm install
npm run dev
```

生产模式可执行 `npm run build` 后运行 `uv run cvg api --static-dir web/dist`。工作台统一展示“总导演→三集导演→故事板→视频成片→审核交付”，节点抽屉可查看实际Prompt、输入素材、Provider任务、审核证据和attempt历史。`/runs/:id`会进入同一个工作台；后台 JobRegistry 只负责 HTTP 进程内异步执行，真正状态仍以 PostgreSQL 为准。Linux 服务器可通过单容器 [Docker Compose 部署](docs/workflows/docker-deployment.md)，直接访问 `http://服务器IP:8765`。

## 文档

- [文档索引](docs/README.md)
- [架构决策](docs/architecture/ADR-001-explicit-workflow.md)
- [完整生产流程](docs/workflows/complete-production.md)
- [Windows 运行手册](docs/workflows/windows-runbook.md)
- [Docker Compose 部署](docs/workflows/docker-deployment.md)
- [本机 HTTP 接口](docs/http-api.md)
- [故事板优先内核 Checklist](docs/checklists/storyboard-first-core.md)

## 安全边界

- `.env`、数据库密码、Ark Key、签名 URL 和 Base64 不进入 Git、日志或 Manifest。
- 每次 Ark 调用前必须先持久化 Step 和完整 Prompt。
- Ark 轮询、下载和 ffprobe 期间不保持数据库事务。
- 当前明文 PostgreSQL 仅在 `CAT_VIDEO_ALLOW_INSECURE_RUNTIME=true` 时显式放行。
