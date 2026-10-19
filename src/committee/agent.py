"""One committee agent: a Claude tool-use loop over its role's MCP tools.

Every tool call runs in-process against src/mcp_server/tools.py with the run's
as_of forced in (agents never choose the date, so a run replays exactly), the
role's call budget enforced, and the whole exchange kept as the agent's trace.
The loop ends when the agent calls its final tool (entregar_opinion for members,
entregar_decision for the chair), whose strict schema is the agent's output.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import jsonschema

from src.mcp_server import server, tools

FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Agents never type a number from the data: they write the reference itself as a
# marker, {<_ref>#<field>} or {<_ref>#<field>!pct}, and the runner writes the stored
# value in its place, so no number the reader sees is copied by a model (prompt v0.7).
_TEXTS = {"type": "array", "items": {"type": "string"}}
_NUM = {"type": ["number", "null"]}
MAX_ITEMS = 5

OPINION_TOOL = {
    "name": "entregar_opinion",
    "description": "Entrega tu análisis final. Llámala una sola vez, al terminar.",
    "strict": True,
    "input_schema": {
        "type": "object", "additionalProperties": False,
        "required": ["stance", "score", "confidence", "expected_move_pct", "stop_pct", "thesis",
                     "key_points", "risks", "change_my_mind", "missing_data"],
        "properties": {
            "stance": {"type": "string", "enum": ["buy", "hold", "sell", "no_view"],
                       "description": "buy = comprar ahora; hold = esperar; sell = evitar; no_view = sin opinión"},
            "score": {"type": "integer", "description": "1 = muy bajista ... 10 = muy alcista (misma escala para todos)"},
            "confidence": {"type": "number", "description": "0 a 1"},
            "expected_move_pct": {**_NUM, "description": "movimiento esperado en el horizonte, en %, o null"},
            "stop_pct": {**_NUM, "description": "caída en % bajo el precio que invalidaría la tesis, o null"},
            "thesis": {"type": "string", "description": "tu razonamiento en un párrafo, máximo 150 palabras, cifras solo con marcadores {ref#campo}"},
            "key_points": {**_TEXTS, "description": f"máximo {MAX_ITEMS}, una oración cada uno, cifras con marcadores {{ref#campo}}"},
            "risks": {**_TEXTS, "description": f"máximo {MAX_ITEMS}, cifras con marcadores {{ref#campo}}"},
            "change_my_mind": {**_TEXTS, "description": "2 o 3 cosas que te harían cambiar de opinión"},
            "missing_data": {**_TEXTS, "description": "datos que te faltaron"},
        },
    },
}

DECISION_TOOL = {
    "name": "entregar_decision",
    "description": "Entrega la decisión final del comité. Llámala una sola vez, al terminar.",
    "strict": True,
    "input_schema": {
        "type": "object", "additionalProperties": False,
        "required": ["decision", "score", "confidence", "expected_move_pct", "target_pct", "stop_pct",
                     "veto_reason", "rationale", "agreements", "disagreements", "invalidation"],
        "properties": {
            "decision": {"type": "string", "enum": ["approve", "veto"]},
            "score": {"type": "integer", "description": "1 = muy bajista ... 10 = muy alcista"},
            "confidence": {"type": "number", "description": "convicción, 0 a 1"},
            "expected_move_pct": {**_NUM, "description": "movimiento esperado en el horizonte, en %"},
            "target_pct": {**_NUM, "description": "objetivo en % sobre el precio actual"},
            "stop_pct": {**_NUM, "description": "stop en % bajo el precio actual (número positivo)"},
            "veto_reason": {"type": ["string", "null"], "description": "por qué se veta; null si se aprueba"},
            "rationale": {"type": "string", "description": "razonamiento para el inversionista, en español, máximo 200 palabras, cifras con marcadores {ref#campo}"},
            "agreements": _TEXTS,
            "disagreements": _TEXTS,
            "invalidation": {**_TEXTS, "description": "precio, fecha o dato concreto que invalida la decisión"},
        },
    },
}

MARKER = re.compile(r"\{([^{}\s!]+#[^{}\s!]+)(?:!(\w+))?\}")
FORMATS = {None, "pct", "mill", "fecha"}
_NUMBERED = re.compile(r"\{\d+(?::\w+)?\}")  # the v0.6 style, {1} or {2:pct}


def texts_of(out: dict) -> list[str]:
    """Every free-text field of a final output, where markers may appear."""
    found = []
    for v in out.values():
        if isinstance(v, str):
            found.append(v)
        elif isinstance(v, list):
            found += [x for x in v if isinstance(x, str)]
    return found


def check_final(tool: dict, out: Any) -> list[str]:
    """Why a final output does not follow the standard format, so the agent can
    fix it once. Same check for every model: some OpenRouter providers do not
    enforce the schema."""
    if not isinstance(out, dict):
        return ["la respuesta debe ser un objeto JSON"]
    errs = [f"{'/'.join(map(str, e.absolute_path)) or 'raíz'}: {e.message}"
            for e in jsonschema.Draft202012Validator(tool["input_schema"]).iter_errors(out)][:8]
    if errs:
        return errs
    for t in texts_of(out):
        for m in MARKER.finditer(t):
            if m.group(2) not in FORMATS:
                errs.append(f"formato desconocido en {m.group(0)}: usa !pct, !mill o !fecha, o nada")
        for bad in _NUMBERED.findall(t):
            errs.append(f"{bad} no es un marcador: escribe la referencia completa, {{tabla:CLAVE#campo}}")
    if not 1 <= out["score"] <= 10:
        errs.append("score va de 1 a 10")
    if not 0 <= out["confidence"] <= 1:
        errs.append("confidence va de 0 a 1")
    return errs


MAX_FIXES = 2


def fix_message(tool: dict, errs: list[str]) -> str:
    return f"Formato incorrecto, corrige y vuelve a llamar {tool['name']}: " + "; ".join(errs)


def tidy(out: dict) -> dict:
    """Keep the length limits the prompt asks for."""
    return {**out, **{k: out[k][:MAX_ITEMS] for k in ("key_points", "risks") if isinstance(out.get(k), list)}}


class BudgetExceeded(RuntimeError):
    pass


def role_tool_defs(role: str) -> list[dict]:
    """The role's MCP tools as Claude tool definitions, without as_of (the runner sets it)."""
    out = []
    for t in asyncio.run(server.build(role).list_tools()):
        schema = json.loads(json.dumps(t.inputSchema))
        schema.get("properties", {}).pop("as_of", None)
        if "required" in schema:
            schema["required"] = [r for r in schema["required"] if r != "as_of"]
        schema.pop("title", None)
        out.append({"name": t.name, "description": t.description or "", "input_schema": schema})
    return out


def usage_cost(usage: Any, prices: dict[str, float]) -> float:
    """USD of one response from its usage (cache writes and reads priced apart)."""
    def g(k: str) -> int:
        return getattr(usage, k, None) or 0
    return (g("input_tokens") * prices["input"] + g("output_tokens") * prices["output"]
            + g("cache_creation_input_tokens") * prices["cache_write"]
            + g("cache_read_input_tokens") * prices["cache_read"]) / 1e6


@dataclass
class AgentResult:
    role: str
    output: dict | None
    tool_calls: list[dict] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    turns: list[dict] = field(default_factory=list)  # the trace: every request's new messages and response
    error: str | None = None
    draft: dict | None = None  # the final output as the agent wrote it, markers unfilled


def _block(b: Any) -> dict:
    return b.model_dump(mode="json", exclude_none=True) if hasattr(b, "model_dump") else dict(b)


def run_agent(client: Any, *, role: str, system: str, prompt: str, tool_defs: list[dict], final_tool: dict,
              as_of: str | None, max_calls: int, cfg: dict, prices: dict[str, float],
              spend: Callable[[float], None], call_tool: Callable[[str, dict], dict] | None = None) -> AgentResult:
    """Run one agent to its final tool call. `spend` gets each response's cost and
    raises BudgetExceeded when the run's cap is crossed."""
    call_tool = call_tool or (lambda name, args: tools.ALL_TOOLS[name](**args))
    allowed = {t["name"] for t in tool_defs}
    res = AgentResult(role=role, output=None)
    messages: list[dict] = [{"role": "user", "content": prompt}]
    nudged = False
    fixes = 0
    for _ in range(max_calls + 4):
        resp = client.beta.messages.create(
            model=cfg["model"], max_tokens=cfg["max_tokens"],
            system=[{"type": "text", "text": system}],
            tools=[*tool_defs, final_tool], messages=messages,
            output_config={"effort": cfg["effort"]}, cache_control={"type": "ephemeral"},
            betas=[FALLBACK_BETA], fallbacks="default")
        u = resp.usage
        res.tokens_in += (u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0) \
            + (getattr(u, "cache_creation_input_tokens", 0) or 0)
        res.tokens_out += u.output_tokens or 0
        cost = usage_cost(u, prices)
        res.cost_usd += cost
        res.turns.append({"request": messages[-1], "response": [_block(b) for b in resp.content],
                          "stop_reason": resp.stop_reason, "model": resp.model, "usage": _block(u)})
        spend(cost)
        if resp.stop_reason == "refusal":
            res.error = "refusal"
            return res
        messages.append({"role": "assistant", "content": resp.content})
        uses = [b for b in resp.content if b.type == "tool_use"]
        final = next((b for b in uses if b.name == final_tool["name"]), None)
        if final is not None:
            out = dict(final.input)
            errs = check_final(final_tool, out)
            if errs and fixes < MAX_FIXES:
                fixes += 1
                messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                     "content": fix_message(final_tool, errs) if b is final else "no se ejecutó"} for b in uses]})
                continue
            if errs:
                res.error = "bad_format: " + "; ".join(errs[:3])
                return res
            res.output = tidy(out)
            return res
        if not uses:
            if nudged or resp.stop_reason == "max_tokens":
                res.error = f"no_final_output ({resp.stop_reason})"
                return res
            nudged = True
            messages.append({"role": "user", "content": f"Llama a {final_tool['name']} con tu análisis."})
            continue
        results = []
        for b in uses:
            args = dict(b.input or {})
            if b.name not in allowed:
                out: dict = {"error": {"code": "not_allowed", "message": f"{b.name} no es una de tus herramientas"}}
            elif len(res.tool_calls) >= max_calls:
                out = {"error": {"code": "budget", "message": f"Ya usaste tus {max_calls} llamadas; "
                                                             f"entrega tu análisis con {final_tool['name']}."}}
            else:
                args.pop("as_of", None)
                t0 = time.monotonic()
                try:
                    out = call_tool(b.name, {**args, "as_of": as_of})
                except Exception as exc:  # a tool bug must not end the committee
                    out = {"error": {"code": "tool_failed", "message": f"{type(exc).__name__}: {exc}"}}
                res.tool_calls.append({"tool": b.name, "args": args, "ms": round((time.monotonic() - t0) * 1000),
                                       "error": (out.get("error") or {}).get("code")})
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(out, default=str),
                            **({"is_error": True} if "error" in out else {})})
        messages.append({"role": "user", "content": results})
    res.error = "too_many_turns"
    return res
