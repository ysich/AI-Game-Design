---
name: ai-planner-visual
description: 读取已有策划案拆出的界面、状态图和页面跳转任务，供 Agent 继续安排生图或评审视觉流程，不依赖网页界面。
---

# 适用场景

用户要求查看策划案对应的页面清单、状态图任务、依赖关系或页面流程时使用。完整策划案生成时，这些任务已经由管线自动拆分。

## 执行方式

```powershell
python -m ai_planner.cli visual <run_id> --root "<仓库根目录>"
```

读取 `visual_tasks` 和 `flow_edges`：先处理没有 `depends_on` 的任务，再处理依赖主界面的状态图。需要生成图片时转交 `ai-planner-image`，不要在本 skill 中伪造图片 URL。
