# Cat Video Generator

人物与猫咪三时段生活流视频的本地生产与交付系统。

- 运行时只接入真实火山方舟 API，不提供 Mock Provider；支持标准按量 Ark
  与 Agent Plan 两种显式访问模式，当前默认标准 Ark API。
- 低风险内容直接使用批准的人物、猫咪和画风参考；高风险内容按需调用 Seedream 生成首帧或首尾帧。
- 每个 Episode 由 Seedance 单次生成8～15秒竖屏原生音视频。
- PostgreSQL 保存计划、任务、审核和交付元数据；图片、视频与交付包保存在本机。
- 最终输出固定为 `01-morning.mp4`、`02-noon.mp4`、`03-evening.mp4` 和 `manifest.json`。
- 不包含微信小程序、HTTP API、对象存储、CDN 或定时发布。

完整设计见[文档总览](docs/README.md)，首次真实测试按[真实 Ark 链路运行手册](docs/workflows/ark-real-chain-runbook.md)执行。

基础验证：

```powershell
uv sync --extra test
uv run pytest -q
uv run cvg --help
```

标准 Ark 默认使用 `/api/v3`、Seedream 5.0 Lite 和
`doubao-seedance-2-0-mini-260615`；Agent Plan 使用 `/api/plan/v3`
及其套餐模型别名。两套配置和 Key 不能混用，访问模式会进入任务幂等输入。操作者也可
显式使用 `doubao-seedance-1.5-pro` 或
`doubao-seedance-1.5-pro-即将下线`；程序按配置原样提交，不会自动截断、
追加后缀或降级模型。

当前远程 `vedio-appdb.cat_video` 已允许在显式
`CAT_VIDEO_ALLOW_INSECURE_RUNTIME=true` 时通过明文 PostgreSQL 运行；诊断会持续标记该临时架构债务。真实生成还必须具备最新 Alembic revision、ffprobe、批准的 Canon、`ARK_API_KEY`，并在命令中显式使用 `--allow-paid-generation`。ffmpeg 只在后续条件式媒体修复时需要。

2026-07-27 已切换标准 Ark，并使用新的10秒内容 revision。标准视频请求
已到达 `doubao-seedance-2-0-mini-260615`；模型开通与限额解除后已成功
生成、下载并通过技术 QC 的 morning MP4，当前等待最终人工音画审核。详见
[标准 Ark morning 真实烟测记录](docs/validation/standard-ark-morning-smoke-2026-07-27.md)。
