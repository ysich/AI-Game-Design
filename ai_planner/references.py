from __future__ import annotations

import json
import io
import mimetypes
import re
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence
from xml.etree import ElementTree

from .models import ContextSegment


MAX_REFERENCE_FILE_BYTES = 20 * 1024 * 1024
MAX_REFERENCE_TEXT_CHARS = 80_000
SUPPORTED_REFERENCE_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".text", ".rst", ".csv", ".json", ".yaml", ".yml", ".log",
    ".doc", ".docx", ".pdf",
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg", ".tif", ".tiff", ".ico", ".avif", ".heic", ".heif",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg", ".tif", ".tiff", ".ico", ".avif", ".heic", ".heif"}
TEXT_EXTENSIONS = {".md", ".markdown", ".txt", ".text", ".rst", ".csv", ".json", ".yaml", ".yml", ".log"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_name(filename: str) -> str:
    name = Path(str(filename or "参考资料")).name.strip() or "参考资料"
    name = re.sub(r"[^0-9A-Za-z一-龥._ -]+", "_", name).strip(" .")
    return name[:160] or "参考资料"


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
    except (KeyError, ElementTree.ParseError, zipfile.BadZipFile) as exc:
        raise ValueError("DOCX 文档无法读取") from exc
    paragraphs: List[str] = []
    for paragraph in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
        text = "".join(node.text or "" for node in paragraph.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"))
        if text.strip():
            paragraphs.append(text.strip())
    return "\n\n".join(paragraphs)


def _extract_pdf(data: bytes) -> str:
    # This handles the common uncompressed PDF text operators without adding a
    # heavyweight dependency. The original PDF is always retained for download.
    raw = data.decode("latin-1", errors="ignore")
    chunks = []
    for match in re.finditer(r"\(([^()]*)\)\s*T[Jj]", raw):
        value = match.group(1).replace(r"\n", "\n").replace(r"\r", "\r").replace(r"\(", "(").replace(r"\)", ")")
        if value.strip():
            chunks.append(value)
    return "\n".join(chunks)


def _extract_legacy_doc(data: bytes) -> str:
    # Legacy .doc is an OLE binary format. Keep it usable without external
    # packages by extracting printable runs when possible.
    candidates = re.findall(rb"[\x20-\x7e\x80-\xff]{4,}", data)
    text = "\n".join(_decode_text(item).strip() for item in candidates)
    return text


class ReferenceLibrary:
    """Persistent local uploads used as optional workflow context."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.files_dir = self.root / "files"
        self.index_path = self.root / "_index.json"
        self.files_dir.mkdir(parents=True, exist_ok=True)

    def _load(self) -> List[Dict[str, Any]]:
        if not self.index_path.is_file():
            return []
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return payload if isinstance(payload, list) else []

    def _save(self, items: Sequence[Dict[str, Any]]) -> None:
        temporary = self.index_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(list(items), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.index_path)

    @staticmethod
    def _kind(extension: str) -> str:
        if extension in IMAGE_EXTENSIONS:
            return "image"
        if extension in TEXT_EXTENSIONS or extension in {".doc", ".docx", ".pdf"}:
            return "document"
        return "file"

    @staticmethod
    def _extract(filename: str, data: bytes) -> tuple[str, str]:
        extension = Path(filename).suffix.lower()
        if extension in IMAGE_EXTENSIONS:
            return "", "image"
        if extension in TEXT_EXTENSIONS:
            return _decode_text(data), "text"
        if extension == ".docx":
            return _extract_docx(data), "text"
        if extension == ".pdf":
            text = _extract_pdf(data)
            return text, "text" if text else "binary"
        if extension == ".doc":
            text = _extract_legacy_doc(data)
            return text, "text" if text else "binary"
        return "", "binary"

    def upload(self, filename: str, data: bytes, mime_type: str = "") -> Dict[str, Any]:
        safe_filename = _safe_name(filename)
        extension = Path(safe_filename).suffix.lower()
        if extension not in SUPPORTED_REFERENCE_EXTENSIONS:
            supported = ", ".join(sorted(SUPPORTED_REFERENCE_EXTENSIONS))
            raise ValueError(f"暂不支持 {extension or '该文件'}，支持：{supported}")
        if not data or len(data) > MAX_REFERENCE_FILE_BYTES:
            raise ValueError("参考文件大小必须在 1 B 到 20 MB 之间")
        content, extraction = self._extract(safe_filename, data)
        content = content[:MAX_REFERENCE_TEXT_CHARS]
        reference_id = f"upload-{uuid.uuid4().hex[:12]}"
        stored_name = reference_id + extension
        (self.files_dir / stored_name).write_bytes(data)
        metadata = {
            "id": reference_id,
            "filename": safe_filename,
            "title": Path(safe_filename).stem or safe_filename,
            "extension": extension,
            "kind": self._kind(extension),
            "mime_type": mime_type or mimetypes.guess_type(safe_filename)[0] or "application/octet-stream",
            "size": len(data),
            "created_at": _utc_now(),
            "stored_name": stored_name,
            "content_chars": len(content),
            "extraction": extraction,
            "url": f"/api/references/files/{reference_id}",
        }
        items = self._load()
        items.append(metadata)
        self._save(items)
        return self._public(metadata)

    @staticmethod
    def _public(metadata: Dict[str, Any]) -> Dict[str, Any]:
        return {key: value for key, value in metadata.items() if key != "stored_name"}

    def list(self) -> List[Dict[str, Any]]:
        return [self._public(item) for item in reversed(self._load())]

    def get(self, reference_id: str) -> Dict[str, Any]:
        item = next((item for item in self._load() if item.get("id") == reference_id), None)
        if not item:
            raise KeyError(f"reference not found: {reference_id}")
        return item

    def read_file(self, reference_id: str) -> tuple[Dict[str, Any], bytes]:
        item = self.get(reference_id)
        path = self.files_dir / str(item["stored_name"])
        if not path.is_file():
            raise KeyError(f"reference file not found: {reference_id}")
        return item, path.read_bytes()

    def delete(self, reference_id: str) -> Dict[str, Any]:
        item = self.get(reference_id)
        path = self.files_dir / str(item["stored_name"])
        if path.is_file():
            path.unlink()
        items = [entry for entry in self._load() if entry.get("id") != reference_id]
        self._save(items)
        return self._public(item)

    def context_segments(self, reference_ids: Iterable[str]) -> List[ContextSegment]:
        selected = set(str(item) for item in reference_ids if str(item).strip())
        segments: List[ContextSegment] = []
        for metadata in self._load():
            if metadata.get("id") not in selected:
                continue
            content = ""
            extraction = metadata.get("extraction")
            if extraction == "image":
                content = f"已上传图片参考《{metadata['filename']}》，可在视觉任务中查看原图。"
            else:
                content_path = self.files_dir / str(metadata["stored_name"])
                try:
                    content, _ = self._extract(str(metadata["filename"]), content_path.read_bytes())
                except OSError:
                    content = ""
                if not content.strip():
                    content = f"已上传文件《{metadata['filename']}》，但未能提取可检索正文；请直接打开原文件查看。"
            segments.append(ContextSegment(
                id=str(metadata["id"]),
                title=f"上传参考：{metadata['filename']}",
                content=content[:MAX_REFERENCE_TEXT_CHARS],
                source=f"upload:{metadata['filename']}",
                priority=88,
                token_estimate=max(1, len(content) // 4),
                url=str(metadata.get("url", "")),
            ))
        return segments
