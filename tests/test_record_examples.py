"""The example recorder, exercised end-to-end with a fake model (no API key needed)."""

import importlib.util
import json
from pathlib import Path

from emporio.llm.base import LLMResponse

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("record_examples", ROOT / "scripts" / "record_examples.py")
rec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rec)

SCENARIOS = {s["id"]: s for s in json.loads((ROOT / "examples" / "scenarios.json").read_text(encoding="utf-8"))}


def tool(name, args, i):
    return LLMResponse([{"type": "tool_use", "id": f"t{i}", "name": name, "input": args}], "tool_use")


def text(t):
    return LLMResponse([{"type": "text", "text": t}], "end_turn")


class ReturnFlowModel:
    """Plays the 'ideal' behaviour for scenario 03, reading real tool results from the transcript."""

    def __init__(self):
        self.n = 0

    def complete(self, *, system, tools, messages, max_tokens):
        self.n += 1
        last = messages[-1]["content"]
        if self.n == 1:
            return tool("search_policies", {"query": "direito de arrependimento devolução"}, 1)
        if self.n == 2:
            return text("Claro! Qual o número do pedido e os 4 últimos dígitos do telefone?")
        if self.n == 3:
            return tool("lookup_order", {"order_id": 7, "verification": "0123"}, 3)
        if self.n == 4:
            data = json.loads(last[0]["content"])
            assert data["return_windows"]["regret_return_7d_open"] is True  # pinned clock from the scenario
            return text("Está dentro dos 7 dias. Foi compra online?")
        if self.n == 5:
            return tool("escalate_to_human", {"category": "return_or_exchange", "summary": "Devolução pedido 7", "customer_name": "Letícia", "order_id": 7}, 5)
        ticket = json.loads(last[0]["content"])["ticket"]
        return text(f"Chamado aberto: {ticket}")


def test_recorder_runs_scenario_and_checks_expectations(monkeypatch, tmp_path):
    monkeypatch.setenv("EMPORIO_STATE_DIR", str(tmp_path))
    monkeypatch.setattr("emporio.app.create_provider", lambda settings: ReturnFlowModel())
    md, problems = rec.run_scenario(SCENARIOS["03-arrependimento-pedido"])
    assert problems == []
    assert md.startswith("# Return request") and "**Cliente:** Me arrependi" in md and "Ferramentas chamadas" in md
    assert "`lookup_order(" in md and "H-20260220-" in md


def test_recorder_reports_missing_tools_and_text(monkeypatch):
    class Lazy:
        def complete(self, **_):
            return text("Não sei.")

    monkeypatch.setattr("emporio.app.create_provider", lambda settings: Lazy())
    _, problems = rec.run_scenario(SCENARIOS["04-pedido-atrasado"])
    assert any("lookup_order" in p for p in problems) and any("BRJL5544332BR" in p for p in problems)
