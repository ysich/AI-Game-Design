import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from ai_planner.models import Chapter, PlanningDocument, to_dict
from ai_planner.pipeline import PlannerPipeline
from ai_planner.server import PlannerRequestHandler
from ai_planner.storage import MarkdownDocumentLibrary


class MarkdownDocumentLibraryEditingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory(dir=Path.cwd())
        self.library = MarkdownDocumentLibrary(Path(self.temp_dir.name) / "documents")
        self.document = PlanningDocument(
            id="doc-editing",
            title="初始标题",
            activity_type="签到活动",
            chapters=[Chapter("overview", "项目概述", "初始正文")],
        )
        self.library.publish(self.document, "# 初始标题\n\n初始正文\n", "run-editing")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_update_creates_new_version_and_preserves_history(self):
        updated = self.library.update(
            self.document.id,
            title="修改后标题",
            activity_type="回流签到活动",
            content="# 修改后标题\n\n修改后正文\n",
            expected_version=1,
        )

        self.assertEqual(updated["version"], 2)
        self.assertEqual(updated["title"], "修改后标题")
        self.assertIn("修改后正文", updated["content"])
        self.assertIn("初始正文", self.library.get(self.document.id, version=1)["content"])
        self.assertIn("修改后正文", self.library.get(self.document.id, version=2)["content"])

    def test_update_rejects_stale_version(self):
        with self.assertRaisesRegex(ValueError, "已更新"):
            self.library.update(
                self.document.id,
                title="修改后标题",
                activity_type="签到活动",
                content="正文",
                expected_version=0,
            )


class JsonRunStoreEditingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory(dir=Path.cwd())
        self.pipeline = PlannerPipeline(Path(self.temp_dir.name))
        self.run = self.pipeline.run("设计一个社区任务活动，要求有分享和奖励。")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_update_validates_and_saves_run_snapshot(self):
        payload = to_dict(self.run)
        payload["request"] = "编辑后的社区任务需求"

        updated = self.pipeline.store.update(self.run.id, payload, self.run.updated_at)

        self.assertEqual(updated.request, "编辑后的社区任务需求")
        self.assertEqual(self.pipeline.store.load(self.run.id).request, "编辑后的社区任务需求")

    def test_update_rejects_changed_id_and_invalid_schema(self):
        payload = to_dict(self.run)
        payload["id"] = "run-other"
        with self.assertRaisesRegex(ValueError, "ID 不允许修改"):
            self.pipeline.store.update(self.run.id, payload)

        payload = to_dict(self.run)
        payload["stage"] = "unknown"
        with self.assertRaisesRegex(ValueError, "结构无效"):
            self.pipeline.store.update(self.run.id, payload)


class EditingApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory(dir=Path.cwd())
        pipeline = PlannerPipeline(Path(self.temp_dir.name))
        self.run = pipeline.run("设计一个社区任务活动，要求有分享和奖励。")

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
        self.temp_dir.cleanup()

    def put(self, path, payload):
        request = Request(
            self.base_url + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_put_updates_document_and_run_through_http_api(self):
        document_id = self.run.document.id
        status, document = self.put(
            f"/api/documents/{document_id}",
            {
                "title": "接口编辑后的标题",
                "activity_type": "社区活动",
                "content": "# 接口编辑后的标题\n\n正文\n",
                "expected_version": 1,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(document["version"], 2)

        record = to_dict(self.run)
        record["request"] = "接口编辑后的需求"
        status, updated_run = self.put(
            f"/api/runs/{self.run.id}",
            {"record": record, "expected_updated_at": self.run.updated_at},
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated_run["request"], "接口编辑后的需求")


if __name__ == "__main__":
    unittest.main()
