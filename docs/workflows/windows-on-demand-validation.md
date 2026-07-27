# Windows 按需执行与验证

## 目的

Windows 本机可以完成契约测试、CLI 测试、PostgreSQL 诊断和后续视频生产。系统没有“前一晚20:00”启动门槛：

- `DailyLifePack.date` 是内容日期。
- `morning/noon/evening` 是内容时段和1、2、3排序。
- PowerShell 命令实际在几点运行，不改变内容和连续性。
- Task Scheduler 仅在需要无人值守时使用。

当前仓库已经实现真实 Seedream/Seedance、媒体下载、QC、`run-pack/resume/deliver`。不带 `--allow-paid-generation` 或没有 `ARK_API_KEY` 时，预检会在创建 Ark 任务前失败；普通测试不会请求 Ark，也不会产生模型费用。

## 1. 本地准备

支持 Python 3.12 和3.13。建议在仓库根目录运行：

```powershell
Set-Location D:\soft\code\OpenGit\cat-video-generator
uv sync --extra test
uv run cvg --help
uv run cvg run-next --help
uv run cvg db validate-remote --help
```

如果不使用 `uv`，可以创建 `.venv` 后执行 `python -m pip install -e ".[test]"`。

检查媒体工具：

```powershell
Get-Command ffmpeg -ErrorAction SilentlyContinue
Get-Command ffprobe -ErrorAction SilentlyContinue
```

Schema 与数据库测试不依赖媒体工具。真实视频 QC 必须能发现 ffprobe；ffmpeg 只在执行条件式修复时需要。缺少 ffprobe 时，收费任务预检会失败，不会静默跳过。

## 2. 不连接数据库的测试

```powershell
uv run pytest -m "not postgres"
```

这组测试覆盖 JSON Schema、视觉输入决策、数据库安全配置、CLI 参数和日期格式，不需要 Docker、Ark 账号或远程 PostgreSQL。

需要在本机启动一次性 PostgreSQL 16 回归测试时，可以安装 Docker Desktop 后运行：

```powershell
uv run pytest -m postgres
```

Docker/Testcontainers 是开发回归手段，不是日常生成视频的运行前提。

## 3. 本地 `.env` 配置

不要把真实密码写进 `.env.example`、PowerShell 脚本或命令历史。
真实值写在已被 `.gitignore` 排除的 `.env`：

```dotenv
CAT_VIDEO_DB_HOST=<database-host>
CAT_VIDEO_DB_PORT=5432
CAT_VIDEO_DB_NAME=vedio-appdb
CAT_VIDEO_DB_USER=postgres
CAT_VIDEO_DB_PASSWORD=<password>
CAT_VIDEO_DB_SSLMODE=disable
CAT_VIDEO_DB_SCHEMA=cat_video
CAT_VIDEO_ALLOW_INSECURE_RUNTIME=true

ARK_ACCESS_MODE=agent_plan
ARK_AGENT_PLAN_TIER=large
ARK_API_KEY=
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/plan/v3
ARK_IMAGE_MODEL=doubao-seedream-5.0-lite
ARK_VIDEO_MODEL=doubao-seedance-2.0-mini
```

CLI 启动时自动读取当前工作目录的 `.env`，但不会覆盖 PowerShell 已有
环境变量。需要临时覆盖密码时仍可使用安全输入：

```powershell
$databasePassword = Read-Host "PostgreSQL password" -AsSecureString
$env:CAT_VIDEO_DB_PASSWORD = [Net.NetworkCredential]::new("", $databasePassword).Password
```

## 4. 明文只读诊断

```powershell
uv run cvg doctor --allow-insecure-readonly-smoke
```

只读诊断检查：

- TCP和PostgreSQL连接。
- `current_database()` 与 `current_user`。
- PostgreSQL版本。
- `pg_stat_ssl`。
- 配置 Schema 权限与 Alembic revision。
- 连接池再次取用。

当前连接未加密时，报告必须包含 `transport_security=plaintext`、
`insecure_runtime_authorized=true` 和临时架构债务警告。该命令不创建
Schema、表或业务记录。

Ark 静态预检同时返回：

- `arkAccessMode=agent_plan`。
- `agentPlanTier=large` 或 `max`。
- `agentPlanTierVerification=declared_only`。
- `endpointProfile=agent_plan`。
- `generationConfigurationValid=true`。
- `providerEntitlementVerification=not_performed`。

这些字段只说明 URL、模式、套餐和模型组合自洽，不会调用供应商，也不会
验证 Key 类型、Key 所属账号或真实套餐权益。输出不包含 Key 或完整鉴权
信息。无前缀的 `API_KEY` 和 `BASE_URL` 不会被读取。

## 5. 正式 Schema 迁移与验证

当前已明确授权 `vedio-appdb.cat_video` 使用明文连接。执行：

```powershell
uv run cvg db upgrade
uv run cvg db current
uv run cvg db validate-runtime
```

安全边界：

1. 明文正式运行只匹配数据库 `vedio-appdb` 和 Schema `cat_video`。
2. 必须显式设置 `CAT_VIDEO_ALLOW_INSECURE_RUNTIME=true`。
3. 已有非空 Schema 没有 Alembic 版本表时拒绝自动接管。
4. `db upgrade` 只向最新 revision 升级，不执行 downgrade 或清空。
5. `db validate-runtime` 对比表、字段、类型、主外键、CHECK、唯一约束和索引。
6. 事务、幂等和 `SKIP LOCKED` 测试使用唯一 `validationRunId`。
7. 验证结束只删除本次 UUID 数据，正式表和业务数据保留。
8. 该命令不读取 `ARK_API_KEY`，不调用 Seedream 或 Seedance。

一次性随机 Schema 命令仍保留用于其他服务器的隔离技术诊断：

```powershell
uv run cvg db validate-remote --allow-insecure-write-test
```

未来启用 TLS 后改为 `CAT_VIDEO_DB_SSLMODE=require` 并关闭明文许可，不需要
迁移 `cat_video` 数据。

## 6. 按内容日期领取

在安全数据库已经迁移且存在 approved/frozen 内容包后：

```powershell
# 领取日期最早的已批准内容包
uv run cvg run-next

# 只领取指定内容日期；过去、当天和未来日期都允许
uv run cvg run-next --target-date 2026-08-01 --allow-paid-generation
```

`--target-date` 必须是严格的 `YYYY-MM-DD`。它不会等待该日期到达，也不会把输入自动改成“次日”。没有匹配内容时返回 `claimedLifePackId: null`，不会创建媒体任务。

当前 `run-next` 在原子领取后进入真实 Ark 编排。以下接口使用相同的按需触发语义：

```powershell
uv run cvg run-pack <lifePackId> --allow-paid-generation
uv run cvg resume <lifePackId> --allow-paid-generation
uv run cvg deliver <lifePackId>
```

`run-pack/run-next/resume` 在 Ark 链路上要求显式确认开关；没有 `ARK_API_KEY` 时会在创建任务前失败。`resume` 只恢复已存在的任务，不会因为某个 Slot 仍为 `planned` 而创建新的收费请求。

## 7. 可选 Task Scheduler

Task Scheduler 不是必经步骤。确实需要无人值守时，它只负责在选定时间启动同一个命令，例如：

```text
Program:  uv
Arguments: run cvg run-next
Start in: D:\soft\code\OpenGit\cat-video-generator
```

调度时间可由运营自由调整。任务不能直接更新 PostgreSQL，也不能绕过计划批准、数据库传输安全门、Alembic revision、依赖状态或幂等键。

## 8. 验证检查表

- `uv run pytest -m "not postgres"` 全部通过。
- `cvg run-next --help` 显示 `--target-date`。
- 非法日期在连接数据库前失败。
- 只读 doctor 不创建任何对象。
- `db current` 返回 `ready_for_runtime=true`。
- `db validate-runtime` 的结构、事务、幂等、锁和清理 checks 全为 `true`。
- 正式 `cat_video` 表保留，验证 UUID 数据不存在。
- 缺少 `CAT_VIDEO_ALLOW_INSECURE_RUNTIME=true` 时明文迁移和运行仍拒绝。
- ffprobe 可发现；如准备执行条件式修复，ffmpeg 也可发现。
- 全仓不存在运行时 Mock Provider 或 Mock 视频路径；只允许显式的
  `agent_plan` 与 `standard` Ark 访问模式。
- Agent Plan Large/Max、Plan URL 和两个固定模型别名通过静态配置检查。
- Standard 模式使用 `/api/v3`、空套餐字段和自身模型/Endpoint ID。
- 任何输出和异常都不显示数据库密码。

## 常见故障

| 现象 | 处理 |
| --- | --- |
| `Missing required database environment variables` | 在同一个 PowerShell 会话补齐数据库环境变量 |
| `connection refused` 或超时 | 检查主机、端口、防火墙和 PostgreSQL监听配置 |
| `Unencrypted PostgreSQL is restricted` | 检查固定数据库、`cat_video` Schema 和显式明文运行开关；或启用 SSL/隧道 |
| `validation schema cleanup failed` | 只检查错误中给出的精确 `cat_video_validation_*` Schema，不使用宽泛删除 |
| Alembic revision 不匹配 | 执行受控 `cvg db upgrade`；已有未知对象时先人工核对 |
| 找不到 ffmpeg/ffprobe | 安装工具并重新打开 PowerShell，使新的 `PATH` 生效 |
| `generationConfigurationValid=false` | 整组检查 `ARK_ACCESS_MODE`、套餐、Base URL 和两个模型；不要混用 Agent Plan 与标准 Ark 配置 |
| Agent Plan 套餐为 Small/Medium | 默认2.0-mini链路需升级至Large/Max；仅在操作者明确选择时允许完整名称 `doubao-seedance-1.5-pro-即将下线`，不会自动降级 |
| Ark 鉴权失败 | 确认 Key 属于当前访问模式；Agent Plan、标准 Ark 和 Coding Plan Key 不能混用 |
| 重复收费风险 | 检查 GenerationJob 幂等记录，不能直接重放供应商 POST |
| 下载中断 | 恢复 `.part`/任务状态，不能把不完整文件当成最终 MP4 |
