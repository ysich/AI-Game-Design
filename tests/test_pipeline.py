import json
import tempfile
import unittest
from pathlib import Path

from ai_planner.adapters import LocalKnowledgeStore
from ai_planner.models import Route, Stage, to_dict
from ai_planner.pipeline import PlannerPipeline


class PlannerPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.pipeline = PlannerPipeline(Path(self.temp_dir.name))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_short_request_stops_for_clarification(self):
        run = self.pipeline.run("做活动")
        self.assertEqual(run.stage, Stage.NEEDS_CLARIFICATION)
        self.assertEqual(run.route.route, Route.CLARIFY)
        self.assertTrue(run.route.clarification_questions)

    def test_full_pipeline_persists_document_review_and_visual_tasks(self):
        run = self.pipeline.run("做一个为期 7 天的回流玩家签到活动，提升回流率并给出界面和奖励方案。")
        self.assertEqual(run.stage, Stage.EXPORTED)
        self.assertIsNotNone(run.document)
        self.assertEqual(len(run.document.chapters), 10)
        self.assertGreaterEqual(len(run.visual_tasks), 3)
        self.assertIn("markdown", run.exports)
        self.assertIn("document_path", run.exports)
        document_record = self.pipeline.store.document_library.get(run.document.id)
        self.assertEqual(document_record["version"], 1)
        self.assertIn("项目概述", document_record["content"])
        loaded = self.pipeline.store.load(run.id)
        self.assertEqual(loaded.stage, Stage.EXPORTED)
        self.assertTrue(any(item.rule_id == "reward-numeric" for item in loaded.findings))

    def test_partial_revision_updates_target_chapter_and_version(self):
        run = self.pipeline.run("设计一个周末挑战活动，包含任务和奖励。")
        original = run.document.version
        revised = self.pipeline.revise(run.id, "把奖励章节改成需要服务端校验并补充排行榜奖励数量。")
        self.assertEqual(revised.stage, Stage.EXPORTED)
        self.assertEqual(revised.document.version, original + 1)
        rewards = revised.document.chapter("rewards")
        self.assertEqual(rewards.status, "revised")
        self.assertIn("服务端校验", rewards.content)
        self.assertEqual(self.pipeline.store.document_library.get(run.document.id, version=1)["requested_version"], 1)
        self.assertEqual(self.pipeline.store.document_library.get(run.document.id, version=2)["requested_version"], 2)

    def test_document_library_searches_markdown_content(self):
        run = self.pipeline.run("设计一个社区任务活动，要求有分享和奖励。")
        matches = self.pipeline.store.document_library.search("社区任务")
        self.assertTrue(any(item["id"] == run.document.id for item in matches))

    def test_knowledge_is_loaded_from_local_markdown(self):
        store = LocalKnowledgeStore(root=Path("Doc/AI策划案管线/知识库"))
        self.assertGreaterEqual(len(store.items), 5)
        self.assertTrue(any(item.source.startswith("md:") for item in store.search("奖励")))

    def test_snapshot_is_json_serializable(self):
        run = self.pipeline.run("设计一个社区任务活动，要求有分享和奖励。")
        payload = json.dumps(to_dict(run), ensure_ascii=False)
        self.assertIn(run.id, payload)

    def test_image_only_route_still_creates_visual_tasks(self):
        run = self.pipeline.run("只生成这个活动的主界面和结算界面")
        self.assertEqual(run.route.route, Route.IMAGE_ONLY)
        self.assertEqual(run.stage, Stage.VISUALIZED)
        self.assertEqual(len(run.visual_tasks), 3)


if __name__ == "__main__":
    unittest.main()
