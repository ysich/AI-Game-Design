import tempfile
import unittest
from pathlib import Path

from ai_planner.adapters import OpenAICompatibleLLMProvider
from ai_planner.pipeline import PlannerPipeline


class ModelConfigTests(unittest.TestCase):
    def test_model_settings_persist_and_mask_api_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline = PlannerPipeline(root)
            saved = pipeline.update_model_config({
                "provider": "openai_compatible",
                "model": "demo-model",
                "base_url": "https://example.test/v1",
                "api_key": "secret-value",
                "temperature": 0.7,
                "max_tokens": 2048,
                "timeout_seconds": 30,
            })
            self.assertEqual(saved["model"], "demo-model")
            self.assertTrue(saved["api_key_configured"])
            self.assertNotIn("secret-value", str(saved))
            self.assertIsInstance(pipeline.llm, OpenAICompatibleLLMProvider)

            reloaded = PlannerPipeline(root)
            self.assertEqual(reloaded.get_model_config()["model"], "demo-model")
            self.assertTrue(reloaded.get_model_config()["api_key_configured"])

    def test_blank_key_keeps_saved_key_and_clear_flag_removes_it(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = PlannerPipeline(Path(directory))
            pipeline.update_model_config({"provider": "openai_compatible", "model": "demo", "api_key": "secret"})
            pipeline.update_model_config({"provider": "openai_compatible", "model": "demo", "api_key": ""})
            self.assertTrue(pipeline.get_model_config()["api_key_configured"])
            pipeline.update_model_config({"provider": "local", "model": "local-rule-v1", "clear_api_key": True})
            self.assertFalse(pipeline.get_model_config()["api_key_configured"])


if __name__ == "__main__":
    unittest.main()
