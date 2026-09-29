from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import PlanningDocument, WorkflowRun, run_from_dict, to_dict


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_filename(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", value, flags=re.UNICODE).strip(".-")
    return (cleaned or fallback)[:80]


class MarkdownDocumentLibrary:
    """A visible, local Markdown library with searchable metadata and history."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.history_dir = self.root / "history"
        self.index_path = self.root / "_index.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)

    def _read_index(self) -> List[Dict[str, Any]]:
        if not self.index_path.exists():
            return []
        payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else []

    def _write_index(self, records: List[Dict[str, Any]]) -> None:
        temp = self.index_path.with_suffix(".tmp")
        temp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.index_path)

    def _record(self, document_id: str) -> Dict[str, Any]:
        record = next((item for item in self._read_index() if item.get("id") == document_id), None)
        if not record:
            raise KeyError(f"document not found: {document_id}")
        return record

    def _path(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        root = self.root.resolve()
        if root not in candidate.parents:
            raise ValueError("document path is outside the local library")
        return candidate

    def publish(self, document: PlanningDocument, markdown: str, run_id: str = "") -> Dict[str, Any]:
        """Write the latest document and an immutable version copy, then update the index."""
        doc_id = _safe_filename(document.id, "document")
        slug = _safe_filename(document.title, doc_id)
        latest_path = self.root / f"{slug}-{doc_id}.md"
        history_doc_dir = self.history_dir / doc_id
        history_doc_dir.mkdir(parents=True, exist_ok=True)
        version_path = history_doc_dir / f"v{document.version}.md"
        latest_path.write_text(markdown, encoding="utf-8")
        version_path.write_text(markdown, encoding="utf-8")

        records = self._read_index()
        now = _now()
        record = next((item for item in records if item.get("id") == document.id), None)
        if record is None:
            record = {"id": document.id, "versions": []}
            records.append(record)
        record.update({
            "title": document.title,
            "activity_type": document.activity_type,
            "version": document.version,
            "updated_at": now,
            "run_id": run_id,
            "path": str(latest_path.relative_to(self.root)),
            "content_chars": len(markdown),
        })
        versions = [item for item in record.get("versions", []) if item.get("version") != document.version]
        versions.append({"version": document.version, "path": str(version_path.relative_to(self.root)), "updated_at": now})
        record["versions"] = sorted(versions, key=lambda item: item["version"])
        self._write_index(sorted(records, key=lambda item: item.get("updated_at", ""), reverse=True))
        return {
            "id": document.id,
            "title": document.title,
            "version": document.version,
            "path": str(latest_path.relative_to(self.root)),
            "version_path": str(version_path.relative_to(self.root)),
            "updated_at": now,
        }

    def list(self) -> List[Dict[str, Any]]:
        return self._read_index()

    def get(self, document_id: str, version: Optional[int] = None) -> Dict[str, Any]:
        record = self._record(document_id)
        relative_path = record["path"]
        if version is not None:
            version_item = next((item for item in record.get("versions", []) if item.get("version") == version), None)
            if not version_item:
                raise KeyError(f"document version not found: {document_id}/v{version}")
            relative_path = version_item["path"]
        path = self._path(relative_path)
        if not path.exists():
            raise KeyError(f"document file not found: {relative_path}")
        result = dict(record)
        result["requested_version"] = version or record.get("version")
        result["content"] = path.read_text(encoding="utf-8")
        return result

    def update(
        self,
        document_id: str,
        *,
        title: str,
        activity_type: str,
        content: str,
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Save an edited document as a new immutable library version."""
        title = title.strip()
        activity_type = activity_type.strip()
        if not title:
            raise ValueError("文档标题不能为空")
        if not activity_type:
            raise ValueError("活动类型不能为空")
        if not content.strip():
            raise ValueError("文档正文不能为空")
        if len(content.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("文档正文不能超过 2 MB")

        records = self._read_index()
        record = next((item for item in records if item.get("id") == document_id), None)
        if not record:
            raise KeyError(f"document not found: {document_id}")
        current_version = int(record.get("version", 0))
        if expected_version is not None and expected_version != current_version:
            raise ValueError(f"文档已更新到 v{current_version}，请刷新后重试")

        version = current_version + 1
        now = _now()
        latest_path = self._path(str(record["path"]))
        history_doc_dir = self.history_dir / _safe_filename(document_id, "document")
        history_doc_dir.mkdir(parents=True, exist_ok=True)
        version_path = history_doc_dir / f"v{version}.md"
        for path in (latest_path, version_path):
            temp = path.with_suffix(path.suffix + ".tmp")
            temp.write_text(content, encoding="utf-8")
            temp.replace(path)

        record.update({
            "title": title,
            "activity_type": activity_type,
            "version": version,
            "updated_at": now,
            "content_chars": len(content),
        })
        versions = [item for item in record.get("versions", []) if item.get("version") != version]
        versions.append({"version": version, "path": str(version_path.relative_to(self.root)), "updated_at": now})
        record["versions"] = sorted(versions, key=lambda item: item["version"])
        self._write_index(sorted(records, key=lambda item: item.get("updated_at", ""), reverse=True))
        return self.get(document_id)

    def search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        words = [word.lower() for word in re.findall(r"[\w\u4e00-\u9fff]+", query) if len(word) > 1]
        records = self._read_index()
        if not words:
            return records[:limit]
        ranked = []
        for record in records:
            haystack = f"{record.get('title', '')} {record.get('activity_type', '')}".lower()
            try:
                haystack += " " + (self.root / record["path"]).read_text(encoding="utf-8").lower()
            except OSError:
                pass
            score = sum(1 for word in words if word in haystack)
            if score:
                ranked.append((score, record))
        ranked.sort(key=lambda item: (item[0], item[1].get("updated_at", "")), reverse=True)
        return [item[1] for item in ranked[:limit]]


class JsonRunStore:
    """Small file store so the pipeline can be used without a database."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.runs_dir = self.root / ".ai-planner" / "runs"
        self.exports_dir = self.root / ".ai-planner" / "exports"
        self.document_library = MarkdownDocumentLibrary(self.root / "Doc" / "AI策划案管线" / "文档库")
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, run_id: str) -> Path:
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", run_id)
        return self.runs_dir / f"{safe_id}.json"

    def save(self, run: WorkflowRun) -> WorkflowRun:
        run.touch()
        path = self.path_for(run.id)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(to_dict(run), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
        return run

    def load(self, run_id: str) -> WorkflowRun:
        path = self.path_for(run_id)
        if not path.exists():
            raise KeyError(f"workflow run not found: {run_id}")
        return run_from_dict(json.loads(path.read_text(encoding="utf-8")))

    def update(self, run_id: str, payload: Dict[str, Any], expected_updated_at: str = "") -> WorkflowRun:
        """Validate and replace one local run snapshot while keeping its ID stable."""
        current = self.load(run_id)
        if expected_updated_at and current.updated_at != expected_updated_at:
            raise ValueError("运行记录已被其他操作更新，请刷新后重试")
        if not isinstance(payload, dict):
            raise ValueError("运行记录必须是 JSON 对象")
        if payload.get("id") != run_id:
            raise ValueError("运行记录 ID 不允许修改")
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 4 * 1024 * 1024:
            raise ValueError("运行记录不能超过 4 MB")
        try:
            run = run_from_dict(payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"运行记录结构无效：{exc}") from exc
        return self.save(run)

    def list(self) -> List[WorkflowRun]:
        items = [run_from_dict(json.loads(path.read_text(encoding="utf-8"))) for path in self.runs_dir.glob("*.json")]
        return sorted(items, key=lambda item: item.updated_at, reverse=True)

    def write_export(self, run_id: str, suffix: str, content: str) -> Path:
        path = self.exports_dir / f"{run_id}.{suffix}"
        path.write_text(content, encoding="utf-8")
        return path
