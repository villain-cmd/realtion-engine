from __future__ import annotations

import pandas as pd

from src.network_viz import build_product_keyword_network
from src.relationship_canvas import build_relationship_html, prepare_graph_payload


def sample_decisions() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "entity_type": "ITEM",
                "product_id": "171-39",
                "product_name": "活性炭 衣類収納ケース",
                "keyword": "",
                "sales_attr": 220000,
                "clicks": 320,
                "cost": 42000,
                "orders_attr": 44,
                "roas_attr_pct": 523.8,
                "expected_incremental_profit": 12000,
                "current_bid": 32,
                "recommended_cpc": 38,
                "category": "衣類収納",
                "product_group": "収納",
                "action": "SCALE_UP",
                "decision_status": "SCALE_UP_PROFITABLE",
            },
            {
                "entity_type": "ITEM",
                "product_id": "131-46",
                "product_name": "布団収納袋",
                "keyword": "",
                "sales_attr": 90000,
                "clicks": 180,
                "cost": 30000,
                "orders_attr": 12,
                "roas_attr_pct": 300.0,
                "expected_incremental_profit": -2000,
                "current_bid": 36,
                "recommended_cpc": 26,
                "category": "寝具収納",
                "product_group": "収納",
                "action": "SCALE_DOWN",
                "decision_status": "SCALE_DOWN_BELOW_TARGET",
            },
            {
                "entity_type": "KEYWORD",
                "product_id": "171-39",
                "product_name": "活性炭 衣類収納ケース",
                "keyword": "衣類 収納",
                "sales_attr": 150000,
                "clicks": 210,
                "cost": 26000,
                "orders_attr": 31,
                "roas_attr_pct": 576.9,
                "expected_incremental_profit": 8000,
                "current_bid": 34,
                "recommended_cpc": 40,
                "category": "衣類収納",
                "product_group": "収納",
                "action": "SCALE_UP",
                "decision_status": "SCALE_UP_PROFITABLE",
            },
            {
                "entity_type": "KEYWORD",
                "product_id": "171-39",
                "product_name": "活性炭 衣類収納ケース",
                "keyword": "収納 ケース",
                "sales_attr": 70000,
                "clicks": 110,
                "cost": 16000,
                "orders_attr": 13,
                "roas_attr_pct": 437.5,
                "expected_incremental_profit": 2200,
                "current_bid": 32,
                "recommended_cpc": 32,
                "category": "衣類収納",
                "product_group": "収納",
                "action": "KEEP",
                "decision_status": "KEEP_WITHIN_BAND",
            },
            {
                "entity_type": "KEYWORD",
                "product_id": "131-46",
                "product_name": "布団収納袋",
                "keyword": "収納 ケース",
                "sales_attr": 40000,
                "clicks": 90,
                "cost": 15000,
                "orders_attr": 7,
                "roas_attr_pct": 266.7,
                "expected_incremental_profit": -1800,
                "current_bid": 36,
                "recommended_cpc": 24,
                "category": "寝具収納",
                "product_group": "収納",
                "action": "SCALE_DOWN",
                "decision_status": "SCALE_DOWN_BELOW_TARGET",
            },
        ]
    )


def test_custom_canvas_payload_uses_product_id_and_sales() -> None:
    network = build_product_keyword_network(sample_decisions(), max_products=10, max_keywords=10)
    payload = prepare_graph_payload(network)
    product = next(node for node in payload["nodes"] if node["type"] == "PRODUCT" and node["productId"] == "171-39")
    assert product["label"] == "171-39"
    assert product["sales"] == 220000
    assert product["action"] == "SCALE_UP"
    assert isinstance(product["x"], float)
    assert isinstance(product["y"], float)


def test_custom_canvas_html_has_no_graph_ui_library_dependency() -> None:
    network = build_product_keyword_network(sample_decisions(), max_products=10, max_keywords=10)
    html = build_relationship_html(network)
    lowered = html.lower()
    assert "<canvas" in lowered
    assert "graphcanvas" in lowered
    assert "d3.js" not in lowered
    assert "cytoscape" not in lowered
    assert "vis-network" not in lowered
    assert "sigma.js" not in lowered
    assert "plotly" not in lowered
    assert "requestanimationframe" in lowered
    assert "doubleclick" not in lowered  # DOM event is intentionally dblclick.
    assert "dblclick" in lowered


def test_custom_canvas_includes_mouse_and_search_controls() -> None:
    network = build_product_keyword_network(sample_decisions(), max_products=10, max_keywords=10)
    html = build_relationship_html(network)
    assert "商品管理番号 / キーワードを検索" in html
    assert "ホイール：ズーム" in html
    assert "1階層だけ表示" in html
    assert "PNG保存" in html
