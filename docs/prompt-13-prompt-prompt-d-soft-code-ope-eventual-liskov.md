# 视频生成质量改造：按行业通用 L1-L4 分层归位

## Context

一次实际运行（`var/prompts/c92de15a-a688-44b5-9186-b01c266ad2bd/`）暴露四类问题：
人设偏女性、画风偏 3D、中午视频道具穿帮（篮子穿桌/幽灵椅子/食物形变）、
傍晚视频镜头间漂移（茶袋消失/开衫缺失/幽灵长椅/茶壶变形）。

经行业调研（[docs/research/aigc-short-drama-pipeline-comparison.md](docs/research/aigc-short-drama-pipeline-comparison.md)，
含火山剧创/趣吉AIGC/FilmAgent 联网核实）确认：这些问题不是孤立 bug，而是
本项目架构偏离了 AIGC 漫剧/短剧行业已收敛的通用五层管线。**火山剧创（火山引擎
官方企业级短剧平台，绑定与我们相同的 Seedance 2.0 + Seedream 5.0 lite）的官方管线
"剧本解析→资产设定→分镜生成→视频合并"完整印证了 L1-L4 分层**。本计划按通用分层
重述全部改造项。

行业核心结论（详见调研文档）：
- 一致性靠 **L2 资产注入 + L3 关键帧关卡**解决，不靠生成模型自觉；
- **逐镜头生成 + 剪辑合成**是无争议共识；镜头衔接标准做法含"真实尾帧复用"
  （火山剧创分镜参考素材明确支持视频帧）；
- 分段按秒计费下成本持平，且失败重试粒度变细（废 4 秒而非 12 秒）；
- 平台侧普遍缺"世界状态/角色记忆"——本项目的日更连续状态是差异化优势。

用户已确认决策：人设仅文字锚点暂不换图；画风锚点用 generic 2D 措辞（env 可覆盖）；
方案 C（分段生成）为目标架构且全自动；规划模型切 doubao-seed-2-1-pro-260628。

## 目标架构（与火山剧创官方管线同构）

```
L1 叙事层   事件池抽签 → 总导演(DayBrief) → 时段导演(EpisodePlan)
            ↑ 世界状态台账（近期内容摘要、道具状态、事件冷却）
L2 资产层   Canon（人物/猫 三视图、画风）+ 风格/人设文字锚点 + 场景道具台账(scene_inventory)
L3 镜头层   Seedream 首帧（把关点）→ 逐镜头分段生成（真实尾帧链接）→ 段级 QC
L4 合成层   ffmpeg concat 自动拼接 → 成片入库（全程零人工，technical_auto）
QC 贯穿     rules.py 硬门 + 关键帧审核 + 段级连续性校验 + 抽卡预算纪律
```

---

## L1 叙事层：数据驱动的剧情（替代"LLM 即兴"）

**病根**：剧情 100% 由 LLM 从静态 `--context` 即兴；`recent_summaries` 参数从未接线
（planning.py:94-97 空调用）；无事件池/冷却 → 题材收敛刻板（反复采茶）。
原则：**"演什么"系统抽签决定（可审计），"怎么演"LLM 负责**。

1. **事件池 EventCard**（新 `domain/events.py` + `content/events/*.yaml` 种子池 +
   `cvg events import` 入库）：`event_id、kind(observation|micro_event|continuation)、
   allowed_slots、beat_pattern、required_props（直接喂 L2 的 scene_inventory）、
   季节/天气标签、weight、cooldown_days、invariants、forbidden_patterns`。
   复活 git 历史中 micro-event.v1/observation.v1 的正确设计。
2. **确定性抽签**（planning.py `plan_day()` 调总导演前）：`seed=sha256(内容日期)`
   加权抽 3 张卡；过滤 allowed_slots / cooldown_days 内未用（repository 查近期计划）/
   季节天气匹配。可复现、可回放。`--events` 手动指定为逃生口，默认全自动。
3. **注入导演**：抽中卡作为硬约束写入总导演 Prompt（含 beatPattern/invariants/
   forbidden_patterns）；`rules.py` 增加 forbidden_patterns 文本扫描 HARD 门。
4. **接线 recent_summaries**：repository 查近 6 次 finalize 计划的 theme/main_event
   填入 `compile_day_director_prompt(recent_summaries=...)`。
5. **跨时段道具状态摘要**：`summarize_episode_state()`（prompts.py:121-138）追加
   `道具=name+placement` 行，noon/evening 导演可感知前序时段物体。
6. **规划模型**：`.env.example:7` 的 `ARK_PLANNING_MODEL` 从 `doubao-seed-2-0-lite-260215`
   同步为 `doubao-seed-2-1-pro-260628`（`.env` 与 config.py:180 已是新值）。
7. **导演指令更新**（prompts.py，详见 L2/L3 各节引用）：scene_inventory 申报义务、
   关系 subject 用 element_id 去重、人设中性指令、分段语义指令。
   不采纳火山剧创的"爆款节奏分镜 Agent"——vlog 定位不需要留存导向剪辑逻辑。

## L2 资产层：资产与台账注入（替代"靠模型自觉"）

### L2.1 场景道具台账 `scene_inventory`（修幽灵椅子/茶杯的核心机制）

- **契约**（contracts.py，`ElementUse` 之后）：
  ```python
  class SceneProp(StrictModel):
      name: Annotated[str, Field(min_length=2, max_length=30)]
      placement: Annotated[str, Field(min_length=2, max_length=80)]
      final_placement: Annotated[str, Field(min_length=2, max_length=80)] | None = None
  ```
  `EpisodeDirectorDraft` 与 `EpisodePlan` 各加
  `scene_inventory: list[SceneProp] = Field(default_factory=list, max_length=10)`
  （有默认值，旧 JSON 计划兼容，无需 Alembic 迁移）。
- **导演指令**（`compile_episode_director_prompt`）：所有镜头/首尾帧出现的家具道具
  必须先列入清单（示例：原木餐桌一张在中央、竹椅两把在左右、白瓷茶杯在石墩茶壶旁）；
  后续镜头不得引入清单外物体，清单内物体不得无故消失/变形/增减。
  `compile_day_director_prompt` 加一行：scene_direction 要点明关键家具道具。
- **覆盖门**（rules.py 新 `validate_scene_coverage()`，在 planning.py:278 后调用）：
  高风险名词表 `("椅子","凳","桌","碗","杯","壶","篮","碟","盘","锅","石墩","沙发",
  "床","柜","架","伞","包","瓶","勺","筷")` 出现在镜头/帧文本但未在清单+scene 声明
  → HARD `undeclared_prop`；清单内道具未被任何文本使用 → HARD `orphan_inventory_prop`；
  element_id 与关系 subject 互为子串 → WARNING `duplicate_relation_subject`。
- **编译注入**（prompts.py）：
  - 图片 Prompt 插入 `【场景布局】{inventory_line}。画面中只出现这些家具道具，不得新增。`
    （首帧追加"必须同时呈现镜头1所需的全部物体"）；
  - 视频 Prompt 设定段加 `场景道具固定为：{inventory_line}。`；
  - 负面清单强化：`禁止物体穿过桌面或地面、家具数量或位置在镜头间变化、镜头切换后
    服装缺失或与身体半融合、道具变形/消失/被替换、关键物体悬空或自动恢复…`；
  - 关系段去重：`_relation_transition_lines()`/`_relation_lines()` 跳过与
    critical_relations.subject 互为子串的 element_uses 行。

### L2.2 画风与人设文字锚点

- `prompts.py` 模块常量：`DEFAULT_STYLE_TOKENS = "二维动画插画风格，平涂上色，手绘质感"`、
  `DEFAULT_STYLE_NEGATIVE = "禁止3D渲染与写实材质"`、
  `DEFAULT_PERSONA_TOKENS = "中性气质的少年感人物，身形纤细，衣着宽松，不呈现明显性别特征"`。
- config.py `RuntimeSettings` 加 `series_style_tokens`/`series_persona_tokens`
  （env `SERIES_STYLE_TOKENS`/`SERIES_PERSONA_TOKENS`，默认取常量）；
  bootstrap.py 注入两个 Service；compile 函数加 `style_anchor=`/`persona_anchor=`
  kwarg（None 回退默认值，现有测试不受影响）。
- 注入点：图片 Prompt【画风与身份】改为 `{style_anchor}；{style_negative}；沿用参考图…
  人物为{persona_anchor}…`；视频 Prompt 身份句后加
  `画面风格：{style_anchor}，{style_negative}。人物为{persona_anchor}。`；
  时段导演指令加 `人物设定为{persona_anchor}；服装、发型与剧情不得暗示或强调性别。`
- **性别词 HARD 门**（rules.py `validate_plan_gate` 禁词扫描扩展）：
  `("女孩","少女","女生","女人","男孩","男子","裙","高跟")` → `gendered_persona_term`。

### L2.3 角色三视图 Canon 扩充（对齐火山剧创角色三视图资产）

**现状**：`derive_canon_crop()` 把 `referenceView` 硬编码为 `"front"`（assets.py:77）；
`_reference_assets()`（production.py:184-200）每角色只选一张 front 视图。
傍晚视频全是背面镜头却只有正面 Canon → 变脸/服装漂移的结构性原因之一。

- **元数据**：`referenceView` 支持 `front|side|back`；`derive_canon_crop()` 加
  `view` 参数；`import_canon()` 加 `view` 参数（默认 front）。
- **CLI**：`cvg canon import --role person --view back --file …`；
  `cvg canon derive-crop --view side`。
- **按镜头视角选择**（`_reference_assets()` 重写选择逻辑）：从
  scene/actions/shots/ending 文本推断主导视角（含"背/背影/身后/远眺"→back；
  "侧/侧身"→side；默认 front），选匹配视图；多模态预算 5 项恰好 =
  人物(front+匹配视角) + 猫(front+匹配视角) + 画风(subjectFree)。
  Seedream 关键帧参考（上限 5 张）用同一选择。
- **缺口警告门**：时段需要 back/side 视角但无对应 Canon → WARNING `missing_view_canon`，
  不阻断（回退 front）但出现在审核记录。
- **Prompt 标注**：`_reference_role_label()` 输出带视角：
  `图1负责固定人物身份与外观（背面视角）`。
- **三视图素材生产工作流**（一次性，人工把关）：用 Seedream 以现有 front Canon +
  画风 Canon 为参考生成 side/back 候选图 → 人工挑选 → `cvg canon import --view …`
  导入。不改生成代码，仅文档化到 runbook。

## L3 镜头层：逐镜头分段生成 + 真实尾帧链接（方案 C，行业默认形态）

**依据**：行业共识"逐镜头生成+剪辑合成"；火山剧创分镜参考支持视频帧；按秒计费下
分段不多花钱且重试粒度变细（12s→4s）；每段单镜头自动落回编译器简单路径，
复杂三段式 Prompt 退役（保留兼容旧计划）。

1. **契约调整**（contracts.py）：
   - `ShotPlan` 加 `duration_seconds: int | None`（4~15，None=旧计划走单次生成旧路径）；
   - `EpisodePlan` 门控：新计划 Σ shots.duration = episode.duration_seconds
     （总时长维持 8~15，如 3 段 = 4+4+4 或 5+4+4）；
   - `CriticalRelation` 加可选 `shot_order` 作用域（段级 QC 用；无作用域=时段级，兼容旧）。
2. **能力档案**：`SeedanceCapabilities.min_duration_seconds` 8→4（media.py:44；
   供应商支持 4~15 秒，volcengine-multimodal.md:19），同步能力快照测试。
3. **导演分段指令**（替换原"高风险时段只用1镜头"保守指令——分段后多镜头不再怕漂移）：
   ```
   每个镜头是一段独立生成的4至15秒视频，必须给出duration_seconds且与动作量匹配
   （4秒约1个简单动作，8秒以上可2至3个动作阶段）。镜头边界必须是动作自然停顿、
   姿态稳定、下一段能从静止首帧直接开始的可切点；除镜头1外，开场动作不得从
   "已在运动中"开始，要从静态姿态自然启动。
   ```
4. **段循环执行**（production.py `_run_episode` 改造，复用 technical_auto 自动批准）：
   - 段1：Seedream 首帧（沿用 `_ensure_image` 路径）→ `strict_first_frame` 提交；
   - 段N：ffmpeg 抽取段N-1 真实尾帧（或 `return_last_frame=True`）入库为
     `segment_frame` 资产 → 作段N `first_frame` 提交；
   - 每段 Prompt 走简单路径，携带外观+场景+scene_inventory+人设/画风锚点
     （每次调用无状态需重建上下文；段 Prompt 更短，预算充裕）；
   - **段级 QC**：每段落袋后校验该段 `shot_order` 作用域内的 critical_relations
     终态（沿用 `validate_critical_continuity` 下沉）；失败只重出该段，
     重出发生在继续链接之前 → 错误不沿链传播；
   - **抽卡预算**：每段重试上限（如 3 次，env `SEGMENT_RETRY_LIMIT`），
     超限置 FAILED 并给出 replan 入口——对齐行业"抽卡预算"纪律。
5. ** sanity 门**：shot 关联 >2 动作阶段且 <6 秒 → WARNING `shot_overloaded`；
   `len(shots)>=2 且 critical_relations 非空` 的**旧路径**计划 → WARNING
   `multi_shot_with_critical_relations`。

## L4 合成层：自动拼接（零人工）

- 全部段 QC 通过后自动执行 `ffmpeg -f concat -safe 0 -i segments.txt -c copy episode.mp4`
  （段清单按 shot order 生成；同模型/720p/9:16/编码 → 流复制秒级完成无画质损失）；
  拼接幂等可重跑，缺段直接拒绝产出。
- 音频：`generate_audio=True` 不变，原生环境音随段拼接；vlog 质感允许轻微跳变，
  可选自动 `loudnorm` 重编码。
- 唯一人工点：最终成片可选复核（非阻断）。无对白定位 → 天然省掉行业最重的
  对口型/配音环节。

## 迁移注意

- `scene_inventory`、`ShotPlan.duration_seconds`、`CriticalRelation.shot_order`
  均有默认值 → 旧 JSON 计划可 `model_validate`，无需 Alembic 迁移（计划 JSON 存储）。
- 无 `duration_seconds` 的旧 ShotPlan 走单次生成旧路径；新旧路径并存一个版本周期
  后评估下线旧路径与复杂三段式 Prompt。
- 编译后 Prompt 文本变化使 `input_hash` 去重失效（production.py:386-390）→
  部署后首跑重新生成关键帧/视频，正是期望行为。
- PromptRecord 为审计数据，不回填。

## 验证

**单元测试**（tests/test_contracts_and_prompts.py、test_planning_service.py、conftest.py）：
1. conftest `daily_plan`：noon 加（餐桌+竹椅×2+面碗）、evening 加（石墩+茶壶+茶杯）
   的 scene_inventory；shots 加 duration_seconds。
2. L2：`test_scene_coverage_rejects_undeclared_chair`（HARD）、
   `test_orphan_inventory_prop_rejected`、`test_relation_lines_dedup`、
   `test_gendered_term_rejected`、`test_image_prompt_contains_style_and_persona_anchors`、
   预算测试更新（注入后仍 ≤1400）。
3. L2 三视图：`test_reference_selection_prefers_matching_view`（文本含"背影"→选 back
   Canon）、`test_missing_view_canon_warns`。
4. L3：ShotPlan 时长校验（4~15、Σ=episode）；能力档案 min=4 快照更新；
   段 Prompt 走简单路径且含清单/锚点；段N 输入计划 first_frame 绑定指向段N-1
   尾帧资产（mock 抽帧）；段级 QC 失败只重出该段。
5. L4：ffmpeg concat 参数与段顺序（mock subprocess）；缺段拒绝拼接。
6. L1：抽签确定性（同日期同结果）、冷却过滤、forbidden_patterns 门、
   总导演 Prompt 含事件卡与非空 recent_summaries（mock repository）。
7. 运行：`pytest tests/ -x -q`。

**端到端冒烟**（分阶段）：
1. 阶段一（L2.1+L2.2+模型同步）后：`cvg plan-day`（context 不含"固定女孩"）→
   检查入库导演输出 scene_inventory 已填、无性别词；`KEYFRAME_REVIEW_MODE=manual`
   跑一天，人工卡首帧（2D 画风、人设中性、道具齐全）。
2. 阶段二（L3+L4）后：`technical_auto` 全自动跑一天 → 查日志链路
   （首帧→段生成→抽尾帧→链接→段级QC→拼接）；对照四类原始 bug 逐项复核成片。
3. 阶段三（L1 事件池+三视图）后：连续跑 2-3 天验证题材不重复、背面镜头不再变脸。
4. 画风仍偏 3D → 先调 `.env` 的 `SERIES_STYLE_TOKENS`；人设仍偏女性 → 评估换
   Canon 人物图（三视图工作流已就绪）。

## 实施顺序（每阶段独立可交付）

| 阶段 | 内容 | 对应行业层 | 关键文件 |
|---|---|---|---|
| 一 | scene_inventory 契约+门控+编译注入、画风/人设锚点、关系去重、.env.example 同步 | L2 | contracts.py, rules.py, prompts.py, config.py, bootstrap.py, .env.example |
| 二 | ShotPlan 时长、分段生成+尾帧链接+段级 QC、ffmpeg 拼接、能力档案 min=4 | L3+L4 | contracts.py, media.py, production.py, prompts.py, rules.py |
| 三 | 事件池抽签+冷却+recent_summaries 接线、三视图 Canon（元数据/选择逻辑/CLI/runbook） | L1+L2 | 新 domain/events.py, planning.py, prompts.py, rules.py, assets.py, production.py, cli.py, content/events/*.yaml, docs/workflows/windows-runbook.md |
