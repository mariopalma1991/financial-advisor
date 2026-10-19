"""One committee on one candidate: five members in turn (none sees another), the
validator over their citations, then the chair's approve-or-veto.

Members run one after another, not concurrently: the MCP tools share one
read-only connection per process. Independence comes from separate
conversations, not from timing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.committee import openrouter, prompts
from src.committee.agent import (
    DECISION_TOOL,
    OPINION_TOOL,
    AgentResult,
    BudgetExceeded,
    role_tool_defs,
    run_agent,
)
from src.mcp_server import tools

_ROOT = Path(__file__).resolve().parents[2]


def load_config() -> dict:
    cfg = yaml.safe_load((_ROOT / "config/committee.yaml").read_text())
    roles = yaml.safe_load((_ROOT / "config/mcp_roles.yaml").read_text())["roles"]
    cfg["max_calls"] = {r: roles[r]["max_calls"] for r in [*cfg["committee"]["members"], cfg["committee"]["chair"]]}
    return cfg


def cites_of(output: dict | None) -> list[dict]:
    """Every {"cite", "value"} an agent's final output carries."""
    if not output:
        return []
    found = [c for kp in output.get("key_points") or [] for c in kp.get("cites") or []]
    found += list(output.get("cites") or [])
    for c in found:  # some models copy the placeholder too: "<_ref>table:KEY#field"
        if isinstance(c.get("cite"), str) and c["cite"].startswith("<_ref>"):
            c["cite"] = c["cite"][len("<_ref>"):]
    return found


def validate(results: list[AgentResult], as_of: str | None) -> dict[str, dict]:
    """resolve_refs over each agent's citations: per role, the checked count,
    mismatches, unresolved cites and the detail of each one that failed."""
    out = {}
    for r in results:
        cites = [c for c in cites_of(r.output) if c.get("cite")]
        if not cites:
            out[r.role] = {"n": 0, "checked": 0, "mismatches": 0, "unresolved": 0, "failed": []}
            continue
        ans = tools.resolve_refs(cites, as_of=as_of)
        rows = (ans.get("data") or {}).get("results") or []
        s = (ans.get("data") or {}).get("summary") or {}
        failed = [x for x in rows if x.get("match") is False or x.get("status") not in ("found", "row_found")]
        out[r.role] = {"n": len(cites), "checked": s.get("checked", 0), "mismatches": s.get("mismatches", 0),
                       "unresolved": s.get("unresolved", 0), "failed": failed}
    return out


def price_now(ticker: str, as_of: str | None) -> dict[str, Any]:
    """Last close in USD and the USD/MXN rate the committee sees, with their refs."""
    ov = tools.get_ticker_overview(ticker, as_of=as_of)
    bar = (ov.get("data") or {}).get("last_bar") or {}
    fx = (tools.get_cost_model(as_of=as_of).get("data") or {}).get("usd_mxn") or {}
    usd, rate = bar.get("close"), fx.get("value")
    return {"price_usd": usd, "usd_mxn": rate, "price_mxn": round(usd * rate, 2) if usd and rate else None,
            "bar_date": bar.get("bar_date"), "fx_date": fx.get("obs_date"),
            "refs": [f"{bar.get('_ref')}#close", f"{fx.get('_ref')}#value"]}


@dataclass
class CommitteeResult:
    ticker: str
    as_of: str | None
    price: dict
    members: list[AgentResult] = field(default_factory=list)
    chair: AgentResult | None = None
    validation: dict[str, dict] = field(default_factory=dict)
    error: str | None = None

    @property
    def cost_usd(self) -> float:
        return sum(r.cost_usd for r in [*self.members, *([self.chair] if self.chair else [])])


def _brief(r: AgentResult, v: dict) -> dict:
    """What the chair reads of one member: only citations the validator confirmed.
    A key point whose every citation failed is dropped; the counts stay, so the
    chair can weigh members by how much of their evidence held up."""
    o = r.output or {}
    failed = {f.get("cite") for f in v.get("failed", [])}
    points, dropped = [], 0
    for kp in o.get("key_points") or []:
        cites = kp.get("cites") or []
        ok = [c for c in cites if c.get("cite") not in failed]
        if cites and not ok:
            dropped += 1
            continue
        points.append({**kp, "cites": ok})
    n = v.get("n", 0)
    verified = n - len(failed)
    return {"rol": r.role, "postura": o.get("stance"), "score": o.get("score"), "confianza": o.get("confidence"),
            "movimiento_esperado_pct": o.get("expected_move_pct"), "stop_pct": o.get("stop_pct"),
            "tesis": o.get("thesis"), "puntos": points, "riesgos": o.get("risks"),
            "cambiaria_de_opinion_si": o.get("change_my_mind"), "datos_faltantes": o.get("missing_data"),
            "evidencia": {"citas_verificadas": verified, "citas_descartadas": len(failed),
                          "puntos_descartados": dropped,
                          "calidad": round(verified / n, 2) if n else None},
            **({"error": r.error} if r.error else {})}


def run(client: Any, ticker: str, as_of: str | None, cfg: dict, context: str = "") -> CommitteeResult:
    c = cfg["committee"]
    via_or = openrouter.is_openrouter(c["model"])
    prices = None if via_or else cfg["pricing"][c["model"]]

    def agent(**kw: Any) -> AgentResult:
        if via_or:
            return openrouter.run_agent(None, cfg=c, spend=spend, **kw)
        return run_agent(client, cfg=c, prices=prices, spend=spend, **kw)
    ticker = ticker.upper()
    horizon = c["horizon_months"]
    price = price_now(ticker, as_of)
    res = CommitteeResult(ticker=ticker, as_of=as_of, price=price)
    spent = [0.0]

    def spend(usd: float) -> None:
        spent[0] += usd
        if spent[0] > c["max_usd_per_run"]:
            raise BudgetExceeded(f"{ticker}: {spent[0]:.2f} USD > max_usd_per_run {c['max_usd_per_run']}")

    day = as_of or "hoy (datos más recientes)"
    px = (f"Precio: {price['price_usd']} USD (cierre {price['bar_date']}), {price['price_mxn']} MXN "
          f"con USD/MXN {price['usd_mxn']}.") if price["price_mxn"] else "Precio: sin datos."
    prompt = f"Analiza {ticker} con datos al {day}. {px}\n{context}".strip()
    try:
        for role in c["members"]:
            res.members.append(agent(
                role=role, system=prompts.member_system(role, ticker, horizon), prompt=prompt,
                tool_defs=role_tool_defs(role), final_tool=OPINION_TOOL, as_of=as_of,
                max_calls=cfg["max_calls"][role]))
        res.validation = validate(res.members, as_of)
        briefs = [_brief(r, res.validation.get(r.role, {})) for r in res.members]
        chair_prompt = (f"{prompt}\n\nOpiniones de los miembros (JSON):\n"
                        f"{json.dumps(briefs, ensure_ascii=False, default=str)}")
        res.chair = agent(
            role=c["chair"], system=prompts.chair_system(ticker, horizon), prompt=chair_prompt,
            tool_defs=role_tool_defs(c["chair"]), final_tool=DECISION_TOOL, as_of=as_of,
            max_calls=cfg["max_calls"][c["chair"]])
        res.validation |= validate([res.chair], as_of)
        if res.chair.output is None:
            res.error = f"chair: {res.chair.error}"
    except BudgetExceeded as exc:
        res.error = f"budget: {exc}"
    return res
