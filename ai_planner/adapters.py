from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Protocol, Sequence

from .models import (
    Chapter,
    ContextSegment,
    IntentRoute,
    PlanningDocument,
    PlannerMode,
    Route,
    VisualTask,
    to_dict,
)


class LLMProvider(Protocol):
    def route(self, request: str, has_document: bool = False) -> IntentRoute: ...

    def draft(
        self,
        request: str,
        context: Sequence[ContextSegment],
        mode: PlannerMode,
        existing: PlanningDocument | None = None,
    ) -> PlanningDocument: ...


class KnowledgeStore(Protocol):
    def search(self, query: str, limit: int = 5) -> List[ContextSegment]: ...


class DocumentExporter(Protocol):
    def export_markdown(self, document: PlanningDocument, findings: Iterable[object], visual_tasks: Sequence[VisualTask]) -> str: ...


@dataclass
class KnowledgeItem:
    id: str
    title: str
    content: str
    tags: List[str]
    source: str = "local"


class LocalKnowledgeStore:
    def __init__(self, items: Sequence[KnowledgeItem] | None = None, root: Path | str | None = None):
        self.root = Path(root) if root else None
        markdown_items = self._load_markdown_items() if self.root else []
        self.items = list(markdown_items or items or default_knowledge_items())

    def _load_markdown_items(self) -> List[KnowledgeItem]:
        if not self.root or not self.root.exists():
            return []
        items: List[KnowledgeItem] = []
        for path in sorted(self.root.glob("*.md")):
            content = path.read_text(encoding="utf-8").strip()
            if not content:
                continue
            title = next((line[2:].strip() for line in content.splitlines() if line.startswith("# ")), path.stem)
            tags = [word for word in re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z][a-zA-Z0-9_-]+", f"{title} {path.stem}")]
            items.append(KnowledgeItem(f"md:{path.stem}", title, content, tags, f"md:{path.name}"))
        return items

    def search(self, query: str, limit: int = 5) -> List[ContextSegment]:
        words = {word.lower() for word in re.findall(r"[\w\u4e00-\u9fff]+", query) if len(word) > 1}
        ranked = []
        for item in self.items:
            haystack = f"{item.title} {item.content} {' '.join(item.tags)}".lower()
            score = sum(1 for word in words if word in haystack)
            if score or not words:
                ranked.append((score, item))
        ranked.sort(key=lambda value: value[0], reverse=True)
        return [
            ContextSegment(
                id=item.id,
                title=item.title,
                content=item.content,
                source=item.source,
                priority=70 + min(score, 20),
                token_estimate=max(1, len(item.content) // 4),
            )
            for score, item in ranked[:limit]
        ]


class LocalLLMProvider:
    """Deterministic fallback. Replace this class with a real model adapter later."""

    def route(self, request: str, has_document: bool = False) -> IntentRoute:
        text = request.strip()
        if len(text) < 12:
            return IntentRoute(
                route=Route.CLARIFY,
                planner_mode=PlannerMode.NEW,
                confidence=0.98,
                clarification_required=True,
                clarification_questions=["这次活动的目标用户、持续时间和核心奖励分别是什么？"],
                reason="需求过短，无法确定活动目标和产出范围",
            )
        if any(token in text for token in ("只生成图", "只要界面", "改图", "生图")) or ("只生成" in text and "界面" in text):
            return IntentRoute(
                route=Route.IMAGE_ONLY,
                planner_mode=PlannerMode.IMAGE_ONLY,
                confidence=0.9,
                reason="识别到图片或界面任务",
            )
        if any(token in text for token in ("重写全文", "全部重写", "重新生成整篇")):
            return IntentRoute(
                route=Route.FULL_REWRITE,
                planner_mode=PlannerMode.FULL_REWRITE,
                confidence=0.92,
                reason="识别到全文重写要求",
            )
        if has_document and any(token in text for token in ("修改", "调整", "改成", "替换", "补充")):
            return IntentRoute(
                route=Route.PARTIAL_REVISION,
                planner_mode=PlannerMode.PARTIAL_REVISION,
                confidence=0.91,
                reason="已有策划案且识别到局部修改要求",
            )
        optimization = any(token in text for token in ("优化", "复盘", "提升参与", "改版"))
        return IntentRoute(
            route=Route.NEW_PLAN,
            planner_mode=PlannerMode.NEW,
            confidence=0.84,
            suggestion_request=optimization,
            clarification_required=False,
            reason="识别为新建活动策划案" + ("，需要确认优化目标" if optimization else ""),
        )

    def draft(
        self,
        request: str,
        context: Sequence[ContextSegment],
        mode: PlannerMode,
        existing: PlanningDocument | None = None,
    ) -> PlanningDocument:
        title = _title_from_request(request)
        activity_type = "活动优化案" if any(token in request for token in ("优化", "复盘", "改版")) else "常规活动"
        if mode == PlannerMode.PARTIAL_REVISION and existing:
            chapters = [Chapter(**chapter.__dict__) for chapter in existing.chapters]
            target = _guess_target_chapter(request, chapters)
            if target:
                target.content = f"根据本轮修改要求“{request}”更新。\n\n" + target.content
                target.status = "revised"
            return PlanningDocument(existing.id, existing.title, existing.activity_type, chapters, existing.version + 1, dict(existing.metadata))

        ref_titles = ", ".join(item.title for item in context if item.source.startswith(("case:", "md:"))) or "暂无本地案例"
        chapters = [
            Chapter("overview", "1. 项目概述", f"需求输入：{request}\n\n目标：明确活动要解决的用户问题、业务目标和成功指标。\n参考案例：{ref_titles}"),
            Chapter("audience", "2. 用户与目标", "目标用户：待策划确认年龄层、活跃阶段与核心动机。\n成功指标：参与人数、参与率、完成率和复访率。"),
            Chapter("gameplay", "3. 核心玩法", f"核心循环：进入活动 → 完成任务 → 获得奖励 → 解锁下一阶段。\n\n本案玩法候选：围绕“{request}”设计低门槛任务，并通过阶段目标提供持续反馈。"),
            Chapter("flow", "4. 活动流程", "入口：主城活动按钮和活动公告。\n流程：报名/进入 → 查看任务 → 完成任务 → 领取奖励 → 结算。\n结算：每日 05:00 刷新，活动结束后统一发放未领取奖励。"),
            Chapter("rewards", "5. 奖励与经济", "奖励：参与奖励、阶段奖励和排名奖励三档。\n奖励数量与投放上限需在评审时结合经济模型确认，避免通货膨胀。"),
            Chapter("exceptions", "6. 异常与补发", "断线重连后以服务端状态为准；重复领取返回幂等成功；活动结束后未发奖励进入邮件补发队列。"),
            Chapter("ui", "7. 界面与文案", "主界面：活动标题、进度、核心任务和领取入口。\n任务详情：任务条件、当前进度、奖励预览和前往按钮。\n结算页：完成状态、奖励明细和再次参与入口。"),
            Chapter("analytics", "8. 埋点与数据", "客户端：活动曝光、入口点击、任务查看、领取点击、分享点击。\n服务端：活动参与、任务完成、奖励发放、补发和异常码。"),
            Chapter("tech", "9. 技术需求", "服务端维护活动配置、任务状态、奖励幂等和结算；客户端支持进度刷新、断线恢复和多状态 UI。"),
            Chapter("risks", "10. 风险与待决策", "待决策：最终奖励数值、活动时长、是否加入排行榜、视觉资源量和灰度范围。"),
        ]
        return PlanningDocument(
            id="doc-" + re.sub(r"[^a-z0-9]", "", title.lower())[:24],
            title=title,
            activity_type=activity_type,
            chapters=chapters,
            metadata={"request": request, "context_refs": [item.id for item in context]},
        )


class OpenAICompatibleLLMProvider:
    """LLM adapter for OpenAI Chat Completions compatible endpoints."""

    def __init__(self, config: Dict[str, object]):
        self.config = config
        self.base_url = str(config["base_url"]).rstrip("/")
        self.model = str(config["model"])
        self.api_key = str(config.get("api_key", ""))
        self.timeout = int(config.get("timeout_seconds", 60))

    def _chat(self, system: str, user: str) -> str:
        url = self.base_url if self.base_url.endswith("/chat/completions") else self.base_url + "/chat/completions"
        payload = {
            "model": self.model,
            "temperature": self.config.get("temperature", 0.4),
            "max_tokens": self.config.get("max_tokens", 4000),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"模型接口返回 HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"模型接口连接失败：{exc.reason}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"模型接口请求失败：{exc}") from exc
        try:
            return str(result["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("模型接口响应缺少 choices[0].message.content") from exc

    @staticmethod
    def _json_content(content: str) -> Dict[str, object]:
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("模型没有返回可解析的 JSON")
            value = json.loads(text[start : end + 1])
        if not isinstance(value, dict):
            raise ValueError("模型返回的 JSON 必须是对象")
        return value

    def route(self, request: str, has_document: bool = False) -> IntentRoute:
        content = self._chat(
            "你是游戏活动策划需求路由器。只返回 JSON，不要 Markdown。字段必须包含 route、planner_mode、confidence、clarification_required、clarification_questions、reason。route 只能是 clarify、new_plan、partial_revision、full_rewrite、image_only、answer；planner_mode 只能是 new、partial_revision、full_rewrite、image_only。",
            json.dumps({"request": request, "has_document": has_document}, ensure_ascii=False),
        )
        data = self._json_content(content)
        route_value = str(data.get("route", Route.NEW_PLAN.value))
        try:
            route = Route(route_value)
        except ValueError:
            route = Route.NEW_PLAN
        mode_value = str(data.get("planner_mode", "new"))
        try:
            mode = PlannerMode(mode_value)
        except ValueError:
            mode = PlannerMode.IMAGE_ONLY if route == Route.IMAGE_ONLY else PlannerMode.NEW
        return IntentRoute(
            route=route,
            planner_mode=mode,
            confidence=float(data.get("confidence", 0.7)),
            clarification_required=bool(data.get("clarification_required", route == Route.CLARIFY)),
            clarification_questions=[str(item) for item in data.get("clarification_questions", []) if str(item).strip()],
            reason=str(data.get("reason", "由配置的模型完成需求路由")),
        )

    def draft(
        self,
        request: str,
        context: Sequence[ContextSegment],
        mode: PlannerMode,
        existing: PlanningDocument | None = None,
    ) -> PlanningDocument:
        context_payload = [to_dict(item) for item in context]
        user_payload = {"request": request, "mode": mode.value, "context": context_payload}
        if existing:
            user_payload["existing"] = to_dict(existing)
        content = self._chat(
            "你是游戏活动策划案生成器。只返回 JSON，不要 Markdown。返回 title、activity_type、chapters；chapters 是对象数组，每项包含 id、title、content、status、source_refs。生成内容必须可执行、可评审，引用上下文时保留来源 id。",
            json.dumps(user_payload, ensure_ascii=False),
        )
        data = self._json_content(content)
        data = data.get("document", data)
        raw_chapters = data.get("chapters", []) if isinstance(data, dict) else []
        chapters = [
            Chapter(
                id=str(item.get("id", f"chapter-{index}")),
                title=str(item.get("title", f"第 {index} 章")),
                content=str(item.get("content", "")).strip(),
                source_refs=[str(ref) for ref in item.get("source_refs", [])],
                status=str(item.get("status", "draft")),
            )
            for index, item in enumerate(raw_chapters, start=1)
            if isinstance(item, dict) and str(item.get("content", "")).strip()
        ]
        if not chapters:
            raise ValueError("模型没有返回有效的策划案章节")
        title = str(data.get("title", "")).strip() or _title_from_request(request)
        document_id = existing.id if existing else "doc-" + re.sub(r"[^a-z0-9]", "", title.lower())[:24]
        return PlanningDocument(
            id=document_id,
            title=title,
            activity_type=str(data.get("activity_type", "常规活动")),
            chapters=chapters,
            version=(existing.version + 1 if existing else 1),
            metadata={"request": request, "context_refs": [item.id for item in context], "provider": "openai_compatible", "model": self.model},
        )


def create_llm_provider(config: Dict[str, object]) -> LLMProvider:
    if config.get("provider") == "openai_compatible":
        return OpenAICompatibleLLMProvider(config)
    return LocalLLMProvider()


class MarkdownExporter:
    def export_markdown(self, document: PlanningDocument, findings: Iterable[object], visual_tasks: Sequence[VisualTask]) -> str:
        lines = [f"# {document.title}", "", f"> 类型：{document.activity_type}  |  版本：{document.version}", ""]
        for chapter in document.chapters:
            lines += [f"## {chapter.title}", "", chapter.content.strip(), ""]
        lines += ["## 附录 A：检查结果", ""]
        findings = list(findings)
        if findings:
            for finding in findings:
                lines.append(f"- [{finding.severity.value}] {finding.rule_id}：{finding.message}")
        else:
            lines.append("- 未发现问题")
        lines += ["", "## 附录 B：界面任务", ""]
        for task in visual_tasks:
            lines.append(f"- **{task.screen_name}**：{task.purpose}（状态：{task.state}）")
        return "\n".join(lines).rstrip() + "\n"


class LocalImageProvider:
    def plan(self, document: PlanningDocument) -> tuple[List[VisualTask], List[Dict[str, str]]]:
        ui = document.chapter("ui")
        if not ui:
            return [], []
        tasks = [
            VisualTask("screen-main", "活动主界面", "承载活动入口、进度和任务列表", "顶部标题 + 中部进度 + 底部任务卡片", ["标题", "进度条", "任务卡片", "领取按钮"], ["前往", "领取奖励"], "default"),
            VisualTask("screen-detail", "任务详情页", "展示单个任务规则和奖励预览", "左侧任务说明 + 右侧奖励与操作", ["条件说明", "奖励预览", "前往按钮"], ["立即前往", "返回"], "default", depends_on=["screen-main"]),
            VisualTask("screen-result", "活动结算页", "反馈完成结果并承接下一次参与", "结果反馈 + 奖励明细 + 下一步操作", ["完成状态", "奖励明细", "再次参与按钮"], ["领取", "继续参与"], "completed", depends_on=["screen-main"]),
        ]
        edges = [
            {"from": "screen-main", "to": "screen-detail", "trigger": "点击任务卡片"},
            {"from": "screen-detail", "to": "screen-main", "trigger": "返回或完成任务"},
            {"from": "screen-main", "to": "screen-result", "trigger": "领取阶段奖励"},
        ]
        return tasks, edges


def _title_from_request(request: str) -> str:
    compact = re.sub(r"\s+", " ", request.strip())
    return (compact[:28] + "…" if len(compact) > 28 else compact) + "｜活动策划案"


def _guess_target_chapter(request: str, chapters: Sequence[Chapter]) -> Chapter | None:
    mapping = {"奖励": "rewards", "界面": "ui", "流程": "flow", "入口": "flow", "埋点": "analytics", "技术": "tech", "异常": "exceptions"}
    for keyword, chapter_id in mapping.items():
        if keyword in request:
            return next((chapter for chapter in chapters if chapter.id == chapter_id), None)
    return chapters[0] if chapters else None


def default_knowledge_items() -> List[KnowledgeItem]:
    return [
        KnowledgeItem("case-daily-login", "连续登录奖励活动案例", "连续登录活动通常按天提供递进奖励，重点关注断签、补签、重复领取与活动结束结算。", ["登录", "奖励", "连续"], "case:local"),
        KnowledgeItem("case-community-task", "社区任务活动案例", "社区任务通过分享、邀请和互动获得进度，需明确反作弊、任务幂等和客户端/服务端埋点。", ["社区", "任务", "分享"], "case:local"),
        KnowledgeItem("rule-economy", "奖励经济约束", "奖励需记录来源、投放上限、领取条件和回收方式；评审阶段必须确认数值与现有经济模型的关系。", ["奖励", "经济", "约束"], "policy:local"),
    ]
