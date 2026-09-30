---
name: ai-planner-image
description: 根据策划案视觉任务在不打开网页的情况下生成或修改界面图片，支持尺寸、补充提示词和本地参考图。
---

# 适用场景

用户要求生成活动主界面、任务页、结算页、状态图，或基于参考图修改视觉方案时使用。

## 执行方式

```powershell
python -m ai_planner.cli image <run_id> <task_id> --root "<仓库根目录>"
python -m ai_planner.cli image <run_id> <task_id> --prompt "<提示词>" --supplement-prompt "<补充要求>" --size 1536x1024 --reference-image <image> --root "<仓库根目录>"
```

先通过 `ai-planner-visual` 获取有效的 `task_id`。如果图片提供商为 `disabled`，应报告配置阻断并转交模型配置流程，不要把规划任务误报为已生成。生成结果中的本地图片 URL、参考图 URL、实际尺寸和修订提示词都要保留。
