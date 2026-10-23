"""Paper portfolio from the factor scores, measured against VOO, in Mexican pesos
(pure; unit-tested). Used by src/jobs/paper_portfolio.py.

Rules (config/paper_portfolio.yaml):
  - On each signal day (the start day, then every month's last factor day) the
    portfolio holds the best `n` eligible stocks by factor rank, at most
    `max_per_industry` from one industry; fewer while the budget is small, so each
    position is at least `min_position_mxn`.
  - A stock already held stays while it is eligible and ranked within `keep_rank`,
    so names hovering around 20th place don't churn.
  - Trades fill at the close of the next trading day (the signal uses that day's
    close, so filling at it would be look-ahead).
  - Exits are sold; new names share the cash equally, each capped at NAV / n;
    whatever cash is left tops up the positions furthest below NAV / n. Whole
    shares only (a name whose one share costs more than its slot is skipped).
  - The investor's budget: an initial amount plus deposits over time (recorded ones
    and a recurring plan, recurring_flows); each deposit is invested on its day in
    the positions furthest below target.
  - Every buy and sell pays the GBM cost per side (commission with IVA, FX spread,
    slippage). Dividends pay withholding. Realized gains pay ISR once a year.
  - The benchmark buys VOO with the same money on the same days, same costs.

Prices are USD and split-adjusted to today's share basis; everything the investor
sees (cash, NAV, costs, gains, taxes) is in MXN at the day's USD/MXN rate.
"""
from __future__ import annotations

import bisect
import calendar
import math
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
    whole_shares: bool = True     # the SIC trades whole shares
    min_position_mxn: float = 0.0  # fewer, larger positions while the budget is small (0 = always n)

    def slots(self, value: float) -> int:
        """Positions to hold for a portfolio worth `value` MXN: n, or fewer so each is
        at least min_position_mxn."""
        if not self.min_position_mxn:
            return self.n
        return max(1, min(self.n, int(value // self.min_position_mxn)))


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


def select_targets(held: Sequence[str], scores: Mapping[str, dict], rules: Rules, n: int | None = None,
                   can_buy: Callable[[str], bool] | None = None) -> list[dict]:
    """Decisions for one signal day: each held or chosen ticker with action
    hold / buy / sell and the reason. scores: ticker -> {rank, eligible, industry, score}.
    n: positions to hold (default rules.n). can_buy(ticker): False when one share
    costs more than a position's budget; that stock is skipped (action skip) and the
    next one taken, so expensive names come in as the budget grows."""
    n = n or rules.n
    cap = max(1, round(n * rules.max_per_industry / rules.n))   # the same share of the portfolio
    def ok(t: str, max_rank: int) -> bool:
        s = scores.get(t)
        return bool(s and s.get("eligible") and s.get("rank") and s["rank"] <= max_rank)

    def group(t: str) -> str:
        return (scores.get(t) or {}).get("industry") or f"_none:{t}"

    per_group: dict[str, int] = {}
    keep: list[str] = []
    for t in sorted((t for t in held if ok(t, rules.keep_rank)), key=lambda t: scores[t]["rank"]):
        if per_group.get(group(t), 0) < cap and len(keep) < n:
            keep.append(t)
            per_group[group(t)] = per_group.get(group(t), 0) + 1
    buys: list[str] = []
    skipped: list[str] = []
    ranked = sorted((t for t, s in scores.items() if s.get("eligible") and s.get("rank")),
                    key=lambda t: scores[t]["rank"])
    for t in ranked:
        if len(keep) + len(buys) >= n:
            break
        if t in held or per_group.get(group(t), 0) >= cap:
            continue
        if can_buy and not can_buy(t):
            skipped.append(t)
            continue
        buys.append(t)
        per_group[group(t)] = per_group.get(group(t), 0) + 1

    def row(t: str, action: str, reason: str) -> dict:
        s = scores.get(t) or {}
        return {"ticker": t, "action": action, "reason": reason, "rank": s.get("rank"),
                "score": s.get("score"), "industry": s.get("industry")}

    out = [row(t, "hold", "rank_within_keep") for t in keep]
    out += [row(t, "buy", "top_rank") for t in buys]
    out += [row(t, "skip", "share_above_position_budget") for t in skipped]
    for t in held:
        if t in keep:
            continue
        s = scores.get(t)
        reason = ("not_scored" if not s else "ineligible" if not s.get("eligible")
                  else "rank_below_keep" if not ok(t, rules.keep_rank) else "too_many_positions_or_industry")
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
    contributed: float = 0.0   # the investor's deposits so far

    def slots(self, value: float, rules: Rules) -> int:
        """Positions to hold: from the deposits, or the value if it grew past them, so
        a drop below the deposits doesn't cut a position and leave its cash idle."""
        return rules.slots(max(value, self.contributed))

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

    def buy(self, t: str, day: str, price: float, fx: float, shares: float, rules: Rules, signal_day: str,
            reason: str):
        gross = shares * price * fx
        cost = gross * rules.cost_per_side
        self.cash -= gross + cost
        p = self.positions.setdefault(t, Position(0.0, 0.0))
        p.shares += shares
        p.cost_mxn += gross + cost
        same = next((f for f in reversed(self.fills) if f["exec_day"] == day and f["ticker"] == t
                     and f["side"] == "buy"), None) if self.fills and self.fills[-1]["exec_day"] == day else None
        if same:  # a top-up right after the buy is one fill
            same["shares"] += shares
            same["amount_mxn"] += gross
            same["cost_mxn"] += cost
            return
        self.fills.append({"exec_day": day, "signal_day": signal_day, "ticker": t, "side": "buy",
                           "reason": reason, "shares": shares, "price_usd": price, "usd_mxn": fx,
                           "amount_mxn": gross, "cost_mxn": cost, "gain_mxn": None})


def shares_for(budget: float, price: float, fx: float, rules: Rules, basis: float = 1.0) -> float:
    """Shares (today's basis) that `budget` MXN buys with costs. With whole shares the
    count is rounded down on the trade day's basis (`basis` = later split ratios)."""
    unit = price * fx * (1 + rules.cost_per_side)
    if unit <= 0 or budget <= 0:
        return 0.0
    if not rules.whole_shares:
        return budget / unit
    return math.floor(budget / (unit * basis) + 1e-9) * basis


def top_up(book: Book, day: str, px, fx: float, rules: Rules, signal_day: str, basis) -> None:
    """Spend cash on the positions furthest below NAV / n (new money, or what is left
    after a rebalance), whole shares, until no position can take another share."""
    value = book.value(day, px, fx)
    target = value / book.slots(value, rules)
    while True:
        best, gap = None, 0.0
        for t, p in book.positions.items():
            price = px(t, day)
            if not price:
                continue
            g = target - p.shares * price * fx
            if g > max(gap, 1.0) and book.cash > 1.0 and shares_for(min(g, book.cash), price, fx, rules, basis(t, day)) > 0:
                best, gap = t, g
        if best is None:
            return
        price = px(best, day)
        book.buy(best, day, price, fx, shares_for(min(gap, book.cash), price, fx, rules, basis(best, day)),
                 rules, signal_day, "top_up")


def rebalance(book: Book, decisions: Sequence[dict], day: str, px, fx: float, rules: Rules,
              signal_day: str, basis) -> None:
    """Sell the exits, buy the new names (equal shares of the cash, each at most
    NAV / n; a name whose single share costs more is skipped), then top up the
    positions below NAV / n with what is left."""
    for d in decisions:
        if d["action"] == "sell" and d["ticker"] in book.positions:
            book.sell(d["ticker"], day, px(d["ticker"], day) or 0.0, fx, rules, signal_day, d["reason"])
    buys = [d["ticker"] for d in decisions if d["action"] == "buy" and px(d["ticker"], day)]
    if buys:
        value = book.value(day, px, fx)
        budget = min(book.cash / len(buys), value / book.slots(value, rules))
        for t in buys:
            n = shares_for(min(budget, book.cash), px(t, day), fx, rules, basis(t, day))
            if n > 0:
                book.buy(t, day, px(t, day), fx, n, rules, signal_day, "top_rank")
    top_up(book, day, px, fx, rules, signal_day, basis)


def _sizing(book: Book, day: str | None, px, fx: float, rules: Rules, basis) -> tuple[int, Callable[[str], bool]]:
    """Positions the book allows (Book.slots), and whether one share of a stock fits one."""
    value = book.value(day, px, fx) if day and fx else book.cash
    slots = book.slots(value, rules)
    budget = value / slots

    def can_buy(t: str) -> bool:
        price = px(t, day) if day else None
        return bool(price and fx and shares_for(budget, price, fx, rules, basis(t, day)) > 0)

    return slots, can_buy


def simulate(days: Sequence[str], signals: Sequence[str], decide: Callable[..., list[dict]],
             px: Callable[[str, str], float | None], fx: Series,
             dividends: Mapping[str, Sequence[tuple[str, float]]], rules: Rules,
             flows: Sequence[tuple[str, float]] = (), basis=None, fill_at_prior_close: bool = False) -> dict:
    """Run the book over trading `days` (ascending). Each signal day's decisions
    (decide(signal_day, held, slots, can_buy)) fill on the first trading day after
    it; slots is how many positions the deposits (or a larger value) allow (Book.slots) and
    can_buy(ticker) whether one whole share fits a position's budget. `flows` are
    the investor's deposits (day, MXN): cash on the first trading day on or after
    their date, spent the same day on the positions below target (or at the first
    fill). basis(t, day) gives later split ratios for whole shares (default 1).
    fill_at_prior_close: trades (signal fills and deposit top-ups) are priced at the previous
    trading day's close and USD/MXN, the quote an alert gives that morning, while the NAV still
    marks each day's own close.
    Returns decisions per signal, fills and one NAV row per day from the first fill,
    with the money put in so far and the time-weighted return (flows removed)."""
    basis = basis or (lambda t, day: 1.0)
    book = Book(cash=rules.initial_mxn, contributed=rules.initial_mxn)
    contributed = rules.initial_mxn
    queue = sorted(flows)
    pending = sorted(signals)
    decided: dict[str, list[dict]] = {}
    nav: list[dict] = []
    started = False
    prev = None
    twr = 1.0
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
        flow = 0.0
        while queue and queue[0][0] <= day:
            flow += queue.pop(0)[1]
        book.cash += flow
        book.contributed += flow
        contributed += flow
        tpx, trate = px, rate   # the price trades are made at
        if fill_at_prior_close and prev:
            tpx, trate = (lambda t, d, _p=prev: px(t, _p)), (fx.at(prev) or rate)
        filled = False
        while pending and pending[0] < day:
            sd = pending.pop(0)
            decided[sd] = decide(sd, list(book.positions), *_sizing(book, day, tpx, trate, rules, basis))
            rebalance(book, decided[sd], day, tpx, trate, rules, sd, basis)
            filled = True
        if started and flow and not filled:
            top_up(book, day, tpx, trate, rules, day, basis)
        value = book.value(day, px, rate)
        if started:
            last = nav[-1]["nav_mxn"]
            twr *= (value - flow) / last if last else 1.0
        elif filled:
            started = True
            twr = value / contributed if contributed else 1.0
        if started:
            nav.append({"day": day, "nav_mxn": value, "cash_mxn": book.cash,
                        "liquidation_mxn": book.liquidation(day, px, rate, rules),
                        "n_positions": len(book.positions), "usd_mxn": rate, "flow_mxn": flow,
                        "contributed_mxn": contributed, "twr": twr - 1})
        prev = day
    last_day = days[-1] if days else None
    for sd in pending:  # decided now (from the signal day's scores), filled on the next trading day
        rate = (fx.at(last_day) if last_day else None) or 0.0
        decided[sd] = decide(sd, list(book.positions), *_sizing(book, last_day, px, rate, rules, basis))
    return {"decisions": decided, "pending": pending, "fills": book.fills, "nav": nav}


def recurring_flows(start: str, end: str, amount: float, days: Sequence[int | str]) -> list[tuple[str, float]]:
    """A recurring deposit of `amount` on each of `days` of every month ("last" is
    the month's last day), dated after start and up to end (ISO dates). A date that
    is not a trading day becomes cash on the next one (simulate)."""
    out: list[tuple[str, float]] = []
    if amount <= 0 or not days:
        return out
    y, m = int(start[:4]), int(start[5:7])
    while f"{y:04d}-{m:02d}" <= end[:7]:
        last = calendar.monthrange(y, m)[1]
        for d in sorted({last if d == "last" else min(int(d), last) for d in days}):
            iso = f"{y:04d}-{m:02d}-{d:02d}"
            if start < iso <= end:
                out.append((iso, float(amount)))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def split_basis(splits: Sequence[dict], day: str) -> float:
    """Product of split ratios after `day`: one share bought that day is this many today."""
    f = 1.0
    for s in splits:
        if s.get("ratio") and s["ex_date"] > day:
            f *= s["ratio"]
    return f


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
