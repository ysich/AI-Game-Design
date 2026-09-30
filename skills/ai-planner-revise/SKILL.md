---
name: ai-planner-revise
description: 在不打开网页的情况下，按用户指令局部修改已有策划案，并自动重新执行检查、页面任务拆分和版本导出。
---

# 适用场景

用户要求修改奖励、流程、界面、埋点、技术或其他已有策划案内容时使用。

## 执行方式

```powershell
python -m ai_planner.cli revise <run_id> "<修改指令>" --root "<仓库根目录>"
```

## 约束

- 必须先取得已有运行 ID；不要根据标题猜测运行记录。
- 修改指令应描述目标章节或业务变化，不能只写“优化一下”。
- 命令会创建新文档版本，不覆盖历史版本；读取 `document.version` 和 `exports.document_version_path` 确认结果。
- 返回 `needs_clarification`、`failed` 或检查错误时，报告原始状态和问题，不宣称修改完成。
