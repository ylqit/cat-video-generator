# 人物与猫咪本地三时段视频系统

本目录描述一个以固定人物和固定灰白猫为双主角的 AI 原生 IP 生活流生产系统。系统每天生成 morning、noon、evening 三段竖屏短视频；三段共享当天生活背景和角色状态，但可以位于不同场景、发生不同活动。

当前产品边界是本地生成、下载、质检、保存和交付三条 MP4，不包含微信小程序、公开视频 URL、CDN/TOS 上传、定时发布或其他展示系统。

`DailyLifePack.date` 只表示内容所属日期，不是生成预约时间。已经批准的内容包可以提前、当天或延后执行；morning、noon、evening 只表示生活时段和1、2、3的交付顺序。手动 PowerShell 是正式支持的触发方式，Windows Task Scheduler 等调度器只是可选外部入口。

## 已锁定方向

- 双主角：person 与 cat 是平等搭档。
- 内容基调：治愈陪伴为主，轻喜剧为辅。
- 连续性：持续存在的角色、背景和状态，不等于每天必须讲完整故事。
- 内容类型：observation、micro_event 和少量 continuation。
- 画面：全画面彩铅、蜡笔、粉彩儿童绘本，不是真人实拍加2D贴纸。
- 声音：Seedance 原生环境声、动作音效和可选轻音乐；无对白、旁白和歌词。
- 生成：低风险内容直接使用已批准人物、猫咪和画风参考；高风险内容由 Seedream 按需生成1至2张场景关键帧，Seedance 默认一次生成8至15秒完整音视频。
- 接入：默认 Agent Plan Large，也支持显式切换到标准按量 Ark；两种
  profile 共用生成、恢复、审核和下载链路，但不复用任务幂等记录。
- 后期：合格原始 MP4 直接保存，FFmpeg 只作条件式修复。
- 交付：固定输出 `01-morning.mp4`、`02-noon.mp4`、`03-evening.mp4` 和 `manifest.json`。

## 文档导航

- [本地系统架构](architecture/daily-content-system.md)
- [代码模块与依赖边界](architecture/code-boundaries.md)
- [本地实施计划](implementation/local-tri-slot-delivery-plan.md)
- [每日生产工作流](workflows/daily-production.md)
- [Windows 按需执行与验证](workflows/windows-on-demand-validation.md)
- [正式 PostgreSQL 运行验收记录](workflows/postgresql-runtime-validation.md)
- [Agent Plan morning 真实烟测记录](validation/agent-plan-morning-smoke-2026-07-27.md)
- [本地视频交付包](delivery/local-video-package.md)
- [三时段生活流](content/tri-slot-life-stream.md)
- [系列、角色与世界 Bible](content/series-bible.md)
- [外部信号与编辑政策](content/editorial-policy.md)
- [火山引擎 Ark 接入](providers/volcengine-ark.md)
- [真实 Ark 链路运行手册](workflows/ark-real-chain-runbook.md)
- [Sowii 公开资料证据边界](research/sowii-evidence.md)

## 数据目录

- `content/schemas/`：DailyLifePack、EpisodeSpec、RenderPlan、DeliveryManifest、LifeChapter 和模板 Schema。
- `content/templates/`：observation、micro_event 和 continuation 模板。
- `content/examples/`：旅游生活流、钓鱼部分承接、旅行章节、三种视觉输入的单次成片与本地交付示例。
- `content/bibles/`：人物、猫咪、关系、世界和系列 Canon。
- `config/providers.example.yaml`：不含密钥的供应商、本地存储和媒体配置。

供应商 Prompt 是 EpisodeSpec 和 RenderPlan 的下游编译产物。角色、世界、排序和连续状态不得散落在每日 Prompt 中。

## 当前仓库状态

当前仓库已经落地真实 Ark 模块化单体链路：内容与 Canon 导入、审核、视觉风险决策、按需 Seedream、Seedance 异步任务、即时下载、SHA-256 不可变存储、ffprobe QC、人工审核、continuation 等待/fallback、1/2/3 本地交付和恢复接口。LifePack 编排、视觉资产、Ark 任务执行和 CLI 已形成独立职责边界。运行时可选 Agent Plan 与标准 Ark 两种真实 API profile，不存在 Mock Provider 或 Mock 配置；自动测试只在进程内替换 SDK/HTTP 边界，不形成可选择的业务链路。

正式 `vedio-appdb.cat_video` 已在用户明确授权下通过明文连接迁移；当前目标
revision 为 `0003_slot_retry_events`，增加了终态任务的显式人工重试审计。
该例外必须同时满足固定数据库、固定 Schema 和
`CAT_VIDEO_ALLOW_INSECURE_RUNTIME=true`；切换 SSL 后关闭该开关即可，不
需要搬迁数据。真实生成仍要求 `ARK_API_KEY` 和显式
`--allow-paid-generation`。首帧真实生成已验证；当前 Key 的视频请求被
供应商以 `UnsupportedModel` 拒绝，尚无 MP4，必须先核实实际视频权益，
不得直接重复付费提交。详见[真实 Ark 链路运行手册](workflows/ark-real-chain-runbook.md)。
