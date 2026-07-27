from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin
from xml.etree import ElementTree

import pandas as pd


class ConnectorConfigurationError(ValueError):
    """Raised when a connector is selected without the required configuration."""


class ConnectorDependencyError(RuntimeError):
    """Raised when an optional API client library is not installed."""


@dataclass
class ConnectorResult:
    source: str
    dataset: str
    dataframe: pd.DataFrame
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_lineage(self) -> pd.DataFrame:
        out = self.dataframe.copy()
        out["_source"] = self.source
        out["_dataset"] = self.dataset
        out["_fetched_at"] = self.fetched_at
        return out


def _require(config: Mapping[str, Any], *keys: str) -> None:
    missing = [key for key in keys if not str(config.get(key, "")).strip()]
    if missing:
        raise ConnectorConfigurationError("不足している設定: " + ", ".join(missing))


def _attr(value: Any, path: str, default: Any = None) -> Any:
    current = value
    for part in path.split("."):
        if current is None:
            return default
        current = getattr(current, part, default)
    return current


class GA4Connector:
    DEFAULT_DIMENSIONS = ("date", "sessionDefaultChannelGroup")
    DEFAULT_METRICS = ("sessions", "totalUsers", "newUsers", "keyEvents", "totalRevenue")

    def __init__(
        self,
        property_id: str,
        service_account_info: Mapping[str, Any] | None = None,
        client: Any | None = None,
    ) -> None:
        self.property_id = str(property_id).replace("properties/", "").strip()
        if not self.property_id:
            raise ConnectorConfigurationError("不足している設定: property_id")
        self.service_account_info = dict(service_account_info or {})
        self._client = client

    def _build_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from google.analytics.data_v1beta import BetaAnalyticsDataClient
            from google.oauth2 import service_account
        except ImportError as exc:
            raise ConnectorDependencyError(
                "GA4接続には google-analytics-data と google-auth が必要です。"
            ) from exc
        if self.service_account_info:
            credentials = service_account.Credentials.from_service_account_info(
                self.service_account_info,
                scopes=["https://www.googleapis.com/auth/analytics.readonly"],
            )
            return BetaAnalyticsDataClient(credentials=credentials)
        return BetaAnalyticsDataClient()

    @staticmethod
    def response_to_frame(response: Any) -> pd.DataFrame:
        dimension_names = [header.name for header in getattr(response, "dimension_headers", [])]
        metric_names = [header.name for header in getattr(response, "metric_headers", [])]
        rows: list[dict[str, Any]] = []
        for row in getattr(response, "rows", []):
            record = {
                name: getattr(value, "value", "")
                for name, value in zip(dimension_names, getattr(row, "dimension_values", []))
            }
            record.update({
                name: getattr(value, "value", "")
                for name, value in zip(metric_names, getattr(row, "metric_values", []))
            })
            rows.append(record)
        out = pd.DataFrame(rows, columns=dimension_names + metric_names)
        for name in metric_names:
            if name in out:
                out[name] = pd.to_numeric(out[name], errors="coerce")
        return out

    def fetch(
        self,
        start_date: str,
        end_date: str,
        dimensions: Iterable[str] | None = None,
        metrics: Iterable[str] | None = None,
        page_size: int = 100_000,
        max_pages: int = 20,
    ) -> ConnectorResult:
        try:
            from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest
        except ImportError as exc:
            raise ConnectorDependencyError("GA4接続には google-analytics-data が必要です。") from exc
        dimension_names = tuple(dimensions or self.DEFAULT_DIMENSIONS)
        metric_names = tuple(metrics or self.DEFAULT_METRICS)
        client = self._build_client()
        frames: list[pd.DataFrame] = []
        offset = 0
        for _ in range(max_pages):
            request = RunReportRequest(
                property=f"properties/{self.property_id}",
                dimensions=[Dimension(name=name) for name in dimension_names],
                metrics=[Metric(name=name) for name in metric_names],
                date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
                limit=page_size,
                offset=offset,
                return_property_quota=True,
            )
            response = client.run_report(request=request)
            frame = self.response_to_frame(response)
            frames.append(frame)
            offset += len(frame)
            row_count = int(getattr(response, "row_count", offset) or offset)
            if frame.empty or offset >= row_count:
                break
        data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if not data.empty:
            identity_columns = [
                column
                for column in ("date", "sessionDefaultChannelGroup")
                if column in data.columns
            ]
            data["_source_record_id"] = data[identity_columns].fillna("").astype(str).agg("|".join, axis=1)
        return ConnectorResult(
            source="ga4",
            dataset="ga4_channel_daily",
            dataframe=data,
            metadata={
                "property_id": self.property_id,
                "start_date": start_date,
                "end_date": end_date,
                "dimensions": list(dimension_names),
                "metrics": list(metric_names),
            },
        )


class GoogleAdsConnector:
    def __init__(self, config: Mapping[str, Any], client: Any | None = None) -> None:
        self.config = {key: value for key, value in dict(config).items() if value not in (None, "")}
        _require(self.config, "customer_id")
        self.customer_id = str(self.config["customer_id"]).replace("-", "")
        self._client = client

    def _build_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from google.ads.googleads.client import GoogleAdsClient
        except ImportError as exc:
            raise ConnectorDependencyError("Google広告接続には google-ads が必要です。") from exc
        auth_keys = {
            "developer_token",
            "client_id",
            "client_secret",
            "refresh_token",
            "login_customer_id",
            "linked_customer_id",
            "use_application_default_credentials",
            "json_key_file_path",
        }
        client_config = {key: value for key, value in self.config.items() if key in auth_keys}
        client_config["use_proto_plus"] = True
        _require(client_config, "developer_token")
        return GoogleAdsClient.load_from_dict(client_config)

    @staticmethod
    def rows_to_frame(rows: Iterable[Any]) -> pd.DataFrame:
        records = []
        for row in rows:
            cost_micros = _attr(row, "metrics.cost_micros", 0) or 0
            records.append({
                "_source_record_id": "|".join(
                    [
                        str(_attr(row, "segments.date", "")),
                        str(_attr(row, "customer.id", "")),
                        str(_attr(row, "campaign.id", "")),
                        str(_attr(row, "ad_group.id", "")),
                    ]
                ),
                "date": str(_attr(row, "segments.date", "")),
                "customer_id": str(_attr(row, "customer.id", "")),
                "campaign_id": str(_attr(row, "campaign.id", "")),
                "campaign_name": str(_attr(row, "campaign.name", "")),
                "ad_group_id": str(_attr(row, "ad_group.id", "")),
                "ad_group_name": str(_attr(row, "ad_group.name", "")),
                "impressions": int(_attr(row, "metrics.impressions", 0) or 0),
                "clicks": int(_attr(row, "metrics.clicks", 0) or 0),
                "cost": float(cost_micros) / 1_000_000,
                "conversions": float(_attr(row, "metrics.conversions", 0) or 0),
                "conversion_value": float(_attr(row, "metrics.conversions_value", 0) or 0),
                "all_conversions": float(_attr(row, "metrics.all_conversions", 0) or 0),
            })
        return pd.DataFrame(records)

    def fetch(self, start_date: str, end_date: str) -> ConnectorResult:
        client = self._build_client()
        service = client.get_service("GoogleAdsService")
        query = f"""
            SELECT
              segments.date,
              customer.id,
              campaign.id,
              campaign.name,
              ad_group.id,
              ad_group.name,
              metrics.impressions,
              metrics.clicks,
              metrics.cost_micros,
              metrics.conversions,
              metrics.conversions_value,
              metrics.all_conversions
            FROM ad_group
            WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
              AND campaign.status != 'REMOVED'
              AND ad_group.status != 'REMOVED'
            ORDER BY segments.date
        """
        rows = (row for batch in service.search_stream(customer_id=self.customer_id, query=query) for row in batch.results)
        data = self.rows_to_frame(rows)
        return ConnectorResult(
            source="google_ads",
            dataset="google_ads_ad_group_daily",
            dataframe=data,
            metadata={"customer_id": self.customer_id, "start_date": start_date, "end_date": end_date},
        )


SHOPIFY_ORDERS_QUERY = """
query OrdersForWarehouse($first: Int!, $after: String, $query: String!) {
  orders(first: $first, after: $after, query: $query, sortKey: CREATED_AT) {
    nodes {
      id
      name
      createdAt
      totalPriceSet {
        shopMoney {
          amount
          currencyCode
        }
      }
      lineItems(first: 250) {
        nodes {
          id
          sku
          name
          quantity
          originalTotalSet {
            shopMoney {
              amount
              currencyCode
            }
          }
        }
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
""".strip()


class ShopifyConnector:
    """Read order line items through the Shopify Admin GraphQL API."""

    def __init__(self, config: Mapping[str, Any], session: Any | None = None) -> None:
        self.config = dict(config)
        _require(self.config, "shop_domain", "access_token")
        domain = str(self.config["shop_domain"]).strip()
        domain = domain.removeprefix("https://").removeprefix("http://").rstrip("/")
        if not domain.endswith(".myshopify.com"):
            raise ConnectorConfigurationError("shop_domain は *.myshopify.com を指定してください。")
        self.shop_domain = domain
        self.api_version = str(self.config.get("api_version", "2026-07"))
        if session is None:
            try:
                import requests
            except ImportError as exc:
                raise ConnectorDependencyError("Shopify接続には requests が必要です。") from exc
            session = requests.Session()
        self.session = session

    @property
    def endpoint(self) -> str:
        return f"https://{self.shop_domain}/admin/api/{self.api_version}/graphql.json"

    @staticmethod
    def orders_to_frame(orders: Iterable[Mapping[str, Any]]) -> pd.DataFrame:
        records: list[dict[str, Any]] = []
        for order in orders:
            order_id = str(order.get("id", ""))
            order_money = dict((order.get("totalPriceSet") or {}).get("shopMoney") or {})
            line_items = list((order.get("lineItems") or {}).get("nodes") or [])
            for line in line_items:
                line_money = dict((line.get("originalTotalSet") or {}).get("shopMoney") or {})
                records.append(
                    {
                        "_source_record_id": f"{order_id}|{line.get('id', '')}",
                        "date": str(order.get("createdAt", ""))[:10],
                        "created_at": str(order.get("createdAt", "")),
                        "order_id": order_id,
                        "order_name": str(order.get("name", "")),
                        "line_item_id": str(line.get("id", "")),
                        "sku": str(line.get("sku") or ""),
                        "product_name": str(line.get("name") or ""),
                        "quantity": int(line.get("quantity") or 0),
                        "revenue": float(line_money.get("amount") or 0),
                        "currency": str(line_money.get("currencyCode") or order_money.get("currencyCode") or ""),
                        "order_total": float(order_money.get("amount") or 0),
                    }
                )
        return pd.DataFrame(records)

    def fetch(self, start_date: str, end_date: str, max_pages: int = 100) -> ConnectorResult:
        cursor: str | None = None
        orders: list[dict[str, Any]] = []
        query_filter = f"created_at:>={start_date} created_at:<={end_date}T23:59:59Z"
        for _ in range(max_pages):
            response = self.session.post(
                self.endpoint,
                headers={
                    "Content-Type": "application/json",
                    "X-Shopify-Access-Token": str(self.config["access_token"]),
                },
                json={
                    "query": SHOPIFY_ORDERS_QUERY,
                    "variables": {"first": 100, "after": cursor, "query": query_filter},
                },
                timeout=60,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("errors"):
                raise RuntimeError(f"Shopify GraphQLエラー: {payload['errors']}")
            connection = ((payload.get("data") or {}).get("orders") or {})
            orders.extend(connection.get("nodes") or [])
            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
            if not cursor:
                break
        return ConnectorResult(
            source="shopify",
            dataset="shopify_order_lines",
            dataframe=self.orders_to_frame(orders),
            metadata={
                "shop_domain": self.shop_domain,
                "api_version": self.api_version,
                "start_date": start_date,
                "end_date": end_date,
            },
        )


def _xml_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_to_value(element: ElementTree.Element) -> Any:
    children = list(element)
    if not children:
        return (element.text or "").strip()
    grouped: dict[str, list[Any]] = {}
    for child in children:
        grouped.setdefault(_xml_name(child.tag), []).append(_xml_to_value(child))
    return {key: values[0] if len(values) == 1 else values for key, values in grouped.items()}


def _as_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    return value if isinstance(value, list) else [value]


class YahooShoppingConnector:
    """Fetch Yahoo!ショッピング orders with OAuth2 and the official orderList API."""

    TOKEN_ENDPOINT = "https://auth.login.yahoo.co.jp/yconnect/v2/token"
    ORDER_ENDPOINT = "https://circus.shopping.yahooapis.jp/ShoppingWebService/V1/orderList"
    DEFAULT_FIELDS = (
        "OrderId,OrderTime,TotalPrice,PayStatus,ShipStatus,"
        "ItemId,Title,UnitPrice,Quantity"
    )

    def __init__(self, config: Mapping[str, Any], session: Any | None = None) -> None:
        self.config = {
            key: value for key, value in dict(config).items() if value not in (None, "")
        }
        _require(self.config, "seller_id")
        if not self.config.get("access_token"):
            _require(self.config, "client_id", "client_secret", "refresh_token")
        if session is None:
            try:
                import requests
            except ImportError as exc:
                raise ConnectorDependencyError("Yahoo!ショッピング接続には requests が必要です。") from exc
            session = requests.Session()
        self.session = session

    def _access_token(self) -> str:
        direct = str(self.config.get("access_token", "")).strip()
        if direct:
            return direct
        basic = base64.b64encode(
            f"{self.config['client_id']}:{self.config['client_secret']}".encode("utf-8")
        ).decode("ascii")
        response = self.session.post(
            str(self.config.get("token_endpoint", self.TOKEN_ENDPOINT)),
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "refresh_token", "refresh_token": str(self.config["refresh_token"])},
            timeout=30,
        )
        response.raise_for_status()
        token = str(response.json().get("access_token", "")).strip()
        if not token:
            raise RuntimeError("Yahoo! JAPAN Tokenエンドポイントからaccess_tokenが返りませんでした。")
        return token

    def _public_key_headers(self) -> dict[str, str]:
        public_key_pem = str(self.config.get("public_key", "")).strip().replace("\\n", "\n")
        if not public_key_pem:
            return {}
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import padding
        except ImportError as exc:
            raise ConnectorDependencyError("Yahoo!公開鍵認証には cryptography が必要です。") from exc
        public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
        authentication_value = (
            f"{self.config['seller_id']}:{int(time.time())}".encode("utf-8")
        )
        encrypted = public_key.encrypt(authentication_value, padding.PKCS1v15())
        return {
            "X-sws-signature": base64.b64encode(encrypted).decode("ascii"),
            "X-sws-signature-version": str(self.config.get("public_key_version", "1")),
        }

    @staticmethod
    def _request_xml(
        seller_id: str,
        start_date: str,
        end_date: str,
        start: int,
        page_size: int,
        fields: str,
    ) -> bytes:
        root = ElementTree.Element("Req")
        search = ElementTree.SubElement(root, "Search")
        ElementTree.SubElement(search, "Result").text = str(page_size)
        ElementTree.SubElement(search, "Start").text = str(start)
        ElementTree.SubElement(search, "Sort").text = "+order_time"
        condition = ElementTree.SubElement(search, "Condition")
        ElementTree.SubElement(condition, "OrderTimeFrom").text = start_date.replace("-", "") + "000000"
        ElementTree.SubElement(condition, "OrderTimeTo").text = end_date.replace("-", "") + "235959"
        ElementTree.SubElement(search, "Field").text = fields
        ElementTree.SubElement(root, "SellerId").text = seller_id
        return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)

    @staticmethod
    def response_to_orders(raw: bytes) -> tuple[list[dict[str, Any]], int | None]:
        root = ElementTree.fromstring(raw)
        errors = [
            (element.text or "").strip()
            for element in root.iter()
            if _xml_name(element.tag) in {"Message", "Detail"} and (element.text or "").strip()
        ]
        if _xml_name(root.tag) == "Error" or any(_xml_name(element.tag) == "Error" for element in root.iter()):
            raise RuntimeError("Yahoo!ショッピングAPIエラー: " + " / ".join(errors))
        orders = [
            dict(_xml_to_value(element))
            for element in root.iter()
            if _xml_name(element.tag) == "OrderInfo"
        ]
        total: int | None = None
        for element in root.iter():
            if _xml_name(element.tag) in {"TotalCount", "TotalResultsAvailable"}:
                try:
                    total = int((element.text or "").strip())
                except ValueError:
                    pass
                break
        return orders, total

    @staticmethod
    def orders_to_frame(orders: Iterable[Mapping[str, Any]]) -> pd.DataFrame:
        records: list[dict[str, Any]] = []
        for order in orders:
            order_id = str(order.get("OrderId", ""))
            order_time = str(order.get("OrderTime", ""))
            if len(order_time) == 14 and order_time.isdigit():
                order_time = (
                    f"{order_time[:4]}-{order_time[4:6]}-{order_time[6:8]}"
                    f"T{order_time[8:10]}:{order_time[10:12]}:{order_time[12:14]}+09:00"
                )
            item_container = order.get("Item") or order.get("ItemInfo") or order.get("Items") or []
            if isinstance(item_container, dict) and set(item_container).intersection({"Item", "ItemInfo"}):
                item_container = item_container.get("Item") or item_container.get("ItemInfo")
            items = _as_list(item_container)
            if not items:
                items = [{}]
            for index, item in enumerate(items):
                item = dict(item) if isinstance(item, Mapping) else {}
                item_id = str(item.get("ItemId") or item.get("ItemCode") or index)
                unit_price = float(item.get("UnitPrice") or item.get("Price") or 0)
                quantity = int(float(item.get("Quantity") or 0))
                records.append(
                    {
                        "_source_record_id": f"{order_id}|{item_id}",
                        "date": order_time[:10].replace("/", "-"),
                        "created_at": order_time,
                        "order_id": order_id,
                        "item_id": item_id,
                        "product_name": str(item.get("Title") or item.get("ItemName") or ""),
                        "quantity": quantity,
                        "revenue": unit_price * quantity,
                        "order_total": float(order.get("TotalPrice") or 0),
                        "pay_status": str(order.get("PayStatus") or ""),
                        "ship_status": str(order.get("ShipStatus") or ""),
                        "currency": "JPY",
                    }
                )
        return pd.DataFrame(records)

    def fetch(
        self,
        start_date: str,
        end_date: str,
        page_size: int = 2000,
        max_pages: int = 100,
    ) -> ConnectorResult:
        token = self._access_token()
        orders: list[dict[str, Any]] = []
        start = 1
        endpoint = str(self.config.get("order_endpoint", self.ORDER_ENDPOINT))
        fields = str(self.config.get("fields", self.DEFAULT_FIELDS))
        for _ in range(max_pages):
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/xml; charset=utf-8",
            }
            headers.update(self._public_key_headers())
            response = self.session.post(
                endpoint,
                headers=headers,
                data=self._request_xml(
                    str(self.config["seller_id"]),
                    start_date,
                    end_date,
                    start,
                    page_size,
                    fields,
                ),
                timeout=60,
            )
            response.raise_for_status()
            page, total = self.response_to_orders(response.content)
            orders.extend(page)
            if not page or len(page) < page_size or (total is not None and len(orders) >= total):
                break
            start += len(page)
        return ConnectorResult(
            source="yahoo_shopping",
            dataset="yahoo_shopping_order_lines",
            dataframe=self.orders_to_frame(orders),
            metadata={
                "seller_id": str(self.config["seller_id"]),
                "start_date": start_date,
                "end_date": end_date,
            },
        )


class AirRegiConnector:
    """Configurable client for Airレジ's Data Integration API.

    Airレジ publishes the API-key/token setup flow, but endpoint specifications are
    supplied to integration systems. The base URL, endpoint, and credential header
    names therefore stay configurable instead of hard-coding an undocumented URL.
    """

    def __init__(self, config: Mapping[str, Any], session: Any | None = None) -> None:
        self.config = dict(config)
        _require(self.config, "base_url", "api_key", "api_token", "transactions_path")
        if session is None:
            try:
                import requests
            except ImportError as exc:
                raise ConnectorDependencyError("Airレジ接続には requests が必要です。") from exc
            session = requests.Session()
        self.session = session

    def _headers(self) -> dict[str, str]:
        key_header = str(self.config.get("api_key_header", "X-API-Key"))
        token_header = str(self.config.get("api_token_header", "Authorization"))
        token_prefix = str(self.config.get("api_token_prefix", "Bearer "))
        headers = {
            "Accept": "application/json",
            key_header: str(self.config["api_key"]),
            token_header: token_prefix + str(self.config["api_token"]),
        }
        headers.update({str(k): str(v) for k, v in dict(self.config.get("extra_headers", {})).items()})
        return headers

    @staticmethod
    def _items(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
        if isinstance(payload, list):
            return payload, None
        if not isinstance(payload, dict):
            raise ValueError("AirレジAPIの応答がJSONオブジェクトまたは配列ではありません。")
        items = payload.get("data", payload.get("items", payload.get("results", [])))
        if isinstance(items, dict):
            items = items.get("items", items.get("results", []))
        next_cursor = payload.get("next_cursor") or payload.get("nextCursor")
        return list(items or []), str(next_cursor) if next_cursor else None

    def fetch(self, start_date: str, end_date: str, max_pages: int = 100) -> ConnectorResult:
        url = urljoin(str(self.config["base_url"]).rstrip("/") + "/", str(self.config["transactions_path"]).lstrip("/"))
        params: dict[str, Any] = {
            str(self.config.get("start_date_param", "start_date")): start_date,
            str(self.config.get("end_date_param", "end_date")): end_date,
        }
        records: list[dict[str, Any]] = []
        for _ in range(max_pages):
            response = self.session.get(url, headers=self._headers(), params=params, timeout=30)
            response.raise_for_status()
            items, cursor = self._items(response.json())
            records.extend(items)
            if not cursor:
                break
            params[str(self.config.get("cursor_param", "cursor"))] = cursor
        data = pd.json_normalize(records, sep=".") if records else pd.DataFrame()
        if not data.empty:
            record_id_field = str(self.config.get("record_id_field", "transactionId"))
            if record_id_field in data:
                data["_source_record_id"] = data[record_id_field].fillna("").astype(str)
            field_aliases = {
                "date_field": "date",
                "occurred_at_field": "created_at",
                "product_id_field": "product_id",
                "product_name_field": "product_name",
                "quantity_field": "quantity",
                "amount_field": "revenue",
            }
            for config_key, standard_name in field_aliases.items():
                source_field = str(self.config.get(config_key, "")).strip()
                if source_field and source_field in data and standard_name not in data:
                    data[standard_name] = data[source_field]
        return ConnectorResult(
            source="airregi",
            dataset="airregi_transactions",
            dataframe=data,
            metadata={"start_date": start_date, "end_date": end_date, "pages_limited_to": max_pages},
        )
