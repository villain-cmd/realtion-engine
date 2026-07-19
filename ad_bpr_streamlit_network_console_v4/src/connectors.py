from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin

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
        return ConnectorResult(
            source="airregi",
            dataset="airregi_transactions",
            dataframe=data,
            metadata={"start_date": start_date, "end_date": end_date, "pages_limited_to": max_pages},
        )

