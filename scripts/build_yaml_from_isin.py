"""Build screening_universe.yaml from ISIN validation progress (resumable).
Reads the incremental progress file, so it works even while validation is still running.
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


def main() -> None:
    progress_file = "scripts/isin_validation_progress.json"

    if not os.path.exists(progress_file):
        print("No progress file found. Run validate_isin.py first.")
        return

    with open(progress_file, "r", encoding="utf-8") as f:
        progress = json.load(f)

    tested = progress["tested"]
    working = {isin: meta for isin, meta in tested.items() if meta.get("ok")}

    # Sort by country then symbol
    def sort_key(item):
        _, meta = item
        country = meta.get("country", "ZZZ")
        return (country, meta.get("symbol", ""))

    sorted_tickers = sorted(working.items(), key=sort_key)

    lines = []
    lines.append("# SCREENING tier — BMV-listed tickers validated via ISIN (yfinance).")
    lines.append("# Symbols are EXACTLY as listed on BMV (no .MX suffix).")
    lines.append("# Source: infoDownload (1).xls — only tickers with confirmed market data.")
    lines.append(f"# Total: {len(sorted_tickers)} tickers (from {len(tested)} tested so far)")
    lines.append("")
    lines.append("tickers:")

    current_country = None
    for isin, meta in sorted_tickers:
        sym = meta.get("symbol", "")
        country = meta.get("country", "UNKNOWN")
        name = meta.get("name", sym)
        exchange_raw = meta.get("exchange", "BMV")

        exchange = EXCHANGE_MAP.get(exchange_raw, exchange_raw)
        currency = CURRENCY_MAP.get(country, "USD")
        benchmark = BENCHMARK_MAP.get(country, "SPY")

        # Clean name
        name_clean = name.replace('"', "'").replace("\\", "").strip()
        if len(name_clean) > 55:
            name_clean = name_clean[:52] + "..."

        if country != current_country:
            current_country = country
            lines.append(f"  # --- {country} ({currency}, benchmark {benchmark}) ---")

        lines.append(
            f'  - {{ symbol: {sym}, name: "{name_clean}", exchange: {exchange}, currency: {currency}, sector: Equity, benchmark: {benchmark}, enabled: true }}'
        )

    with open("config/screening_universe.yaml", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # Stats
    countries: dict[str, int] = {}
    for _, meta in sorted_tickers:
        c = meta.get("country", "UNKNOWN")
        countries[c] = countries.get(c, 0) + 1

    print(f"Wrote {len(sorted_tickers)} tickers to config/screening_universe.yaml")
    print(f"(from {len(tested)} tested, {len(tested) - len(sorted_tickers)} failed)")
    print("\nBy country:")
    for c, n in sorted(countries.items(), key=lambda x: -x[1]):
        print(f"  {c}: {n}")


if __name__ == "__main__":
    main()