import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_planner.adapters import BingSearchProvider, DuckDuckGoSearchProvider, JsonWebSearchProvider
from ai_planner.pipeline import PlannerPipeline


class FakeResponse:
    def __init__(self, payload, text=False):
        self.payload = payload
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.payload.encode("utf-8") if self.text else json.dumps(self.payload).encode("utf-8")


class WebSearchTests(unittest.TestCase):
    def test_bing_rss_results_are_normalized(self):
        rss = '''<?xml version="1.0" encoding="utf-8"?>
        <rss version="2.0"><channel><item><title>活动运营案例</title>
        <link>https://example.com/case</link><description>奖励与留存设计摘要</description>
        </item></channel></rss>'''
        provider = BingSearchProvider({"base_url": "https://search.test/search", "timeout_seconds": 5})
        with patch("urllib.request.urlopen", return_value=FakeResponse(rss, text=True)) as request:
            results = provider.search("活动运营", limit=3)
        self.assertEqual(results[0].title, "活动运营案例")
        self.assertEqual(results[0].content, "奖励与留存设计摘要")
        self.assertEqual(results[0].url, "https://example.com/case")
        self.assertEqual(results[0].source, "web:bing")
        self.assertIn("format=rss", request.call_args.args[0].full_url)

    def test_duckduckgo_results_are_normalized_with_source_urls(self):
        html = '''
        <div class="result">
          <a class="result__a" href="https://example.com/activity">签到活动案例</a>
          <a class="result__snippet" href="https://example.com/activity">连续签到和递进奖励的设计参考</a>
        </div>
        '''
        provider = DuckDuckGoSearchProvider({"base_url": "https://search.test/html/", "timeout_seconds": 5})
        with patch("urllib.request.urlopen", return_value=FakeResponse(html, text=True)) as request:
            results = provider.search("签到活动", limit=3)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "签到活动案例")
        self.assertEqual(results[0].content, "连续签到和递进奖励的设计参考")
        self.assertEqual(results[0].url, "https://example.com/activity")
        self.assertEqual(results[0].source, "web:duckduckgo")
        self.assertIn("q=%E7%AD%BE%E5%88%B0%E6%B4%BB%E5%8A%A8", request.call_args.args[0].full_url)

    def test_tavily_results_are_normalized_and_api_key_is_sent(self):
        provider = JsonWebSearchProvider({"base_url": "https://api.test/search", "api_key": "secret", "timeout_seconds": 5}, "tavily")
        payload = {"results": [{"title": "玩法趋势", "url": "https://example.com/trend", "content": "摘要"}]}
        with patch("urllib.request.urlopen", return_value=FakeResponse(payload)) as request:
            results = provider.search("玩法趋势", limit=2)
        self.assertEqual(results[0].source, "web:tavily")
        self.assertEqual(results[0].url, "https://example.com/trend")
        body = json.loads(request.call_args.args[0].data)
        self.assertEqual(body["api_key"], "secret")
        self.assertEqual(body["max_results"], 2)

    def test_pipeline_search_references_uses_injected_provider(self):
        class FakeProvider:
            def search(self, query, limit=5):
                from ai_planner.models import ContextSegment
                return [ContextSegment("web:test", query, "result", "web:test", url="https://example.com")]

        with tempfile.TemporaryDirectory() as directory:
            pipeline = PlannerPipeline(Path(directory), web_search=FakeProvider())
            results = pipeline.search_references("社区任务")
        self.assertEqual(results[0].url, "https://example.com")


if __name__ == "__main__":
    unittest.main()
