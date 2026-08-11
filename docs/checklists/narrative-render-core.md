# 极简混合导演契约与JSON收敛 Checklist

> 历史归档：本清单记录契约版本2的实施事实，已由V3生活故事项目和
> `single-slot-optional-links.md`替代。开发阶段不运行Ruff、pytest、Web构建、
> 数据库验证或真实Ark；全部功能、清理工具和文档落盘后，才统一执行检查并进行
> 一次真实Web全链路验证。

## Batch 0：边界与基线保护

- [x] 保留当前工作树，不执行reset或覆盖用户改动。
- [x] 冻结开发阶段真实Ark调用。
- [x] 确认旧生产Run不做契约转换，只保留批准Canon。
- [x] 保持PostgreSQL为唯一工作流状态源。

## Batch 1：极简导演契约

- [x] 将DayBrief收敛为`theme / dayArc / slotBriefs / handoffs`。
- [x] SlotBrief只保留叙事职责、事件方向、外观意图、活动焦点、时长档和决策原因。
- [x] EpisodeScript以完整`storyText`作为创作事实主体。
- [x] RelationshipArc收敛为一段猫咪主活动、人物回应和关系汇合文字。
- [x] 镜头收敛为1～3个`order + direction`完整导演段落。
- [x] 关键交互收敛为`shotOrders + text`自然语言硬约束。
- [x] 删除ActionStage、重复镜头字段、criticalProps和道具起终状态表。
- [x] 8～45秒RenderPlan继续由精确时长确定性推导。

## Batch 2：导演、Prompt与校验

- [x] Day Director只输出全天弧、时段职责、活动焦点、时长档和交接。
- [x] 三个时段导演顺序读取前序结果并输出完整长剧情。
- [x] Director Prompt明确要求每个镜头段落包含站位、路径、接触、结果和稳定切点。
- [x] 图片、视频和审核Prompt从同一EpisodeScript确定性编译。
- [x] 同一硬约束只在最终Prompt中表达一次，并只投影到相关镜头。
- [x] 删除本地Prompt字符硬门和旧自由文本兼容分支。
- [x] 保留字段类型、顺序、引用、时长及活动焦点等必要校验，不恢复世界状态模拟。

## Batch 3：应用、API与Trace

- [x] Planning、Studio Editing、Visual Preparation和Queries使用当前最小契约。
- [x] 当前编译Prompt、实际调用Prompt、Provider原始输出和标准化结果继续可追踪。
- [x] 高级Prompt覆盖绑定脚本哈希，上游变化后自动过期。
- [x] 已提交attempt的实际Prompt、Task ID和错误永久保留。
- [x] 契约版本不匹配返回明确业务错误，不泄露Pydantic堆栈或HTTP 500。
- [x] Retry、resume、reconcile与`submission_unknown`付费安全语义保持不变。

## Batch 4：Web创作台

- [x] DayBrief编辑器展示全天弧、时段方向、活动焦点、时长档和交接。
- [x] 三集导演页以完整剧情编辑器作为主界面。
- [x] 提供1～3个完整镜头段落编辑器。
- [x] 提供少量关键硬约束编辑器。
- [x] 保留外观、关系弧、声音、结尾和精确时长控制。
- [x] 实时预览最终Prompt；Ark原始JSON和技术信息默认折叠。
- [x] 节点Trace继续展示输入、Prompt、Provider、媒体、审核和attempt历史。

## Batch 5：数据库版本与历史清理

- [x] 新增`0012_minimal_director_contract`迁移。
- [x] `production_runs.contract_version`固定为2并具有数据库CHECK约束。
- [x] 0012发现旧Run时拒绝升级，不静默转换旧JSON。
- [x] 提供预览Manifest、Canon完整性校验和精确确认口令的清理工具。
- [x] 清理工具只删除已记录且位于媒体根目录内的非Canon媒体。
- [x] 生成正式库清理Manifest并人工核对。
- [x] 显式清理全部非Canon生产记录和媒体。
- [x] 升级正式数据库到`0012_minimal_director_contract`。
- [x] 确认11个批准Canon及其SHA-256保持不变。

## Batch 6：旧架构和文档收敛

- [x] 当前运行时代码不再保存旧动作、重复镜头和关键道具状态结构。
- [x] Web类型和DTO不再暴露被删除字段。
- [x] README、ADR、完整流程、Windows手册和HTTP说明对齐契约版本2。
- [x] 清理工具和迁移文档统一使用0012命名。
- [x] 最终静态检索确认活动源码与Web不存在旧符号或死分支。

## Batch 7：唯一一次统一检查

> 只有Batch 1～6全部完成后执行；本批已在全部功能落盘后一次性执行完毕。

- [x] `uv run ruff check .`
- [x] `uv run pytest -q`（54 passed，1 skipped）。
- [x] `CAT_VIDEO_POSTGRES_TEST_MODE=remote-schema uv run pytest -m postgres -q`（1 passed）。
- [x] `npm --prefix web run build`
- [x] `git diff --check`
- [x] `uv run cvg doctor`
- [x] Doctor确认正式Schema位于`0012_minimal_director_contract`。

## Batch 8：唯一一次真实Web全链路验证

- [x] 从Web创建全新放风筝全天Run，默认`cat_lead`（Run `b04ccb2d-f2fa-4732-99c9-9498d1a3a715`）。
- [x] Morning使用short，Noon使用medium并执行一次官方延展，Evening使用short。
- [x] 检查四次导演输出的长剧情、镜头段落和硬约束。
- [x] 生成或复用三时段定妆图并生成三张开场锚点。
- [x] 三张最终采用的锚点通过图片语义审核。
- [x] 生成Morning、Noon首段与延展、Evening最终视频。
- [x] 最终采用的中午成片中风筝线连接人物手中线轴与同一风筝，不连接或缠绕猫咪。
- [x] 猫咪保持四足自然行为并推动主要可见信息。
- [x] 三条完成技术QC、语义诊断和Web人工播放检查；诊断对合法切镜产生的非阻断提示由人工复核。
- [x] 三条均通过人工内容审核并构建1/2/3交付包（revision 1）。

## 失败处理

- 统一检查失败时只修复代码、契约、测试或文档，不调用真实Ark。
- 真实验证失败时保留成功收费节点，只分析并修复通用原因。
- 仅对失败节点创建新attempt，并再次显式确认付费；不重跑已成功节点。

## Batch 9：顺序人工导演与实际结果交接

- [x] 新增`guided_sequential / auto_day`两种规划方式，Web默认顺序人工确认。
- [x] DayBrief必须由用户保存确认后才解锁Morning导演。
- [x] Morning、Noon、Evening只按已确认结果卡顺序解锁。
- [x] `AcceptedOutcome`只保存实际摘要、继续继承和禁止继承三类事实。
- [x] 视频诊断只产生结果卡草稿，不自动成为下一时段事实。
- [x] Web显示缺失时段的锁定原因、唯一规划操作和可编辑结果卡。
- [x] Seedance初始Prompt不再重复整段`storyText`，延展Prompt不复述完整前文。
- [x] 将Docs中的信息刷新、单一运镜、稳定切点和根因修复方法内化到Prompt。
- [x] Docs项目保持设计时参考，不成为运行时依赖或新状态源。

## Batch 10：顺序模式最终一致性检查

- [x] 最后一条视频批准后仍需确认傍晚结果卡，才允许Run进入ready。
- [x] 可解析但语义拒绝的导演节点显示planning_rejected，而不是普通成功。
- [x] 缺失时段只能执行规划、按原因重规划或节点恢复中的一个明确操作。
- [x] Ruff、Python、远程PostgreSQL、Web构建、diff检查和doctor统一通过。
