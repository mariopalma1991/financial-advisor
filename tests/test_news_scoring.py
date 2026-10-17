from datetime import UTC, datetime

from src.adapters.base import RawNewsArticle
from src.jobs import ingest_news
from src.transforms.news_scoring import article_text, is_about, pg_to_py
from src.transforms.ticker_aliases import patterns_for


def test_pg_word_boundaries_translate():
    assert pg_to_py(r"\mNVIDIA\M") == r"\bNVIDIA\b"


def test_is_about_uses_summary_and_ticker_rules():
    pats = patterns_for("NVDA", "NVIDIA Corp")
    assert is_about("Chip stocks rally. Nvidia beat estimates on data-center demand.", pats)
    assert is_about("Analysts like $NVDA into earnings", pats)
    assert not is_about("Warren Buffett adds to Coca-Cola stake", pats)
    # case-sensitive ticker rule: lowercase word is not the ticker
    pats_m = patterns_for("M", "Macy's Inc")
    assert not is_about("m is a letter", pats_m)


def test_article_text_joins_headline_and_summary():
    assert article_text("Up 5%", "Revenue beat.") == "Up 5%. Revenue beat."
    assert article_text("Up 5%", None) == "Up 5%"
    assert article_text(None, "Only summary") == "Only summary"


def test_enrich_sets_payload(monkeypatch):
    monkeypatch.setattr(ingest_news, "finbert_scores", lambda texts: [0.8 for _ in texts])
    a = RawNewsArticle(source="finnhub", article_id="1", published_at=datetime(2026, 10, 16, tzinfo=UTC),
                       headline="Market wrap", body="Apple shares rose after the iPhone launch.",
                       payload={"related": "AAPL"})
    b = RawNewsArticle(source="finnhub", article_id="2", published_at=datetime(2026, 10, 16, tzinfo=UTC),
                       headline="Oil climbs", body=None, payload={"related": "AAPL"})
    stats = ingest_news.enrich([a, b], {"AAPL": patterns_for("AAPL", "Apple Inc", ["Apple", "iPhone"])})
    assert a.payload["about"] is True and b.payload["about"] is False
    assert a.payload["sentiment"] == 0.8 and a.payload["sentiment_model"] == "finbert"
    assert stats == {"articles": 2, "with_summary": 0.5, "about": 0.5, "avg_sentiment": 0.8}
