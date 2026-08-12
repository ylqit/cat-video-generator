# Windows 运行手册（V4）

## 配置与启动

```powershell
uv sync --extra dev
uv run cvg doctor
uv run cvg api
```

另一个终端启动前端：

```powershell
npm --prefix web install
npm --prefix web run dev
```

访问 `http://localhost:5173/studio`。生产单服务可先构建前端，再执行
`uv run cvg api --static-dir web/dist`。

## 升级到 0015

V4 不兼容旧生产结构。先归档并清理旧业务数据，再迁移；脚本不删除 Canon 或已交付
本地文件：

```powershell
uv run python scripts/archive_v3_and_clear.py
uv run alembic upgrade head
uv run cvg doctor
```

归档 Manifest 与整体 SHA-256 位于 `var/diagnostics/`。迁移最终应显示
`0015_shot_queue_core`，且只保留批准 Canon。

## Web 操作

1. 创建项目并填写第一场景原文。
2. 手工添加镜头，或点击“AI 建议镜头卡”并确认一次文本模型费用。
3. 编辑镜头描述和 8～15 秒时长。
4. 选择纯文本、已有图片或生成新锚点；按职责绑定必要素材。
5. 查看最终 Prompt，显式确认 Seedance 费用并生成片段。
6. 人工观看后批准或拒绝；AI 抽帧结果只是建议。
7. 重做只影响当前镜头，旧 attempt 和媒体保留。
8. 多个批准镜头可本地合成总片；局部问题可在底部时间轴发起区间重拍。

## 恢复边界

- 已有视频 Task ID：点击“继续查询原任务”。
- `submission_unknown`：点击“查询候选并对账”，不得直接重做。
- 图片同步超时：供应商无持久 Task ID；再次生成前需接受潜在重复计费。
- 编辑或 Prompt 预览不收费；生成、重新生成和区间重拍均逐次确认费用。

## 最终本地验收

```powershell
uv run ruff check .
npm --prefix web run build
git diff --check
uv run cvg doctor
uv run python scripts/local_dataflow_smoke.py
```

烟测使用替身 Provider，不读取 Ark Key、不访问网络。真实画面质量由用户在 Web 中验证。
