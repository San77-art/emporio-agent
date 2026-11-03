"""Provider-neutral LLM interface.

The agent works with one *canonical* message format (the Anthropic content-block shape):

    user      : str | [{"type": "text", ...}] | [{"type": "tool_result", "tool_use_id", "content": str}]
    assistant : [{"type": "text", "text"} | {"type": "tool_use", "id", "name", "input": dict}]

Each provider translates to/from its SDK. ``system`` is a list of ``{"text": str, "cache": bool}``
blocks so providers that support prompt caching can mark the stable prefix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class LLMResponse:
    content: list[dict]
    stop_reason: str
    usage: dict = field(default_factory=dict)


class LLMProvider(Protocol):
    def complete(self, *, system: list[dict], tools: list[dict], messages: list[dict], max_tokens: int) -> LLMResponse:
        ...
