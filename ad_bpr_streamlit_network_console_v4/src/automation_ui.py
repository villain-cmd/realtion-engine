from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Mapping

import pandas as pd
import streamlit as st

from .connectors import ConnectorConfigurationError, ConnectorDependencyError, ConnectorResult
from .database import SupabaseStore, add_lineage
from .ingestion import SOURCE_LABELS, fetch_source, persist_result
from .io_utils import LoadedCsv, dataframe_to_csv_bytes, read_csv_flexible


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def secret_section(name: str) -> dict[str, Any]:
    try:
        return _plain(st.secrets[name])
    except (KeyError, FileNotFoundError):
        return {}


def service_account_info() -> dict[str, Any]:
    return secret_section("gcp_service_account") or secret_section("google_service_account")


def database_store() -> SupabaseStore:
    return SupabaseStore.from_config(secret_section("supabase"))


def _loaded_from_frame(frame: pd.DataFrame, name: str) -> LoadedCsv:
    loaded = read_csv_flexible(dataframe_to_csv_bytes(frame))
    loaded.name = name
    return loaded


def _result_from_state(payload: Mapping[str, Any]) -> ConnectorResult:
    return ConnectorResult(
        source=str(payload["source"]),
        dataset=str(payload["dataset"]),
        dataframe=payload["dataframe"],
        fetched_at=str(payload["fetched_at"]),
        metadata=dict(payload.get("metadata", {})),
    )


def _store_result_state(result: ConnectorResult) -> None:
    st.session_state["latest_connector_result"] = {
        "source": result.source,
        "dataset": result.dataset,
        "dataframe": result.dataframe,
        "fetched_at": result.fetched_at,
        "metadata": result.metadata,
    }


def _render_db_runner() -> None:
    st.caption("Supabase PostgreSQLからデータを読み込みます。CSVは初期移行やAPI非対応項目だけの補助入力です。")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        performance_table = st.text_input("実績テーブル", "performance_input", key="db_performance_table")
    with c2:
        settings_table = st.text_input("入札設定テーブル", "bid_settings", key="db_settings_table")
    with c3:
        master_table = st.text_input("商品マスタテーブル", "product_master", key="db_master_table")
    with c4:
        metrics_table = st.text_input("統合日次指標", "business_metrics_daily", key="db_metrics_table")

    if st.button("DBから読み込んで分析", type="primary", key="load_database", width="stretch"):
        try:
            store = database_store()
            performance = store.read_frame(performance_table)
            setting = store.read_frame(settings_table)
            master = store.read_frame(master_table)
            metrics = store.read_frame(metrics_table)
            st.session_state["db_performance_frame"] = performance
            st.session_state["db_setting_frame"] = setting
            st.session_state["db_master_frame"] = master
            st.session_state["db_metrics_frame"] = metrics
            st.success(
                f"DB読込: 分析実績 {len(performance):,}行 / 統合日次指標 {len(metrics):,}行"
            )
        except Exception as exc:
            st.error(f"DB読込に失敗しました: {exc}")

    metrics = st.session_state.get("db_metrics_frame")
    if isinstance(metrics, pd.DataFrame) and not metrics.empty:
        st.markdown("##### API統合データ")
        st.dataframe(metrics.tail(100), hide_index=True, width="stretch")
    performance = st.session_state.get("db_performance_frame")
    if isinstance(performance, pd.DataFrame) and not performance.empty:
        st.markdown("##### 入札分析用データ")
        st.dataframe(performance.head(20), hide_index=True, width="stretch")


def _source_requirements(source: str, config: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    required = {
        "ga4": ["property_id"],
        "google_ads": ["customer_id", "developer_token"],
        "shopify": ["shop_domain", "access_token"],
        "yahoo_shopping": ["seller_id"],
        "airregi": ["base_url", "transactions_path", "api_key", "api_token"],
    }[source]
    missing = [key for key in required if not str(config.get(key, "")).strip()]
    if source == "ga4" and not service_account_info():
        missing.append("gcp_service_account")
    if source == "google_ads":
        oauth = all(str(config.get(key, "")).strip() for key in ("client_id", "client_secret", "refresh_token"))
        adc = bool(config.get("use_application_default_credentials"))
        service_account = bool(config.get("json_key_file_path"))
        if not (oauth or adc or service_account):
            missing.append("Google広告OAuth/ADC認証")
    if source == "yahoo_shopping":
        direct = bool(str(config.get("access_token", "")).strip())
        refresh = all(
            str(config.get(key, "")).strip()
            for key in ("client_id", "client_secret", "refresh_token")
        )
        if not (direct or refresh):
            missing.append("access_token または OAuth更新用3項目")
    return required, missing


def _render_api_ingestion() -> None:
    source = st.selectbox(
        "接続先",
        ["ga4", "google_ads", "shopify", "yahoo_shopping", "airregi"],
        format_func=SOURCE_LABELS.get,
        key="automation_api_source",
    )
    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input("開始日", value=date.today() - timedelta(days=28), key="automation_start_date")
    with c2:
        end_date = st.date_input("終了日", value=date.today() - timedelta(days=1), key="automation_end_date")
    config = secret_section(source)
    _, missing = _source_requirements(source, config)
    if missing:
        st.warning("Secrets未設定: " + ", ".join(missing))
    else:
        st.success("接続設定が揃っています。取得プレビューを実行できます。")
    if source == "airregi":
        st.caption("Airレジは店舗ごとのAPIキー／トークンに加え、連携システム向けに案内されたBase URLと取引エンドポイントを設定します。")
    if source == "yahoo_shopping":
        st.caption("注文APIは事前申請が必要です。固定IPを登録している場合、同じ送信元から実行してください。")

    if st.button("APIから取得プレビュー", type="primary", disabled=bool(missing), key="fetch_api_preview", width="stretch"):
        try:
            with st.spinner(f"{SOURCE_LABELS[source]}から取得中..."):
                result = fetch_source(
                    source=source,
                    config=config,
                    start_date=start_date.isoformat(),
                    end_date=end_date.isoformat(),
                    service_account_info=service_account_info(),
                )
            _store_result_state(result)
            st.success(f"{len(result.dataframe):,}行を取得しました。保存前に内容を確認してください。")
        except (ConnectorConfigurationError, ConnectorDependencyError, Exception) as exc:
            st.error(f"API取得に失敗しました: {exc}")

    payload = st.session_state.get("latest_connector_result")
    if not payload or payload.get("source") != source:
        return
    result = _result_from_state(payload)
    st.dataframe(result.dataframe.head(50), hide_index=True, width="stretch")
    destination = st.text_input("保存先テーブル", value=result.dataset, key=f"api_destination_{source}")
    if st.button("PostgreSQLへ確定保存", key="persist_api_result", width="stretch"):
        try:
            run = persist_result(database_store(), result, table=destination, mode="upsert")
            st.success(f"{run.persisted_table} に {run.persisted_rows:,}行をUpsertしました。")
        except Exception as exc:
            st.error(f"DB保存に失敗しました: {exc}")


def _render_csv_import() -> None:
    st.caption("CSVは補助入力です。APIで取得できないデータ、初期マスタ、過去データの移行に使います。")
    uploads = st.file_uploader(
        "DBへ追加するCSV",
        type=["csv"],
        accept_multiple_files=True,
        key="database_csv_imports",
    )
    c1, c2 = st.columns(2)
    with c1:
        table = st.text_input("保存先テーブル", "performance_input", key="csv_db_table")
    with c2:
        mode = st.selectbox("書込方法", ["upsert", "append", "replace"], key="csv_db_mode")
    frames = []
    errors = []
    for uploaded in uploads or []:
        try:
            loaded = read_csv_flexible(uploaded.getvalue())
            frame = loaded.dataframe.copy()
            frame["_source_file"] = uploaded.name
            frames.append(frame)
        except Exception as exc:
            errors.append(f"{uploaded.name}: {exc}")
    for error in errors:
        st.error(error)
    if frames:
        combined = pd.concat(frames, ignore_index=True, sort=False)
        st.dataframe(combined.head(30), hide_index=True, width="stretch")
        if st.button("CSVをDBへインポート", type="primary", key="import_csv_to_db", width="stretch"):
            try:
                payload = add_lineage(combined, "csv", table)
                rows = database_store().write_frame(table, payload, mode=mode)
                st.success(f"{table} に {rows:,}行を{mode}しました。")
            except Exception as exc:
                st.error(f"CSVインポートに失敗しました: {exc}")


def render_data_automation() -> tuple[list[LoadedCsv], pd.DataFrame | None, pd.DataFrame | None]:
    st.markdown("#### DATA AUTOMATION")
    st.caption("取得 → プレビュー → DB保存 → 分析実行を画面内で進めます。")
    mode = st.radio(
        "実行モード",
        ["supabase", "api", "csv"],
        format_func={"supabase": "DBから分析", "api": "APIから取込", "csv": "CSVをDBへ追加"}.get,
        horizontal=True,
        label_visibility="collapsed",
        key="automation_mode",
    )
    if mode == "supabase":
        _render_db_runner()
    elif mode == "api":
        _render_api_ingestion()
    else:
        _render_csv_import()

    reports: list[LoadedCsv] = []
    performance = st.session_state.get("db_performance_frame")
    if isinstance(performance, pd.DataFrame) and not performance.empty:
        reports.append(_loaded_from_frame(performance, "supabase:performance_input"))
    setting = st.session_state.get("db_setting_frame")
    master = st.session_state.get("db_master_frame")
    return (
        reports,
        setting if isinstance(setting, pd.DataFrame) and not setting.empty else None,
        master if isinstance(master, pd.DataFrame) and not master.empty else None,
    )
