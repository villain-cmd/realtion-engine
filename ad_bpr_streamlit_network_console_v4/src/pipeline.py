from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd

from .decision_engine import run_decision_engine
from .metrics import (
    QualityResult,
    build_setting_maps,
    normalize_product_master,
    normalize_reports,
    select_analysis_windows,
    validate_performance,
)
from .policy import Policy
from .state_bundle import STATE_SCHEMA_VERSION, StateBundle, make_run_id, merge_active_overrides


KEYS = ["platform", "entity_type", "product_id", "keyword"]


@dataclass
class PipelineContext:
    performance: pd.DataFrame
    windows: pd.DataFrame
    decision_input: pd.DataFrame
    decisions: pd.DataFrame
    quality: QualityResult
    input_hash: str
    run_id: str
    duplicate_input: bool
    setting_events: pd.DataFrame

    @property
    def external_manual_changes(self) -> pd.DataFrame:
        if self.setting_events.empty:
            return self.setting_events
        return self.setting_events[self.setting_events["event_type"].eq("EXTERNAL_MANUAL_CHANGE")]

    @property
    def confirmed_plans(self) -> pd.DataFrame:
        if self.setting_events.empty:
            return self.setting_events
        return self.setting_events[self.setting_events["event_type"].eq("PLAN_CONFIRMED")]


def _combined_input_hash(performance: pd.DataFrame) -> str:
    if performance.empty:
        return hashlib.sha256(b"").hexdigest()
    hashes = sorted(set(performance["source_hash"].astype(str)))
    return hashlib.sha256("|".join(hashes).encode("utf-8")).hexdigest()


def _latest_state_settings(state: StateBundle) -> pd.DataFrame:
    cols = KEYS + ["state_applied_cpc", "state_effective_at", "state_source", "state_run_id"]
    if state.applied_settings.empty:
        return pd.DataFrame(columns=cols)
    df = state.applied_settings.copy()
    df["effective_at_dt"] = pd.to_datetime(df.get("effective_at"), errors="coerce", utc=True)
    # Empty effective_at means an initial baseline. Keep insertion order as the tie breaker.
    df["_order"] = np.arange(len(df))
    df = df.sort_values(["effective_at_dt", "_order"], na_position="first").drop_duplicates(KEYS, keep="last")
    df["state_applied_cpc"] = pd.to_numeric(df.get("applied_cpc"), errors="coerce")
    df["state_effective_at"] = df.get("effective_at", "")
    df["state_source"] = df.get("source", "PRIOR_STATE")
    df["state_run_id"] = df.get("run_id", "")
    return df[cols]


def _latest_pending_plans(state: StateBundle) -> pd.DataFrame:
    cols = KEYS + ["planned_cpc", "planned_at", "planned_source", "planned_run_id"]
    if state.planned_settings.empty:
        return pd.DataFrame(columns=cols)
    df = state.planned_settings.copy()
    status = df.get("planned_status", "PENDING").astype(str).str.upper()
    df = df[status.eq("PENDING")]
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["planned_at_dt"] = pd.to_datetime(df.get("planned_at"), errors="coerce", utc=True)
    df["_order"] = np.arange(len(df))
    df = df.sort_values(["planned_at_dt", "_order"], na_position="first").drop_duplicates(KEYS, keep="last")
    df["planned_cpc"] = pd.to_numeric(df.get("planned_cpc"), errors="coerce")
    df["planned_source"] = df.get("source", "")
    df["planned_run_id"] = df.get("run_id", "")
    return df[cols]


def _latest_metric_history(state: StateBundle) -> pd.DataFrame:
    cols = KEYS + ["prev_clicks", "prev_cvr_attr", "prev_roas_attr_pct", "prev_actual_cpc", "prev_period_end"]
    if state.metric_history.empty:
        return pd.DataFrame(columns=cols)
    hist = state.metric_history.copy()
    hist["period_end_dt"] = pd.to_datetime(hist.get("period_end"), errors="coerce")
    hist["captured_at_dt"] = pd.to_datetime(hist.get("captured_at"), errors="coerce", utc=True)
    hist = hist.sort_values(["period_end_dt", "captured_at_dt"]).drop_duplicates(KEYS, keep="last")
    hist = hist.rename(columns={
        "clicks": "prev_clicks",
        "cvr_attr": "prev_cvr_attr",
        "roas_attr_pct": "prev_roas_attr_pct",
        "actual_cpc": "prev_actual_cpc",
        "period_end": "prev_period_end",
    })
    for c in cols:
        if c not in hist:
            hist[c] = np.nan
    return hist[cols]


def _setting_table(item_setting: pd.DataFrame, keyword_setting: pd.DataFrame) -> pd.DataFrame:
    frames = [x for x in (item_setting, keyword_setting) if not x.empty]
    cols = KEYS + ["registered_cpc_setting", "setting_source", "setting_row_exists"]
    if not frames:
        return pd.DataFrame(columns=cols)
    out = pd.concat(frames, ignore_index=True).rename(columns={"registered_cpc": "registered_cpc_setting"})
    for c in cols:
        if c not in out:
            out[c] = np.nan
    return out[cols]


def _reconcile_setting_state(enriched: pd.DataFrame, now: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reconcile current media settings with confirmed baselines and pending plans.

    A downloaded plan is not treated as applied. It becomes confirmed only when a later
    setting CSV contains the planned CPC. Direct edits in the media UI are detected as
    EXTERNAL_MANUAL_CHANGE.
    """
    out = enriched.copy()
    events: list[dict] = []
    out["setting_reconciliation_event"] = ""

    for idx, row in out.iterrows():
        setting = pd.to_numeric(pd.Series([row.get("registered_cpc_setting")]), errors="coerce").iloc[0]
        prior = pd.to_numeric(pd.Series([row.get("state_applied_cpc")]), errors="coerce").iloc[0]
        planned = pd.to_numeric(pd.Series([row.get("planned_cpc")]), errors="coerce").iloc[0]
        planned_at = pd.to_datetime(row.get("planned_at"), errors="coerce", utc=True)
        planned_run_id = str(row.get("planned_run_id", "") or "")
        event_type = ""
        effective_at = row.get("state_effective_at", "")
        source = row.get("state_source", "")

        if pd.notna(setting):
            if pd.notna(planned) and abs(float(setting) - float(planned)) < 1.0:
                event_type = "PLAN_CONFIRMED"
                effective_at = planned_at.isoformat() if pd.notna(planned_at) else now.isoformat()
                source = "PLAN_CONFIRMED"
                out.at[idx, "state_applied_cpc"] = float(setting)
                out.at[idx, "state_effective_at"] = effective_at
                out.at[idx, "state_source"] = source
            elif pd.isna(prior):
                event_type = "INITIAL_BASELINE"
                effective_at = ""
                source = "INITIAL_SETTING"
                out.at[idx, "state_applied_cpc"] = float(setting)
                out.at[idx, "state_effective_at"] = ""
                out.at[idx, "state_source"] = source
            elif abs(float(setting) - float(prior)) >= 1.0:
                event_type = "EXTERNAL_MANUAL_CHANGE"
                effective_at = now.isoformat()
                source = "EXTERNAL_MANUAL_CHANGE"
                out.at[idx, "state_applied_cpc"] = float(setting)
                out.at[idx, "state_effective_at"] = effective_at
                out.at[idx, "state_source"] = source
            elif pd.notna(planned) and abs(float(setting) - float(planned)) >= 1.0:
                # A pending plan that is still absent from a later setting export is not applied.
                age_days = (now - planned_at).total_seconds() / 86400.0 if pd.notna(planned_at) else 999.0
                if age_days >= 1.0:
                    event_type = "PLAN_NOT_APPLIED"

        if event_type:
            out.at[idx, "setting_reconciliation_event"] = event_type
            events.append({
                **{k: row.get(k, "") for k in KEYS},
                "event_type": event_type,
                "setting_cpc": float(setting) if pd.notna(setting) else np.nan,
                "prior_cpc": float(prior) if pd.notna(prior) else np.nan,
                "planned_cpc": float(planned) if pd.notna(planned) else np.nan,
                "planned_at": row.get("planned_at", ""),
                "planned_run_id": planned_run_id,
                "effective_at": effective_at,
                "detected_at": now.isoformat(),
            })
    return out, pd.DataFrame(events)


def build_pipeline(
    loaded_reports,
    setting_df: pd.DataFrame | None,
    product_master_df: pd.DataFrame | None,
    state: StateBundle,
    policy: Policy,
    as_of: pd.Timestamp | None = None,
) -> PipelineContext:
    now = as_of or pd.Timestamp.utcnow()
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    performance = normalize_reports(loaded_reports, platform_hint=policy.platform)
    quality = validate_performance(performance, policy, state.previous_row_counts())
    input_hash = _combined_input_hash(performance)
    duplicate_input = input_hash in state.known_input_hashes()
    run_id = make_run_id(input_hash, policy)

    windows = select_analysis_windows(performance, policy)
    if windows.empty:
        return PipelineContext(performance, windows, windows, windows, quality, input_hash, run_id, duplicate_input, pd.DataFrame())

    item_setting, keyword_setting = build_setting_maps(setting_df)
    settings = _setting_table(item_setting, keyword_setting)
    enriched = windows.merge(settings, on=KEYS, how="left")
    enriched = enriched.merge(_latest_state_settings(state), on=KEYS, how="left")
    enriched = enriched.merge(_latest_pending_plans(state), on=KEYS, how="left")
    enriched, setting_events = _reconcile_setting_state(enriched, now)

    product_master = normalize_product_master(product_master_df, enriched["product_id"].unique(), platform=policy.platform)
    enriched = enriched.merge(product_master, on=["platform", "product_id"], how="left")
    enriched = enriched.merge(_latest_metric_history(state), on=KEYS, how="left")
    enriched = merge_active_overrides(enriched, state.manual_overrides, as_of=now)

    bad_keys = set(quality.row_errors["entity_key"].astype(str)) if not quality.row_errors.empty else set()
    enriched["row_quality_block"] = enriched["entity_key"].astype(str).isin(bad_keys)
    if quality.status == "BLOCK":
        enriched["row_quality_block"] = True

    decisions = run_decision_engine(enriched, policy, as_of=now)
    decisions["run_id"] = run_id
    decisions["decision_id"] = decisions["run_id"].astype(str) + "|" + decisions["entity_key"].astype(str)
    decisions["duplicate_input"] = duplicate_input
    if duplicate_input:
        mask = decisions["operator_action"].eq("ACCEPT")
        decisions.loc[mask, "operator_action"] = "PENDING"
        decisions.loc[mask, "auto_approved_default"] = False
        decisions.loc[mask, "decision_status"] = decisions.loc[mask, "decision_status"].astype(str) + "_DUPLICATE_INPUT"
        decisions.loc[mask, "reason"] = decisions.loc[mask, "reason"].astype(str) + " 同一入力の再実行のため重複入稿を自動承認しません。"

    return PipelineContext(performance, windows, enriched, decisions, quality, input_hash, run_id, duplicate_input, setting_events)


def finalize_operator_decisions(decisions: pd.DataFrame) -> pd.DataFrame:
    out = decisions.copy()
    valid_actions = {"ACCEPT", "MODIFY", "LOCK", "FORCE_STOP"}
    out["operator_action"] = out["operator_action"].fillna("PENDING").astype(str).str.upper()
    out["operator_cpc"] = pd.to_numeric(out["operator_cpc"], errors="coerce")
    out["final_approved"] = out["operator_action"].isin(valid_actions)
    out["final_cpc"] = np.where(out["final_approved"], out["operator_cpc"], out["current_bid"])
    force_stop = out["operator_action"].eq("FORCE_STOP")
    out.loc[force_stop, "final_cpc"] = out.loc[force_stop, "min_cpc"]
    out["final_cpc"] = np.ceil(out["final_cpc"].clip(lower=out["min_cpc"], upper=out["max_cpc"])).astype(int)
    out["final_changed"] = out["final_approved"] & (out["final_cpc"] != np.ceil(out["current_bid"]).astype(int))
    row_exists = out.get("setting_row_exists", pd.Series(False, index=out.index)).astype(str).str.lower().isin(["true", "1", "yes", "y", "on"])
    out["upload_match"] = np.where(out["platform"].astype(str).str.lower().eq("rakuten"), row_exists, True)
    out["upload_eligible"] = out["final_changed"] & out["upload_match"]
    return out


def _append_table(existing: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    if rows is None or rows.empty:
        return existing
    if existing is None or existing.empty:
        return rows.copy().reset_index(drop=True)
    return pd.concat([existing, rows], ignore_index=True)


def _apply_setting_events_to_state(state: StateBundle, events: pd.DataFrame, run_id: str) -> None:
    if events.empty:
        return
    applied_events = events[events["event_type"].isin(["INITIAL_BASELINE", "PLAN_CONFIRMED", "EXTERNAL_MANUAL_CHANGE"])].copy()
    if not applied_events.empty:
        applied_rows = pd.DataFrame({
            "platform": applied_events["platform"],
            "entity_type": applied_events["entity_type"],
            "product_id": applied_events["product_id"],
            "keyword": applied_events["keyword"],
            "applied_cpc": applied_events["setting_cpc"],
            "applied_status": "ACTIVE",
            "effective_at": applied_events["effective_at"],
            "source": applied_events["event_type"],
            "run_id": run_id,
        })
        state.applied_settings = _append_table(state.applied_settings, applied_rows)

    if not state.planned_settings.empty:
        for _, ev in events.iterrows():
            planned_run_id = str(ev.get("planned_run_id", "") or "")
            if not planned_run_id:
                continue
            mask = state.planned_settings["run_id"].astype(str).eq(planned_run_id)
            for k in KEYS:
                mask &= state.planned_settings[k].astype(str).eq(str(ev.get(k, "")))
            if ev["event_type"] == "PLAN_CONFIRMED":
                state.planned_settings.loc[mask, "planned_status"] = "CONFIRMED"
            elif ev["event_type"] in {"PLAN_NOT_APPLIED", "EXTERNAL_MANUAL_CHANGE"}:
                state.planned_settings.loc[mask, "planned_status"] = "NOT_APPLIED" if ev["event_type"] == "PLAN_NOT_APPLIED" else "SUPERSEDED"

    confirmed = events[events["event_type"].eq("PLAN_CONFIRMED")]
    if not confirmed.empty and not state.action_history.empty:
        for _, ev in confirmed.iterrows():
            mask = state.action_history["run_id"].astype(str).eq(str(ev.get("planned_run_id", "")))
            for k in KEYS:
                mask &= state.action_history[k].astype(str).eq(str(ev.get(k, "")))
            state.action_history.loc[mask, "applied_at"] = ev.get("effective_at", "")
            state.action_history.loc[mask, "outcome_status"] = "PENDING"


def update_state_bundle(
    state: StateBundle,
    context: PipelineContext,
    finalized: pd.DataFrame,
    policy: Policy,
    as_of: pd.Timestamp | None = None,
) -> StateBundle:
    now = as_of or pd.Timestamp.utcnow()
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    state.policy = policy
    run_id = context.run_id

    _apply_setting_events_to_state(state, context.setting_events, run_id)

    metric_rows = finalized[[
        "platform", "entity_type", "product_id", "keyword", "period_start_shortwin", "period_end_shortwin",
        "clicks", "cost", "sales_attr", "orders_attr", "actual_cpc", "cvr_attr", "roas_attr_pct", "current_bid",
    ]].copy().rename(columns={
        "period_start_shortwin": "period_start", "period_end_shortwin": "period_end", "current_bid": "registered_cpc"
    })
    metric_rows.insert(0, "run_id", run_id)
    metric_rows["captured_at"] = now.isoformat()
    state.metric_history = _append_table(state.metric_history, metric_rows)

    action_rows = finalized[[
        "platform", "entity_type", "product_id", "keyword", "period_start_shortwin", "period_end_shortwin",
        "current_bid", "recommended_cpc", "operator_cpc", "final_cpc", "action", "decision_status",
        "operator_action", "operator_reason",
    ]].copy().rename(columns={
        "period_start_shortwin": "period_start", "period_end_shortwin": "period_end", "current_bid": "previous_cpc"
    })
    action_rows.insert(0, "run_id", run_id)
    action_rows["applied_at"] = ""
    action_rows["outcome_due_date"] = (now.to_pydatetime() + timedelta(days=int(policy.change_observation_days))).date().isoformat()
    action_rows["outcome_status"] = np.where(finalized["upload_eligible"].values, "PLANNED", "NOT_APPLIED")
    state.action_history = _append_table(state.action_history, action_rows)

    planned = finalized[finalized["upload_eligible"]].copy()
    if not planned.empty:
        planned_rows = pd.DataFrame({
            "platform": planned["platform"],
            "entity_type": planned["entity_type"],
            "product_id": planned["product_id"],
            "keyword": planned["keyword"],
            "planned_cpc": planned["final_cpc"],
            "planned_status": "PENDING",
            "planned_at": now.isoformat(),
            "source": planned["operator_action"],
            "run_id": run_id,
        })
        state.planned_settings = _append_table(state.planned_settings, planned_rows)

    manual = finalized[finalized["operator_action"].isin(["LOCK", "FORCE_STOP"])].copy()
    if not manual.empty:
        ov = pd.DataFrame({
            "override_id": [f"{run_id}_{i}" for i in range(len(manual))],
            "platform": manual["platform"].values,
            "entity_type": manual["entity_type"].values,
            "product_id": manual["product_id"].values,
            "keyword": manual["keyword"].values,
            "override_mode": np.where(manual["operator_action"].eq("LOCK"), "LOCK_CPC", "FORCE_STOP"),
            "override_cpc": manual["final_cpc"].values,
            "effective_from": now.isoformat(),
            "effective_until": "",
            "active": True,
            "reason": manual["operator_reason"].values,
            "created_run_id": run_id,
        })
        state.manual_overrides = _append_table(state.manual_overrides, ov)

    run_row = pd.DataFrame([{
        "run_id": run_id,
        "created_at": now.isoformat(),
        "input_hash": context.input_hash,
        "policy_hash": hashlib.sha256(json.dumps(policy.to_dict(), sort_keys=True).encode()).hexdigest(),
        "quality_status": context.quality.status,
        "quality_score": context.quality.score,
        "item_rows": int((finalized["entity_type"] == "ITEM").sum()),
        "keyword_rows": int((finalized["entity_type"] == "KEYWORD").sum()),
        "duplicate_input": context.duplicate_input,
    }])
    state.run_history = _append_table(state.run_history, run_row)
    state.manifest.update({
        "schema_version": STATE_SCHEMA_VERSION,
        "last_run_id": run_id,
        "last_updated_at": now.isoformat(),
    })
    return state
