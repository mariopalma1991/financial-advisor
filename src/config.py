"""Configuration loading and validation for the data platform.

All operational behavior is driven by YAML files in config/ — see plan §12.
Nothing operational should be hardcoded; add it to a config file instead.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"

load_dotenv(REPO_ROOT / ".env")


# ---------- Environment settings ----------

class Settings(BaseModel):
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_db_url: str = ""
    alert_webhook_url: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            supabase_url=os.getenv("SUPABASE_URL", ""),
            supabase_service_key=os.getenv("SUPABASE_SERVICE_KEY", ""),
            supabase_db_url=os.getenv("SUPABASE_DB_URL", ""),
            alert_webhook_url=os.getenv("ALERT_WEBHOOK_URL", ""),
        )

    def require_supabase(self) -> None:
        missing = [
            name
            for name, val in (
                ("SUPABASE_URL", self.supabase_url),
                ("SUPABASE_SERVICE_KEY", self.supabase_service_key),
            )
            if not val
        ]
        if missing:
            raise RuntimeError(
                f"Missing required env vars: {', '.join(missing)}. "
                "Copy .env.example to .env and fill in your Supabase credentials."
            )


# ---------- Tickers ----------

class Ticker(BaseModel):
    symbol: str
    name: str
    exchange: str
    currency: Literal["USD", "MXN"]
    sector: str
    benchmark: str
    enabled: bool = True
    tags: list[str] = []
    notes: str = ""


class TickerUniverse(BaseModel):
    tickers: list[Ticker]

    def enabled(self) -> list[Ticker]:
        return [t for t in self.tickers if t.enabled]

    @property
    def symbols(self) -> list[str]:
        return [t.symbol for t in self.enabled()]

    def by_symbol(self) -> dict[str, Ticker]:
        return {t.symbol: t for t in self.enabled()}


# ---------- Screens ----------

class ScreenFilter(BaseModel):
    metric: str
    op: Literal[">", ">=", "<", "<=", "==", "!="]
    value: float


class Screen(BaseModel):
    description: str = ""
    filters: list[ScreenFilter]


class ScreensConfig(BaseModel):
    screens: dict[str, Screen]


# ---------- Feeds ----------

class FeedDef(BaseModel):
    tables: list[str]


class FeedsConfig(BaseModel):
    feeds: dict[str, FeedDef]


# ---------- Cost model ----------

class CostModel(BaseModel):
    broker: str
    currency: str
    iva_pct: float
    commission: dict[str, Any]
    fx: dict[str, Any]
    slippage_per_side_pct: float
    spread_per_side_pct: float
    decision_rules: dict[str, Any]

    def round_trip_pct(self, currency: str) -> float:
        """Total round-trip cost as a fraction (e.g. 0.0068 ≈ 0.68%)."""
        per_side = float(self.commission["current_tier_pct"]) * (1 + self.iva_pct)
        fx = float(self.fx["spread_per_side_pct"]) if currency == "USD" else 0.0
        return 2 * (per_side + self.slippage_per_side_pct + self.spread_per_side_pct + fx)

    def min_edge_pct(self, currency: str) -> float:
        """Minimum expected move the judge requires before a BUY."""
        return self.round_trip_pct(currency) * float(self.decision_rules["min_edge_multiple"])


# ---------- Platform ----------

class PlatformConfig(BaseModel):
    platform: dict[str, Any]
    ingestion: dict[str, Any]
    indicators: dict[str, Any]
    horizons: dict[str, Any]
    quality: dict[str, Any]
    feeds: dict[str, Any]


# ---------- Loaders ----------

def _load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@lru_cache(maxsize=1)
def load_platform() -> PlatformConfig:
    return PlatformConfig(**_load_yaml("platform.yaml"))


@lru_cache(maxsize=1)
def load_analysis_universe() -> TickerUniverse:
    return TickerUniverse(**_load_yaml("tickers.yaml"))


@lru_cache(maxsize=1)
def load_screening_universe() -> TickerUniverse:
    return TickerUniverse(**_load_yaml("screening_universe.yaml"))


@lru_cache(maxsize=1)
def load_screens() -> ScreensConfig:
    return ScreensConfig(**_load_yaml("screens.yaml"))


@lru_cache(maxsize=1)
def load_feeds() -> FeedsConfig:
    return FeedsConfig(**_load_yaml("feeds.yaml"))


@lru_cache(maxsize=1)
def load_cost_model() -> CostModel:
    return CostModel(**_load_yaml("cost_model.yaml")["cost_model"])


def all_symbols() -> list[str]:
    """Enabled symbols across both tiers (analysis first, then screening-only)."""
    analysis = set(load_analysis_universe().symbols)
    screening = [s for s in load_screening_universe().symbols if s not in analysis]
    return load_analysis_universe().symbols + screening


def tier_of(symbol: str) -> Literal["analysis", "screening"]:
    if symbol in load_analysis_universe().by_symbol():
        return "analysis"
    return "screening"
