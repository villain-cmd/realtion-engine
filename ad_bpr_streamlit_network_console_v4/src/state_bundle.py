from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

import pandas as pd

from .io_utils import dataframe_to_csv_bytes, stable_json_hash
from .policy import Policy

STATE_SCHEMA_VERSION = 3

STATE_TABLE_COLUMNS = {
    "applied_settings": [
        "platform", "entity_type", "product_id", "keyword", "applied_cpc", "applied_status",
        "effective_at", "source", "run_id",
    ],
    "planned_settings": [
        "platform", "entity_type", "product_id", "keyword", "planned_cpc", "planned_status",
        "planned_at", "source", "run_id",
    ],
    "manual_overrides": [
        "override_id", "platform", "entity_type", "product_id", "keyword", "override_mode",
        "override_cpc", "effective_from", "effective_until", "active", "reason", "created_run_id",
    ],
    "action_history": [
        "run_id", "platform", "entity_type", "product_id", "keyword", "period_start", "period_end",
        "previous_cpc", "recommended_cpc", "operator_cpc", "final_cpc", "action", "decision_status",
        "operator_action", "operator_reason", "applied_at", "outcome_due_date", "outcome_status",
    ],
    "metric_history": [
        "run_id", "platform", "entity_type", "product_id", "keyword", "period_start", "period_end",
        "clicks", "cost", "sales_attr", "orders_attr", "actual_cpc", "cvr_attr", "roas_attr_pct",
        "registered_cpc", "captured_at",
    ],
    "run_history": [
        "run_id", "created_at", "input_hash", "policy_hash", "quality_status", "quality_score",
        "item_rows", "keyword_rows", "duplicate_input",
    ],
}


def empty_table(name: str) -> pd.DataFrame:
    return pd.DataFrame(columns=STATE_TABLE_COLUMNS[name])


@dataclass
class StateBundle:
    policy: Policy = field(default_factory=Policy)
    applied_settings: pd.DataFrame = field(default_factory=lambda: empty_table("applied_settings"))
    planned_settings: pd.DataFrame = field(default_factory=lambda: empty_table("planned_settings"))
    manual_overrides: pd.DataFrame = field(default_factory=lambda: empty_table("manual_overrides"))
    action_history: pd.DataFrame = field(default_factory=lambda: empty_table("action_history"))
    metric_history: pd.DataFrame = field(default_factory=lambda: empty_table("metric_history"))
    run_history: pd.DataFrame = field(default_factory=lambda: empty_table("run_history"))
    manifest: dict = field(default_factory=dict)

    @classmethod
    def empty(cls, policy: Policy | None = None) -> "StateBundle":
        return cls(policy=policy or Policy(), manifest={
            "schema_version": STATE_SCHEMA_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_run_id": None,
        })

    @classmethod
    def from_zip_bytes(cls, raw: bytes | BinaryIO) -> "StateBundle":
        if hasattr(raw, "read"):
            raw = raw.read()
        with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
            names = set(zf.namelist())
            manifest = json.loads(zf.read("state_manifest.json")) if "state_manifest.json" in names else {}
            policy_raw = json.loads(zf.read("policy.json")) if "policy.json" in names else {}
            tables = {}
            for name in STATE_TABLE_COLUMNS:
                filename = f"{name}.csv"
                if filename in names:
                    tables[name] = pd.read_csv(io.BytesIO(zf.read(filename)), dtype=str, keep_default_na=False)
                else:
                    tables[name] = empty_table(name)
            return cls(policy=Policy.from_dict(policy_raw), manifest=manifest, **tables)

    def to_zip_bytes(self) -> bytes:
        self.manifest.setdefault("schema_version", STATE_SCHEMA_VERSION)
        self.manifest["exported_at"] = datetime.now(timezone.utc).isoformat()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("state_manifest.json", json.dumps(self.manifest, ensure_ascii=False, indent=2, default=str))
            zf.writestr("policy.json", json.dumps(self.policy.to_dict(), ensure_ascii=False, indent=2, default=str))
            for name in STATE_TABLE_COLUMNS:
                df = getattr(self, name)
                for c in STATE_TABLE_COLUMNS[name]:
                    if c not in df.columns:
                        df[c] = ""
                zf.writestr(f"{name}.csv", dataframe_to_csv_bytes(df[STATE_TABLE_COLUMNS[name]], "utf-8-sig"))
        return buffer.getvalue()

    def previous_row_counts(self) -> dict[str, int]:
        if self.run_history.empty:
            return {}
        last = self.run_history.iloc[-1]
        out = {}
        for entity, col in [("ITEM", "item_rows"), ("KEYWORD", "keyword_rows")]:
            try:
                out[entity] = int(float(last.get(col, 0)))
            except Exception:
                pass
        return out

    def known_input_hashes(self) -> set[str]:
        if self.run_history.empty or "input_hash" not in self.run_history:
            return set()
        return set(self.run_history["input_hash"].astype(str))


def make_run_id(input_hash: str, policy: Policy) -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run_{now}_{input_hash[:8]}_{stable_json_hash(policy.to_dict())[:6]}"


def merge_active_overrides(base: pd.DataFrame, overrides: pd.DataFrame, as_of: pd.Timestamp | None = None) -> pd.DataFrame:
    if base.empty or overrides is None or overrides.empty:
        return base
    now = as_of or pd.Timestamp.utcnow()
    ov = overrides.copy()
    for c in ["effective_from", "effective_until"]:
        ov[c] = pd.to_datetime(ov.get(c), errors="coerce", utc=True)
    active_text = ov.get("active", "true").astype(str).str.lower()
    ov = ov[active_text.isin(["1", "true", "yes", "y", "on"])]
    ov = ov[(ov["effective_from"].isna() | (ov["effective_from"] <= now)) & (ov["effective_until"].isna() | (ov["effective_until"] >= now))]
    if ov.empty:
        return base
    keys = ["platform", "entity_type", "product_id", "keyword"]
    ov = ov.sort_values("effective_from").drop_duplicates(keys, keep="last")
    cols = keys + ["override_mode", "override_cpc", "effective_from", "effective_until", "reason"]
    return base.merge(ov[cols], on=keys, how="left")
