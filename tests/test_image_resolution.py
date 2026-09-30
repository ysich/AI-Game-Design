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

    def generate(self, prompt, size=None, reference_image=None):
        self.calls.append((prompt, size, reference_image))
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

    def test_supplement_and_reference_image_are_forwarded_and_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = PlannerPipeline(Path(directory))
            run = pipeline.run("设计一个包含任务、奖励和结算页面的周末挑战活动。")
            provider = RecordingImageProvider()
            pipeline.image_generator = provider
            reference_bytes = b"\x89PNG\r\n\x1a\nreference"
            encoded = base64.b64encode(reference_bytes).decode("ascii")

            result = pipeline.generate_image(
                run.id,
                run.visual_tasks[0].id,
                prompt="基础界面提示词",
                supplement_prompt="使用更明亮的蓝色强调按钮",
                reference_image=f"data:image/png;base64,{encoded}",
                reference_image_name="参考界面.png",
            )

            self.assertEqual(provider.calls[0][0], "基础界面提示词\n补充要求：使用更明亮的蓝色强调按钮")
            self.assertEqual(provider.calls[0][2], (reference_bytes, "image/png"))
            self.assertEqual(result["supplement_prompt"], "使用更明亮的蓝色强调按钮")
            self.assertEqual(result["reference_image_name"], "参考界面.png")
            self.assertTrue((pipeline.images_dir / Path(result["reference_image_url"]).name).is_file())

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

    def test_provider_uses_images_edit_endpoint_for_reference_image(self):
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
        reference = b"\x89PNG\r\n\x1a\nreference"
        with patch("urllib.request.urlopen", return_value=FakeHttpResponse({"data": [{"b64_json": encoded}]})) as request:
            provider.generate("edit the referenced screen", reference_image=(reference, "image/png"))

        http_request = request.call_args.args[0]
        self.assertTrue(http_request.full_url.endswith("/images/edits"))
        body = http_request.data.decode("latin-1")
        self.assertIn('name="image"; filename="reference.png"', body)
        self.assertIn("edit the referenced screen", body)
        self.assertIn("Content-Type: image/png", body)

    def test_provider_sends_multiple_reference_images_as_array_fields(self):
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
        references = [
            (b"\x89PNG\r\n\x1a\nfirst", "image/png"),
            (b"\xff\xd8\xffsecond", "image/jpeg"),
        ]
        with patch("urllib.request.urlopen", return_value=FakeHttpResponse({"data": [{"b64_json": encoded}]})) as request:
            provider.generate("merge the referenced screens", reference_image=references)

        body = request.call_args.args[0].data.decode("latin-1")
        self.assertEqual(body.count('name="image[]"'), 2)
        self.assertIn('filename="reference-1.png"', body)
        self.assertIn('filename="reference-2.jpg"', body)


if __name__ == "__main__":
    unittest.main()
