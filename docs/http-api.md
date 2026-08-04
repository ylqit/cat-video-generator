# FastAPI 本机接口

启动：

```powershell
uv run cvg api
```

默认监听 `http://127.0.0.1:8765`。`--host` 可显式修改监听地址；Docker 使用 `--host 0.0.0.0`。`--read-only` 只启用查询路由；`--static-dir web/dist` 可托管前端构建产物，并要求目录中存在 `index.html`。

## 查询路由

```text
GET /api/v1/health
GET /api/v1/runs
GET /api/v1/runs/{runId}
GET /api/v1/runs/{runId}/graph
GET /api/v1/episodes/{episodeId}
GET /api/v1/episodes/{episodeId}/prompt-preview
GET /api/v1/steps/{stepId}
GET /api/v1/prompts/{promptId}
GET /api/v1/assets/{assetId}
GET /api/v1/assets/{assetId}/content
GET /api/v1/canon
GET /api/v1/runs/{runId}/deliveries
GET /api/v1/deliveries/{packageId}/manifest
GET /api/v1/jobs
GET /api/v1/jobs/{jobId}
```

CLI 和 HTTP 共用 `QueryService`。媒体读取会验证路径属于资产根或交付根，路径越界返回 `403`。

## 生产控制路由

```text
POST /api/v1/plans
POST /api/v1/runs/{runId}/generate
POST /api/v1/runs/{runId}/resume
POST /api/v1/runs/{runId}/resume-planning
POST /api/v1/runs/{runId}/episodes/{slot}/replan
POST /api/v1/steps/{stepId}/retry
POST /api/v1/steps/{stepId}/resume
GET  /api/v1/steps/{stepId}/reconciliation-candidates
POST /api/v1/steps/{stepId}/reconcile
POST /api/v1/assets/{assetId}/review
POST /api/v1/canon
POST /api/v1/canon/{assetId}/derive-crop
POST /api/v1/episodes/{episodeId}/references
PUT  /api/v1/episodes/{episodeId}/prompt-overrides
POST /api/v1/episodes/{episodeId}/storyboards
POST /api/v1/runs/{runId}/deliver
```

- 规划、生成和收费重试必须显式提交 `allowPaidGeneration=true`。
- `generate` 可指定 morning/noon/evening；不提供分段或分辨率实验参数。
- `retry`接受失败、过期或取消的媒体Step并创建`attempt+1`。Seedream同步超时形成的`submission_unknown`只有在请求体额外提交`acknowledgeDuplicateBilling=true`时允许人工再生成。
- `resume`只查询已有Seedance Task ID，不创建新的生成POST。
- 视频`submission_unknown`使用`reconciliation-candidates`查询候选，再由用户通过`reconcile`确认绑定；Task ID全局唯一，不可绑定到两个Step。
- Canon与用户显式上传的Episode元素/场景图片必须携带`semantic_key`；剧情中的逻辑`entityKey`不等于数据库资产键，普通场景没有专用参考图也可生成故事板。
- 规划和生成返回 `202 {jobId, dedupKey, context}`，其中`context`只包含已知的
  `runId / episodeId / slot / operationKey`。前端轮询 Job 与 Run graph，失败上下文
  会持续展示并支持定位节点。
- 最终视频由人工审核，API 不会自动完成交付。

工作台定位由URL保存：`/studio?run=<runId>&stage=storyboard&slot=morning&node=storyboard:morning`。后端工作流阶段与当前浏览页签相互独立，后台轮询不会覆盖用户选择。`/runs/{runId}`只负责跳转到该统一工作台。

`GET /health`除数据库迁移状态外，还只读返回当前生效的导演、图片、审核、视频API和视频监看超时；不返回Key、密码或连接串。

JobRegistry 只在本进程执行后台函数和去重活跃请求；Run、Episode、Step、Prompt、Asset 的持久状态全部来自 PostgreSQL。

若设置 `CAT_VIDEO_API_TOKEN`，所有 `/api/` 请求必须携带 `Authorization: Bearer <token>`。
