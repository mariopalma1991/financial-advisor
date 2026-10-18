"""FINRA consolidated short interest (api.finra.org, public data, no key needed):
shares sold short in every exchange-listed stock as of each settlement date
(twice a month), with days to cover. Exchange data, the same numbers Yahoo
republishes, but with history.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

URL = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
PAGE = 5000
_HEADERS = {"Accept": "application/json", "User-Agent": "financial-advisor (github.com/mariopalma1991)"}


def to_row(r: dict[str, Any]) -> dict[str, Any]:
    """One API record -> finra_short_interest columns. FINRA writes class shares
    with a dot (BRK.B); the universe uses a dash (BRK-B)."""
    def _int(v):
        return int(v) if v is not None else None
    return {
        "ticker": str(r.get("symbolCode") or "").upper().replace(".", "-").replace("/", "-"),
        "settlement_date": r.get("settlementDate"),
        "short_shares": _int(r.get("currentShortPositionQuantity")),
        "prior_short_shares": _int(r.get("previousShortPositionQuantity")),
        "change_pct": r.get("changePercent"),
        "avg_daily_volume": _int(r.get("averageDailyVolumeQuantity")),
        "days_to_cover": r.get("daysToCoverQuantity"),
        "market": r.get("marketClassCode"),
    }


class FinraClient:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=60, headers=_HEADERS)

    def _post(self, body: dict) -> list[dict]:
        for attempt in range(4):
            resp = self._client.post(URL, json=body)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json() if resp.content else []
        return []

    def settlement_dates(self, since: str, probe_symbol: str = "AAPL") -> list[str]:
        """Settlement dates since a day, from one always-listed stock's history."""
        rows = self._post({"limit": PAGE, "fields": ["settlementDate"],
                           "compareFilters": [{"compareType": "equal", "fieldName": "symbolCode",
                                               "fieldValue": probe_symbol}],
                           "dateRangeFilters": [{"fieldName": "settlementDate", "startDate": since,
                                                 "endDate": "2100-01-01"}]})
        return sorted({r["settlementDate"] for r in rows if r.get("settlementDate")})

    def for_date(self, settlement_date: str) -> list[dict[str, Any]]:
        """Every stock's record for one settlement date (paged)."""
        out, offset = [], 0
        while True:
            page = self._post({"limit": PAGE, "offset": offset,
                               "compareFilters": [{"compareType": "equal", "fieldName": "settlementDate",
                                                   "fieldValue": settlement_date}]})
            out += page
            if len(page) < PAGE:
                return out
            offset += PAGE
