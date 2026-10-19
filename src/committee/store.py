"""Where a committee run lands: committee_runs, committee_opinions and
committee_alerts (db/migrations/0018_committee_log.sql, owned by Capa 1), and the
full trace (every prompt, tool call and answer) as gzipped JSON in Storage.

Every finished run writes one alert: the candidate's buy, vetoed or not, so
score_alerts can grade the chair and, through the alert, each member's stance.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2.extras

from src.committee.agent import AgentResult
from src.committee.committee import CommitteeResult

TRACE_PREFIX = "committee_traces"


def _stance(role: str, chair: str, out: dict | None) -> str | None:
    if not out:
        return None
    if role == chair:
        return "buy" if out.get("decision") == "approve" else "veto"
    return out.get("stance")


def opinion_row(run_id: int, r: AgentResult, chair: str, validation: dict) -> tuple:
    o = r.output or {}
    evidence = {k: o.get(k) for k in o if k not in ("thesis", "rationale")}
    evidence["validation"] = {k: x for k, x in validation.items() if k != "values"}
    if r.error:
        evidence["error"] = r.error
    score = o.get("score")
    return (run_id, r.role, _stance(r.role, chair, r.output), float(score) if score is not None else None,
            o.get("confidence"), o.get("thesis") or o.get("rationale"), json.dumps(evidence, default=str),
            json.dumps(r.tool_calls, default=str), r.tokens_in, r.tokens_out, round(r.cost_usd, 4))


def alert_row(run_id: int, res: CommitteeResult, horizon: int) -> tuple | None:
    o = res.chair.output if res.chair else None
    p = res.price
    if not o or not p.get("bar_date"):
        return None
    mxn = p.get("price_mxn")
    target = round(mxn * (1 + o["target_pct"] / 100), 2) if mxn and o.get("target_pct") is not None else None
    stop = round(mxn * (1 - abs(o["stop_pct"]) / 100), 2) if mxn and o.get("stop_pct") is not None else None
    vetoed = o.get("decision") == "veto"
    return (run_id, p["bar_date"], "all", res.ticker, "buy", vetoed, o.get("veto_reason") if vetoed else None,
            o.get("confidence"), horizon, p.get("price_usd"), p.get("usd_mxn"), mxn, target, stop, o.get("rationale"))


def trace_doc(res: CommitteeResult, run_id: int, cfg: dict) -> dict:
    agents = [*res.members, *([res.chair] if res.chair else [])]
    return {"committee_run_id": run_id, "ticker": res.ticker, "as_of": res.as_of, "price": res.price,
            "config": cfg, "validation": res.validation, "error": res.error,
            "agents": [{"role": a.role, "output": a.output, "draft": a.draft, "error": a.error, "tool_calls": a.tool_calls,
                        "tokens_in": a.tokens_in, "tokens_out": a.tokens_out, "cost_usd": a.cost_usd,
                        "turns": a.turns} for a in agents]}


def start_run(cur, ticker: str, as_of_ts: datetime, cfg: dict, gh_run: str) -> int:
    c = cfg["committee"]
    cur.execute("insert into committee_runs (kind, ticker, as_of, model, prompt_version, run_id) "
                "values ('new_idea', %s, %s, %s, %s, %s) returning id",
                (ticker, as_of_ts, c["model"], c["prompt_version"], gh_run))
    return cur.fetchone()[0]


def finish_run(cur, run_id: int, res: CommitteeResult, cfg: dict, trace_path: str | None) -> int | None:
    """Opinions, the alert and the run's totals. Returns the alert id, if any."""
    c = cfg["committee"]
    agents = [*res.members, *([res.chair] if res.chair else [])]
    rows = [opinion_row(run_id, a, c["chair"], res.validation.get(a.role, {})) for a in agents]
    if rows:
        psycopg2.extras.execute_values(
            cur, "insert into committee_opinions (committee_run_id, role, stance, score, confidence, reasoning, "
                 "evidence, tool_calls, tokens_in, tokens_out, cost_usd) values %s", rows)
    alert_id = None
    alert = alert_row(run_id, res, c["horizon_months"]) if not res.error else None
    if alert:
        cur.execute("insert into committee_alerts (committee_run_id, alert_day, owner, ticker, alert, vetoed, "
                    "veto_reason, conviction, horizon_months, price_usd, usd_mxn, price_mxn, target_mxn, stop_mxn, "
                    f"rationale) values ({', '.join(['%s'] * len(alert))}) returning id", alert)
        alert_id = cur.fetchone()[0]
    v = res.validation.values()
    cur.execute("update committee_runs set finished_at = now(), status = %s, tokens_in = %s, tokens_out = %s, "
                "cost_usd = %s, refs_checked = %s, refs_mismatched = %s, trace_path = %s where id = %s",
                ("done" if alert_id else "failed", sum(a.tokens_in for a in agents), sum(a.tokens_out for a in agents),
                 round(res.cost_usd, 4), sum(x.get("checked", 0) for x in v),
                 sum(x.get("mismatches", 0) + x.get("unresolved", 0) for x in v), trace_path, run_id))
    return alert_id


def save_trace(doc: dict, out_dir: Path, s3: Any | None, bucket: str | None) -> str | None:
    """The trace as a local file (kept as a workflow artifact) and in Storage;
    returns the Storage key, or None when the upload is not possible."""
    body = gzip.compress(json.dumps(doc, ensure_ascii=False, default=str).encode())
    day = (doc.get("price") or {}).get("bar_date") or datetime.now(timezone.utc).date().isoformat()
    key = f"{TRACE_PREFIX}/{day}/{doc['ticker']}_{doc['committee_run_id']}.json.gz"
    local = out_dir / key
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(body)
    if s3 is None:
        return None
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json", ContentEncoding="gzip")
    return key
