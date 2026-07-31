# 可见世界融合改造批次——批判性审查报告

> 审查日期：2026-07-30。对象：工作树中约 27 个文件的未提交改动
> （实现《三时段视频系统融合改造计划：L1-L4分层 + 可见世界连续性内核》）。
> 方法：三路并行深读（领域契约层 / 应用与基础设施层 / 测试与文档），
> 关键疑点做了实际运行验证（崩溃复现、误报/漏报复现、Prompt 长度实测）。
> 所有发现均在代码中实证，行号对应当前工作树。

## 时效声明与修复进展（2026-07-30 补充）

**本报告反映的是三路分析时刻的工作树快照；落盘前复核发现工作树已向前推进
（另一会话已实施部分修复批次），下文行号以分析时刻快照为准。**
已对当前工作树逐条核验的修复进展：

| 原发现 | 当前状态 | 证据（新工作树） |
|---|---|---|
| P0-1 校验器 KeyError 崩溃 | **已修复**（契约级拦截） | continuity.py:211-220：state_entity_id 引用未建账实体/未列入 targets 直接 ValueError；配合 no_state_change 标志，崩溃路径消除 |
| P0-2 Segment↔Shot 无一致性校验 | **已修复** | contracts.py:451-454：片段动作必须与对应镜头完全一致、仅第二段可要求尾帧链接 |
| P0-3 失败后永久锁死 | **已修复**（显式 retry-step 设计） | 新 application/retry.py（"从不可变旧Step创建带来源记录的新attempt"）、ports.py:352 next_step_attempt、repositories.py:200-207（"Segment序号不再冒充重试次数"）、errors.py、production.py:123（"run-day不会隐式创建新的收费attempt"） |
| P0-4 语义拒绝永久阻断 | **已修复** | visual_preparation.py:426（"显式创建关键帧新attempt；拒绝资产和旧Prompt始终保留"）；:218-224 复用改为 find_reusable_asset + input_hash + 状态不含 rejected（顺带修复原 A-L3 复用不校验 input_hash） |
| P0-5 review_asset 跨事务 | **未复核** | 需对新树重新检查 assets.py:187-244 |
| P0-6 拼接容差不匹配 | **部分修复** | multi_clip_finalization.py:234 已有 next_step_attempt（finalize 可新 attempt）；分段/整片容差对齐未复核 |
| P0-7 镜头文本不扫描 | **已修复** | rules.py:233-253：`_episode_script_text` 已纳入 shot.framing/direction 与可见世界文本 |
| P0-8 子串误报 | **已修复**（白名单方案） | rules.py:262：`protected_terms = ("少年宫", "马尾松")` |
| P0-9 空壳 transition 绕过 | **已修复** | rules.py:204-211：`missing_state_entity` HARD 门 + `no_state_change` 显式标志（continuity.py、director_prompts.py 同步） |
| conftest 夹具"女孩" | **已修复** | conftest.py:71 已改为"中性儿童" |

同时新增的文件：application/retry.py、application/errors.py、
application/resolution_comparison.py、domain/director_prompts.py（Prompt 编译拆分）。

**注意**：P1/P2 清单与"修复计划四批次"基于分析时刻快照，行号可能已漂移；
引用前请对新树做增量复核（特别是 P0-5、P0-6 容差、D-M2/M4/M6/M8、
生产执行层测试缺位——test_visible_world_and_assets.py 之后是否新增执行层测试
未复核）。

---

## 总体判断（分析时刻快照）

**方向正确、骨架扎实，但当前状态不可直接用于真实付费环境。**

分层契约 + 纯函数重放 + extra="forbid" + 供应商形状归一化 + semantic_key 精确寻址
的架构选择是正确的，与行业通行做法（火山剧创官方管线）同向。但存在：
1. **校验器自身会被合法输入击穿**（P0-1/P0-2）——与"矛盾阻断、风险诊断"的设计直接违背；
2. **失败恢复模型缺失**（P0-3~P0-6）——任何一次确定性失败都倾向把 Episode 推向
   不可恢复状态，且错误表现形式都不指向正确处置；
3. **身份门控既漏又冤**（P0-7/P0-8）——漏掉镜头文本，冤枉合法地名，
   误报代价是整日付费规划作废；
4. **连续性内核可被空壳 transition 合法规避**（P0-9）——目前真正起强制作用的
   主要是 ID 引用完整性，连续性语义的覆盖取决于导演是否认真填字段；
5. **生产执行层零自动化测试**——正确性完全押注在两份人工 real-test 记录上。

## 已验证做对的部分（骨架可信）

- **模块真实拆分**：production.py 只做编排与状态推进；选图/关键帧/审核在
  visual_preparation；收费任务/QC/拼接在 video_execution / multi_clip_finalization。
  workflow.py 仍是唯一状态转换表，未引入 LangGraph 或新状态源。
- **semantic_key 四重隔离**：可空迁移 + 回填 + `legacy:<assetId>` 隔离 +
  (scope, semantic_key, status, created_at) 索引（models.py:301-307、迁移 0003）；
  只选最新已批准；候选/拒绝/旧Run/归档全排除；负向样本 Run c92de15a
  （episode 作用域 + archived + semantic_key NULL）三重排除，不可能被新 Run 复用。
- **幂等与冻结**：意图先行、重复执行复用原 Step（规划侧有强测试）；
  submission_unknown 冻结不重发（转换表禁止回 SUBMITTING）。
- **recent_summaries 接线正确**：仅 READY/DELIVERED 且有 plan 的最近 6 个
  （query_repository.py:186-233），失败/未交付/归档排除。
- **事件种子不越界**：content/events/everyday.yaml 存在；sha256 确定性排序取前 3；
  空池不阻断；结果写入 planningMetadata；未新增 DB 表/CLI（符合计划约束）。
- **关键帧审核链路**：9:16 偏差≤1%、首尾帧同尺寸、黑边检测齐全；
  semantic_auto 默认 + confidence≥0.80 自动批准 + 低置信转人工；
  technical_auto 必须显式 `--allow-unverified-keyframes`（visual_preparation.py:254-259）；
  证据只存分数/诊断/帧哈希，无 Base64/URL；审核失败不创建 Seedance 任务。
- **multi_clip 人工门槛**：两段、每段≥4s、总长 8-15s（contracts.py:540-564）；
  高风险动作不跨切点（rules.py:307-328）；必须 `--allow-multi-clip` 且存在
  已人工拒绝的 single_pass 成片（video_execution.py:207-213）；
  每段独立 Step/Prompt/task/资产/审核；尾帧链接要求前段 ready；缺段不产出成片。
- **actor_id 编译落地**：`_compile_shot` 按 actor 分组输出，environment/guest 有明确标签。
- **Prompt 预算现实**：富 transition 夹具实测 583 字符，离 1400 警告线很远。
- **配置同步**：.env.example 与 config.py 一一对应（ARK_REVIEW_MODEL /
  VIDEO_SEMANTIC_REVIEW_MODE / KEYFRAME_REVIEW_MODE），旧值 auto 有迁移警告。
- **规划层测试强**：精确错误消息、事件顺序、状态机终态断言；103 个测试计数可信。

## P0 高危发现（合并阻断级）

### 主题一：校验器自身会崩溃

**P0-1 `validate_visible_world` 对合法契约输入抛 KeyError，击穿越规划 Run 并绕过修复循环**
- 位置：continuity.py:316-318（`entities[state_entity_id]`），根因
  continuity.py:543-545（`resolve_transition_state_entity_id` 回退分支）。
- 场景：target_entity_ids 允许包含 scene anchor；transition 未填 state_entity_id 且
  actor 未建账时，回退返回第一个 target（可能是 anchor id）→ `entities[anchor_id]`
  KeyError。已复现：anchor `table-1` 作唯一 target、无 state_entity_id 的 transition
  通过 model_validate，validate_visible_world 抛 `KeyError: 'table-1'`。
- 后果：plan_day 通用 except 把 Run 置 FAILED、**绕过自动修复循环**；
  同一函数被 records.py:102 在只读查询路径调用，脏数据让查询 API 500。
- 修法：回退只从 entities 键中选择（过滤 anchor）；或解析后判
  `state_entity_id not in entities` 时追加 issue 并 continue，把崩溃降级为矛盾报告。

**P0-2 SegmentPlan 与 ShotPlan 无结构一致性校验，编译期 StopIteration**
- 位置：contracts.py:540-564（multi_clip 校验不查 segment.shot_order）；
  prompts.py:321（`next(...)` 无默认值）。
- 场景：`shot_order=3` 的片段通过全部契约校验（multi_clip 强制恰好 2 个 shot），
  compile_segment_video_prompt 抛裸 StopIteration；两片段同指一个 shot 或顺序颠倒
  同样不被拦截。
- 修法：EpisodePlan 校验 `segments[i].shot_order == shots[i].order`；
  `next(...)` 加默认值并抛业务异常。

### 主题二：失败恢复模型缺失（同根：attempt 硬编码 + FAILED 终态 + 复用查询含 rejected）

**P0-3 付费 Step 失败后永久锁死，无新 attempt 重试路径**
- 位置：video_execution.py:129-171（single_pass attempt=1）、:309-348
  （segment attempt=order）、visual_preparation.py:189-212（image attempt=1）。
- 场景：幂等键 = run|episode|kind|attempt|input_hash。Step FAILED 后相同输入重试
  命中原 FAILED Step：single_pass/image 触发 `WorkflowTransitionError(failed→submitting)`
  （workflow.py:192 只允许 FAILED→ARCHIVED）；segment 显式 raise
  （video_execution.py:347-348）。Seedream 一次比例偏差 1.2% 就让该 Episode
  在相同 Canon 下永远无法再生成关键帧。
- 修法：仿 next_director_attempt，对 FAILED/EXPIRED/CANCELLED 的现存 Step
  递增 attempt 生成新幂等键。

**P0-4 语义审核拒绝的关键帧永久阻断 Episode，无再生/覆盖路径**
- 位置：visual_preparation.py:172-179（existing 查询含 "rejected"）、
  :100-103, :118-121。
- 场景：Ark 审核模型误判 `worldStateOk=false`（confidence=0.9）→ 资产 rejected、
  Step FAILED → prepare 每次 `raise RuntimeError("首帧语义审核失败…")`；
  人工无法覆盖（见 P0-5），也没有"携带 violations 重新生成"的修复循环。
- 修法：existing 查询排除 rejected；或 rejected 为最新时允许一次带反馈的再生
  （新 attempt）；人工可显式把 rejected 帧标记为 ready 并闭环 Step 状态。

**P0-5 `review_asset` 跨三个独立事务，状态校验在写入之后**
- 位置：assets.py:187-217、:224-244。
- 场景：record_review（事务1）→ set_asset_status（事务2）→ 才校验
  `step.status is AWAITING_REVIEW`（事务3，抛错）。人工批准被拒绝的关键帧时：
  CLI 报错，但资产已 ready、审核已落库——留下 Step=FAILED/资产=ready 的
  矛盾审计态，且下次 run-day 会拿着这个 ready 资产继续生成。
- 修法：先读 Step 校验 AWAITING_REVIEW，再在同一事务内完成
  review + asset + step + episode 四处写入。

**P0-6 拼接 QC 窗口与分段 QC 窗口不匹配，finalize 失败后不可重做**
- 位置：video_execution.py:431-437（分段时长容差 ±1000ms）；
  qc.py:103-108（整片 ±1000ms 且硬上限 15000ms）；
  multi_clip_finalization.py:128-131（FAILED 即拒绝重做）。
- 场景：15 秒方案拆 7+8，两段各 +0.9s 均通过分段 QC → 拼接后 16.7s →
  `duration_invalid` → finalize FAILED、Episode FAILED，且不可自动重做——
  两段已付费、已人工批准的素材永久锁死。
- 修法：分段 QC 容差收紧（如 ±400ms）或整片放宽到分段容差之和；
  finalize 失败后允许新 attempt（本地后期不重提 Ark，重试安全）。

### 主题三：身份门控既漏又冤

**P0-7 性别/身份词门结构性盲区：镜头文本与实体显示名不扫描，原文直达收费 Prompt**
- 位置：rules.py:199-209（`_episode_script_text` 只拼 main_event/scene/appearance/
  ending/actions）；prompts.py:384-393（`_compile_shot` 把 shot.direction 原文嵌入
  视频 Prompt）；prompts.py:465-502（display_name/appearance_signature 经
  `_world_transition_lines` 进入 Prompt）。
- 已复现：conftest.py:69 的 direction 含"女孩"，validate_plan_gate 返回空 issues，
  而编译出的视频 Prompt 中 `"女孩" in text == True`。
- 修法：`_episode_script_text` 纳入 shots[].direction/framing 及 visible_world 中
  person/cat/guest 类实体的 display_name/appearance_signature。

**P0-8 性别词门子串匹配误报，且 plan 级失败无修复路径**
- 位置：rules.py:80-95（`term in searchable` 子串匹配）；
  planning.py:490-497（`_assemble_plan` 抛 RuntimeError）。
- 已复现：scene 写"路过少年宫门口的广场"→ `gendered_identity_rewrite` HARD。
  禁用词表（visual_profiles.py:36-44）含"少年""少女""马尾"，对"少年宫""马尾松"
  必误报。且 plan 门在四个**付费**导演调用全部完成后才跑，误报直接
  RuntimeError → Run FAILED，已付费导演输出全部作废、无自动修复循环。
- 修法：禁用词加词边界或白名单（或"少年/少女"降级 WARNING 由人工裁定）；
  plan 门失败映射到可重规划通道。

### 主题四：连续性内核可被空壳绕过

**P0-9 空 transition 合法过门——每条 ActionTransition 不强制任何状态字段**
- 位置：continuity.py:88-122（全部字段可选）；rules.py:170-182
  （只校验 transition 的 action_order 集合等于动作集合）。
- 已验证：conftest.py:116-131 的夹具 transition 只有 action_order/shot_order/
  actor_id，无任何 target/锚点/支撑字段，validate_visible_world 返回空、门全绿——
  这套夹具正是全套通过测试的基础，**状态重放逻辑从未被负面测试过**。
- 后果：导演学会"每条动作交一条空壳 transition 就能过门"后，实体使用前存在性、
  支撑合法性、before=上一结束状态等全部规则失效——与"校验是纯函数而非
  LLM 自觉"的设计目标直接冲突。
- 修法：要求每条 transition 声明 target_entity_ids 非空或显式
  `no_state_change: bool` 标志；或对"visible_result 暗示物理变化但 transition
  无任何状态字段"发 WARNING。

## P1 中危发现

| # | 位置 | 问题 | 修法 |
|---|---|---|---|
| D-M1 | continuity.py:95-98, 528-545; prompts.py:161-163, 446, 478 | state_entity_id"必须填"只是 Prompt 口号；契约静默回退且回退结果张冠李戴；门控与 Prompt 编译两处解析语义不一致 | 导演草稿路径强制必填（或 rules 门 HARD）；统一两处解析 |
| D-M2 | continuity.py:316-335 | 生命周期只查 resolve 出的单个实体；targets 引用已离场实体不报；支撑物 exit 后悬空支撑仍判合法 | 重放对全部 targets 做活性检查；生命周期事件后扫描指向失活实体的引用 |
| D-M3 | continuity.py:445-447 | actor 未建账时坐下/站起校验整体跳过 | actor 未建账先报矛盾再 return |
| D-M4 | continuity.py:462-483 | before=上一结束状态只在双方均声明时校验；current 未知时用声明 before 回填，矛盾被系统性掩盖 | 回填分支改为矛盾报告 |
| D-M5 | prompts.py:295-299; video_execution.py:123 | Prompt 预算在执行期才检查；契约上限理论组合可超 2000，规划期零感知、无修复路径 | 规划门内用空 bindings 预编译一次，超预算提前为可自动修复的 HARD |
| D-M6 | contracts.py:523-527; provider_normalization.py:26-47 | 同一动作可出现于多镜头：continuous_shot 推导失效、跨切点检查语义崩塌、shot_states 归属含糊 | 契约强制动作-镜头一一对应，或重写三处逻辑 |
| D-M7 | director_execution.py:95-99; contracts.py:478-493 | 旧成功步骤回放用新契约校验旧输出→裸 ValidationError→Run FAILED；from_scene_props 丢 final_placement、同名 anchor 重复、support 匹配恒 None | 回放 ValidationError 包装为可重规划错误；保留 final_placement、按 placement 去重 anchor |
| D-M8 | rules.py:23,80-84; planning.py:36,122-129; visual_preparation.py:127-141,180-184; visual_profiles.py:36-44,73 | 视觉档案"可配置"是假象：全链路硬编码 DEFAULT；StyleProfile 三个 reference_key 是死配置；"中性儿童"默认与禁词"少年/少女"语义冲突 | profile 提升为 Service 构造依赖全链路传递；选图键读 profile；统一人设决策并同步禁词表 |
| A-M1 | finalizer.py:159-169; multi_clip_finalization.py:132 | ffmpeg 缺 remux 层，策略静态选定、失败后无升级；与 P0-6 叠加锁死 | finalize 内按 stream_copy→audio_transcode→transcode 升级重试 |
| A-M2 | rules.py:332-376; planning.py:257-261 | 冷却用子串匹配（docstring 称"完全相同"）；短地点词必误中、措辞略变即绕过；且在四个付费调用全部完成后才校验，Run FAILED 无指引 | 摘要结构化后精确等值比较；每生成一个 Episode 立即冷却校验；违规映射可重规划通道 |
| A-M3 | planning.py:99-101; bootstrap.py:182-186; event_seeds.py:67-68 | 事件种子目录为 CWD 相对路径；非仓库根目录运行时种子静默为空，被"空池不阻断"设计掩盖 | 配置化或按仓库根解析；缺失时 preflight 暴露 warning |
| A-M4 | video_execution.py:162-163; production.py:114-115 | 人工拒绝成片后重跑 run-day 静默返回"视频步骤已经成功"，Run 停 GENERATING 无提示 | 检测 Episode FAILED + Step SUCCEEDED 组合，返回明确 nextAction |
| A-M5 | production.py:55-56; workflow.py:101-106 | Run=FAILED 且有 plan 时 run-day 结尾必然 WorkflowTransitionError——在所有付费工作完成后 | run_day 入口把 FAILED 与 PLANNED 同等处理（转换表已允许 FAILED→GENERATING） |

## P2 低危/卫生清单

- prompts.py:544-552：`_duplicated_element_ids` 用英文 slug 与中文 subject 互做子串匹配，
  几乎永不命中——"物理关系只描述一次"未达成。
- continuity.py:161-168：只查 entity_id/anchor_id 唯一，不查 display_name 唯一。
- rules.py:311-318：`transition.action_order in action_to_shot` 恒 True，死条件。
- continuity.py:337-347：未要求 transition.shot_order 随 action_order 单调不减，
  乱序会污染 shot_states 比较基准。
- rules.py:96-98+111-116：`_fixed_cast_count_issue` 在 plan 门和 episode 门重复上报。
- 死字段：SceneAnchor.appears_from_shot/persists、TrackedEntity.count、
  appearance_signature（transform 结果签名存而不用）、multi_clip_recommended
  （只展示不回喂）。
- continuity.py:371-381：`before=None, after=X` 即计物理变化，渲染风险系统性高估
  （仅 WARNING，影响有限）。
- contracts.py:148-152：ActionStage.actor_id 默认 "person"，旧归档猫咪动作被静默归到人物。
- prompts.py:203-210：跨时段摘要用 initial_anchor + 声明 lifecycle，
  传递的是过期世界状态（非重放终态）。
- prompts.py:111-123,142：repair="" 仍占空行；修复 Prompt 内嵌完整被拒候选 JSON
  无长度预算。
- contracts.py:444-446：cast 第三槽任意合法字符串即可，不必须是 "guest"。
- archive_import.py:155-222：归档导入不写 semantic_key（功能上仍被排除，
  但与 legacy 隔离约定不一致，且无任何提示）。
- config.py:238-270：ARK_REVIEW_MODEL 无视觉能力校验，配错文本模型时
  semantic_auto 名存实亡。
- visual_preparation.py:172-179：关键帧复用不校验 input_hash，Canon 更新后
  仍复用旧帧（若属有意设计需文档写明）。
- event_seeds.py:37-64：EventSeed.slots 字段在筛选中未使用（死重）。
- visual_preparation.py:400-426 / qc.py:44-53,96-102：关键帧技术硬门缺独立
  "UI 损坏"检测；视频 QC 比例容差（绝对 0.02）比关键帧（相对 1%）宽松约 3.5 倍，
  口径不一致。

## 测试缺位与文档不实

**Test Plan 覆盖矩阵**（对照融合计划 11 项）：

| # | 项目 | 结论 |
|---|---|---|
| 1 | 中性人物规则（拒绝固定女孩/发髻/马尾，允许有因换装） | 强断言覆盖；但只触发"固定女孩/马尾"两词 |
| 2 | StyleProfile 缺参考/含人物UI/3D倾向不得自动批准 | **机制不存在**：import_canon 直接 approved（assets.py:61），无内容检查 |
| 3 | 错误 Element 无法经 semantic_key 选中 | 强断言覆盖（legacy: 前缀隔离无测试） |
| 4 | VisibleWorldPlan 六类拒绝 | **部分**：无支撑/未声明座椅强；切镜冲突弱；**容器混用、无原因消失、复制、形变零覆盖** |
| 5 | 高风险跨切镜规划失败 | **与最终决策矛盾**：代码明确"风险永不升级为硬门"（continuity.py:354）；唯一相关硬门 validate_generation_strategy 的 physical_change_crosses_cut（rules.py:311-328）零测试 |
| 6 | 关键帧尺寸/语义错误不建视频 Step | 实现有，**测试零覆盖** |
| 7 | 幂等复用 / submission_unknown | 规划侧强；**视频侧零覆盖**（仅一份人工 real-test 记录） |
| 8 | 普通 Episode 用 single_pass | 弱（契约默认有测试，运行时行为无测试） |
| 9 | multi_clip 显式/两段/缺段 | **大部分零覆盖**（门槛检查、缺段不拼接、尾帧抽取均无测试） |
| 10 | 语义诊断失败保持 content_review | 结构性成立（video_diagnostic 不改状态），**零测试** |
| 11 | 历史 Run 可查不可复用 | SQL 层有隔离，**repository 层零测试** |

**文档不实**：
- docs/checklists/visible-world-integration.md:54-55 声称"变形/消耗和高风险跨切镜
  均有收费前负向测试"——与实际不符（变形零测试、消耗只有正向、跨切镜只有诊断级）。
- 同文件 :56-58 "复杂度硬门"措辞过时（复杂度预算已删除），与
  windows-runbook.md:161"渲染风险只供判断，不会自动阻断"自相矛盾。
- Test Plan 第 5 条本身与"风险永不阻断"的现行架构决策冲突，应重新表述。
- conftest.py:69 夹具 direction 含"跟随**女孩**和灰白猫"（与中性人设口吻不一致，
  且正是 P0-7 的实证）。
- test_visible_world_and_assets.py:486 只断言首条 issue 子串，断言偏弱。

**事实修正**（融合计划文本与工作树不符之处）：
- `tests/fixtures/episode_draft_self_inconsistent.json` 不存在；修复循环测试在
  test_planning_service.py:414-415 内联构造。
- `content/series/life-v1.json` 不存在；人设/画风档案硬编码于 visual_profiles.py:71-96。
- 新增测试文件实为 tests/test_visible_world_and_assets.py（765 行）。
- content/bibles|templates 等删除发生在已提交的 90c0244，非本批改动；全仓零残留引用。

## 修复计划（四批，每批独立可交付）

**批次 1：校验器崩溃修复（P0-1、P0-2、D-M7a）**
- continuity.py：回退解析过滤 anchor；无法解析时降级为矛盾 issue。
- contracts.py：segments↔shots 结构一致性校验；prompts.py 的 `next()` 加默认值。
- director_execution.py：回放 ValidationError 包装为可重规划错误。
- 测试：P0-1/P0-2 崩溃输入回归用例。

**批次 2：失败恢复模型（P0-3~P0-6、A-M1、A-M4、A-M5）**
- 统一"失败后新 attempt"机制（仿 next_director_attempt），覆盖 single_pass/segment/image。
- visual_preparation：existing 查询排除 rejected；拒绝后允许一次带反馈再生；
  人工可覆盖 rejected→ready 并闭环 Step。
- assets.py：review_asset 单事务 + 先校验后写入。
- 拼接容差对齐（分段收紧或整片放宽到分段之和）；finalize 失败允许新 attempt；
  ffmpeg 失败按 stream_copy→audio_transcode→transcode 升级。
- production.py：run_day 入口接纳 FAILED Run；人工拒绝成片返回明确 nextAction。

**批次 3：门控语义（P0-7、P0-8、A-M2、D-M5、A-M3）**
- _episode_script_text 纳入镜头文本与实体显示名/外观签名。
- 禁用词词边界/白名单；plan 门失败映射可重规划通道。
- 冷却：结构化摘要精确等值比较 + 每 Episode 生成后立即校验。
- 规划门内预编译视频 Prompt 做预算预检。
- 事件种子路径配置化 + preflight 警告。

**批次 4：内核防绕过 + 测试补齐（P0-9、D-M1~M4、D-M6、D-M8、测试缺位）**
- transition 非空强制或显式 no_state_change；state_entity_id 导演草稿必填；
  targets 全量活性检查；before 回填改矛盾；动作-镜头一一对应。
- visual profile 提升为 Service 构造依赖；选图键读 profile；统一人设决策。
- 生产执行层测试：VideoExecutionService 幂等/submission_unknown、关键帧尺寸与
  语义阻断、multi_clip 三道门槛、transform/containment/缺 change_reason 负向。
- 文档修订：checklist 两处不实声明、Test Plan 第 5 条重新表述、
  conftest "女孩"夹具改中性、:486 断言加强。
