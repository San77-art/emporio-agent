import pytest

from emporio.data import DISCONTINUED, COMING_SOON, IN_STOCK, OUT_OF_STOCK, DataError, Store, normalize
from emporio.policies import parse_sections


def test_counts(store):
    assert (len(store.products), len(store.categories), len(store.customers), len(store.orders), len(store.promotions)) == (65, 9, 50, 20, 25)
    assert sum(len(o.items) for o in store.orders.values()) == 22


def test_availability_derivation(store):
    assert store.products[96].availability == OUT_OF_STOCK  # active but stock 0
    assert store.products[113].availability == DISCONTINUED
    assert store.products[136].availability == DISCONTINUED  # discontinued even though stock > 0
    assert store.products[130].availability == COMING_SOON  # coming_soon even though stock > 0
    assert store.products[95].availability == IN_STOCK


def test_unreliable_descriptions_are_exactly_the_scrambled_ones(store):
    bad = sorted(p.product_id for p in store.products.values() if not p.description_reliable)
    assert bad == [135, 136, 137, 139, 140, 142, 144]
    assert store.products[95].description_reliable  # Takamine description matches name
    assert store.products[138].description_reliable  # Music Man name + Music Man StingRay text


def test_promotions(store):
    active = sorted(p.product_id for p in store.promotions if p.is_active)
    assert active == [90, 94, 121, 127]
    assert store.active_promotion(96) is None  # only inactive promos
    assert store.active_promotion(121).discount_percent == 20


def test_payment_parsing(store):
    assert (store.orders[2].payment_method, store.orders[2].installments) == ("credit", 6)
    assert (store.orders[1].payment_method, store.orders[1].installments) == ("pix", 1)


def test_normalize():
    assert normalize("Violões Nylon") == "violoes nylon"


def test_missing_data_dir(tmp_path):
    with pytest.raises(DataError):
        Store(tmp_path)


# -- policies ----------------------------------------------------------------------------------
def test_manual_is_parsed_into_numbered_sections(policies):
    ids = {s.section_id for s in policies.sections}
    assert {"2", "3", "3.1", "4.1", "4.2", "4.3", "4.4", "5.1", "5.2", "5.3", "6.2", "7.1", "7.3", "8.1", "9"} <= ids
    assert len(policies.sections) == 26


def test_list_items_are_not_mistaken_for_headings():
    raw = "7.2 Fluxo de Atendimento Padrão\n1. Saudação: cumprimentar o cliente\n2. Entendimento: identificar a necessidade\n"
    sections = parse_sections(raw)
    assert [s.section_id for s in sections] == ["7.2"]
    assert "Saudação" in sections[0].text


@pytest.mark.parametrize(
    "question, expected",
    [
        ("Me arrependi da minha compra, posso devolver meu pedido?", "4.1"),
        ("Qual o horário de funcionamento aos sábados?", "2"),
        ("Vocês aceitam PIX? Tem desconto?", "3"),
        ("Em quantas vezes posso parcelar no cartão?", "3.1"),
        ("O violão chegou com defeito, o que faço?", "4.2"),
        ("Como rastrear meu pedido?", "5.3"),
        ("Qual o prazo de entrega para Dourados?", "5.2"),
        ("O frete é grátis?", "5.1"),
        ("Quero excluir meus dados pessoais", "9"),
        ("A promoção de Black Friday vale junto com o desconto do PIX?", "6.2"),
        ("Quanto tempo de garantia tem?", "8.1"),
        ("Vocês vendem cordas e palhetas?", "1"),
        ("Quero fazer uma reclamação", "7.3"),
    ],
)
def test_policy_retrieval_finds_the_right_section(policies, question, expected):
    top3 = [s.section_id for s in policies.search(question, top_k=3)]
    assert expected in top3, f"{question!r} -> {top3}"


def test_policy_search_returns_nothing_for_gibberish(policies):
    assert policies.search("xyzzy qwerty", top_k=3) == []
