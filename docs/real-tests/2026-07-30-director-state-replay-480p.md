# 2026-07-30 导演主导与状态重放真实验证

## 结论

固定物理次数预算已经取消，新的全天 Run 完成了真实规划和三条 Seedance
单次成片。三个 Episode 的世界状态均自洽，较多交互只记录为高渲染风险，没有
阻断规划、没有自动改成 `multi_clip`。

最终状态：

- Run：`reviewing`
- morning、noon、evening：均为 `content_review`
- DeliveryPackage：未创建
- 自动内容批准：未执行

这次验证说明“导演创作完整剧情、确定性状态重放只阻断矛盾、真实成片决定内容
质量”的职责划分已经跑通。同时，技术 QC 通过并不代表语义内容合格：傍晚联系表
出现第二只猫，必须由内容审核拒绝或重做。

## 真实配置

```text
Run ID: eedf8275-bbb5-464e-a6be-a443c1daaad2
Content date: 2026-08-05
Ark access mode: standard
Planning model: doubao-seed-2-1-pro-260628
Image model: doubao-seedream-5-0-260128
Video model: doubao-seedance-2-0-260128
Requested resolution: 480p
Generation strategy: single_pass
Native audio: enabled
PostgreSQL migration: 0004_planning_review
```

实际 `.env` 使用完整 Seedance 2.0，而不是 Mini。运行时现已同时接受当前完整
模型和 Mini 配置，但不会自行切换模型。

报告不记录 API Key、数据库密码、连接串、Base64 或签名下载 URL。

## 规划与世界状态

总导演和三个时段导演按独立调用执行。首次候选出现真实状态矛盾时，系统保存候选
和错误，并最多自动完整重写一次；后续人工重规划同样保留全部历史 Prompt。

最终三个 Episode 均满足：

- 一个主事件、2～4个动作阶段、1～3个镜头。
- 实体、锚点和支撑关系先声明后使用。
- 动作前状态与上一动作结束状态一致。
- 无未解释的 `enter`、`exit`、`consume` 或 `transform`。
- 切镜继承仍可见角色、服装和持久物。

三个 Episode 都被标记为高渲染风险，因为存在多次目标动作或可见状态变化。风险
只显示在状态中，未修改剧情、未阻断 `single_pass`、未自动创建额外收费任务。

## 视频任务与媒体

| Slot | Ark task ID | Prompt | 字符数 | 时长 | 媒体 | SHA-256 |
| --- | --- | --- | ---: | ---: | --- | --- |
| morning | `cgt-20260730120809-fgxxk` | `ad84d568-fc63-4a5a-9628-f4f67cbd4f2f` | 1550 | 11.052秒 | H.264/AAC，496×864 | `b6977f15808246020fb0f745eefc731ac1361ca6f80bec18038bcc86b4b5559a` |
| noon | `cgt-20260730122822-9rzdf` | `9d77c04e-9166-415b-958f-6b6ce08460bc` | 1213 | 12.051秒 | H.264/AAC，496×864 | `de000a022b1929e50a38557ef5a313418b44d8e23d5a0f45949711c01b6e2df6` |
| evening | `cgt-20260730123057-94c6t` | `83c85b5e-2808-4557-837b-01455e289dbe` | 1175 | 11.052秒 | H.264/AAC，496×864 | `706263bebd20eb3223f219cbb493d4cad7c042afb5d9ed42efa7a5d4b29b9ea0` |

每条只有一个成功的视频 Step。重复运行 morning 时复用了现有结果，没有创建
第二个收费任务。三条均包含双声道 AAC 原生音频并通过容器、编码、可播放性和
时长技术 QC。

## Prompt 与诊断资产

全部13份导演 Prompt和3份实际视频 Prompt保存在：

```text
var/prompts/eedf8275-bbb5-464e-a6be-a443c1daaad2/
```

主要视频 Prompt：

- `21-morning-video.txt`
- `22-noon-video.txt`
- `23-evening-video.txt`

均匀抽帧联系表保存在：

```text
var/reviews/eedf8275-bbb5-464e-a6be-a443c1daaad2/
```

诊断性目检结果：

- morning：一人一猫、二维绘本风格和主要空间在采样帧中较稳定。
- noon：一人一猫保持；水果盘已在场景 Prompt 中声明，但只在第二镜头被清楚
  揭示，容易形成“切镜后突然出现”的观感，属于构图连续性警告。
- evening：第二镜头出现第二只灰白猫，明确违反固定一只猫和禁止分身规则。

因此本轮没有批准或交付任何视频。联系表只用于快速诊断，最终决定仍需观看原始
MP4及声音。

## 实现验证

- 固定“最多两次物理变化、最多一次坐下/站起、高风险不得跨镜头”已删除。
- `VisibleWorldPlan`改为按 `action_order`进行状态重放。
- 明确矛盾阻断；交互次数、拓扑变化和跨镜头动作只形成 `renderRisk`。
- 时段导演首次矛盾时最多自动完整重写一次。
- 第二次仍矛盾时进入 `planning_review`，不会创建媒体任务。
- `status`已返回世界自洽、矛盾、风险、分段建议、导演修复和下一动作。
- Prompt压缩后，完整世界账本仍保存在PostgreSQL，Seedance只接收本集执行信息。

## 自动化基线

- Ruff：通过。
- Pytest：103 passed。
- Run状态：`reviewing`。
- 视频Step：3个，全部 `succeeded`。
- 视频资产：3个，全部 `candidate`。
- Episode：3个，全部 `content_review`。
- DeliveryPackage：0。

