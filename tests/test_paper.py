import pytest

from src.jobs.paper_portfolio import signal_days
from src.transforms import paper

RULES = paper.Rules(n=2, keep_rank=3, max_per_industry=1, cost_per_side=0.01, initial_mxn=1000.0,
                    isr_on_gains=0.10, dividend_withholding=0.10, whole_shares=False)


def _scores(ranked, industries=None, ineligible=()):
    industries = industries or {}
    return {t: {"rank": i + 1, "eligible": t not in ineligible, "industry": industries.get(t, t), "score": -i}
            for i, t in enumerate(ranked)}


def _acts(decisions):
    return {d["ticker"]: (d["action"], d["reason"]) for d in decisions}


def test_select_fills_best_ranks_with_industry_cap():
    s = _scores(["A", "B", "C"], industries={"A": "bank", "B": "bank", "C": "oil"})
    assert _acts(paper.select_targets([], s, RULES)) == {"A": ("buy", "top_rank"), "C": ("buy", "top_rank")}


def test_select_keeps_holding_inside_keep_rank_and_sells_the_rest():
    s = _scores(["A", "B", "C", "D"], ineligible={"D"})
    acts = _acts(paper.select_targets(["C", "D"], s, RULES))
    assert acts["C"] == ("hold", "rank_within_keep")     # 3rd: outside top 2, inside keep_rank 3
    assert acts["D"] == ("sell", "ineligible")
    assert acts["A"] == ("buy", "top_rank") and "B" not in acts


def test_select_sells_below_keep_rank_and_unscored():
    s = _scores(["A", "B", "C", "D"])
    acts = _acts(paper.select_targets(["D", "Z"], s, RULES))
    assert acts["D"] == ("sell", "rank_below_keep") and acts["Z"] == ("sell", "not_scored")


def test_series_last_value_on_or_before():
    s = paper.Series([("2026-01-02", 1.0), ("2026-01-05", 2.0)])
    assert s.at("2026-01-01") is None and s.at("2026-01-04") == 1.0 and s.at("2026-01-09") == 2.0


def _run(prices, fx_rate=20.0, divs=None, signals=("2026-01-01",), decide=None, rules=RULES, flows=(),
         basis=None):
    days = sorted({d for p in prices.values() for d in p})
    series = {t: paper.Series(list(p.items())) for t, p in prices.items()}
    px = lambda t, d: series[t].at(d) if t in series else None
    fx = paper.Series([(days[0], fx_rate)])
    decide = decide or (lambda sd, held, n, can_buy: paper.select_targets(
        held, _scores(sorted(prices)), rules, n, can_buy))
    return paper.simulate(days, list(signals), decide, px, fx, divs or {}, rules, flows=flows, basis=basis)


def test_fills_next_day_in_mxn_with_costs():
    prices = {"A": {"2026-01-01": 10.0, "2026-01-02": 10.0}, "B": {"2026-01-01": 5.0, "2026-01-02": 5.0}}
    r = _run(prices)
    buys = [f for f in r["fills"] if f["side"] == "buy"]
    assert {f["exec_day"] for f in buys} == {"2026-01-02"}       # not the signal day's close
    a = next(f for f in buys if f["ticker"] == "A")
    assert a["amount_mxn"] + a["cost_mxn"] == pytest.approx(500.0)
    assert a["shares"] == pytest.approx(500 / 1.01 / (10 * 20))
    assert r["nav"][-1]["contributed_mxn"] == 1000.0
    nav = r["nav"][-1]
    assert nav["nav_mxn"] == pytest.approx(1000 / 1.01)
    # selling everything today pays the sell cost too (no gain, so no ISR)
    assert nav["liquidation_mxn"] == pytest.approx(1000 / 1.01 * 0.99)


def test_dividends_after_withholding_and_isr_at_year_end():
    prices = {"A": {"2025-12-30": 10.0, "2025-12-31": 10.0, "2026-01-02": 20.0, "2026-01-05": 20.0},
              "B": {"2025-12-30": 10.0, "2025-12-31": 10.0, "2026-01-02": 10.0, "2026-01-05": 10.0}}
    rules = paper.Rules(n=1, keep_rank=1, max_per_industry=1, cost_per_side=0.0, initial_mxn=100.0,
                        isr_on_gains=0.10, dividend_withholding=0.10, whole_shares=False)
    sets = {"2025-12-29": ["A"], "2026-01-02": ["B"]}

    def decide(sd, held, n, can_buy):
        return paper.select_targets(held, _scores(sets[sd]), rules, n, can_buy)

    r = _run(prices, fx_rate=1.0, divs={"A": [("2025-12-31", 1.0)]}, signals=sets, decide=decide, rules=rules)
    nav = {x["day"]: x for x in r["nav"]}
    assert nav["2025-12-31"]["cash_mxn"] == pytest.approx(10 * 1.0 * 0.9)   # 10 shares, 1 USD, 10% withheld
    assert nav["2025-12-31"]["twr"] == pytest.approx(0.09)                 # no deposits: plain return
    sell = next(f for f in r["fills"] if f["side"] == "sell")
    assert sell["exec_day"] == "2026-01-05" and sell["gain_mxn"] == pytest.approx(100.0)
    # the gain falls in 2026, so ISR is still owed: only the liquidation value takes it
    assert nav["2026-01-05"]["liquidation_mxn"] == pytest.approx(nav["2026-01-05"]["nav_mxn"] - 10.0)


def test_isr_paid_on_first_day_of_new_year():
    prices = {"A": {"2025-12-01": 10.0, "2025-12-02": 10.0, "2025-12-03": 20.0, "2026-01-02": 20.0},
              "B": {"2025-12-01": 10.0, "2025-12-02": 10.0, "2025-12-03": 10.0, "2026-01-02": 10.0}}
    rules = paper.Rules(n=1, keep_rank=1, max_per_industry=1, cost_per_side=0.0, initial_mxn=100.0,
                        whole_shares=False)
    sets = {"2025-12-01": ["A"], "2025-12-02": ["B"]}
    r = _run(prices, fx_rate=1.0, signals=sets,
             decide=lambda sd, held, n, cb: paper.select_targets(held, _scores(sets[sd]), rules, n, cb), rules=rules)
    nav = {x["day"]: x for x in r["nav"]}
    assert nav["2025-12-03"]["cash_mxn"] == pytest.approx(200.0 - 200.0)    # all in B
    assert nav["2026-01-02"]["nav_mxn"] == pytest.approx(200.0 - 10.0)      # 10% of the 100 gain


def test_pending_signal_is_decided_but_not_filled():
    prices = {"A": {"2026-01-01": 10.0}, "B": {"2026-01-01": 5.0}}
    r = _run(prices)
    assert r["pending"] == ["2026-01-01"] and r["fills"] == [] and r["nav"] == []
    assert _acts(r["decisions"]["2026-01-01"])["A"][0] == "buy"


def test_dividends_split_adjusted_only_when_stored_before_split():
    splits = [{"ex_date": "2026-06-01", "ratio": 2.0}]
    divs = [{"ex_date": "2026-03-01", "amount": 1.0, "ingested_at": "2026-03-02T00:00:00"},
            {"ex_date": "2026-04-01", "amount": 0.5, "ingested_at": "2026-07-01T00:00:00"}]
    assert paper.adjust_dividends(divs, splits) == [("2026-03-01", 0.5), ("2026-04-01", 0.5)]


def test_signal_days_start_then_completed_month_ends():
    days = ["2025-10-31", "2026-09-30", "2026-10-16", "2026-10-19", "2026-10-20"]
    assert signal_days(days, "2026-10-19", "2026-10-20") == ["2026-10-19"]
    assert signal_days(days, "2025-10-31", "2026-10-20") == ["2025-10-31", "2026-09-30"]
    assert signal_days(days + ["2026-11-02"], "2026-10-19", "2026-11-02") == ["2026-10-19", "2026-10-20"]


WHOLE = paper.Rules(n=4, keep_rank=8, max_per_industry=1, cost_per_side=0.0, initial_mxn=0.0,
                    min_position_mxn=2000.0)


def test_slots_grow_with_the_budget():
    assert [WHOLE.slots(v) for v in (1000, 5000, 10000, 1e6)] == [1, 2, 4, 4]


def test_small_budget_skips_shares_above_the_position_budget():
    # 5,000 MXN -> 2 positions of 2,500; A costs 3,000 a share (skipped), B 1,000, C 500
    prices = {t: {"2026-01-01": p, "2026-01-02": p} for t, p in (("A", 150.0), ("B", 50.0), ("C", 25.0))}
    r = _run(prices, rules=WHOLE, flows=[("2026-01-01", 5000.0)])
    acts = _acts(r["decisions"]["2026-01-01"])
    assert acts["A"] == ("skip", "share_above_position_budget")
    assert acts["B"][0] == "buy" and acts["C"][0] == "buy"
    shares = {f["ticker"]: sum(x["shares"] for x in r["fills"] if x["ticker"] == f["ticker"]) for f in r["fills"]}
    assert all(float(v).is_integer() for v in shares.values())   # whole shares only
    # B: 2 shares of 1,000; C: 5 of 500; the 500 left can't buy another B within its budget
    assert r["nav"][-1]["cash_mxn"] == pytest.approx(500.0)


def test_deposit_tops_up_positions_and_is_removed_from_the_return():
    days = ["2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06"]
    prices = {"B": {d: 50.0 for d in days}, "C": {d: 25.0 for d in days}}
    rules = paper.Rules(**{**WHOLE.__dict__, "n": 2})
    r = _run(prices, rules=rules, flows=[("2026-01-01", 4000.0), ("2026-01-05", 2000.0)])
    nav = {x["day"]: x for x in r["nav"]}
    assert nav["2026-01-05"]["contributed_mxn"] == 6000.0 and nav["2026-01-05"]["flow_mxn"] == 2000.0
    assert nav["2026-01-05"]["cash_mxn"] == pytest.approx(0.0)    # the deposit went into B and C
    assert nav["2026-01-06"]["twr"] == pytest.approx(0.0)         # flat prices: no return from the deposit


def test_deposit_that_opens_a_new_slot_waits_for_the_month_end():
    days = ["2026-01-01", "2026-01-02", "2026-01-05"]
    prices = {"B": {d: 50.0 for d in days}, "C": {d: 25.0 for d in days}}
    r = _run(prices, rules=WHOLE, flows=[("2026-01-01", 4000.0), ("2026-01-05", 2000.0)])
    # 6,000 allows a third position: the 2,000 stays as cash until the next rebalance buys one
    assert {x["day"]: x for x in r["nav"]}["2026-01-05"]["cash_mxn"] == pytest.approx(2000.0)


def test_whole_shares_rounded_on_the_trade_days_basis():
    # a 2-for-1 split later: today's price 50 was 100 on the trade day, so 250 MXN at fx 1
    # buys 2 shares then, 4 today
    assert paper.shares_for(250.0, 50.0, 1.0, WHOLE, basis=2.0) == 4.0
    assert paper.shares_for(99.0, 100.0, 1.0, WHOLE) == 0.0
