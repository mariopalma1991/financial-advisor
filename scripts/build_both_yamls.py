"""Build screening_universe.yaml (all 2,079) and curated_universe.yaml (news + fundamentals).
Reads ISIN validation progress + news/fundamentals coverage data.
"""
import json
import os

CURRENCY_MAP = {
    "ESTADOS UNIDOS": "USD", "MEXICO": "MXN", "CANADA": "CAD",
    "REINO UNIDO": "GBP", "ALEMANIA": "EUR", "FRANCIA": "EUR",
    "ESPANA": "EUR", "ITALIA": "EUR", "HOLANDA": "EUR", "SUIZA": "CHF",
    "JAPON": "JPY", "AUSTRALIA": "AUD", "BRASIL": "BRL",
    "CHINA": "USD", "INDIA": "INR", "SUECIA": "SEK", "DINAMARCA": "DKK",
    "NORUEGA": "NOK", "ISLAS CAYMAN": "USD", "BERMUDA": "USD",
    "IRLANDA": "EUR", "LUXEMBURGO": "EUR", "ISRAEL": "ILS",
    "ARGENTINA": "USD", "SUDAFRICA": "ZAR", "TAIWAN": "TWD",
    "COREA (SUR)": "KRW", "SINGAPUR": "SGD", "CHILE": "CLP",
    "PERU": "USD", "COLOMBIA": "USD", "BELGICA": "EUR",
    "AUSTRIA": "EUR", "PORTUGAL": "EUR", "GRECIA": "EUR",
    "FINLANDIA": "EUR", "KAZAJISTAN": "USD", "PANAMA": "USD",
    "PUERTO RICO": "USD", "ISLAS VIRGENES (REINO UNIDO)": "USD",
    "ISLAS MARSHALL": "USD", "GUERNSEY": "GBP", "JERSEY": "GBP",
    "HONG KONG": "HKD", "CHIPRE": "EUR", "POLONIA": "PLN",
    "MALASIA": "MYR", "ISLANDIA": "ISK", "TURQUIA": "TRY",
    "NUEVA ZELANDA": "NZD",
}

BENCHMARK_MAP = {
    "ESTADOS UNIDOS": "SPY", "MEXICO": "EWW", "CANADA": "EWC",
    "BRASIL": "EWZ", "CHINA": "FXI", "JAPON": "EWJ",
    "REINO UNIDO": "EWU", "ALEMANIA": "EWG", "FRANCIA": "EWQ",
    "SUIZA": "EWL", "AUSTRALIA": "EWA", "INDIA": "INDA",
    "COREA (SUR)": "EWY", "TAIWAN": "EWT",
}

EXCHANGE_MAP = {
    "NEW YORK STOCK EXCHANGE": "NYSE", "NASDAQ": "NASDAQ", "BMV": "BMV",
    "LONDON STOCK EXCHANGE": "LSE", "TOKIO STOCK EXCHANGE": "TSE",
    "XETRA": "XETRA", "FRANKFURT STOCK EXCHANGE": "FRA",
    "SIX SWISS EXCHANGE": "SIX", "HONG KONG STOCK EXCHANGE": "HKEX",
}


def _clean_name(name: str, max_len: int = 55) -> str:
    n = name.replace('"', "'").replace("\\", "").strip()
    if len(n) > max_len:
        n = n[:max_len - 3] + "..."
    return n


def _build_yaml_lines(tickers: dict, title: str, description: str) -> list[str]:
    """Build YAML lines for a set of tickers."""
    lines = []
    lines.append(f"# {title}")
    lines.append(f"# {description}")
    lines.append(f"# Total: {len(tickers)} tickers")
    lines.append("")
    lines.append("tickers:")

    # Sort by country then symbol
    def sort_key(item):
        _, meta = item
        return (meta.get("country", "ZZZ"), meta.get("symbol", ""))

    sorted_tickers = sorted(tickers.items(), key=sort_key)

    current_country = None
    for isin, meta in sorted_tickers:
        sym = meta.get("symbol", "")
        country = meta.get("country", "UNKNOWN")
        name = meta.get("name", sym)
        exchange_raw = meta.get("exchange", "BMV")

        exchange = EXCHANGE_MAP.get(exchange_raw, exchange_raw)
        currency = CURRENCY_MAP.get(country, "USD")
        benchmark = BENCHMARK_MAP.get(country, "SPY")
        name_clean = _clean_name(name)

        if country != current_country:
            current_country = country
            lines.append(f"  # --- {country} ({currency}, benchmark {benchmark}) ---")

        lines.append(
            f'  - {{ symbol: "{sym}", name: "{name_clean}", exchange: {exchange}, currency: {currency}, sector: Equity, benchmark: {benchmark}, enabled: true }}'
        )

    return lines


def main() -> None:
    # Load ISIN validation data
    with open("scripts/isin_validation_progress.json", "r", encoding="utf-8") as f:
        progress = json.load(f)

    all_tested = progress["tested"]
    working = {isin: meta for isin, meta in all_tested.items() if meta.get("ok")}
    print(f"Working tickers: {len(working)}")

    # Load news + fundamentals coverage
    with open("scripts/news_coverage.json", "r", encoding="utf-8") as f:
        news_data = json.load(f)
    with open("scripts/fundamentals_coverage.json", "r", encoding="utf-8") as f:
        fund_data = json.load(f)

    curated_symbols = set(fund_data["with_news_and_fundamentals"])
    print(f"Curated (news + fundamentals): {len(curated_symbols)}")

    # Build screening universe (all working)
    screening_lines = _build_yaml_lines(
        working,
        "SCREENING UNIVERSE",
        "All BMV-listed tickers (SIC) with confirmed market data via yfinance (ISIN). "
        "Symbols are EXACTLY as listed on BMV (no .MX suffix). "
        "Source: infoDownload (1).xls. "
        f"{len(working)} tickers — broad universe for screening."
    )

    with open("config/screening_universe.yaml", "w", encoding="utf-8") as f:
        f.write("\n".join(screening_lines) + "\n")
    print(f"Wrote config/screening_universe.yaml ({len(working)} tickers)")

    # Build curated universe (news + fundamentals)
    curated = {isin: meta for isin, meta in working.items() if meta["symbol"] in curated_symbols}
    curated_lines = _build_yaml_lines(
        curated,
        "CURATED UNIVERSE",
        "Tickers with market data + news articles + fundamentals. "
        "This is the analysis-tier universe — used for deep research and feed building. "
        "Symbols are EXACTLY as listed on BMV (no .MX suffix). "
        f"{len(curated)} tickers."
    )

    with open("config/curated_universe.yaml", "w", encoding="utf-8") as f:
        f.write("\n".join(curated_lines) + "\n")
    print(f"Wrote config/curated_universe.yaml ({len(curated)} tickers)")

    # Stats
    print(f"\nScreening universe: {len(working)} tickers")
    print(f"Curated universe: {len(curated)} tickers")
    print(f"Curated symbols: {sorted(curated_symbols)}")


if __name__ == "__main__":
    main()