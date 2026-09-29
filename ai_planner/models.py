from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Type, TypeVar


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Route(str, Enum):
    ANSWER = "answer"
    CLARIFY = "clarify"
    NEW_PLAN = "new_plan"
    PARTIAL_REVISION = "partial_revision"
    FULL_REWRITE = "full_rewrite"
    IMAGE_ONLY = "image_only"


class PlannerMode(str, Enum):
    NEW = "new"
    PARTIAL_REVISION = "partial_revision"
    FULL_REWRITE = "full_rewrite"
    IMAGE_ONLY = "image_only"


class Stage(str, Enum):
    INTAKE = "intake"
    CONTEXT_READY = "context_ready"
    DRAFTED = "drafted"
    REVIEWED = "reviewed"
    VISUALIZED = "visualized"
    EXPORTED = "exported"
    NEEDS_CLARIFICATION = "needs_clarification"
    FAILED = "failed"


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class IntentRoute:
    route: Route
    planner_mode: PlannerMode
    confidence: float
    clarification_required: bool = False
    clarification_questions: List[str] = field(default_factory=list)
    suggestion_request: bool = False
    continue_planner_after_image: bool = False
    image_result_should_update_planner: bool = False
    reason: str = ""


@dataclass
class ContextSegment:
    id: str
    title: str
    content: str
    source: str
    priority: int = 50
    token_estimate: int = 0
    url: str = ""


@dataclass
class Chapter:
    id: str
    title: str
    content: str
    source_refs: List[str] = field(default_factory=list)
    status: str = "draft"


@dataclass
class PlanningDocument:
    id: str
    title: str
    activity_type: str
    chapters: List[Chapter]
    version: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)

    def chapter(self, chapter_id: str) -> Optional[Chapter]:
        return next((item for item in self.chapters if item.id == chapter_id), None)


@dataclass
class CheckFinding:
    rule_id: str
    severity: Severity
    message: str
    chapter_id: Optional[str] = None
    evidence: str = ""
    suggestion: str = ""


@dataclass
class VisualTask:
    id: str
    screen_name: str
    purpose: str
    layout: str
    components: List[str]
    copy: List[str]
    state: str
    reference_refs: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)
    status: str = "planned"


@dataclass
class WorkflowRun:
    id: str
    request: str
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    stage: Stage = Stage.INTAKE
    route: Optional[IntentRoute] = None
    context: List[ContextSegment] = field(default_factory=list)
    document: Optional[PlanningDocument] = None
    findings: List[CheckFinding] = field(default_factory=list)
    visual_tasks: List[VisualTask] = field(default_factory=list)
    generated_images: List[Dict[str, str]] = field(default_factory=list)
    flow_edges: List[Dict[str, str]] = field(default_factory=list)
    decisions: List[Dict[str, Any]] = field(default_factory=list)
    exports: Dict[str, str] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def touch(self) -> None:
        self.updated_at = utc_now()


T = TypeVar("T")


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def to_dict(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: to_dict(item) for key, item in value.items()}
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_dict(item) for key, item in asdict(value).items()}
    return value


def _chapter(data: Dict[str, Any]) -> Chapter:
    return Chapter(**data)


def document_from_dict(document: Dict[str, Any]) -> PlanningDocument:
    return PlanningDocument(
        id=document["id"],
        title=document["title"],
        activity_type=document["activity_type"],
        chapters=[_chapter(item) for item in document.get("chapters", [])],
        version=document.get("version", 1),
        metadata=document.get("metadata", {}),
    )


def _context_segment(data: Dict[str, Any]) -> ContextSegment:
    """Load context snapshots while tolerating fields added by newer versions."""
    segment = ContextSegment(
        id=data["id"],
        title=data["title"],
        content=data["content"],
        source=data["source"],
        priority=data.get("priority", 50),
        token_estimate=data.get("token_estimate", 0),
    )
    if "url" in data:
        segment.url = str(data.get("url") or "")
    return segment


def run_from_dict(data: Dict[str, Any]) -> WorkflowRun:
    route = data.get("route")
    route_obj = None
    if route:
        route_obj = IntentRoute(
            route=Route(route["route"]),
            planner_mode=PlannerMode(route["planner_mode"]),
            confidence=route["confidence"],
            clarification_required=route.get("clarification_required", False),
            clarification_questions=route.get("clarification_questions", []),
            suggestion_request=route.get("suggestion_request", False),
            continue_planner_after_image=route.get("continue_planner_after_image", False),
            image_result_should_update_planner=route.get("image_result_should_update_planner", False),
            reason=route.get("reason", ""),
        )
    document = data.get("document")
    document_obj = None
    if document:
        document_obj = document_from_dict(document)
    return WorkflowRun(
        id=data["id"],
        request=data["request"],
        created_at=data.get("created_at", utc_now()),
        updated_at=data.get("updated_at", utc_now()),
        stage=Stage(data.get("stage", Stage.INTAKE.value)),
        route=route_obj,
        context=[_context_segment(item) for item in data.get("context", [])],
        document=document_obj,
        findings=[
            CheckFinding(
                rule_id=item["rule_id"],
                severity=Severity(item["severity"]),
                message=item["message"],
                chapter_id=item.get("chapter_id"),
                evidence=item.get("evidence", ""),
                suggestion=item.get("suggestion", ""),
            )
            for item in data.get("findings", [])
        ],
        visual_tasks=[VisualTask(**item) for item in data.get("visual_tasks", [])],
        generated_images=data.get("generated_images", []),
        flow_edges=data.get("flow_edges", []),
        decisions=data.get("decisions", []),
        exports=data.get("exports", {}),
        errors=data.get("errors", []),
    )
