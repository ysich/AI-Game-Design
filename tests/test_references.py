import io
import json
import shutil
import threading
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ai_planner.pipeline import PlannerPipeline
from ai_planner.references import ReferenceLibrary
from ai_planner.server import PlannerRequestHandler


class CapturingImageProvider:
    def __init__(self):
        self.reference_image = None

    def generate(self, prompt, **kwargs):
        self.reference_image = kwargs.get("reference_image")
        return b"\x89PNG\r\n\x1a\nresult", "image/png", prompt + " refined"


class ReferenceLibraryTests(unittest.TestCase):
    def test_upload_extracts_text_docx_and_keeps_image(self):
        root = Path.cwd() / ".reference-library-test"
        shutil.rmtree(root, ignore_errors=True)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        library = ReferenceLibrary(root)
        markdown = library.upload("活动规则.md", "# 奖励规则\n每日签到获得 10 积分".encode(), "text/markdown")
        self.assertEqual(markdown["kind"], "document")
        self.assertEqual(markdown["extraction"], "text")

        document_xml = '''<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>DOCX 参考正文</w:t></w:r></w:p></w:body></w:document>'''.encode()
        docx_data = io.BytesIO()
        with zipfile.ZipFile(docx_data, "w") as archive:
            archive.writestr("word/document.xml", document_xml)
        docx = library.upload("需求.docx", docx_data.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        image = library.upload("界面.png", b"\x89PNG\r\n\x1a\nimage", "image/png")

        segments = library.context_segments([markdown["id"], docx["id"], image["id"]])
        self.assertIn("奖励规则", next(item.content for item in segments if item.id == markdown["id"]))
        self.assertIn("DOCX 参考正文", next(item.content for item in segments if item.id == docx["id"]))
        self.assertIn("已上传图片", next(item.content for item in segments if item.id == image["id"]))


class ReferenceApiTests(unittest.TestCase):
    def setUp(self):
        root = Path.cwd() / ".reference-api-test"
        shutil.rmtree(root, ignore_errors=True)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        pipeline = PlannerPipeline(root)
        self.pipeline = pipeline

        class Handler(PlannerRequestHandler):
            pass

        Handler.pipeline = pipeline
        Handler.web_root = Path("web")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    @staticmethod
    def multipart(filename, content, content_type="text/plain"):
        boundary = "----reference-test-boundary"
        body = (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"files\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
        return body, f"multipart/form-data; boundary={boundary}"

    def request(self, path, data=None, headers=None, method="GET"):
        request = Request(self.base_url + path, data=data, headers=headers or {}, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_upload_and_use_reference_in_pipeline(self):
        body, content_type = self.multipart("rules.txt", "奖励上限为 10 次".encode())
        status, result = self.request("/api/references/upload", body, {"Content-Type": content_type}, "POST")
        self.assertEqual(status, 201)
        reference_id = result["items"][0]["id"]

        status, listed = self.request("/api/references/uploaded")
        self.assertEqual(status, 200)
        self.assertEqual(listed[0]["id"], reference_id)
        status, run = self.request(
            "/api/runs",
            json.dumps({"request": "请参考资料", "reference_ids": [reference_id]}).encode(),
            {"Content-Type": "application/json"},
            "POST",
        )
        self.assertEqual(status, 200)
        self.assertEqual(run["reference_ids"], [reference_id])
        self.assertTrue(any(item["id"] == reference_id and "奖励上限" in item["content"] for item in run["context"]))

    def test_run_level_image_reference_is_reused_for_generation(self):
        image_data = b"\x89PNG\r\n\x1a\nreference"
        body, content_type = self.multipart("整体风格.png", image_data, "image/png")
        status, uploaded = self.request("/api/references/upload", body, {"Content-Type": content_type}, "POST")
        self.assertEqual(status, 201)
        reference_id = uploaded["items"][0]["id"]

        status, run = self.request(
            "/api/runs",
            json.dumps({"request": "设计一个包含任务、奖励和结算页面的周末挑战活动。"}).encode(),
            {"Content-Type": "application/json"},
            "POST",
        )
        self.assertEqual(status, 200)
        status, updated = self.request(
            f"/api/runs/{run['id']}/image-reference",
            json.dumps({"reference_id": reference_id}).encode(),
            {"Content-Type": "application/json"},
            "PUT",
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["image_reference_id"], reference_id)

        provider = CapturingImageProvider()
        self.pipeline.image_generator = provider
        status, generated = self.request(
            "/api/images/generate",
            json.dumps({"run_id": run["id"], "task_id": run["visual_tasks"][0]["id"]}).encode(),
            {"Content-Type": "application/json"},
            "POST",
        )
        self.assertEqual(status, 201)
        self.assertEqual(provider.reference_image, (image_data, "image/png"))
        self.assertEqual(generated["reference_image_id"], reference_id)
        self.assertEqual(generated["reference_image_name"], "整体风格.png")

        status, cleared = self.request(
            f"/api/runs/{run['id']}/image-reference",
            json.dumps({"reference_id": ""}).encode(),
            {"Content-Type": "application/json"},
            "PUT",
        )
        self.assertEqual(status, 200)
        self.assertEqual(cleared["image_reference_id"], "")

    def test_run_uses_selected_library_image_as_default_reference(self):
        image_data = b"\xff\xd8\xffreference"
        body, content_type = self.multipart("默认风格.jpg", image_data, "image/jpeg")
        status, uploaded = self.request("/api/references/upload", body, {"Content-Type": content_type}, "POST")
        reference_id = uploaded["items"][0]["id"]
        status, run = self.request(
            "/api/runs",
            json.dumps({
                "request": "设计一个社区任务活动，包含任务和奖励。",
                "reference_ids": [reference_id],
            }).encode(),
            {"Content-Type": "application/json"},
            "POST",
        )
        self.assertEqual(status, 200)
        self.assertIsNone(run["image_reference_id"])

        provider = CapturingImageProvider()
        self.pipeline.image_generator = provider
        status, generated = self.request(
            "/api/images/generate",
            json.dumps({"run_id": run["id"], "task_id": run["visual_tasks"][0]["id"]}).encode(),
            {"Content-Type": "application/json"},
            "POST",
        )
        self.assertEqual(status, 201)
        self.assertEqual(provider.reference_image, (image_data, "image/jpeg"))
        self.assertEqual(generated["reference_image_id"], reference_id)

    def test_run_level_image_reference_rejects_document(self):
        body, content_type = self.multipart("规则.txt", "规则正文".encode())
        status, uploaded = self.request("/api/references/upload", body, {"Content-Type": content_type}, "POST")
        self.assertEqual(status, 201)
        status, run = self.request(
            "/api/runs",
            json.dumps({"request": "设计一个社区任务活动。"}).encode(),
            {"Content-Type": "application/json"},
            "POST",
        )
        status, result = self.request(
            f"/api/runs/{run['id']}/image-reference",
            json.dumps({"reference_id": uploaded["items"][0]["id"]}).encode(),
            {"Content-Type": "application/json"},
            "PUT",
        )
        self.assertEqual(status, 400)
        self.assertIn("必须选择图片", result["error"])


if __name__ == "__main__":
    unittest.main()
