import unittest

from InsightEngine.utils.text_processing import (
    format_search_results_for_prompt as format_insight_results,
)
from MediaEngine.utils.text_processing import (
    format_search_results_for_prompt as format_media_results,
)
from QueryEngine.utils.text_processing import (
    format_search_results_for_prompt as format_query_results,
)


class SourceReferenceFormattingTestCase(unittest.TestCase):
    """Regression tests for safe source references in engine prompts."""

    def _assert_formatter_common_behavior(self, formatter):
        normal = formatter(
            [
                {
                    "title": "标题 [含括号]",
                    "url": "https://example.com/news?id=1",
                    "content": "这里是搜索结果正文。",
                }
            ]
        )
        self.assertEqual(len(normal), 1)
        self.assertIn("[标题 \\[含括号\\]](https://example.com/news?id=1)", normal[0])
        self.assertIn("这里是搜索结果正文。", normal[0])

        no_url = formatter(
            [
                {
                    "title": "无链接来源",
                    "content": "本地数据库内容。",
                    "platform": "weibo",
                    "source_table": "weibo_note",
                    "author": "作者A",
                    "published_date": "2026-06-10",
                }
            ]
        )
        self.assertIn("来源：平台:weibo，表:weibo_note，作者:作者A，时间:2026-06-10", no_url[0])
        self.assertNotIn("](javascript:", no_url[0])

        dangerous = formatter(
            [
                {
                    "title": "危险链接",
                    "url": "javascript:alert(1)",
                    "content": "危险链接应该降级。",
                    "platform": "xhs",
                }
            ]
        )
        self.assertIn("来源：平台:xhs", dangerous[0])
        self.assertNotIn("javascript:alert", dangerous[0])

    def test_query_engine_source_references_are_safe(self):
        self._assert_formatter_common_behavior(format_query_results)

    def test_media_engine_source_references_are_safe(self):
        self._assert_formatter_common_behavior(format_media_results)

    def test_insight_engine_source_references_are_safe_and_keep_sentiment(self):
        formatted = format_insight_results(
            [
                {
                    "title": "舆情样本",
                    "url": "https://example.com/post/1",
                    "content": "用户评论内容。",
                    "sentiment_label": "positive",
                    "sentiment_score": 0.82,
                }
            ]
        )
        self.assertIn("[舆情样本](https://example.com/post/1)", formatted[0])
        self.assertIn("情绪: positive (0.82)", formatted[0])
        self._assert_formatter_common_behavior(format_insight_results)


if __name__ == "__main__":
    unittest.main()
