from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from emporio.config import Settings
from emporio.data import Store
from emporio.llm.base import LLMResponse
from emporio.policies import PolicyIndex
from emporio.session import Session
from emporio.tools import ToolContext, ToolRegistry

DATA = Path(__file__).resolve().parents[1] / "data"
TZ = ZoneInfo("America/Campo_Grande")


def at(y, mo, d, h=10, mi=0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


@pytest.fixture(scope="session")
def store() -> Store:
    return Store(DATA)


@pytest.fixture(scope="session")
def policies() -> PolicyIndex:
    return PolicyIndex.from_pdf(DATA / "politicas_da_loja.pdf")


@pytest.fixture
def make_registry(store, policies, tmp_path):
    """Factory: registry with a fresh session and a pinned clock."""

    def _make(now: datetime | None = None, session: Session | None = None) -> ToolRegistry:
        now = now or at(2026, 2, 20)
        ctx = ToolContext(store, policies, session or Session("test"), lambda: now, tmp_path / "handoffs.jsonl")
        return ToolRegistry(ctx)

    return _make


@pytest.fixture
def registry(make_registry) -> ToolRegistry:
    return make_registry()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=DATA, state_dir=tmp_path / "state", provider="anthropic", model="test-model",
        max_tokens=512, temperature=0.0, now_override="2026-02-20T10:00",
    )


class ScriptedProvider:
    """Fake LLM: replays a fixed list of responses and records what it was sent."""

    def __init__(self, script: list[LLMResponse]):
        self.script = list(script)
        self.calls: list[dict] = []

    def complete(self, *, system, tools, messages, max_tokens) -> LLMResponse:
        self.calls.append({"system": copy.deepcopy(system), "messages": copy.deepcopy(messages), "tools": tools})
        if not self.script:
            raise AssertionError("ScriptedProvider ran out of responses")
        return self.script.pop(0)


def say(text: str) -> LLMResponse:
    return LLMResponse([{"type": "text", "text": text}], "end_turn")


def call(name: str, args: dict, call_id: str = "toolu_1") -> LLMResponse:
    return LLMResponse([{"type": "tool_use", "id": call_id, "name": name, "input": args}], "tool_use")
