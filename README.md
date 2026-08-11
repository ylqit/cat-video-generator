# Cat Video Generator

面向“固定中性短发儿童 + 固定灰白猫”的逐集生活故事视频生产系统。默认由猫咪的
探索、发现、追逐或自然反应推动可见信息，人物承担工具活动或回应，早中晚可组成
跨场景连续生活弧，也可以在顺序模式下逐集补充和确认。

```text
生活故事项目输入
→ 可选 Project Outline（仅主题扩写模式）
→ Morning导演 / 视觉 / 成片 / 人工结果卡
→ Noon导演 / 视觉 / 成片 / 人工结果卡
→ Evening导演 / 视觉 / 成片 / 人工结果卡
→ 01 / 02 / 03本地交付
```

顺序工作台一次只展开当前时段。中午和傍晚可以保存原文，但在前一时段完成前不会
创建收费Step。每个后续时段都可独立选择：不关联、手工关联或付费生成关联建议；
即使保存了关联卡，也只有显式开启“加载关联卡”才会进入导演Prompt。前序图片、代表帧
和视频引用同样默认关闭，由用户逐项指定身份、道具、场景、构图或运动职责。

## 技术架构

- Python、Pydantic、SQLAlchemy、Alembic与显式状态机。
- PostgreSQL是工作流、Prompt和媒体元数据的唯一事实来源。
- Ark Responses负责导演与非阻断视觉建议，Seedream生成单张视觉锚点，Seedance生成视频。
- `EpisodeScript`以完整长剧情为创作主体，用1～3段完整镜头描述和少量关键硬约束
  承担自动化控制；图片、视频和审核Prompt由它确定性编译，已提交attempt的实际Prompt
  不可覆盖。
- 8～15秒单次成片；16～45秒使用完整模型的官方视频延展。
- 媒体以SHA-256不可变落盘，不写入数据库。
- FastAPI与Vue提供统一生产工作台；Typer保留运维CLI。
- 工作台使用固定语义画布展示当前节点和未来锁定路线；画布只读投影PostgreSQL，
  不允许任意连线或创建业务节点。
- 每条成片具有非破坏性单轨时间轴。整条重生成保留旧attempt；0.5～13秒的单Clip区间
  可生成候选revision，经QC和人工审核后再显式切换正式版本，原视频和原音轨不覆盖。
- 不使用故事板组图、逐镜独立生成、创作型多片段拼接、LangGraph、Celery或Redis。
- Seedance延展实际返回新增尾段；中长视频仅用FFmpeg `stream copy`把原始区段顺序封装，
  不重新编码画面或声音。

## 快速开始

```powershell
uv sync --extra test
uv run cvg doctor

# 终端一
uv run cvg api

# 终端二
npm --prefix web install
npm --prefix web run dev
```

访问`http://localhost:5173/studio`。新建生活故事项目时先选择剧情来源：

- `theme_expand`：只给主题，调用一次总导演生成三个命名时段的场景方向。
- `episode_scripts`：一次粘贴三集，或在顺序模式下先填写一个时段；创建项目本身
  不调用Ark，当前时段进入镜头化时才单独确认费用。

项目可配置`adaptive / progressive_locations / single_location`场景路线，以及全天
默认活动焦点和早中晚
各自的活动焦点和时长档：short 8～15秒、medium 16～30秒、long 31～45秒或
adaptive。可选总导演只解析全天方向；时段导演在档位内生成完整剧情、1～3段镜头
和精确秒数。Web默认编辑完整剧情、镜头段落及少量连接、承重、容器、穿戴或交接硬
约束；高级Prompt覆盖默认关闭，脚本变化后会自动失效并要求重新确认。

Web默认采用`guided_sequential`：后续时段不会自动读取前序内容；只有用户保存并启用
关联卡时才读取该卡正文，不会把原脚本或诊断模型判断直接当成当天事实。需要无人值守时可显式选择
`auto_day`；已有剧本模式必须先提供完整三集，主题扩写模式则由项目大纲提供方向。

## 恢复与付费安全

- 每次Ark请求前先原子持久化Step和实际Prompt。
- 相同输入复用幂等Step；终态失败只能显式创建新attempt。
- 已有Seedance Task ID只继续查询，不重复POST。
- `submission_unknown`视频必须人工对账；Seedream同步超时不会自动产生第二次付费请求，用户确认计费风险后才能显式重试。
- 最终视频始终停在`content_review`；顺序模式还需确认傍晚结果卡后才能交付。
- AI对剧情、身份、画风、道具关系和接缝的判断只作为建议；只有素材损坏、供应商
  能力不支持、付费许可/幂等/Task绑定或数据库契约损坏会在生产前硬阻断。

## 文档

- [文档索引](docs/README.md)
- [架构决策](docs/architecture/ADR-001-explicit-workflow.md)
- [完整生产流程](docs/workflows/complete-production.md)
- [Windows手册](docs/workflows/windows-runbook.md)
- [Docker Compose部署](docs/workflows/docker-deployment.md)
- [HTTP接口](docs/http-api.md)
- [实施Checklist](docs/checklists/narrative-render-core.md)
- [语义画布与区间重生成Checklist](docs/checklists/workflow-canvas-range-edit.md)
- [生活故事项目V3 Checklist](docs/checklists/story-project-v3.md)
- [单时段、可选关联与本地验收Checklist](docs/checklists/single-slot-optional-links.md)

## 安全边界

`.env`、数据库密码、Ark Key、签名URL和Base64不得进入Git、日志或Manifest。媒体读取
限定在配置根目录；当前明文PostgreSQL只在显式不安全许可下放行。
