#!/usr/bin/env python3
"""Build the PDF summary required by the selection-process e-mail (Google Forms upload).

    python scripts/build_submission_pdf.py --repo-url https://github.com/<user>/<repo> [--out-dir DIR]

The file is named "<Name> - <Career>.pdf", as the e-mail asks. Content is in English.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

NAME = "Santiago Ribeiro de Lima"
CAREER = "AI Engineer – Full-Stack para Aplicações com IA Generativa"

DECISIONS = [
    ("Agent approach", "Native tool calling; deterministic Python tools for data + BM25 retrieval over the policy manual",
     "Data needs exact answers and privacy (typed tools); policies are prose (retrieval). The model chooses between them. Text-to-SQL rejected: it cannot express the business rules and risks leaking customer data."),
    ("Model / provider", "Claude (Anthropic API), default claude-sonnet-5-5; adapter for any OpenAI-compatible endpoint (Ollama, OpenAI...)",
     "Reliable tool use and PT-BR quality; prompt caching for the long static prompt; provider-agnostic canonical message format."),
    ("Interface", "CLI (chat + one-shot ask)", "Focus on the agent; Agent.reply() is channel-agnostic, so a WhatsApp/FastAPI wrapper is thin."),
    ("Conversation history", "JSON per session, atomic writes, last 12 turns sent to the model", "Resumable, inspectable; a turn is committed only on success, so API errors never corrupt history."),
    ("Policy retrieval", "Section-level BM25 with PT query expansion", "26 short sections, closed vocabulary: deterministic, testable, no extra infrastructure. Embeddings are the upgrade path."),
    ("Data treatment", "Derived availability; unreliable descriptions withheld; audit report", "Never silently rewrite data. 7 products have descriptions that contradict their names; they are not repeated to customers."),
    ("Safety", "Identity check for orders, lockout, no enumeration, loop/size limits, human hand-off", "Orders are personal data (LGPD); the agent cannot process refunds, so it opens a ticket."),
]


def build(repo_url: str | None, out_dir: Path) -> Path:
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9.5, leading=12.5)
    cell = ParagraphStyle("cell", parent=body, fontSize=8.3, leading=10.3)
    h1 = ParagraphStyle("h1", parent=styles["Title"], fontSize=17, leading=21, alignment=0, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=11.5, spaceBefore=9, spaceAfter=3, textColor=colors.HexColor("#0b2447"))
    mono = ParagraphStyle("mono", parent=body, fontName="Courier", fontSize=8.5, leading=11)

    def bullets(items: list[str]) -> ListFlowable:
        return ListFlowable([ListItem(Paragraph(t, body), leftIndent=12) for t in items], bulletType="bullet", bulletFontSize=6, leftIndent=12)

    repo = (f'<a href="{repo_url}" color="blue">{repo_url}</a>' if repo_url
            else "(link sent in the reply e-mail, as requested in the challenge brief)")
    story = [
        Paragraph("Technical Case – Stage 1: Customer-service AI agent", h1),
        Paragraph(f"<b>Candidate:</b> {NAME}<br/><b>Role:</b> {CAREER}<br/><b>Repository:</b> {repo}", body),
        Paragraph("Overview", h2),
        Paragraph(
            "<i>Empório da Música</i> is a fictional musical-instrument store in Campo Grande/MS. The delivered project is a Python "
            "text-message agent (\"Dó\") that answers in Brazilian Portuguese with the shop's informal-but-professional tone, queries "
            "operational data (catalog, prices, stock, promotions, orders) through tools, consults the policy manual (returns, payment, "
            "shipping, warranty) through retrieval, and declines out-of-scope requests. Every number it states is computed in code, not by the model.", body),
        Paragraph("Technical decisions", h2),
    ]
    rows = [[Paragraph(f"<b>{h}</b>", cell) for h in ("Decision", "Choice", "Why")]]
    rows += [[Paragraph(f"<b>{a}</b>", cell), Paragraph(b, cell), Paragraph(c, cell)] for a, b, c in DECISIONS]
    table = Table(rows, colWidths=[2.8 * cm, 5.6 * cm, 8.6 * cm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8edf5")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b8c2d4")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [table, Paragraph("Data findings handled", h2), bullets([
        "Product status vs stock conflicts (active with stock 0, discontinued with stock 13, coming_soon already sold): availability is derived explicitly.",
        "7 catalog descriptions describe a different brand/model than the product name: withheld from the model.",
        "The policy manual contradicts itself (phone/WhatsApp numbers, instalment minimum, 30-day vs 90-day defect window): each case has a documented decision.",
        "Orders: totals that do not match items, historical instalments below the manual's minimum, no real delivery date: documented, never silently changed.",
    ]), Paragraph("How to run", h2), Paragraph(
        "pip install -r requirements.txt<br/>cp .env.example .env &nbsp;# set ANTHROPIC_API_KEY<br/>python -m emporio chat --verbose<br/>"
        "pytest -q &nbsp;# 82 tests, no API key needed<br/>python scripts/record_examples.py &nbsp;# regenerates examples/", mono),
        Paragraph("Known limitations and next steps", h2), bullets([
            "Model behaviour is validated by recorded scenarios, not yet by an automated LLM-judged eval suite (first next step).",
            "Identity check (last 4 phone digits) is weak for production: use WhatsApp sender matching and/or OTP.",
            "Lexical catalog/policy search; hybrid BM25 + embeddings, a database with stock reservation and a WhatsApp webhook are the natural evolution.",
        ]), Paragraph("Use of AI coding assistants", h2), Paragraph(
            "Claude (Anthropic) was used as a pair-programmer: it audited the data, proposed the architecture and wrote code, tests and documentation "
            "iteratively with the test suite run at each stage. The workflow and the decisions are described in the repository README.", body),
    ]

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{NAME} - {CAREER}.pdf"
    SimpleDocTemplate(str(out), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm, bottomMargin=1.8 * cm,
                      title=f"{NAME} - {CAREER}", author=NAME).build(story)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-url", default=None)
    ap.add_argument("--out-dir", default=".", type=Path)
    args = ap.parse_args()
    print(build(args.repo_url, args.out_dir))
