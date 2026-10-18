"""Adapters must call methods the installed finnhub client actually has.

An adapter once called a method the client does not have
(`visa_application` instead of `stock_visa_application`), retried the
AttributeError on every symbol and failed after 50 minutes without fetching anything.
"""
import re
from pathlib import Path

import finnhub
import pytest

from src.adapters.base import _is_retryable

ADAPTERS = Path(__file__).resolve().parent.parent / "src" / "adapters"


@pytest.mark.parametrize("path", sorted(ADAPTERS.glob("finnhub_*.py")), ids=lambda p: p.name)
def test_adapter_calls_existing_client_methods(path):
    for method in re.findall(r"client\.([a-z_]+)\(", path.read_text()):
        assert hasattr(finnhub.Client, method), f"{path.name}: finnhub.Client has no {method}()"


def test_programming_errors_are_not_retried():
    assert not _is_retryable(AttributeError("no such method"))
    assert _is_retryable(ConnectionError("reset"))
