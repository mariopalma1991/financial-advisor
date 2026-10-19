from types import SimpleNamespace as NS

import pytest

from src.committee import agent, committee, store
from src.committee.agent import DECISION_TOOL, OPINION_TOOL, BudgetExceeded, run_agent

CFG = {"model": "claude-sonnet-5-5", "max_tokens": 1000, "effort": "medium"}
PRICES = {"input": 4.0, "output": 20.0, "cache_write": 5.0, "cache_read": 0.2}


def _usage(i=1000, o=100):
    return NS(input_tokens=i, output_tokens=o, cache_creation_input_tokens=0, cache_read_input_tokens=0,
              model_dump=lambda **_: {"input_tokens": i, "output_tokens": o})


def _use(name, inp, i="tu1"):
    return NS(type="tool_use", name=name, input=inp, id=i, model_dump=lambda **_: {"type": "tool_use", "name": name})


def _text(t):
    return NS(type="text", text=t, model_dump=lambda **_: {"type": "text", "text": t})


class FakeClient:
    """Replays scripted responses and records each request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.requests.append({**kw, "messages": list(kw["messages"])})
        content, stop = self.responses.pop(0)
        return NS(content=content, stop_reason=stop, model=kw["model"], usage=_usage())


OPINION = {"stance": "buy", "score": 7, "confidence": 0.6, "expected_move_pct": 12, "stop_pct": 8,
           "thesis": "Crece.", "key_points": ["ROE de {1:pct}"], "risks": [], "change_my_mind": [],
           "missing_data": [], "cites": ["x:AAPL#roe"]}
TOOLS = [{"name": "get_fundamentals", "description": "", "input_schema": {"type": "object"}}]


def _run(client, call_tool, max_calls=2, spend=lambda c: None, as_of="2026-10-16"):
    return run_agent(client, role="optimista", system="s", prompt="p", tool_defs=TOOLS, final_tool=OPINION_TOOL,
                     as_of=as_of, max_calls=max_calls, cfg=CFG, prices=PRICES, spend=spend, call_tool=call_tool)


def test_agent_forces_as_of_and_returns_final_output():
    calls = []
    client = FakeClient([([_use("get_fundamentals", {"ticker": "AAPL", "as_of": "2030-01-01"})], "tool_use"),
                         ([_use("entregar_opinion", OPINION, "tu2")], "tool_use")])
    res = _run(client, lambda n, a: calls.append((n, a)) or {"data": {}})
    assert calls == [("get_fundamentals", {"ticker": "AAPL", "as_of": "2026-10-16"})]
    assert res.output == OPINION and res.error is None
    assert res.tool_calls[0]["tool"] == "get_fundamentals" and "as_of" not in res.tool_calls[0]["args"]
    assert res.cost_usd == pytest.approx(2 * (1000 * 4 + 100 * 20) / 1e6)
    # the tool result goes back in one user message, after the assistant turn
    last = client.requests[1]["messages"]
    assert last[-2]["role"] == "assistant" and last[-1]["content"][0]["type"] == "tool_result"
    assert client.requests[0]["fallbacks"] == "default" and client.requests[0]["output_config"] == {"effort": "medium"}


def test_agent_enforces_call_budget_and_role_tools():
    calls = []
    client = FakeClient([([_use("get_fundamentals", {"ticker": "A"}, "a"), _use("get_fundamentals", {"ticker": "A"}, "b"),
                           _use("get_portfolio", {}, "c")], "tool_use"),
                         ([_use("entregar_opinion", OPINION, "d")], "tool_use")])
    res = _run(client, lambda n, a: calls.append(n) or {"data": {}}, max_calls=1)
    assert calls == ["get_fundamentals"]
    results = client.requests[1]["messages"][-1]["content"]
    assert [r.get("is_error", False) for r in results] == [False, True, True]
    assert "budget" in results[1]["content"] and "not_allowed" in results[2]["content"]
    assert res.output == OPINION


def test_agent_nudges_once_then_gives_up():
    client = FakeClient([([_text("listo")], "end_turn"), ([_text("ya")], "end_turn")])
    res = _run(client, lambda n, a: {})
    assert res.output is None and res.error.startswith("no_final_output")
    assert "entregar_opinion" in client.requests[1]["messages"][-1]["content"]


def test_agent_tool_exception_becomes_error_result():
    def boom(n, a):
        raise ValueError("db down")
    client = FakeClient([([_use("get_fundamentals", {"ticker": "A"})], "tool_use"),
                         ([_use("entregar_opinion", OPINION, "z")], "tool_use")])
    res = _run(client, boom)
    assert res.tool_calls[0]["error"] == "tool_failed"
    assert client.requests[1]["messages"][-1]["content"][0]["is_error"] is True


def test_budget_cap_stops_the_agent():
    def spend(c):
        raise BudgetExceeded("cap")
    client = FakeClient([([_use("entregar_opinion", OPINION)], "tool_use")])
    with pytest.raises(BudgetExceeded):
        _run(client, lambda n, a: {}, spend=spend)


def test_final_tool_schemas_are_strict():
    for t in (OPINION_TOOL, DECISION_TOOL):
        s = t["input_schema"]
        assert t["strict"] and s["additionalProperties"] is False
        assert set(s["required"]) == set(s["properties"])


def test_role_tool_defs_hide_as_of():
    defs = agent.role_tool_defs("optimista")
    assert {d["name"] for d in defs} >= {"get_fundamentals", "get_price_technicals"}
    assert all("as_of" not in d["input_schema"].get("properties", {}) for d in defs)
    assert all("as_of" not in d["input_schema"].get("required", []) for d in defs)


def test_cites_of_reads_references_in_order():
    assert committee.cites_of(OPINION) == ["x:AAPL#roe"]
    assert committee.cites_of({"cites": ["<_ref>a:B|1#roe", {"cite": "c:D#x", "value": 2}]}) == ["a:B|1#roe", "c:D#x"]
    assert committee.cites_of(None) == []


def test_render_writes_stored_values_into_markers():
    vals = [0.4282, 22822500.0, "2026-11-04", 2.5, None]
    text = "Ingresos +{1:pct}, ventas {2:mill} USD, reporte {3:fecha}, beta {4}, otro {5}"
    assert committee.render(text, vals) == ("Ingresos +42.8%, ventas 22.8 millones USD, reporte 2026-11-04, "
                                            "beta 2.50, otro [sin dato]")
    assert committee.misses(text, vals) and not committee.misses("beta {4}", vals)
    assert committee.loose_numbers("beta {4} y margen 41.6% en 2026") == 2


def test_check_final_catches_bad_markers_and_shape():
    assert agent.check_final(OPINION_TOOL, OPINION) == []
    bad = {**OPINION, "key_points": ["ROE {2:pct}", "x {1:usd}"]}
    errs = agent.check_final(OPINION_TOOL, bad)
    assert any("{2:pct}" in e for e in errs) and any("{1:usd}" in e for e in errs)
    assert agent.check_final(OPINION_TOOL, {**OPINION, "cites": [{"cite": "x"}]})
    assert agent.check_final(OPINION_TOOL, {**OPINION, "score": 11}) == ["score va de 1 a 10"]


def test_agent_asks_once_to_fix_the_format():
    client = FakeClient([([_use("entregar_opinion", {**OPINION, "key_points": ["{3}"]}, "a")], "tool_use"),
                         ([_use("entregar_opinion", OPINION, "b")], "tool_use")])
    res = _run(client, lambda n, a: {})
    assert res.output == OPINION and res.error is None
    fix = client.requests[1]["messages"][-1]["content"][0]
    assert fix["is_error"] and "{3}" in fix["content"]


def _result(decision="veto", target=20, stop=10):
    chair = agent.AgentResult(role="presidente", output={
        "decision": decision, "score": 4, "confidence": 0.7, "expected_move_pct": 5, "target_pct": target,
        "stop_pct": stop, "veto_reason": "deuda" if decision == "veto" else None, "rationale": "r",
        "agreements": [], "disagreements": [], "invalidation": [], "cites": []}, cost_usd=0.5)
    m = agent.AgentResult(role="optimista", output=OPINION, cost_usd=0.25)
    return committee.CommitteeResult(ticker="AAPL", as_of=None, members=[m], chair=chair,
                                     price={"price_usd": 100.0, "usd_mxn": 18.0, "price_mxn": 1800.0,
                                            "bar_date": "2026-10-16"})


def test_alert_row_vetoed_buy_in_mxn():
    row = store.alert_row(7, _result("veto"), 6)
    assert row[:9] == (7, "2026-10-16", "all", "AAPL", "buy", True, "deuda", 0.7, 6)
    assert row[11:14] == (1800.0, 2160.0, 1620.0)
    approved = store.alert_row(7, _result("approve", target=None, stop=-5), 6)
    assert approved[5] is False and approved[6] is None and approved[12] is None and approved[13] == 1710.0


def test_opinion_rows_stance_per_role():
    res = _result("approve")
    assert store.opinion_row(1, res.chair, "presidente", {})[2] == "buy"
    assert store.opinion_row(1, _result("veto").chair, "presidente", {})[2] == "veto"
    row = store.opinion_row(1, res.members[0], "presidente", {"n": 1})
    assert row[2] == "buy" and row[3] == 7.0 and row[5] == "Crece."
    assert res.cost_usd == 0.75


def test_save_trace_local_without_storage(tmp_path):
    doc = store.trace_doc(_result(), 9, {"committee": {}})
    assert store.save_trace(doc, tmp_path, None, None) is None
    assert (tmp_path / "committee_traces/2026-10-16/AAPL_9.json.gz").exists()


def test_committee_run_members_then_chair(monkeypatch):
    monkeypatch.setattr(committee.tools, "get_ticker_overview", lambda t, as_of=None: {"data": {
        "last_bar": {"_ref": "current_price_bars:AAPL|2026-10-16", "close": 100.0, "bar_date": "2026-10-16"}}})
    monkeypatch.setattr(committee.tools, "get_cost_model", lambda as_of=None: {"data": {
        "usd_mxn": {"_ref": "macro_latest:DEXMXUS|2026-10-16", "value": 18.0, "obs_date": "2026-10-16"}}})
    monkeypatch.setattr(committee.tools, "resolve_refs", lambda cites, as_of=None: {"data": {
        "results": [{"cite": c, "status": "ref_not_found"} for c in cites]}})
    monkeypatch.setattr(committee, "role_tool_defs", lambda role: TOOLS)
    decision = _result("approve").chair.output
    client = FakeClient([([_use("entregar_opinion", OPINION)], "tool_use")] * 5
                        + [([_use("entregar_decision", decision)], "tool_use")])
    cfg = committee.load_config()
    res = committee.run(client, "aapl", None, cfg, context="lugar 3")
    assert [m.role for m in res.members] == cfg["committee"]["members"] and res.error is None
    assert res.price["price_mxn"] == 1800.0
    assert res.chair.output["decision"] == "approve"
    chair_prompt = client.requests[-1]["messages"][0]["content"]
    # the reference that did not resolve never reaches the chair, nor the point using it; the count does
    assert "x:AAPL#roe" not in chair_prompt and "ROE de" not in chair_prompt
    assert "\"citas_descartadas\": 1" in chair_prompt and "lugar 3" in chair_prompt
    assert res.validation["optimista"]["unresolved"] == 1
    assert res.members[0].output["key_points"] == ["ROE de [sin dato]"]


def test_committee_budget_cap_marks_error(monkeypatch):
    monkeypatch.setattr(committee, "price_now", lambda t, a: {"price_mxn": None})
    monkeypatch.setattr(committee, "role_tool_defs", lambda role: TOOLS)
    cfg = committee.load_config()
    cfg["committee"]["max_usd_per_run"] = 0.001
    client = FakeClient([([_use("entregar_opinion", OPINION)], "tool_use")])
    res = committee.run(client, "AAPL", None, cfg)
    assert res.error.startswith("budget") and res.chair is None


class FakeOpenRouter:
    """Stands in for the httpx client: replays chat-completion messages."""

    def __init__(self, messages):
        self.messages = list(messages)
        self.bodies = []

    def post(self, url, json):
        self.bodies.append({**json, "messages": list(json["messages"])})
        msg, finish = self.messages.pop(0)
        data = {"model": json["model"], "choices": [{"message": msg, "finish_reason": finish}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "cost": 0.01}}
        return NS(status_code=200, raise_for_status=lambda: None, json=lambda: data)


def _call(name, args, i="c1"):
    import json as _json
    return {"id": i, "type": "function", "function": {"name": name, "arguments": _json.dumps(args)}}


def test_openrouter_agent_forces_as_of_and_parses_final():
    from src.committee import openrouter
    calls = []
    http = FakeOpenRouter([
        ({"role": "assistant", "content": None, "reasoning_details": [{"type": "x"}],
          "tool_calls": [_call("get_fundamentals", {"ticker": "AAPL", "as_of": "2030-01-01"})]}, "tool_calls"),
        ({"role": "assistant", "content": None, "tool_calls": [_call("entregar_opinion", OPINION, "c2")]},
         "tool_calls")])
    spent = []
    res = openrouter.run_agent(http, role="optimista", system="s", prompt="p", tool_defs=TOOLS,
                               final_tool=OPINION_TOOL, as_of="2026-10-16", max_calls=2,
                               cfg={**CFG, "model": "openrouter:openai/gpt-x"}, spend=spent.append,
                               call_tool=lambda n, a: calls.append((n, a)) or {"data": {}})
    assert calls == [("get_fundamentals", {"ticker": "AAPL", "as_of": "2026-10-16"})]
    assert res.output == OPINION and res.error is None
    assert res.cost_usd == pytest.approx(0.02) and spent == [0.01, 0.01]
    body = http.bodies[1]
    assert body["model"] == "openai/gpt-x"
    assert body["tools"][-1]["function"]["name"] == "entregar_opinion"
    # the assistant turn goes back whole (reasoning kept), then the tool result
    assert body["messages"][2]["reasoning_details"] == [{"type": "x"}]
    assert body["messages"][3]["role"] == "tool" and body["messages"][3]["tool_call_id"] == "c1"


def test_openrouter_agent_refuses_tools_outside_role():
    from src.committee import openrouter
    http = FakeOpenRouter([
        ({"role": "assistant", "tool_calls": [_call("get_portfolio", {})]}, "tool_calls"),
        ({"role": "assistant", "tool_calls": [_call("entregar_opinion", OPINION, "c2")]}, "tool_calls")])
    res = openrouter.run_agent(http, role="optimista", system="s", prompt="p", tool_defs=TOOLS,
                               final_tool=OPINION_TOOL, as_of=None, max_calls=2,
                               cfg={**CFG, "model": "openrouter:x/y"}, spend=lambda c: None,
                               call_tool=lambda n, a: pytest.fail("must not run"))
    assert res.tool_calls == [] and "not_allowed" in http.bodies[1]["messages"][-1]["content"]


def test_brief_and_fill_use_only_stored_values():
    out = {**OPINION, "key_points": ["ROE {1:pct}", "inventado {2}", "sin cifras"], "risks": ["beta {3}"],
           "thesis": "ROE {1:pct} y 41.6% a mano", "cites": ["a#roe", "b#x", "c#beta"]}
    r = agent.AgentResult(role="optimista", output=out)
    v = {"n": 3, "checked": 2, "unresolved": 1, "loose_numbers": 1, "values": [0.3297, None, 2.5]}
    committee.fill(r, v)
    b = committee._brief(r, v)
    assert b["puntos"] == ["ROE 33.0%", "sin cifras"] and b["riesgos"] == ["beta 2.50"]
    assert b["tesis"] == "ROE 33.0% y 41.6% a mano"
    assert b["referencias"] == [{"cite": "a#roe", "value": 0.3297}, {"cite": "c#beta", "value": 2.5}]
    assert b["evidencia"] == {"citas_verificadas": 2, "citas_descartadas": 1, "puntos_descartados": 1,
                              "cifras_sin_cita": 1, "calidad": 0.67}
    assert r.draft == out and r.output["key_points"][1] == "inventado [sin dato]"
    assert r.output["cites"][0] == {"cite": "a#roe", "value": 0.3297}
