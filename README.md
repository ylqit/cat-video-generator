# Cat Video Generator

面向固定中性儿童、固定灰白猫和统一绘本画风的本地 AIGC 镜头片段生产台。

```text
主题入口 → AI扩写并人工确认 ┐
完整剧情 → 可选诊断/重写 ──┴→ 分镜导演拆分/选择版本
→ 进入五阶段视觉制作看板 → 按需准备服装 / 环境 / 关键道具
→ 可选场景视觉基准（不是首帧）→ 每片段独立开场设计与实际参考图确认
→ 纯文字 / 上一尾帧 / 已有图片 / 派生开场图 / 场景画面直接起镜
→ 视觉与 Prompt 审稿 → 每片段独立视频 → 版本审核与尾帧衔接
→ AI建议＋人工审核 → cut / 淡黑 / 叠化编排 → 本地总片 → 可选子镜头区间重拍
```

项目不再强制总导演、自动三集、上午/中午/傍晚、场景路线或世界状态模拟。章节标签
只是文字；一个视频片段是一项独立、可重做、可回退的 8～15 秒 Seedance 生产单元，
内部允许 2～4 个连续编号子镜头。

## 架构

- FastAPI + Vue 单体工作台，PostgreSQL 是唯一工作流状态源。
- `ShotGenerationSpec` 是看板、片段工作区、Prompt 预览和真实提交共同使用的唯一生成事实；`text_only`、`reference_media`、`first_frame` 三种 Provider 模式互斥。
- 制作看板一次返回项目图与全部片段规格；片段生成台使用可刷新、可深链接的独立路由，不再加载完整项目图。
- Pydantic V5 契约保存版本化项目视觉档案、场景视觉基准草稿、完整分镜描述和三层素材职责。
- 同一个 Ark 规划模型串行承担剧情医生、剧本编辑、分镜导演、视觉资产规划和多模态审稿角色；每一步单独付费确认、保存原稿与接受稿，不是多智能体或多进程系统。
- 视频输入采用互斥模式：有批准锚点时 Seedance 只接收该 `first_frame`；没有锚点时，普通参考图才按“片段自定义 → 场景视觉基准 → 项目 Canon”排序并按资产 ID、SHA-256 去重。素材可在正文中以 `{{人物}}`、`{{猫咪}}`、`{{场景}}`、`{{道具:名称}}` 引用，提交时再按真实顺序编译成 `@图片N`。
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
npm --prefix web run dev -- --host 0.0.0.0
```

访问 `http://localhost:5173/studio`。单服务模式：

```powershell
npm --prefix web run build
uv run cvg api --static-dir web/dist
```

## Web 使用方式

1. 只有一句话主题时先生成并接受剧情扩写；已有完整剧情时可直接运行分镜，也可按需先做诊断和重写。
2. 分镜是进入制作阶段的唯一必经 LLM 步骤。分镜落成后，“视觉资产准备”只建议是否需要服装、环境、关键道具或构图图，用户可逐项生成、上传、复用或跳过。
3. 场景视觉基准同样可跳过；只有本场共同服装、环境和固定道具需要统一时才生成。每个片段仍单独设计动作开始前的静态开场。
4. 点击片段进入全屏生成台；“参考素材”显示锚点、片段、场景和项目层最终会提交的真实图片顺序。
5. “分镜与 Prompt”默认只展示创作正文；技术规格、素材快照和 Provider Prompt 位于专家调试折叠区。
6. 图片、LLM 和视频任务提交后可关闭工作台或切换项目；进度与恢复入口统一位于左侧“全局任务”。
7. 只有批准的视频默认进入尾帧提取和成片编排；相邻片段可采用上一批准尾帧，成片支持硬切、淡黑和短叠化。

全局模型由左侧“系统设置”（`/settings`）管理。Web 只能选择服务端白名单中的规划、
图片、视频和审稿模型，并可调整 480p/720p 与语义审稿开关；保存后立即作用于新任务。
Ark Key、Endpoint、数据库和媒体路径始终保留在服务端环境中。运行时固定使用真实 Ark，
不提供 Fake/Provider 切换；Ark 未配置时项目浏览和编辑仍可用，付费操作会提前禁用。

## 付费按钮

只有“剧情扩写”“剧情诊断”“剧情重写”“分镜导演”“视觉资产规划”“片段视觉与 Prompt 审稿”“生成视觉参考图”“生成场景视觉基准”“生成锚点”“生成视频片段”“重新生成”和“区间重拍”会调用
Ark；创建项目、编辑场景/片段、仅保存、绑定素材、Prompt 预览、人工审核、版本选择和本地总片
合成都不会产生 Ark 生成费用。

## 文档

- [架构决策](docs/architecture/ADR-001-explicit-workflow.md)
- [完整生产流程](docs/workflows/complete-production.md)
- [放风筝三片段生产运行手册](docs/workflows/kite-production-runbook.md)
- [Windows 手册](docs/workflows/windows-runbook.md)
- [Docker Compose 部署](docs/workflows/docker-deployment.md)
- [HTTP API](docs/http-api.md)
- [V5 升级 Checklist](docs/checklists/v5-creation-flow-upgrade.md)
- [片段视觉与 LLM 辅助 Checklist](docs/checklists/v5-shot-assistance-upgrade.md)
- [分阶段 LLM 创作工作流 Checklist](docs/checklists/v5-staged-creative-workflow.md)
- [LibTV 编排适配 Checklist](docs/checklists/v5-libtv-editing-upgrade.md)
- [设计脚本教程适配说明](docs/workflows/tutorial-adaptation.md)

`.env`、Ark Key、数据库密码、Base64 和签名 URL 不得进入 Git、日志或诊断 Manifest。
