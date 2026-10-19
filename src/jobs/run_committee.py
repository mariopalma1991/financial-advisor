"""Capa 3: run the agent committee on candidates from the Capa 2 factor list and
store each run, every agent's opinion and the chair's approve-or-veto alert
(src/committee). Needs ANTHROPIC_API_KEY.

  python -m src.jobs.run_committee AAPL NVDA          # these tickers
  python -m src.jobs.run_committee --top 20           # the day's top 20 by factor rank
  python -m src.jobs.run_committee AAPL --as-of 2026-10-16
  python -m src.jobs.run_committee --new-buys          # the portfolio's latest buys not yet reviewed
  python -m src.jobs.run_committee AAPL --model openrouter:google/gemini-3.8-flash   # another model, this run only
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import anthropic

from src.committee import committee, openrouter, store
from src.mcp_server import envelope, tools
from src.quality.run_metadata import current_run_id, job_run
from src.storage import archive
from src.storage.supabase_client import pg_connection


def candidates(top_n: int, as_of: str | None) -> list[dict]:
    """The day's best-ranked eligible tickers (the list Capa 2 buys from)."""
    out = tools.screen_universe(top_n=top_n, as_of=as_of)
    return [{"ticker": r["ticker"], "rank": r["rank"], "as_of": r["as_of"]}
            for r in (out.get("data") or {}).get("candidates") or []]


# The model portfolio's buys from its latest decision day that the committee has not
# reviewed since (a ticker that failed twice since that day is left for a person).
_NEW_BUYS = """
select d.ticker, d.rank, d.signal_day from paper_decisions d
where d.portfolio = %(p)s and d.action = 'buy'
  and d.signal_day = (select max(signal_day) from paper_decisions where portfolio = %(p)s)
  and not exists (select 1 from committee_runs r where r.ticker = d.ticker and r.status = 'done'
                  and r.as_of::date >= d.signal_day)
  and (select count(*) from committee_runs r where r.ticker = d.ticker and r.status <> 'done'
       and r.as_of::date >= d.signal_day) < 2
order by d.rank limit %(n)s
"""


def new_buys(cur, portfolio: str, limit: int) -> list[str]:
    cur.execute(_NEW_BUYS, {"p": portfolio, "n": limit})
    return [r[0] for r in cur.fetchall()]


def factor_context(ticker: str, as_of: str | None) -> str:
    s = (tools.get_factor_scores(ticker, as_of=as_of).get("data") or {}).get("scores") or {}
    if not s:
        return "No tiene puntaje de factores ese día."
    return (f"Viene de la lista de factores de Capa 2: lugar {s.get('rank')} el {s.get('as_of')} "
            f"(score {s.get('score')}).")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("tickers", nargs="*")
    p.add_argument("--top", type=int, default=0, help="take the day's top N by factor rank")
    p.add_argument("--new-buys", action="store_true",
                   help="review the model portfolio's latest buys the committee has not seen")
    p.add_argument("--as-of", default=os.getenv("COMMITTEE_AS_OF") or None)
    p.add_argument("--model", default=os.getenv("COMMITTEE_MODEL") or None,
                   help="model for this run only (default: config/committee.yaml)")
    p.add_argument("--out", default="committee_out", help="local folder for the traces")
    args = p.parse_args()
    cfg = committee.load_config()
    if args.model:
        if args.model not in cfg["pricing"] and not openrouter.is_openrouter(args.model):
            raise SystemExit(f"no pricing for {args.model} in config/committee.yaml "
                             f"(OpenRouter models go as {openrouter.PREFIX}<vendor/model>)")
        cfg["committee"]["model"] = args.model
    as_of = args.as_of or None
    tickers = [t.upper() for t in args.tickers]
    if args.new_buys:
        with pg_connection() as conn, conn.cursor() as cur:
            tickers = new_buys(cur, cfg["committee"]["new_buys_portfolio"], cfg["committee"]["max_new_buys"])
        if not tickers:
            print("no new buys to review")
            return
    elif not tickers:
        tickers = [c["ticker"] for c in candidates(args.top or cfg["committee"]["default_top_n"], as_of)]
    at = envelope.parse_as_of(as_of)
    as_of_ts = at.ts if at.past else envelope.parse_as_of(envelope._SNAPSHOT).ts
    client = None if openrouter.is_openrouter(cfg["committee"]["model"]) else anthropic.Anthropic()
    try:
        s3, bucket = archive.s3_client(), archive.bucket()
    except RuntimeError:
        s3 = bucket = None
    out_dir = Path(args.out)
    failed = 0
    with job_run("run_committee") as em:
        for t in tickers:
            with pg_connection() as conn, conn.cursor() as cur:
                run_id = store.start_run(cur, t, as_of_ts, cfg, current_run_id())
                conn.commit()
                try:
                    res = committee.run(client, t, as_of, cfg, context=factor_context(t, as_of))
                except Exception as exc:
                    cur.execute("update committee_runs set status = 'failed', finished_at = now() where id = %s",
                                (run_id,))
                    conn.commit()
                    em.emit("ERROR", "committee_failed", f"{t}: {type(exc).__name__}: {exc}", ticker=t,
                            payload={"committee_run_id": run_id})
                    failed += 1
                    continue
                trace_path = None
                try:
                    trace_path = store.save_trace(store.trace_doc(res, run_id, cfg), out_dir, s3, bucket)
                except Exception as exc:  # the run still counts; the local copy stays as an artifact
                    em.emit("WARN", "trace_upload_failed", f"{t}: {exc}", ticker=t)
                alert_id = store.finish_run(cur, run_id, res, cfg, trace_path)
                conn.commit()
            chair = (res.chair.output or {}) if res.chair else {}
            payload = {"committee_run_id": run_id, "alert_id": alert_id, "decision": chair.get("decision"),
                       "cost_usd": round(res.cost_usd, 3), "error": res.error,
                       "stances": {m.role: (m.output or {}).get("stance") for m in res.members},
                       "refs": {r: {k: v[k] for k in ("n", "mismatches", "unresolved")}
                                for r, v in res.validation.items()}}
            if alert_id:
                em.emit("INFO", "committee_done", f"{t}: {chair.get('decision')}", ticker=t, payload=payload)
            else:
                failed += 1
                em.emit("ERROR", "committee_failed", f"{t}: {res.error}", ticker=t, payload=payload)
    if failed:
        raise SystemExit(f"{failed} of {len(tickers)} committees failed")


if __name__ == "__main__":
    main()
