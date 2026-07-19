from __future__ import annotations

import pandas as pd

from src.io_utils import read_csv_flexible
from src.pipeline import build_pipeline, finalize_operator_decisions, update_state_bundle
from src.policy import Policy
from src.state_bundle import StateBundle


def performance_loaded():
    raw = (
        "platform,entity_type,product_id,keyword,period_start,period_end,registered_bid,impressions,clicks,cost,actual_cpc,sales_short,orders_short,sales_attr,orders_attr,ctr_pct\n"
        "rakuten,ITEM,P1,,2026-07-01,2026-07-07,30,1000,100,3000,30,10000,3,20000,5,10\n"
    ).encode("utf-8")
    return read_csv_flexible(raw)


def setting(cpc: int):
    return pd.DataFrame([{
        "コントロールカラム": "", "商品管理番号": "P1", "商品名": "x", "価格": "1000",
        "商品URL": "https://example.com", "商品CPC": str(cpc), "キーワード": "",
        "キーワードCPC": "", "目安CPC": "",
    }])


def master():
    return pd.DataFrame([{
        "platform": "rakuten", "common_product_id": "P1", "product_id": "P1",
        "category": "C", "product_group": "G", "gross_margin_rate": 0.4,
        "other_promo_cost_rate": 0, "item_min_cpc": 24, "item_max_cpc": 100,
        "stock_qty": 100, "product_status": "ACTIVE", "launch_date": "2020-01-01", "role": "PROFIT",
    }])


def confirmed_baseline_state() -> StateBundle:
    state = StateBundle.empty()
    state.applied_settings = pd.DataFrame([{
        "platform": "rakuten", "entity_type": "ITEM", "product_id": "P1", "keyword": "",
        "applied_cpc": 30, "applied_status": "ACTIVE", "effective_at": "",
        "source": "INITIAL_BASELINE", "run_id": "base",
    }])
    return state


def test_pending_plan_is_confirmed_only_when_setting_matches():
    state = confirmed_baseline_state()
    state.planned_settings = pd.DataFrame([{
        "platform": "rakuten", "entity_type": "ITEM", "product_id": "P1", "keyword": "",
        "planned_cpc": 40, "planned_status": "PENDING", "planned_at": "2026-07-05T00:00:00+00:00",
        "source": "ACCEPT", "run_id": "plan1",
    }])
    ctx = build_pipeline([performance_loaded()], setting(40), master(), state, Policy(), as_of=pd.Timestamp("2026-07-08T00:00:00Z"))
    assert "PLAN_CONFIRMED" in set(ctx.setting_events["event_type"])
    row = ctx.decisions.iloc[0]
    assert row["current_bid"] == 40
    assert row["decision_status"] == "OBSERVE_AFTER_CHANGE"


def test_pending_plan_not_applied_does_not_move_baseline():
    state = confirmed_baseline_state()
    state.planned_settings = pd.DataFrame([{
        "platform": "rakuten", "entity_type": "ITEM", "product_id": "P1", "keyword": "",
        "planned_cpc": 40, "planned_status": "PENDING", "planned_at": "2026-07-01T00:00:00+00:00",
        "source": "ACCEPT", "run_id": "plan1",
    }])
    ctx = build_pipeline([performance_loaded()], setting(30), master(), state, Policy(), as_of=pd.Timestamp("2026-07-08T00:00:00Z"))
    assert "PLAN_NOT_APPLIED" in set(ctx.setting_events["event_type"])
    assert ctx.decisions.iloc[0]["current_bid"] == 30


def test_direct_media_edit_becomes_new_baseline():
    state = confirmed_baseline_state()
    ctx = build_pipeline([performance_loaded()], setting(35), master(), state, Policy(), as_of=pd.Timestamp("2026-07-08T00:00:00Z"))
    assert "EXTERNAL_MANUAL_CHANGE" in set(ctx.setting_events["event_type"])
    assert ctx.decisions.iloc[0]["current_bid"] == 35
    assert ctx.decisions.iloc[0]["decision_status"] == "OBSERVE_AFTER_CHANGE"


def test_approved_output_is_planned_not_immediately_applied():
    state = StateBundle.empty()
    ctx = build_pipeline([performance_loaded()], setting(30), master(), state, Policy(), as_of=pd.Timestamp("2026-07-08T00:00:00Z"))
    decisions = ctx.decisions.copy()
    decisions.loc[:, "operator_action"] = "MODIFY"
    decisions.loc[:, "operator_cpc"] = 25
    finalized = finalize_operator_decisions(decisions)
    updated = update_state_bundle(state, ctx, finalized, Policy(), as_of=pd.Timestamp("2026-07-08T00:00:00Z"))
    assert (updated.planned_settings["planned_status"] == "PENDING").any()
    # Only the initial setting baseline is confirmed; the proposed 25 yen is not.
    confirmed = pd.to_numeric(updated.applied_settings["applied_cpc"], errors="coerce")
    assert 25 not in set(confirmed.dropna())
