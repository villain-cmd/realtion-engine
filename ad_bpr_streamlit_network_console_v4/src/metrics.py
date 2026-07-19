from __future__ import annotations

import math
import re
import warnings
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

import numpy as np
import pandas as pd

from .io_utils import LoadedCsv, coerce_numeric, normalize_text
from .policy import Policy

ROW_DATE_RE = re.compile(r"(20\d{2})年(\d{2})月(\d{2})日[～~-](20\d{2})年(\d{2})月(\d{2})日")


@dataclass
class QualityResult:
    status: str
    score: float
    summary: dict
    row_errors: pd.DataFrame
    warnings: list[str]


def _series(df: pd.DataFrame, name: str, default: object = pd.NA) -> pd.Series:
    if name in df.columns:
        return df[name]
    return pd.Series([default] * len(df), index=df.index)


def _parse_period_from_row(value: object) -> tuple[str | None, str | None]:
    if value is None or pd.isna(value):
        return None, None
    m = ROW_DATE_RE.search(str(value))
    if not m:
        return None, None
    ys, ms, ds, ye, me, de = map(int, m.groups())
    return date(ys, ms, ds).isoformat(), date(ye, me, de).isoformat()


def _window_days(start: object, end: object) -> float:
    try:
        s = pd.Timestamp(start)
        e = pd.Timestamp(end)
        return float((e - s).days + 1)
    except Exception:
        return np.nan


def normalize_report(loaded: LoadedCsv, platform_hint: str = "rakuten") -> pd.DataFrame:
    df = loaded.dataframe.copy()
    if loaded.file_type == "normalized_performance":
        out = pd.DataFrame(index=df.index)
        out["platform"] = normalize_text(_series(df, "platform", platform_hint)).str.lower()
        out["entity_type"] = normalize_text(_series(df, "entity_type", "ITEM")).str.upper()
        out["product_id"] = normalize_text(_series(df, "product_id"))
        out["keyword"] = normalize_text(_series(df, "keyword"))
        out["registered_bid_report"] = coerce_numeric(_series(df, "registered_bid"))
        out["impressions"] = coerce_numeric(_series(df, "impressions"))
        out["clicks"] = coerce_numeric(_series(df, "clicks"))
        out["cost"] = coerce_numeric(_series(df, "cost"))
        out["actual_cpc"] = coerce_numeric(_series(df, "actual_cpc"))
        out["sales_short"] = coerce_numeric(_series(df, "sales_short"))
        out["orders_short"] = coerce_numeric(_series(df, "orders_short"))
        out["sales_attr"] = coerce_numeric(_series(df, "sales_attr"))
        out["orders_attr"] = coerce_numeric(_series(df, "orders_attr"))
        out["ctr_pct"] = coerce_numeric(_series(df, "ctr_pct"))
        out["period_start"] = normalize_text(_series(df, "period_start", loaded.period_start or ""))
        out["period_end"] = normalize_text(_series(df, "period_end", loaded.period_end or ""))
    elif loaded.file_type in {"rakuten_rpp_item_report", "rakuten_rpp_keyword_report"}:
        is_keyword = loaded.file_type == "rakuten_rpp_keyword_report"
        out = pd.DataFrame(index=df.index)
        out["platform"] = "rakuten"
        out["entity_type"] = "KEYWORD" if is_keyword else "ITEM"
        out["product_id"] = normalize_text(_series(df, "商品管理番号"))
        out["keyword"] = normalize_text(_series(df, "キーワード")) if is_keyword else ""
        report_bid_col = "キーワードCPC" if is_keyword else "入札単価"
        out["registered_bid_report"] = coerce_numeric(_series(df, report_bid_col))
        out["impressions"] = coerce_numeric(_series(df, "表示回数"))
        out["clicks"] = coerce_numeric(_series(df, "クリック数(合計)"))
        out["cost"] = coerce_numeric(_series(df, "実績額(合計)"))
        out["actual_cpc"] = coerce_numeric(_series(df, "CPC実績(合計)"))
        out["sales_short"] = coerce_numeric(_series(df, "売上金額(合計12時間)"))
        out["orders_short"] = coerce_numeric(_series(df, "売上件数(合計12時間)"))
        out["sales_attr"] = coerce_numeric(_series(df, "売上金額(合計720時間)"))
        out["orders_attr"] = coerce_numeric(_series(df, "売上件数(合計720時間)"))
        out["ctr_pct"] = coerce_numeric(_series(df, "CTR(%)"))
        row_periods = _series(df, "日付").map(_parse_period_from_row)
        out["period_start"] = [p[0] or loaded.period_start for p in row_periods]
        out["period_end"] = [p[1] or loaded.period_end for p in row_periods]
    else:
        raise ValueError(f"実績レポートとして未対応の形式です: {loaded.file_type} ({loaded.name})")

    out["source_name"] = loaded.name
    out["source_hash"] = loaded.sha256
    out["snapshot_id"] = loaded.sha256[:16]
    out["executed_at"] = loaded.executed_at
    out["window_days"] = [
        _window_days(s, e) for s, e in zip(out["period_start"], out["period_end"])
    ]
    out["entity_key"] = (
        out["platform"].fillna("") + "|" + out["entity_type"].fillna("") + "|" +
        out["product_id"].fillna("") + "|" + out["keyword"].fillna("")
    )
    return out.reset_index(drop=True)


def normalize_reports(loaded_reports: Iterable[LoadedCsv], platform_hint: str = "rakuten") -> pd.DataFrame:
    frames = [normalize_report(x, platform_hint=platform_hint) for x in loaded_reports]
    if not frames:
        return pd.DataFrame()
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="The behavior of DataFrame concatenation with empty or all-NA entries is deprecated",
            category=FutureWarning,
        )
        out = pd.concat(frames, ignore_index=True)
    # A duplicated file may be intentionally re-run. Do not sum the same snapshot twice.
    out = out.drop_duplicates(subset=["snapshot_id", "entity_key"], keep="last")
    return out.reset_index(drop=True)


def build_setting_maps(setting_df: pd.DataFrame | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    item_cols = ["platform", "entity_type", "product_id", "keyword", "registered_cpc", "setting_source", "setting_row_exists"]
    if setting_df is None or setting_df.empty:
        return pd.DataFrame(columns=item_cols), pd.DataFrame(columns=item_cols)
    df = setting_df.copy()
    if "商品管理番号" not in df.columns:
        return pd.DataFrame(columns=item_cols), pd.DataFrame(columns=item_cols)
    product = normalize_text(_series(df, "商品管理番号"))
    keyword = normalize_text(_series(df, "キーワード"))
    item_cpc = coerce_numeric(_series(df, "商品CPC"))
    kw_cpc = coerce_numeric(_series(df, "キーワードCPC"))

    item = pd.DataFrame({
        "platform": "rakuten",
        "entity_type": "ITEM",
        "product_id": product,
        "keyword": "",
        "registered_cpc": item_cpc,
        "setting_source": "UPLOADED_SETTING",
        "setting_row_exists": True,
    })
    item = item[item["product_id"].ne("")].copy()
    item["_has_cpc"] = item["registered_cpc"].notna()
    item = item.sort_values(["product_id", "_has_cpc"], ascending=[True, False]).drop_duplicates("product_id", keep="first")
    item = item.drop(columns="_has_cpc")

    kw = pd.DataFrame({
        "platform": "rakuten",
        "entity_type": "KEYWORD",
        "product_id": product,
        "keyword": keyword,
        "registered_cpc": kw_cpc,
        "setting_source": "UPLOADED_SETTING",
        "setting_row_exists": True,
    })
    kw = kw[kw["product_id"].ne("") & kw["keyword"].ne("")].copy()
    kw = kw.drop_duplicates(["product_id", "keyword"], keep="last")
    return item.reset_index(drop=True), kw.reset_index(drop=True)


def normalize_product_master(master_df: pd.DataFrame | None, product_ids: Iterable[str], platform: str = "rakuten") -> pd.DataFrame:
    ids = pd.Series(sorted({str(x).strip() for x in product_ids if str(x).strip()}), name="product_id")
    base = pd.DataFrame({"product_id": ids})
    base["platform"] = platform
    base["common_product_id"] = base["product_id"]
    if master_df is None or master_df.empty:
        master = pd.DataFrame(columns=[
            "platform", "common_product_id", "product_id", "product_name", "category", "product_group",
            "gross_margin_rate", "other_promo_cost_rate", "item_min_cpc", "item_max_cpc",
            "stock_qty", "product_status", "launch_date", "role",
        ])
    else:
        master = master_df.copy()
        for col, default in {
            "platform": platform,
            "common_product_id": "",
            "product_id": "",
            "product_name": "",
            "category": "UNKNOWN",
            "product_group": "UNKNOWN",
            "gross_margin_rate": pd.NA,
            "other_promo_cost_rate": 0,
            "item_min_cpc": pd.NA,
            "item_max_cpc": pd.NA,
            "stock_qty": pd.NA,
            "product_status": "ACTIVE",
            "launch_date": pd.NA,
            "role": "PROFIT",
        }.items():
            if col not in master.columns:
                master[col] = default
        master["platform"] = normalize_text(master["platform"]).str.lower().replace("", platform)
        master["product_id"] = normalize_text(master["product_id"])
        master["common_product_id"] = normalize_text(master["common_product_id"])
        master["product_name"] = normalize_text(master["product_name"])
        master["category"] = normalize_text(master["category"]).replace("", "UNKNOWN")
        master["product_group"] = normalize_text(master["product_group"]).replace("", "UNKNOWN")
        for c in ["gross_margin_rate", "other_promo_cost_rate", "item_min_cpc", "item_max_cpc", "stock_qty"]:
            master[c] = coerce_numeric(master[c])
        # Accept 35 as 35% as well as 0.35.
        master.loc[master["gross_margin_rate"] > 1, "gross_margin_rate"] /= 100.0
        master.loc[master["other_promo_cost_rate"] > 1, "other_promo_cost_rate"] /= 100.0
        master["product_status"] = normalize_text(master["product_status"]).str.upper().replace("", "ACTIVE")
        master["role"] = normalize_text(master["role"]).str.upper().replace("", "PROFIT")
        master = master.drop_duplicates(["platform", "product_id"], keep="last")

    out = base.merge(master, on=["platform", "product_id"], how="left", suffixes=("", "_m"))
    out["common_product_id"] = out["common_product_id_m"].fillna(out["common_product_id"])
    out = out.drop(columns=[c for c in ["common_product_id_m"] if c in out.columns])
    defaults = {
        "category": "UNKNOWN",
        "product_group": "UNKNOWN",
        "other_promo_cost_rate": 0.0,
        "product_status": "ACTIVE",
        "role": "PROFIT",
    }
    for c, v in defaults.items():
        out[c] = out[c].fillna(v)
    return out


def _pick_window(group: pd.DataFrame, target_days: int, prefer_longer: bool = False) -> pd.Series:
    g = group.copy()
    end_dt = pd.to_datetime(g["period_end"], errors="coerce")
    latest_end = end_dt.max()
    if pd.notna(latest_end):
        # Prefer snapshots ending on the latest date; allow one-day difference.
        fresh = g[(latest_end - end_dt).dt.days.fillna(9999) <= 1]
        if not fresh.empty:
            g = fresh
    days = pd.to_numeric(g["window_days"], errors="coerce")
    penalty = (days - target_days).abs().fillna(100000)
    if prefer_longer:
        penalty = penalty + np.where(days < target_days, 5, 0)
    g = g.assign(_window_penalty=penalty, _end=pd.to_datetime(g["period_end"], errors="coerce"))
    g = g.sort_values(["_window_penalty", "_end"], ascending=[True, False])
    return g.iloc[0]


def select_analysis_windows(performance: pd.DataFrame, policy: Policy) -> pd.DataFrame:
    if performance.empty:
        return pd.DataFrame()
    keys = ["platform", "entity_type", "product_id", "keyword", "entity_key"]
    short_rows = []
    long_rows = []
    for _, g in performance.groupby(keys, dropna=False, sort=False):
        short_rows.append(_pick_window(g, policy.short_window_days, prefer_longer=False))
        long_rows.append(_pick_window(g, policy.long_window_days, prefer_longer=True))
    short = pd.DataFrame(short_rows).reset_index(drop=True)
    long = pd.DataFrame(long_rows).reset_index(drop=True)

    metric_cols = [
        "snapshot_id", "source_name", "source_hash", "period_start", "period_end", "executed_at", "window_days",
        "registered_bid_report", "impressions", "clicks", "cost", "actual_cpc", "sales_short",
        "orders_short", "sales_attr", "orders_attr", "ctr_pct",
    ]
    short_keep = keys + metric_cols
    long_keep = keys + metric_cols
    out = short[short_keep].merge(long[long_keep], on=keys, how="left", suffixes=("_shortwin", "_longwin"))
    out["trend_available"] = out["snapshot_id_shortwin"].ne(out["snapshot_id_longwin"])
    # Current decision metrics are the short-window snapshot. If only a long snapshot exists, this remains usable.
    for col in ["impressions", "clicks", "cost", "actual_cpc", "sales_short", "orders_short", "sales_attr", "orders_attr", "ctr_pct"]:
        out[col] = pd.to_numeric(out[f"{col}_shortwin"], errors="coerce")
        out[f"{col}_long"] = pd.to_numeric(out[f"{col}_longwin"], errors="coerce")
    out["cvr_attr"] = np.where(out["clicks"] > 0, out["orders_attr"] / out["clicks"], np.nan)
    out["roas_attr_pct"] = np.where(out["cost"] > 0, out["sales_attr"] / out["cost"] * 100.0, np.nan)
    out["aov_attr"] = np.where(out["orders_attr"] > 0, out["sales_attr"] / out["orders_attr"], np.nan)
    out["cvr_attr_long"] = np.where(out["clicks_long"] > 0, out["orders_attr_long"] / out["clicks_long"], np.nan)
    out["roas_attr_pct_long"] = np.where(out["cost_long"] > 0, out["sales_attr_long"] / out["cost_long"] * 100.0, np.nan)
    out["aov_attr_long"] = np.where(out["orders_attr_long"] > 0, out["sales_attr_long"] / out["orders_attr_long"], np.nan)
    return out.reset_index(drop=True)


def validate_performance(performance: pd.DataFrame, policy: Policy, previous_run_rows: dict[str, int] | None = None) -> QualityResult:
    if performance.empty:
        return QualityResult("BLOCK", 0.0, {"reason": "NO_DATA"}, pd.DataFrame(), ["実績データがありません。"])
    checks = []
    required = ["product_id", "clicks", "cost", "sales_attr", "orders_attr", "period_start", "period_end"]
    for idx, row in performance.iterrows():
        errors = []
        if not str(row.get("product_id", "")).strip():
            errors.append("MISSING_PRODUCT_ID")
        for col in ["clicks", "cost", "sales_attr", "orders_attr"]:
            v = row.get(col)
            if pd.isna(v):
                errors.append(f"MISSING_{col.upper()}")
            elif float(v) < 0:
                errors.append(f"NEGATIVE_{col.upper()}")
        if not row.get("period_start") or not row.get("period_end"):
            errors.append("MISSING_PERIOD")
        if errors:
            checks.append({"row_index": idx, "entity_key": row.get("entity_key", ""), "errors": "|".join(errors)})
    row_errors = pd.DataFrame(checks)
    error_rate = len(row_errors) / max(len(performance), 1)
    warnings: list[str] = []
    blocked = error_rate > policy.data_missing_block_rate

    duplicate_rows = int(performance.duplicated(["snapshot_id", "entity_key"], keep=False).sum())
    if duplicate_rows:
        warnings.append(f"同一スナップショット内の重複行 {duplicate_rows}件を検出しました。")

    row_count_drop = False
    if previous_run_rows:
        for entity_type, count in performance.groupby("entity_type").size().items():
            prev = previous_run_rows.get(str(entity_type))
            if prev and count < prev * (1.0 - policy.row_count_drop_block_rate):
                row_count_drop = True
                warnings.append(f"{entity_type}行数が前回{prev:,}件から{count:,}件へ急減しました。")
    blocked = blocked or row_count_drop

    score = max(0.0, 100.0 - error_rate * 100.0 - (20.0 if row_count_drop else 0.0) - min(10.0, duplicate_rows * 0.1))
    status = "BLOCK" if blocked else ("WARN" if warnings or not row_errors.empty else "PASS")
    summary = {
        "rows": int(len(performance)),
        "row_error_count": int(len(row_errors)),
        "row_error_rate": float(error_rate),
        "duplicate_rows": duplicate_rows,
        "row_count_drop": row_count_drop,
        "required_columns": required,
    }
    return QualityResult(status, round(score, 1), summary, row_errors, warnings)


def summarize_kpis(entity_df: pd.DataFrame, entity_type: str = "ITEM") -> dict[str, float]:
    if entity_df.empty:
        return {k: 0.0 for k in ["clicks", "cost", "sales_attr", "orders_attr", "roas_attr_pct", "cvr_attr", "actual_cpc"]}
    df = entity_df[entity_df["entity_type"].eq(entity_type)]
    if df.empty:
        df = entity_df
    clicks = float(df["clicks"].fillna(0).sum())
    cost = float(df["cost"].fillna(0).sum())
    sales = float(df["sales_attr"].fillna(0).sum())
    orders = float(df["orders_attr"].fillna(0).sum())
    return {
        "clicks": clicks,
        "cost": cost,
        "sales_attr": sales,
        "orders_attr": orders,
        "roas_attr_pct": sales / cost * 100.0 if cost else 0.0,
        "cvr_attr": orders / clicks if clicks else 0.0,
        "actual_cpc": cost / clicks if clicks else 0.0,
    }
