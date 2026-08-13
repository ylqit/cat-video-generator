# Windows 运行手册（V5）

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

FFmpeg 与 FFprobe 推荐安装到系统 PATH；`.env` 中的 `FFMPEG_PATH`、`FFPROBE_PATH`
可以留空。有效显式路径优先，失效显式路径会报告警告并回退 PATH。

## 升级到 0018 并修复 Canon

从现有 V4 直接迁移到 V5，然后按清单校验并重链 11 张批准 Canon。命令重复执行幂等，
任何源文件哈希不一致都会拒绝修改数据库：

```powershell
uv run alembic upgrade head
uv run cvg canon-repair --source-dir "风格定稿/Canon-v1"
uv run cvg doctor
```

迁移最终应显示 `0018_v5_shot_assistance`；`doctor` 分别报告数据库、Ark、FFmpeg、
FFprobe、视频生成和本地合成状态。

## Web 操作

1. 创建项目并填写第一场景原文。
2. 选择单片段或填写 2～6 个多片段目标数，依次运行剧情诊断、选择方案、剧情重写并接受人工编辑稿；每一步单独确认规划模型费用。
3. 运行分镜导演，在建议表单中编辑造型、环境、姿态、构图、片段标题、2～4 个编号子镜头和时长后接受。
4. 在 Canon 页面确认 5–7 岁短发儿童、灰白猫和画风文本/参考；旧项目引用异常时点击“恢复 Canon 引用”。
5. 打开场景定妆工作台，编辑草稿、选择职责参考、检查 Prompt 预览后按需生成；该建议不阻断视频。
6. 在版本画廊中大图查看并批准/选择定妆；片段可切换项目继承、场景定妆和自定义素材。
7. 为每个片段选择关闭、只继承造型、完整参考或派生锚点；保存后逐次选择“仅保存”或付费 LLM 分析。
8. 逐项勾选 LLM 字段或正文候选；过期 Revision 必须重新分析。右侧分别查看创作正文、系统技术外壳、图片顺序/职责和最终 Prompt。
9. 选择纯文本、已有图片或生成新锚点，确认 Seedance 费用；人工观看后批准或拒绝。
10. 批准视频会本地抽取尾帧；下一片段可采用，来源视频变化时旧尾帧会标为过期。
11. 重做只影响当前片段，旧 attempt 和媒体保留；批准片段可合成总片或区间重拍。

## 恢复边界

- 已有视频 Task ID：点击“继续查询原任务”。
- `submission_unknown`：点击“查询候选并对账”，不得直接重做。
- 图片同步超时：供应商无持久 Task ID；再次生成前需接受潜在重复计费。
- Canon/定妆显示 HTTP 404：查看资产 ID；`contentReady=false` 或 `legacy:` 时执行
  `cvg canon-repair`（Canon）或重新上传项目图片。不要把旧机器绝对路径写回数据库。
- 定妆生成返回 409：页面草稿 Revision 已过期，重新打开工作台、预览并保存后再提交。
- 编辑或 Prompt 预览不收费；生成、重新生成和区间重拍均逐次确认费用。
- 片段保存不收费；`/assist` 必须再次明确确认。分析超时只记录失败，不回滚已保存草稿。
- 尾帧不可用：先检查 `cvg doctor` 的 FFmpeg 状态；批准记录不会因抽帧失败而回滚，可在下一片段重新采用时重试。

## 最终本地验收

```powershell
uv run ruff check .
npm --prefix web run typecheck
npm --prefix web test
npm --prefix web run build
git diff --check
uv run cvg doctor
uv run python scripts/local_dataflow_smoke.py
```

烟测使用替身 Provider，不读取 Ark Key、不访问网络。真实画面质量由用户在 Web 中验证。
