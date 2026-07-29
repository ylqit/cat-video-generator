# FastAPI本机接口

启动：

```powershell
uv run cvg api
```

默认地址：`http://127.0.0.1:8765`。默认启动完整接口（只读 + 写端点 +
后台任务），`--read-only` 退回纯只读模式，`--static-dir web/dist` 可让
同一进程托管前端构建产物。

## 只读路由

```text
GET /api/v1/health
GET /api/v1/runs
GET /api/v1/runs/{runId}
GET /api/v1/runs/{runId}/graph
GET /api/v1/episodes/{episodeId}
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

CLI 与 HTTP 共用 `QueryService`，不会各自推测状态。

`/assets/{assetId}/content` 只允许读取配置的资产根目录或交付根目录中的
文件。即使数据库中出现异常路径，越界访问也返回 `403`。交付包
`manifest.json` 的读取同样受交付根目录白名单约束。

## 写路由（生产控制）

```text
POST /api/v1/plans                          # 全天规划（后台任务）
POST /api/v1/runs/{runId}/generate          # 按slot或全天生成（后台任务）
POST /api/v1/runs/{runId}/resume            # 恢复在途供应商任务（后台任务，不扣费）
POST /api/v1/assets/{assetId}/review        # 人工审核，body: {approve, reason}
POST /api/v1/canon                          # multipart上传person/cat/style图
POST /api/v1/runs/{runId}/deliver           # 构建本地交付包
```

- 规划与生成必须在请求体携带 `"allowPaidGeneration": true`，否则直接
  `422`，等价于 CLI 的 `--allow-paid-generation`。
- `plans`、`generate`、`resume` 立即返回 `202 {jobId, dedupKey}`，服务端
  在线程池中推进并落库；前端轮询 `/runs/{runId}/graph` 观察分镜状态，
  轮询 `/jobs/{jobId}` 获取任务级结果与错误。相同 `dedupKey` 的活跃任务
  重复提交返回 `409`；付费任务在进程内串行执行，防止重复扣费。
- 错误映射：参数或状态非法 `422`、记录不存在 `404`、供应商错误 `502`、
  任务冲突 `409`。
- `submission_unknown` 步骤保持冻结，`resume` 也不会重复 POST，必须人工
  对账。

## 前端数据源

本地前端（`web/`，Vue3 + Element Plus）以 `/runs/{runId}/graph` 为主
数据源展示：

- 三个 Episode 的 1、2、3 顺序与状态机进度。
- 每个 Step 的状态和 Ark task ID。
- 实际持久化 Prompt（展开时经 `/prompts/{promptId}` 拉取全文）。
- 分镜图、视频资产（`/assets/{assetId}/content` 直接播放）、QC 与
  审核结果。

## 可选令牌

服务只监听 `127.0.0.1`，本机使用不需要认证。若设置环境变量
`CAT_VIDEO_API_TOKEN`，所有 `/api/` 请求必须携带
`Authorization: Bearer <token>`。
