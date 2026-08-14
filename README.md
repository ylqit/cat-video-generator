# Cat Video Generator

面向固定中性儿童、固定灰白猫和统一绘本画风的本地 AIGC 镜头片段生产台。

```text
创建项目 → 锁定项目角色与画风 Revision → 选择单/多片段模式
→ 剧情医生诊断 → 人工选择方案 → 剧本编辑重写 → 人工确认
→ 分镜导演拆分/选择版本 → 进入五阶段视觉制作看板
→ 场景视觉基准（不是首帧）→ 每片段独立开场设计与实际参考图确认
→ 纯文字 / 上一尾帧 / 已有图片 / 派生开场图 / 场景画面直接起镜
→ 视觉与 Prompt 审稿 → 每片段独立视频 → 版本审核与尾帧衔接
→ AI建议＋人工审核 → cut / 淡黑 / 叠化编排 → 本地总片 → 可选子镜头区间重拍
```

项目不再强制总导演、自动三集、上午/中午/傍晚、场景路线或世界状态模拟。章节标签
只是文字；一个视频片段是一项独立、可重做、可回退的 8～15 秒 Seedance 生产单元，
内部允许 2～4 个连续编号子镜头。

## 架构

- FastAPI + Vue 单体工作台，PostgreSQL 是唯一工作流状态源。
- Pydantic V5 契约保存版本化项目视觉档案、场景视觉基准草稿、完整分镜描述和三层素材职责。
- 同一个 Ark 规划模型串行承担剧情医生、剧本编辑、分镜导演和多模态审稿角色；每一步单独付费确认、保存原稿与接受稿，不是多智能体或多进程系统。
- 片段参考按“批准锚点 → 片段自定义 → 场景视觉基准 → 项目 Canon”排序，并按资产 ID 和 SHA-256 去重；素材可在正文中以 `{{人物}}`、`{{猫咪}}`、`{{场景}}`、`{{道具:名称}}` 引用，提交时再按真实顺序编译成 `@图片N`。
- 最终视频 Prompt 是“人工确认的 LLM 创作正文 + 系统技术外壳”；系统只补分辨率、比例、时长、角色/画风档案、真实图片编号和技术排除项，不用代码重写剧情。
- `docs/采茶叶.mp4` 只定义二维水彩画风，不定义角色身份；角色固定为 5–7 岁短波波头儿童与灰白虎斑猫。
- Step 与实际 Prompt 原子落库；Task ID、attempt、资产和审核记录永久保留。
- `submission_unknown` 冻结并人工对账，已有 Task ID 只能继续查询。
- 图片由人工审核；视频抽帧分析只给建议，不会自动拒绝或自动重试付费任务。
- 片段与项目总片使用不可变版本；总片支持硬切、淡黑和短叠化，区间重拍不覆盖源视频。

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

## Web 使用方式

1. 从主题或完整剧情创建项目，在“剧情与分镜创作”中确认剧情诊断、重写和分镜版本。
2. 分镜落成后，折叠创作历史，按看板主按钮先确认场景视觉基准，再逐片段设计开场。
3. 点击片段进入全屏生成台；“参考素材”显示锚点、片段、场景和项目层最终会提交的真实图片顺序。
4. “分镜与 Prompt”默认只展示创作正文；技术规格、素材快照和 Provider Prompt 位于专家调试折叠区。
5. 图片、LLM 和视频任务提交后可关闭工作台或切换项目；进度与恢复入口统一位于左侧“全局任务”。
6. 只有批准的视频默认进入尾帧提取和成片编排；相邻片段可采用上一批准尾帧，成片支持硬切、淡黑和短叠化。

## 付费按钮

只有“剧情诊断”“剧情重写”“分镜导演”“片段视觉与 Prompt 审稿”“生成场景视觉基准”“生成锚点”“生成视频片段”“重新生成”和“区间重拍”会调用
Ark；创建项目、编辑场景/片段、仅保存、绑定素材、Prompt 预览、人工审核、版本选择和本地总片
合成都不会产生 Ark 生成费用。

## 文档

- [架构决策](docs/architecture/ADR-001-explicit-workflow.md)
- [完整生产流程](docs/workflows/complete-production.md)
- [Windows 手册](docs/workflows/windows-runbook.md)
- [Docker Compose 部署](docs/workflows/docker-deployment.md)
- [HTTP API](docs/http-api.md)
- [V5 升级 Checklist](docs/checklists/v5-creation-flow-upgrade.md)
- [片段视觉与 LLM 辅助 Checklist](docs/checklists/v5-shot-assistance-upgrade.md)
- [分阶段 LLM 创作工作流 Checklist](docs/checklists/v5-staged-creative-workflow.md)
- [LibTV 编排适配 Checklist](docs/checklists/v5-libtv-editing-upgrade.md)
- [设计脚本教程适配说明](docs/workflows/tutorial-adaptation.md)

`.env`、Ark Key、数据库密码、Base64 和签名 URL 不得进入 Git、日志或诊断 Manifest。
