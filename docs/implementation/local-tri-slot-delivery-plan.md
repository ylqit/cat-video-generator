# 本地三时段视频生产与交付系统实施计划

## 当前状态

仓库已经落地 PostgreSQL 持久化、两版 Alembic 迁移、数据库安全守卫、内容/Canon 导入审核、RenderPlan 编译、真实 Seedream/Seedance SDK 映射、下载与 ffprobe QC、人工审核、恢复、fallback 和三条本地交付。当前没有执行收费烟测；文档不记录本机凭据状态。真实生成仍必须通过 Ark profile 静态检查、数据库和 ffprobe 预检，并由操作者显式确认付费。

当前范围：

```text
DailyLifePack
→ 视觉风险判断
→ 主体直接参考或按需关键帧
→ Seedance
→ 立即下载
→ QC
→ 不可变保存
→ 01/02/03 三条 MP4 + manifest
```

不实现微信小程序、HTTP API、公开 URL、对象存储、CDN、定时发布和无限画布。内容生产由 CLI 按需触发，不绑定前一晚20:00或任何固定执行窗口。

## 技术选型

- Python 3.12 或3.13。
- `uv` 管理依赖和锁文件。
- Typer 提供 CLI。
- JSON Schema Draft 2020-12 校验内容契约；运行时领域对象按需要逐批实现。
- SQLAlchemy 2、Alembic 和 `psycopg` 管理远程 PostgreSQL。
- 所有业务表位于数据库 `vedio-appdb` 的独立 `cat_video` Schema。
- `volcengine-python-sdk[ark]` 调用 Ark。
- Ark 接入支持 `agent_plan` 与 `standard` 两个显式 profile；默认 Agent
  Plan Large，Medium/Small 不进入 Seedance 2.0-mini 生产链路。
- `httpx` 流式下载媒体。
- ffprobe 执行每条视频的只读媒体检查。
- FFmpeg 保留用于后续条件式 remux、转码、音轨替换、混音或 multi_clip；当前 V1 对 QC 不合格媒体直接阻断，不自动改写。
- pytest 提供单元、契约、集成和恢复测试。

当前每天只有三个 Slot、单个 LifePack 在同一工作目录完成，不需要 FastAPI、Redis、Celery 和分布式调度。PostgreSQL 使用短事务和 `FOR UPDATE SKIP LOCKED` 防止重复领取，媒体文件仍只保存在执行机器本地。Windows PowerShell 是一等运行入口；外部调度器可选。

## 按需执行语义

- `DailyLifePack.date` 是内容日期，不是生成预约。
- `run-next` 不读取当前小时，只领取最早的 approved/frozen 内容包。
- `run-next --target-date YYYY-MM-DD` 对内容日期做精确筛选；过去、当天和未来日期都合法。
- 后续 `run-pack <lifePackId>` 精确执行指定包。
- morning/noon/evening 固定映射为1、2、3，但不会拆成三个定时任务。
- `content.timezone=Asia/Shanghai` 只负责内容日期默认值和时间戳展示。
- Windows Task Scheduler、cron 和 systemd 只调用 CLI，不进入领域模型，也不拥有状态转换权限。

## PostgreSQL 安全边界

- 环境变量使用 `CAT_VIDEO_DB_HOST/PORT/NAME/USER/PASSWORD/SSLMODE/SCHEMA`，代码通过 `SQLAlchemy.URL.create()` 构建连接。
- 密码不得出现在配置示例、日志、异常文本或交付 manifest。
- 默认正式迁移和运行要求 `sslmode=require|verify-ca|verify-full`。
- 当前用户明确授权的例外只匹配 `vedio-appdb.cat_video`、`sslmode=disable` 和 `CAT_VIDEO_ALLOW_INSECURE_RUNTIME=true`；其他明文数据库仍被拒绝。
- `cvg db validate-remote --allow-insecure-write-test` 保留为其他服务器的随机临时 Schema 隔离诊断，不是当前正式 Schema 的主要验证入口。
- 本地 Testcontainers 仅在 loopback、`test*` 数据库和显式测试开关同时满足时允许无 SSL。
- 连接池固定为3，最大溢出2，启用 pre-ping 和900秒 recycle。
- PostgreSQL 最低版本14，集成测试使用 PostgreSQL 16。
- 当前正式账号按已确定方案继续使用 `postgres`，密码只保存在被 Git 忽略的 `.env` 或 PowerShell 环境；已经暴露的旧密码仍建议轮换。
- 当前服务器不支持 PostgreSQL SSL，正式 Schema 和业务任务暂时通过显式明文许可运行；诊断必须持续显示该架构债务。

### 连接、超时与失败语义

- `pool_size=3`、`max_overflow=2`、`pool_pre_ping=true`、`pool_recycle=900`。
- `connect_timeout=5s`、`statement_timeout=30s`、`lock_timeout=5s`、`idle_in_transaction_session_timeout=60s`。
- 每条连接设置 `application_name=cat-video-generator`，便于服务端审计。
- 默认隔离级别使用 PostgreSQL `READ COMMITTED`。
- 只对已经确认无副作用的只读查询做有限重试；不自动重放状态转换或收费任务提交。
- 提交结果未知时，依据业务幂等键和已有记录恢复。生成任务幂等键由 `episodeId + renderRevision + jobType + clipIndex + normalizedInputHash` 派生。
- Seedance 调用、任务轮询和视频下载期间不保持数据库事务；每次状态变化使用短事务。
- `status` 在数据库不可用时只返回数据库错误，不根据本地目录推测业务状态。

### 迁移与远程验收顺序

1. CLI 从被忽略的 `.env` 读取连接，PowerShell 环境变量具有更高优先级。
2. 运行只读 doctor，确认 PostgreSQL版本、数据库、用户、`pg_stat_ssl` 和明文授权状态。
3. `cvg db upgrade` 在空 Schema 上创建正式 `cat_video`；已有未知对象且无 Alembic 版本表时拒绝接管。
4. 迁移到 `0002_content_and_reviews`，保留十张业务表和版本表。
5. `cvg db current` 确认 Schema 权限和 Alembic head。
6. `cvg db validate-runtime` 对比字段、类型、约束和索引，并执行事务、幂等、`SKIP LOCKED` 和交付原子性检查。
7. 验证记录只按本次 UUID 清理，不删除 Schema 或既有业务数据。
8. 在 Testcontainers 空库保留 upgrade/downgrade 和破坏性回归；正式 Schema 不执行 downgrade。
9. 配置 Ark Key 后执行一条8秒 morning smoke，再扩展三时段交付。

当前正式 `cat_video` 已在 PostgreSQL 16.13 上迁移并通过运行验证。正式 Schema 禁止清空、降级和宽泛删除；运行验证只清理自身 UUID 数据。常规破坏性迁移回归仍优先在 Testcontainers PostgreSQL 16 中执行。

## 代码边界

当前目录：

```text
src/cat_video_generator/
  cli.py
  commands/
  generation/
    service.py
    visual_assets.py
    jobs.py
    continuity.py
  contracts.py / state.py / visual_policy.py / render_plan.py
  models.py / db.py / repository.py
  ark_provider.py
  media.py
  delivery.py
tests/
alembic/
```

职责：

- `contracts/state/visual_policy/render_plan`：内容契约和纯领域规则。
- `models/db/repository`：SQLAlchemy模型、连接、锁和幂等查询。
- `ark_provider.py`：Create/Get、请求映射、供应商状态和错误分类。
- `media`：下载、哈希、原子写入、ffprobe 和条件式 FFmpeg。
- `generation/service`：LifePack/Slot状态机、依赖和 fallback。
- `generation/visual_assets`：Canon、直接参考和按需关键帧。
- `generation/jobs`：收费任务幂等、重试、轮询、下载和 QC。
- `delivery`：三条资产选择、目录构建、manifest 和连续状态提交。
- `commands`：CLI参数与输出；根 `cli.py` 只组合命令。

不得创建只改名、格式化路径或转发参数的薄 Manager/Service。存储边界只有在真正拥有原子写入、哈希、冲突策略和恢复语义时才单独抽象。

## 数据库设计

### daily_life_packs

- `id`
- `life_pack_id`
- `date`
- `plan_revision`
- `day_context_json`
- `status`
- `is_degraded`
- `approved_at`
- `frozen_at`
- `ready_at`
- `delivered_at`
- `created_at`
- `updated_at`

约束：`UNIQUE(life_pack_id, plan_revision)`。

### daily_slots

- `id`
- `daily_life_pack_id`
- `slot`
- `sort_order`
- `status`
- `selected_variant_id`
- `last_error`
- 时间戳

约束：

- `CHECK sort_order BETWEEN 1 AND 3`。
- slot/sort_order 固定映射 CHECK。
- `UNIQUE(daily_life_pack_id, slot)`。
- `UNIQUE(daily_life_pack_id, sort_order)`。
- `(id, selected_variant_id)` 组合外键指向 EpisodeVariant 的 `(daily_slot_id, id)`，禁止选择其他 Slot 的 Variant。

### episode_variants

- `id`
- `daily_slot_id`
- `episode_id`
- `role=primary|content_fallback`
- `episode_spec_json`
- `render_plan_json`
- `active_render_revision`
- `status`
- `last_error`

fallback 不重复保存 sort_order；它通过 daily_slot 继承顺序。

### generation_jobs

- `id`
- `episode_variant_id`
- `render_revision`
- `job_type`
- `clip_index`
- `normalized_input_hash`
- `idempotency_key`
- `provider`
- `provider_task_id`
- `status`
- `attempt_no`
- `request_snapshot_json`
- `error_code`
- `error_message`
- `created_at`
- `submitted_at`
- `completed_at`
- `downloaded_at`

约束和索引：

- `UNIQUE(idempotency_key)`。
- `provider_task_id IS NOT NULL` 时全局唯一。
- 活跃状态 `submitting/submission_unknown/queued/running` 使用部分索引。

### media_assets

- `id`
- `episode_variant_id`
- `generation_job_id`
- `asset_kind`
- `storage_path`
- `sha256`
- `byte_size`
- `container`
- `video_codec`
- `audio_codec`
- `width`
- `height`
- `duration_ms`
- `has_audio`
- `qc_status`
- `qc_report_json`
- `created_at`

路径唯一；SHA-256 建索引。供应商原始视频和修复视频分别记录，不能覆盖。

### delivery_packages / delivery_items

DeliveryPackage 保存 lifePack、delivery revision、root path、manifest path、manifest hash 和状态。DeliveryItem 保存：

- `delivery_package_id`
- `sort_order`
- `slot`
- `episode_variant_id`
- `media_asset_id`
- `file_name`

约束和索引：

- `PRIMARY KEY(delivery_package_id, sort_order)`。
- slot 与 sort_order 固定映射为 morning/1、noon/2、evening/3。
- `(daily_life_pack_id, delivery_revision)` 唯一，`delivery_revision` 另有查询索引。

### continuity_events

只追加已交付 Variant 的 stateWrites。历史更正新增 replace 或 retire，不修改旧记录。

## CLI

当前已实现：

```text
cvg doctor [--allow-insecure-readonly-smoke]
cvg db upgrade
cvg db current
cvg db validate-remote --allow-insecure-write-test
cvg db validate-runtime
cvg canon import --role <person|cat|style> --asset-id <id> --file <path>
cvg validate-pack <file>
cvg import-pack <file>
cvg approve-pack <lifePackId>
cvg status [lifePackId]
cvg run-next [--target-date YYYY-MM-DD] --allow-paid-generation
cvg run-pack <lifePackId> [--slot <slot>] --allow-paid-generation
cvg review <assetId> --approve|--reject --reason <text>
cvg resume [lifePackId]
cvg deliver <lifePackId>
```

- `doctor`：检查数据库、迁移、Ark Key 是否配置以及 FFmpeg/ffprobe 发现状态，不产生收费请求。
- `db upgrade/current/validate-runtime`：分别负责正式受控迁移、状态检查和保留 Schema 的运行验证；`validate-remote` 只用于随机隔离技术诊断。
- `validate-pack`：只运行 Schema 和连续性校验。
- `import-pack`：创建 LifePack、Slot 和 Variant 记录。
- `approve-pack`：批准内容但不产生收费任务。
- `run-pack`：只调用真实 Ark；先落幂等任务意图，再推进到关键帧审核、视频审核、失败或 ready。
- `run-next`：用 `FOR UPDATE SKIP LOCKED` 原子选择最早的 approved/frozen 内容包，可按内容日期筛选。
- `review`：人工确认长期 Canon 参考、按需场景关键帧或视频内容。
- `resume`：只恢复已经存在的任务、轮询、下载或 QC，不为缺失任务创建新的收费 POST。
- `deliver`：严格构建三条完整交付包。

## 分阶段实施

### 第一批：契约和工程骨架

1. 创建 pyproject、锁文件、源码与测试目录。
2. 建立配置模型和 `cvg doctor`。
3. 将现有 JSON Schema 映射为领域模型。
4. 实现 DailyLifePack 跨对象校验。
5. 建立 PostgreSQL、Alembic、`cat_video` Schema 和排序 CHECK。
6. 用旅行、钓鱼和 DeliveryManifest 示例建立契约测试。

完成标准：

- 所有示例通过校验。
- 非法 slot/sort_order 被数据库拒绝。
- 运行 `doctor` 只连接 PostgreSQL 执行只读检查，不产生数据库写入或收费模型请求。

### 第二批：内容导入和状态机

1. 实现 validate/import/approve/status。
2. 导入器由 Slot 推导 sort_order。
3. fallback 保存为同 Slot Variant。
4. 冻结 planRevision，重试只增加 renderRevision。
5. 实现 Slot、Variant 和 Job 状态转换。

完成标准：

- 同一文件重复导入不产生重复记录。
- 未批准 LifePack 不能调用媒体模型。
- continuation 只能依赖更小 sort_order。

### 第三批：视觉输入决策与按需 Seedream

1. 对人物本体、猫咪本体和画风参考执行一次长期 Canon 审核并记录不可变资产。
2. 读取 EpisodeSpec `visualControl`，按精确结尾、其他视觉风险、低风险的优先级选择视觉输入模式。
3. `direct_references` 直接编译人物、猫咪和画风参考，不创建图片任务。
4. `generated_first_frame` 或 `generated_first_last_frames` 编译关键帧 Prompt，调用 Seedream 并立即下载图片。
5. 对场景关键帧执行哈希、原子保存、媒体记录和人工 approve/reject。
6. 保存视觉输入模式、决策原因以及全部来源资产 ID 和哈希。

完成标准：

- 未批准 Canon 参考不能进入任何 Seedance 任务。
- 直接参考路径不会创建 Seedream 任务。
- 场景关键帧模式在关键帧批准前不能进入 Seedance。
- 重启后不会重复生成相同场景关键帧任务。

### 第四批：Seedance 和任务恢复

1. 映射 single_pass、三种视觉输入模式、时长、比例和音频参数。
2. 创建任务前写入 submitting 记录。
3. 保存 provider task ID 并轮询标准状态。
4. 依据 error.code 和 message 区分重试。
5. POST 结果未知时进入 submission_unknown。
6. 每个 Slot 限制两次收费视频尝试。

完成标准：

- 相同幂等键不会创建第二个收费任务。
- 进程重启后可继续轮询。
- submission_unknown 不会自动重复 POST。
- `cvg reconcile-job <jobId>` 只列出已配置模型的 Ark 任务候选；人工核对后通过 `--provider-task-id` 显式绑定，再交给 `resume`，系统不会自动猜测匹配。

### 第五批：下载、QC 和条件式后期

1. 流式写入同盘 `.part`。
2. 边下载边计算 SHA-256。
3. 校验 HTTP、大小、MP4 特征和 ffprobe。
4. 实现自动媒体报告和人工 content_review。
5. QC 通过时 passthrough。
6. 仅按问题触发 remux、transcode、replace_audio 或 hybrid_mix。
7. 内容哈希寻址保存最终资产。

完成标准：

- 下载中断不会留下可见最终 MP4。
- QC 通过路径不会调用 FFmpeg。
- 供应商临时 URL 不进入 manifest。

### 第六批：依赖、fallback 和交付

1. shared_context Slot 允许独立推进。
2. continuation 等待依赖终态。
3. 依赖 ready 时选择主内容；失败时激活 fallback。
4. 三个 Slot ready 后构建 `.building-*`。
5. 按1、2、3生成固定文件名和 manifest。
6. 验证哈希后原子改名。
7. 幂等提交 delivery records 和 continuity events。

完成标准：

- 旅游三条无依赖可完成交付。
- 钓鱼中午失败时，第三条使用 fallback 且仍为 sortOrder 3。
- 正式目录永远不会只出现一部分文件。

### 第七批：真实账户烟雾测试与可选自动化

1. 使用一条8秒内容验证模型权限、余额、并发和真人素材资格。
2. 确认真实音轨编码、生成耗时和下载行为。
3. 再运行完整三条 DailyLifePack。
4. 验证 Windows PowerShell 可在任意时间执行 `run-pack`、`run-next`、`resume` 和 `deliver`。
5. 只有确实需要无人值守时，才选择性配置 Windows Task Scheduler 或其他外部调度器。

无论手动还是自动触发，都只处理已经批准的计划；没有 approved/frozen LifePack 时安全退出。调度器不改变内容日期、Slot 顺序、数据库安全门或幂等策略。

## 验收测试

### Schema

- 8秒、10秒和15秒 Episode 通过；15001毫秒失败。
- Episode 不再包含发布时间字段。
- Episode 必须完整提供四项 `visualControl`。
- 低风险、首帧和首尾帧 RenderPlan 分别要求0、1、2张场景关键帧。
- 人物、猫咪和画风直接参考合计最多9张，资产角色不能重复。
- DeliveryManifest 恰好包含1、2、3三项。
- 文件名或 Slot 与 sortOrder 错配时失败。

### 数据库

- 重复 Slot、重复 sort_order 和错误映射被拒绝。
- fallback 不建立新的排序行。
- render revision 和 delivery revision 不覆盖旧资产。
- 过去、当天和未来内容日期都能被显式领取。
- 不传目标日期时仍领取最早的 approved/frozen 内容包。
- 未设置显式运行许可时迁移和运行命令拒绝远程明文连接。
- 显式许可只适用于 `vedio-appdb.cat_video`。
- 正式运行验证保留表，只清理本次 UUID 数据。

### 任务

- 覆盖 queued、running、succeeded、failed、expired、cancelled。
- 可恢复429和500退避重试。
- 鉴权、余额、安全和参数错误不重试。
- 创建响应未知不重复 POST。

### 下载

- 空响应、错误格式、超限文件和中断下载失败。
- `.part` 只在完整验证后原子改名。
- 文件哈希、媒体属性和数据库记录一致。

### 连续性

- shared_context 不执行跨 Slot 依赖检查。
- continuation 只依赖排序更早内容。
- fallback 不读取缺失依赖状态。
- stateWrites 只在完整交付后应用。

### 端到端

- 使用测试专用 SDK/HTTP fake 验证状态机；运行时代码不提供 Mock Provider。
- 输出三个 MP4 和通过 Schema 的 manifest。
- 连续运行两次不重复收费、不覆盖旧 revision。
- 在轮询、下载、QC 和目录改名阶段中断后均能恢复。

## 默认假设

- Windows 是本地开发、验证和可选运行环境；同一 LifePack 在同一工作目录完成。
- 远程 PostgreSQL、单 Worker、每天三条，但执行时间不固定。
- 正式数据库默认应启用 SSL 或安全隧道；当前明文运行是用户明确接受的临时架构债务。
- 当前例外允许 `vedio-appdb.cat_video` 保存正式计划、Ark任务和媒体元数据，仍要求固定 Schema 与显式开关。
- DailyLifePack 在 V1 由人工或现有模板准备并批准。
- 每条默认10秒，允许8至15秒。
- 默认 `single_pass + native audio + validate_then_passthrough`。
- 视觉输入默认使用显式风险规则，低风险内容跳过 Seedream。
- 人物和猫咪一致性保留人工审核。
- 正式交付默认要求三条齐全。
- 本地 delivery 目录是当前唯一交付接口。
