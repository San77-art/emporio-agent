"""Data-quality audit of the CSVs and of the policy manual.

Run ``python -m emporio data-report`` to regenerate ``docs/data_quality.md``. The audit does not
mutate anything: it documents what is wrong and how the agent treats it (see ``data.py``/``tools.py``).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .business import money, min_installment_value
from .data import COMING_SOON, DISCONTINUED, OUT_OF_STOCK, Store


@dataclass(frozen=True)
class Issue:
    area: str
    problem: str
    handling: str


def collect_issues(store: Store) -> list[Issue]:
    issues: list[Issue] = []
    products = store.products

    for p in products.values():
        if p.raw_status == "active" and p.stock == 0:
            issues.append(Issue("products", f"#{p.product_id} {p.name}: status `active` but stock 0.",
                                "Derived availability = out_of_stock; never offered as available, alternatives suggested."))
        if p.raw_status == "discontinued" and p.stock > 0:
            issues.append(Issue("products", f"#{p.product_id} {p.name}: status `discontinued` but stock {p.stock}.",
                                "`status` wins: treated as discontinued (not sold); alternatives suggested."))
        if p.raw_status == "coming_soon" and p.stock > 0:
            issues.append(Issue("products", f"#{p.product_id} {p.name}: status `coming_soon` but stock {p.stock}.",
                                "Treated as not purchasable; price and stock are not quoted."))

    bad_desc = [p for p in products.values() if not p.description_reliable]
    if bad_desc:
        names = "; ".join(f"#{p.product_id} {p.name}" for p in sorted(bad_desc, key=lambda p: p.product_id))
        issues.append(Issue("products", f"{len(bad_desc)} products whose description/specs describe a different brand/model than the name "
                            f"(e.g. 'Music Man Bass 1X' described as a Fender Jazz Bass; 5-string text vs 4-string spec): {names}.",
                            "Flagged `description_reliable=False`: description and specs are withheld from the model; it states only name, "
                            "category, price, availability and offers to confirm details with the team."))

    sold_cs = sorted({i.product_id for o in store.orders.values() for i in o.items if products.get(i.product_id) and products[i.product_id].availability == COMING_SOON})
    if sold_cs:
        issues.append(Issue("orders", f"`coming_soon` product(s) {sold_cs} already appear in orders (some delivered).", "Reported only; order history is shown as recorded."))

    for o in store.orders.values():
        known = [(products[i.product_id].price, i.quantity) for i in o.items if i.product_id in products]
        expected = sum(money(p) * q for p, q in known)
        if o.items and abs(float(expected) - o.total) > 0.01:
            issues.append(Issue("orders", f"Order #{o.order_id}: total R$ {o.total:.2f} ≠ sum of items at list price R$ {float(expected):.2f}.",
                                "`total_brl` is treated as what was charged; never recomputed from items."))
        if o.payment_method == "credit" and o.installments > 1:
            parcel = money(o.total / o.installments)
            if parcel < min_installment_value(o.installments):
                issues.append(Issue("orders", f"Order #{o.order_id}: {o.installments}x of R$ {parcel} is below the manual's minimum parcel (R$ {min_installment_value(o.installments)}).",
                                    "Historical data; reported only. New quotes always follow the manual (`business.installment_options`)."))
        if o.customer_id not in store.customers:
            issues.append(Issue("orders", f"Order #{o.order_id} references unknown customer {o.customer_id}.", "Lookup fails verification."))
        for i in o.items:
            if i.product_id not in products:
                issues.append(Issue("orders", f"Order #{o.order_id} references unknown product {i.product_id}.", "Shown as 'Produto #id'."))

    delivered = [o for o in store.orders.values() if o.status == "delivered"]
    issues.append(Issue("orders", f"{len(delivered)} delivered orders have no real delivery date, only `estimated_delivery`.",
                        "Return/warranty windows use the estimate as a proxy and the tool output says so; the agent asks the customer when in doubt."))
    last = max((o.order_date for o in store.orders.values() if o.order_date), default=None)
    issues.append(Issue("orders", f"Latest order date is {last}; pending/confirmed/shipped orders may look stale relative to today.",
                        "`shipped` past its estimate is flagged as a delay and offered for human follow-up. `STORE_NOW` pins the clock for demos."))

    emails = Counter(c.email for c in store.customers.values())
    for email, n in emails.items():
        if n > 1:
            ids = [c.customer_id for c in store.customers.values() if c.email == email]
            issues.append(Issue("customers", f"Duplicate e-mail `{email}` on customers {ids}.",
                                "Identity checks are always bound to the *order's* customer record, so duplicates cannot cross-expose orders."))
    phones = Counter(c.phone_digits for c in store.customers.values())
    for ph, n in phones.items():
        if n > 1:
            issues.append(Issue("customers", f"Duplicate phone {ph} on {n} customers.", "Same as above."))

    empty = [c.name for c in store.categories.values() if not any(p.category_id == c.category_id for p in products.values())]
    if empty:
        issues.append(Issue("catalog", f"Categories with no products: {', '.join(empty)}. The manual says the store sells wind instruments and orchestral strings.",
                            "Agent says the item does not appear in the catalog it can query and offers to confirm with the team (never a categorical 'we don't sell it')."))
    issues.append(Issue("catalog", f"Catalog has {len(products)} products; the manual claims 300+.",
                        "The CSV is the source of truth for availability; the agent never claims the full catalog is visible."))

    per_product = Counter(p.product_id for p in store.promotions)
    multi = sorted(pid for pid, n in per_product.items() if n > 1)
    active = [p for p in store.promotions if p.is_active]
    issues.append(Issue("promotions", f"{len(active)} of {len(store.promotions)} promotions are active; no start/end dates exist; products with several promotions: {multi}.",
                        "Only `is_active=1` counts. If several were active for one product the best one would apply (not cumulative, manual §6.2). "
                        "The agent never promises seasonal campaigns (e.g. Black Friday) that are not active in the data."))
    return issues


MANUAL_CONFLICTS = [
    ("Contact numbers", "§1.2 lists '(67) 3341-4444' as Telefone/WhatsApp, §7 says the WhatsApp channel is '(67) 3321-4500'.",
     "Agent gives 3341-4444 as phone and 3321-4500 as WhatsApp (the more specific §7 wins for WhatsApp)."),
    ("E-mail", "§1.2 prints 'contato@empóriodamusica.com.br' (accent, invalid) vs 'contato@emporiodamusica.com.br' on the cover.",
     "Agent uses the unaccented address."),
    ("Instalment minimum", "§3 table: credit card 'parcela mínima de R$ 100,00' for all 12x; §3.1: 2–3x min R$50, 4–6x min R$80, 7–12x min R$100.",
     "§3.1 is more specific and wins; implemented in `business.installment_options` (2–3x read as 'parcel must be ≥ R$50')."),
    ("Defect window", "§4.2: exchange for manufacturing defect within 30 days, then go to the manufacturer; §8.1: 90-day legal warranty for manufacturing defects.",
     "≤30 days: store exchange; 31–90 days: the agent does not deny, explains the legal warranty and opens a ticket for the team."),
    ("Regret right scope", "§4.1 grants the 7-day right of regret to *online* purchases; orders data has no sales-channel column.",
     "Agent asks whether the purchase was online when it matters; windows are computed from the delivery estimate."),
    ("'Região metropolitana'", "§5.1 gives local delivery rules for the 'metropolitan region of Campo Grande' without listing municipalities.",
     "Only the city of Campo Grande is treated as local; other cities get Correios/Jadlog rules plus 'confirm with the team'."),
    ("Free shipping threshold", "§5.1 says free shipping 'acima de R$ 500,00'.", "Strictly greater than R$ 500.00 (exactly R$ 500.00 pays the R$ 35.00 fee)."),
]


def render_markdown(data_dir: Path | str) -> str:
    store = Store(data_dir)
    issues = collect_issues(store)
    counts = Counter(i.area for i in issues)
    lines = [
        "# Data quality report",
        "",
        "_Generated by `python -m emporio data-report` — do not edit by hand._",
        "",
        f"Dataset: {len(store.products)} products, {len(store.categories)} categories, {len(store.customers)} customers, "
        f"{len(store.orders)} orders, {sum(len(o.items) for o in store.orders.values())} order items, {len(store.promotions)} promotions.",
        "",
        "Principle: **never silently rewrite source data**. The loader derives explicit fields (`availability`, `description_reliable`) "
        "and everything else is reported here with how the agent behaves.",
        "",
        f"## Findings in the CSVs ({sum(counts.values())})",
        "",
    ]
    for area in ("products", "catalog", "promotions", "orders", "customers"):
        group = [i for i in issues if i.area == area]
        if not group:
            continue
        lines += [f"### {area.capitalize()}", ""]
        lines += [f"- **Problem:** {i.problem}  \n  **Handling:** {i.handling}" for i in group]
        lines.append("")
    lines += ["## Contradictions / ambiguities in the policy manual", ""]
    lines += [f"- **{t}.** {p}  \n  **Decision:** {d}" for t, p, d in MANUAL_CONFLICTS]
    lines.append("")
    return "\n".join(lines)
