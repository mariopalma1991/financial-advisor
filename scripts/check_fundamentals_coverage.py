"""Check which ISIN-validated tickers have fundamentals in Supabase."""
import json
import sys
sys.path.insert(0, ".")

from src.storage.supabase_client import get_client


def main() -> None:
    with open("scripts/isin_validation_progress.json", "r", encoding="utf-8") as f:
        d = json.load(f)

    ok_symbols = set(v["symbol"] for v in d["tested"].values() if v.get("ok"))
    print(f"OK symbols (market data): {len(ok_symbols)}")

    client = get_client()

    # Get distinct tickers from fundamentals_quarterly
    fundamentals_tickers: set[str] = set()
    try:
        offset = 0
        page_size = 1000
        while True:
            resp = client.table("fundamentals_quarterly").select("ticker").range(offset, offset + page_size - 1).execute()
            if not resp.data:
                break
            for row in resp.data:
                fundamentals_tickers.add(row["ticker"])
            if len(resp.data) < page_size:
                break
            offset += page_size
        print(f"fundamentals_quarterly: {len(fundamentals_tickers)} distinct tickers")
    except Exception as e:
        print(f"fundamentals_quarterly error: {e}")

    # Load news coverage
    with open("scripts/news_coverage.json", "r", encoding="utf-8") as f:
        news_data = json.load(f)
    news_tickers = set(news_data["with_news"])

    # Intersections
    with_fundamentals = ok_symbols & fundamentals_tickers
    with_news_and_fundamentals = ok_symbols & news_tickers & fundamentals_tickers
    with_news_only = ok_symbols & news_tickers - fundamentals_tickers
    with_fundamentals_only = ok_symbols & fundamentals_tickers - news_tickers

    print(f"\n=== RESULTS ===")
    print(f"OK symbols (market data): {len(ok_symbols)}")
    print(f"  With fundamentals: {len(with_fundamentals)}")
    print(f"  With news: {len(news_tickers)}")
    print(f"  With BOTH news + fundamentals: {len(with_news_and_fundamentals)}")
    print(f"  News only (no fundamentals): {len(with_news_only)}")
    print(f"  Fundamentals only (no news): {len(with_fundamentals_only)}")

    # Save
    result = {
        "with_fundamentals": sorted(with_fundamentals),
        "with_news_and_fundamentals": sorted(with_news_and_fundamentals),
        "fundamentals_count": len(fundamentals_tickers),
        "with_fundamentals_count": len(with_fundamentals),
        "curated_count": len(with_news_and_fundamentals),
    }
    with open("scripts/fundamentals_coverage.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to scripts/fundamentals_coverage.json")


if __name__ == "__main__":
    main()