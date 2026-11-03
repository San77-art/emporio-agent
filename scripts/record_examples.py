#!/usr/bin/env python3
"""Run the scenarios in examples/scenarios.json against the REAL model and save the transcripts.

    python scripts/record_examples.py              # all scenarios
    python scripts/record_examples.py 03 04        # only ids starting with these prefixes

Each scenario gets a fresh in-memory conversation and a pinned store clock (``now`` in the JSON) so
results are reproducible. Besides writing ``examples/<id>.md`` the script checks that the expected
tools were called and that expected strings show up in the answers, and exits non-zero otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from emporio.app import build_agent  # noqa: E402
from emporio.config import load_settings  # noqa: E402


def _short(obj, limit: int = 320) -> str:
    text = json.dumps(obj, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + " …"


def run_scenario(sc: dict) -> tuple[str, list[str]]:
    settings = load_settings(now_override=sc.get("now"))
    agent = build_agent(settings, session_id=f"example-{sc['id']}", persist=False)

    lines = [
        f"# {sc['title']}",
        "",
        f"> Recorded automatically by `scripts/record_examples.py` · model `{settings.provider}/{settings.model}` · "
        f"simulated store time `{sc['now']}` (America/Campo_Grande)",
        ">",
        f"> **What this shows:** {sc['description']}",
        "",
    ]
    used_tools: list[str] = []
    answers: list[str] = []
    for msg in sc["turns"]:
        reply = agent.reply(msg)
        used_tools += [t.name for t in reply.tool_calls]
        answers.append(reply.text)
        lines += [f"**Cliente:** {msg}", "", f"**Dó:** {reply.text}", ""]
        if reply.tool_calls:
            lines += [f"<details><summary>Ferramentas chamadas ({len(reply.tool_calls)})</summary>", ""]
            for t in reply.tool_calls:
                lines.append(f"- `{t.name}({json.dumps(t.args, ensure_ascii=False)})` → `{_short(t.result)}`")
            lines += ["", "</details>", ""]

    problems = [f"tool `{name}` was never called" for name in sc.get("expect_tools", []) if name not in used_tools]
    joined = "\n".join(answers)
    problems += [f"answer does not contain {s!r}" for s in sc.get("expect_text", []) if s not in joined]
    return "\n".join(lines).rstrip() + "\n", problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("prefixes", nargs="*", help="only run scenarios whose id starts with one of these")
    args = parser.parse_args()

    scenarios = json.loads((ROOT / "examples" / "scenarios.json").read_text(encoding="utf-8"))
    if args.prefixes:
        scenarios = [s for s in scenarios if any(s["id"].startswith(p) for p in args.prefixes)]

    failed = 0
    for sc in scenarios:
        try:
            md, problems = run_scenario(sc)
        except RuntimeError as exc:
            print(f"Configuration error: {exc}", file=sys.stderr)
            return 2
        (ROOT / "examples" / f"{sc['id']}.md").write_text(md, encoding="utf-8")
        status = "OK  " if not problems else "FAIL"
        print(f"[{status}] {sc['id']}" + ("" if not problems else " — " + "; ".join(problems)))
        failed += bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
