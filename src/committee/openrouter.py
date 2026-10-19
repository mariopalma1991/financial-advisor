"""The same committee agent loop as agent.run_agent, for any model on OpenRouter
(OpenAI-style chat completions with function calling). A model in
config/committee.yaml or --model written as "openrouter:<vendor/model>" runs
here; needs OPEN_ROUTER_API_KEY. Cost comes from OpenRouter's own usage
accounting, so these models need no entry under pricing.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable

import httpx

from src.committee.agent import MAX_FIXES, AgentResult, check_final, fix_message, tidy
from src.mcp_server import tools

PREFIX = "openrouter:"
URL = "https://openrouter.ai/api/v1/chat/completions"


def is_openrouter(model: str) -> bool:
    return model.startswith(PREFIX)


def _fn(t: dict) -> dict:
    return {"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                             "parameters": t["input_schema"]}}


def _post(http: httpx.Client, body: dict) -> dict:
    """One completion, retried on rate limits and server errors."""
    for attempt in range(4):
        r = http.post(URL, json=body)
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            time.sleep(5 * 2 ** attempt)
            continue
        if r.status_code >= 400:  # OpenRouter says why (no credit, model blocked for the key...) in the body
            raise RuntimeError(f"openrouter {r.status_code}: {r.text[:300]}")
        data = r.json()
        if data.get("error"):
            raise RuntimeError(f"openrouter: {data['error']}")
        return data
    raise RuntimeError("openrouter: retries exhausted")


def run_agent(http: httpx.Client | None, *, role: str, system: str, prompt: str, tool_defs: list[dict],
              final_tool: dict, as_of: str | None, max_calls: int, cfg: dict,
              spend: Callable[[float], None], call_tool: Callable[[str, dict], dict] | None = None) -> AgentResult:
    """Run one agent to its final tool call through OpenRouter. Same rules as
    agent.run_agent: the role's tools only, its call budget, as_of forced in."""
    call_tool = call_tool or (lambda name, args: tools.ALL_TOOLS[name](**args))
    own = http is None
    if own:
        http = httpx.Client(timeout=600, headers={
            "Authorization": f"Bearer {os.environ['OPEN_ROUTER_API_KEY']}",
            "X-Title": "financial-advisor committee"})
    allowed = {t["name"] for t in tool_defs}
    res = AgentResult(role=role, output=None)
    messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    fns = [_fn(t) for t in [*tool_defs, final_tool]]
    nudged = False
    fixes = 0
    try:
        for _ in range(max_calls + 4):
            data = _post(http, {
                "model": cfg["model"][len(PREFIX):], "messages": messages, "tools": fns,
                "max_tokens": cfg["max_tokens"], "reasoning": {"effort": cfg["effort"]},
                "usage": {"include": True}, "provider": {"require_parameters": True}})
            u = data.get("usage") or {}
            res.tokens_in += u.get("prompt_tokens") or 0
            res.tokens_out += u.get("completion_tokens") or 0
            cost = float(u.get("cost") or 0)
            res.cost_usd += cost
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            finish = choice.get("finish_reason")
            res.turns.append({"request": messages[-1], "response": msg, "stop_reason": finish,
                              "model": data.get("model"), "provider": data.get("provider"), "usage": u})
            spend(cost)
            # Keep the whole assistant message (reasoning_details included) so reasoning
            # models can continue their chain across tool calls.
            messages.append({k: v for k, v in msg.items() if k in
                             ("role", "content", "tool_calls", "reasoning", "reasoning_details")})
            calls = msg.get("tool_calls") or []
            final = next((c for c in calls if c["function"]["name"] == final_tool["name"]), None)
            if final is not None:
                try:
                    out = json.loads(final["function"].get("arguments") or "{}")
                    errs = check_final(final_tool, out)
                except json.JSONDecodeError as exc:
                    out, errs = None, [f"JSON inválido: {exc}"]
                if errs and fixes < MAX_FIXES:
                    fixes += 1
                    for c in calls:
                        messages.append({"role": "tool", "tool_call_id": c["id"],
                                         "content": fix_message(final_tool, errs) if c is final else "no se ejecutó"})
                    continue
                if errs:
                    res.error = "bad_format: " + "; ".join(errs[:3])
                else:
                    res.output = tidy(out)
                return res
            if not calls:
                if nudged or finish == "length":
                    res.error = f"no_final_output ({finish})"
                    return res
                nudged = True
                messages.append({"role": "user", "content": f"Llama a {final_tool['name']} con tu análisis."})
                continue
            for c in calls:
                name = c["function"]["name"]
                try:
                    args = json.loads(c["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = None
                if args is None:
                    out: dict = {"error": {"code": "bad_arguments", "message": "argumentos no son JSON válido"}}
                elif name not in allowed:
                    out = {"error": {"code": "not_allowed", "message": f"{name} no es una de tus herramientas"}}
                elif len(res.tool_calls) >= max_calls:
                    out = {"error": {"code": "budget", "message": f"Ya usaste tus {max_calls} llamadas; "
                                                                 f"entrega tu análisis con {final_tool['name']}."}}
                else:
                    args.pop("as_of", None)
                    t0 = time.monotonic()
                    try:
                        out = call_tool(name, {**args, "as_of": as_of})
                    except Exception as exc:  # a tool bug must not end the committee
                        out = {"error": {"code": "tool_failed", "message": f"{type(exc).__name__}: {exc}"}}
                    res.tool_calls.append({"tool": name, "args": args, "ms": round((time.monotonic() - t0) * 1000),
                                           "error": (out.get("error") or {}).get("code")})
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(out, default=str)})
        res.error = "too_many_turns"
        return res
    finally:
        if own:
            http.close()
