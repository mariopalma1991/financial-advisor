"""Bar sanitizing, filings form filter and news aliases."""
from datetime import date

from src.adapters.base import RawPriceBar
from src.config import load_platform
from src.transforms.price_quality import sanitize_bars
from src.transforms.ticker_aliases import core_name, patterns_for


def _bar(**kw):
    base = {"source": "yfinance", "ticker": "WBD", "bar_date": date(2026, 10, 5),
            "open": 31.0, "high": 31.5, "low": 30.5, "close": 31.0, "volume": 100}
    return RawPriceBar(**{**base, **kw})


def test_sanitize_drops_bars_without_close_and_nulls_bad_fields():
    bars, fixes = sanitize_bars([_bar(open=0), _bar(close=None), _bar(close=-1), _bar(high=30.0)])
    assert len(bars) == 2
    assert bars[0].open is None and bars[0].close == 31.0
    assert bars[1].high == 31.0  # widened to contain open/close
    assert fixes["dropped_no_close"] == 2 and fixes["nulled_non_positive"] == 1 and fixes["widened_high"] == 1


def test_clean_bars_pass_untouched():
    bars, fixes = sanitize_bars([_bar()])
    assert bars == [_bar()] and not fixes


def test_keep_forms_cover_us_and_foreign_issuers():
    keep = set(load_platform().ingestion["filings"]["keep_forms"])
    assert {"8-K", "10-K", "10-Q", "6-K", "20-F", "4", "144"} <= keep
    assert not {"424B2", "FWP", "S-8", "13F-HR"} & keep


def test_core_name_strips_suffixes_and_trailing_initials():
    assert core_name("THERMO FISHER SCIENTIFIC INC.") == ["THERMO", "FISHER", "SCIENTIFIC"]
    assert core_name("FERRARI N.V.") == ["FERRARI"]
    assert core_name("THE TJX COMPANIES INC.") == ["TJX"]


def test_patterns_avoid_ambiguous_words():
    pats = [p for p, _ in patterns_for("BNY", "BANK OF NEW YORK MELLON")]
    assert r"\mBANK OF NEW YORK MELLON\M" in pats and r"\mBANK OF\M" not in pats
    # short tickers only match as cashtag / exchange prefix, never as a bare word
    pats_m = patterns_for("M", "MACY´S INC.")
    assert all(cs for p, cs in pats_m if "M\\M" in p and "MACY" not in p)
    assert not any(p == r"\mM\M" for p, _ in pats_m)
    assert any("Disney" in p for p, _ in patterns_for("DIS", "WALT DISNEY CO", ["Disney"]))
