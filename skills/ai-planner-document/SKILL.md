---
name: ai-planner-document
description: 在不打开网页的情况下检索、读取、编辑和管理本地 Markdown 策划案文档及其不可变历史版本。
---

# 适用场景

用户要求查看策划案、按关键词搜索、修改文档正文、查看历史版本或删除本地文档时使用。

## 常用命令

```powershell
python -m ai_planner.cli document list --root "<仓库根目录>"
python -m ai_planner.cli document search "<关键词>" --root "<仓库根目录>"
python -m ai_planner.cli document get <document_id> --version <版本> --root "<仓库根目录>"
python -m ai_planner.cli document update <document_id> --content-file <file.md> --expected-version <版本> --root "<仓库根目录>"
python -m ai_planner.cli document delete <document_id> --expected-version <版本> --root "<仓库根目录>"
```

编辑时优先带上 `expected-version`，避免覆盖其他修改。更新会生成新版本；删除会移动到本地回收目录，必须明确告知用户删除结果。
