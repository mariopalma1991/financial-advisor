"""Cross-reference market data, news, and SEC filings coverage."""
import json

# Load with utf-8
with open("scripts/finnhub_news_coverage_progress.json", "r", encoding="utf-8") as f:
    n = json.load(f)
with open("scripts/finnhub_sec_coverage_progress.json", "r", encoding="utf-8") as f:
    s = json.load(f)
with open("scripts/isin_validation_progress.json", "r", encoding="utf-8") as f:
    i = json.load(f)

news_tickers = {k for k, v in n.items() if v is True}
sec_tickers = {k for k, v in s.items() if v is True}
market_tickers = {v["symbol"] for v in i["tested"].values() if v.get("ok")}

all_three = news_tickers & sec_tickers & market_tickers
news_sec = news_tickers & sec_tickers
news_market = news_tickers & market_tickers
sec_market = sec_tickers & market_tickers

print("=== BROADER UNIVERSE COVERAGE ===")
print(f"Total screening universe: 2079")
print(f"NA tickers (NYSE/NASDAQ/TSX): 1615")
print()
print(f"Market data (ISIN validated): {len(market_tickers)}")
print(f"Finnhub news:                 {len(news_tickers)} ({100*len(news_tickers)/1615:.1f}% of NA)")
print(f"Finnhub SEC filings:          {len(sec_tickers)} ({100*len(sec_tickers)/1615:.1f}% of NA)")
print()
print(f"News + SEC filings:           {len(news_sec)}")
print(f"News + Market data:           {len(news_market)}")
print(f"SEC + Market data:            {len(sec_market)}")
print(f"ALL THREE (mkt+news+sec):     {len(all_three)}")
print()
print(f"News only (no SEC):           {len(news_tickers - sec_tickers)}")
print(f"SEC only (no news):           {len(sec_tickers - news_tickers)}")
print(f"Neither news nor SEC:         {1615 - len(news_tickers | sec_tickers)}")

# Save
result = {
    "total_screening": 2079,
    "na_tickers": 1615,
    "market_data": len(market_tickers),
    "finnhub_news": len(news_tickers),
    "finnhub_sec": len(sec_tickers),
    "news_and_sec": len(news_sec),
    "news_and_market": len(news_market),
    "sec_and_market": len(sec_market),
    "all_three": len(all_three),
    "news_only": len(news_tickers - sec_tickers),
    "sec_only": len(sec_tickers - news_tickers),
    "neither": 1615 - len(news_tickers | sec_tickers),
}
with open("scripts/cross_coverage.json", "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2)
print(f"\nSaved to scripts/cross_coverage.json")