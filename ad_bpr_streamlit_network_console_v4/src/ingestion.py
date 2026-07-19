from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd

from .connectors import AirRegiConnector, ConnectorResult, GA4Connector, GoogleAdsConnector
from .sheets_store import GoogleSheetsStore, add_lineage


SOURCE_LABELS = {
    "google_sheets": "Google Sheets DB",
    "ga4": "GA4 API",
    "google_ads": "Google広告 API",
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
    if source == "airregi":
        return AirRegiConnector(config).fetch(start_date, end_date)
    raise ValueError(f"未対応のデータソースです: {source}")


def persist_result(
    store: GoogleSheetsStore,
    result: ConnectorResult,
    table: str | None = None,
    mode: str = "upsert",
) -> IngestionRun:
    destination = table or result.dataset
    frame = add_lineage(result.dataframe, result.source, result.dataset)
    rows = store.write_frame(destination, frame, mode=mode, key_columns=["_record_hash"])
    return IngestionRun(result=result, persisted_table=destination, persisted_rows=rows)

