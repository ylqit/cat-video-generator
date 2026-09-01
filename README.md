# Cat Video Generator

面向个人创作者的最小“一人一猫”短视频生产系统。

产品只有一条业务主线：

```text
项目＋固定 Canon
→ 当前故事
→ 1–6 个当前镜头
→ 冻结生成输入
→ Provider 任务
→ 选择媒体版本
→ 时间线与导出
```

Web 页面只提供三个项目入口：

- 剧本：编辑创作要求、故事候选和当前长文本正文。
- 角色资产：管理固定儿童、固定猫咪、净化画风板和按需参考。
- 生产：编辑镜头、预览冻结输入、生成视频、选片和编排时间线。

## 数据边界

在线数据库只有七张业务表：

```text
creator_projects
creator_shots
media_assets
generation_snapshots
generation_tasks
generation_task_events
creator_timelines
```

创作内容保持宽松；实际付费执行严格冻结 Prompt、有序参考、Provider 参数、费用和输入哈希。任务继续使用幂等键、Worker lease、Provider task ID、恢复与取消语义。

项目必须固定且唯一绑定：

- `child_identity`
- `cat_identity`
- `style_board`

`style_source`不能进入日常 Provider 输入。

## 本地启动

```powershell
uv sync --extra dev
uv run alembic upgrade head

# API
uv run cvg api

# Worker
uv run cvg-media-worker

# Web
npm --prefix web install
npm --prefix web run dev -- --host 0.0.0.0
```

浏览器打开 `http://localhost:5173/projects`。

## 验证

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests alembic
.\.venv\Scripts\python.exe -m pytest -q
npm --prefix web run test
npm --prefix web run typecheck
npm --prefix web run build
git diff --check
git ls-files "*.mp4"
```

历史成功成片只保存在被 Git 忽略的 `local-production-archive/`。数据库切换临时备份位于被忽略的 `migration-backups/`，完成验收后删除。
