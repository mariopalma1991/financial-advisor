"""Shared Finnhub client: reads FINNHUB_API_KEY from the environment or .env.

Rate limit: 30 calls/min on our Finnhub plan, shared across all Finnhub adapters.
"""

from __future__ import annotations

import os
from pathlib import Path

import finnhub
from dotenv import load_dotenv

_load_dotenv_done = False


def _ensure_dotenv() -> None:
    global _load_dotenv_done
    if not _load_dotenv_done:
        load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
        _load_dotenv_done = True


def _finnhub_client() -> finnhub.Client:
    _ensure_dotenv()
    api_key = os.getenv("FINNHUB_API_KEY", "")
    if not api_key:
        raise RuntimeError("FINNHUB_API_KEY not set.")
    return finnhub.Client(api_key=api_key)
