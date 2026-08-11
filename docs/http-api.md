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
GET /api/v1/runs/{runId}/slots/{slot}/connection
GET /api/v1/runs/{runId}/slots/{slot}/references
GET /api/v1/episodes/{episodeId}/video-sequences
```

旧契约Run的列表摘要会返回`compatible=false`和明确原因；它们不进入当前Graph、
媒体生产或重试接口。

Prompt预览自动使用`.env`的真实视频分辨率，不允许前端传入与生产配置不一致的值。
`prompt-preview`也支持POST一个尚未保存的结构化Episode草稿，用相同领域编译器返回
定妆、开场锚点和各视频区段Prompt；该操作不写数据库、不调用Ark。

## 生产控制

```text
POST /api/v1/story-projects/preview
POST /api/v1/projects
POST /api/v1/runs/{runId}/continue
POST /api/v1/runs/{runId}/generate
POST /api/v1/runs/{runId}/resume
POST /api/v1/runs/{runId}/resume-planning
POST /api/v1/runs/{runId}/slots/{slot}/plan
PUT  /api/v1/runs/{runId}/slots/{slot}/source
POST /api/v1/runs/{runId}/episodes/{slot}/replan
PUT  /api/v1/runs/{runId}/slots/{slot}/outcome
PUT  /api/v1/runs/{runId}/slots/{slot}/connection
POST /api/v1/runs/{runId}/slots/{slot}/connection/suggest
PUT  /api/v1/runs/{runId}/slots/{slot}/references
POST /api/v1/episodes/{episodeId}/shot-notes
POST /api/v1/episodes/{episodeId}/visuals
PUT  /api/v1/episodes/{episodeId}/script
PUT  /api/v1/episodes/{episodeId}/prompt-overrides
POST /api/v1/episodes/{episodeId}/prompt-preview
POST /api/v1/steps/{stepId}/retry
POST /api/v1/steps/{stepId}/resume
GET  /api/v1/steps/{stepId}/reconciliation-candidates
POST /api/v1/steps/{stepId}/reconcile
POST /api/v1/steps/{stepId}/regenerate
POST /api/v1/episodes/{episodeId}/video-sequences/{sequenceId}/range-edits
POST /api/v1/video-sequences/{sequenceId}/select
POST /api/v1/assets/{assetId}/review
POST /api/v1/runs/{runId}/deliver
```

`story-projects/preview`只做本地文本拆分，不写库、不调用Ark。`projects`接收
`StoryProjectInput`：`theme_expand`会按流水线配置调用总导演；
`episode_scripts + guided_sequential`只保存项目和原文，创建时不要求付费许可。
`episode_scripts + auto_day`必须提供完整三集，并在自动调用三个时段导演前确认费用。

导演、图片、视频和收费重试必须逐次提交`allowPaidGeneration=true`。`resume`只查询
已有Seedance Task ID；`reconcile`只绑定现有任务；两者不创建生成POST。图片
`submission_unknown`再次生成还需确认潜在重复计费。

`planningMode=guided_sequential`是Web默认值。主题扩写先暂停在Project Outline并由
用户确认；已有剧本直接从用户输入解锁Morning。`slots/{slot}/plan`一次只调用当前解锁
时段，已有原文走镜头化适配；时段原文为空时只有显式`generateFromTheme=true`才允许
AI根据主题生成；只有另行启用关联卡时才参考前序。结果卡
GET返回视频诊断和脚本结尾形成的可编辑草稿；PUT只接受最终视频已经人工批准的时段，
确认后才解锁下一时段。确认结果本身不会自动进入后续Prompt；用户可选择不关联、手工
关联或生成一版付费关联建议，并独立决定是否加载。前序媒体也必须逐项指定职责和使用
节点，系统不会自动把所有匹配素材发送给Ark。`auto_day`仍可生成三个时段。

统一工作台地址为`/studio?run=<runId>&stage=<stage>&slot=<slot>&node=<nodeId>`，
可追加`sequence=<sequenceId>`直接定位视频revision。阶段固定为
`projectOutline / script / visual / video / review`。轮询不会覆盖URL中的浏览位置。
`/runs/{runId}`只重定向到同一工作台。

Run Graph中的语义节点只使用稳定`semanticNodeId`。`availability=locked`且
`executionStatus=not_created`表示未来路线占位，不对应数据库Step。节点同时返回锁定原因、
解锁条件、当前attempt、全部attempt ID、资产/审核ID和后端判定的`allowedActions`。

`regenerate`保留旧attempt并创建图片或整条视频新版本；它不能处理正在运行或
`submission_unknown`节点。区间编辑请求包含`startMs/endMs/boundaryMode/instruction`，
选区必须为0.5～13秒且位于同一来源Clip；默认吸附计划镜头边界。编辑只支持仍能通过
Ark task查询HTTPS源视频的版本。新revision完成QC后停在内容审核，只有批准后才能调用
`select`成为正式版本。

Step Trace只在用户打开节点时读取，返回输入摘要、当前结构化结果、当前编译Prompt、
各attempt实际Prompt、脱敏后的Ark原始结构化JSON、可选归一化结果与警告、有序素材、
媒体、审核证据和尝试历史。Run Graph不重复携带这些大正文。

健康响应包含模型、分辨率、是否支持视频延展和各类超时，但不包含Key、密码或连接串。
