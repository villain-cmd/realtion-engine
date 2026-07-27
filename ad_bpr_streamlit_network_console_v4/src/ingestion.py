from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd

from .connectors import (
    AirRegiConnector,
    ConnectorResult,
    GA4Connector,
    GoogleAdsConnector,
    ShopifyConnector,
    YahooShoppingConnector,
)
from .database import SupabaseStore, add_lineage


SOURCE_LABELS = {
    "supabase": "Supabase PostgreSQL",
    "ga4": "GA4 API",
    "google_ads": "Google広告 API",
    "shopify": "Shopify Admin API",
    "yahoo_shopping": "Yahoo!ショッピング 注文API",
    "airregi": "Airレジ API",
    "csv": "CSV（任意）",
}


@dataclass
class IngestionRun:
    result: ConnectorResult
    persisted_table: str | None = None
    persisted_rows: int = 0


def fetch_source(
    source: str,
    config: Mapping[str, Any],
    start_date: str,
    end_date: str,
    service_account_info: Mapping[str, Any] | None = None,
) -> ConnectorResult:
    if source == "ga4":
        return GA4Connector(
            property_id=str(config.get("property_id", "")),
            service_account_info=service_account_info,
        ).fetch(start_date, end_date)
    if source == "google_ads":
        return GoogleAdsConnector(config).fetch(start_date, end_date)
    if source == "shopify":
        return ShopifyConnector(config).fetch(start_date, end_date)
    if source == "yahoo_shopping":
        return YahooShoppingConnector(config).fetch(start_date, end_date)
    if source == "airregi":
        return AirRegiConnector(config).fetch(start_date, end_date)
    raise ValueError(f"未対応のデータソースです: {source}")


CANONICAL_COLUMNS = [
    "_source_record_id",
    "date",
    "source",
    "channel",
    "entity_type",
    "entity_id",
    "entity_name",
    "impressions",
    "clicks",
    "cost",
    "conversions",
    "orders",
    "quantity",
    "revenue",
    "sessions",
    "users",
    "currency",
]


def _series(frame: pd.DataFrame, column: str, default: Any = 0) -> pd.Series:
    if column in frame:
        return frame[column]
    return pd.Series(default, index=frame.index)


def canonical_metrics(result: ConnectorResult) -> pd.DataFrame:
    """Map the five APIs to one small cross-channel reporting contract."""

    source = result.source
    frame = result.dataframe.copy()
    if frame.empty:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)
    out = pd.DataFrame(index=frame.index)
    dates = _series(frame, "date", "").astype(str)
    compact_dates = dates.str.fullmatch(r"\d{8}", na=False)
    dates.loc[compact_dates] = pd.to_datetime(
        dates.loc[compact_dates], format="%Y%m%d", errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    out["date"] = dates.str[:10]
    out["source"] = source
    out["channel"] = source
    out["entity_type"] = "ACCOUNT"
    out["entity_id"] = ""
    out["entity_name"] = ""
    out["impressions"] = 0
    out["clicks"] = 0
    out["cost"] = 0.0
    out["conversions"] = 0.0
    out["orders"] = 0.0
    out["quantity"] = 0.0
    out["revenue"] = 0.0
    out["sessions"] = 0.0
    out["users"] = 0.0
    out["currency"] = ""

    if source == "ga4":
        out["channel"] = _series(frame, "sessionDefaultChannelGroup", "unknown").fillna("unknown")
        out["entity_type"] = "CHANNEL"
        out["entity_id"] = out["channel"]
        out["entity_name"] = out["channel"]
        out["conversions"] = pd.to_numeric(_series(frame, "keyEvents"), errors="coerce").fillna(0)
        out["revenue"] = pd.to_numeric(_series(frame, "totalRevenue"), errors="coerce").fillna(0)
        out["sessions"] = pd.to_numeric(_series(frame, "sessions"), errors="coerce").fillna(0)
        out["users"] = pd.to_numeric(_series(frame, "totalUsers"), errors="coerce").fillna(0)
    elif source == "google_ads":
        out["entity_type"] = "AD_GROUP"
        out["entity_id"] = _series(frame, "ad_group_id", "").astype(str)
        out["entity_name"] = _series(frame, "ad_group_name", "").astype(str)
        for column in ("impressions", "clicks", "cost", "conversions"):
            out[column] = pd.to_numeric(_series(frame, column), errors="coerce").fillna(0)
        out["revenue"] = pd.to_numeric(_series(frame, "conversion_value"), errors="coerce").fillna(0)
    elif source in {"shopify", "yahoo_shopping"}:
        out["entity_type"] = "PRODUCT"
        identifier = "sku" if source == "shopify" else "item_id"
        out["entity_id"] = _series(frame, identifier, "").astype(str)
        out["entity_name"] = _series(frame, "product_name", "").astype(str)
        out["orders"] = 1.0
        out["quantity"] = pd.to_numeric(_series(frame, "quantity"), errors="coerce").fillna(0)
        out["revenue"] = pd.to_numeric(_series(frame, "revenue"), errors="coerce").fillna(0)
        out["currency"] = _series(frame, "currency", "JPY").fillna("JPY").astype(str)
    elif source == "airregi":
        out["entity_type"] = "PRODUCT"
        out["entity_id"] = _series(frame, "product_id", "").astype(str)
        out["entity_name"] = _series(frame, "product_name", "").astype(str)
        out["orders"] = 1.0
        out["quantity"] = pd.to_numeric(_series(frame, "quantity", 1), errors="coerce").fillna(0)
        out["revenue"] = pd.to_numeric(_series(frame, "revenue"), errors="coerce").fillna(0)
        out["currency"] = "JPY"

    dimensions = [
        "date",
        "source",
        "channel",
        "entity_type",
        "entity_id",
        "entity_name",
        "currency",
    ]
    metrics = [
        "impressions",
        "clicks",
        "cost",
        "conversions",
        "orders",
        "quantity",
        "revenue",
        "sessions",
        "users",
    ]
    daily = (
        out.groupby(dimensions, dropna=False, as_index=False)[metrics]
        .sum()
        .reset_index(drop=True)
    )
    daily["_source_record_id"] = daily[dimensions].fillna("").astype(str).agg("|".join, axis=1)
    return daily[CANONICAL_COLUMNS]


def persist_result(
    store: SupabaseStore,
    result: ConnectorResult,
    table: str | None = None,
    mode: str = "upsert",
    persist_canonical: bool = True,
) -> IngestionRun:
    destination = table or result.dataset
    frame = add_lineage(result.dataframe, result.source, result.dataset)
    rows = store.write_frame(destination, frame, mode=mode)
    if persist_canonical:
        canonical = canonical_metrics(result)
        if not canonical.empty:
            canonical = add_lineage(canonical, result.source, "business_metrics_daily")
            store.write_frame("business_metrics_daily", canonical, mode="upsert")
    return IngestionRun(result=result, persisted_table=destination, persisted_rows=rows)
