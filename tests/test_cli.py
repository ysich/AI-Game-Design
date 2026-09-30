import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from ai_planner.cli import main


class AgentCliTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def invoke_json(self, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            main([*args, "--root", str(self.root)])
        return json.loads(output.getvalue())

    def test_generate_visual_and_coding_commands_share_run_snapshot(self):
        run = self.invoke_json("generate", "设计一个包含任务、奖励和结算页面的周末挑战活动。")
        self.assertEqual(run["stage"], "exported")

        visual = self.invoke_json("visual", run["id"])
        self.assertEqual(visual["run_id"], run["id"])
        self.assertGreaterEqual(len(visual["visual_tasks"]), 3)

        coding_path = self.root / "coding-case.md"
        result = self.invoke_json("coding", run["id"], "--output", str(coding_path))
        self.assertEqual(result["run_id"], run["id"])
        self.assertIn("Coding 案", coding_path.read_text(encoding="utf-8"))

    def test_reference_upload_can_feed_generation(self):
        reference_path = self.root / "奖励约束.txt"
        reference_path.write_text("每日奖励最多领取 3 次", encoding="utf-8")
        uploaded = self.invoke_json("reference", "upload", str(reference_path))

        run = self.invoke_json(
            "generate",
            "设计一个社区任务活动，包含任务、分享和奖励。",
            "--reference-id",
            uploaded[0]["id"],
        )
        self.assertEqual(run["reference_ids"], [uploaded[0]["id"]])
        self.assertTrue(any("最多领取" in item["content"] for item in run["context"]))

    def test_document_update_creates_version(self):
        run = self.invoke_json("generate", "设计一个社区任务活动，包含任务和奖励。")
        document_id = run["document"]["id"]
        updated = self.invoke_json(
            "document",
            "update",
            document_id,
            "--content",
            "# 修改后的策划案\n\n新正文\n",
            "--expected-version",
            "1",
        )
        self.assertEqual(updated["version"], 2)
        self.assertIn("新正文", updated["content"])


if __name__ == "__main__":
    unittest.main()
