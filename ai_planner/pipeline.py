from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .adapters import (
    DocumentExporter,
    LLMProvider,
    KnowledgeStore,
    LocalImageProvider,
    LocalKnowledgeStore,
    MarkdownExporter,
    create_llm_provider,
)
from .config import ModelConfigStore, public_model_config
from .models import (
    CheckFinding,
    ContextSegment,
    IntentRoute,
    PlannerMode,
    Route,
    Severity,
    Stage,
    WorkflowRun,
    document_from_dict,
    to_dict,
)
from .storage import JsonRunStore


class PlannerPipeline:
    """Orchestrates the six stages of a planning workflow.

    Every stage persists the run. A real LLM or document service can be injected
    without changing this orchestration layer.
    """

    def __init__(
        self,
        root: Path | str = ".",
        llm: LLMProvider | None = None,
        knowledge: KnowledgeStore | None = None,
        exporter: DocumentExporter | None = None,
        image_provider: LocalImageProvider | None = None,
    ):
        self.root = Path(root)
        self.store = JsonRunStore(self.root)
        self.model_config_store = ModelConfigStore(self.root)
        self.model_config = self.model_config_store.load()
        self.llm = llm or create_llm_provider(self.model_config)
        self.knowledge = knowledge or LocalKnowledgeStore(root=self.root / "Doc" / "AI策划案管线" / "知识库")
        self.exporter = exporter or MarkdownExporter()
        self.image_provider = image_provider or LocalImageProvider()

    def get_model_config(self) -> Dict[str, Any]:
        return public_model_config(self.model_config)

    def update_model_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.model_config = self.model_config_store.save(payload)
        self.llm = create_llm_provider(self.model_config)
        return self.get_model_config()

    def create_run(self, request: str, existing_document: Dict[str, Any] | None = None) -> WorkflowRun:
        run_id = f"run-{uuid.uuid4().hex[:10]}"
        has_document = bool(existing_document)
        run = WorkflowRun(id=run_id, request=request.strip())
        run.route = self.llm.route(run.request, has_document=has_document)
        if existing_document and run.route.planner_mode == PlannerMode.PARTIAL_REVISION:
            payload = existing_document.get("document", existing_document)
            if payload.get("id") and payload.get("chapters"):
                run.document = document_from_dict(payload)
        run.decisions.append({"type": "intent_route", "value": run.route.route.value, "reason": run.route.reason})
        if existing_document:
            run.decisions.append({"type": "base_document", "value": existing_document.get("id", "inline")})
        if run.route.clarification_required:
            run.stage = Stage.NEEDS_CLARIFICATION
        self.store.save(run)
        return run

    def run(self, request: str, answers: Sequence[str] | None = None, existing_document: Dict[str, Any] | None = None) -> WorkflowRun:
        run = self.create_run(request, existing_document=existing_document)
        if run.stage == Stage.NEEDS_CLARIFICATION and not answers:
            return run
        if answers:
            run.request = run.request + "\n已确认信息：" + "；".join(answer for answer in answers if answer.strip())
            run.route = self.llm.route(run.request, has_document=bool(existing_document))
            run.stage = Stage.INTAKE
            self.store.save(run)
        return self.continue_run(run.id, existing_document=existing_document)

    def continue_run(self, run_id: str, existing_document: Dict[str, Any] | None = None) -> WorkflowRun:
        run = self.store.load(run_id)
        try:
            self._assemble_context(run)
            if run.route and run.route.route == Route.IMAGE_ONLY:
                run.stage = Stage.VISUALIZED
                run.visual_tasks, run.flow_edges = self._plan_visuals_without_document(run)
                run.exports["json"] = "workflow snapshot"
                self.store.save(run)
                return run
            self._draft(run, existing_document)
            self._review(run)
            self._visualize(run)
            self._export(run)
            self.store.save(run)
        except Exception as exc:  # keep failure inspectable and resumable
            run.stage = Stage.FAILED
            run.errors.append(f"{type(exc).__name__}: {exc}")
            self.store.save(run)
        return run

    def revise(self, run_id: str, instruction: str) -> WorkflowRun:
        run = self.store.load(run_id)
        if not run.document:
            raise ValueError("只有已经生成策划案的运行才能进行局部修改")
        run.request = instruction.strip()
        run.route = self.llm.route(run.request, has_document=True)
        run.decisions.append({"type": "revision", "instruction": instruction})
        run.stage = Stage.INTAKE
        run.findings = []
        run.visual_tasks = []
        run.flow_edges = []
        self.store.save(run)
        return self.continue_run(run.id, existing_document={"id": run.document.id})

    def _assemble_context(self, run: WorkflowRun) -> None:
        base = [
            ContextSegment("project-profile", "项目档案", "项目类型：多人在线游戏。输出需要可评审、可拆解、可追踪。", "project", 100, 30),
            ContextSegment("team-profile", "团队角色", "策划负责目标与取舍；程序负责规则、状态和埋点；美术负责页面与视觉资源。", "team", 90, 30),
            ContextSegment("constraints", "项目约束", "规则、奖励和结算必须明确服务端归属；异常路径必须有补发或回滚策略。", "constraint", 95, 32),
        ]
        template_name = "活动优化案模板" if run.route and run.route.suggestion_request else "常规活动策划案模板"
        base.append(ContextSegment("template", template_name, "固定章节：目标、用户、玩法、流程、奖励、异常、界面、埋点、技术和风险。", "template", 98, 36))
        refs = self.knowledge.search(run.request, limit=4)
        raw_chars = sum(len(item.content) for item in base + refs)
        run.context = self._compact_context(base + refs, max_chars=12000)
        retained_chars = sum(len(item.content) for item in run.context)
        if raw_chars <= 7200:
            tier = "tier_0"
        elif raw_chars <= 9600:
            tier = "tier_1_micro_compact"
        elif raw_chars <= 11400:
            tier = "tier_2_memory_compact"
        else:
            tier = "tier_3_full_compact"
        run.decisions.append({
            "type": "context",
            "segment_ids": [item.id for item in run.context],
            "template": template_name,
            "waterline": {"raw_chars": raw_chars, "retained_chars": retained_chars, "budget_chars": 12000, "tier": tier},
        })
        run.stage = Stage.CONTEXT_READY
        self.store.save(run)

    @staticmethod
    def _compact_context(segments: Sequence[ContextSegment], max_chars: int) -> List[ContextSegment]:
        # Keep policy/template segments first, then the highest ranked references.
        ordered = sorted(segments, key=lambda item: item.priority, reverse=True)
        result: List[ContextSegment] = []
        used = 0
        for segment in ordered:
            content = segment.content
            if used + len(content) <= max_chars:
                result.append(segment)
                used += len(content)
                continue
            room = max_chars - used
            if room >= 120:
                result.append(ContextSegment(segment.id, segment.title, content[:room].rstrip() + "…", segment.source, segment.priority, room // 4))
            break
        return result

    def _draft(self, run: WorkflowRun, existing_document: Dict[str, Any] | None) -> None:
        existing = None
        if run.route and run.route.planner_mode == PlannerMode.PARTIAL_REVISION and run.document:
            existing = run.document
        run.document = self.llm.draft(run.request, run.context, run.route.planner_mode if run.route else PlannerMode.NEW, existing=existing)
        if existing is None:
            # A document ID is stable across revisions and unique across runs.
            run.document.id = f"{run.id}-doc"
        run.decisions.append({"type": "draft", "document_id": run.document.id, "chapter_count": len(run.document.chapters)})
        run.stage = Stage.DRAFTED
        self.store.save(run)

    def _review(self, run: WorkflowRun) -> None:
        if not run.document:
            return
        required = ["overview", "audience", "gameplay", "flow", "rewards", "exceptions", "ui", "analytics", "tech", "risks"]
        chapters = {chapter.id: chapter for chapter in run.document.chapters}
        findings: List[CheckFinding] = []
        for chapter_id in required:
            chapter = chapters.get(chapter_id)
            if not chapter or not chapter.content.strip():
                findings.append(CheckFinding("required-chapter", Severity.ERROR, "必填章节缺失或为空", chapter_id, suggestion="补齐该章节后再进入评审"))
        flow = chapters.get("flow")
        if flow:
            missing = [word for word in ("入口", "流程", "结算") if word not in flow.content]
            if missing:
                findings.append(CheckFinding("flow-complete", Severity.ERROR, f"流程缺少：{'、'.join(missing)}", "flow", flow.content[:160], "补充入口、执行路径和结算时机"))
        rewards = chapters.get("rewards")
        if rewards and not re.search(r"\d", rewards.content):
            findings.append(CheckFinding("reward-numeric", Severity.WARNING, "奖励章节尚未出现可核对的数量或上限", "rewards", rewards.content[:160], "评审时补充奖励数量、投放上限和领取条件"))
        exceptions = chapters.get("exceptions")
        if exceptions and not any(word in exceptions.content for word in ("补发", "回滚", "幂等")):
            findings.append(CheckFinding("exception-recovery", Severity.WARNING, "异常章节缺少补发、回滚或幂等策略", "exceptions", exceptions.content[:160], "补充断线、重复提交和活动结束后的处理"))
        risks = chapters.get("risks")
        if risks and "待决策" in risks.content:
            findings.append(CheckFinding("open-decisions", Severity.INFO, "仍有待策划确认的决策项", "risks", risks.content[:160], "在评审中逐项确认并写回版本记录"))
        run.findings = findings
        run.decisions.append({"type": "review", "finding_count": len(findings), "errors": sum(item.severity == Severity.ERROR for item in findings)})
        run.stage = Stage.REVIEWED
        self.store.save(run)

    def _visualize(self, run: WorkflowRun) -> None:
        if not run.document:
            return
        run.visual_tasks, run.flow_edges = self.image_provider.plan(run.document)
        run.decisions.append({"type": "visual_plan", "task_count": len(run.visual_tasks), "edge_count": len(run.flow_edges)})
        run.stage = Stage.VISUALIZED
        self.store.save(run)

    def _plan_visuals_without_document(self, run: WorkflowRun):
        from .models import Chapter, PlanningDocument

        placeholder = PlanningDocument(
            "image-only",
            "界面任务",
            "视觉任务",
            [Chapter("ui", "界面需求", run.request)],
        )
        return self.image_provider.plan(placeholder)

    def _export(self, run: WorkflowRun) -> None:
        if not run.document:
            return
        run.exports["markdown"] = self.exporter.export_markdown(run.document, run.findings, run.visual_tasks)
        document_record = self.store.document_library.publish(run.document, run.exports["markdown"], run.id)
        run.exports["document_id"] = document_record["id"]
        run.exports["document_path"] = str(Path("Doc") / "AI策划案管线" / "文档库" / document_record["path"])
        run.exports["document_version_path"] = str(Path("Doc") / "AI策划案管线" / "文档库" / document_record["version_path"])
        run.exports["json"] = "workflow snapshot available via API"
        run.stage = Stage.EXPORTED
        run.decisions.append({"type": "export", "formats": ["markdown", "json"]})
        self.store.save(run)
        json_path = self.store.write_export(run.id, "json", json.dumps(to_dict(run), ensure_ascii=False, indent=2))
        run.exports["json_path"] = str(json_path.relative_to(self.root))
        self.store.save(run)
