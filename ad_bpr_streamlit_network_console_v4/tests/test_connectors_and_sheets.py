from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from src.connectors import AirRegiConnector, GA4Connector, GoogleAdsConnector
from src.sheets_store import GoogleSheetsStore, add_lineage, safe_sheet_title


class FakeWorksheet:
    def __init__(self, title, values=None):
        self.title = title
        self.values = values or []

    def get_all_values(self):
        return self.values

    def clear(self):
        self.values = []

    def update(self, values, range_name, value_input_option):
        assert range_name == "A1"
        assert value_input_option == "RAW"
        self.values = values


class FakeSpreadsheet:
    def __init__(self):
        self.tabs = {}

    def worksheet(self, title):
        if title not in self.tabs:
            raise RuntimeError("missing")
        return self.tabs[title]

    def add_worksheet(self, title, rows, cols):
        self.tabs[title] = FakeWorksheet(title)
        return self.tabs[title]

    def worksheets(self):
        return list(self.tabs.values())


def ns(**kwargs):
    return SimpleNamespace(**kwargs)


def test_ga4_response_is_normalized_to_dataframe():
    response = ns(
        dimension_headers=[ns(name="date")],
        metric_headers=[ns(name="sessions")],
        rows=[ns(dimension_values=[ns(value="20260718")], metric_values=[ns(value="42")])],
    )
    frame = GA4Connector.response_to_frame(response)
    assert frame.to_dict("records") == [{"date": "20260718", "sessions": 42}]


def test_google_ads_rows_convert_micros_and_metrics():
    row = ns(
        segments=ns(date="2026-07-18"),
        customer=ns(id=1),
        campaign=ns(id=2, name="Search"),
        ad_group=ns(id=3, name="Brand"),
        metrics=ns(impressions=100, clicks=10, cost_micros=1_250_000, conversions=2, conversions_value=8000, all_conversions=2),
    )
    frame = GoogleAdsConnector.rows_to_frame([row])
    assert frame.iloc[0]["cost"] == 1.25
    assert frame.iloc[0]["conversion_value"] == 8000


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, headers, params, timeout):
        self.calls.append((url, headers, dict(params), timeout))
        return FakeResponse({"data": [{"transactionId": "T1", "amount": 1200}]})


def test_airregi_uses_configurable_endpoint_and_credentials():
    session = FakeSession()
    connector = AirRegiConnector({
        "base_url": "https://api.example.test/v1/",
        "transactions_path": "transactions",
        "api_key": "key",
        "api_token": "token",
    }, session=session)
    result = connector.fetch("2026-07-01", "2026-07-18")
    assert result.dataframe.iloc[0]["transactionId"] == "T1"
    assert session.calls[0][1]["X-API-Key"] == "key"
    assert session.calls[0][1]["Authorization"] == "Bearer token"


def test_google_sheets_store_upserts_by_record_hash():
    store = GoogleSheetsStore(FakeSpreadsheet())
    first = add_lineage(pd.DataFrame([{"date": "2026-07-18", "sessions": 42}]), "ga4", "daily")
    duplicate = add_lineage(pd.DataFrame([{"date": "2026-07-18", "sessions": 42}]), "ga4", "daily")
    store.write_frame("ga4/daily", first, mode="upsert", key_columns=["_record_hash"])
    store.write_frame("ga4/daily", duplicate, mode="upsert", key_columns=["_record_hash"])
    loaded = store.read_frame("ga4/daily")
    assert len(loaded) == 1
    assert safe_sheet_title("ga4/daily") == "ga4_daily"


def test_upsert_requires_a_key():
    store = GoogleSheetsStore(FakeSpreadsheet())
    with pytest.raises(ValueError):
        store.write_frame("x", pd.DataFrame([{"a": 1}]), mode="upsert")


def test_upsert_preserves_legacy_rows_without_record_hash():
    spreadsheet = FakeSpreadsheet()
    spreadsheet.tabs["daily"] = FakeWorksheet("daily", [["date", "sessions"], ["2026-07-17", "10"], ["2026-07-18", "20"]])
    store = GoogleSheetsStore(spreadsheet)
    incoming = add_lineage(pd.DataFrame([{"date": "2026-07-19", "sessions": 30}]), "ga4", "daily")
    store.write_frame("daily", incoming, mode="upsert", key_columns=["_record_hash"])
    assert len(store.read_frame("daily")) == 3
