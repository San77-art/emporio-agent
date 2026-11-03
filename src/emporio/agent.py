"""The agent loop: persona prompt + tool-calling until the model produces a final answer."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .llm.base import LLMProvider
from .prompts import STATIC_PROMPT, dynamic_block
from .session import Session, trim_history
from .tools import ToolRegistry

MAX_STEPS = 6  # LLM calls per customer message (tool rounds); guards against loops
HISTORY_TURNS = 12  # customer turns sent to the model; the full history stays on disk
MAX_INPUT_CHARS = 2000

FALLBACK_TEXT = (
    "Desculpe, não consegui concluir essa consulta agora. Pode repetir a pergunta? "
    "Se preferir, chamo um atendente da equipe para te ajudar."
)


@dataclass
class ToolTrace:
    name: str
    args: dict
    result: dict


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolTrace] = field(default_factory=list)
    llm_calls: int = 0
    usage: dict[str, int] = field(default_factory=dict)


class Agent:
    def __init__(self, provider: LLMProvider, registry: ToolRegistry, session: Session, settings: Settings):
        self.provider, self.registry, self.session, self.settings = provider, registry, session, settings

    def reply(self, user_text: str) -> Reply:
        user_text = user_text.strip()[:MAX_INPUT_CHARS]
        if not user_text:
            raise ValueError("empty message")

        system = [
            {"text": STATIC_PROMPT, "cache": True},
            {"text": dynamic_block(self.settings.now())},
        ]
        new: list[dict] = [{"role": "user", "content": user_text}]  # committed only on success
        traces: list[ToolTrace] = []
        usage: dict[str, int] = {}
        final_text, calls = "", 0

        for _ in range(MAX_STEPS):
            window = trim_history(self.session.messages + new, HISTORY_TURNS)
            resp = self.provider.complete(
                system=system, tools=self.registry.specs, messages=window, max_tokens=self.settings.max_tokens
            )
            calls += 1
            for k, v in resp.usage.items():
                usage[k] = usage.get(k, 0) + int(v or 0)
            new.append({"role": "assistant", "content": resp.content})

            tool_uses = [b for b in resp.content if b["type"] == "tool_use"]
            if not tool_uses:
                final_text = "".join(b["text"] for b in resp.content if b["type"] == "text").strip()
                break

            results = []
            for block in tool_uses:
                result = self.registry.execute(block["name"], block.get("input") or {})
                traces.append(ToolTrace(block["name"], block.get("input") or {}, result))
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                    "is_error": set(result) == {"error"},
                })
            new.append({"role": "user", "content": results})
        else:
            final_text = FALLBACK_TEXT
            new.append({"role": "assistant", "content": [{"type": "text", "text": final_text}]})

        if not final_text:  # model returned nothing usable
            final_text = FALLBACK_TEXT
            new[-1] = {"role": "assistant", "content": [{"type": "text", "text": final_text}]}

        self.session.messages.extend(new)
        self.session.save()
        return Reply(final_text, traces, calls, usage)
