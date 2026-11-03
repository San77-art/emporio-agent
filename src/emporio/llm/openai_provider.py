"""OpenAI-compatible provider (OpenAI, Ollama, Groq, Gemini's OpenAI endpoint, vLLM, ...).

Translates the canonical Anthropic-style messages to Chat Completions and back, so the agent loop
and the persisted history stay provider-agnostic.
"""

from __future__ import annotations

import json

from .base import LLMResponse


def to_openai_messages(system: list[dict], messages: list[dict]) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": "\n\n".join(s["text"] for s in system)}]
    for m in messages:
        content = m["content"]
        if m["role"] == "user":
            if isinstance(content, str):
                out.append({"role": "user", "content": content})
                continue
            for b in content:
                if b["type"] == "tool_result":
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"]})
                elif b["type"] == "text":
                    out.append({"role": "user", "content": b["text"]})
        else:
            if isinstance(content, str):
                out.append({"role": "assistant", "content": content})
                continue
            text = "".join(b["text"] for b in content if b["type"] == "text")
            calls = [
                {"id": b["id"], "type": "function",
                 "function": {"name": b["name"], "arguments": json.dumps(b["input"], ensure_ascii=False)}}
                for b in content if b["type"] == "tool_use"
            ]
            msg: dict = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
    return out


def to_openai_tools(tools: list[dict]) -> list[dict]:
    return [
        {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
        for t in tools
    ]


class OpenAICompatProvider:
    def __init__(self, model: str, temperature: float | None = 0.3, client=None):
        if client is None:
            try:
                import openai
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("Install the SDK: pip install openai") from exc
            client = openai.OpenAI()  # reads OPENAI_API_KEY / OPENAI_BASE_URL
        self.client, self.model, self.temperature = client, model, temperature

    def complete(self, *, system, tools, messages, max_tokens) -> LLMResponse:
        kwargs = dict(
            model=self.model,
            messages=to_openai_messages(system, messages),
            tools=to_openai_tools(tools),
            max_tokens=max_tokens,
        )
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message

        content: list[dict] = []
        if msg.content:
            content.append({"type": "text", "text": msg.content})
        for call in msg.tool_calls or []:
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            content.append({"type": "tool_use", "id": call.id, "name": call.function.name, "input": args})
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            content=content,
            stop_reason="tool_use" if msg.tool_calls else "end_turn",
            usage={"input_tokens": getattr(usage, "prompt_tokens", 0), "output_tokens": getattr(usage, "completion_tokens", 0)},
        )
