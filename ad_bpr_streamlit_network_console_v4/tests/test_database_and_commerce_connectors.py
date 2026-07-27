from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from src.connectors import ShopifyConnector, YahooShoppingConnector
from src.database import SupabaseStore, add_lineage
from src.ingestion import canonical_metrics


class FakeResponse:
    def __init__(self, payload=None, content: bytes = b""):
        self.payload = payload
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class ShopifySession:
    def __init__(self):
        self.calls = []

    def post(self, url, headers, json, timeout):
        self.calls.append((url, headers, json, timeout))
        return FakeResponse(
            {
                "data": {
                    "orders": {
                        "nodes": [
                            {
                                "id": "gid://shopify/Order/1",
                                "name": "#1001",
                                "createdAt": "2026-07-26T03:00:00Z",
                                "totalPriceSet": {
                                    "shopMoney": {"amount": "1800", "currencyCode": "JPY"}
                                },
                                "lineItems": {
                                    "nodes": [
                                        {
                                            "id": "gid://shopify/LineItem/10",
                                            "sku": "BEANS-01",
                                            "name": "Coffee beans",
                                            "quantity": 2,
                                            "originalTotalSet": {
                                                "shopMoney": {
                                                    "amount": "1800",
                                                    "currencyCode": "JPY",
                                                }
                                            },
                                        }
                                    ]
                                },
                            }
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        )


def test_shopify_orders_are_flattened_and_keyed():
    session = ShopifySession()
    result = ShopifyConnector(
        {
            "shop_domain": "coffee.myshopify.com",
            "access_token": "secret",
            "api_version": "2026-07",
        },
        session=session,
    ).fetch("2026-07-25", "2026-07-26")
    assert result.dataset == "shopify_order_lines"
    assert result.dataframe.iloc[0]["sku"] == "BEANS-01"
    assert result.dataframe.iloc[0]["revenue"] == 1800
    assert "created_at:>=2026-07-25" in session.calls[0][2]["variables"]["query"]
    assert session.calls[0][1]["X-Shopify-Access-Token"] == "secret"


YAHOO_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<ResultSet>
  <TotalCount>1</TotalCount>
  <OrderInfo>
    <OrderId>coffee-1001</OrderId>
    <OrderTime>2026-07-26T12:30:00+09:00</OrderTime>
    <TotalPrice>1200</TotalPrice>
    <PayStatus>1</PayStatus>
    <ShipStatus>0</ShipStatus>
    <Item>
      <ItemId>DRIP-01</ItemId>
      <Title>Drip bag</Title>
      <UnitPrice>600</UnitPrice>
      <Quantity>2</Quantity>
    </Item>
  </OrderInfo>
</ResultSet>
"""


class YahooSession:
    def __init__(self):
        self.calls = []

    def post(self, url, headers, data, timeout):
        self.calls.append((url, headers, data, timeout))
        return FakeResponse(content=YAHOO_XML)


def test_yahoo_order_xml_is_flattened():
    session = YahooSession()
    result = YahooShoppingConnector(
        {"seller_id": "coffee", "access_token": "short-lived"},
        session=session,
    ).fetch("2026-07-25", "2026-07-26")
    row = result.dataframe.iloc[0]
    assert row["order_id"] == "coffee-1001"
    assert row["item_id"] == "DRIP-01"
    assert row["quantity"] == 2
    assert row["revenue"] == 1200
    assert session.calls[0][1]["Authorization"] == "Bearer short-lived"
    assert b"<OrderTimeFrom>20260725000000</OrderTimeFrom>" in session.calls[0][2]
    assert b"<OrderTimeTo>20260726235959</OrderTimeTo>" in session.calls[0][2]


class SupabaseSession:
    def __init__(self):
        self.posts = []

    def post(self, url, headers, params, json, timeout):
        self.posts.append((url, headers, params, json, timeout))
        return FakeResponse([])


def test_supabase_upsert_uses_composite_conflict_key_and_json_payload():
    session = SupabaseSession()
    store = SupabaseStore(
        "https://project.supabase.co",
        "service-role",
        session=session,
    )
    frame = add_lineage(
        pd.DataFrame(
            [
                {
                    "_source_record_id": "2026-07-26|campaign-1",
                    "date": "2026-07-26",
                    "clicks": 4,
                }
            ]
        ),
        "google_ads",
        "google_ads_ad_group_daily",
    )
    assert store.write_frame("google_ads_ad_group_daily", frame) == 1
    _, headers, params, rows, _ = session.posts[0]
    assert params["on_conflict"] == "source,dataset,record_key"
    assert "resolution=merge-duplicates" in headers["Prefer"]
    assert rows[0]["record_key"] == "2026-07-26|campaign-1"
    assert rows[0]["payload"]["clicks"] == 4


def test_shopify_maps_to_common_business_metrics():
    result = SimpleNamespace(
        source="shopify",
        dataframe=pd.DataFrame(
            [
                {
                    "_source_record_id": "order|line",
                    "date": "2026-07-26",
                    "sku": "BEANS-01",
                    "product_name": "Coffee beans",
                    "quantity": 2,
                    "revenue": 1800,
                    "currency": "JPY",
                }
            ]
        ),
    )
    metrics = canonical_metrics(result)
    assert metrics.iloc[0]["entity_type"] == "PRODUCT"
    assert metrics.iloc[0]["entity_id"] == "BEANS-01"
    assert metrics.iloc[0]["orders"] == 1
    assert metrics.iloc[0]["revenue"] == 1800
