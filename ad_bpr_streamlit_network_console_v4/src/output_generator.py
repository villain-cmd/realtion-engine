from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .io_utils import dataframe_to_csv_bytes

RPP_UPLOAD_COLUMNS = [
    "コントロールカラム", "商品管理番号", "商品名", "価格", "商品URL", "商品CPC", "キーワード", "キーワードCPC", "目安CPC"
]


def _norm(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def _ensure_rpp_columns(setting_df: pd.DataFrame) -> pd.DataFrame:
    out = setting_df.copy()
    for col in RPP_UPLOAD_COLUMNS:
        if col not in out:
            out[col] = ""
    return out[RPP_UPLOAD_COLUMNS].copy()


def _approved_map(finalized: pd.DataFrame, entity_type: str) -> pd.DataFrame:
    eligible = finalized["upload_eligible"] if "upload_eligible" in finalized else finalized["final_approved"]
    d = finalized[(finalized["entity_type"] == entity_type) & eligible].copy()
    if d.empty:
        return d
    d["product_key"] = _norm(d["product_id"])
    d["keyword_key"] = _norm(d["keyword"])
    d["final_cpc"] = pd.to_numeric(d["final_cpc"], errors="coerce")
    return d


def build_rakuten_full_outputs(setting_df: pd.DataFrame, finalized: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if setting_df is None or setting_df.empty:
        return {}
    base = _ensure_rpp_columns(setting_df)
    base["_product_key"] = _norm(base["商品管理番号"])
    base["_keyword_key"] = _norm(base["キーワード"])

    item_decisions = _approved_map(finalized, "ITEM")
    keyword_decisions = _approved_map(finalized, "KEYWORD")

    # One complete row per product. This prevents the same item CPC update from being repeated for every keyword row.
    item_out = base.sort_values(["_product_key", "_keyword_key"]).drop_duplicates("_product_key", keep="first").copy()
    item_out["キーワード"] = ""
    item_out["キーワードCPC"] = ""
    item_out["目安CPC"] = ""
    item_out["コントロールカラム"] = ""
    rollback_item = item_out.copy()
    if not item_decisions.empty:
        item_map = item_decisions.drop_duplicates("product_key", keep="last").set_index("product_key")["final_cpc"].to_dict()
        mask = item_out["_product_key"].isin(item_map)
        item_out.loc[mask, "商品CPC"] = item_out.loc[mask, "_product_key"].map(item_map).astype("Int64")
        item_out.loc[mask, "コントロールカラム"] = "u"
        rollback_item.loc[mask, "コントロールカラム"] = "u"

    keyword_out = base[base["_keyword_key"].ne("")].copy()
    keyword_out["コントロールカラム"] = ""
    rollback_keyword = keyword_out.copy()
    if not keyword_decisions.empty:
        kw_map = keyword_decisions.drop_duplicates(["product_key", "keyword_key"], keep="last").set_index(["product_key", "keyword_key"])["final_cpc"].to_dict()
        keys = list(zip(keyword_out["_product_key"], keyword_out["_keyword_key"]))
        changed = [k in kw_map for k in keys]
        values = [kw_map.get(k, np.nan) for k in keys]
        mask = pd.Series(changed, index=keyword_out.index)
        keyword_out.loc[mask, "キーワードCPC"] = pd.Series(values, index=keyword_out.index)[mask].astype("Int64")
        keyword_out.loc[mask, "コントロールカラム"] = "u"
        rollback_keyword.loc[mask, "コントロールカラム"] = "u"

    def clean(df: pd.DataFrame) -> pd.DataFrame:
        return df.drop(columns=[c for c in ["_product_key", "_keyword_key"] if c in df], errors="ignore")[RPP_UPLOAD_COLUMNS].reset_index(drop=True)

    return {
        "rakuten_item_full": clean(item_out),
        "rakuten_keyword_full": clean(keyword_out),
        "rakuten_item_rollback": clean(rollback_item),
        "rakuten_keyword_rollback": clean(rollback_keyword),
    }



def build_upload_validation(finalized: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "platform", "entity_type", "product_id", "keyword", "current_bid", "final_cpc",
        "final_approved", "final_changed", "upload_match", "upload_eligible", "operator_action",
        "decision_status", "reason_code",
    ]
    out = finalized[[c for c in cols if c in finalized]].copy()
    out["upload_validation_status"] = np.select(
        [
            ~out.get("final_approved", False).astype(bool),
            out.get("final_approved", False).astype(bool) & ~out.get("final_changed", False).astype(bool),
            out.get("final_changed", False).astype(bool) & ~out.get("upload_match", False).astype(bool),
            out.get("upload_eligible", False).astype(bool),
        ],
        ["NOT_APPROVED", "APPROVED_NO_CHANGE", "NO_SETTING_ROW", "OUTPUT_INCLUDED"],
        default="REVIEW",
    )
    return out

def build_generic_yahoo_review(finalized: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "platform", "entity_type", "product_id", "keyword", "current_bid", "recommended_cpc", "operator_cpc",
        "final_cpc", "final_approved", "action", "decision_status", "reason_code", "reason",
    ]
    return finalized[[c for c in cols if c in finalized]].copy()


def make_download_payloads(setting_df: pd.DataFrame | None, finalized: pd.DataFrame, run_id: str) -> dict[str, tuple[str, bytes, str]]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payloads = {
        "decision_detail": (
            f"decision_detail_{run_id}.csv",
            dataframe_to_csv_bytes(finalized, "utf-8-sig"),
            "text/csv",
        ),
        "yahoo_review": (
            f"yahoo_normalized_review_{run_id}.csv",
            dataframe_to_csv_bytes(build_generic_yahoo_review(finalized), "utf-8-sig"),
            "text/csv",
        ),
        "upload_validation": (
            f"upload_validation_{run_id}.csv",
            dataframe_to_csv_bytes(build_upload_validation(finalized), "utf-8-sig"),
            "text/csv",
        ),
    }
    if setting_df is not None and not setting_df.empty:
        for key, df in build_rakuten_full_outputs(setting_df, finalized).items():
            payloads[key] = (
                f"{key}_{run_id}_{stamp}.csv",
                dataframe_to_csv_bytes(df, "cp932"),
                "text/csv",
            )
    return payloads
