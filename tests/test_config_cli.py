from types import SimpleNamespace

import pytest

import emporio.cli as cli
from emporio import config
from emporio.llm.anthropic_provider import AnthropicProvider


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.setattr(config, "_load_dotenv", lambda path: None)  # ignore a developer's local .env
    for var in ("LLM_PROVIDER", "LLM_MODEL", "LLM_TEMPERATURE", "STORE_NOW"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_temperature_defaults(clean_env):
    assert config.load_settings().temperature is None  # Claude: parameter omitted
    clean_env.setenv("LLM_PROVIDER", "openai")
    assert config.load_settings().temperature == 0.3
    clean_env.setenv("LLM_TEMPERATURE", "0.7")
    assert config.load_settings().temperature == 0.7
    clean_env.setenv("LLM_TEMPERATURE", "")
    assert config.load_settings().temperature is None  # explicit empty = omit


def test_model_defaults_and_validation(clean_env):
    assert config.load_settings().model == "claude-sonnet-5-5"
    clean_env.setenv("LLM_MODEL", "claude-haiku-5-5")
    assert config.load_settings().model == "claude-haiku-5-5"
    clean_env.setenv("LLM_PROVIDER", "nope")
    with pytest.raises(ValueError):
        config.load_settings()


# -- strict fake that mimics the new SDK signature (no `temperature` argument) -----------------------
class StrictMessages:
    def __init__(self):
        self.calls = 0

    def create(self, *, model, max_tokens, system, tools, messages):  # any extra kwarg -> TypeError, like the real SDK
        self.calls += 1
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0))


def _complete(provider):
    return provider.complete(system=[{"text": "s"}], tools=[], messages=[{"role": "user", "content": "oi"}], max_tokens=10)


def test_default_request_works_with_a_strict_sdk():
    client = SimpleNamespace(messages=StrictMessages())
    resp = _complete(AnthropicProvider("m", None, client=client))
    assert resp.content[0]["text"] == "ok" and client.messages.calls == 1


def test_temperature_rejected_by_sdk_is_retried_without_it():
    client = SimpleNamespace(messages=StrictMessages())
    provider = AnthropicProvider("m", 0.3, client=client)
    assert _complete(provider).content[0]["text"] == "ok"
    assert client.messages.calls == 1 and provider.temperature is None  # first attempt raised before counting
    _complete(provider)
    assert client.messages.calls == 2  # later calls go straight through


def test_unrelated_type_errors_are_not_swallowed():
    def create(**kw):
        raise TypeError("something else entirely")

    with pytest.raises(TypeError):
        _complete(AnthropicProvider("m", 0.3, client=SimpleNamespace(messages=SimpleNamespace(create=create))))


def test_no_color_when_not_a_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: False, raising=False)
    assert cli._colors() == ("", "")
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(cli.os, "name", "posix")
    assert cli._colors() == ("\033[90m", "\033[0m")
    monkeypatch.setenv("NO_COLOR", "1")
    assert cli._colors() == ("", "")
