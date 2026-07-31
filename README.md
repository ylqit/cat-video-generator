# Cat Video Generator

面向“固定人物 + 固定灰白猫”的三时段本地视频生产系统。系统在任意时间生成某个
内容日期的 morning、noon、evening 三条视频，最终只交付本地 MP4 与
`manifest.json`，不包含小程序、CDN、定时发布或自动上传。

当前生产链路只有一条：

```text
L1 规划：近期记忆 + 事件种子 → DayBrief → 三个时段导演
L2 世界：人物/猫咪/画风档案 → 精确语义资产 → VisibleWorldPlan
L3 渲染：语义关键帧 → Seedance单次8～15秒成片
                              └→ 人工明确选择时才使用双片段备用路径
L4 交付：技术QC → 视频语义诊断 → 人工审核 → 01/02/03本地交付
```

## 技术选择

- Python 3.12/3.13。
- PostgreSQL 是工作流、Prompt 和媒体元数据的唯一事实来源。
- SQLAlchemy 2 + Alembic 管理八张核心表。
- Pydantic 只表达当前业务契约，不保留 V1～V5 版本分派。
- Ark Responses、Seedream、Seedance 通过一个网关接入。
- FastAPI 提供本机查询、媒体读取与逐请求付费许可的生产控制。
- 视频文件以 SHA-256 内容寻址方式保存在本地，不写入数据库。
- 不使用 LangGraph、AgentScope、PydanticAI、Celery、Redis 或微服务。

## 快速开始（Windows PowerShell）

```powershell
uv sync --extra test
uv run cvg doctor

uv run cvg canon import --role person --semantic-key person:front `
  --view front --file "主题示例\人物本体.png"
uv run cvg canon import --role cat --semantic-key cat:front `
  --view front --file "主题示例\猫咪本体.png"
uv run cvg canon import --role style --semantic-key style:line_texture `
  --file "画风示例\已批准线条裁片.png"

uv run cvg plan-day `
  --target-date 2026-08-01 `
  --allow-paid-generation

uv run cvg run-day <runId> --allow-paid-generation
uv run cvg status <runId>
uv run cvg review <assetId> --approve --reason "人工观看通过"
uv run cvg deliver <runId>
```

`plan-day` 本身会调用付费文本模型，所以也要求显式付费许可。`run-day` 默认按
1、2、3推进三条 Episode，也可用 `--slot morning` 定向验证一条。
一次规划固定调用四次导演：一条总方向和三条时段细化，不用一个超长 Prompt
同时写完三条分镜。某个时段需要修改时只执行：

```powershell
# 规划在某个时段失败时，复用已成功的DayBrief并只补齐缺失时段。
uv run cvg resume-planning <runId> --allow-paid-generation

# 已经生成Episode后，需要根据人工理由重写单个时段。
uv run cvg replan-episode <runId> `
  --slot noon `
  --reason "修正纸袋承重与水果回袋动作" `
  --allow-paid-generation
```

导演不再受“最多两次物理变化”或“最多一次坐下/站起”的固定预算限制。系统会按
动作顺序重放可见世界，只阻断无支撑、状态断链、未知实体、无座位坐下等真实矛盾。
多次交接、坐下再站起或跨镜头动作只记录非阻断渲染风险，剧情自洽后仍直接进入
Seedance。时段导演首次矛盾会自动完整重写一次；再次失败时Run进入
`planning_review`，等待带理由的局部重规划。

媒体终态失败不会由`run-day`隐式重试。`status`会给出精确Step和下一动作；
相同剧本重新渲染必须显式执行并再次确认付费：

```powershell
uv run cvg retry-step <stepId> `
  --reason "说明本次重试原因" `
  --allow-paid-generation
```

`submission_unknown`仍只能对账；旧Step、Prompt、Ark task ID和错误永久保留。
本地multi-clip拼接/QC的`retry-step`不需要付费许可。

人物只要求主要面貌、发型和体型可辨识为同一个人；猫咪保持同一只灰白猫的脸型、
体型和主要斑纹。眼睛服从参考素材整体画风，不再硬编码某一种眼睛拓扑。
人物定位为中性儿童；“女孩、男孩、马尾、发髻”等改变主体定位或真实发长的导演
描述会在收费任务前被拒绝。衣服、鞋帽和背包服从剧情，同一连续场景必须保持。

`KEYFRAME_REVIEW_MODE=semantic_auto` 是当前默认值。关键帧必须先通过9:16技术硬门，
再由独立视觉审核检查身份、二维画风、可见世界状态和空间拓扑；低置信转人工，
明确错误直接拒绝且不可人工覆盖；需要重做时创建新图片attempt。`technical_auto`
只供实验，运行时还必须显式提供
`--allow-unverified-keyframes`。

Episode确实需要动作、声音、场景或元素参考时，可以先导入批准素材：

```powershell
uv run cvg reference import --episode-id <episodeId> `
  --role motion --semantic-key motion:gentle-walk `
  --file "references\gentle-walk.mp4"

uv run cvg reference import --episode-id <episodeId> `
  --role atmosphere --semantic-key atmosphere:morning `
  --file "references\morning-ambience.mp3"
```

普通 Episode 始终使用 `single_pass`。`multi_clip` 只适用于两个天然硬切镜头，
并且必须先有一条人工拒绝的单次成片，再用 `--allow-multi-clip` 明确选择；
每段分别生成和审核，缺少任一段都不会拼出最终视频。

## Web 前端（本地可视化生产台）

`web/` 提供 Vue3 + Element Plus 的分镜卡片式前端：展示每个分镜的剧本、
出场资产、分镜图与完整 Prompt，并可直接在页面执行规划、按 slot 生成、
审核与交付。规划与生成以后台任务推进，页面轮询刷新状态。

```powershell
# 终端一：启动完整HTTP接口（含写端点，仅监听127.0.0.1）
uv run cvg api

# 终端二：开发模式启动前端（代理到8765）
cd web
npm install
npm run dev    # http://127.0.0.1:5173
```

生产模式可单进程托管：`npm run build` 后
`uv run cvg api --static-dir web/dist`，浏览器直接访问 8765。
纯观察场景可用 `uv run cvg api --read-only` 退回只读接口。

## 文档

- [文档索引](docs/README.md)
- [架构决策](docs/architecture/ADR-001-explicit-workflow.md)
- [从导演到三条视频的完整流程](docs/workflows/complete-production.md)
- [Windows运行手册](docs/workflows/windows-runbook.md)
- [火山方舟多模态能力基线](docs/providers/volcengine-multimodal.md)
- [本机只读HTTP接口](docs/http-api.md)
- [原始设计脚本教程](docs/设计脚本教程)

## 安全边界

- `.env`、数据库密码、Ark Key、签名下载 URL 与 Base64 不进入 Git、日志或
  Manifest。
- Ark 调用前必须先在 PostgreSQL 保存 Step 与完整 Prompt。
- `submission_unknown` 不得自动重复 POST，必须先人工对账。
- 当前明文 PostgreSQL 仅由 `CAT_VIDEO_ALLOW_INSECURE_RUNTIME=true`
  显式放行；后续启用 TLS 或隧道只需修改连接配置。
- 所有通过 QC 的供应商 MP4 直接保存，不强制 FFmpeg 重编码。
