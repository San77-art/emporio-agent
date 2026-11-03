from datetime import date

import pytest

from conftest import at
from emporio.business import (
    format_brl,
    holidays,
    installment_options,
    quote_price,
    return_windows,
    shipping_estimate,
    store_status,
)


def test_format_brl():
    assert format_brl(1299) == "R$ 1.299,00"
    assert format_brl(599.9) == "R$ 599,90"
    assert format_brl(11499) == "R$ 11.499,00"


@pytest.mark.parametrize(
    "price, max_n, parcel",
    [
        (2199, 12, "183.25"),  # 12x >= R$100
        (159.9, 3, "53.30"),  # 4x would be R$39.98 (< R$80)
        (429.9, 5, "85.98"),  # 6x = 71.65 < 80
        (599, 6, "99.83"),  # 6x ok (>=80); 7x = 85.57 < 100
        (49.9, 1, "49.90"),  # 2x = 24.95 < 50 -> cash only
    ],
)
def test_installments_follow_manual(price, max_n, parcel):
    best = installment_options(price)[-1]
    assert best["times"] == max_n
    assert str(best["parcel"]) == parcel


def test_pix_discount_only_without_promotion():
    plain = quote_price(2199)
    assert str(plain.pix_price) == "2089.05"
    promo = quote_price(549, (20, "Desconto Exclusivo"))
    assert str(promo.final_price) == "439.20"
    assert promo.pix_price is None
    assert "sem desconto adicional" in promo.as_dict()["pix"]
    assert "de R$ 549,00 por R$ 439,20" in promo.as_dict()["promotion"]


def test_shipping_campo_grande_threshold_is_strictly_above_500():
    assert shipping_estimate("Campo Grande", 500)["shipping_fee"] == "R$ 35,00"
    assert "grátis" in shipping_estimate("Campo Grande", 500.01)["shipping_fee"]
    assert "grátis" in shipping_estimate("campo grande - MS", 1000)["shipping_fee"]


def test_shipping_other_cities_and_missing_city():
    out = shipping_estimate("Dourados", 1000, large_item=True)
    assert any("SEDEX" in o for o in out["options"])
    assert "cotação individual" in out["large_item"]
    assert shipping_estimate(None)["needs_city"] is True


def test_store_hours():
    assert store_status(at(2026, 11, 3, 10, 29))["is_open"]  # Tuesday
    early = store_status(at(2026, 11, 3, 8, 0))
    assert not early["is_open"] and early["next_open"] == "hoje às 09:00"
    sat_after = store_status(at(2026, 11, 7, 13, 0))  # Saturday closes at 13:00 sharp
    assert not sat_after["is_open"] and "segunda-feira (09/11)" in sat_after["next_open"]
    sunday = store_status(at(2026, 11, 8, 11, 0))
    assert not sunday["is_open"] and "domingo" in sunday["summary"]
    assert store_status(at(2026, 11, 7, 12, 59))["is_open"]


def test_holiday_closes_store():
    finados = store_status(at(2026, 11, 2, 10, 0))  # Monday, Finados
    assert not finados["is_open"]
    assert "Finados" in finados["summary"] and "terça-feira (03/11)" in finados["next_open"]


def test_movable_holidays_2026():
    h = holidays(2026)
    assert h[date(2026, 4, 3)] == "Sexta-feira Santa"  # Easter 2026-04-05
    assert h[date(2026, 6, 4)] == "Corpus Christi"
    assert h[date(2026, 8, 26)] == "Aniversário de Campo Grande"


def test_return_windows_boundaries():
    delivered = date(2026, 2, 17)
    assert return_windows(delivered, date(2026, 2, 24))["regret_return_7d_open"] is True  # day 7
    assert return_windows(delivered, date(2026, 2, 25))["regret_return_7d_open"] is False  # day 8
    w = return_windows(delivered, date(2026, 3, 19))  # day 30
    assert w["defect_exchange_30d_open"] and w["legal_warranty_90d_open"]
    w = return_windows(delivered, date(2026, 3, 20))  # day 31
    assert not w["defect_exchange_30d_open"] and w["legal_warranty_90d_open"]
    assert not return_windows(delivered, date(2026, 5, 19))["legal_warranty_90d_open"]  # day 91
