"""Loading and cleaning of the store's CSV data.

The CSVs are the store's "system of record". This module turns them into typed objects and
derives the fields the agent needs, *without silently altering source values*:

* ``availability`` — a single, unambiguous state per product (in_stock / out_of_stock /
  discontinued / coming_soon) derived from ``status`` **and** ``stock_quantity``.
* ``description_reliable`` — some catalog descriptions describe a different brand/model than the
  product name (e.g. "Music Man Bass 1X" described as a "Fender Jazz Bass"). The agent must never
  repeat such a description as fact, so those products are flagged and their description/specs are
  withheld from the model (see ``tools.product_view``).
* Search helpers (accent-insensitive normalisation).

Everything questionable that is *not* auto-fixed is listed by ``emporio.data_quality``.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

IN_STOCK = "in_stock"
OUT_OF_STOCK = "out_of_stock"
DISCONTINUED = "discontinued"
COMING_SOON = "coming_soon"

BRANDS = (
    "Yamaha", "Fender", "Gibson", "Ibanez", "Music Man", "Pearl", "Tama", "Roland", "Korg",
    "Nord", "PRS", "Taylor", "Martin", "Takamine", "Crafter", "Giannini", "Tagima", "Shelby",
    "Rozini", "Kala", "Ohana", "Kalani",
)
# Products whose names follow the generic "<Brand> <Type> <N>" pattern are the ones with
# scrambled descriptions in the source data.
_GENERIC_NAME = re.compile(r"\b(Bass|Kit|Synth)\s+\d", re.IGNORECASE)


class DataError(RuntimeError):
    """Raised when the data files are missing or malformed."""


def normalize(text: object) -> str:
    """Lower-case and strip accents: 'Violões' -> 'violoes'."""
    s = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in s if not unicodedata.combining(ch)).lower().strip()


def _brands_in(text: str) -> set[str]:
    low = normalize(text)
    return {b for b in BRANDS if re.search(rf"\b{re.escape(normalize(b))}\b", low)}


@dataclass(frozen=True)
class Category:
    category_id: int
    name: str
    description: str


@dataclass(frozen=True)
class Product:
    product_id: int
    name: str
    price: float
    category_id: int
    category_name: str
    description: str
    stock: int
    raw_status: str
    specs: dict
    created_at: date | None
    availability: str
    description_reliable: bool

    @property
    def is_purchasable(self) -> bool:
        return self.availability == IN_STOCK


@dataclass(frozen=True)
class Promotion:
    promotion_id: int
    product_id: int
    discount_percent: int
    name: str
    is_active: bool


@dataclass(frozen=True)
class Customer:
    customer_id: int
    name: str
    phone: str
    phone_digits: str
    email: str
    city: str

    @property
    def first_name(self) -> str:
        return self.name.split()[0] if self.name else ""


@dataclass(frozen=True)
class OrderItem:
    product_id: int
    quantity: int


@dataclass(frozen=True)
class Order:
    order_id: int
    customer_id: int
    order_date: date | None
    status: str
    total: float
    payment_method: str  # pix | credit | debit | boleto
    installments: int
    tracking_code: str
    estimated_delivery: date | None
    notes: str
    items: tuple[OrderItem, ...] = field(default_factory=tuple)


def _read_csv(path: Path) -> list[dict]:
    if not path.is_file():
        raise DataError(f"Missing data file: {path}")
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(fh)]


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _int(value: str, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _derive_availability(raw_status: str, stock: int) -> str:
    status = raw_status.lower()
    if status == "discontinued":
        return DISCONTINUED
    if status == "coming_soon":
        return COMING_SOON
    if status == "active":
        return IN_STOCK if stock > 0 else OUT_OF_STOCK
    return OUT_OF_STOCK  # unknown status: never claim it is purchasable


def _description_reliable(name: str, description: str) -> bool:
    name_brands, desc_brands = _brands_in(name), _brands_in(description)
    if desc_brands and not (desc_brands & name_brands):
        return False  # description is about another brand
    if _GENERIC_NAME.search(name) and not desc_brands:
        return False  # generic scrambled name + brandless description: cannot be verified
    return True


def _parse_payment(value: str) -> tuple[str, int]:
    m = re.fullmatch(r"(credit)_(\d+)x", value.lower())
    if m:
        return "credit", int(m.group(2))
    return value.lower(), 1


class Store:
    """In-memory view of the store's operational data."""

    def __init__(self, data_dir: Path | str):
        d = Path(data_dir)
        self.categories: dict[int, Category] = {
            _int(r["category_id"]): Category(_int(r["category_id"]), r["name"], r["description"])
            for r in _read_csv(d / "categories.csv")
        }

        self.products: dict[int, Product] = {}
        for r in _read_csv(d / "products.csv"):
            pid = _int(r["product_id"])
            cat = self.categories.get(_int(r["category_id"]))
            try:
                specs = json.loads(r.get("specs") or "{}")
            except json.JSONDecodeError:
                specs = {}
            stock = max(_int(r["stock_quantity"]), 0)
            self.products[pid] = Product(
                product_id=pid,
                name=r["name"],
                price=float(r["price_brl"]),
                category_id=_int(r["category_id"]),
                category_name=cat.name if cat else "Sem categoria",
                description=r["description"],
                stock=stock,
                raw_status=r["status"].lower(),
                specs=specs if isinstance(specs, dict) else {},
                created_at=_date(r.get("created_at", "")),
                availability=_derive_availability(r["status"], stock),
                description_reliable=_description_reliable(r["name"], r["description"]),
            )

        self.promotions: list[Promotion] = [
            Promotion(_int(r["promotion_id"]), _int(r["product_id"]), _int(r["discount_percent"]),
                      r["description"], r["is_active"] in {"1", "true", "True"})
            for r in _read_csv(d / "promotions.csv")
        ]

        self.customers: dict[int, Customer] = {}
        for r in _read_csv(d / "customers.csv"):
            cid = _int(r["customer_id"])
            self.customers[cid] = Customer(
                cid, r["name"], r["phone"], re.sub(r"\D", "", r["phone"]), r["email"].lower(), r["city"]
            )

        items: dict[int, list[OrderItem]] = {}
        for r in _read_csv(d / "order_items.csv"):
            items.setdefault(_int(r["order_id"]), []).append(OrderItem(_int(r["product_id"]), _int(r["quantity"], 1)))

        self.orders: dict[int, Order] = {}
        for r in _read_csv(d / "orders.csv"):
            oid = _int(r["order_id"])
            method, n = _parse_payment(r["payment_method"])
            self.orders[oid] = Order(
                order_id=oid,
                customer_id=_int(r["customer_id"]),
                order_date=_date(r["order_date"]),
                status=r["status"].lower(),
                total=float(r["total_brl"]),
                payment_method=method,
                installments=n,
                tracking_code=r["tracking_code"],
                estimated_delivery=_date(r["estimated_delivery"]),
                notes=r["notes"],
                items=tuple(items.get(oid, ())),
            )

    # -- lookups -----------------------------------------------------------------------------
    def active_promotion(self, product_id: int) -> Promotion | None:
        """Best *active* promotion for a product. Promotions are not cumulative (manual §6.2)."""
        active = [p for p in self.promotions if p.product_id == product_id and p.is_active]
        return max(active, key=lambda p: p.discount_percent, default=None)

    def category_by_name(self, name: str) -> Category | None:
        target = normalize(name)
        for c in self.categories.values():
            if normalize(c.name) == target:
                return c
        for c in self.categories.values():
            if target and (target in normalize(c.name) or normalize(c.name) in target):
                return c
        return None
