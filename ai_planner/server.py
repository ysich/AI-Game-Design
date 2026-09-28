from __future__ import annotations

import json
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .adapters import default_knowledge_items
from .models import to_dict
from .pipeline import PlannerPipeline


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
        return json.loads(self.rfile.read(length) or b"{}")

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                self._send(200, (self.web_root / "index.html").read_text(encoding="utf-8"), "text/html; charset=utf-8")
                return
            if path == "/api/health":
                self._send(200, {"ok": True, "service": "ai-planner-pipeline"})
                return
            if path == "/api/runs":
                self._send(200, [to_dict(run) for run in self.pipeline.store.list()])
                return
            if path == "/api/knowledge":
                self._send(200, [item.__dict__ for item in default_knowledge_items()])
                return
            if path == "/api/documents":
                keyword = query.get("q", [""])[0]
                documents = self.pipeline.store.document_library.search(keyword) if keyword else self.pipeline.store.document_library.list()
                self._send(200, documents)
                return
            if path.startswith("/api/documents/"):
                document_id = path.split("/", 3)[3]
                version_value = query.get("version", [None])[0]
                version = int(version_value) if version_value else None
                self._send(200, self.pipeline.store.document_library.get(document_id, version=version))
                return
            if path.startswith("/api/runs/"):
                run_id = path.split("/", 3)[3]
                run = self.pipeline.store.load(run_id)
                self._send(200, to_dict(run))
                return
            self._send(404, {"error": "not found"})
        except KeyError as exc:
            self._send(404, {"error": str(exc)})
        except Exception as exc:
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = self._json_body()
            if path == "/api/runs":
                request = str(body.get("request", ""))
                answers = body.get("answers") or []
                run = self.pipeline.run(request, answers=answers, existing_document=body.get("existing_document"))
                self._send(200, to_dict(run))
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
