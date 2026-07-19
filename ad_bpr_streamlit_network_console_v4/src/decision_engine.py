from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd

from .policy import Policy


HARD_STOP_STATUSES = {"OUT_OF_STOCK", "DISCONTINUED", "RESERVED", "INACTIVE", "STOP"}


def _clip(value: float, low: float, high: float) -> float:
    return float(max(low, min(high, value)))


def _ceil_yen(value: float) -> int:
    return int(math.ceil(float(value)))


def _bool_text(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _days_since(value: object, as_of: pd.Timestamp) -> float | None:
    ts = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(ts):
        return None
    now = as_of
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    return float((now - ts).total_seconds() / 86400.0)


def _beta_posterior_stats(orders: float, clicks: float, prior_cvr: float, strength: float) -> tuple[float, float, float]:
    orders = max(0.0, min(float(orders), float(clicks)))
    clicks = max(0.0, float(clicks))
    prior_cvr = _clip(float(prior_cvr), 0.000001, 0.999999)
    strength = max(float(strength), 0.0)
    a = orders + prior_cvr * strength
    b = max(clicks - orders, 0.0) + (1.0 - prior_cvr) * strength
    mean = a / (a + b) if a + b else prior_cvr
    var = (a * b) / (((a + b) ** 2) * (a + b + 1.0)) if a + b > 0 else 0.0
    sd = math.sqrt(max(var, 0.0))
    # One-sided ~80% lower bound. Conservative enough for scale-up screening without scipy.
    lower = max(0.0, mean - 1.2816 * sd)
    return mean, lower, sd


def _group_priors(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    work = df.copy()
    for c in ["category", "product_group"]:
        if c not in work:
            work[c] = "UNKNOWN"
        work[c] = work[c].fillna("UNKNOWN").astype(str)
    work["_prior_group"] = (
        work["platform"].astype(str) + "|" + work["entity_type"].astype(str) + "|" +
        work["category"].astype(str) + "|" + work["product_group"].astype(str)
    )
    grouped = work.groupby("_prior_group", dropna=False).agg(
        group_clicks=("clicks", "sum"), group_orders=("orders_attr", "sum"),
        group_sales=("sales_attr", "sum"),
    )
    grouped["group_cvr"] = np.where(grouped["group_clicks"] > 0, grouped["group_orders"] / grouped["group_clicks"], np.nan)
    grouped["group_aov"] = np.where(grouped["group_orders"] > 0, grouped["group_sales"] / grouped["group_orders"], np.nan)
    marketplace = work.groupby(["platform", "entity_type"], dropna=False).agg(
        clicks=("clicks", "sum"), orders=("orders_attr", "sum"), sales=("sales_attr", "sum")
    )
    marketplace["cvr"] = np.where(marketplace["clicks"] > 0, marketplace["orders"] / marketplace["clicks"], 0.0)
    marketplace["aov"] = np.where(marketplace["orders"] > 0, marketplace["sales"] / marketplace["orders"], 3000.0)

    priors_cvr = []
    priors_aov = []
    for _, row in work.iterrows():
        g = grouped.loc[row["_prior_group"]]
        m = marketplace.loc[(row["platform"], row["entity_type"])]
        cvr = g["group_cvr"] if pd.notna(g["group_cvr"]) else m["cvr"]
        aov = g["group_aov"] if pd.notna(g["group_aov"]) else m["aov"]
        priors_cvr.append(float(cvr) if pd.notna(cvr) else 0.0)
        priors_aov.append(float(aov) if pd.notna(aov) and float(aov) > 0 else 3000.0)
    return pd.Series(priors_cvr, index=df.index), pd.Series(priors_aov, index=df.index)


def _resolve_current_bid(row: pd.Series, policy: Policy) -> tuple[float, str]:
    mode = str(row.get("override_mode", "") or "").upper()
    override_cpc = pd.to_numeric(pd.Series([row.get("override_cpc")]), errors="coerce").iloc[0]
    candidates = []
    if mode in {"LOCK_CPC", "BASELINE"} and pd.notna(override_cpc) and override_cpc > 0:
        candidates.append((float(override_cpc), "MANUAL_OVERRIDE"))
    for col, source in [
        ("registered_cpc_setting", "UPLOADED_SETTING"),
        ("state_applied_cpc", "PRIOR_STATE"),
        ("registered_bid_report_shortwin", "REPORT_REGISTERED_BID"),
        ("registered_bid_report", "REPORT_REGISTERED_BID"),
        ("actual_cpc", "ACTUAL_CPC_FALLBACK"),
    ]:
        v = pd.to_numeric(pd.Series([row.get(col)]), errors="coerce").iloc[0]
        if pd.notna(v) and float(v) > 0:
            candidates.append((float(v), source))
    value, source = candidates[0] if candidates else (policy.min_cpc_default, "POLICY_MIN_FALLBACK")
    low = row.get("item_min_cpc")
    high = row.get("item_max_cpc")
    low = float(low) if pd.notna(low) and float(low) > 0 else policy.min_cpc_default
    high = float(high) if pd.notna(high) and float(high) > 0 else policy.max_cpc_default
    return _clip(value, low, high), source


def _detect_metric_anomaly(row: pd.Series, policy: Policy) -> tuple[bool, list[str]]:
    clicks = float(row.get("clicks", 0) or 0)
    prev_clicks = pd.to_numeric(pd.Series([row.get("prev_clicks")]), errors="coerce").iloc[0]
    if clicks < policy.anomaly_min_clicks or pd.isna(prev_clicks) or float(prev_clicks) < policy.anomaly_min_clicks:
        return False, []
    signals: list[str] = []
    cur_cvr = pd.to_numeric(pd.Series([row.get("cvr_attr")]), errors="coerce").iloc[0]
    prev_cvr = pd.to_numeric(pd.Series([row.get("prev_cvr_attr")]), errors="coerce").iloc[0]
    cur_roas = pd.to_numeric(pd.Series([row.get("roas_attr_pct")]), errors="coerce").iloc[0]
    prev_roas = pd.to_numeric(pd.Series([row.get("prev_roas_attr_pct")]), errors="coerce").iloc[0]
    cur_cpc = pd.to_numeric(pd.Series([row.get("actual_cpc")]), errors="coerce").iloc[0]
    prev_cpc = pd.to_numeric(pd.Series([row.get("prev_actual_cpc")]), errors="coerce").iloc[0]
    if pd.notna(prev_cvr) and prev_cvr > 0 and pd.notna(cur_cvr) and cur_cvr <= prev_cvr * (1.0 - policy.anomaly_cvr_drop_rate):
        signals.append("CVR_DROP")
    if pd.notna(prev_roas) and prev_roas > 0 and pd.notna(cur_roas) and cur_roas <= prev_roas * (1.0 - policy.anomaly_roas_drop_rate):
        signals.append("ROAS_DROP")
    if pd.notna(prev_cpc) and prev_cpc > 0 and pd.notna(cur_cpc) and cur_cpc >= prev_cpc * (1.0 + policy.anomaly_cpc_rise_rate):
        signals.append("CPC_RISE")
    return len(signals) >= policy.anomaly_required_signals, signals


def _attribution_maturity(row: pd.Series, policy: Policy) -> float:
    platform = str(row.get("platform", "rakuten")).lower()
    hours = policy.attribution_hours_rakuten if platform == "rakuten" else policy.attribution_hours_yahoo
    horizon_days = max(hours / 24.0, 1.0)
    start = pd.to_datetime(row.get("period_start_shortwin"), errors="coerce")
    end = pd.to_datetime(row.get("period_end_shortwin"), errors="coerce")
    executed = pd.to_datetime(row.get("executed_at_shortwin"), errors="coerce")
    if pd.isna(executed):
        executed = pd.Timestamp.utcnow().tz_localize(None)
    if pd.isna(start) or pd.isna(end):
        return 0.5
    total_days = max((end - start).days + 1, 1)
    # Average fraction matured across an aggregate window. This is only a confidence marker,
    # not a conversion-uplift correction.
    ages = np.arange(total_days - 1, -1, -1, dtype=float) + max((executed - end).total_seconds() / 86400.0, 0.0)
    return float(np.mean(np.clip(ages / horizon_days, 0.0, 1.0)))


def run_decision_engine(entity_df: pd.DataFrame, policy: Policy, as_of: pd.Timestamp | None = None) -> pd.DataFrame:
    if entity_df.empty:
        return pd.DataFrame()
    now = as_of or pd.Timestamp.utcnow()
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    df = entity_df.copy().reset_index(drop=True)
    for col, default in {
        "platform": policy.platform,
        "entity_type": "ITEM",
        "product_id": "",
        "keyword": "",
        "category": "UNKNOWN",
        "product_group": "UNKNOWN",
        "product_status": "ACTIVE",
        "role": "PROFIT",
        "other_promo_cost_rate": policy.other_promo_cost_rate_default,
        "row_quality_block": False,
    }.items():
        if col not in df:
            df[col] = default
        df[col] = df[col].fillna(default)

    numeric_cols = [
        "clicks", "cost", "sales_attr", "orders_attr", "actual_cpc", "cvr_attr", "roas_attr_pct",
        "aov_attr", "cvr_attr_long", "roas_attr_pct_long", "aov_attr_long", "gross_margin_rate",
        "other_promo_cost_rate", "item_min_cpc", "item_max_cpc", "stock_qty", "state_applied_cpc",
        "registered_cpc_setting", "prev_clicks", "prev_cvr_attr", "prev_roas_attr_pct", "prev_actual_cpc",
    ]
    for col in numeric_cols:
        if col not in df:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")

    prior_cvr, prior_aov = _group_priors(df)
    df["prior_cvr"] = prior_cvr
    df["prior_aov"] = prior_aov

    # Keyword priors inherit the parent product's observed CVR where available.
    item_cvr_map = (
        df[df["entity_type"].eq("ITEM")]
        .set_index(["platform", "product_id"])["cvr_attr"]
        .dropna().to_dict()
    )
    kw_mask = df["entity_type"].eq("KEYWORD")
    if kw_mask.any():
        inherited = [item_cvr_map.get((p, i), np.nan) for p, i in zip(df.loc[kw_mask, "platform"], df.loc[kw_mask, "product_id"])]
        inherited = pd.Series(inherited, index=df.index[kw_mask], dtype="float64").dropna()
        if not inherited.empty:
            df.loc[inherited.index, "prior_cvr"] = inherited

    output_rows: list[dict] = []
    for _, row in df.iterrows():
        result = row.to_dict()
        current_bid, current_source = _resolve_current_bid(row, policy)
        min_cpc = float(row["item_min_cpc"]) if pd.notna(row["item_min_cpc"]) and row["item_min_cpc"] > 0 else policy.min_cpc_default
        max_cpc = float(row["item_max_cpc"]) if pd.notna(row["item_max_cpc"]) and row["item_max_cpc"] > 0 else policy.max_cpc_default
        if max_cpc < min_cpc:
            max_cpc = min_cpc
        current_bid = _clip(current_bid, min_cpc, max_cpc)
        clicks = float(row.get("clicks", 0) or 0)
        orders = float(row.get("orders_attr", 0) or 0)
        sales = float(row.get("sales_attr", 0) or 0)
        cost = float(row.get("cost", 0) or 0)
        prior = float(row.get("prior_cvr", 0) or 0)
        cvr_mean, cvr_lower, cvr_sd = _beta_posterior_stats(orders, clicks, prior, policy.bayes_prior_clicks)
        aov = float(row["aov_attr"]) if pd.notna(row["aov_attr"]) and row["aov_attr"] > 0 else float(row["prior_aov"])
        margin_missing = pd.isna(row["gross_margin_rate"])
        margin = policy.default_margin_rate if margin_missing else float(row["gross_margin_rate"])
        margin = _clip(margin, 0.0, 1.0)
        promo_rate = float(row["other_promo_cost_rate"]) if pd.notna(row["other_promo_cost_rate"]) else policy.other_promo_cost_rate_default
        promo_rate = _clip(promo_rate, 0.0, 1.0)
        contribution_rate_before_ad = max(margin - promo_rate, 0.0)

        expected_sales_per_click = cvr_mean * aov
        lower_sales_per_click = cvr_lower * aov
        expected_contribution_before_ad_per_click = expected_sales_per_click * contribution_rate_before_ad
        lower_contribution_before_ad_per_click = lower_sales_per_click * contribution_rate_before_ad
        profit_guard_cpc = expected_contribution_before_ad_per_click * (1.0 - policy.profit_safety_buffer_rate)
        roas_guard_cpc = expected_sales_per_click / max(policy.target_roas_pct / 100.0, 0.01)
        raw_target = min(profit_guard_cpc, roas_guard_cpc)

        trend_multiplier = 1.0
        if _bool_text(row.get("trend_available", False)) and pd.notna(row.get("cvr_attr_long")) and row.get("cvr_attr_long", 0) > 0:
            ratio = cvr_mean / max(float(row["cvr_attr_long"]), 0.000001)
            trend_multiplier = _clip(math.sqrt(max(ratio, 0.0)), 0.75, 1.15)
            raw_target *= trend_multiplier

        raw_target = _clip(raw_target, min_cpc, max_cpc)
        up_limit = min(current_bid * (1.0 + policy.max_raise_pct), current_bid + policy.max_absolute_change_yen, max_cpc)
        down_limit = max(current_bid * (1.0 - policy.max_down_pct), current_bid - policy.max_absolute_change_yen, min_cpc)
        bounded_target = min(raw_target, up_limit) if raw_target > current_bid else max(raw_target, down_limit)
        bounded_target = _clip(bounded_target, min_cpc, max_cpc)
        if policy.round_mode.upper() == "CEIL":
            recommended = _ceil_yen(bounded_target)
        else:
            recommended = int(round(bounded_target))
        recommended = int(_clip(recommended, min_cpc, max_cpc))

        deadband = max(policy.deadband_yen, current_bid * policy.deadband_pct)
        maturity = _attribution_maturity(row, policy)
        anomaly, anomaly_signals = _detect_metric_anomaly(row, policy)
        override_mode = str(row.get("override_mode", "") or "").upper()
        product_status = str(row.get("product_status", "ACTIVE") or "ACTIVE").upper()
        stock_known = pd.notna(row.get("stock_qty"))
        stock_zero = stock_known and float(row.get("stock_qty")) <= 0
        launch_age = _days_since(row.get("launch_date"), now)
        last_change_age = _days_since(row.get("state_effective_at"), now)

        action = "KEEP"
        status = "KEEP"
        reason_code = "WITHIN_DEADBAND"
        reason = "推奨CPCと登録CPCの差がデッドバンド内です。"
        auto_approved = False
        final_recommended = recommended

        if _bool_text(row.get("row_quality_block", False)):
            action, status = "MANUAL_REVIEW", "BLOCKED_DATA_QUALITY"
            reason_code, reason = "ROW_DATA_ERROR", "行データに欠損または不正値があるため計算対象外です。"
            final_recommended = int(round(current_bid))
        elif override_mode == "EXCLUDE":
            action, status = "EXCLUDE", "EXCLUDED_MANUAL"
            reason_code, reason = "MANUAL_EXCLUDE", "手動除外が有効です。"
            final_recommended = int(round(current_bid))
        elif override_mode == "FORCE_STOP" or product_status in HARD_STOP_STATUSES or stock_zero:
            action, status = "STOP_OR_MIN_CPC", "STOP_AUTO_READY"
            reason_code = "HARD_STOP"
            reason = "商品状態または在庫のハード制約により停止候補です。"
            final_recommended = int(min_cpc)
            auto_approved = policy.auto_ready_stop
        elif override_mode == "LOCK_CPC":
            action, status = "KEEP", "MANUAL_LOCK"
            reason_code, reason = "MANUAL_CPC_LOCK", "手動CPCロック中のため自動提案を行いません。"
            final_recommended = int(round(current_bid))
        elif anomaly:
            action, status = "MANUAL_REVIEW", "ANOMALY_MANUAL_INTERVENTION"
            reason_code = "+".join(anomaly_signals)
            reason = "複数指標の急変を検出したため、アップロード生成を停止し手動確認へ送ります。"
            final_recommended = int(round(current_bid))
        elif last_change_age is not None and last_change_age < policy.change_observation_days:
            action, status = "OBSERVE", "OBSERVE_AFTER_CHANGE"
            reason_code = "MEASUREMENT_LOCK"
            reason = f"直近変更から{last_change_age:.1f}日で、{policy.change_observation_days}日間の効果観測中です。"
            final_recommended = int(round(current_bid))
        elif margin_missing and not policy.allow_assumed_margin_for_simulation:
            action, status = "MANUAL_REVIEW", "HOLD_MISSING_MARGIN"
            reason_code, reason = "MISSING_MARGIN", "商品マスタの粗利率が未登録です。"
            final_recommended = int(round(current_bid))
        elif launch_age is not None and launch_age < policy.new_product_observation_days:
            action, status = "OBSERVE", "OBSERVE_NEW_PRODUCT"
            reason_code = "NEW_PRODUCT"
            reason = f"販売開始から{launch_age:.1f}日で、新商品観測期間中です。自動的な探索増額は行いません。"
            final_recommended = int(round(current_bid))
        elif clicks == 0:
            action, status = "MANUAL_REVIEW", "NO_TRAFFIC_REVIEW"
            reason_code = "ZERO_CLICK_NOT_PERFORMANCE_STOP"
            reason = "クリック0は効果不良ではなく露出・需要・適格性の問題を含むため、停止を自動断定しません。"
            final_recommended = int(round(current_bid))
        elif clicks < policy.min_clicks_for_judgement:
            action, status = "OBSERVE", "HOLD_INSUFFICIENT_DATA"
            reason_code = "INSUFFICIENT_CLICKS"
            reason = f"クリック数{int(clicks)}が通常判定閾値{policy.min_clicks_for_judgement}未満です。"
            final_recommended = int(round(current_bid))
        elif orders <= 0 and clicks >= policy.no_order_stop_clicks:
            action, status = "STOP_OR_MIN_CPC", "STOP_AUTO_READY"
            reason_code = "NO_ORDER_STOP_THRESHOLD"
            reason = f"{int(clicks)}クリックで注文0のため、停止または最低CPC候補です。"
            final_recommended = int(min_cpc)
            auto_approved = policy.auto_ready_stop
        elif raw_target <= min_cpc and expected_contribution_before_ad_per_click <= current_bid:
            action, status = "STOP_OR_MIN_CPC", "STOP_AUTO_READY"
            reason_code = "NON_POSITIVE_UNIT_ECONOMICS"
            reason = "推定広告前限界粗利が現在CPCを賄えないため、停止または最低CPC候補です。"
            final_recommended = int(min_cpc)
            auto_approved = policy.auto_ready_stop
        elif abs(recommended - current_bid) < deadband:
            action, status = "KEEP", "KEEP"
            reason_code = "WITHIN_DEADBAND"
            reason = f"変更差が{deadband:.1f}円未満のため据え置きです。"
            final_recommended = int(round(current_bid))
        elif recommended < current_bid:
            action, status = "SCALE_DOWN", "SCALE_DOWN_AUTO_READY"
            reason_code = "PROFIT_OR_ROAS_GUARD_DOWN"
            reason = "粗利ガードと手動目標ROASの双方を満たす許容CPCが登録CPCを下回ります。"
            auto_approved = policy.auto_ready_scale_down and (not margin_missing or policy.allow_assumed_margin_for_upload)
            if maturity < 0.50:
                auto_approved = False
                status = "SCALE_DOWN_REVIEW_PROVISIONAL_ATTRIBUTION"
                reason += " アトリビューション未成熟のため自動承認しません。"
        else:
            # Increase only when conservative unit economics remain positive.
            if lower_contribution_before_ad_per_click > recommended:
                action, status = "SCALE_UP", "SCALE_UP_REVIEW_REQUIRED"
                reason_code = "LOWER_BOUND_PROFITABLE"
                reason = "CVR下限推定でも広告前限界粗利が推奨CPCを上回るため増額候補です。"
            else:
                action, status = "KEEP", "KEEP_UNCERTAIN_UPSIDE"
                reason_code = "UNCERTAIN_SCALE_UP"
                reason = "平均値では増額余地がありますが、CVR下限推定で採算を確認できないため据え置きます。"
                final_recommended = int(round(current_bid))

        if margin_missing:
            reason += " 粗利率は仮定値で試算しています。"
            if action == "SCALE_UP":
                status = "SCALE_UP_REVIEW_MISSING_MARGIN"
                auto_approved = False
            if not policy.allow_assumed_margin_for_upload:
                auto_approved = False

        unit_profit_current = expected_contribution_before_ad_per_click - current_bid
        unit_profit_recommended = expected_contribution_before_ad_per_click - final_recommended
        elasticity = pd.to_numeric(pd.Series([row.get("elasticity_estimate")]), errors="coerce").iloc[0]
        elasticity = float(elasticity) if pd.notna(elasticity) else 0.50
        expected_delta_clicks = clicks * elasticity * ((final_recommended - current_bid) / max(current_bid, 1.0))
        expected_incremental_profit = expected_delta_clicks * unit_profit_recommended

        result.update({
            "current_bid": float(current_bid),
            "current_bid_source": current_source,
            "min_cpc": float(min_cpc),
            "max_cpc": float(max_cpc),
            "margin_rate_used": float(margin),
            "margin_source": "ASSUMED" if margin_missing else "PRODUCT_MASTER",
            "promo_cost_rate_used": float(promo_rate),
            "smoothed_cvr": float(cvr_mean),
            "cvr_lower_bound": float(cvr_lower),
            "cvr_posterior_sd": float(cvr_sd),
            "expected_aov": float(aov),
            "expected_sales_per_click": float(expected_sales_per_click),
            "expected_contribution_before_ad_per_click": float(expected_contribution_before_ad_per_click),
            "lower_contribution_before_ad_per_click": float(lower_contribution_before_ad_per_click),
            "profit_guard_cpc": float(profit_guard_cpc),
            "roas_guard_cpc": float(roas_guard_cpc),
            "raw_target_cpc": float(raw_target),
            "trend_multiplier": float(trend_multiplier),
            "attribution_maturity": float(maturity),
            "recommended_cpc": int(final_recommended),
            "delta_cpc": float(final_recommended - current_bid),
            "delta_pct": float((final_recommended - current_bid) / current_bid) if current_bid else np.nan,
            "expected_unit_profit_current": float(unit_profit_current),
            "expected_unit_profit_recommended": float(unit_profit_recommended),
            "expected_incremental_profit": float(expected_incremental_profit),
            "action": action,
            "decision_status": status,
            "reason_code": reason_code,
            "reason": reason,
            "anomaly_signals": "|".join(anomaly_signals),
            "auto_approved_default": bool(auto_approved),
            "operator_action": "ACCEPT" if auto_approved else "PENDING",
            "operator_cpc": int(final_recommended),
            "operator_reason": "",
        })
        output_rows.append(result)

    out = pd.DataFrame(output_rows)
    out = apply_product_keyword_hierarchy(out)
    sort_cols = [c for c in ["decision_status", "expected_incremental_profit", "cost"] if c in out]
    if sort_cols:
        ascending = [True] + [False] * (len(sort_cols) - 1)
        out = out.sort_values(sort_cols, ascending=ascending)
    return out.reset_index(drop=True)


def apply_product_keyword_hierarchy(decisions: pd.DataFrame) -> pd.DataFrame:
    if decisions.empty or not decisions["entity_type"].eq("KEYWORD").any():
        return decisions
    out = decisions.copy()
    item = out[out["entity_type"].eq("ITEM")].set_index(["platform", "product_id"])
    for idx, row in out[out["entity_type"].eq("KEYWORD")].iterrows():
        key = (row["platform"], row["product_id"])
        if key not in item.index:
            continue
        parent = item.loc[key]
        if isinstance(parent, pd.DataFrame):
            parent = parent.iloc[0]
        parent_action = str(parent.get("action", "KEEP"))
        parent_rec = float(parent.get("recommended_cpc", row["recommended_cpc"]))
        current = float(row.get("current_bid", row["recommended_cpc"]))
        if parent_action == "STOP_OR_MIN_CPC":
            parent_auto = bool(parent.get("auto_approved_default", False))
            out.at[idx, "recommended_cpc"] = int(row.get("min_cpc", parent_rec))
            out.at[idx, "action"] = "STOP_OR_MIN_CPC"
            out.at[idx, "decision_status"] = "PARENT_STOP_AUTO_READY" if parent_auto else "PARENT_STOP_REVIEW"
            out.at[idx, "reason_code"] = "PARENT_PRODUCT_STOP"
            out.at[idx, "reason"] = "商品単位判定が停止のため、キーワードも商品判定に従います。"
            out.at[idx, "auto_approved_default"] = parent_auto
            out.at[idx, "operator_action"] = "ACCEPT" if parent_auto else "PENDING"
        elif parent_action == "SCALE_DOWN" and row.get("action") == "SCALE_UP":
            cap = min(current, parent_rec)
            out.at[idx, "recommended_cpc"] = int(round(cap))
            out.at[idx, "action"] = "KEEP" if cap == current else "SCALE_DOWN"
            out.at[idx, "decision_status"] = "PARENT_PRODUCT_CAP"
            out.at[idx, "reason_code"] = "PARENT_PRODUCT_PRIORITY"
            out.at[idx, "reason"] = "商品単位CPCを優先し、キーワード増額を抑制しました。"
            out.at[idx, "auto_approved_default"] = cap < current
            out.at[idx, "operator_action"] = "ACCEPT" if cap < current else "PENDING"
        out.at[idx, "delta_cpc"] = float(out.at[idx, "recommended_cpc"] - current)
        out.at[idx, "delta_pct"] = float(out.at[idx, "delta_cpc"] / current) if current else np.nan
        out.at[idx, "operator_cpc"] = int(out.at[idx, "recommended_cpc"])
    return out
