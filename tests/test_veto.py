from src.jobs.committee_portfolio import deadline
from src.transforms import paper, veto


def _d(t, action, reason="top_rank"):
    return {"ticker": t, "action": action, "reason": reason, "rank": 1, "score": 0.0, "industry": "x"}


def test_verdict_needs_enough_vetoes():
    rev = [{"model": "a", "vetoed": True}, {"model": "b", "vetoed": False}, {"model": "c", "vetoed": True}]
    assert veto.verdict(rev, 2) == (True, "vetoed_2_of_3")
    assert veto.verdict(rev[:2], 2) == (False, "approved_1_of_2")
    assert veto.verdict([], 1) == (False, "no_review")


def test_apply_veto_skips_vetoed_buys_and_follows_capa2_on_held():
    capa2 = [_d("A", "buy"), _d("B", "buy"), _d("C", "hold"), _d("D", "sell", "rank_below_keep"), _d("E", "hold")]
    out = {d["ticker"]: (d["action"], d["reason"])
           for d in veto.apply_veto(capa2, ["C", "D", "F"], {"A": [{"vetoed": True}]}, 1)}
    assert out == {"A": ("veto", "vetoed_1_of_1"), "B": ("buy", "no_review"), "C": ("hold", "top_rank"),
                   "D": ("sell", "rank_below_keep"), "F": ("sell", "not_in_capa2")}   # E: vetoed before, not held


def test_vetoed_money_stays_cash():
    days = ["2026-01-01", "2026-01-02", "2026-01-05"]
    series = {"A": paper.Series([(d, 10.0) for d in days]), "B": paper.Series([(d, 10.0) for d in days])}
    px = lambda t, d: series[t].at(d) if t in series else None
    rules = paper.Rules(n=2, cost_per_side=0.0, initial_mxn=2000.0, isr_on_gains=0.0, whole_shares=True,
                        min_position_mxn=1000.0)
    capa2 = [_d("A", "buy"), _d("B", "buy")]
    r = paper.simulate(days, ["2026-01-01"], lambda sd, held, n, cb: veto.apply_veto(capa2, held, {"B": [{"vetoed": True}]}, 1),
                       px, paper.Series([(days[0], 1.0)]), {}, rules)
    assert [f["ticker"] for f in r["fills"]] == ["A"]
    assert r["nav"][-1]["cash_mxn"] == 1000.0 and r["nav"][-1]["n_positions"] == 1


def test_deadline_is_the_fill_days_close():
    assert deadline("2026-10-19", ["2026-10-19", "2026-10-20"], "20:00").isoformat() == "2026-10-20T20:00:00+00:00"
    assert deadline("2026-10-23", ["2026-10-23"], "20:00").isoformat() == "2026-10-26T20:00:00+00:00"   # Friday -> Monday
