from __future__ import annotations

from .models import WorkflowRun


def coding_case(run: WorkflowRun) -> str:
    """Render one exported planning run as a developer-facing coding case."""
    if not run.document:
        raise ValueError("该运行记录还没有可生成 Coding 案的策划案")
    document = run.document
    chapter = {item.id: item for item in document.chapters}
    lines = [
        f"# {document.title} · Coding 案",
        "",
        f"> 来源运行：{run.id}  |  版本：{document.version}",
        "",
        "## 研发目标",
        "",
        chapter.get("overview", document.chapters[0]).content,
        "",
    ]
    for key, title in (
        ("flow", "流程与状态"),
        ("ui", "客户端界面"),
        ("analytics", "埋点与数据"),
        ("tech", "服务端与技术要求"),
        ("exceptions", "异常与验收"),
    ):
        item = chapter.get(key)
        if item:
            lines.extend([f"## {title}", "", item.content, ""])
    lines.extend(["## 页面任务", ""])
    for task in run.visual_tasks:
        lines.append(
            f"- {task.screen_name}：{task.purpose}；组件：{'、'.join(task.components)}；状态：{task.state}"
        )
    lines.extend([
        "",
        "## 开发检查项",
        "",
        "- 配置、奖励发放和结算以服务端状态为准。",
        "- 客户端断线重连后恢复活动状态。",
        "- 所有奖励领取接口支持幂等。",
        "- 上线前完成规则检查与埋点核对。",
        "",
    ])
    return "\n".join(lines)
