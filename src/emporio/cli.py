"""Command-line chat interface.

    python -m emporio chat [--session NAME] [--verbose]
    python -m emporio ask "Quanto custa o Takamine GD20?" [--session NAME] [--verbose]
    python -m emporio data-report
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .app import build_agent
from .config import load_settings

HELP = "Comandos: /nova (recomeça a conversa) · /sair"


def _colors() -> tuple[str, str]:
    """ANSI grey for tool traces; disabled when piped or when NO_COLOR is set."""
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return "", ""
    if os.name == "nt":
        os.system("")  # switches on ANSI escape processing in Windows 10+ consoles
    return "\033[90m", "\033[0m"


GREY, RESET = _colors()


def _print_trace(reply) -> None:
    for t in reply.tool_calls:
        args = json.dumps(t.args, ensure_ascii=False)
        status = "erro" if set(t.result) == {"error"} else "ok"
        print(f"{GREY}  ↳ {t.name}({args}) → {status}{RESET}")


def _chat(args) -> int:
    settings = load_settings()
    agent = build_agent(settings, session_id=args.session)
    resumed = len(agent.session.messages) > 0
    print(f"Empório da Música — atendimento ({settings.provider}/{settings.model})")
    print(("Conversa retomada. " if resumed else "") + HELP + "\n")
    while True:
        try:
            text = input("Você: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text.lower() in {"/sair", "/exit", "/quit"}:
            return 0
        if text.lower() == "/nova":
            agent.session.reset()
            print("(conversa reiniciada)\n")
            continue
        try:
            reply = agent.reply(text)
        except Exception as exc:  # network/API errors must not kill the chat or corrupt the history
            print(f"{GREY}[erro ao falar com o modelo: {exc}]{RESET}\nDesculpe, tive um problema técnico. Pode tentar de novo?\n")
            continue
        if args.verbose:
            _print_trace(reply)
        print(f"Dó: {reply.text}\n")


def _ask(args) -> int:
    agent = build_agent(load_settings(), session_id=args.session)
    reply = agent.reply(args.message)
    if args.verbose:
        _print_trace(reply)
    print(reply.text)
    return 0


def _data_report(_args) -> int:
    from .data_quality import render_markdown

    print(render_markdown(load_settings().data_dir))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="emporio", description="Agente de atendimento do Empório da Música")
    sub = parser.add_subparsers(dest="command")

    chat = sub.add_parser("chat", help="conversa interativa no terminal")
    chat.add_argument("--session", default="default", help="nome da conversa (o histórico é salvo e retomado)")
    chat.add_argument("--verbose", "-v", action="store_true", help="mostra as ferramentas chamadas")
    chat.set_defaults(func=_chat)

    ask = sub.add_parser("ask", help="envia uma única mensagem")
    ask.add_argument("message")
    ask.add_argument("--session", default="default")
    ask.add_argument("--verbose", "-v", action="store_true")
    ask.set_defaults(func=_ask)

    rep = sub.add_parser("data-report", help="imprime o relatório de qualidade dos dados (markdown)")
    rep.set_defaults(func=_data_report)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except RuntimeError as exc:  # e.g. missing API key
        print(f"Erro de configuração: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
