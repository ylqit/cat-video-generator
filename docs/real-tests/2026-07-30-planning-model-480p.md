# 2026-07-30 Ark 真实链路验证记录

## 结论

本次真实验证已调用 Ark 规划模型，但没有进入 Seedream 或 Seedance。

新的规划模型能够生成全天提案，但上午 Episode 连续多次超出当前
`VisibleWorldPlan` 的物理复杂度预算。系统在创建图片或视频收费步骤前阻断，
因此没有生成 MP4，也不能据此评价 480p 的画面效果。

这是一项有效的失败验证：

- `.env` 中的规划模型、视频模型和分辨率已经被运行时正确读取。
- 导演 Prompt 在外部调用前已经写入 PostgreSQL。
- 非法导演输出不会创建 Episode、关键帧或视频任务。
- 失败 Run 会进入 `failed`，不会残留为可领取的 `draft`。
- 恢复规划只复用已成功的 DayBrief，不会重复调用总导演。
- 本次没有 Seedream、Seedance、媒体下载或交付任务。

## 实际运行配置

```text
Ark access mode: standard
Planning model: doubao-seed-2-1-pro-260628
Image model: doubao-seedream-5-0-260128
Video model: doubao-seedance-2-0-mini-260615
Video resolution: 480p
Keyframe review: semantic_auto
Video semantic review: diagnostic
PostgreSQL: vedio-appdb / PostgreSQL 16
Alembic: 0003_asset_semantic_key
```

报告不记录 API Key、数据库密码、连接串或签名下载 URL。

## Run 对账

### Run `4aa380da-4d99-47fa-b333-653c432454a2`

- 状态：`failed`
- Episode：0
- Asset：0
- Day Director Step：`9325c4de-12d0-4a51-8cb8-d01262c33064`
- Prompt：`97867a23-c106-4d38-86ae-d4477347e65a`
- 结果：Ark 返回 `incomplete`，未形成 DayBrief。

### Run `55fedf8e-c5d0-452f-9942-60a04eaf02e2`

- 状态：`failed`
- Episode：0
- Asset：0
- Day Director Step：`1ce84434-af5d-40a5-8ad1-d8ffb407f47d`
- Day Prompt：`d34efd76-5c0c-43e8-82c5-c2aa54a0203b`
- Morning Director Step：`1147720e-c80b-4985-9fb8-51c2514caebf`
- Morning Prompt：`b88a090f-fa3e-486d-bacc-2360ef9b8572`
- 结果：上午 Episode 声明了超过一次坐下/站起拓扑变化，被确定性规则拒绝。

### Run `d211e3c3-f2d9-47b5-92bc-3e7568e6ed42`

- 状态：`failed`
- Episode：0
- Asset：0
- Day Director Step：`0ae4e7d3-7d5f-4e45-95ff-09e889fd3d0e`
- Day Prompt：`76bd2300-6055-45a1-9544-c283e1bc72fd`
- Morning Prompt attempts：
  - `2740669c-1aad-4844-9a15-95508cb7d58b`
  - `94203efc-a4a2-4dd0-b25b-98260ee627fe`
  - `7be827bc-6e8d-4125-a400-1936509278b0`
  - `4feb2126-1704-4fa4-9c32-ae961057051b`
  - `a102c56d-6c77-440a-8da8-62ed4cbfb5c6`
- 结果：恢复规划正确复用了 DayBrief，但上午 Episode 仍包含超过两个关键物体/
  状态变化，最终保持失败。
- 其中一次实验性原生 JSON Schema 请求被 Ark 以
  `json: unknown field "json_schema"` 拒绝。运行配置已恢复为兼容的
  `json_object_schema_prompt`，没有把该实验模式写入 `.env`。

## 本次发现并修复的问题

1. Responses 的隐藏推理可能耗尽结构化输出预算。导演与审核请求现在显式关闭
   thinking，并保存 `incomplete_details`。
2. Day Director 失败时 Run 原先可能停在 `draft`。现在任何规划异常都会将 Run
   关闭为 `failed`。
3. 旧关键变化计数把人物正常行走的每个字段变化都算作多个物理事件。现在一个
   连贯道具转移只计一次，人物和猫咪的普通移动不占用关键物理预算。
4. 增加 `resume-planning`：只恢复缺失时段，成功的 DayBrief 不重复收费调用。
5. 增加受控的供应商结构归一化：只修复无歧义的字段层级和镜头连续标志，不删除
   实体、不放宽物理规则。

## 架构判断

本次失败说明“让文本导演直接产出完整数据库级 `VisibleWorldPlan`”仍然过于脆弱。
规划模型擅长剧情、镜头和动作意图，但不稳定地遵守实体生命周期、支撑关系和复杂度
计数等工程契约。

下一步应将输入职责进一步收敛：

```text
Ark DirectorDraft
主题、事件、角色、动作阶段、镜头意图、关键道具
        ↓
本地 WorldCompiler
场景锚点、实体生命周期、支撑/接触/包含、切镜继承
        ↓
确定性校验
        ↓
Seedream / Seedance
```

DirectorDraft 仍由真实规划模型创作；WorldCompiler 只把导演明确声明的事实转换成
可执行世界状态，不发明剧情。只有出现歧义或确实超过复杂度预算时，才把带具体错误
反馈的单个时段送回导演重规划。

在完成该收敛前继续付费重试同一复杂契约，预期收益较低，所以本轮停止后续 Ark
调用。下一次真实测试应在 WorldCompiler 落地且非付费回归通过后重新显式授权。

> 后续决策（2026-07-30）：保留导演直接输出完整`VisibleWorldPlan`，删除固定物理
> 次数预算，改用状态重放检查真实矛盾；复杂交互只形成非阻断渲染风险。每个时段
> 候选矛盾时自动完整重写一次，第二次失败进入`planning_review`。

## 自动化基线

- Ruff：通过。
- Pytest：90 passed。
- 迁移版本：最新。
- 付费媒体任务：0。
- 本地 MP4：0。
- DeliveryPackage：0。
