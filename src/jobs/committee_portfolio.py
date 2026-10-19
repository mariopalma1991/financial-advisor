"""Capa 4: the Capa 2 model portfolio with the committee's veto, against Capa 2 and
VOO, in MXN (rules in src/transforms/veto.py, settings in config/committee_portfolio.yaml).

One book per entry in books (one per committee model, and the majority of the three),
written to paper_decisions, paper_fills and paper_nav like Capa 2's books. Every book
starts on Capa 2's first forward decision, with the investor's deposits from
investor_contributions plus the recurring plan, the same costs and whole shares
(config/paper_portfolio.yaml). Recomputed every run from what is stored once: Capa 2's
forward decisions and the committee's runs. Runs after paper_portfolio and committee.
  python -m src.jobs.committee_portfolio
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import psycopg2.extras
import yaml

from src.jobs import paper_portfolio as pp
from src.quality.run_metadata import job_run
from src.storage.supabase_client import pg_connection
from src.transforms import paper, veto

_ROOT = Path(__file__).resolve().parents[2]

# Each model's first finished review of a ticker in the window (Capa 2's decision, the buy's close).
_REVIEWS = """
select distinct on (r.ticker, r.model) r.ticker, r.model, a.vetoed, a.conviction, r.id as run
from committee_runs r join committee_alerts a on a.committee_run_id = r.id
where r.kind = 'new_idea' and r.status = 'done' and r.model = any(%(models)s)
  and r.started_at >= %(start)s and r.finished_at <= %(deadline)s
order by r.ticker, r.model, r.finished_at
"""


def deadline(signal_day: str, days: list[str], at_utc: str) -> datetime:
    """The buy's close: the first trading day after the signal (a weekday when the
    prices don't reach it yet), at at_utc."""
    fill = next((d for d in days if d > signal_day), None)
    if not fill:
        d = date.fromisoformat(signal_day) + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        fill = d.isoformat()
    h, m = (int(x) for x in at_utc.split(":"))
    return datetime.fromisoformat(fill).replace(hour=h, minute=m, tzinfo=UTC)


def main() -> None:
    cfg = yaml.safe_load((_ROOT / "config/committee_portfolio.yaml").read_text())
    rules, pcfg = pp.load_rules()
    src = cfg["source_portfolio"]
    with job_run("committee_portfolio") as em, pg_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            capa2: dict[str, list[dict]] = defaultdict(list)
            decided_at: dict[str, datetime] = {}
            for r in pp._rows(cur, "select signal_day, ticker, action, reason, rank, score, industry, decided_at "
                                   "from paper_decisions where portfolio = %(p)s", {"p": src}):
                sd = pp._iso(r.pop("signal_day"))
                at = r.pop("decided_at")
                decided_at[sd] = min(decided_at.get(sd, at), at)
                capa2[sd].append(r)
            signals = sorted(capa2)
            if not signals:
                em.emit("INFO", "capa4_waiting", f"{src} has no decisions yet")
                return
            market = pp.load_market(cur, signals[0])
            bench = pcfg["benchmark"]
            if bench not in market["closes"] or market["closes"][bench].days[0] > signals[0]:
                bench = pp.FALLBACK_BENCHMARK
            days = [d for d in market["closes"][bench].days if d >= signals[0]]
            latest = days[-1] if days else signals[0]
            deposits = pp.contributions(cur, pcfg["owner"]) or [(pcfg["forward_start"], float(pcfg["initial_mxn"]))]
            flows = sorted(deposits + paper.recurring_flows(pcfg["forward_start"], latest,
                                                            float(pcfg.get("recurring_mxn") or 0),
                                                            pcfg.get("recurring_days") or []))
            bres = pp.run_benchmark(signals[0], bench, market, days, rules, flows)
            for name, book in cfg["books"].items():
                reviews: dict[str, dict[str, list[dict]]] = {}
                for sd in signals:
                    cur.execute(_REVIEWS, {"models": book["models"], "start": decided_at[sd],
                                           "deadline": deadline(sd, days, cfg["review_deadline_utc"])})
                    by_ticker: dict[str, list[dict]] = defaultdict(list)
                    for r in cur.fetchall():
                        by_ticker[r["ticker"]].append(dict(r))
                    reviews[sd] = by_ticker

                def decide(sd, held, slots, can_buy, _rev=reviews, _votes=book["veto_votes"]):
                    return veto.apply_veto(capa2[sd], held, _rev[sd], _votes)

                result = paper.simulate(days, signals, decide, pp._px(market), market["fx"], market["divs"], rules,
                                        flows=flows, basis=pp._basis(market))
                pp.store(cur, name, result, bres, bench, rules, result["decisions"], replace_decisions=True)
                conn.commit()
                last = result["nav"][-1] if result["nav"] else None
                blast = bres["nav"][-1] if bres["nav"] else None
                em.emit("INFO", "capa4_book", name, payload={
                    "signals": signals, "fills": len(result["fills"]), "pending": result["pending"],
                    "vetoed": sorted(d["ticker"] for ds in result["decisions"].values() for d in ds
                                     if d["action"] == "veto"),
                    "nav_mxn": last and round(last["nav_mxn"]), "cash_mxn": last and round(last["cash_mxn"]),
                    "bench_nav_mxn": blast and round(blast["nav_mxn"])})


if __name__ == "__main__":
    main()
