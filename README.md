# Cat Video Generator

面向固定中性儿童、固定灰白猫和统一绘本画风的本地 AIGC 镜头片段生产台。

```text
创建项目 → 添加任意场景 → AI建议或手工镜头卡
→ 纯文本 / 已有图片 / 新锚点 → 每镜独立视频
→ AI建议＋人工审核 → 可选总片 → 可选区间重拍
```

项目不再强制总导演、自动三集、上午/中午/傍晚、场景路线或世界状态模拟。章节标签
只是文字；一张镜头卡是一项独立、可重做、可回退的 8～15 秒视频生产单元。

## 架构

- FastAPI + Vue 单体工作台，PostgreSQL 是唯一工作流状态源。
- Pydantic V4 契约只保存项目、场景、完整镜头描述和显式素材职责。
- Ark 文本模型可按场景给出镜头建议；Seedream 可选生成锚点；Seedance 每镜生成片段。
- Step 与实际 Prompt 原子落库；Task ID、attempt、资产和审核记录永久保留。
- `submission_unknown` 冻结并人工对账，已有 Task ID 只能继续查询。
- 图片由人工审核；视频抽帧分析只给建议，不会自动拒绝或自动重试付费任务。
- 片段与项目总片使用不可变版本；区间重拍不覆盖源视频。

## 本地启动

```powershell
uv sync --extra dev
uv run cvg doctor

# 终端一
uv run cvg api

# 终端二
npm --prefix web install
npm --prefix web run dev
```

访问 `http://localhost:5173/studio`。单服务模式：

```powershell
npm --prefix web run build
uv run cvg api --static-dir web/dist
```

## 付费按钮

只有“AI 建议镜头卡”“生成锚点”“生成视频片段”“重新生成”和“区间重拍”会调用
Ark；创建项目、编辑场景/镜头、绑定素材、Prompt 预览、人工审核、版本选择和本地总片
合成都不会产生 Ark 生成费用。

## 文档

- [架构决策](docs/architecture/ADR-001-explicit-workflow.md)
- [完整生产流程](docs/workflows/complete-production.md)
- [Windows 手册](docs/workflows/windows-runbook.md)
- [Docker Compose 部署](docs/workflows/docker-deployment.md)
- [HTTP API](docs/http-api.md)
- [V4 实施 Checklist](docs/checklists/shot-queue-production-core.md)

`.env`、Ark Key、数据库密码、Base64 和签名 URL 不得进入 Git、日志或诊断 Manifest。
