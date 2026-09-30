import http.client
import json
import tempfile
import threading
import unittest
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from ai_planner.pipeline import PlannerPipeline
from ai_planner.server import PlannerRequestHandler


class FakeHttpResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class ModelTestingTests(unittest.TestCase):
    def test_text_test_uses_unsaved_values_and_preserves_saved_config(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = PlannerPipeline(Path(directory))
            pipeline.update_model_config({
                "text": {
                    "provider": "openai_compatible",
                    "model": "saved-model",
                    "base_url": "https://saved.example/v1",
                    "api_key": "saved-secret",
                }
            })
            with patch(
                "urllib.request.urlopen",
                return_value=FakeHttpResponse({"choices": [{"message": {"content": "OK"}}]}),
            ) as request:
                result = pipeline.test_model_config("text", {
                    "provider": "openai_compatible",
                    "model": "unsaved-model",
                    "base_url": "https://draft.example/v1",
                    "api_key": "",
                    "temperature": 1.2,
                    "max_tokens": 4000,
                    "timeout_seconds": 20,
                })

            sent = request.call_args.args[0]
            body = json.loads(sent.data)
            self.assertTrue(result["ok"])
            self.assertEqual(sent.full_url, "https://draft.example/v1/chat/completions")
            self.assertEqual(sent.headers["Authorization"], "Bearer saved-secret")
            self.assertEqual(body["model"], "unsaved-model")
            self.assertEqual(body["max_tokens"], 16)
            self.assertEqual(pipeline.model_config["text"]["model"], "saved-model")

    def test_image_test_queries_model_without_generating_an_image(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = PlannerPipeline(Path(directory))
            with patch(
                "urllib.request.urlopen",
                return_value=FakeHttpResponse({"id": "image/preview", "object": "model"}),
            ) as request:
                result = pipeline.test_model_config("image", {
                    "provider": "openai_compatible",
                    "model": "image/preview",
                    "base_url": "https://images.example/v1/images/generations",
                    "api_key": "image-secret",
                    "size": "1024x1024",
                    "quality": "auto",
                    "timeout_seconds": 30,
                })

            sent = request.call_args.args[0]
            self.assertTrue(result["ok"])
            self.assertEqual(sent.method, "GET")
            self.assertEqual(sent.full_url, "https://images.example/v1/models/image%2Fpreview")
            self.assertIsNone(sent.data)
            self.assertEqual(sent.headers["Authorization"], "Bearer image-secret")

    def test_connection_refused_includes_actionable_hint(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = PlannerPipeline(Path(directory))
            error = urllib.error.URLError(ConnectionRefusedError(10061, "目标计算机积极拒绝"))
            with patch("urllib.request.urlopen", side_effect=error):
                with self.assertRaisesRegex(RuntimeError, "检查接口地址和端口"):
                    pipeline.test_model_config("image", {
                        "provider": "openai_compatible",
                        "model": "image-model",
                        "base_url": "http://127.0.0.1:9999/v1",
                    })

    def test_search_api_key_is_kept_when_a_later_save_leaves_it_blank(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = PlannerPipeline(Path(directory))
            pipeline.update_model_config({
                "search": {
                    "provider": "tavily",
                    "base_url": "https://api.tavily.com/search",
                    "api_key": "search-secret",
                }
            })
            pipeline.update_model_config({
                "search": {
                    "provider": "tavily",
                    "base_url": "https://api.tavily.com/search",
                    "api_key": "",
                }
            })
            self.assertEqual(pipeline.model_config["search"]["api_key"], "search-secret")

    def test_settings_page_exposes_a_test_button_for_every_provider(self):
        page = (Path(__file__).parents[1] / "web" / "model-settings.html").read_text(encoding="utf-8")
        self.assertEqual(page.count("data-test-provider="), 3)
        self.assertIn("/api/config/test", page)

    def test_config_test_http_endpoint_returns_structured_result(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = PlannerPipeline(Path(directory))

            class Handler(PlannerRequestHandler):
                pass

            Handler.pipeline = pipeline
            Handler.web_root = Path(__file__).parents[1] / "web"
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                body = json.dumps({
                    "kind": "text",
                    "config": {"provider": "local", "model": "local-rule-v1"},
                })
                connection.request("POST", "/api/config/test", body, {"Content-Type": "application/json"})
                response = connection.getresponse()
                payload = json.loads(response.read().decode("utf-8"))
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

            self.assertEqual(response.status, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["kind"], "text")


if __name__ == "__main__":
    unittest.main()
