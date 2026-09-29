import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_planner.adapters import OpenAICompatibleImageGenerationProvider, OpenAICompatibleLLMProvider
from ai_planner.config import ModelConfigStore
from ai_planner.pipeline import PlannerPipeline


class FakeImageProvider:
    def generate(self, prompt):
        return b"\x89PNG\r\n\x1a\nmock", "image/png", prompt + " refined"


class FakeHttpResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class ModelConfigTests(unittest.TestCase):
    def test_text_and_image_settings_persist_and_mask_api_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline = PlannerPipeline(root)
            saved = pipeline.update_model_config({
                "text": {
                    "provider": "openai_compatible",
                    "model": "demo-text",
                    "base_url": "https://example.test/v1",
                    "api_key": "text-secret",
                    "temperature": 0.7,
                    "max_tokens": 2048,
                    "timeout_seconds": 30,
                },
                "image": {
                    "provider": "openai_compatible",
                    "model": "demo-image",
                    "base_url": "https://images.example.test/v1",
                    "api_key": "image-secret",
                    "size": "1536x1024",
                    "quality": "high",
                    "timeout_seconds": 90,
                },
            })
            self.assertEqual(saved["text"]["model"], "demo-text")
            self.assertEqual(saved["image"]["model"], "demo-image")
            self.assertTrue(saved["text"]["api_key_configured"])
            self.assertTrue(saved["image"]["api_key_configured"])
            self.assertNotIn("secret", json.dumps(saved))
            self.assertIsInstance(pipeline.llm, OpenAICompatibleLLMProvider)
            self.assertIsInstance(pipeline.image_generator, OpenAICompatibleImageGenerationProvider)

            reloaded = PlannerPipeline(root).get_model_config()
            self.assertEqual(reloaded["text"]["model"], "demo-text")
            self.assertEqual(reloaded["image"]["size"], "1536x1024")

    def test_legacy_flat_text_config_is_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / ".ai-planner" / "model-config.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"provider": "local", "model": "legacy-local"}), encoding="utf-8")
            config = ModelConfigStore(root).load()
            self.assertEqual(config["text"]["model"], "legacy-local")
            self.assertEqual(config["image"]["provider"], "disabled")

    def test_blank_keys_are_preserved_and_clear_flags_remove_them(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = PlannerPipeline(Path(directory))
            pipeline.update_model_config({
                "text": {"provider": "openai_compatible", "model": "text", "api_key": "text-secret"},
                "image": {"provider": "openai_compatible", "model": "image", "api_key": "image-secret"},
            })
            pipeline.update_model_config({"text": {"provider": "openai_compatible", "model": "text"}})
            self.assertTrue(pipeline.get_model_config()["text"]["api_key_configured"])
            self.assertTrue(pipeline.get_model_config()["image"]["api_key_configured"])
            pipeline.update_model_config({
                "text": {"provider": "local", "model": "local-rule-v1", "clear_api_key": True},
                "image": {"provider": "disabled", "model": "gpt-image-1", "clear_api_key": True},
            })
            public = pipeline.get_model_config()
            self.assertFalse(public["text"]["api_key_configured"])
            self.assertFalse(public["image"]["api_key_configured"])

    def test_generated_image_is_saved_and_linked_to_visual_task(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = PlannerPipeline(Path(directory))
            run = pipeline.run("设计一个包含任务、奖励和结算页面的周末挑战活动。")
            pipeline.image_generator = FakeImageProvider()
            result = pipeline.generate_image(run.id, run.visual_tasks[0].id)
            self.assertTrue((pipeline.images_dir / Path(result["url"]).name).is_file())
            loaded = pipeline.store.load(run.id)
            self.assertEqual(loaded.generated_images[0]["task_id"], run.visual_tasks[0].id)
            self.assertEqual(loaded.visual_tasks[0].status, "generated")

    def test_gpt_image_request_parses_base64_without_legacy_response_format(self):
        provider = OpenAICompatibleImageGenerationProvider({
            "provider": "openai_compatible",
            "model": "gpt-image-1",
            "base_url": "https://example.test/v1",
            "api_key": "secret",
            "size": "1024x1024",
            "quality": "high",
            "timeout_seconds": 30,
        })
        encoded = base64.b64encode(b"\x89PNG\r\n\x1a\nmock").decode("ascii")
        with patch("urllib.request.urlopen", return_value=FakeHttpResponse({"data": [{"b64_json": encoded}]})) as request:
            image, mime_type, _ = provider.generate("draw a game screen")
        body = json.loads(request.call_args.args[0].data)
        self.assertNotIn("response_format", body)
        self.assertEqual(mime_type, "image/png")
        self.assertTrue(image.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
