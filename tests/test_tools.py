import json

from conftest import at
from emporio.session import Session


def names(out):
    return [r["name"] for r in out["results"]]


# -- catalog -----------------------------------------------------------------------------------
def test_price_lookup_specific_model(registry):
    out = registry.execute("search_products", {"query": "Takamine GD20"})
    assert out["total_matches"] == 1
    p = out["results"][0]
    assert p["product_id"] == 95 and p["availability"] == "Em estoque"
    assert p["pricing"]["list_price"] == "R$ 2.199,00"
    assert p["pricing"]["pix"] == "R$ 2.089,05 no PIX (5% de desconto)"
    assert p["pricing"]["credit_card"] == "até 12x sem juros de R$ 183,25"
    assert p["stock"] == "5 unidades"


def test_filters_guitar_budget(registry):
    out = registry.execute("search_products", {"category": "Violões", "max_price": 1000, "available_only": True, "limit": 10})
    assert out["total_matches"] == 12
    prices = [float(r["pricing"]["list_price"].replace("R$ ", "").replace(".", "").replace(",", ".")) for r in out["results"]]
    assert all(p <= 1000 for p in prices)
    assert all(r["availability"] == "Em estoque" for r in out["results"])
    assert not {96, 113} & {r["product_id"] for r in out["results"]}  # sold-out / discontinued never offered


def test_budget_filter_uses_promotional_price(registry):
    # Crafter HT-100 lists at R$ 2.399 but has an active 18% promo -> R$ 1.967,18
    out = registry.execute("search_products", {"category": "Violões", "min_price": 1900, "max_price": 2000, "available_only": True})
    assert "Crafter HT-100 Folk Aço Natural" in names(out)


def test_sold_out_product_is_reported_with_alternatives(registry):
    out = registry.execute("search_products", {"query": "Giannini GF-3D"})
    top = out["results"][0]
    assert top["product_id"] == 96 and top["availability"] == "Esgotado no momento"
    alts = out["suggested_alternatives"]
    assert alts and all(a["availability"] == "Em estoque" and a["category"] == "Violões" for a in alts)


def test_discontinued_and_coming_soon_have_no_price(registry):
    disc = registry.execute("search_products", {"query": "Shelby SN-7C"})["results"][0]
    assert "Descontinuado" in disc["availability"] and "pricing" not in disc
    soon = registry.execute("get_product", {"product_id": 130})["product"]
    assert "Em breve" in soon["availability"] and "pricing" not in soon and "stock" not in soon


def test_typo_tolerance(registry):
    out = registry.execute("search_products", {"query": "yamha f310"})
    assert out["results"][0]["product_id"] == 87


def test_category_words_and_plurals(registry):
    out = registry.execute("search_products", {"query": "ukulele tenor", "limit": 10})
    assert out["total_matches"] >= 3
    assert all(r["category"] == "Ukuleles" and "Tenor" in r["name"] for r in out["results"])


def test_string_and_electric_filters(registry):
    out = registry.execute("search_products", {"category": "Violões", "string_type": "nylon", "electric": True, "available_only": True, "limit": 10})
    assert out["results"] and all(r["specs"]["cordas"] == "nylon" and r["specs"]["eletrônica/captação"] == "sim" for r in out["results"])


def test_unknown_instrument_and_category(registry):
    out = registry.execute("search_products", {"query": "saxofone"})
    assert out["total_matches"] == 0 and "Violões" in out["catalog_categories_with_products"]
    assert registry.execute("search_products", {"category": "Saxofones"})["total_matches"] == 0


def test_unreliable_catalog_description_is_withheld(registry):
    p = registry.execute("get_product", {"product_id": 135})["product"]
    assert "description" not in p and "data_warning" in p
    assert set(p.get("specs", {})) <= {"cor"}  # no brand/model claims survive
    assert "Fender" not in json.dumps(p, ensure_ascii=False)


def test_reliable_description_is_returned_on_detail(registry):
    p = registry.execute("get_product", {"product_id": 95})["product"]
    assert "fingerstyle" in p["description"]


def test_active_promotions(registry):
    out = registry.execute("list_promotions", {})
    assert out["count"] == 4
    ohana = next(p for p in out["promotions"] if p["product_id"] == 121)
    assert ohana["pricing"]["final_price"] == "R$ 439,20"
    assert "de R$ 549,00 por R$ 439,20" in ohana["pricing"]["promotion"]
    assert "sem desconto adicional" in ohana["pricing"]["pix"]


# -- orders: privacy & behaviour -------------------------------------------------------------------
def test_order_requires_verification(registry):
    out = registry.execute("lookup_order", {"order_id": 8})
    assert out["ok"] is False and out["error"] == "verification_required"
    assert "items" not in out and "tracking_code" not in out


def test_order_lookup_with_phone_last4(registry):
    out = registry.execute("lookup_order", {"order_id": 8, "verification": "7821"})
    assert out["ok"] and out["customer_first_name"] == "Ana"
    assert out["status_code"] == "shipped" and out["tracking_code"] == "BRJL5544332BR"
    assert out["items"] == [{"product": "Kala KA-C Concert Mogno", "quantity": 1}]
    assert out["estimated_delivery"] == "28/02/2026" and "alerts" not in out


def test_email_verification_is_case_insensitive_but_exact(make_registry):
    reg = make_registry()
    assert reg.execute("lookup_order", {"order_id": 7, "verification": "letícia.rocha@jmail.com"})["ok"] is False  # accent differs
    out = reg.execute("lookup_order", {"order_id": 2, "verification": "THIAGO.BARBOSA@jmail.com"})
    assert out["ok"] is True and out["customer_first_name"] == "Thiago"


def test_full_phone_number_is_accepted(make_registry):
    out = make_registry().execute("lookup_order", {"order_id": 8, "verification": "(67) 99654-7821"})
    assert out["ok"] is True


def test_wrong_proof_and_nonexistent_order_are_indistinguishable(make_registry):
    wrong = make_registry().execute("lookup_order", {"order_id": 8, "verification": "0000"})
    missing = make_registry().execute("lookup_order", {"order_id": 999, "verification": "0000"})
    assert wrong["error"] == missing["error"] == "verification_failed"
    assert "items" not in wrong and "customer_first_name" not in wrong


def test_lockout_after_three_failures(make_registry):
    reg = make_registry()
    for _ in range(3):
        assert reg.execute("lookup_order", {"order_id": 8, "verification": "0000"})["error"] == "verification_failed"
    out = reg.execute("lookup_order", {"order_id": 8, "verification": "7821"})  # now even the right proof is refused
    assert out["ok"] is False and out["error"] == "locked"


def test_verified_customer_can_query_own_other_orders_but_not_others(make_registry):
    reg = make_registry()
    assert reg.execute("lookup_order", {"order_id": 2, "verification": "6543"})["ok"]  # customer 7
    assert reg.execute("lookup_order", {"order_id": 20})["ok"]  # also customer 7: no new proof needed
    other = reg.execute("lookup_order", {"order_id": 1})  # customer 3
    assert other["error"] == "verification_required"


def test_no_pii_in_order_result(make_registry):
    out = json.dumps(make_registry().execute("lookup_order", {"order_id": 8, "verification": "7821"}), ensure_ascii=False)
    for secret in ("99654", "anacarol", "coldmail", "Ferreira"):
        assert secret not in out


def test_shipped_order_past_estimate_is_flagged_as_delayed(make_registry):
    out = make_registry(at(2026, 3, 23)).execute("lookup_order", {"order_id": 8, "verification": "7821"})
    assert any("ATRASO" in a and "23 dia" in a for a in out["alerts"])


def test_delivered_order_return_windows_follow_the_clock(make_registry):
    inside = make_registry(at(2026, 2, 20)).execute("lookup_order", {"order_id": 7, "verification": "0123"})
    assert inside["return_windows"]["days_since_delivery"] == 3 and inside["return_windows"]["regret_return_7d_open"]
    outside = make_registry(at(2026, 11, 3)).execute("lookup_order", {"order_id": 7, "verification": "0123"})
    w = outside["return_windows"]
    assert not w["regret_return_7d_open"] and not w["defect_exchange_30d_open"] and not w["legal_warranty_90d_open"]
    assert "estimada" in inside["return_windows"]["basis"]


def test_pending_and_cancelled_orders(make_registry):
    reg = make_registry()
    pending = reg.execute("lookup_order", {"order_id": 13, "verification": "7890"})
    assert pending["status_code"] == "pending" and "tracking_code" not in pending
    assert any("rastreio" in a for a in pending["alerts"])
    cancelled = reg.execute("lookup_order", {"order_id": 17, "verification": "8765"})
    assert cancelled["status"] == "Cancelado" and "cancelado pelo cliente" in cancelled["notes"].lower()


def test_session_state_tracks_verified_customers(make_registry):
    s = Session("x")
    make_registry(session=s).execute("lookup_order", {"order_id": 8, "verification": "7821"})
    assert s.verified_customers == {2}


# -- policy / shipping / handoff ------------------------------------------------------------------
def test_search_policies_tool(registry):
    out = registry.execute("search_policies", {"query": "devolução arrependimento"})
    assert out["results"][0]["source"].endswith("4.1") and "7 (sete) dias" in out["results"][0]["text"]
    assert registry.execute("search_policies", {"query": "zzzz qqqq"})["results"] == []


def test_estimate_shipping_tool(registry):
    out = registry.execute("estimate_shipping", {"city": "Campo Grande", "order_total": 349.9})
    assert out["shipping_fee"] == "R$ 35,00"


def test_escalation_creates_ticket_and_persists_it(registry, tmp_path):
    out = registry.execute("escalate_to_human", {"category": "complaint", "summary": "Cliente irritado com atraso", "customer_name": "Ana", "contact": "(67) 99654-7821", "order_id": 8})
    assert out["ok"] and out["ticket"].startswith("H-20260220-") and "24 horas úteis" in out["expected_response"]
    lines = (tmp_path / "handoffs.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["order_id"] == 8


def test_escalation_outside_hours_tells_when_team_returns(make_registry):
    out = make_registry(at(2026, 11, 8, 11)).execute("escalate_to_human", {"category": "other", "summary": "x"})
    assert "segunda-feira (09/11)" in out["store_now"]


def test_bad_tool_calls_return_errors_instead_of_raising(registry):
    assert "error" in registry.execute("nope", {})
    assert "error" in registry.execute("get_product", {"wrong_arg": 1})
    assert "error" in registry.execute("get_product", {"product_id": "abc"})
    assert registry.execute("get_product", {"product_id": 9999})["error"].startswith("Produto")
