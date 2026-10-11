"""Build screening_universe.yaml from BMV test results.
Uses only tickers confirmed working (no .MX suffix, exact BMV symbols).
"""
import json

CURRENCY_MAP = {
    "ESTADOS UNIDOS": "USD",
    "MEXICO": "MXN",
    "CANADA": "CAD",
    "REINO UNIDO": "GBP",
    "ALEMANIA": "EUR",
    "FRANCIA": "EUR",
    "ESPANA": "EUR",
    "ITALIA": "EUR",
    "HOLANDA": "EUR",
    "SUIZA": "CHF",
    "JAPON": "JPY",
    "AUSTRALIA": "AUD",
    "BRASIL": "BRL",
    "CHINA": "USD",
    "INDIA": "INR",
    "SUECIA": "SEK",
    "DINAMARCA": "DKK",
    "NORUEGA": "NOK",
    "ISLAS CAYMAN": "USD",
    "BERMUDA": "USD",
    "IRLANDA": "EUR",
    "LUXEMBURGO": "EUR",
    "ISRAEL": "ILS",
    "ARGENTINA": "USD",
    "SUDAFRICA": "ZAR",
    "TAIWAN": "TWD",
    "COREA (SUR)": "KRW",
    "SINGAPUR": "SGD",
    "CHILE": "CLP",
    "PERU": "USD",
    "COLOMBIA": "USD",
    "BELGICA": "EUR",
    "AUSTRIA": "EUR",
    "PORTUGAL": "EUR",
    "GRECIA": "EUR",
    "FINLANDIA": "EUR",
    "KAZAJISTAN": "USD",
    "PANAMA": "USD",
    "PUERTO RICO": "USD",
    "ISLAS VIRGENES (REINO UNIDO)": "USD",
    "ISLAS MARSHALL": "USD",
    "GUERNSEY": "GBP",
    "JERSEY": "GBP",
}

BENCHMARK_MAP = {
    "ESTADOS UNIDOS": "SPY",
    "MEXICO": "EWW",
    "CANADA": "EWC",
    "BRASIL": "EWZ",
    "CHINA": "FXI",
    "JAPON": "EWJ",
    "REINO UNIDO": "EWU",
    "ALEMANIA": "EWG",
    "FRANCIA": "EWQ",
    "SUIZA": "EWL",
    "AUSTRALIA": "EWA",
    "INDIA": "INDA",
    "COREA (SUR)": "EWY",
    "TAIWAN": "EWT",
}

EXCHANGE_MAP = {
    "NEW YORK STOCK EXCHANGE": "NYSE",
    "NASDAQ": "NASDAQ",
    "BMV": "BMV",
    "LONDON STOCK EXCHANGE": "LSE",
    "TOKIO STOCK EXCHANGE": "TSE",
    "XETRA": "XETRA",
    "FRANKFURT STOCK EXCHANGE": "FRA",
    "SIX SWISS EXCHANGE": "SIX",
    "HONG KONG STOCK EXCHANGE": "HKEX",
}


def main() -> None:
    with open("scripts/bmv_test_results.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    working = data["working"]

    # Sort by country then symbol
    def sort_key(item):
        sym, meta = item
        country = meta.get("country", "ZZZ")
        # Normalize unicode
        country = country.replace("\u00f1", "n").replace("\u00e9", "e").replace("\u00f3", "o").replace("\u00e1", "a").replace("\u00fa", "u").replace("\u00ed", "i")
        return (country, sym)

    sorted_tickers = sorted(working.items(), key=sort_key)

    lines = []
    lines.append("# SCREENING tier — all BMV-listed tickers (SIC + native) that can fetch data via yfinance.")
    lines.append("# Symbols are EXACTLY as listed on BMV (no .MX suffix).")
    lines.append("# Generated from infoDownload.xls + infoDownload (1).xls — only tickers with confirmed market data.")
    lines.append(f"# Total: {len(sorted_tickers)} tickers")
    lines.append("")
    lines.append("tickers:")

    current_country = None
    for sym, meta in sorted_tickers:
        country = meta.get("country", "UNKNOWN")
        name = meta.get("name", sym)
        exchange_raw = meta.get("exchange", "BMV")
        exchange = EXCHANGE_MAP.get(exchange_raw, exchange_raw)
        currency = CURRENCY_MAP.get(country, "USD")
        benchmark = BENCHMARK_MAP.get(country, "SPY")

        # Clean name for YAML
        name_clean = name.replace('"', "'").replace("\\", "").strip()
        if len(name_clean) > 60:
            name_clean = name_clean[:57] + "..."

        # Add country header
        if country != current_country:
            current_country = country
            lines.append(f"  # --- {country} ({currency}, benchmark {benchmark}) ---")

        # Truncate long names
        display_name = name_clean if len(name_clean) <= 40 else name_clean[:37] + "..."

        lines.append(
            f'  - {{ symbol: {sym}, name: "{display_name}", exchange: {exchange}, currency: {currency}, sector: Equity, benchmark: {benchmark}, enabled: true }}'
        )

    with open("config/screening_universe.yaml", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"Wrote {len(sorted_tickers)} tickers to config/screening_universe.yaml")

    # Stats
    countries: dict[str, int] = {}
    for _, meta in sorted_tickers:
        c = meta.get("country", "UNKNOWN")
        countries[c] = countries.get(c, 0) + 1
    print("\nBy country:")
    for c, n in sorted(countries.items(), key=lambda x: -x[1]):
        print(f"  {c}: {n}")


if __name__ == "__main__":
    main()