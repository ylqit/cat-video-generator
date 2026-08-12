# FastAPI V5 接口

```text
GET    /api/v1/health
GET    /api/v1/projects
POST   /api/v1/projects
GET    /api/v1/projects/{id}
PATCH  /api/v1/projects/{id}
PUT    /api/v1/projects/{id}/default-references

POST   /api/v1/projects/{id}/scenes
PATCH  /api/v1/scenes/{id}
DELETE /api/v1/scenes/{id}
PUT    /api/v1/projects/{id}/scene-order
PUT    /api/v1/scenes/{id}/look-asset
POST   /api/v1/scenes/{id}/look-images

POST   /api/v1/scenes/{id}/shot-suggestions
POST   /api/v1/steps/{id}/accept-suggestions
POST   /api/v1/scenes/{id}/shots
PATCH  /api/v1/shots/{id}
DELETE /api/v1/shots/{id}
PUT    /api/v1/scenes/{id}/shot-order
GET    /api/v1/shots/{id}
GET    /api/v1/shots/{id}/prompt-preview

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

造型/片段建议、场景定妆、锚点、视频和区间重拍必须提交 `allowPaidGeneration=true`。锚点/视频请求的
`regenerate=true`明确创建新 attempt，并可携带人工 reason；活跃任务或
`submission_unknown`不得重做。Prompt 预览、编辑、人工审核、版本选择和总片本地合成
不调用生成 Provider。

`POST /steps/{id}/accept-suggestions` 必须提交用户编辑后的 `lookPlan` 和 1～6 个
`shots`；单片段场景严格为 1 个，多片段场景严格等于场景设置的 2～6 个。原始
`providerOutput` 不变，服务端另存 `acceptedOutput` 与 `acceptedAt`。已有图片或视频
Provider 历史时，整批覆盖返回 409。
