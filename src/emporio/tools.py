"""Tools exposed to the LLM (function calling).

Design rules
------------
* The model decides *when* to call a tool; the tool decides *what is true*. Prices, stock,
  installments, shipping, hours and return windows are all computed here, in code.
* Results are small, Portuguese, ready-to-quote dicts. Anything the model must not repeat
  (inconsistent catalog descriptions, customer PII) is simply not returned.
* Order data is personal data (LGPD, manual §9): ``lookup_order`` requires an identity check and
  locks after repeated failures; it never reveals whether an order number exists.
"""

from __future__ import annotations

import difflib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .business import (
    format_brl,
    quote_price,
    return_windows,
    shipping_estimate,
    store_status,
)
from .data import COMING_SOON, DISCONTINUED, IN_STOCK, OUT_OF_STOCK, Customer, Product, Store, normalize
from .policies import PolicyIndex
from .session import MAX_FAILED_VERIFICATIONS, Session

AVAILABILITY_PT = {
    IN_STOCK: "Em estoque",
    OUT_OF_STOCK: "Esgotado no momento",
    DISCONTINUED: "Descontinuado (fora de catálogo)",
    COMING_SOON: "Em breve (ainda não disponível para compra)",
}
_AVAIL_RANK = {IN_STOCK: 0, OUT_OF_STOCK: 1, COMING_SOON: 2, DISCONTINUED: 3}

ORDER_STATUS_PT = {
    "pending": "Pendente: pedido registrado, ainda não confirmado",
    "confirmed": "Confirmado: em preparação para envio",
    "shipped": "Enviado: a caminho do cliente",
    "delivered": "Entregue",
    "cancelled": "Cancelado",
}
PAYMENT_PT = {"pix": "PIX", "debit": "Cartão de débito", "boleto": "Boleto bancário"}

_SPEC_LABELS = {
    "top": "tampo", "back_sides": "fundo e laterais", "neck": "braço", "strings": "cordas", "scale": "escala",
    "electronics": "eletrônica/captação", "color": "cor", "body": "corpo", "shells": "cascos",
    "pieces": "peças", "hardware": "ferragens", "keys": "teclas", "type": "tipo", "polyphony": "polifonia",
}
_SPEC_VALUES = {"nylon": "nylon", "steel": "aço", "yes": "sim", "no": "não", "included": "inclusas"}

_QUERY_STOPWORDS = set(
    "de da do das dos para com o a os as um uma e em no na nos nas por quanto custa preco valor tem qual quais "
    "que voces voce disponivel disponiveis opcao opcoes ate ou me mostra mostre quero queria procuro r".split()
)


def _canon(token: str) -> str:
    if token.endswith("oes"):
        return token[:-3] + "ao"
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def _tokens(text: str) -> list[str]:
    return [_canon(t) for t in re.findall(r"[a-z0-9]+", normalize(text))]


# --------------------------------------------------------------------------------------------------
@dataclass
class ToolContext:
    store: Store
    policies: PolicyIndex
    session: Session
    now: Callable[[], datetime]
    handoff_path: Path | None = None


class ToolRegistry:
    def __init__(self, ctx: ToolContext):
        self.ctx = ctx
        self._index = self._build_search_index()
        self._handlers: dict[str, Callable[..., dict]] = {
            "search_products": self.search_products,
            "get_product": self.get_product,
            "list_promotions": self.list_promotions,
            "lookup_order": self.lookup_order,
            "estimate_shipping": self.estimate_shipping,
            "search_policies": self.search_policies,
            "escalate_to_human": self.escalate_to_human,
        }

    # -- dispatch --------------------------------------------------------------------------------
    @property
    def specs(self) -> list[dict]:
        return TOOL_SPECS

    def execute(self, name: str, args: dict[str, Any]) -> dict:
        handler = self._handlers.get(name)
        if handler is None:
            return {"error": f"Unknown tool {name!r}"}
        try:
            return handler(**(args or {}))
        except TypeError as exc:  # wrong/missing argument names from the model
            return {"error": f"Invalid arguments for {name}: {exc}"}
        except (ValueError, KeyError) as exc:
            return {"error": f"Invalid value for {name}: {exc}"}

    # -- product views ---------------------------------------------------------------------------
    def _promo_tuple(self, p: Product):
        promo = self.ctx.store.active_promotion(p.product_id)
        return (promo.discount_percent, promo.name) if promo else None

    def _final_price(self, p: Product) -> float:
        return float(quote_price(p.price, self._promo_tuple(p)).final_price)

    def _product_view(self, p: Product, detail: bool = False) -> dict:
        view: dict[str, Any] = {
            "product_id": p.product_id,
            "name": p.name,
            "category": p.category_name,
            "availability": AVAILABILITY_PT[p.availability],
        }
        if p.availability == IN_STOCK:
            view["stock"] = f"últimas {p.stock} unidade(s)" if p.stock <= 3 else f"{p.stock} unidades"
            view["pricing"] = quote_price(p.price, self._promo_tuple(p)).as_dict()
        elif p.availability == OUT_OF_STOCK:
            view["pricing"] = {"list_price": format_brl(p.price), "note": "Preço de tabela; produto esgotado, não confirme disponibilidade."}
        elif p.availability == COMING_SOON:
            view["note"] = "Ainda não disponível para compra; não há previsão de chegada no sistema."
        else:
            view["note"] = "Fora de catálogo; ofereça modelos equivalentes."

        if p.description_reliable:
            view["specs"] = {
                _SPEC_LABELS.get(k, k): _SPEC_VALUES.get(str(v), v) for k, v in p.specs.items()
            }
            if detail:
                view["description"] = p.description
        else:
            color = p.specs.get("color")
            if color:
                view["specs"] = {"cor": color}
            view["data_warning"] = (
                "Cadastro inconsistente (descrição/ficha técnica não confiáveis e omitidas). Informe apenas nome, "
                "categoria, preço e disponibilidade; para detalhes técnicos ou de marca, ofereça confirmar com a equipe."
            )
        return view

    def _alternatives(self, p: Product, limit: int = 3) -> list[dict]:
        wanted_strings = p.specs.get("strings")
        cands = [
            c for c in self.ctx.store.products.values()
            if c.availability == IN_STOCK and c.category_id == p.category_id and c.product_id != p.product_id
            and 0.65 * p.price <= c.price <= 1.35 * p.price
        ]
        cands.sort(key=lambda c: (c.specs.get("strings") != wanted_strings, abs(c.price - p.price)))
        return [self._product_view(c) for c in cands[:limit]]

    # -- search ----------------------------------------------------------------------------------
    def _build_search_index(self) -> dict[int, dict[str, set[str]]]:
        index: dict[int, dict[str, set[str]]] = {}
        for p in self.ctx.store.products.values():
            spec_words = []
            for k, v in p.specs.items() if p.description_reliable else [("color", p.specs.get("color", ""))]:
                spec_words.append(str(v))
                if v == "steel":
                    spec_words.append("aco")
                if k == "electronics" and v == "yes":
                    spec_words.extend(["eletroacustico", "eletrico", "captacao"])
            index[p.product_id] = {
                "name": set(_tokens(p.name)),
                "category": set(_tokens(p.category_name)),
                "spec": set(_tokens(" ".join(spec_words))),
                "desc": set(_tokens(p.description)) if p.description_reliable else set(),
            }
        self._vocab = sorted({t for f in index.values() for toks in f.values() for t in toks if len(t) > 2})
        return index

    @staticmethod
    def _token_weight(q: str, fields: dict[str, set[str]]) -> int:
        for field, weight in (("name", 3), ("category", 2), ("spec", 2), ("desc", 1)):
            for t in fields[field]:
                if t == q or (len(q) >= 4 and t.startswith(q)):
                    return weight
        return 0

    def search_products(
        self,
        query: str = "",
        category: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        string_type: str | None = None,
        electric: bool | None = None,
        available_only: bool = False,
        limit: int = 6,
    ) -> dict:
        store = self.ctx.store
        limit = max(1, min(int(limit), 10))
        pool = list(store.products.values())

        if category:
            cat = store.category_by_name(category)
            if cat is None:
                return self._no_results(f"Categoria '{category}' não existe no catálogo.")
            pool = [p for p in pool if p.category_id == cat.category_id]
        if string_type:
            st = {"nylon": "nylon", "aco": "steel", "aço": "steel", "steel": "steel"}.get(normalize(string_type))
            pool = [p for p in pool if st and p.specs.get("strings") == st and p.description_reliable]
        if electric is not None:
            want = "yes" if electric else "no"
            pool = [p for p in pool if p.description_reliable and p.specs.get("electronics") == want]
        if available_only:
            pool = [p for p in pool if p.availability == IN_STOCK]
        if min_price is not None:
            pool = [p for p in pool if self._final_price(p) >= float(min_price)]
        if max_price is not None:
            pool = [p for p in pool if self._final_price(p) <= float(max_price)]

        scored: list[tuple[int, Product]] = [(0, p) for p in pool]
        partial = False
        q_tokens = [t for t in _tokens(query) if t not in _QUERY_STOPWORDS]
        if q_tokens:
            # typo tolerance: replace tokens unknown to the whole catalog by their closest known word
            fixed = []
            for t in q_tokens:
                known = any(self._token_weight(t, f) for f in self._index.values())
                if not known and not t.isdigit():
                    close = difflib.get_close_matches(t, self._vocab, n=1, cutoff=0.8)
                    t = close[0] if close else t
                fixed.append(t)
            q_tokens = [t for t in fixed if not (t.isdigit() and not any(self._token_weight(t, f) for f in self._index.values()))]
        if q_tokens:
            def score(p: Product) -> tuple[int, int]:
                weights = [self._token_weight(t, self._index[p.product_id]) for t in q_tokens]
                return sum(1 for w in weights if w), sum(weights)

            ranked = [(score(p), p) for p in pool]
            full = [(s[1], p) for s, p in ranked if s[0] == len(q_tokens)]
            if full:
                scored = full
            else:
                need = max(1, -(-len(q_tokens) * 6 // 10))  # ceil(60%)
                scored = [(s[1], p) for s, p in ranked if s[0] >= need]
                partial = bool(scored)
                if not scored:
                    return self._no_results("Nenhum produto corresponde à busca.")

        if not scored:
            return self._no_results("Nenhum produto corresponde aos filtros.")

        scored.sort(key=lambda sp: (_AVAIL_RANK[sp[1].availability], -sp[0], self._final_price(sp[1])))
        results = [p for _, p in scored]
        out: dict[str, Any] = {
            "total_matches": len(results),
            "showing": min(limit, len(results)),
            "results": [self._product_view(p) for p in results[:limit]],
        }
        if partial:
            out["partial_match"] = "Nenhum produto bate com TODOS os termos; estes são os mais próximos. Confirme com o cliente."
        if results[0].availability != IN_STOCK:
            out["suggested_alternatives"] = self._alternatives(results[0])
        return out

    def _no_results(self, message: str) -> dict:
        counts: dict[str, int] = {}
        for p in self.ctx.store.products.values():
            counts[p.category_name] = counts.get(p.category_name, 0) + 1
        return {
            "total_matches": 0,
            "results": [],
            "message": message,
            "catalog_categories_with_products": counts,
            "note": "Só afirme que a loja não tem o item se for acessório (a loja não vende acessórios). Para instrumentos, diga que não aparece no catálogo consultável agora e ofereça confirmar com a equipe.",
        }

    def get_product(self, product_id: int) -> dict:
        p = self.ctx.store.products.get(int(product_id))
        if p is None:
            return {"error": f"Produto {product_id} não encontrado."}
        out = {"product": self._product_view(p, detail=True)}
        if p.availability != IN_STOCK:
            out["suggested_alternatives"] = self._alternatives(p)
        return out

    def list_promotions(self) -> dict:
        seen: dict[int, Product] = {}
        for promo in self.ctx.store.promotions:
            if promo.is_active and promo.product_id in self.ctx.store.products:
                seen[promo.product_id] = self.ctx.store.products[promo.product_id]
        items = sorted(seen.values(), key=lambda p: p.price)
        return {
            "count": len(items),
            "promotions": [self._product_view(p) for p in items],
            "note": (
                "Somente promoções ativas no sistema. O sistema não registra data de validade: não prometa prazo nem "
                "desconto que não esteja listado aqui (campanhas sazonais só valem quando aparecem como ativas)."
            ),
        }

    # -- orders ----------------------------------------------------------------------------------
    @staticmethod
    def _verify(customer: Customer, proof: str) -> bool:
        proof = proof.strip()
        if "@" in proof:
            return proof.lower() == customer.email
        digits = re.sub(r"\D", "", proof)
        if len(digits) == 4:
            return customer.phone_digits.endswith(digits)
        if len(digits) >= 10:
            return digits.endswith(customer.phone_digits) or customer.phone_digits.endswith(digits)
        return False

    def lookup_order(self, order_id: int, verification: str | None = None) -> dict:
        ctx, sess = self.ctx, self.ctx.session
        if sess.locked:
            return {"ok": False, "error": "locked",
                    "message": "Verificação bloqueada nesta conversa após tentativas inválidas. Não consulte pedidos; ofereça encaminhar a um atendente (escalate_to_human)."}
        try:
            oid = int(order_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid_order_id", "message": "Número de pedido inválido; peça o número novamente."}

        order = ctx.store.orders.get(oid)
        customer = ctx.store.customers.get(order.customer_id) if order else None
        already_verified = bool(customer and customer.customer_id in sess.verified_customers)
        if not already_verified:
            if not verification:
                return {"ok": False, "error": "verification_required",
                        "message": "Antes de mostrar dados do pedido, peça ao cliente os 4 últimos dígitos do telefone cadastrado (ou o e-mail do cadastro)."}
            if not (order and customer and self._verify(customer, verification)):
                sess.failed_verifications += 1
                left = MAX_FAILED_VERIFICATIONS - sess.failed_verifications
                # same answer whether the order exists or not: no order-number enumeration
                return {"ok": False, "error": "verification_failed",
                        "message": f"Não foi possível confirmar o pedido com esses dados. Tentativas restantes: {left}. Não revele se o pedido existe nem qual dado falhou."}
            sess.verified_customers.add(customer.customer_id)

        today = ctx.now().date()
        products = ctx.store.products
        out: dict[str, Any] = {
            "ok": True,
            "order_id": order.order_id,
            "customer_first_name": customer.first_name,
            "order_date": f"{order.order_date:%d/%m/%Y}" if order.order_date else None,
            "status_code": order.status,
            "status": ORDER_STATUS_PT.get(order.status, order.status),
            "items": [
                {"product": products[i.product_id].name if i.product_id in products else f"Produto #{i.product_id}",
                 "quantity": i.quantity}
                for i in order.items
            ],
            "total": format_brl(order.total),
            "payment": (f"Cartão de crédito em {order.installments}x" if order.payment_method == "credit"
                        else PAYMENT_PT.get(order.payment_method, order.payment_method)),
        }
        alerts: list[str] = []
        if order.tracking_code and order.status in {"shipped", "delivered"}:
            out["tracking_code"] = order.tracking_code
            out["tracking_hint"] = "Pode ser consultado no site dos Correios ou da Jadlog (manual §5.3)."
        if order.estimated_delivery:
            out["estimated_delivery"] = f"{order.estimated_delivery:%d/%m/%Y}"
        if order.notes:
            out["notes"] = order.notes

        if order.status == "shipped" and order.estimated_delivery and order.estimated_delivery < today:
            late = (today - order.estimated_delivery).days
            alerts.append(f"ATRASO: a previsão de entrega passou há {late} dia(s). Peça desculpas, informe o código de rastreio e ofereça abrir um chamado (escalate_to_human, categoria delivery_issue).")
        if order.status in {"pending", "confirmed"}:
            alerts.append("Ainda sem código de rastreio: ele é enviado por e-mail e WhatsApp assim que o pedido é despachado (manual §5.3).")
        if order.status == "cancelled":
            alerts.append("Não há informação de estorno no sistema: não prometa prazo; se o cliente perguntar, ofereça encaminhar à equipe.")
        if order.status == "delivered" and order.estimated_delivery:
            out["return_windows"] = {
                "basis": "O sistema não registra a data real de entrega; usada a data estimada como referência.",
                **return_windows(order.estimated_delivery, today),
            }
            alerts.append("Para devolução/troca: aplique os prazos de return_windows junto com as regras do manual (search_policies). O canal da compra (online/loja) não consta no sistema: confirme com o cliente se foi online.")
        if alerts:
            out["alerts"] = alerts
        return out

    # -- shipping / policy / handoff ---------------------------------------------------------------
    def estimate_shipping(self, city: str | None = None, order_total: float | None = None, large_item: bool = False) -> dict:
        return shipping_estimate(city, order_total, large_item)

    def search_policies(self, query: str, top_k: int = 3) -> dict:
        sections = self.ctx.policies.search(query, top_k=max(1, min(int(top_k), 5)))
        if not sections:
            return {"results": [], "message": "Nada encontrado no manual para essa consulta. Não invente regras: ofereça encaminhar à equipe."}
        return {
            "results": [s.as_dict() for s in sections],
            "note": "Texto do manual interno: parafraseie para o cliente (não cite o manual). Se houver conflito entre trechos, siga as 'Regras para conflitos conhecidos' do seu prompt.",
        }

    def escalate_to_human(
        self,
        category: str,
        summary: str,
        customer_name: str | None = None,
        contact: str | None = None,
        order_id: int | None = None,
    ) -> dict:
        ctx = self.ctx
        now = ctx.now()
        ticket = f"H-{now:%Y%m%d}-{secrets.token_hex(2).upper()}"
        record = {
            "ticket": ticket,
            "created_at": now.isoformat(timespec="seconds"),
            "session": ctx.session.session_id,
            "category": category,
            "summary": summary,
            "customer_name": customer_name,
            "contact": contact,
            "order_id": order_id,
        }
        if ctx.handoff_path:
            ctx.handoff_path.parent.mkdir(parents=True, exist_ok=True)
            with ctx.handoff_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        status = store_status(now)
        sla = "Retorno em até 24 horas úteis (manual §7.3)." if category == "complaint" else "Um atendente retoma o caso no horário de funcionamento."
        return {"ok": True, "ticket": ticket, "expected_response": sla, "store_now": status["summary"],
                "note": "Informe o número do chamado. Não prometa reembolso, troca ou prazo além do indicado."}


# --------------------------------------------------------------------------------------------------
TOOL_SPECS: list[dict] = [
    {
        "name": "search_products",
        "description": (
            "Search the store catalog (instruments only) by free text and/or filters. Use for ANY question about "
            "a product, price, stock/availability, recommendations or comparisons — never answer those from memory. "
            "Returns computed prices (promotion, PIX, instalments) and availability. "
            "Use available_only=true for open-ended recommendations; leave it false when the customer asks about a "
            "specific model so you can tell them if it is sold out, discontinued or coming soon. "
            "Price filters apply to the current selling price (after any active promotion)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Brand/model/keywords, e.g. 'Takamine GD20', 'ukulele tenor'. Leave empty to rely on filters only."},
                "category": {"type": "string", "enum": ["Guitarras", "Baixos", "Baterias e Percussão", "Teclados e Pianos", "Violões", "Instrumentos de Sopro (Madeiras)", "Instrumentos de Sopro (Metais)", "Cordas Orquestrais", "Ukuleles"]},
                "min_price": {"type": "number", "description": "BRL"},
                "max_price": {"type": "number", "description": "BRL"},
                "string_type": {"type": "string", "enum": ["nylon", "aço"], "description": "Only for violões/ukuleles."},
                "electric": {"type": "boolean", "description": "true = eletroacústico (with pickup); false = acoustic only."},
                "available_only": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 6, "minimum": 1, "maximum": 10},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_product",
        "description": "Full details (description, specs, pricing, availability, alternatives) of one product by product_id, e.g. after search_products.",
        "input_schema": {
            "type": "object",
            "properties": {"product_id": {"type": "integer"}},
            "required": ["product_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_promotions",
        "description": "List ACTIVE promotions with original price, discount and final price. Use when the customer asks about promotions/discounts/offers.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "lookup_order",
        "description": (
            "Look up an order's status, items, tracking code and delivery estimate. Order data is personal data: "
            "you MUST have the order number AND an identity proof from the customer — the last 4 digits of the phone "
            "number on file, or the e-mail on file — before calling with `verification`. Once a customer is verified in "
            "this conversation, further lookups for that customer's orders need no new proof. "
            "Also returns return/warranty windows for delivered orders."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "integer"},
                "verification": {"type": "string", "description": "Last 4 digits of the registered phone, or the registered e-mail."},
            },
            "required": ["order_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "estimate_shipping",
        "description": "Shipping fee/lead time rules by destination city (Campo Grande vs other cities). Use for questions about frete, prazo de entrega, delivery options.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "order_total": {"type": "number", "description": "Order value in BRL, if known (decides free shipping in Campo Grande)."},
                "large_item": {"type": "boolean", "description": "true for drums, digital pianos and other bulky instruments."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "search_policies",
        "description": (
            "Search the store policy manual (hours, address/contact, payment methods and instalment rules, returns "
            "and exchanges, warranty, shipping, promotion rules, privacy, service guidelines). Use for any "
            "'how does it work / what is the rule' question and for applying return/exchange/warranty rules. "
            "Write the query in Portuguese using the customer's topic, e.g. 'direito de arrependimento devolução'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "top_k": {"type": "integer", "default": 3, "minimum": 1, "maximum": 5}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "escalate_to_human",
        "description": (
            "Hand the conversation to the human team and open a ticket. Use for complaints, return/exchange/"
            "warranty requests that need processing, delayed or problematic deliveries, special quotes (bulky "
            "freight), requests you cannot resolve, or whenever the customer asks for a person. Summarize the case "
            "so the customer never has to repeat it. Collect a name and contact first when you do not have them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": ["complaint", "return_or_exchange", "defect_or_warranty", "delivery_issue", "special_quote", "other"]},
                "summary": {"type": "string", "description": "What the customer wants and what was already checked."},
                "customer_name": {"type": "string"},
                "contact": {"type": "string", "description": "Phone or e-mail for the callback."},
                "order_id": {"type": "integer"},
            },
            "required": ["category", "summary"],
            "additionalProperties": False,
        },
    },
]
