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

## 3. 临时注入 PostgreSQL 配置

不要把真实密码写进 `.env.example`、PowerShell 脚本或命令历史。使用当前 PowerShell 进程的临时环境变量：

```powershell
$env:CAT_VIDEO_DB_HOST = "<database-host>"
$env:CAT_VIDEO_DB_PORT = "5432"
$env:CAT_VIDEO_DB_NAME = "vedio-appdb"
$env:CAT_VIDEO_DB_USER = "postgres"
$env:CAT_VIDEO_DB_SSLMODE = "disable"
$databasePassword = Read-Host "PostgreSQL password" -AsSecureString
$env:CAT_VIDEO_DB_PASSWORD = [Net.NetworkCredential]::new("", $databasePassword).Password
```

`CAT_VIDEO_DB_PASSWORD` 在子进程环境中仍是明文，只是不会进入命令历史。完成验证后必须从当前会话清除：

```powershell
Remove-Item Env:\CAT_VIDEO_DB_PASSWORD -ErrorAction SilentlyContinue
$databasePassword = $null
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

当前连接未加密时，报告必须包含未使用 SSL 的警告。该命令不创建 Schema、表或业务记录。

## 5. 一次性隔离写入验证

用户明确接受临时公网明文写测时，执行：

```powershell
uv run cvg db validate-remote --allow-insecure-write-test
```

该命令的安全边界：

1. 生成唯一 `cat_video_validation_<runId>` Schema 名。
2. 确认实际数据库名称和最低 PostgreSQL 版本。
3. 拒绝复用任何已经存在的 Schema。
4. 只在随机验证 Schema 中运行 Alembic。
5. 验证事务回滚、morning/1排序约束、生成任务幂等和 `FOR UPDATE SKIP LOCKED`。
6. 不读取 `ARK_API_KEY`，不调用 Seedream 或 Seedance。
7. 完成后执行 `DROP SCHEMA ... CASCADE`，只删除本次创建的精确 Schema。
8. 如果清理连接失败，错误会返回待检查的完整验证 Schema 名；不得扩大删除范围。

该开关不会授权以下命令通过明文连接：

```powershell
uv run cvg db upgrade
uv run cvg run-next --allow-paid-generation
```

两条命令都必须继续失败。正式 `cat_video` Schema 的迁移和业务运行仍要求 `sslmode=require|verify-ca|verify-full` 或安全隧道。

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

调度时间可由运营自由调整。任务不能直接更新 PostgreSQL，也不能绕过计划批准、SSL检查、Alembic revision、依赖状态或幂等键。

## 8. 验证检查表

- `uv run pytest -m "not postgres"` 全部通过。
- `cvg run-next --help` 显示 `--target-date`。
- 非法日期在连接数据库前失败。
- 只读 doctor 不创建任何对象。
- 隔离写测返回所有 checks 为 `true` 且 `cleanup_succeeded=true`。
- 数据库中不残留 `cat_video_validation_*` Schema。
- 普通迁移和运行命令仍拒绝明文远程连接。
- ffprobe 可发现；如准备执行条件式修复，ffmpeg 也可发现。
- 全仓不存在运行时 Mock Provider、Mock 视频路径或 Provider 模式切换。
- 任何输出和异常都不显示数据库密码。

## 常见故障

| 现象 | 处理 |
| --- | --- |
| `Missing required database environment variables` | 在同一个 PowerShell 会话补齐数据库环境变量 |
| `connection refused` 或超时 | 检查主机、端口、防火墙和 PostgreSQL监听配置 |
| `Unencrypted PostgreSQL is restricted` | 普通运行必须启用 SSL/隧道；临时验证只能使用专用命令 |
| `validation schema cleanup failed` | 只检查错误中给出的精确 `cat_video_validation_*` Schema，不使用宽泛删除 |
| Alembic revision 不匹配 | 在安全连接上执行正式 `cvg db upgrade` |
| 找不到 ffmpeg/ffprobe | 安装工具并重新打开 PowerShell，使新的 `PATH` 生效 |
| 重复收费风险 | 检查 GenerationJob 幂等记录，不能直接重放供应商 POST |
| 下载中断 | 恢复 `.part`/任务状态，不能把不完整文件当成最终 MP4 |
