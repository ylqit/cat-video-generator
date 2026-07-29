# 文档索引

当前文档只描述一套生产架构，不再按 V1～V5 重复维护同一规则。

- [ADR：显式状态机与模块化单体](architecture/ADR-001-explicit-workflow.md)
- [完整三时段生产流程](workflows/complete-production.md)
- [Windows运行手册](workflows/windows-runbook.md)
- [火山方舟多模态能力与Prompt基线](providers/volcengine-multimodal.md)
- [FastAPI只读接口](http-api.md)
- [用户提供的设计脚本教程](设计脚本教程)
- [Seedance 2.0工程化Prompt规则](SKILL.md)

历史 V5 Prompt、媒体和审核记录已经保存为本地哈希归档，并以 `archived` 状态
导入当前八表 Schema。它们可查询，但不能恢复生成。V1～V4 运行代码、Schema、
回归命令和自动输出 JSON 已删除。
