---
name: ai-planner-coding-case
description: 将已导出的游戏活动策划案转换为研发可执行的 Coding 案，包含流程、界面、埋点、服务端、异常和验收检查项。
---

# 适用场景

用户要求把策划案交给程序、美术或测试执行时使用。该流程只读取本地运行快照，不启动网页。

## 执行方式

直接输出 Markdown：

```powershell
python -m ai_planner.cli coding <run_id> --root "<仓库根目录>"
```

保存到文件：

```powershell
python -m ai_planner.cli coding <run_id> --output <coding-case.md> --root "<仓库根目录>"
```

运行必须包含已生成的 `document`；否则应说明需要先执行 `ai-planner-generate` 或 `ai-planner-revise`。输出后检查是否包含页面任务和开发检查项。
