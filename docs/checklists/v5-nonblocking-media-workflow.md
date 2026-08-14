# CAT-VIDEO-GENERATOR V5 非阻塞任务、视觉引用与媒体版本 Checklist

状态：`planned` 尚未实现；`implemented` 已落盘；`unit-checked` 已通过隔离测试；`final-verified` 已纳入全部功能完成后的唯一一次本地数据链路验证。

实施期间不调用真实 Ark，不运行正式本地 smoke，不修改数据库迁移；Alembic HEAD 保持 `0018_v5_shot_assistance`。

## 阶段 1：生成规格与接口

- [unit-checked] 锚点与视频共用生成规格编译边界，预览和真实提交保持相同素材顺序、Prompt 与输入哈希。
- [unit-checked] Prompt 预览支持 `anchor/video` 双目标，返回生成就绪状态、阻断原因和当前输入哈希。
- [unit-checked] 分镜建议可选携带现有 `anchorMode` 与 `sceneLookUsage`，旧版本按兼容默认值读取。
- [unit-checked] 媒体版本使用现有资产、步骤与输入快照判断是否基于当前输入，不增加迁移。

## 阶段 2：全局非阻塞任务中心

- [unit-checked] 长任务提交后立即解除页面和组件遮罩，不在业务组件中等待任务完成。
- [unit-checked] 应用级任务中心统一显示进程任务、持久化 Provider 步骤、完成通知和错误恢复入口。
- [unit-checked] 页面切换或对话框关闭不丢失任务；任务完成后按项目、场景或片段刷新数据。

## 阶段 3：场景视觉基准与片段引用

- [unit-checked] 场景卡直接显示当前视觉基准、版本数与生成状态，工作台顶部提供新候选、重试和版本入口。
- [unit-checked] 单次 Seedream 只追加一个候选，历史 Prompt、参考图快照与审核状态保留。
- [unit-checked] 片段卡显示锚点、片段自定义、场景基准、项目继承和去重后实际数量。
- [unit-checked] 右侧按生成配置、实际参考图、锚点与视频版本、LLM 审稿与 Prompt 分区。

## 阶段 4：锚点与视频历史

- [unit-checked] `derive_anchor` 缺少批准锚点时在付费提交前阻断，并明确下一步操作。
- [unit-checked] 运行中、候选、批准、拒绝、失败和待对账版本统一显示；媒体可直接预览。
- [unit-checked] 历史版本按输入哈希标记当前或基于旧输入，旧版本选择前要求显式确认。
- [unit-checked] 已批准锚点和视频均可从历史版本重新选择，生成和重试不覆盖旧资产。

## 阶段 5：测试与唯一最终验证

- [unit-checked] 后端隔离测试覆盖双目标预览、素材顺序、输入哈希、锚点门槛和分镜兼容。
- [unit-checked] 前端组件测试覆盖非阻塞提交、任务中心、视觉基准版本、实际引用和媒体历史。
- [final-verified] 唯一一次本地数据链路、pytest、Ruff、前端测试、类型检查和生产构建均已通过。
- [final-verified] 已输出脱敏诊断报告；fake 网关完成创作与生成验证，真实 Ark 新生成调用数为 0。
