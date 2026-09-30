---
name: ai-planner-generate
description: 在不打开网页的情况下，根据自然语言需求和本地参考资料生成完整游戏活动策划案，并返回可追踪的运行快照、检查结果和 Markdown 文档。
---

# 适用场景

用户要求新建、生成、完善一份游戏活动策划案时使用。该 skill 直接调用本地管线，不启动网页服务。

## 执行方式

在仓库根目录执行：

```powershell
python -m ai_planner.cli generate "<需求>" --root "<仓库根目录>"
```

可重复添加 `--answer "<澄清答案>"` 和 `--reference-id <参考资料 ID>`。需要基于已有 JSON 文档时使用 `--existing-document <file.json>`。

## 结果处理

- 读取 JSON 中的 `stage`、`route`、`document`、`findings`、`visual_tasks` 和 `exports`。
- `stage` 为 `needs_clarification` 时，只整理 `route.clarification_questions` 并向用户提问，不伪造策划案。
- `stage` 为 `exported` 时，报告运行 ID、文档路径、检查问题数量和页面任务数量。
- `stage` 为 `failed` 时，保留运行 ID 和 `errors`，优先使用 `ai-planner-run-recovery` 流程继续执行。

不要用网页抓取替代本地管线，也不要在本 skill 中擅自修改知识库或模型配置。
