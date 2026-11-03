import json
from types import SimpleNamespace

import pytest

from conftest import ScriptedProvider, call, say
from emporio.agent import FALLBACK_TEXT, MAX_INPUT_CHARS, MAX_STEPS
from emporio.app import build_agent
from emporio.llm.anthropic_provider import AnthropicProvider
from emporio.llm.base import LLMResponse
from emporio.llm.openai_provider import OpenAICompatProvider, to_openai_messages, to_openai_tools
from emporio.session import Session, trim_history
from emporio.tools import TOOL_SPECS


def agent_with(script, settings, persist=False, session_id="t"):
    provider = ScriptedProvider(script)
    return build_agent(settings, session_id=session_id, provider=provider, persist=persist), provider


# -- agent loop ----------------------------------------------------------------------------------
def test_tool_round_trip(settings):
    agent, provider = agent_with(
        [call("search_products", {"query": "Takamine GD20"}), say("O Takamine GD20 sai por *R$ 2.199,00*.")], settings
    )
    reply = agent.reply("Quanto custa o Takamine GD20?")
    assert reply.text.startswith("O Takamine GD20") and reply.llm_calls == 2
    assert [t.name for t in reply.tool_calls] == ["search_products"]
    assert reply.tool_calls[0].result["results"][0]["product_id"] == 95

    # the tool result was fed back to the model as a tool_result block with the real JSON
    second_call_msgs = provider.calls[1]["messages"]
    tool_result = second_call_msgs[-1]["content"][0]
    assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "toolu_1"
    assert "R$ 2.199,00" in tool_result["content"] and tool_result["is_error"] is False

    roles = [m["role"] for m in agent.session.messages]
    assert roles == ["user", "assistant", "user", "assistant"]


def test_system_prompt_blocks(settings):
    agent, provider = agent_with([say("Oi!")], settings)
    agent.reply("oi")
    static, dynamic = provider.calls[0]["system"]
    assert static["cache"] is True and "Dó" in static["text"] and "Empório da Música" in static["text"]
    assert not dynamic.get("cache") and "sexta-feira, 20/02/2026, 10:00" in dynamic["text"] and "aberta agora" in dynamic["text"]
    assert provider.calls[0]["tools"] == TOOL_SPECS


def test_tool_errors_are_fed_back_not_raised(settings):
    agent, provider = agent_with([call("does_not_exist", {}), say("Desculpe, não consegui.")], settings)
    reply = agent.reply("oi")
    assert reply.text == "Desculpe, não consegui."
    assert provider.calls[1]["messages"][-1]["content"][0]["is_error"] is True


def test_multiple_tool_calls_in_one_step(settings):
    two = LLMResponse(
        [
            {"type": "tool_use", "id": "a", "name": "search_policies", "input": {"query": "devolução"}},
            {"type": "tool_use", "id": "b", "name": "lookup_order", "input": {"order_id": 7, "verification": "0123"}},
        ],
        "tool_use",
    )
    agent, provider = agent_with([two, say("ok")], settings)
    reply = agent.reply("Posso devolver o pedido 7? meu telefone termina em 0123")
    assert [t.name for t in reply.tool_calls] == ["search_policies", "lookup_order"]
    results = provider.calls[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"]
    assert reply.tool_calls[1].result["return_windows"]["regret_return_7d_open"] is True  # pinned clock: 3 days after delivery


def test_runaway_tool_loop_is_stopped(settings):
    script = [call("list_promotions", {}, f"id{i}") for i in range(MAX_STEPS + 3)]
    agent, provider = agent_with(script, settings)
    reply = agent.reply("promoções?")
    assert reply.text == FALLBACK_TEXT and reply.llm_calls == MAX_STEPS
    assert agent.session.messages[-1]["role"] == "assistant"  # history stays well-formed


def test_provider_failure_does_not_corrupt_history(settings):
    class Boom:
        def complete(self, **_):
            raise RuntimeError("network down")

    agent = build_agent(settings, provider=Boom(), persist=False)
    with pytest.raises(RuntimeError):
        agent.reply("oi")
    assert agent.session.messages == []  # nothing half-written; the customer can simply retry


def test_empty_message_rejected_and_long_message_truncated(settings):
    agent, provider = agent_with([say("ok")], settings)
    with pytest.raises(ValueError):
        agent.reply("   ")
    agent.reply("a" * (MAX_INPUT_CHARS + 500))
    assert len(provider.calls[0]["messages"][0]["content"]) == MAX_INPUT_CHARS


def test_history_is_persisted_and_resumed(settings):
    a1, _ = agent_with([say("Oi! Eu sou o Dó.")], settings, persist=True, session_id="cliente-1")
    a1.reply("Oi")
    a2, p2 = agent_with([say("Claro!")], settings, persist=True, session_id="cliente-1")
    assert len(a2.session.messages) == 2
    a2.reply("Tem violão?")
    sent = p2.calls[0]["messages"]
    assert sent[0]["content"] == "Oi" and sent[-1]["content"] == "Tem violão?"  # earlier turn reaches the model


def test_verification_state_survives_a_restart(settings):
    a1, _ = agent_with([call("lookup_order", {"order_id": 8, "verification": "7821"}), say("achei")], settings, persist=True, session_id="v")
    a1.reply("pedido 8, final 7821")
    a2, _ = agent_with([call("lookup_order", {"order_id": 8}), say("de novo")], settings, persist=True, session_id="v")
    reply = a2.reply("e agora?")
    assert reply.tool_calls[0].result["ok"] is True  # no second identity check needed


def test_session_reset_deletes_file(settings):
    a, _ = agent_with([say("oi")], settings, persist=True, session_id="r")
    a.reply("oi")
    assert a.session.path.is_file()
    a.session.reset()
    assert not a.session.path.exists() and a.session.messages == []


def test_session_id_is_sanitised(tmp_path):
    s = Session.load("../../etc/passwd", tmp_path)
    assert tmp_path in s.path.parents and ".." not in s.path.name


# -- history window ------------------------------------------------------------------------------
def _turn(i, with_tool=True):
    msgs = [{"role": "user", "content": f"q{i}"}]
    if with_tool:
        msgs += [
            {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": "x", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": "{}"}]},
        ]
    msgs.append({"role": "assistant", "content": [{"type": "text", "text": f"a{i}"}]})
    return msgs


def test_trim_history_keeps_whole_turns():
    history = [m for i in range(10) for m in _turn(i)]
    window = trim_history(history, 3)
    assert window[0] == {"role": "user", "content": "q7"} and len(window) == 12
    ids_use = {b["id"] for m in window if m["role"] == "assistant" for b in m["content"] if b["type"] == "tool_use"}
    ids_res = {b["tool_use_id"] for m in window if m["role"] == "user" and isinstance(m["content"], list) for b in m["content"]}
    assert ids_use == ids_res  # no orphaned tool_use / tool_result
    assert trim_history(history, 50) is history


# -- provider adapters ------------------------------------------------------------------------------
CANONICAL = [
    {"role": "user", "content": "Tem Takamine?"},
    {"role": "assistant", "content": [
        {"type": "text", "text": "Vou ver."},
        {"type": "tool_use", "id": "c1", "name": "search_products", "input": {"query": "Takamine"}},
    ]},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "{\"total_matches\": 4}"}]},
    {"role": "assistant", "content": [{"type": "text", "text": "Temos 4."}]},
]


def test_openai_message_translation():
    out = to_openai_messages([{"text": "SYS1"}, {"text": "SYS2"}], CANONICAL)
    assert out[0] == {"role": "system", "content": "SYS1\n\nSYS2"}
    assert out[1] == {"role": "user", "content": "Tem Takamine?"}
    assistant = out[2]
    assert assistant["content"] == "Vou ver." and assistant["tool_calls"][0]["id"] == "c1"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"query": "Takamine"}
    assert out[3] == {"role": "tool", "tool_call_id": "c1", "content": "{\"total_matches\": 4}"}
    assert out[4] == {"role": "assistant", "content": "Temos 4."}
    tools = to_openai_tools(TOOL_SPECS)
    assert tools[0]["type"] == "function" and tools[0]["function"]["parameters"]["type"] == "object"


def test_openai_provider_parses_tool_calls():
    msg = SimpleNamespace(content=None, tool_calls=[
        SimpleNamespace(id="x1", function=SimpleNamespace(name="get_product", arguments='{"product_id": 95}')),
        SimpleNamespace(id="x2", function=SimpleNamespace(name="list_promotions", arguments="not json")),
    ])
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5)))))
    resp = OpenAICompatProvider("m", 0.2, client=fake).complete(system=[{"text": "s"}], tools=TOOL_SPECS, messages=CANONICAL[:1], max_tokens=100)
    assert resp.stop_reason == "tool_use"
    assert resp.content[0] == {"type": "tool_use", "id": "x1", "name": "get_product", "input": {"product_id": 95}}
    assert resp.content[1]["input"] == {}  # malformed arguments degrade to {} instead of crashing
    assert resp.usage == {"input_tokens": 10, "output_tokens": 5}


def test_anthropic_provider_request_shape_and_parsing():
    seen = {}

    def create(**kw):
        seen.update(kw)
        blocks = [SimpleNamespace(type="text", text="Olá"), SimpleNamespace(type="tool_use", id="u1", name="list_promotions", input={})]
        return SimpleNamespace(content=blocks, stop_reason="tool_use",
                               usage=SimpleNamespace(input_tokens=7, output_tokens=3, cache_read_input_tokens=None))

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    provider = AnthropicProvider("claude-x", 0.3, client=client)
    resp = provider.complete(system=[{"text": "stable", "cache": True}, {"text": "dynamic"}], tools=TOOL_SPECS, messages=CANONICAL[:1], max_tokens=300)

    assert seen["model"] == "claude-x" and seen["max_tokens"] == 300 and seen["temperature"] == 0.3
    assert seen["system"][0]["cache_control"] == {"type": "ephemeral"} and "cache_control" not in seen["system"][1]
    assert seen["tools"][-1]["cache_control"] == {"type": "ephemeral"} and "cache_control" not in TOOL_SPECS[-1]  # input not mutated
    assert resp.content == [{"type": "text", "text": "Olá"}, {"type": "tool_use", "id": "u1", "name": "list_promotions", "input": {}}]
    assert resp.stop_reason == "tool_use" and resp.usage["cache_read_input_tokens"] == 0

    seen.clear()
    AnthropicProvider("claude-x", None, client=client).complete(system=[{"text": "s"}], tools=[], messages=CANONICAL[:1], max_tokens=10)
    assert "temperature" not in seen
