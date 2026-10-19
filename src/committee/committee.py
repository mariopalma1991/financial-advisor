"""One committee on one candidate: five members in turn (none sees another), a
rebuttal round where each reads the others and gives its final view, the validator
over their citations, then the chair's approve-or-veto. The data most members ask
for first is fetched once per candidate and handed to each member whose role may
call that tool (shared_data in config/committee.yaml), so they need fewer turns.

Members run one after another, not concurrently: the MCP tools share one
read-only connection per process. Independence comes from separate
conversations, not from timing.
"""
from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.committee import openrouter, prompts
from src.committee.agent import (
    DECISION_TOOL,
    MARKER,
    OPINION_TOOL,
    AgentResult,
    BudgetExceeded,
    role_tool_defs,
    run_agent,
    texts_of,
)
from src.mcp_server import tools

_ROOT = Path(__file__).resolve().parents[2]


def load_config() -> dict:
    cfg = yaml.safe_load((_ROOT / "config/committee.yaml").read_text())
    roles = yaml.safe_load((_ROOT / "config/mcp_roles.yaml").read_text())["roles"]
    cfg["max_calls"] = {r: roles[r]["max_calls"] for r in [*cfg["committee"]["members"], cfg["committee"]["chair"]]}
    return cfg


def _clean(ref: str) -> str:
    return ref.strip().removeprefix("<_ref>")  # some models copy the placeholder too


def repair(cite: str, seen: dict[str, dict]) -> str:
    """The reference an agent meant, when it copied one slightly wrong: the same
    reference with its calc: prefix, or else the only row it was shown for that
    ticker that carries the field. Anything less certain stays as written (and
    fails)."""
    ref, _, path = cite.partition("#")
    if ref in seen or not path:
        return cite
    if "calc:" + ref in seen:
        return f"calc:{ref}#{path}"
    ticker = ref.partition(":")[2].split("|")[0]
    hits = [r for r, row in seen.items()
            if ticker and ticker in r.partition(":")[2].split("|") and tools._field(row, path)[0]]
    return f"{hits[0]}#{path}" if len(hits) == 1 else cite


def cites_of(output: dict | None) -> list[str]:
    """The references an agent's text cites through its markers, first use first."""
    found = [_clean(m.group(1)) for t in texts_of(output or {}) for m in MARKER.finditer(t)]
    return list(dict.fromkeys(found))


def fmt(value: Any, kind: str | None, field: str = "") -> str:
    """A looked-up value as a person reads it: !pct a fraction as % (a *pct* field
    already in percent, like avg_surprise_pct 443.5, is not multiplied), !mill in millions, !fecha
    or text as stored, and a bare number rounded to what matters."""
    if value is None:
        return "[sin dato]"
    if isinstance(value, bool) or not isinstance(value, (int, float)) or kind == "fecha":
        return str(value)
    if kind == "pct":
        in_pct = "pct" in field.rsplit(".", 1)[-1] and abs(value) >= 1  # pct_from_high stays a fraction
        return f"{value:.1f}%" if in_pct else f"{value * 100:.1f}%"
    if kind == "mill":
        return f"{value / 1e6:,.1f} millones"
    if float(value).is_integer():
        return f"{int(value):,}"
    a = abs(value)
    return f"{value:,.1f}" if a >= 100 else f"{value:.2f}" if a >= 1 else f"{value:.4f}".rstrip("0")


def render(text: str, values: dict[str, Any]) -> str:
    """The text with each marker replaced by the value the database holds."""
    def one(m: re.Match) -> str:
        ref = _clean(m.group(1))
        return fmt(values.get(ref), m.group(2), ref.partition("#")[2])
    return MARKER.sub(one, text)


def misses(text: str, values: dict[str, Any]) -> bool:
    """Whether the text uses a reference that did not resolve to a value."""
    return any(values.get(_clean(m.group(1))) is None for m in MARKER.finditer(text))


_LOOSE = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)?(?![\w])")
_PROSE = ("thesis", "key_points", "risks", "rationale", "veto_reason")


def loose_numbers(text: str) -> int:
    """Numbers an agent typed itself instead of citing (dates and years count too)."""
    return len(_LOOSE.findall(MARKER.sub("", text)))


def validate(results: list[AgentResult], as_of: str | None) -> dict[str, dict]:
    """resolve_refs over each agent's references: per role, how many resolved to a
    single value, the ones that did not (with why) and the value behind each."""
    out = {}
    for r in results:
        o = r.draft if r.draft is not None else r.output
        cites = cites_of(o)
        fixed = {c: repair(c, r.seen) for c in cites}
        found: dict[str, Any] = {}
        failed: list[dict] = []
        if cites:
            ans = tools.resolve_refs(list(dict.fromkeys(fixed.values())), as_of=as_of)
            by_ref = {x.get("cite"): x for x in (ans.get("data") or {}).get("results") or []}
            for c in cites:
                x = by_ref.get(fixed[c]) or {"cite": c, "status": "ref_not_found"}
                v = x.get("value")
                if x.get("status") == "found" and v is not None and not isinstance(v, (dict, list)):
                    found[c] = v
                else:  # a whole object (e.g. #returns instead of #returns.6m) is not one number
                    failed.append({k: x.get(k) for k in ("cite", "status", "detail") if x.get(k) is not None}
                                  | ({"status": "not_a_value"} if x.get("status") == "found" else {}))
        prose = [t for k in _PROSE for t in texts_of({k: (o or {}).get(k)})]
        out[r.role] = {"n": len(cites), "checked": len(found), "mismatches": 0, "unresolved": len(failed),
                       "repaired": {c: f for c, f in fixed.items() if f != c},
                       "loose_numbers": sum(loose_numbers(t) for t in prose), "failed": failed, "values": found}
    return out


def fill(r: AgentResult, v: dict) -> None:
    """Write the looked-up values into the agent's output: every marker becomes the
    stored number, and cites lists each reference with its value. The agent's own
    version stays in draft."""
    if r.output is None:
        return
    r.draft = r.output
    values = v.get("values") or {}
    out: dict[str, Any] = {}
    for k, x in r.draft.items():
        if isinstance(x, str):
            out[k] = render(x, values)
        elif isinstance(x, list):
            out[k] = [render(t, values) if isinstance(t, str) else t for t in x]
        else:
            out[k] = x
    out["cites"] = [{"cite": c, "value": values.get(c)} for c in cites_of(r.draft)]
    r.output = out


def price_now(ticker: str, as_of: str | None) -> dict[str, Any]:
    """Last close in USD and the USD/MXN rate the committee sees, with their refs."""
    ov = tools.get_ticker_overview(ticker, as_of=as_of)
    bar = (ov.get("data") or {}).get("last_bar") or {}
    fx = (tools.get_cost_model(as_of=as_of).get("data") or {}).get("usd_mxn") or {}
    usd, rate = bar.get("close"), fx.get("value")
    return {"price_usd": usd, "usd_mxn": rate, "price_mxn": round(usd * rate, 2) if usd and rate else None,
            "bar_date": bar.get("bar_date"), "fx_date": fx.get("obs_date"),
            "refs": [f"{bar.get('_ref')}#close", f"{fx.get('_ref')}#value"]}


def shared_data(ticker: str, as_of: str | None, names: list[str]) -> dict[str, dict]:
    """The answers most members ask for first, fetched once per candidate with each
    tool's default arguments (tool -> answer)."""
    out = {}
    for name in names:
        fn = tools.ALL_TOOLS[name]
        kw = {"ticker": ticker} if "ticker" in inspect.signature(fn).parameters else {}
        try:
            out[name] = fn(**kw, as_of=as_of)
        except Exception as exc:  # noqa: BLE001 -- a member can still ask for it itself
            out[name] = {"error": {"code": "tool_failed", "message": f"{type(exc).__name__}: {exc}"}}
    return out


def with_data(prompt: str, data: dict[str, dict]) -> str:
    if not data:
        return prompt
    return (f"{prompt}\n\nYa consulté para ti estas herramientas con sus parámetros por defecto; no las vuelvas "
            f"a pedir así (sí con otros parámetros, o las demás). Usa sus _ref en tus marcadores igual que si "
            f"las hubieras pedido tú. Respuestas (JSON):\n{json.dumps(data, ensure_ascii=False, default=str)}")


def handed(r: AgentResult, data: dict[str, dict], ticker: str) -> None:
    """Count the data handed to a member as seen and in its tool calls, marked."""
    for ans in data.values():
        tools._index_refs(ans.get("data"), r.seen)
    r.tool_calls[:0] = [{"tool": n, "args": {"ticker": ticker}, "prefetched": True,
                         "error": (a.get("error") or {}).get("code")} for n, a in data.items()]


def rebuttal_prompt(prompt: str, own: dict, others: list[dict]) -> str:
    return (f"{prompt}\n\nTu análisis (con tus marcadores):\n{json.dumps(own, ensure_ascii=False, default=str)}"
            f"\n\nOpiniones de los otros miembros (las cifras ya las puso el sistema):\n"
            f"{json.dumps(others, ensure_ascii=False, default=str)}")


@dataclass
class CommitteeResult:
    ticker: str
    as_of: str | None
    price: dict
    members: list[AgentResult] = field(default_factory=list)
    rebuttals: list[AgentResult] = field(default_factory=list)  # each member's final view, role <role>_replica
    chair: AgentResult | None = None
    validation: dict[str, dict] = field(default_factory=dict)
    error: str | None = None
    shared: list[str] = field(default_factory=list)

    @property
    def agents(self) -> list[AgentResult]:
        return [*self.members, *self.rebuttals, *([self.chair] if self.chair else [])]

    @property
    def cost_usd(self) -> float:
        return sum(r.cost_usd for r in self.agents)


REPLICA = "_replica"


def final_brief(first: AgentResult, second: AgentResult | None, validation: dict[str, dict]) -> dict:
    """The chair's view of a member: its view after the rebuttal round when there is
    one, with the stance it held before."""
    if second is None or second.output is None:
        return _brief(first, validation.get(first.role, {}))
    b = _brief(second, validation.get(second.role, {}))
    return {**b, "rol": first.role, "postura_inicial": (first.draft or first.output or {}).get("stance")}


def _brief(r: AgentResult, v: dict) -> dict:
    """What the chair reads of one member: its text with the database's numbers in it.
    A key point or risk that needs a reference that did not verify is dropped; the
    counts stay, so the chair can weigh members by how much of their evidence held up."""
    d = r.draft or {}
    values = v.get("values") or {}
    kept: dict[str, list[str]] = {}
    dropped = 0
    for k in ("key_points", "risks"):
        kept[k] = [render(t, values) for t in d.get(k) or [] if not misses(t, values)]
        dropped += len(d.get(k) or []) - len(kept[k])
    n = v.get("n", 0)
    ok = v.get("checked", 0)
    refs = {c: values[c] for c in cites_of(d) if c in values}
    return {"rol": r.role, "postura": d.get("stance"), "score": d.get("score"), "confianza": d.get("confidence"),
            "movimiento_esperado_pct": d.get("expected_move_pct"), "stop_pct": d.get("stop_pct"),
            "tesis": render(d.get("thesis") or "", values), "puntos": kept["key_points"], "riesgos": kept["risks"],
            "cambiaria_de_opinion_si": d.get("change_my_mind"), "datos_faltantes": d.get("missing_data"),
            "referencias": [{"cite": c, "value": val} for c, val in refs.items()],
            "evidencia": {"citas_verificadas": ok, "citas_descartadas": v.get("unresolved", 0),
                          "puntos_descartados": dropped, "cifras_sin_cita": v.get("loose_numbers", 0),
                          "calidad": round(ok / n, 2) if n else None},
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
    data = shared_data(ticker, as_of, c.get("shared_data") or [])
    res.shared = list(data)
    try:
        for role in c["members"]:
            defs = role_tool_defs(role)
            mine = {n: a for n, a in data.items() if n in {t["name"] for t in defs}}
            r = agent(role=role, system=prompts.member_system(role, ticker, horizon), prompt=with_data(prompt, mine),
                      tool_defs=defs, final_tool=OPINION_TOOL, as_of=as_of, max_calls=cfg["max_calls"][role])
            handed(r, mine, ticker)
            res.members.append(r)
        res.validation = validate(res.members, as_of)
        for r in res.members:
            fill(r, res.validation.get(r.role, {}))
        replies: dict[str, AgentResult] = {}
        if c.get("rebuttal"):
            briefs = {r.role: _brief(r, res.validation.get(r.role, {})) for r in res.members if r.output}
            for r in res.members:
                if r.output is None:
                    continue
                others = [b for k, b in briefs.items() if k != r.role]
                rr = agent(role=r.role + REPLICA, system=prompts.rebuttal_system(r.role, ticker, horizon),
                           prompt=rebuttal_prompt(prompt, r.draft or {}, others), tool_defs=[],
                           final_tool=OPINION_TOOL, as_of=as_of, max_calls=0)
                rr.seen = dict(r.seen)
                res.rebuttals.append(rr)
                replies[r.role] = rr
            res.validation |= validate(res.rebuttals, as_of)
            for rr in res.rebuttals:
                fill(rr, res.validation.get(rr.role, {}))
        briefs = [final_brief(r, replies.get(r.role), res.validation) for r in res.members]
        chair_prompt = (f"{prompt}\n\nOpiniones de los miembros (JSON):\n"
                        f"{json.dumps(briefs, ensure_ascii=False, default=str)}")
        res.chair = agent(
            role=c["chair"], system=prompts.chair_system(ticker, horizon, bool(c.get("rebuttal"))), prompt=chair_prompt,
            tool_defs=role_tool_defs(c["chair"]), final_tool=DECISION_TOOL, as_of=as_of,
            max_calls=cfg["max_calls"][c["chair"]])
        res.validation |= validate([res.chair], as_of)
        fill(res.chair, res.validation[res.chair.role])
        if res.chair.output is None:
            res.error = f"chair: {res.chair.error}"
    except BudgetExceeded as exc:
        res.error = f"budget: {exc}"
    return res
