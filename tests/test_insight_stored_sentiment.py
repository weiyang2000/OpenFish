from InsightEngine.agent import DeepSearchAgent
from InsightEngine.tools.search import QueryResult


def test_stored_sentiment_summary_uses_query_result_fields():
    summary = DeepSearchAgent._stored_sentiment_summary(
        [
            QueryResult(
                platform="weibo",
                content_type="comment",
                title_or_content="支持这个服务",
                sentiment_label="positive",
                sentiment_score=0.9,
                source_table="weibo_note_comment",
            ),
            QueryResult(
                platform="weibo",
                content_type="comment",
                title_or_content="还没有分析",
                sentiment_label="unknown",
                sentiment_score=None,
                source_table="weibo_note_comment",
            ),
        ]
    )

    assert summary["source"] == "stored_crawler_fields"
    assert summary["total_analyzed"] == 1
    assert summary["success_rate"] == "1/2"
    assert summary["sentiment_distribution"]["positive"] == 1
    assert summary["sentiment_distribution"]["unknown"] == 1
