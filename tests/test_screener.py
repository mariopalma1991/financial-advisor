"""Screener filter evaluation."""
from typing import Literal

from src.config import ScreenFilter
from src.screener.engine import evaluate_screen


def _f(metric: str, op: Literal[">", ">=", "<", "<=", "==", "!="], value: float) -> ScreenFilter:
    return ScreenFilter(metric=metric, op=op, value=value)


def test_all_filters_must_pass():
    metrics = {"rsi_14": 30.0, "atr_pct": 0.02}
    assert evaluate_screen(metrics, [_f("rsi_14", "<=", 35), _f("atr_pct", ">=", 0.015)])
    assert not evaluate_screen(metrics, [_f("rsi_14", "<=", 25)])


def test_none_metric_fails_closed():
    assert not evaluate_screen({"rsi_14": None}, [_f("rsi_14", "<=", 35)])
    assert not evaluate_screen({}, [_f("rsi_14", "<=", 35)])


def test_all_operators():
    m = {"x": 5.0}
    assert evaluate_screen(m, [_f("x", ">", 4)])
    assert evaluate_screen(m, [_f("x", ">=", 5)])
    assert evaluate_screen(m, [_f("x", "<", 6)])
    assert evaluate_screen(m, [_f("x", "<=", 5)])
    assert evaluate_screen(m, [_f("x", "==", 5)])
    assert evaluate_screen(m, [_f("x", "!=", 4)])
