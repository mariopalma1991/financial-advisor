"""TEMP probe (removed before merge): 10-K Item 1A extraction and FINRA endpoints from Actions."""
import httpx

from src.adapters.sec_edgar import EdgarClient
from src.adapters.sec_risk_factors import compare, fetch_item_1a, latest_10ks

edgar = EdgarClient()
ciks = edgar.company_tickers()
for t in ["AAPL", "OSCR", "TXG", "HOG", "JPM", "DAR", "KO", "NVDA"]:
    try:
        ks = latest_10ks(edgar, ciks[t], "2026-10-18")
        secs = [fetch_item_1a(edgar, ciks[t], k) for k in ks]
        c = compare(secs[0], secs[1]) if len(secs) == 2 else {}
        print(t, [k["filing_date"] for k in ks], [len(s) for s in secs],
              {k: v for k, v in c.items() if k not in ("added", "removed")})
        print("   first:", (secs[0][0][:160] if secs and secs[0] else None))
        print("   added0:", (c.get("added") or [""])[0][:200])
    except Exception as e:  # noqa: BLE001
        print(t, "ERROR", type(e).__name__, e)

h = {"User-Agent": "financial-advisor research", "Accept": "application/json"}
for url in [
    "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest?limit=3",
    "https://api.finra.org/data/group/otcMarket/name/regShoDaily?limit=3",
    "https://cdn.finra.org/equity/regsho/daily/CNMSshvol20261016.txt",
    "https://cdn.finra.org/equity/regsho/daily/FNSQshvol20261016.txt",
]:
    try:
        r = httpx.get(url, headers=h, timeout=30, follow_redirects=True)
        print(url, r.status_code, len(r.content), r.text[:400].replace("\n", " | "))
    except Exception as e:  # noqa: BLE001
        print(url, "ERROR", e)
for url, body in [
    ("https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest",
     {"limit": 3, "compareFilters": [{"compareType": "equal", "fieldName": "symbolCode", "fieldValue": "AAPL"}]}),
]:
    try:
        r = httpx.post(url, json=body, headers=h, timeout=30)
        print("POST", url, r.status_code, r.text[:600].replace("\n", " | "))
    except Exception as e:  # noqa: BLE001
        print(url, "ERROR", e)
