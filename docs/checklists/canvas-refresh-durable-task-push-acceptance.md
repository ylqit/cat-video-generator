# 画布无黑屏刷新与持久异步任务推送验收清单

> 状态约定：`[ ]` 未验证，`[x]` 已通过，`[-]` 已实现但受外部环境阻塞。

## 1. 数据与持久队列

- [x] `canvas_events` 使用单调递增 `sequence`，UUID 主键继续保留。
- [x] 项目事件按 `(production_run_id, sequence)` 建立游标索引。
- [x] `workflow_steps.progress_json` 持久化步骤、百分比和用户可读说明。
- [x] 六阶段长任务由 API 校验并持久入队，HTTP 立即返回 `202`。
- [x] 幂等重复提交返回同一任务，不重复扣费。
- [x] 任务输入固定来源版本、阶段和内容哈希，Worker 执行前重新验证。

## 2. 独立 Worker

- [x] `cvg-worker --concurrency 1 --poll-seconds 1` 可独立启动。
- [x] `cvg-media-worker` 保持兼容并复用同一 Worker 生命周期。
- [x] 默认配置严格单任务顺序执行。
- [x] 任务使用 60 秒租约，并在执行期间每 20 秒续租。
- [x] Worker 崩溃后，租约过期任务可重新领取。
- [x] Provider 处理中通过 `next_retry_at` 延迟查询，不长时间 `sleep`。
- [x] 网络错误最多自动重试 3 次，计数独立于业务候选版本。
- [x] `submission_unknown` 不自动重新提交，可按 Provider ID 恢复。

## 3. 事件与 SSE

- [x] 任务状态变更与持久事件在同一事务提交。
- [x] 项目 SSE 使用 `sequence` 作为游标并保持长连接。
- [x] 全局 `/api/v1/task-center/events` 支持 `Last-Event-ID` 和 `afterEventId`。
- [x] SSE 每 15 秒发送心跳，每批最多补发 200 条。
- [x] 断线续传不会遗漏相同时间戳事件或重复通知。
- [x] 事件覆盖排队、运行、进度、待审核、成功、失败和提交状态未知。

## 4. 前端无感刷新

- [x] 首次无内容时显示骨架/加载态，后台刷新不显示全局遮罩。
- [x] 删除 SSE 断开后的 15 秒画布全量轮询。
- [x] 同时只执行一个画布刷新请求，重复事件合并为一次尾随刷新。
- [x] 后台刷新保留缩放、视口、选中节点、局部控制台和未提交草稿。
- [x] 进度事件仅局部更新任务状态，投影变化才静默刷新画布。
- [x] SSE 断开时仅轮询任务中心：有活跃任务 4 秒、无任务 25 秒。
- [x] 任务通知按事件 `sequence` 去重，成功、失败和待审核结果可定位到节点。

## 5. 自动测试与人工验收

- [x] 数据库迁移与模型测试通过。
- [x] 后端持久队列、租约、恢复、事件和 SSE 测试通过。
- [x] 前端无遮罩刷新、SSE 降级和通知去重测试通过。
- [x] Python 静态检查与完整测试通过：225 项通过。
- [x] 前端测试、类型检查和生产构建通过：75 项通过。
- [-] 页面保持打开 10 分钟，无周期性黑屏；需在迁移后的真实浏览器环境持续观察。
- [-] 任务提交后 2 秒内显示排队或执行状态；需启动 API、Worker 与 PostgreSQL 联调计时。
- [-] Worker 完成后 2 秒内展示结果通知和节点状态；需真实 SSE 联调计时。
- [-] API 或 Worker 重启后任务不丢失、不重复提交、不重复扣费；需执行进程级故障注入。

## 6. 本次自动验收记录

- `alembic heads`：`0025_schema_contract_alignment (head)`。
- `ruff check src tests`：通过。
- `pytest -q`：225 项通过，1 条第三方 `httpx` 弃用警告。
- `npm --prefix web run typecheck`：通过。
- `npm --prefix web test -- --run --silent=true`：75 项通过。
- `npm --prefix web run build`：通过；保留现有大包体积提示，不影响构建产物。
