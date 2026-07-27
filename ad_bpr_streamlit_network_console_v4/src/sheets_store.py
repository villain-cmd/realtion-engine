from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

import pandas as pd

from .connectors import ConnectorConfigurationError, ConnectorDependencyError


SHEET_TITLE_RE = re.compile(r"[^0-9A-Za-z_ぁ-んァ-ン一-龥-]+")


def safe_sheet_title(value: str) -> str:
    title = SHEET_TITLE_RE.sub("_", str(value).strip()).strip("_")
    return (title or "data")[:100]


def _cell_value(value: Any) -> Any:
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def add_lineage(df: pd.DataFrame, source: str, dataset: str) -> pd.DataFrame:
    out = df.copy()
    now = datetime.now(timezone.utc).isoformat()
    out["_source"] = source
    out["_dataset"] = dataset
    out["_ingested_at"] = now
    payload_columns = [c for c in out.columns if not str(c).startswith("_")]
    out["_record_hash"] = [
        hashlib.sha256(
            json.dumps({c: _cell_value(row[c]) for c in payload_columns}, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        for _, row in out.iterrows()
    ]
    return out


class GoogleSheetsStore:
    def __init__(self, spreadsheet: Any) -> None:
        self.spreadsheet = spreadsheet

    @classmethod
    def from_service_account_info(
        cls,
        spreadsheet_id: str,
        service_account_info: Mapping[str, Any],
    ) -> "GoogleSheetsStore":
        if not str(spreadsheet_id).strip():
            raise ConnectorConfigurationError("不足している設定: spreadsheet_id")
        if not service_account_info:
            raise ConnectorConfigurationError("不足している設定: gcp_service_account")
        try:
            import gspread
        except ImportError as exc:
            raise ConnectorDependencyError("Google Sheets接続には gspread が必要です。") from exc
        client = gspread.service_account_from_dict(dict(service_account_info))
        return cls(client.open_by_key(str(spreadsheet_id)))

    def _worksheet(self, table: str, create: bool = False, columns: int = 32) -> Any:
        title = safe_sheet_title(table)
        try:
            return self.spreadsheet.worksheet(title)
        except Exception as exc:
            if not create:
                raise KeyError(f"Google Sheetsに '{title}' タブがありません。") from exc
        return self.spreadsheet.add_worksheet(title=title, rows=1000, cols=max(columns, 16))

    def list_tables(self) -> list[str]:
        return [worksheet.title for worksheet in self.spreadsheet.worksheets()]

    def read_frame(self, table: str) -> pd.DataFrame:
        values = self._worksheet(table).get_all_values()
        if not values:
            return pd.DataFrame()
        headers = [str(value).strip() for value in values[0]]
        width = len(headers)
        rows = [(row + [""] * width)[:width] for row in values[1:]]
        return pd.DataFrame(rows, columns=headers).replace("", pd.NA).dropna(how="all").reset_index(drop=True)

    def write_frame(
        self,
        table: str,
        df: pd.DataFrame,
        mode: str = "upsert",
        key_columns: Iterable[str] | None = None,
    ) -> int:
        mode = str(mode).lower()
        if mode not in {"append", "replace", "upsert"}:
            raise ValueError("mode は append / replace / upsert のいずれかです。")
        incoming = df.copy()
        if incoming.empty:
            return 0
        keys = list(key_columns or (["_record_hash"] if "_record_hash" in incoming.columns else []))
        if mode == "upsert" and (not keys or any(key not in incoming.columns for key in keys)):
            raise ValueError("upsertには有効な key_columns または _record_hash が必要です。")
        worksheet = self._worksheet(table, create=True, columns=len(incoming.columns) + 4)
        existing = pd.DataFrame()
        values = worksheet.get_all_values()
        if values:
            headers = values[0]
            width = len(headers)
            existing = pd.DataFrame([(row + [""] * width)[:width] for row in values[1:]], columns=headers)
        if mode == "replace" or existing.empty:
            merged = incoming
        elif mode == "append":
            merged = pd.concat([existing, incoming], ignore_index=True, sort=False)
        else:
            # Preserve rows written before lineage keys were introduced. Pandas treats
            # repeated missing keys as duplicates, so give those legacy rows a stable,
            # unique placeholder before combining them with keyed incoming records.
            for key in keys:
                if key not in existing.columns:
                    existing[key] = [f"__legacy__{key}__{index}" for index in range(len(existing))]
                else:
                    missing = existing[key].isna() | existing[key].astype(str).str.strip().eq("")
                    existing.loc[missing, key] = [f"__legacy__{key}__{index}" for index in existing.index[missing]]
            merged = pd.concat([existing, incoming], ignore_index=True, sort=False)
            merged = merged.drop_duplicates(keys, keep="last")
        columns = list(dict.fromkeys([*existing.columns.tolist(), *incoming.columns.tolist()]))
        merged = merged.reindex(columns=columns)
        matrix = [columns] + [[_cell_value(value) for value in row] for row in merged.itertuples(index=False, name=None)]
        worksheet.clear()
        worksheet.update(values=matrix, range_name="A1", value_input_option="RAW")
        return int(len(incoming))
