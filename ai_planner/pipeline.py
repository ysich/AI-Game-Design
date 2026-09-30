from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .adapters import (
    DocumentExporter,
    LLMProvider,
    KnowledgeStore,
    ImageGenerationProvider,
    LocalImageProvider,
    LocalKnowledgeStore,
    MarkdownExporter,
    create_image_generation_provider,
    create_llm_provider,
    create_web_search_provider,
    WebSearchProvider,
)
from .config import ModelConfigStore, SUPPORTED_IMAGE_SIZES, public_model_config
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
from .references import ReferenceLibrary
from .storage import JsonRunStore


MAX_REFERENCE_IMAGE_BYTES = 10 * 1024 * 1024
MAX_REFERENCE_IMAGES = 16
REFERENCE_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}


def _decode_reference_image(value: str | None) -> tuple[bytes, str] | None:
    if not value:
        return None
    if not isinstance(value, str) or not value.startswith("data:"):
        raise ValueError("参考图必须是 PNG、JPEG 或 WebP 图片")
    header, separator, encoded = value.partition(",")
    if not separator or ";base64" not in header:
        raise ValueError("参考图数据格式无效")
    mime_type = header[5:].split(";", 1)[0].lower()
    if mime_type not in REFERENCE_IMAGE_MIME_TYPES:
        raise ValueError("参考图仅支持 PNG、JPEG 或 WebP 格式")
    try:
        image = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("参考图数据无法读取") from exc
    return _validate_reference_image(image, mime_type)


def _validate_reference_image(image: bytes, mime_type: str = "") -> tuple[bytes, str]:
    if not image or len(image) > MAX_REFERENCE_IMAGE_BYTES:
        raise ValueError("参考图大小必须在 1 B 到 10 MB 之间")
    detected_mime = _detect_reference_image_mime(image)
    if mime_type and detected_mime != mime_type.lower():
        raise ValueError("参考图格式与文件内容不一致")
    return image, detected_mime


def _detect_reference_image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("参考图不是受支持的 PNG、JPEG 或 WebP 图片")


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
        web_search: WebSearchProvider | None = None,
    ):
        self.root = Path(root)
        self.store = JsonRunStore(self.root)
        self.model_config_store = ModelConfigStore(self.root)
        self.model_config = self.model_config_store.load()
        self.llm = llm or create_llm_provider(self.model_config["text"])
        self.image_generator: ImageGenerationProvider = create_image_generation_provider(self.model_config["image"])
        self.web_search: WebSearchProvider = web_search or create_web_search_provider(self.model_config["search"])
        self.images_dir = self.root / ".ai-planner" / "images"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.reference_library = ReferenceLibrary(self.root / ".ai-planner" / "references")
        self.knowledge = knowledge or LocalKnowledgeStore(root=self.root / "Doc" / "AI策划案管线" / "知识库")
        self.exporter = exporter or MarkdownExporter()
        self.image_provider = image_provider or LocalImageProvider()

    def get_model_config(self) -> Dict[str, Any]:
        return public_model_config(self.model_config)

    def update_model_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.model_config = self.model_config_store.save(payload)
        self.llm = create_llm_provider(self.model_config["text"])
        self.image_generator = create_image_generation_provider(self.model_config["image"])
        self.web_search = create_web_search_provider(self.model_config["search"])
        return self.get_model_config()

    def search_references(self, query: str, limit: int | None = None) -> List[ContextSegment]:
        query = str(query).strip()
        if not query:
            raise ValueError("搜索关键词不能为空")
        if len(query) > 500:
            raise ValueError("搜索关键词不能超过 500 个字符")
        selected_limit = limit if limit is not None else self.model_config["search"].get("max_results", 5)
        try:
            selected_limit = int(selected_limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("搜索结果数必须是数字") from exc
        if selected_limit < 1 or selected_limit > 10:
            raise ValueError("搜索结果数必须在 1 到 10 之间")
        return self.web_search.search(query, limit=selected_limit)

    def set_image_references(self, run_id: str, reference_ids: Sequence[str]) -> WorkflowRun:
        run = self.store.load(run_id)
        selected_ids = list(dict.fromkeys(str(item).strip() for item in reference_ids if str(item).strip()))
        if len(selected_ids) > MAX_REFERENCE_IMAGES:
            raise ValueError(f"总参考图最多选择 {MAX_REFERENCE_IMAGES} 张")
        for selected_id in selected_ids:
            metadata, image = self.reference_library.read_file(selected_id)
            if metadata.get("kind") != "image":
                raise ValueError("总参考图必须选择图片资料")
            _validate_reference_image(image, str(metadata.get("mime_type", "")))
        run.image_reference_ids = selected_ids
        return self.store.save(run)

    def _run_image_references(self, run: WorkflowRun) -> List[tuple[bytes, str, str, str]]:
        reference_ids = run.image_reference_ids
        if reference_ids is None:
            available_images = {
                item.get("id") for item in self.reference_library.list()
                if item.get("kind") == "image" and item.get("mime_type") in REFERENCE_IMAGE_MIME_TYPES
            }
            reference_ids = [item_id for item_id in run.reference_ids if item_id in available_images][:MAX_REFERENCE_IMAGES]
        references = []
        for reference_id in reference_ids:
            try:
                metadata, image = self.reference_library.read_file(reference_id)
            except KeyError:
                continue
            if metadata.get("kind") != "image":
                continue
            image_data, mime_type = _validate_reference_image(image, str(metadata.get("mime_type", "")))
            references.append((image_data, mime_type, str(metadata.get("filename", "总参考图")), reference_id))
        return references

    def generate_image(
        self,
        run_id: str,
        task_id: str,
        prompt: str = "",
        size: str | None = None,
        supplement_prompt: str = "",
        reference_image: str | None = None,
        reference_image_name: str = "",
    ) -> Dict[str, Any]:
        run = self.store.load(run_id)
        task = next((item for item in run.visual_tasks if item.id == task_id), None)
        if not task:
            raise KeyError(f"visual task not found: {task_id}")
        base_prompt = prompt.strip() or self._image_prompt(run, task)
        supplement = str(supplement_prompt or "").strip()
        final_prompt = base_prompt + (f"\n补充要求：{supplement}" if supplement else "")
        if len(final_prompt) > 8000:
            raise ValueError("图片提示词不能超过 8000 个字符")
        direct_reference = _decode_reference_image(reference_image)
        if direct_reference:
            references = [(direct_reference[0], direct_reference[1], reference_image_name or "参考图", "")]
        else:
            references = self._run_image_references(run)
        provider_references = [item[:2] for item in references]
        provider_reference = provider_references[0] if len(provider_references) == 1 else provider_references
        selected_size = str(size).strip() if size is not None else str(self.model_config["image"].get("size", "1024x1024"))
        if selected_size not in SUPPORTED_IMAGE_SIZES:
            raise ValueError("图片尺寸不受支持")
        if size is None:
            if references:
                image, mime_type, revised_prompt = self.image_generator.generate(final_prompt, reference_image=provider_reference)
            else:
                image, mime_type, revised_prompt = self.image_generator.generate(final_prompt)
        else:
            if references:
                image, mime_type, revised_prompt = self.image_generator.generate(final_prompt, size=selected_size, reference_image=provider_reference)
            else:
                image, mime_type, revised_prompt = self.image_generator.generate(final_prompt, size=selected_size)
        extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[mime_type]
        safe_task_id = re.sub(r"[^a-zA-Z0-9_-]", "", task.id)[:48] or "task"
        filename = f"{run.id}-{safe_task_id}-{uuid.uuid4().hex[:8]}.{extension}"
        (self.images_dir / filename).write_bytes(image)
        result = {
            "id": f"image-{uuid.uuid4().hex[:10]}",
            "run_id": run.id,
            "task_id": task.id,
            "screen_name": task.screen_name,
            "prompt": final_prompt,
            "base_prompt": base_prompt,
            "supplement_prompt": supplement,
            "revised_prompt": revised_prompt,
            "model": str(self.model_config["image"]["model"]),
            "size": selected_size,
            "url": f"/api/images/{filename}",
            "mime_type": mime_type,
        }
        if references:
            saved_references = []
            for index, (image_data, reference_mime, reference_name, reference_id) in enumerate(references, start=1):
                reference_extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[reference_mime]
                reference_filename = f"{run.id}-{safe_task_id}-reference-{index}-{uuid.uuid4().hex[:8]}.{reference_extension}"
                (self.images_dir / reference_filename).write_bytes(image_data)
                saved_reference = {
                    "url": f"/api/images/{reference_filename}",
                    "name": str(reference_name or f"参考图 {index}")[:160],
                }
                if reference_id:
                    saved_reference["id"] = reference_id
                saved_references.append(saved_reference)
            result["reference_images"] = saved_references
            result["reference_image_url"] = saved_references[0]["url"]
            result["reference_image_name"] = saved_references[0]["name"]
            if saved_references[0].get("id"):
                result["reference_image_id"] = saved_references[0]["id"]
        run.generated_images.append(result)
        task.status = "generated"
        self.store.save(run)
        return result

    @staticmethod
    def _image_prompt(run: WorkflowRun, task: Any) -> str:
        title = run.document.title if run.document else run.request
        components = "、".join(task.components)
        copy = "、".join(task.copy)
        return (
            f"为游戏活动《{title}》设计{task.screen_name}的高保真 UI 效果图。"
            f"用途：{task.purpose}。布局：{task.layout}。组件：{components}。"
            f"界面文案：{copy}。状态：{task.state}。画面清晰，信息层级明确，适合游戏内实际落地。"
        )

    def create_run(
        self,
        request: str,
        existing_document: Dict[str, Any] | None = None,
        reference_ids: Sequence[str] | None = None,
    ) -> WorkflowRun:
        run_id = f"run-{uuid.uuid4().hex[:10]}"
        has_document = bool(existing_document)
        run = WorkflowRun(id=run_id, request=request.strip())
        run.reference_ids = [str(item).strip() for item in (reference_ids or []) if str(item).strip()]
        route_request = run.request
        if run.reference_ids and len(route_request) < 12:
            route_request += "\n已附带本地参考文件，请结合资料继续生成策划案。"
        run.route = self.llm.route(route_request, has_document=has_document)
        if existing_document and run.route.planner_mode == PlannerMode.PARTIAL_REVISION:
            payload = existing_document.get("document", existing_document)
            if payload.get("id") and payload.get("chapters"):
                run.document = document_from_dict(payload)
        run.decisions.append({"type": "intent_route", "value": run.route.route.value, "reason": run.route.reason})
        if existing_document:
            run.decisions.append({"type": "base_document", "value": existing_document.get("id", "inline")})
        if run.reference_ids:
            run.decisions.append({"type": "uploaded_references", "ids": run.reference_ids[:]})
        if run.route.clarification_required:
            run.stage = Stage.NEEDS_CLARIFICATION
        self.store.save(run)
        return run

    def run(
        self,
        request: str,
        answers: Sequence[str] | None = None,
        existing_document: Dict[str, Any] | None = None,
        reference_ids: Sequence[str] | None = None,
    ) -> WorkflowRun:
        run = self.create_run(request, existing_document=existing_document, reference_ids=reference_ids)
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
        uploaded_refs = self.reference_library.context_segments(run.reference_ids)
        refs = self.knowledge.search(run.request, limit=4)
        web_refs: List[ContextSegment] = []
        search_config = self.model_config.get("search", {})
        if search_config.get("auto_context") and len(refs) < 2:
            try:
                web_refs = self.search_references(run.request, limit=min(3, int(search_config.get("max_results", 5))))
            except Exception as exc:
                run.decisions.append({"type": "web_search", "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            else:
                run.decisions.append({"type": "web_search", "status": "completed", "result_count": len(web_refs)})
        raw_chars = sum(len(item.content) for item in base + uploaded_refs + refs + web_refs)
        run.context = self._compact_context(base + uploaded_refs + refs + web_refs, max_chars=12000)
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
                result.append(ContextSegment(segment.id, segment.title, content[:room].rstrip() + "…", segment.source, segment.priority, room // 4, segment.url))
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
