from __future__ import annotations

import pandas as pd

from src.decision_engine import run_decision_engine
from src.policy import Policy


def base_row(**overrides):
    row = {
        "platform": "rakuten",
        "entity_type": "ITEM",
        "product_id": "P1",
        "keyword": "",
        "entity_key": "rakuten|ITEM|P1|",
        "category": "C",
        "product_group": "G",
        "product_status": "ACTIVE",
        "role": "PROFIT",
        "clicks": 100,
        "cost": 3000,
        "sales_attr": 20000,
        "orders_attr": 5,
        "actual_cpc": 30,
        "cvr_attr": 0.05,
        "roas_attr_pct": 666.7,
        "aov_attr": 4000,
        "clicks_long": 100,
        "cvr_attr_long": 0.05,
        "roas_attr_pct_long": 666.7,
        "aov_attr_long": 4000,
        "trend_available": False,
        "gross_margin_rate": 0.4,
        "other_promo_cost_rate": 0.0,
        "item_min_cpc": 24,
        "item_max_cpc": 100,
        "stock_qty": 100,
        "launch_date": "2020-01-01",
        "registered_cpc_setting": 40,
        "registered_bid_report_shortwin": 35,
        "period_start_shortwin": "2026-06-01",
        "period_end_shortwin": "2026-06-07",
        "executed_at_shortwin": "2026-07-15 00:00:00",
        "row_quality_block": False,
    }
    row.update(overrides)
    return row


def test_registered_setting_is_control_baseline():
    out = run_decision_engine(pd.DataFrame([base_row(actual_cpc=12, registered_cpc_setting=40)]), Policy())
    assert out.iloc[0]["current_bid"] == 40
    assert out.iloc[0]["current_bid_source"] == "UPLOADED_SETTING"


def test_zero_click_is_not_automatic_stop():
    out = run_decision_engine(pd.DataFrame([base_row(clicks=0, cost=0, sales_attr=0, orders_attr=0, cvr_attr=0)]), Policy())
    assert out.iloc[0]["decision_status"] == "NO_TRAFFIC_REVIEW"
    assert out.iloc[0]["action"] == "MANUAL_REVIEW"


def test_no_order_threshold_stops():
    out = run_decision_engine(pd.DataFrame([base_row(clicks=50, cost=2000, sales_attr=0, orders_attr=0, cvr_attr=0)]), Policy())
    assert out.iloc[0]["action"] == "STOP_OR_MIN_CPC"
    assert out.iloc[0]["recommended_cpc"] == 24


def test_manual_lock_wins():
    out = run_decision_engine(pd.DataFrame([base_row(override_mode="LOCK_CPC", override_cpc=55)]), Policy())
    assert out.iloc[0]["current_bid"] == 55
    assert out.iloc[0]["decision_status"] == "MANUAL_LOCK"


def test_two_signal_anomaly_requires_manual_intervention():
    row = base_row(
        cvr_attr=0.01,
        roas_attr_pct=100,
        actual_cpc=50,
        prev_clicks=100,
        prev_cvr_attr=0.05,
        prev_roas_attr_pct=600,
        prev_actual_cpc=30,
    )
    out = run_decision_engine(pd.DataFrame([row]), Policy())
    assert out.iloc[0]["decision_status"] == "ANOMALY_MANUAL_INTERVENTION"
    assert "CVR_DROP" in out.iloc[0]["anomaly_signals"]


def test_missing_margin_never_auto_approves_upload_by_default():
    out = run_decision_engine(pd.DataFrame([base_row(gross_margin_rate=float("nan"), sales_attr=0, orders_attr=0, clicks=50)]), Policy())
    assert not bool(out.iloc[0]["auto_approved_default"])
