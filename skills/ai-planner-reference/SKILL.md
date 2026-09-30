---
name: ai-planner-reference
description: 管理 AI 策划案管线的本地参考资料，支持上传、解析、检索上下文、读取和删除，不依赖网页工作台。
---

# 适用场景

用户要把 Markdown、TXT、CSV、JSON、YAML、DOC/DOCX、PDF 或图片作为策划参考资料时使用。

## 常用命令

```powershell
python -m ai_planner.cli reference upload <file>... --root "<仓库根目录>"
python -m ai_planner.cli reference list --root "<仓库根目录>"
python -m ai_planner.cli reference get <reference_id> --root "<仓库根目录>"
python -m ai_planner.cli reference context <reference_id>... --root "<仓库根目录>"
python -m ai_planner.cli reference delete <reference_id> --root "<仓库根目录>"
```

上传结果中的 `id` 才是生成策划案时的 `--reference-id`。删除前应确认 ID；原始文件和索引会同时移除，不能把删除描述成取消勾选。
