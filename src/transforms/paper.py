"""Paper portfolio from the factor scores, measured against VOO, in Mexican pesos
(pure; unit-tested). Used by src/jobs/paper_portfolio.py.

Rules (config/paper_portfolio.yaml):
  - On each signal day (the start day, then every month's last factor day) the
    portfolio holds the best `n` eligible stocks by factor rank, at most
    `max_per_industry` from one industry.
  - A stock already held stays while it is eligible and ranked within `keep_rank`,
    so names hovering around 20th place don't churn.
  - Trades fill at the close of the next trading day (the signal uses that day's
    close, so filling at it would be look-ahead).
  - Exits are sold; new names share the cash equally, each capped at NAV / n.
    Names kept are not re-weighted (that would pay costs for little).
  - Every buy and sell pays the GBM cost per side (commission with IVA, FX spread,
    slippage). Dividends pay withholding. Realized gains pay ISR once a year.
  - The benchmark buys VOO once with the same costs and holds it.

Prices are USD and split-adjusted to today's share basis; everything the investor
sees (cash, NAV, costs, gains, taxes) is in MXN at the day's USD/MXN rate.
"""
from __future__ import annotations

import bisect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Rules:
    n: int = 20
    keep_rank: int = 40
    max_per_industry: int = 5
    cost_per_side: float = 0.0084
    initial_mxn: float = 1_000_000.0
    isr_on_gains: float = 0.10
    dividend_withholding: float = 0.10


class Series:
    """Last known value on or before a day, for sorted (day, value) pairs."""

    def __init__(self, pairs: Sequence[tuple[str, float]]):
        pairs = sorted(pairs)
        self.days = [d for d, _ in pairs]
        self.vals = [v for _, v in pairs]

    def at(self, day: str) -> float | None:
        i = bisect.bisect_right(self.days, day)
        return self.vals[i - 1] if i else None

    def last_day(self, day: str) -> str | None:
        i = bisect.bisect_right(self.days, day)
        return self.days[i - 1] if i else None


def select_targets(held: Sequence[str], scores: Mapping[str, dict], rules: Rules) -> list[dict]:
    """Decisions for one signal day: each held or chosen ticker with action
    hold / buy / sell and the reason. scores: ticker -> {rank, eligible, industry, score}."""
    def ok(t: str, max_rank: int) -> bool:
        s = scores.get(t)
        return bool(s and s.get("eligible") and s.get("rank") and s["rank"] <= max_rank)

    def group(t: str) -> str:
        return (scores.get(t) or {}).get("industry") or f"_none:{t}"

    per_group: dict[str, int] = {}
    keep: list[str] = []
    for t in sorted((t for t in held if ok(t, rules.keep_rank)), key=lambda t: scores[t]["rank"]):
        if per_group.get(group(t), 0) < rules.max_per_industry and len(keep) < rules.n:
            keep.append(t)
            per_group[group(t)] = per_group.get(group(t), 0) + 1
    buys: list[str] = []
    ranked = sorted((t for t, s in scores.items() if s.get("eligible") and s.get("rank")),
                    key=lambda t: scores[t]["rank"])
    for t in ranked:
        if len(keep) + len(buys) >= rules.n:
            break
        if t in held or per_group.get(group(t), 0) >= rules.max_per_industry:
            continue
        buys.append(t)
        per_group[group(t)] = per_group.get(group(t), 0) + 1

    def row(t: str, action: str, reason: str) -> dict:
        s = scores.get(t) or {}
        return {"ticker": t, "action": action, "reason": reason, "rank": s.get("rank"),
                "score": s.get("score"), "industry": s.get("industry")}

    out = [row(t, "hold", "rank_within_keep") for t in keep]
    out += [row(t, "buy", "top_rank") for t in buys]
    for t in held:
        if t in keep:
            continue
        s = scores.get(t)
        reason = ("not_scored" if not s else "ineligible" if not s.get("eligible")
                  else "rank_below_keep" if not ok(t, rules.keep_rank) else "industry_cap")
        out.append(row(t, "sell", reason))
    return out


@dataclass
class Position:
    shares: float
    cost_mxn: float


@dataclass
class Book:
    """Cash and positions in MXN; realized gains of the current year until ISR is paid."""
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    realized_ytd: float = 0.0
    fills: list[dict] = field(default_factory=list)

    def value(self, day: str, px: Callable[[str, str], float | None], fx: float) -> float:
        return self.cash + sum(p.shares * (px(t, day) or 0.0) * fx for t, p in self.positions.items())

    def liquidation(self, day: str, px, fx: float, rules: Rules) -> float:
        """What the investor would keep selling everything today: after the sell
        cost and ISR on the year's net gain (losses offset gains)."""
        proceeds = sum(p.shares * (px(t, day) or 0.0) * fx * (1 - rules.cost_per_side)
                       for t, p in self.positions.items())
        gain = self.realized_ytd + proceeds - sum(p.cost_mxn for p in self.positions.values())
        return self.cash + proceeds - rules.isr_on_gains * max(0.0, gain)

    def sell(self, t: str, day: str, price: float, fx: float, rules: Rules, signal_day: str, reason: str):
        p = self.positions.pop(t)
        gross = p.shares * price * fx
        cost = gross * rules.cost_per_side
        gain = gross - cost - p.cost_mxn
        self.cash += gross - cost
        self.realized_ytd += gain
        self.fills.append({"exec_day": day, "signal_day": signal_day, "ticker": t, "side": "sell",
                           "reason": reason, "shares": p.shares, "price_usd": price, "usd_mxn": fx,
                           "amount_mxn": gross, "cost_mxn": cost, "gain_mxn": gain})

    def buy(self, t: str, day: str, price: float, fx: float, budget: float, rules: Rules, signal_day: str):
        cost = budget * rules.cost_per_side / (1 + rules.cost_per_side)
        shares = (budget - cost) / (price * fx)
        self.cash -= budget
        self.positions[t] = Position(shares, budget)
        self.fills.append({"exec_day": day, "signal_day": signal_day, "ticker": t, "side": "buy",
                           "reason": "top_rank", "shares": shares, "price_usd": price, "usd_mxn": fx,
                           "amount_mxn": budget - cost, "cost_mxn": cost, "gain_mxn": None})


def rebalance(book: Book, decisions: Sequence[dict], day: str, px, fx: float, rules: Rules,
              signal_day: str) -> None:
    """Sell the exits, then split the cash among the buys (each at most NAV / n).
    A buy with no price is skipped (its cash stays)."""
    for d in decisions:
        if d["action"] == "sell" and d["ticker"] in book.positions:
            book.sell(d["ticker"], day, px(d["ticker"], day) or 0.0, fx, rules, signal_day, d["reason"])
    buys = [d["ticker"] for d in decisions if d["action"] == "buy" and px(d["ticker"], day)]
    if not buys:
        return
    cap = book.value(day, px, fx) / rules.n
    budget = min(book.cash / len(buys), cap)
    for t in buys:
        if budget > 0:
            book.buy(t, day, px(t, day), fx, budget, rules, signal_day)


def simulate(days: Sequence[str], signals: Sequence[str], decide: Callable[[str, list[str]], list[dict]],
             px: Callable[[str, str], float | None], fx: Series,
             dividends: Mapping[str, Sequence[tuple[str, float]]], rules: Rules) -> dict:
    """Run the book over trading `days` (ascending). Each signal day's decisions
    (decide(signal_day, held)) fill on the first trading day after it. Returns
    decisions per signal, fills and one NAV row per day from the first fill."""
    book = Book(cash=rules.initial_mxn)
    pending = sorted(signals)
    decided: dict[str, list[dict]] = {}
    nav: list[dict] = []
    started = False
    prev = None
    for day in days:
        rate = fx.at(day)
        if rate is None:
            prev = day
            continue
        if started and prev and day[:4] != prev[:4]:
            # ISR on the past year's net realized gain, paid with the first day of the new year
            book.cash -= rules.isr_on_gains * max(0.0, book.realized_ytd)
            book.realized_ytd = 0.0
        if started and prev:
            for t, p in book.positions.items():
                for ex, amount in dividends.get(t, ()):
                    if prev < ex <= day:
                        book.cash += p.shares * amount * rate * (1 - rules.dividend_withholding)
        while pending and pending[0] < day:
            sd = pending.pop(0)
            decided[sd] = decide(sd, list(book.positions))
            rebalance(book, decided[sd], day, px, rate, rules, sd)
            started = True
        if started:
            nav.append({"day": day, "nav_mxn": book.value(day, px, rate), "cash_mxn": book.cash,
                        "liquidation_mxn": book.liquidation(day, px, rate, rules),
                        "n_positions": len(book.positions), "usd_mxn": rate})
        prev = day
    for sd in pending:  # decided now (from the signal day's scores), filled on the next trading day
        decided[sd] = decide(sd, list(book.positions))
    return {"decisions": decided, "pending": pending, "fills": book.fills, "nav": nav}


def adjust_dividends(divs: Sequence[dict], splits: Sequence[dict]) -> list[tuple[str, float]]:
    """Dividend per share on today's share basis: Yahoo already adjusts amounts
    fetched after a split, so only those stored before the split's ex_date are divided."""
    out = []
    for d in divs:
        amount = d["amount"]
        for s in splits:
            if d["ex_date"] < s["ex_date"] and str(d.get("ingested_at") or "")[:10] < s["ex_date"] and s.get("ratio"):
                amount /= s["ratio"]
        out.append((d["ex_date"], amount))
    return sorted(out)
