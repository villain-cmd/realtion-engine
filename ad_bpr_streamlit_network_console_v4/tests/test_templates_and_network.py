from __future__ import annotations

import io
import zipfile

import pandas as pd

from src.network_viz import build_keyword_similarity_network, build_product_keyword_network
from src.template_catalog import TEMPLATES, all_column_dictionary, template_pack_bytes
from src.text_mining import extract_terms, normalize_phrase


def sample_decisions() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "entity_type": "ITEM", "product_id": "A", "product_name": "布団収納袋", "keyword": "",
                "sales_attr": 100000, "clicks": 100, "cost": 10000, "orders_attr": 10, "roas_attr_pct": 1000,
                "expected_incremental_profit": 5000, "category": "寝具", "product_group": "布団収納",
            },
            {
                "entity_type": "ITEM", "product_id": "B", "product_name": "衣類収納ケース", "keyword": "",
                "sales_attr": 60000, "clicks": 80, "cost": 12000, "orders_attr": 6, "roas_attr_pct": 500,
                "expected_incremental_profit": 1000, "category": "衣類", "product_group": "衣類収納",
            },
            {
                "entity_type": "KEYWORD", "product_id": "A", "product_name": "布団収納袋", "keyword": "布団 収納袋",
                "sales_attr": 70000, "clicks": 60, "cost": 6000, "orders_attr": 7, "roas_attr_pct": 1166,
                "expected_incremental_profit": 3500, "category": "寝具", "product_group": "布団収納",
            },
            {
                "entity_type": "KEYWORD", "product_id": "A", "product_name": "布団収納袋", "keyword": "敷布団 収納",
                "sales_attr": 30000, "clicks": 40, "cost": 4000, "orders_attr": 3, "roas_attr_pct": 750,
                "expected_incremental_profit": 1500, "category": "寝具", "product_group": "布団収納",
            },
            {
                "entity_type": "KEYWORD", "product_id": "B", "product_name": "衣類収納ケース", "keyword": "衣類 収納袋",
                "sales_attr": 60000, "clicks": 80, "cost": 12000, "orders_attr": 6, "roas_attr_pct": 500,
                "expected_incremental_profit": 1000, "category": "衣類", "product_group": "衣類収納",
            },
        ]
    )


def test_three_input_templates_have_definitions_and_pack() -> None:
    assert set(TEMPLATES) == {"performance", "settings", "product_master"}
    for template in TEMPLATES.values():
        assert not template.dataframe.empty
        assert {"column", "requirement", "type", "example", "definition"}.issubset(template.columns.columns)
        assert template.to_bytes()
    dictionary = all_column_dictionary()
    assert dictionary["input_key"].nunique() == 3
    with zipfile.ZipFile(io.BytesIO(template_pack_bytes())) as zf:
        names = set(zf.namelist())
    assert "input_column_dictionary_utf8.csv" in names
    assert any(name.startswith("01_performance") for name in names)
    assert any(name.startswith("02_current_bid") for name in names)
    assert any(name.startswith("03_product_master") for name in names)


def test_text_normalization_and_terms() -> None:
    assert normalize_phrase(" 布団　収納袋 ") == "布団 収納袋"
    terms = extract_terms("立てて収納できる布団収納袋")
    assert any("布団" in term or "収納" in term for term in terms)


def test_product_keyword_network_separates_node_types() -> None:
    network = build_product_keyword_network(sample_decisions(), max_products=10, max_keywords=20)
    assert set(network.nodes["node_type"]) == {"PRODUCT", "KEYWORD"}
    assert len(network.edges) == 3
    assert network.summary["products"] == 2
    assert network.summary["keywords"] == 3


def test_keyword_similarity_network_returns_keyword_nodes() -> None:
    network = build_keyword_similarity_network(sample_decisions(), threshold=0.1, max_keywords=20)
    assert not network.nodes.empty
    assert network.nodes["node_type"].eq("KEYWORD").all()
