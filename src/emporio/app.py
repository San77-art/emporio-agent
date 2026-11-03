"""Composition root: wires data, policies, tools, provider, session and agent together."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .agent import Agent
from .config import Settings, load_settings
from .data import Store
from .llm import create_provider
from .llm.base import LLMProvider
from .policies import PolicyIndex
from .session import Session
from .tools import ToolContext, ToolRegistry

POLICY_PDF = "politicas_da_loja.pdf"


@lru_cache(maxsize=4)
def _load_store(data_dir: str) -> Store:
    return Store(data_dir)


@lru_cache(maxsize=4)
def _load_policies(pdf_path: str) -> PolicyIndex:
    return PolicyIndex.from_pdf(pdf_path)


def build_agent(
    settings: Settings | None = None,
    session_id: str = "default",
    provider: LLMProvider | None = None,
    persist: bool = True,
) -> Agent:
    """Create an agent for one conversation.

    ``persist=False`` keeps history in memory only (used by tests and the example recorder).
    """
    settings = settings or load_settings()
    store = _load_store(str(settings.data_dir))
    policies = _load_policies(str(Path(settings.data_dir) / POLICY_PDF))
    session = Session.load(session_id, settings.state_dir) if persist else Session(session_id)
    ctx = ToolContext(
        store=store,
        policies=policies,
        session=session,
        now=settings.now,
        handoff_path=Path(settings.state_dir) / "handoffs.jsonl",
    )
    return Agent(provider or create_provider(settings), ToolRegistry(ctx), session, settings)
