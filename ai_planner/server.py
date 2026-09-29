from __future__ import annotations

import json
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .models import to_dict
from .pipeline import PlannerPipeline


MAX_JSON_BODY_BYTES = 16 * 1024 * 1024


class PlannerRequestHandler(BaseHTTPRequestHandler):
    pipeline: PlannerPipeline
    web_root: Path

    def _send(self, status: int, payload, content_type: str = "application/json; charset=utf-8") -> None:
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        elif isinstance(payload, str):
            body = payload.encode("utf-8")
        else:
            body = bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_JSON_BODY_BYTES:
            raise ValueError("请求体不能超过 16 MB")
        return json.loads(self.rfile.read(length) or b"{}")

    def _static_file(self, path: str) -> bool:
        relative = path.removeprefix("/web/")
        if not relative or relative.startswith("/") or ".." in Path(relative).parts:
            return False
        candidate = (self.web_root / relative).resolve()
        web_root = self.web_root.resolve()
        if web_root not in candidate.parents and candidate != web_root:
            return False
        if not candidate.is_file():
            return False
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self._send(200, candidate.read_bytes(), content_type)
        return True

    def _analytics(self):
        runs = self.pipeline.store.list()
        by_day = {}
        for run in runs:
            day = (run.updated_at or run.created_at or "")[:10] or "unknown"
            item = by_day.setdefault(day, {"date": day, "runs": 0, "exported": 0, "clarifications": 0, "findings": 0})
            item["runs"] += 1
            item["exported"] += int(run.stage.value == "exported")
            item["clarifications"] += int(run.stage.value == "needs_clarification")
            item["findings"] += len(run.findings)
        documents = self.pipeline.store.document_library.list()
        return {
            "metrics": {
                "runs": len(runs),
                "exported": sum(run.stage.value == "exported" for run in runs),
                "clarifications": sum(run.stage.value == "needs_clarification" for run in runs),
                "documents": len(documents),
                "visual_tasks": sum(len(run.visual_tasks) for run in runs),
                "findings": sum(len(run.findings) for run in runs),
            },
            "daily": sorted(by_day.values(), key=lambda item: item["date"], reverse=True),
            "recent_runs": [
                {"id": run.id, "request": run.request, "stage": run.stage.value, "updated_at": run.updated_at, "title": run.document.title if run.document else ""}
                for run in runs[:20]
            ],
        }

    def _coding_case(self, run):
        if not run.document:
            raise ValueError("该运行记录还没有可生成 Coding 案的策划案")
        document = run.document
        chapter = {item.id: item for item in document.chapters}
        lines = [f"# {document.title} · Coding 案", "", f"> 来源运行：{run.id}  |  版本：{document.version}", "", "## 研发目标", "", chapter.get("overview", document.chapters[0]).content, ""]
        for key, title in (("flow", "流程与状态"), ("ui", "客户端界面"), ("analytics", "埋点与数据"), ("tech", "服务端与技术要求"), ("exceptions", "异常与验收")):
            item = chapter.get(key)
            if item:
                lines.extend([f"## {title}", "", item.content, ""])
        lines.extend(["## 页面任务", ""])
        for task in run.visual_tasks:
            lines.append(f"- {task.screen_name}：{task.purpose}；组件：{'、'.join(task.components)}；状态：{task.state}")
        lines.extend(["", "## 开发检查项", "", "- 配置、奖励发放和结算以服务端状态为准。", "- 客户端断线重连后恢复活动状态。", "- 所有奖励领取接口支持幂等。", "- 上线前完成规则检查与埋点核对。", ""])
        return "\n".join(lines)

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                self._send(200, (self.web_root / "index.html").read_text(encoding="utf-8"), "text/html; charset=utf-8")
                return
            if path.startswith("/web/") and self._static_file(path):
                return
            if path == "/api/health":
                self._send(200, {"ok": True, "service": "ai-planner-pipeline"})
                return
            if path == "/api/config":
                self._send(200, self.pipeline.get_model_config())
                return
            if path.startswith("/api/images/"):
                filename = path.split("/", 3)[3]
                if Path(filename).name != filename:
                    self._send(404, {"error": "not found"})
                    return
                image_path = self.pipeline.images_dir / filename
                if not image_path.is_file():
                    self._send(404, {"error": "image not found"})
                    return
                content_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
                self._send(200, image_path.read_bytes(), content_type)
                return
            if path == "/api/runs":
                self._send(200, [to_dict(run) for run in self.pipeline.store.list()])
                return
            if path == "/api/knowledge":
                keyword = query.get("q", [""])[0]
                items = self.pipeline.knowledge.items
                if keyword:
                    needle = keyword.lower()
                    items = [item for item in items if needle in f"{item.title} {item.content} {' '.join(item.tags)}".lower()]
                self._send(200, [item.__dict__ for item in items])
                return
            if path in ("/api/references/search", "/api/references"):
                keyword = query.get("q", [""])[0]
                limit_value = query.get("limit", [None])[0]
                limit = int(limit_value) if limit_value else None
                items = self.pipeline.search_references(keyword, limit=limit)
                self._send(200, {
                    "query": keyword,
                    "items": [to_dict(item) for item in items],
                    "count": len(items),
                    "provider": self.pipeline.model_config.get("search", {}).get("provider", "disabled"),
                })
                return
            if path == "/api/analytics":
                self._send(200, self._analytics())
                return
            if path == "/api/documents":
                keyword = query.get("q", [""])[0]
                documents = self.pipeline.store.document_library.search(keyword) if keyword else self.pipeline.store.document_library.list()
                self._send(200, documents)
                return
            if path.startswith("/api/documents/"):
                document_id = unquote(path.split("/", 3)[3])
                version_value = query.get("version", [None])[0]
                version = int(version_value) if version_value else None
                self._send(200, self.pipeline.store.document_library.get(document_id, version=version))
                return
            if path.startswith("/api/runs/"):
                run_id = unquote(path.split("/", 3)[3])
                run = self.pipeline.store.load(run_id)
                self._send(200, to_dict(run))
                return
            self._send(404, {"error": "not found"})
        except KeyError as exc:
            self._send(404, {"error": str(exc)})
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_PUT(self):  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = self._json_body()
            if path.startswith("/api/documents/"):
                document_id = unquote(path.split("/", 3)[3])
                expected_version = body.get("expected_version")
                if expected_version is not None:
                    expected_version = int(expected_version)
                document = self.pipeline.store.document_library.update(
                    document_id,
                    title=str(body.get("title", "")),
                    activity_type=str(body.get("activity_type", "")),
                    content=str(body.get("content", "")),
                    expected_version=expected_version,
                )
                self._send(200, document)
                return
            if path.startswith("/api/runs/"):
                run_id = unquote(path.split("/", 3)[3])
                record = body.get("record")
                if not isinstance(record, dict):
                    raise ValueError("record 必须是 JSON 对象")
                run = self.pipeline.store.update(run_id, record, str(body.get("expected_updated_at", "")))
                self._send(200, to_dict(run))
                return
            self._send(404, {"error": "not found"})
        except KeyError as exc:
            self._send(404, {"error": str(exc)})
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_DELETE(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path.startswith("/api/documents/"):
                document_id = unquote(path.split("/", 3)[3])
                version_value = query.get("expected_version", [None])[0]
                expected_version = int(version_value) if version_value is not None else None
                self._send(200, self.pipeline.store.document_library.delete(document_id, expected_version))
                return
            if path.startswith("/api/runs/"):
                run_id = unquote(path.split("/", 3)[3])
                expected_updated_at = query.get("expected_updated_at", [""])[0]
                self._send(200, self.pipeline.store.delete(run_id, expected_updated_at))
                return
            self._send(404, {"error": "not found"})
        except KeyError as exc:
            self._send(404, {"error": str(exc)})
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = self._json_body()
            if path == "/api/config":
                self._send(200, self.pipeline.update_model_config(body))
                return
            if path == "/api/images/generate":
                run_id = str(body.get("run_id", "")).strip()
                task_id = str(body.get("task_id", "")).strip()
                if not run_id or not task_id:
                    raise ValueError("run_id 和 task_id 不能为空")
                requested_size = body.get("size")
                result = self.pipeline.generate_image(
                    run_id,
                    task_id,
                    str(body.get("prompt", "")),
                    size=None if requested_size is None else str(requested_size),
                    supplement_prompt=str(body.get("supplement_prompt", "")),
                    reference_image=body.get("reference_image"),
                    reference_image_name=str(body.get("reference_image_name", "")),
                )
                self._send(201, result)
                return
            if path == "/api/runs":
                request = str(body.get("request", ""))
                answers = body.get("answers") or []
                run = self.pipeline.run(request, answers=answers, existing_document=body.get("existing_document"))
                self._send(200, to_dict(run))
                return
            if path == "/api/coding":
                run_id = str(body.get("run_id", "")).strip()
                if not run_id:
                    raise ValueError("run_id 不能为空")
                run = self.pipeline.store.load(run_id)
                self._send(200, {"run_id": run_id, "title": run.document.title if run.document else "", "markdown": self._coding_case(run)})
                return
            if path == "/api/test-cases/run":
                cases = body.get("cases") or []
                results = []
                for index, case in enumerate(cases[:30], start=1):
                    request = str(case.get("request", "")).strip() if isinstance(case, dict) else str(case).strip()
                    if not request:
                        results.append({"index": index, "request": "", "status": "skipped", "message": "空输入"})
                        continue
                    run = self.pipeline.run(request)
                    results.append({"index": index, "request": request, "run_id": run.id, "stage": run.stage.value, "status": "passed" if run.stage.value == "exported" else "needs_review", "findings": len(run.findings), "message": "已完成本地管线" if run.stage.value == "exported" else "需要补充需求"})
                self._send(200, {"results": results})
                return
            if path.startswith("/api/runs/") and path.endswith("/revise"):
                run_id = path.split("/")[3]
                run = self.pipeline.revise(run_id, str(body.get("instruction", "")))
                self._send(200, to_dict(run))
                return
            self._send(404, {"error": "not found"})
        except KeyError as exc:
            self._send(404, {"error": str(exc)})
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, format, *args):
        # Keep the local workbench terminal readable.
        sys.stderr.write("[planner] " + format % args + "\n")


def serve(root: Path | str = ".", host: str = "127.0.0.1", port: int = 8765) -> None:
    root = Path(root).resolve()

    class Handler(PlannerRequestHandler):
        pass

    Handler.pipeline = PlannerPipeline(root)
    Handler.web_root = root / "web"
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"AI planner workbench: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
