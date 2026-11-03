"""Anthropic (Claude) provider using native tool use and prompt caching."""

from __future__ import annotations

from .base import LLMResponse


class AnthropicProvider:
    def __init__(self, model: str, temperature: float | None = 0.3, client=None):
        if client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("Install the SDK: pip install anthropic") from exc
            client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self.client, self.model, self.temperature = client, model, temperature

    def complete(self, *, system, tools, messages, max_tokens) -> LLMResponse:
        system_blocks = []
        for block in system:
            b = {"type": "text", "text": block["text"]}
            if block.get("cache"):
                b["cache_control"] = {"type": "ephemeral"}  # long, stable prefix -> cached
            system_blocks.append(b)

        tool_defs = [dict(t) for t in tools]
        if tool_defs:
            tool_defs[-1]["cache_control"] = {"type": "ephemeral"}

        kwargs = dict(model=self.model, max_tokens=max_tokens, system=system_blocks, tools=tool_defs, messages=messages)
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        resp = self.client.messages.create(**kwargs)

        content: list[dict] = []
        for block in resp.content:
            if block.type == "text":
                content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input)})
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            content=content,
            stop_reason=resp.stop_reason,
            usage={
                "input_tokens": getattr(usage, "input_tokens", 0),
                "output_tokens": getattr(usage, "output_tokens", 0),
                "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            },
        )
