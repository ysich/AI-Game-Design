import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_planner.adapters import OpenAICompatibleImageGenerationProvider
from ai_planner.pipeline import PlannerPipeline


class RecordingImageProvider:
    def __init__(self):
        self.calls = []

    def generate(self, prompt, size=None):
        self.calls.append((prompt, size))
        return b"\x89PNG\r\n\x1a\nmock", "image/png", ""


class FakeHttpResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class ImageResolutionTests(unittest.TestCase):
    def test_generation_override_is_forwarded_and_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = PlannerPipeline(Path(directory))
            run = pipeline.run("设计一个包含任务、奖励和结算页面的周末挑战活动。")
            provider = RecordingImageProvider()
            pipeline.image_generator = provider

            result = pipeline.generate_image(run.id, run.visual_tasks[0].id, size="1536x1024")

            self.assertEqual(provider.calls[0][1], "1536x1024")
            self.assertEqual(result["size"], "1536x1024")
            self.assertEqual(pipeline.store.load(run.id).generated_images[0]["size"], "1536x1024")

    def test_generation_rejects_unknown_size(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = PlannerPipeline(Path(directory))
            run = pipeline.run("设计一个包含活动主界面的活动。")
            provider = RecordingImageProvider()
            pipeline.image_generator = provider

            with self.assertRaisesRegex(ValueError, "图片尺寸不受支持"):
                pipeline.generate_image(run.id, run.visual_tasks[0].id, size="1920x1080")
            self.assertEqual(provider.calls, [])

    def test_screen_ratio_sizes_are_valid_configuration_values(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = PlannerPipeline(Path(directory))
            for size in ("1792x768", "1536x864", "1365x1024", "1024x1365", "864x1536", "768x1792"):
                config = pipeline.update_model_config({"image": {"size": size}})
                self.assertEqual(config["image"]["size"], size)

    def test_provider_uses_per_request_size_over_default(self):
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
            provider.generate("draw a game screen", size="1024x1536")

        body = json.loads(request.call_args.args[0].data)
        self.assertEqual(body["size"], "1024x1536")


if __name__ == "__main__":
    unittest.main()
