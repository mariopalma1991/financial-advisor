"""Check which ISIN-validated tickers have news coverage in Supabase."""
import json
import sys
sys.path.insert(0, ".")

from src.storage.supabase_client import get_client


def main() -> None:
    # Load OK symbols
    with open("scripts/isin_validation_progress.json", "r", encoding="utf-8") as f:
        d = json.load(f)

    ok_symbols = set(v["symbol"] for v in d["tested"].values() if v.get("ok"))
    print(f"OK symbols (market data): {len(ok_symbols)}")

    client = get_client()

    # 1. Check news_articles (tickers is text[])
    news_tickers: set[str] = set()
    try:
        # Paginate through all rows
        offset = 0
        page_size = 1000
        while True:
            resp = client.table("news_articles").select("tickers").range(offset, offset + page_size - 1).execute()
            if not resp.data:
                break
            for row in resp.data:
                for t in row.get("tickers") or []:
                    news_tickers.add(t)
            if len(resp.data) < page_size:
                break
            offset += page_size
        print(f"news_articles: {len(news_tickers)} distinct tickers")
    except Exception as e:
        print(f"news_articles error: {e}")

    # 2. Check news_sentiment_daily
    sentiment_tickers: set[str] = set()
    try:
        offset = 0
        page_size = 1000
        while True:
            resp = client.table("news_sentiment_daily").select("ticker").range(offset, offset + page_size - 1).execute()
            if not resp.data:
                break
            for row in resp.data:
                sentiment_tickers.add(row["ticker"])
            if len(resp.data) < page_size:
                break
            offset += page_size
        print(f"news_sentiment_daily: {len(sentiment_tickers)} distinct tickers")
    except Exception as e:
        print(f"news_sentiment_daily error: {e}")

    # 3. Cross-reference
    all_news_tickers = news_tickers | sentiment_tickers
    with_news = ok_symbols & all_news_tickers
    without_news = ok_symbols - all_news_tickers

    print(f"\n=== RESULTS ===")
    print(f"OK symbols with market data: {len(ok_symbols)}")
    print(f"  With news: {len(with_news)}")
    print(f"  Without news: {len(without_news)}")

    # Save results
    result = {
        "with_news": sorted(with_news),
        "without_news": sorted(without_news),
        "total_ok": len(ok_symbols),
        "with_news_count": len(with_news),
        "without_news_count": len(without_news),
    }
    with open("scripts/news_coverage.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to scripts/news_coverage.json")


if __name__ == "__main__":
    main()