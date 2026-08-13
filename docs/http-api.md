# FastAPI V5 接口

```text
GET    /api/v1/health
GET    /api/v1/projects
POST   /api/v1/projects
GET    /api/v1/projects/{id}
PATCH  /api/v1/projects/{id}
PUT    /api/v1/projects/{id}/default-references
GET    /api/v1/projects/{id}/visual-profile
PUT    /api/v1/projects/{id}/visual-profile
POST   /api/v1/projects/{id}/restore-canon-references

POST   /api/v1/projects/{id}/scenes
PATCH  /api/v1/scenes/{id}
DELETE /api/v1/scenes/{id}
PUT    /api/v1/projects/{id}/scene-order
GET    /api/v1/scenes/{id}/creative-workflow
POST   /api/v1/scenes/{id}/story-diagnoses
POST   /api/v1/steps/{id}/accept-story-diagnosis
POST   /api/v1/scenes/{id}/story-rewrites
POST   /api/v1/steps/{id}/accept-story-rewrite
PUT    /api/v1/scenes/{id}/look-asset
GET    /api/v1/scenes/{id}/look-draft
PUT    /api/v1/scenes/{id}/look-draft
POST   /api/v1/scenes/{id}/look-prompt-preview
POST   /api/v1/scenes/{id}/look-images
GET    /api/v1/scenes/{id}/look-versions

POST   /api/v1/scenes/{id}/shot-suggestions
POST   /api/v1/steps/{id}/accept-suggestions
POST   /api/v1/scenes/{id}/shots
PATCH  /api/v1/shots/{id}
DELETE /api/v1/shots/{id}
PUT    /api/v1/scenes/{id}/shot-order
GET    /api/v1/shots/{id}
GET    /api/v1/shots/{id}/prompt-preview
GET    /api/v1/shots/{id}/assist-context
POST   /api/v1/shots/{id}/assist
GET    /api/v1/shots/{id}/assist-analyses
POST   /api/v1/steps/{id}/accept-shot-assistance
GET    /api/v1/shots/{id}/previous-tail
POST   /api/v1/shots/{id}/adopt-previous-tail-anchor

POST   /api/v1/projects/{id}/references
PUT    /api/v1/shots/{id}/references
POST   /api/v1/shots/{id}/anchors
POST   /api/v1/shots/{id}/videos
GET    /api/v1/shots/{id}/versions
POST   /api/v1/assets/{id}/review
POST   /api/v1/shots/{id}/versions/{assetId}/select
POST   /api/v1/shots/{id}/range-edits

POST   /api/v1/projects/{id}/sequences
GET    /api/v1/projects/{id}/sequences
POST   /api/v1/projects/{id}/sequences/{sequenceId}/select

POST   /api/v1/steps/{id}/resume
GET    /api/v1/steps/{id}/reconciliation-candidates
POST   /api/v1/steps/{id}/reconcile
GET    /api/v1/assets/{id}/content
GET    /api/v1/canon
GET    /api/v1/jobs
GET    /api/v1/jobs/{id}
```

剧情诊断、剧情重写、分镜建议、片段多模态审稿、场景定妆、锚点、视频和区间重拍必须提交 `allowPaidGeneration=true`。锚点/视频请求的
`regenerate=true`明确创建新 attempt，并可携带人工 reason；活跃任务或
`submission_unknown`不得重做。Prompt 预览、编辑、人工审核、版本选择和总片本地合成
不调用生成 Provider。

`POST /steps/{id}/accept-suggestions` 必须提交用户编辑后的 `lookPlan` 和 1～6 个
`shots`；单片段场景严格为 1 个，多片段场景严格等于场景设置的 2～6 个。原始
`providerOutput` 不变，服务端另存 `acceptedOutput` 与 `acceptedAt`。已有图片或视频
Provider 历史时，客户端使用 `applyMode=update_existing` 并提交所有 `sourceShotRevisions`：片段
数量相同则按现有 ID 原子更新并清除过期的当前选择；数量不同返回 409。无媒体历史时使用
`applyMode=replace` 整批替换。

剧情诊断只分析当前原稿；接受时提交人工编辑后的 diagnosis、选择的三档方案之一，或明确
`preserveOriginal=true`。剧情重写只能使用已接受诊断；接受后写回现有 `Scene.sourceText`。
分镜导演只能读取已接受重写稿，或用户明确保留的原稿。四阶段复用现有 Step/Prompt 表；场景
输入哈希或片段 Revision 已变化时接受旧结果返回 409，不增加故事物理字段或 0019 迁移。

视觉档案 `PUT` 按文本、参考资产 ID 和参考内容 SHA-256 计算内容哈希：相同内容复用
revision，不同内容创建不可变 revision。`PUT /look-draft` 使用 `expectedRevision` 乐观锁；
`POST /look-images` 必须携带刚保存的 `draftRevision`。实际付费提交前要求人物身份、猫咪
身份和画风三类参考齐全、内容可读且去重后不超过 14 张。Prompt 预览不调用 Ark。

`PATCH /shots/{id}` 只保存人工片段稿并递增 `draftRevision`，不会调用 Ark。保存成功后，
`POST /shots/{id}/assist` 仍必须显式提交付费授权和当前 `sourceDraftRevision`；分析失败只记录
失败 Step，不回滚已经保存的片段。分析读取当前及相邻片段和用户勾选的最多 9 张压缩预览。
`providerOutput` 永久保留，`accept-shot-assistance` 只接受用户勾选且确实由该分析提出的字段或正文候选，
保存 `acceptedOutput/acceptedAt`；草稿 Revision 已变化时返回 409。
视觉审稿的候选图片只能来自当前实际输入上下文；即使客户端以其他顺序勾选，服务端仍按
“批准锚点 → 片段自定义 → 场景定妆 → 项目 Canon → 可用上一片段尾帧”重排并按 SHA 去重。
四个创作角色都使用 `ARK_PLANNING_MODEL`；`ARK_REVIEW_MODEL` 只用于生成后的视频抽帧诊断。

`sceneLookUsage` 是片段场景视觉基准策略的权威字段：`off`、`appearance_only`、
`full_reference`、`derive_anchor`。响应中的 `useSceneLook` 仅为 V5 兼容派生值。派生锚点时，
场景视觉基准只进入 Seedream 锚点输入；锚点批准后不再重复进入 Seedance 视频输入。

`prompt-preview` 分别返回 `creativeBody`、`systemShell`、实际参考图片及 `prompt`。素材职责由
来源锁定：Canon 人物/猫咪为 identity、Canon 画风为 style、场景视觉基准为 scene、批准首帧为
anchor；正文可使用内部语义标记，变更绑定后图片编号按实际输入重新编译，不输出不存在的
`@图片N`。

批准片段视频后，系统尝试用本地 FFmpeg 生成可追溯 `shot_tail_frame`。下一片段可通过
`adopt-previous-tail-anchor` 将其设为唯一批准锚点；如果上一片段更换批准视频，旧尾帧状态为
过期，必须重新采用。采用新锚点会清除下一片段当前选中的锚点、视频与成片，但保留历史版本。

`POST /projects/{id}/sequences` 可提交每个非首片段的 `transitionFromPrevious`：`cut` 的持续时间
必须为 0，`fade_black` 和 `cross_dissolve` 为 150～1000 毫秒；旧请求省略时兼容为硬切。
项目图片上传可附带 `displayName`，保存在现有 metadata 中，不新增数据库列。
