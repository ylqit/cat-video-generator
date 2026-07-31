# 可见世界连续性融合改造 Checklist

本清单用于跟踪“L1-L4 分层 + 可见世界连续性内核”的本地落地状态。
`c92de15a-a688-44b5-9186-b01c266ad2bd` 是只读负向回归样本：不得覆盖、
不得交付，也不得作为新 Run 的活动参考资产。

## Batch 0：基线保护

- [x] 记录当前工作树和未提交改动，未执行 reset 或覆盖。
- [x] 将 pytest 临时目录固定到被 Git 忽略的 `var/test-tmp`。
- [x] 基线非付费测试通过；测试数量以当前 `pytest` 输出为准，不在清单中固定旧数字。
- [x] 固定负向 Run 的 Prompt 与联系表哈希（见文末）。
- [x] 建立失败样本：中午物体穿透/座椅凭空出现/食物变形；傍晚道具消失/
  服装丢失/座椅和茶壶凭空出现。

## Batch 1：领域契约

- [x] 实现 `SeriesVisualProfile`、`StyleProfile` 和 `VisibleWorldPlan`。
- [x] 将旧 `SceneProp` 兼容输入归一化为场景锚点和跟踪实体。
- [x] 接通最近 6 个已批准或已交付 Run 摘要。
- [x] 实现事件种子、地点和关键道具冷却。
- [x] 增加结构化 `dominantView`；固定物理次数预算已改为非阻断渲染风险诊断。

## Batch 2：资产与数据库

- [x] 增加 `assets.semantic_key` 迁移和索引。
- [x] 回填可确认 Canon；无法确认的资产隔离为 `legacy:<assetId>`。
- [x] Canon 导入必须提供合法语义键。
- [x] 修复按全局 role 选择最新元素的错误。
- [x] 持久化 `ReferenceSelectionPlan`、别名、顺序与哈希。

## Batch 3：Prompt 与审核

- [x] 改为 `actorId` 驱动的动作和镜头 Prompt。
- [x] 去除重复关系与严格帧模式下未定义的主体别名。
- [x] 图片 Prompt 固定 9:16 并使用结构化视觉档案。
- [x] 增加图片 9:16 与首尾尺寸一致的技术硬门。
- [x] 实现独立 `VisualReviewGateway`。
- [x] 默认关键帧审核切换为 `semantic_auto`。
- [x] `technical_auto` 保留显式实验安全门。

## Batch 4：生产服务

- [x] 拆分 `VisualPreparationService` 和 `VideoExecutionService`。
- [x] `ProductionService` 仅拥有工作流编排。
- [x] 保持 `single_pass` 为默认策略。
- [x] 实现最多两段且必须显式选择的 `multi_clip` 合同、审核和条件式合成。
- [x] 保持幂等、短事务和 `submission_unknown` 冻结。

## Batch 5：原融合版本的验证与文档

- [x] 更新架构 ADR、完整工作流、供应商说明和 Windows 运行手册。
- [x] 原融合版本曾通过当时的全量单元/架构测试、迁移 head、Ruff 和秘密扫描；
  稳定化后的最终结果见下方“合并门槛”，不以旧记录替代当前测试。
- [x] 当前 noon/evening 的无支撑、切镜丢失、未声明座面和道具无原因消失已登记
  为负向样本；旧 Run 本身保持只读，不重新执行。
- [x] 使用新规划模型和480p运行配置执行真实规划验证；结果在收费媒体任务前被
  旧版复杂度硬门阻断。该硬门现已删除，记录仅作为历史诊断，详见
  [真实验证记录](../real-tests/2026-07-30-planning-model-480p.md)。
- [x] 创建全新非模板化全天 Run `eedf8275-bbb5-464e-a6be-a443c1daaad2`
  并导出13份导演Prompt和3份视频Prompt。
- [x] 显式付费授权后生成三条480p视频；每条只有一个成功的
  `single_pass`视频Step。
- [x] 三条视频停在 `content_review`，未自动 `deliver`。真实任务和诊断见
  [导演主导与状态重放真实验证](../real-tests/2026-07-30-director-state-replay-480p.md)。

## 稳定化 Batch 1：连续性与契约安全

- [x] 锚点不再被解析为状态实体；无法解析时返回业务矛盾，不抛 `KeyError`。
- [x] 状态变化必须声明 `state_entity_id`；纯观察显式使用 `no_state_change`。
- [x] 校验失活实体、悬空支撑/包含、动作前状态、生命周期原因与变形结果。
- [x] 终态由统一状态重放结果提供给后续时段、Prompt 和查询。
- [x] 动作只能属于一个 Shot；multi-clip 的 Segment 与 Shot 一一对应。
- [x] 非法分段对象抛稳定 `PromptCompilationError`，不泄露 `StopIteration`。
- [x] Prompt 预算在时段导演验收阶段进入同一次自动修复循环。

## 稳定化 Batch 2：恢复、审核与后期

- [x] 新增 `cvg retry-step`；图片、视频、分段、分辨率对比和本地合成都使用
  稳定 `operationKey` 与递增 attempt。
- [x] `run-day`只报告精确失败 Step 和下一命令，不隐式创建第二次收费任务。
- [x] `submission_unknown`继续冻结，只能对账。
- [x] 关键帧复用匹配 Prompt、有序参考和 Canon/Style SHA-256，且排除 rejected。
- [x] 人工、语义自动和视频审核共用带行锁的单事务提交入口；相反决定不可覆盖。
- [x] FFmpeg 按 stream copy → 仅音频转码 → 完整转码升级；超长结果本地规范化，
  缺段或明显偏短不产出成片。
- [x] 本地 Finalize 失败可创建新 QC attempt，已付费片段保持不可变复用。

## 稳定化 Batch 3：导演门控与配置

- [x] Planning、视觉准备和视频执行显式接收 Series/Style Profile。
- [x] 风格资产键从 StyleProfile 读取，不再按全局 role 或硬编码最新资产选择。
- [x] 中性人物检查覆盖动作、镜头和实体字段，并保护“少年宫、马尾松”等非人物词。
- [x] 冷却使用 `eventKey`、`locationKey` 和元素 `semantic_key` 精确比较。
- [x] 后续时段读取统一状态重放终态，不再读取初态。
- [x] `CAT_VIDEO_EVENT_SEED_ROOT`相对路径按配置根解析；缺失只产生 doctor 警告。
- [x] 事件种子的天气、上下文和 Slot 参与筛选，空目录不阻断导演原创。

## 稳定化 Batch 4：测试与合并门槛

- [x] 增加状态重放、生命周期、Shot/Segment、Prompt预算、重试、关键帧复用、
  Run恢复和FFmpeg升级链回归测试。
- [x] PostgreSQL审核事务、并发幂等和operationKey测试默认使用一次性
  PostgreSQL 16；显式授权时可使用远程唯一`cat_video_test_<runId>` Schema，
  始终不接触正式`cat_video`业务对象。
- [x] `uv run ruff check .`
- [x] `uv run pytest -q`（133 passed；默认Docker模式4项明确skip，已由下一项远程实测覆盖）
- [x] `uv run pytest -m postgres -q`（远程隔离Schema：4 passed）
- [x] `git diff --check`（仅Windows行尾转换提示，无空白错误）
- [ ] 再次获得显式授权后只运行一次真实 Ark `plan-day`；不得创建Seedream或
  Seedance任务。

## 只读回归资产

- 负向 Run：`c92de15a-a688-44b5-9186-b01c266ad2bd`。
- 480p 全天 Run：`eedf8275-bbb5-464e-a6be-a443c1daaad2`。
- 已存在于 PostgreSQL 和本地资产目录的480p/720p分辨率比较任务与媒体继续只读，
  本次稳定化不修改、不删除、不重新提交。

## 负向样本 SHA-256

```text
55d8acde945bf247b5c58db561c0e05c82a57cbfa90e27106533b835012ca7be  00-day-director.txt
d9b25993bb469e1fca05397ad9b0d67b82e5bf98ed4f1cb662932acd9b905efb  01-morning-director.txt
183da8b557c0d898fb0750265909659a008a17032dae9cb1d71a7cac09db8403  02-noon-director.txt
4d168d7d9a21a044e96299cce074b2a44f74a618c74c7e4336ec585f1b48037f  03-evening-director.txt
5bef3ac2f9ea325650de75bc2d15d044e325a8ba79b85fee73b49b106c25391e  10-morning-first-frame.txt
7d0cab1cfc6c16a9c7634300a504bf5fe69c037cbd89a0983d4bdfb193aaade8  11-morning-last-frame.txt
dd9ecdbccf34cd43d8d8d63fd74617393be2ca105774eed58ede5e710e0e44d8  12-morning-video.txt
bfb70f424fc3ac20df13405df0177ca630894d456b721028a02bb85b9293d122  20-noon-first-frame.txt
dacd4cd6196a05319a09443e53e7bc46ac0e17323d8158ff29b4f38af217cb76  21-noon-last-frame.txt
99c7310efdd5156c7cbaadf3c86da309df77ba1aced222aeb278c68632e41d72  22-noon-video.txt
c3f269e8fb5a894d777faad4ad5526e5879024dc14be0c30e146ca5aa94685f9  30-evening-first-frame.txt
97732d14a0e787f624377f57fca9cdc4cadd48bff8bc92adb0dd474e6bb4cd56  31-evening-last-frame.txt
9bc8d0f5548fd740d62a3ae9c1ddce6d2456f84106484137471fe09f8e9359e2  32-evening-video.txt
a067f09f6b37ae4e204cd0cde2bc895537e621ad3ce1d634ae68621f002d42f2  evening-contact.jpg
0ad5449653156c80e9d3a47d816e9cea779e4ae9b56393d924febc493188135c  morning-contact.jpg
76fb2ecc153feda80f002da33fae2fd47f1a2d17403b211cfc8bbcc6911ed6e1  noon-contact.jpg
```
