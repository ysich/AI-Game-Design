# AI 策划案管线

这是参考 `Doc/AI策划案分享/AI策划案分享.md` 实现的本地优先 MVP。它把一句话需求推进为：意图路由、项目上下文与历史案例装配、结构化策划案、规则检查、界面任务拆分、页面跳转边和本地 Markdown 文档库。

## 快速运行

需要 Python 3.11+，不需要安装第三方依赖。

```powershell
python -m ai_planner.cli run "做一个为期 7 天的回流玩家签到活动，提升回流率并给出界面和奖励方案。"
python -m ai_planner.cli serve
```

打开 <http://127.0.0.1:8765/> 使用工作台。运行记录会保存到项目目录下的 `.ai-planner/runs/`；策划案正文会写入 `Doc/AI策划案管线/文档库/`，每次修改还会保存到该目录的 `history/`。用于生成上下文的模板、约束和历史案例位于 `Doc/AI策划案管线/知识库/`，新增 Markdown 文件后重启工作台即可被检索。

## API

- `GET /api/health`：健康检查。
- `GET /api/runs`：列出运行快照。
- `GET /api/runs/{id}`：读取完整运行结果。
- `POST /api/runs`：运行管线，body 为 `{ "request": "...", "answers": [] }`。
- `POST /api/runs/{id}/revise`：对已有运行做局部修改，body 为 `{ "instruction": "把奖励章节改为..." }`。
- `GET /api/documents`：列出本地 Markdown 文档库中的策划案。
- `GET /api/documents?q=签到`：检索本地文档标题和正文。
- `GET /api/documents/{id}?version=2`：读取文档当前版本或指定历史版本。

## 代码结构

- `ai_planner/models.py`：数据契约与可序列化模型。
- `ai_planner/pipeline.py`：六阶段工作流和持久化边界。
- `ai_planner/adapters.py`：本地 LLM、知识库、视觉任务和 Markdown 导出适配器；可以替换为真实服务。
- `ai_planner/storage.py`：JSON 快照存储与本地 Markdown 文档库。
- `Doc/AI策划案管线/知识库/`：本地 Markdown 模板、约束和历史案例。
- `ai_planner/server.py`：标准库 HTTP API。
- `web/index.html`：无构建依赖的本地工作台。
- `Doc/AI策划案管线/设计大纲.md`：设计、阶段计划和主动迭代记录。

## 后续接入点

实现 `LLMProvider`、`KnowledgeStore`、`DocumentExporter` 或 `LocalImageProvider` 的替代类即可接入真实模型、向量检索和生图服务。文档正文保持 Markdown 文件格式，后续可以在文档库之上增加 Git、SQLite 或对象存储索引；管线仍以 `WorkflowRun` 快照为边界。

## Git 提交规范

本项目使用本地 Git 管理版本。每次代码、测试、配置、策划文档、网页资源或说明文件发生更新后，都必须创建一次提交。提交信息需要详细记录变更内容、变更原因和验证方式，推荐使用仓库根目录的 `.gitmessage.txt` 模板。

提交前至少执行：

```powershell
git status --short
python -m pytest
git diff --check
git add <本次更新涉及的文件>
git commit
git status --short --branch
```

完整要求见 [AGENTS.md](AGENTS.md)。
