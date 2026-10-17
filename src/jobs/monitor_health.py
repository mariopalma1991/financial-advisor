"""Daily health check: job runs, table freshness and free-plan budgets.

Reads config/monitoring.yaml. Every check emits a `health_check` event (so the
dashboard can chart them); a breach is WARN or ERROR. Any ERROR makes the run fail,
which GitHub emails to the repo owner, and goes to Slack if ALERT_WEBHOOK_URL is set.

Job status comes from the job_runs view (db/migrations/0004_run_lineage.sql).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.config import _load_yaml
from src.quality.run_metadata import EventEmitter, job_run
from src.storage.supabase_client import pg_connection


def _hours_since(ts: datetime | None, now: datetime) -> float | None:
    return None if ts is None else round((now - ts).total_seconds() / 3600, 1)


def check_jobs(cur, jobs: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    cur.execute(
        """
        select job_name,
               max(ended_at) filter (where status like 'completed%%') as last_ok,
               (array_agg(status order by started_at desc))[1]         as last_status,
               (array_agg(log_url order by started_at desc))[1]        as last_log
        from job_runs where job_name = any(%s) group by job_name
        """,
        (list(jobs),),
    )
    seen = {r[0]: r[1:] for r in cur.fetchall()}
    out = []
    for job, cfg in jobs.items():
        last_ok, last_status, last_log = seen.get(job, (None, None, None))
        age = _hours_since(last_ok, now)
        ok = age is not None and age <= cfg["max_hours"]
        out.append({
            "check": "job", "name": job, "ok": ok, "level": "INFO" if ok else "ERROR",
            "message": (
                f"{job}: last success {age} h ago (max {cfg['max_hours']} h)"
                if age is not None else f"{job}: no successful run on record"
            ) + ("" if ok else f"; last run {last_status or 'none'}"),
            "payload": {"hours_since_success": age, "max_hours": cfg["max_hours"],
                        "last_status": last_status, "log_url": last_log},
        })
    return out


def check_tables(cur, tables: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    out = []
    for table, cfg in tables.items():
        cur.execute(f"select max({cfg['column']}) from {table}")
        age = _hours_since(cur.fetchone()[0], now)
        ok = age is not None and age <= cfg["max_hours"]
        out.append({
            "check": "table", "name": table, "ok": ok, "level": "INFO" if ok else "ERROR",
            "message": f"{table}: newest row {age} h old (max {cfg['max_hours']} h)",
            "payload": {"hours_since_newest": age, "max_hours": cfg["max_hours"]},
        })
    return out


def _budget(name: str, used_mb: float, cfg: dict[str, Any]) -> dict[str, Any]:
    pct = round(100 * used_mb / cfg["limit"], 1)
    level = "ERROR" if pct >= cfg["error_pct"] else "WARN" if pct >= cfg["warn_pct"] else "INFO"
    return {
        "check": "budget", "name": name, "ok": level == "INFO", "level": level,
        "message": f"{name}: {used_mb:.0f} of {cfg['limit']} MB ({pct}%)",
        "payload": {"used_mb": round(used_mb, 1), "limit_mb": cfg["limit"], "pct": pct},
    }


def check_budgets(cur, budgets: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    if cfg := budgets.get("database_mb"):
        cur.execute("select pg_database_size(current_database()) / 1048576.0")
        out.append(_budget("database_mb", float(cur.fetchone()[0]), cfg))
    if cfg := budgets.get("storage_mb"):
        cur.execute(
            "select coalesce(sum((metadata ->> 'size')::bigint), 0) / 1048576.0"
            " from storage.objects where bucket_id = %s",
            (cfg["bucket"],),
        )
        out.append(_budget("storage_mb", float(cur.fetchone()[0]), cfg))
    return out


def run_checks(em: EventEmitter, cfg: dict[str, Any], now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    with pg_connection() as conn, conn.cursor() as cur:
        results = (
            check_jobs(cur, cfg.get("jobs", {}), now)
            + check_tables(cur, cfg.get("tables", {}), now)
            + check_budgets(cur, cfg.get("budgets", {}))
        )
    for r in results:
        em.emit(r["level"], "health_check", r["message"],
                payload={"check": r["check"], "name": r["name"], **r["payload"]})
    return results


def main() -> None:
    with job_run("monitor_health") as em:
        results = run_checks(em, _load_yaml("monitoring.yaml"))
        failed = [r["name"] for r in results if r["level"] == "ERROR"]
        if failed:
            raise RuntimeError(f"{len(failed)} health check(s) failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
