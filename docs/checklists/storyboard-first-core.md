# 故事板优先内核落地Checklist

## 契约与校验

- [x] Episode只保留脚本、动作、镜头、结尾和SceneContinuity。
- [x] ActionStage只保存actorId、action与visibleResult，不保存逐动作状态变化。
- [x] 关键实体只保存startState、endState、lifecycle与稳定formKey。
- [x] 停步、转头、蹲下、嗅闻和走动不进入世界状态。
- [x] 普通背景不进入连续性账本。
- [x] Pydantic只负责结构；EpisodeValidator集中执行一次剧情语义校验。
- [x] Prompt字符数只展示，不参与收费准入。
- [x] 语义失败进入planning_review，不自动产生第二次导演调用。

## 故事板生产

- [x] 每条Episode只有一个`image:storyboard`步骤。
- [x] 两动作生成3张面板，三至四动作生成4张面板。
- [x] Seedream使用一次sequential image generation请求。
- [x] 每张面板独立下载、哈希和保存序号。
- [x] 少图、序号错误、非9:16、尺寸不一致或黑边阻断视频。
- [x] 一次语义审核原子批准或拒绝全部面板。
- [x] 关键项失败阻断；普通背景、轻微姿势和构图偏差只保存warnings。
- [x] rejected故事板不会被复用；重试必须创建新attempt。

## 视频与Web

- [x] 默认按顺序发送3～4张故事板参考图。
- [x] 结尾视觉关键时只发送首张和末张作为严格帧。
- [x] Canon只进入Seedream，不与故事板重复传给Seedance。
- [x] Web展示故事板Prompt、面板序号、审核和视频节点。
- [x] Web不再展示首帧/尾帧旧入口或历史运行抽屉。
- [x] 旧Run、旧步骤和旧Prompt已清理，11个Canon资产保留。

## 验收

- [x] `uv run ruff check .`
- [x] `uv run pytest -q`
- [x] 远程PostgreSQL隔离Schema迁移测试。
- [x] `npm run build`
- [x] `git diff --check`
- [x] `uv run cvg doctor`
- [x] 非付费草稿Run经真实FastAPI health/list/graph烟测后精确清理，无残留。

## Prompt与Web稳定化（0009）

- [x] Prompt Purpose统一为director、storyboard、storyboard_review、video和review。
- [x] 0008的image Prompt升级时迁移为storyboard，未知Purpose拒绝迁移。
- [x] WorkflowStep与生成Prompt在一个事务中创建；Prompt失败不残留Step。
- [x] 故事板与视频审核Prompt分别关联对应生成Prompt。
- [x] persist实体允许正常移动，合理换装只要求变化原因。
- [x] 结尾超过4个关键实体只产生诊断，不阻断规划。
- [x] 创作台页签写入URL，轮询不覆盖用户选择。
- [x] 后台错误持久展示Run、Episode、Slot与Operation Key。
- [x] 三个旧测试Run清理前诊断已保存到忽略目录，11个Canon保留。
- [x] 正式Schema已升级到0009_storyboard_prompt_purposes。
