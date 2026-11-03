"""Deterministic business rules from the store manual.

LLMs are unreliable at arithmetic and at remembering thresholds, so every number the agent states
(promo price, PIX discount, installments, shipping fee, store hours, return windows) is computed
here and handed to the model as a ready-made fact.

Manual references (``Manual de Políticas e Procedimentos`` v2.1) are given as §N.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .data import normalize

CENTS = Decimal("0.01")
PIX_DISCOUNT = Decimal("0.05")  # §3
FREE_SHIPPING_ABOVE = Decimal("500.00")  # §5.1 (strictly "acima de")
LOCAL_SHIPPING_FEE = Decimal("35.00")  # §5.1

WEEKDAYS_PT = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]


# -- money ---------------------------------------------------------------------------------------
def money(value) -> Decimal:
    return Decimal(str(value)).quantize(CENTS, rounding=ROUND_HALF_UP)


def format_brl(value) -> str:
    """1299 -> 'R$ 1.299,00'"""
    s = f"{money(value):,.2f}"
    return "R$ " + s.replace(",", "§").replace(".", ",").replace("§", ".")


# -- installments (§3, §3.1) -------------------------------------------------------------------
def min_installment_value(n: int) -> Decimal:
    """Minimum parcel by number of instalments (§3.1). 2–3x: R$50 (reading of 'exceto abaixo de R$50')."""
    if n <= 3:
        return Decimal("50")
    if n <= 6:
        return Decimal("80")
    return Decimal("100")


def installment_options(price) -> list[dict]:
    """Allowed credit-card instalment plans for ``price`` (interest-free, up to 12x)."""
    total = money(price)
    options = []
    for n in range(1, 13):
        parcel = money(total / n)
        if n == 1 or parcel >= min_installment_value(n):
            options.append({"times": n, "parcel": parcel})
    return options


@dataclass(frozen=True)
class PriceQuote:
    list_price: Decimal
    discount_percent: int | None
    promo_name: str | None
    final_price: Decimal
    pix_price: Decimal | None  # None when a promotion applies (§6.2: PIX discount is not cumulative)
    max_installments: int
    installment_value: Decimal

    def as_dict(self) -> dict:
        d = {
            "list_price": format_brl(self.list_price),
            "final_price": format_brl(self.final_price),
            "on_promotion": self.discount_percent is not None,
            "credit_card": (
                f"até {self.max_installments}x sem juros de {format_brl(self.installment_value)}"
                if self.max_installments > 1
                else "somente à vista no cartão (preço baixo demais para parcelar)"
            ),
            "boleto_or_debit": f"{format_brl(self.final_price)} à vista",
        }
        if self.discount_percent is not None:
            d["promotion"] = f"{self.promo_name}: {self.discount_percent}% de desconto (de {format_brl(self.list_price)} por {format_brl(self.final_price)})"
            d["pix"] = "PIX: sem desconto adicional (o desconto de 5% do PIX não se aplica a preço promocional)"
        else:
            d["pix"] = f"{format_brl(self.pix_price)} no PIX (5% de desconto)"
        return d


def quote_price(list_price, promotion: tuple[int, str] | None = None) -> PriceQuote:
    lp = money(list_price)
    if promotion:
        pct, name = promotion
        final = money(lp * (Decimal(100) - Decimal(pct)) / Decimal(100))
        pix = None
    else:
        pct, name, final = None, None, lp
        pix = money(lp * (Decimal(1) - PIX_DISCOUNT))
    best = installment_options(final)[-1]
    return PriceQuote(lp, pct, name, final, pix, best["times"], best["parcel"])


# -- shipping (§5) -------------------------------------------------------------------------------
def _clean_city(city: str) -> str:
    c = normalize(city)
    for suffix in (" - ms", "/ms", ", ms", " ms"):
        if c.endswith(suffix):
            c = c[: -len(suffix)]
    return c.strip()


def shipping_estimate(city: str | None, order_total=None, large_item: bool = False) -> dict:
    if not city:
        return {"needs_city": True, "message": "Pergunte a cidade de entrega para calcular as condições."}
    total = money(order_total) if order_total is not None else None
    if _clean_city(city) == "campo grande":
        out = {
            "region": "Campo Grande/MS (entrega por motoboy próprio)",
            "delivery_time": "1 a 3 dias úteis",
            "contact": "O cliente é contactado por telefone antes da entrega.",
            "rule": "Frete grátis para pedidos acima de R$ 500,00; abaixo disso, taxa fixa de R$ 35,00.",
        }
        if total is not None:
            out["shipping_fee"] = "R$ 0,00 (frete grátis)" if total > FREE_SHIPPING_ABOVE else format_brl(LOCAL_SHIPPING_FEE)
        return out
    out = {
        "region": f"{city} (fora de Campo Grande): envio por Correios ou Jadlog",
        "options": [
            "PAC (Correios): 5 a 12 dias úteis",
            "SEDEX (Correios): 2 a 5 dias úteis",
            "Jadlog (.package): 3 a 8 dias úteis",
        ],
        "included": "Rastreamento e seguro contra extravio/danos inclusos em todos os envios.",
        "shipping_fee": "Calculado automaticamente pelo CEP de destino, peso e dimensões no fechamento do pedido; não é possível informar o valor aqui.",
        "note": "Se a cidade fizer parte da região metropolitana de Campo Grande, a entrega local pode se aplicar: confirmar com a equipe.",
    }
    if large_item:
        out["large_item"] = "Instrumentos de grande porte (ex.: baterias acústicas, pianos digitais) exigem frete especial com cotação individual pela equipe."
    return out


# -- store hours (§2) ----------------------------------------------------------------------------
_OPENING = {  # weekday() -> (opens, closes)
    **{d: (time(9, 0), time(18, 0)) for d in range(5)},
    5: (time(9, 0), time(13, 0)),
}


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm (Meeus/Jones/Butcher)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def holidays(year: int) -> dict[date, str]:
    """National holidays + the two local ones that close the store. Carnival is an optional day off
    ("ponto facultativo") and is intentionally not included — see README limitations."""
    easter = _easter(year)
    fixed = {
        (1, 1): "Confraternização Universal", (4, 21): "Tiradentes", (5, 1): "Dia do Trabalho",
        (9, 7): "Independência do Brasil", (10, 12): "Nossa Senhora Aparecida", (11, 2): "Finados",
        (11, 15): "Proclamação da República", (11, 20): "Consciência Negra", (12, 25): "Natal",
        (8, 26): "Aniversário de Campo Grande", (10, 11): "Criação do Estado de MS",
    }
    out = {date(year, m, d): name for (m, d), name in fixed.items()}
    out[easter - timedelta(days=2)] = "Sexta-feira Santa"
    out[easter + timedelta(days=60)] = "Corpus Christi"
    return out


def _is_open_day(d: date) -> tuple[bool, str | None]:
    hol = holidays(d.year).get(d)
    if hol:
        return False, hol
    return d.weekday() in _OPENING, None


def _fmt_day(d: date) -> str:
    return f"{WEEKDAYS_PT[d.weekday()]} ({d:%d/%m})"


def store_status(now: datetime) -> dict:
    """Is the store open right now, and when does it open next?"""
    today = now.date()
    open_day, holiday = _is_open_day(today)
    if open_day:
        opens, closes = _OPENING[today.weekday()]
        t = now.time().replace(second=0, microsecond=0)
        if opens <= t < closes:
            return {"is_open": True, "closes_at": f"{closes:%H:%M}",
                    "summary": f"Loja aberta agora (fecha hoje às {closes:%H:%M})."}
        if t < opens:
            return {"is_open": False, "next_open": f"hoje às {opens:%H:%M}",
                    "summary": f"Loja fechada agora; abre hoje às {opens:%H:%M}."}
    for offset in range(1, 15):
        d = today + timedelta(days=offset)
        ok, _ = _is_open_day(d)
        if ok:
            opens = _OPENING[d.weekday()][0]
            reason = f"feriado ({holiday})" if holiday else WEEKDAYS_PT[today.weekday()]
            return {"is_open": False, "next_open": f"{_fmt_day(d)} às {opens:%H:%M}",
                    "summary": f"Loja fechada agora ({reason}); reabre {_fmt_day(d)} às {opens:%H:%M}."}
    return {"is_open": False, "summary": "Loja fechada."}  # pragma: no cover


# -- returns & warranty (§4, §8) ---------------------------------------------------------------
def return_windows(delivered_on: date, today: date) -> dict:
    """Which post-delivery windows are still open. Windows count calendar days from receipt."""
    days = (today - delivered_on).days
    if days < 0:
        return {"days_since_delivery": days, "note": "Data de entrega ainda no futuro: pedido não deve estar entregue."}
    return {
        "days_since_delivery": days,
        "regret_return_7d_open": days <= 7,  # §4.1 (online purchases) and §4.3 (exchange by preference)
        "defect_exchange_30d_open": days <= 30,  # §4.2
        "legal_warranty_90d_open": days <= 90,  # §8.1
    }
