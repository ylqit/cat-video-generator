# FastAPI 接口

本地启动`uv run cvg api`，默认`127.0.0.1:8765`；Docker显式监听`0.0.0.0`。

## 查询

```text
GET /api/v1/health
GET /api/v1/runs
GET /api/v1/runs/{runId}/graph
GET /api/v1/episodes/{episodeId}
GET /api/v1/episodes/{episodeId}/prompt-preview
GET /api/v1/steps/{stepId}
GET /api/v1/steps/{stepId}/trace
GET /api/v1/prompts/{promptId}
GET /api/v1/assets/{assetId}
GET /api/v1/assets/{assetId}/content
GET /api/v1/canon
GET /api/v1/jobs
GET /api/v1/runs/{runId}/slots/{slot}/outcome
```

Prompt预览自动使用`.env`的真实视频分辨率，不允许前端传入与生产配置不一致的值。
`prompt-preview`也支持POST一个尚未保存的结构化Episode草稿，用相同领域编译器返回
定妆、开场锚点和各视频区段Prompt；该操作不写数据库、不调用Ark。

## 生产控制

```text
POST /api/v1/plans
POST /api/v1/runs/{runId}/continue
POST /api/v1/runs/{runId}/generate
POST /api/v1/runs/{runId}/resume
POST /api/v1/runs/{runId}/resume-planning
POST /api/v1/runs/{runId}/slots/{slot}/plan
POST /api/v1/runs/{runId}/episodes/{slot}/replan
PUT  /api/v1/runs/{runId}/slots/{slot}/outcome
POST /api/v1/episodes/{episodeId}/visuals
PUT  /api/v1/episodes/{episodeId}/script
PUT  /api/v1/episodes/{episodeId}/prompt-overrides
POST /api/v1/episodes/{episodeId}/prompt-preview
POST /api/v1/steps/{stepId}/retry
POST /api/v1/steps/{stepId}/resume
GET  /api/v1/steps/{stepId}/reconciliation-candidates
POST /api/v1/steps/{stepId}/reconcile
POST /api/v1/assets/{assetId}/review
POST /api/v1/runs/{runId}/deliver
```

规划、图片、视频和收费重试必须逐次提交`allowPaidGeneration=true`。`resume`只查询
已有Seedance Task ID；`reconcile`只绑定现有任务；两者不创建生成POST。图片
`submission_unknown`再次生成还需确认潜在重复计费。

`planningMode=guided_sequential`是Web默认值。该模式先暂停在DayBrief，用户通过现有
DayBrief保存接口确认边界，再使用`slots/{slot}/plan`一次只调用当前解锁时段。结果卡
GET返回视频诊断和脚本结尾形成的可编辑草稿；PUT只接受最终视频已经人工批准的时段，
确认后才解锁下一时段。`auto_day`仍通过原规划入口一次顺序生成三个时段。

统一工作台地址为`/studio?run=<runId>&stage=<stage>&slot=<slot>&node=<nodeId>`，
阶段固定为`dayBrief / script / visual / video / review`。轮询不会覆盖URL中的浏览位置。
`/runs/{runId}`只重定向到同一工作台。

Step Trace只在用户打开节点时读取，返回输入摘要、当前结构化结果、当前编译Prompt、
各attempt实际Prompt、脱敏后的Ark原始结构化JSON、可选归一化结果与警告、有序素材、
媒体、审核证据和尝试历史。Run Graph不重复携带这些大正文。

健康响应包含模型、分辨率、是否支持视频延展和各类超时，但不包含Key、密码或连接串。
