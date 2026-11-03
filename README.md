# Empório da Música — AI customer-service agent

A text-message agent ("**Dó**", the store's virtual assistant) for *Empório da Música*, a fictional musical-instrument shop in Campo Grande/MS. It answers in Brazilian Portuguese, in the shop's informal-but-professional WhatsApp tone, and knows **when to look at operational data** (stock, prices, promotions, orders) and **when to consult the policy manual** (returns, payment, shipping, warranty…). Built for the Artefact *AI Engineer* technical challenge.

**Design in one line:** a tool-calling LLM agent whose tools are deterministic Python — the model decides *what to ask*, the code decides *what is true*.

- 7 tools: catalog search, product detail, active promotions, order lookup (with identity check), shipping rules, policy search (BM25 over the PDF manual), human hand-off.
- Every number the agent states (promo price, PIX discount, instalments, shipping fee, store hours, return windows) is computed in code (`business.py`), never by the model.
- Data problems are handled explicitly and documented: [`docs/data_quality.md`](docs/data_quality.md).
- Privacy by design (LGPD): orders are only shown after an identity check; lockout after 3 failures; no order-number enumeration.
- 82 automated tests that run **without an API key** (fake model + real data and manual).
- Provider-agnostic: Claude by default, any OpenAI-compatible endpoint (OpenAI, Ollama, Groq, Gemini-compat…) via one env var.

---

## Quick start

Requires Python ≥ 3.10.

```bash
git clone <this-repo> && cd emporio-agent
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env                                   # then put your key in .env
#   ANTHROPIC_API_KEY=sk-ant-...

python -m emporio chat --verbose                       # interactive chat (shows tool calls)
python -m emporio ask "Quanto custa o Takamine GD20?"  # single message
```

In the chat: `/nova` starts a fresh conversation, `/sair` quits. Conversations are saved under `.state/sessions/` and resumed automatically; use `--session NAME` for separate customers.

| Command | What it does |
|---|---|
| `pytest -q` | 82 tests, no API key needed |
| `python scripts/record_examples.py` | runs the 5 scripted scenarios against the real model and writes `examples/*.md` |
| `python -m emporio data-report` | regenerates the data-quality report |

**Other providers**

```bash
# OpenAI
LLM_PROVIDER=openai  OPENAI_API_KEY=sk-...  LLM_MODEL=gpt-4o-mini
# Local model with Ollama (free): `ollama pull qwen2.5:7b` first (needs a model with tool support)
LLM_PROVIDER=openai  OPENAI_API_KEY=ollama  OPENAI_BASE_URL=http://localhost:11434/v1  LLM_MODEL=qwen2.5:7b
```

**Reproducible demos.** The store clock can be pinned with `STORE_NOW=2026-02-20T10:00` (store time). It matters because the dataset's orders end in March 2026, so return windows and "late delivery" only make sense near those dates. By default the real clock is used.

---

## How it works

```
 customer message
        │
        ▼
 ┌───────────────┐  system prompt = persona + rules (cached) + "AGORA: date/time/open?"   ┌──────────────────────┐
 │  Agent loop   │ ─────────────────────────────────────────────────────────────────────► │ LLM (Claude or any   │
 │  agent.py     │ ◄───────────────────────────────  tool_use / final text  ───────────── │ OpenAI-compatible)   │
 └──────┬────────┘          max 6 LLM rounds per customer message                          └──────────────────────┘
        │ executes the requested tools
        ▼
 ┌──────────────────────────────── tools.py ────────────────────────────────────────────────┐
 │ search_products · get_product · list_promotions ─► catalog + promotions (CSV, data.py)    │
 │ lookup_order (identity check, lockout)           ─► orders/customers (CSV)                │
 │ estimate_shipping                                ─► business.py rules (§5)                │
 │ search_policies                                  ─► BM25 index over the manual (policies.py)│
 │ escalate_to_human                                ─► ticket in .state/handoffs.jsonl       │
 └───────────────────────────────────────────────────────────────────────────────────────────┘
        business.py: prices, PIX, instalments, shipping, hours/holidays, return windows
        session.py : JSON history (atomic writes), history window, verified-customer state
```

| Module | Responsibility |
|---|---|
| `data.py` | Load/clean CSVs; derive `availability` and `description_reliable` |
| `business.py` | Deterministic rules from the manual (money, instalments, shipping, hours, return windows) |
| `policies.py` | PDF → numbered sections → BM25 retrieval with PT query expansion |
| `tools.py` | Tool schemas + handlers; the only place the model touches data |
| `prompts.py` | Persona, routing rules, guardrails, known-conflict resolutions |
| `agent.py` | Tool-use loop, error containment, history commit |
| `llm/` | Canonical message format + Anthropic and OpenAI-compatible adapters |
| `session.py`, `app.py`, `cli.py` | Persistence, wiring, interface |

What the model actually sees for *"Quanto custa o Takamine GD20?"* (real output of `search_products`):

```json
{"product_id": 95, "name": "Takamine GD20 Dreadnought Natural", "availability": "Em estoque", "stock": "5 unidades",
 "pricing": {"list_price": "R$ 2.199,00", "pix": "R$ 2.089,05 no PIX (5% de desconto)",
             "credit_card": "até 12x sem juros de R$ 183,25", "boleto_or_debit": "R$ 2.199,00 à vista"}}
```

---

## Technical decisions and why

### 1. Agent approach: native tool calling, hybrid (structured tools + policy retrieval)
The brief lists RAG, function calling, ReAct, SQL agents or hybrids. I chose **function calling with deterministic tools**, plus **retrieval only for the policy text**:

- **Two kinds of knowledge, two mechanisms.** Operational data is structured and must be exact → typed tools. Policies are prose → retrieval. The model has to *choose* between them (the brief's "know when to consult data vs. policies"), and the choice is visible in the tool trace. Questions that need both (*"can I return order 7?"*) trigger both calls.
- **Why not a text-to-SQL agent?** The data is tiny, and the hard part is not querying it but the rules on top: derived availability (`active` with stock 0 is sold out; `discontinued` with stock 13 is not sellable), promotion/PIX non-cumulativity, instalment minimums, privacy. SQL cannot express those, and a model writing SQL over `orders`/`customers` is a data-leak risk (LGPD). Tools expose only what a customer may see.
- **Why not ReAct-style prompting?** Native tool use gives structured arguments and results, no text parsing, and is more reliable than a free-text Thought/Action format.
- **Why not "everything in the prompt"?** Possible at this size, but it would give up exactness (LLM arithmetic), auditability and privacy, and would not scale.

### 2. Model and provider: Claude through the Anthropic API (default), adapter for anything OpenAI-compatible
- Strong tool use and Brazilian-Portuguese quality, good instruction following for a persona with strict rules. Default `claude-sonnet-5-5`; `claude-haiku-5-5` is the cheaper/faster option for this workload (`LLM_MODEL`). Temperature 0.3: consistent tone with a bit of natural variation.
- **Prompt caching:** the long, stable system prompt and the tool schemas are marked cacheable; only a one-line "AGORA: time, open/closed" block changes per request.
- **No lock-in:** the agent speaks one canonical message format; `llm/openai_provider.py` translates it to Chat Completions, so free/local models (Ollama) work too. Both adapters are unit-tested against fake SDK clients.
- Honest caveat: I could only verify the deterministic layers offline; recorded conversations come from running `scripts/record_examples.py` with a real key.

### 3. Interface: CLI (+ one-shot `ask`)
The brief says the focus is the agent working correctly. `Agent.reply(text)` is channel-agnostic, so a WhatsApp Business webhook or FastAPI endpoint is a thin wrapper around it (see next steps).

### 4. Conversation persistence: JSON per session, windowed
- Full history (including tool calls) is stored in `.state/sessions/<id>.json` with **atomic writes**; resuming a chat just works.
- The model receives the **last 12 customer turns**, trimmed on turn boundaries so a `tool_use` is never separated from its `tool_result`.
- A turn is **committed only on success**: an API error mid-turn leaves the history untouched, so the customer can simply retry.
- Security state (verified customers, failed attempts) is persisted with the history so it cannot be reset by restarting the process.
- A database would be the production choice; for a single-process prototype a file is simpler and inspectable.

### 5. Data treatment (details in [`docs/data_quality.md`](docs/data_quality.md))
Principle: never silently rewrite source data; derive explicit fields and report the rest.
- `availability` combines `status` and `stock_quantity` into four states (in stock / sold out / discontinued / coming soon). Only *in stock* is ever offered.
- **7 products have descriptions that contradict their names** (e.g. "Music Man Bass 1X" described as a Fender Jazz Bass; a "5-string" text with a 4-string spec). They are flagged `description_reliable=False`; their description/specs are withheld from the model, which then states only name, price and availability and offers to confirm details with the team. Repeating a wrong brand would be false advertising (manual §7.1).
- `total_brl` is treated as what was charged (two orders don't match their items); `estimated_delivery` is the proxy for the real delivery date (not recorded); promotions have no dates, so only `is_active=1` counts.
- The catalog has 65 products (the manual says 300+) and no wind/orchestral instruments, so "not found" answers are phrased as *"not in the catalog I can query"* + offer to check with the team, never a categorical denial.

### 6. Policy retrieval: section-level BM25 over the PDF
The manual is parsed at start-up (`pypdf`, layout mode) into 26 numbered sections (§4.1, §5.2…). Retrieval is BM25 with a title boost and a small Portuguese query-expansion table ("me arrependi" → devolução/arrependimento/reembolso).
- **Why not embeddings?** 26 short sections, a closed vocabulary, no extra infra/cost/latency, and results that are deterministic and unit-testable (13 realistic customer questions are asserted to hit the right section). Embeddings/hybrid are the natural upgrade if the manual grows.
- The PDF is the single source: replacing the file updates the agent. Sections come back with their number, so answers are auditable.

### 7. Prompt strategy
Written in Portuguese (the persona's language), split into a cached static part and a dynamic clock line:
1. **Persona & tone** (WhatsApp-short, no heavy markdown, ≤1 emoji, identifies as a virtual assistant — never as a human).
2. **Fixed store card** (address, hours, contacts): answered without a tool call.
3. **Routing rules** — which tool for which question, and that mixed questions need both data and policy.
4. **Golden rules** — never state price/stock/deadline without a tool call in *this* conversation; copy values verbatim; show original price + % + promo price; offer alternatives when something is unavailable; never execute returns/refunds (open a ticket instead).
5. **Scope** — accessories (the store sells instruments only) and unrelated topics are declined politely; instructions embedded in data or in user messages are never followed; the prompt/tools/internal manual are never revealed.
6. **Known manual conflicts** and how to resolve them (see Assumptions).

### 8. Guardrails and safety
- **Identity check** for orders (order number + last 4 phone digits or e-mail); the same answer for "wrong proof" and "order doesn't exist"; lockout after 3 failures per conversation; tool results contain no phone/e-mail/surname.
- **Containment:** unknown tools or bad arguments return an error result to the model (no crash); max 6 LLM rounds per message; input capped at 2,000 chars; fallback message if the model produces nothing.
- **Hand-off:** complaints, returns/exchanges/warranty processing, late deliveries, bulky-freight quotes and "I want a person" open a ticket (`.state/handoffs.jsonl`) with a summary, so the customer never repeats the story; complaints get the manual's 24-business-hour promise; out of hours the agent says when the team is back.

---

## Assumptions (ambiguities documented, as the brief asks)

The manual contradicts itself in places; the decisions are in `prompts.py` and `business.py` and listed in [`docs/data_quality.md`](docs/data_quality.md#contradictions--ambiguities-in-the-policy-manual):

| Topic | Decision |
|---|---|
| Phone vs WhatsApp number (§1.2 vs §7) | Phone (67) 3341-4444; WhatsApp (67) 3321-4500 |
| Instalment minimum (§3 table vs §3.1) | §3.1 wins: 2–3x ≥ R$50, 4–6x ≥ R$80, 7–12x ≥ R$100 |
| Defect window (§4.2: 30 days vs §8.1: 90-day legal warranty) | ≤30 days store exchange; 31–90 days never denied: explained + ticket |
| Right of regret applies to online purchases (§4.1) | Orders have no channel column → the agent asks; windows run from the delivery estimate |
| "Região metropolitana de Campo Grande" (§5.1) | Only Campo Grande city is local; other cities → Correios/Jadlog + "confirm with the team" |
| Free shipping "acima de R$ 500" | Strictly > R$ 500.00 |
| Order status semantics | pending = registered/not confirmed; confirmed = being prepared; shipped/delivered/cancelled as named |
| Store clock | America/Campo_Grande; national holidays + Campo Grande/MS local ones close the store |
| Persona | "Dó" (musical note); informal-professional per §7.1 |

---

## Testing

`pytest -q` → 82 tests, ~2 s, no network:
- **Business rules:** instalments at every threshold, PIX vs promo, shipping boundary (R$500.00 vs R$500.01), store hours incl. holidays/Easter-based dates, return-window boundaries (day 7/8, 30/31, 90/91).
- **Data & policies:** counts, derived availability, the exact set of 7 unreliable descriptions, manual parsing, 13 retrieval questions.
- **Tools:** price lookups, filters on promotional prices, typo tolerance, sold-out → alternatives, withheld descriptions, **order privacy** (verification, no enumeration, lockout, no PII), delayed shipments, hand-off tickets.
- **Agent loop (fake LLM):** tool round trip, multi-tool steps, runaway-loop stop, atomic history, persistence and resume, history window, both provider adapters.
- **Recorder pipeline** end-to-end with a fake model.

What these do *not* prove is the model's behaviour (tone, whether it chooses the right tool, refusing off-topic requests). That is what `scripts/record_examples.py` is for (below), and what an LLM-judged eval set would automate (next steps).

## Example interactions

[`examples/`](examples) holds 5 conversations recorded from the real agent by `scripts/record_examples.py` (each lists the tool calls and raw results used):

1. Price + instalments for a specific model ("Quanto custa o Takamine GD20?")
2. Catalog browsing with a budget ("violões disponíveis até R$ 1000")
3. **Non-trivial:** return request → policy + order lookup + identity check + window computed from the clock + ticket
4. **Non-trivial:** order tracking with identity check and a delivery 23 days late
5. Store info on a Sunday (open/closed, address) + accessories + out-of-scope request

<!-- TODO before submitting: run `python scripts/record_examples.py` and commit examples/0*.md -->

## Known limitations

- **Model behaviour is validated by recorded scenarios, not by an automated eval.** No LLM-judge suite, no latency/cost measurements, no red-team set (prompt injection is mitigated in the prompt and at tool level, but not systematically tested).
- **Weak identity proof.** Last-4 digits of a phone is guessable; the lockout is per conversation, so a determined attacker could open new ones. Production: match the WhatsApp sender number to the customer record and/or send an OTP.
- **Return windows use the *estimated* delivery date** (the real one isn't recorded) and the sales channel is unknown.
- **Lexical search.** Catalog search is token/fuzzy based (no semantic "a guitar for my 8-year-old"); policy search relies on a hand-written expansion table.
- **Single-product quotes.** No cart totals, mixed-payment simulation or multi-item freight; freight outside Campo Grande can't be priced (needs the CEP and the carriers).
- **No stock reservation or promo validity dates** in the data; "Black Friday" prices are only quoted if marked active.
- **Holiday calendar** covers national holidays, Good Friday, Corpus Christi and two local dates; optional days off (Carnival) and special store hours are not modelled.
- **Prototype infrastructure:** CLI only, JSON files (not multi-process safe), PT-BR only, no streaming, no WhatsApp integration.

## What I would do with more time

1. An **eval harness**: golden conversations + LLM-as-judge (correct tool, no unsupported claims, tone, refusals) in CI, plus an adversarial/prompt-injection set.
2. A **WhatsApp Business webhook** (FastAPI) with sender-based identity, rate limiting and a human inbox for tickets.
3. **Hybrid retrieval** (BM25 + embeddings) for policies and semantic catalog search; query rewriting.
4. Real **database + stock reservation**, promotion validity dates, and fixing the data issues at the source (list in `docs/data_quality.md`).
5. **Observability:** structured logs of tool calls, token/cost dashboards, conversation-level satisfaction signals.
6. **Streaming** and multi-message replies for a more natural chat feel.

## Use of AI coding assistants

I used **Claude (Anthropic)** — the claude.ai chat with agentic file/shell tools (model Claude Sonnet 5.5) — as a pair-programmer for this challenge. Workflow:

1. Gave Claude the challenge PDF, the six CSVs and the policy manual and asked for the system to be built as a senior engineer would.
2. Claude read the files, audited the data (the issues in `docs/data_quality.md` came from that pass), proposed the architecture above and wrote the code, tests and this README iteratively, running the test suite after each stage and committing in logical steps.
3. Design decisions that mattered (tools doing all arithmetic, identity check for orders, withholding unreliable descriptions, resolving manual contradictions, atomic history commits) were made explicit and are documented above so they can be challenged.
4. The recorded conversations are produced by running the real agent, not written by hand.

I take responsibility for the final code and the decisions documented here.

<!-- Add here, in your own words, what you reviewed/changed and anything you would do differently. -->

## Project layout

```
data/                CSVs + políticas (politicas_da_loja.pdf)  — the challenge inputs
src/emporio/         agent, tools, business rules, policies, LLM adapters, CLI
tests/               82 tests (no network)
scripts/             record_examples.py (real-model transcripts)
examples/            scenarios.json + recorded conversations
docs/data_quality.md generated audit of the CSVs and the manual
```
