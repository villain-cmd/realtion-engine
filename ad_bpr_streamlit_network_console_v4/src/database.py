from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping

import pandas as pd

from .connectors import ConnectorConfigurationError, ConnectorDependencyError


DATASET_RE = re.compile(r"^[0-9A-Za-z_][0-9A-Za-z_.:-]{0,127}$")


def _json_value(value: Any) -> Any:
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if hasattr(value, "item"):
        return value.item()
    return value


def _record_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        {str(key): _json_value(value) for key, value in payload.items() if not str(key).startswith("_")},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def add_lineage(df: pd.DataFrame, source: str, dataset: str) -> pd.DataFrame:
    """Attach deterministic keys used by both interactive and scheduled ingestion."""

    out = df.copy()
    now = datetime.now(timezone.utc).isoformat()
    out["_source"] = source
    out["_dataset"] = dataset
    out["_ingested_at"] = now
    hashes = [
        _record_hash({str(column): row[column] for column in out.columns if not str(column).startswith("_")})
        for _, row in out.iterrows()
    ]
    out["_record_hash"] = hashes
    if "_record_key" not in out:
        source_ids = out.get("_source_record_id", pd.Series("", index=out.index)).fillna("").astype(str).str.strip()
        out["_record_key"] = [source_id or digest for source_id, digest in zip(source_ids, hashes)]
    return out


def _dataset_name(value: str) -> str:
    name = str(value).strip()
    if not DATASET_RE.fullmatch(name):
        raise ValueError("dataset名は英数字・_・.・:・- の128文字以内で指定してください。")
    return name


def _occurred_at(payload: Mapping[str, Any]) -> str | None:
    for key in ("occurred_at", "created_at", "updated_at", "date", "Date", "OrderTime"):
        value = payload.get(key)
        if value not in (None, ""):
            parsed = pd.to_datetime(str(value), errors="coerce", utc=True)
            return parsed.isoformat() if not pd.isna(parsed) else None
    return None


class SupabaseStore:
    """Tiny PostgREST client backed by one generic JSONB table.

    One physical table keeps setup and maintenance small while datasets remain
    logically separated by the ``dataset`` column.
    """

    def __init__(
        self,
        url: str,
        service_role_key: str,
        table: str = "source_records",
        session: Any | None = None,
    ) -> None:
        if not str(url).strip():
            raise ConnectorConfigurationError("不足している設定: supabase.url")
        if not str(service_role_key).strip():
            raise ConnectorConfigurationError("不足している設定: supabase.service_role_key")
        if not re.fullmatch(r"[0-9A-Za-z_]+", str(table)):
            raise ConnectorConfigurationError("supabase.table は英数字と_だけで指定してください。")
        self.url = str(url).rstrip("/")
        self.key = str(service_role_key)
        self.table = str(table)
        if session is None:
            try:
                import requests
            except ImportError as exc:
                raise ConnectorDependencyError("Supabase接続には requests が必要です。") from exc
            session = requests.Session()
        self.session = session

    @classmethod
    def from_config(cls, config: Mapping[str, Any], session: Any | None = None) -> "SupabaseStore":
        return cls(
            url=str(config.get("url", "")),
            service_role_key=str(config.get("service_role_key", config.get("key", ""))),
            table=str(config.get("table", "source_records")),
            session=session,
        )

    @property
    def endpoint(self) -> str:
        return f"{self.url}/rest/v1/{self.table}"

    def _headers(self, prefer: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def read_frame(self, dataset: str, page_size: int = 1000, max_rows: int = 100_000) -> pd.DataFrame:
        dataset = _dataset_name(dataset)
        payloads: list[dict[str, Any]] = []
        for offset in range(0, max_rows, page_size):
            response = self.session.get(
                self.endpoint,
                headers=self._headers(),
                params={
                    "select": "payload",
                    "dataset": f"eq.{dataset}",
                    "order": "occurred_at.asc.nullslast,ingested_at.asc",
                    "limit": str(page_size),
                    "offset": str(offset),
                },
                timeout=30,
            )
            response.raise_for_status()
            rows = response.json()
            payloads.extend(dict(row.get("payload") or {}) for row in rows)
            if len(rows) < page_size:
                break
        return pd.DataFrame(payloads)

    def list_datasets(self) -> list[str]:
        response = self.session.get(
            self.endpoint,
            headers=self._headers(),
            params={"select": "dataset", "order": "dataset.asc", "limit": "10000"},
            timeout=30,
        )
        response.raise_for_status()
        return sorted({str(row["dataset"]) for row in response.json() if row.get("dataset")})

    def write_frame(
        self,
        dataset: str,
        df: pd.DataFrame,
        mode: str = "upsert",
        key_columns: Iterable[str] | None = None,
        chunk_size: int = 500,
    ) -> int:
        dataset = _dataset_name(dataset)
        mode = str(mode).lower()
        if mode not in {"append", "replace", "upsert"}:
            raise ValueError("mode は append / replace / upsert のいずれかです。")
        if df.empty:
            return 0
        incoming = df.copy()
        if "_record_hash" not in incoming or "_record_key" not in incoming:
            source = str(incoming.get("_source", pd.Series(["unknown"])).iloc[0])
            incoming = add_lineage(incoming, source=source, dataset=dataset)
        keys = list(key_columns or [])
        if keys:
            missing = [key for key in keys if key not in incoming]
            if missing:
                raise ValueError("key_columns がDataFrameにありません: " + ", ".join(missing))
            incoming["_record_key"] = [
                hashlib.sha256(
                    json.dumps(
                        [_json_value(row[key]) for key in keys],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                for _, row in incoming.iterrows()
            ]

        if mode == "replace":
            response = self.session.delete(
                self.endpoint,
                headers=self._headers(),
                params={"dataset": f"eq.{dataset}"},
                timeout=30,
            )
            response.raise_for_status()

        rows: list[dict[str, Any]] = []
        for _, row in incoming.iterrows():
            payload = {str(column): _json_value(row[column]) for column in incoming.columns}
            source = str(payload.get("_source") or "unknown")
            record_key = str(payload.get("_record_key") or payload["_record_hash"])
            if mode == "append":
                record_key = hashlib.sha256(
                    f"{record_key}:{payload.get('_ingested_at')}:{len(rows)}".encode("utf-8")
                ).hexdigest()
            rows.append(
                {
                    "source": source,
                    "dataset": dataset,
                    "record_key": record_key,
                    "record_hash": str(payload["_record_hash"]),
                    "occurred_at": _occurred_at(payload),
                    "ingested_at": str(payload.get("_ingested_at") or datetime.now(timezone.utc).isoformat()),
                    "payload": payload,
                }
            )

        prefer = "resolution=merge-duplicates,return=minimal" if mode == "upsert" else "return=minimal"
        params = {"on_conflict": "source,dataset,record_key"} if mode == "upsert" else {}
        for start in range(0, len(rows), chunk_size):
            response = self.session.post(
                self.endpoint,
                headers=self._headers(prefer),
                params=params,
                json=rows[start : start + chunk_size],
                timeout=60,
            )
            response.raise_for_status()
        return len(rows)
