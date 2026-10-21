"""One-off what-if: the last year holding only the factor #1 stock (sell when it is no
longer #1, buy the new #1), on the same budget, costs and taxes as the paper books,
against VOO. Reads only; the result goes to pipeline_events (event_type top1_sim).
  python -m src.jobs.simulate_top1 [start]
"""
from __future__ import annotations

import sys
from collections import defaultdict

import psycopg2.extras

from src.jobs import paper_portfolio as pp
from src.quality.run_metadata import job_run
from src.storage.supabase_client import pg_connection
from src.transforms import paper


def main(start: str = "2025-10-21") -> None:
    rules, cfg = pp.load_rules()
    one = paper.Rules(**{**rules.__dict__, "n": 1, "keep_rank": 1, "max_per_industry": 1, "min_position_mxn": 0.0})
    with job_run("simulate_top1") as em, pg_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            factor_days = [pp._iso(r["as_of"]) for r in pp._rows(cur, "select distinct as_of from factor_scores")]
            cur.execute("select max(bar_date) as d from price_bars_daily")
            latest = pp._iso(cur.fetchone()["d"])
            market = pp.load_market(cur, start)
            signals = pp.signal_days(factor_days, start, latest)
            flows = sorted([(signals[0], float(cfg["initial_mxn"]))]
                           + paper.recurring_flows(signals[0], latest, float(cfg.get("recurring_mxn") or 0),
                                                   cfg.get("recurring_days") or []))
            bench = cfg["benchmark"]
            vc = market["closes"].get(bench)
            if not vc or not vc.days or vc.days[0] > signals[0]:
                bench = pp.FALLBACK_BENCHMARK
            days = [d for d in market["closes"][bench].days if d >= signals[0]]
            res = pp.run_book(cur, signals, {}, market, days, one, flows)
            ben = pp.run_benchmark(signals[0], bench, market, days, one, flows)
            em.emit("INFO", "top1_sim", "summary", payload={
                "signals": signals, "benchmark": bench, "pending": res["pending"],
                "decisions": {sd: [(d["ticker"], d["action"], d.get("rank")) for d in ds] for sd, ds in res["decisions"].items()},
                "fills": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in f.items()} for f in res["fills"]],
                "last": res["nav"][-1], "bench_last": ben["nav"][-1],
                "nav_sample": [r for r in res["nav"] if r["day"][-2:] in ("28", "29", "30", "31")][::3],
            })
            em.emit("INFO", "top1_sim", "done")


if __name__ == "__main__":
    main(*sys.argv[1:2])
