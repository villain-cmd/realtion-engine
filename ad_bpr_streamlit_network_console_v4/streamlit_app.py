from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from src.io_utils import dataframe_to_csv_bytes, json_bytes, read_csv_flexible
from src.metrics import summarize_kpis
from src.network_viz import (
    METRIC_LABELS,
    build_keyword_similarity_network,
    build_product_keyword_network,
    build_product_similarity_network,
    correlation_heatmap,
    performance_scatter,
    scale_treemap,
    term_cooccurrence_figure,
    term_frequency_figure,
    top_network_entities,
)
from src.relationship_canvas import render_relationship_canvas
from src.output_generator import make_download_payloads
from src.pipeline import build_pipeline, finalize_operator_decisions, update_state_bundle
from src.policy import Policy
from src.state_bundle import StateBundle
from src.template_catalog import TEMPLATES, all_column_dictionary, template_pack_bytes
from src.ui_theme import (
    brand_header,
    callout,
    hero_empty_state,
    inject_theme,
    insight_list,
    kpi_cards,
    section_heading,
)

st.set_page_config(
    page_title="Profit Network Console | 広告運用計算機",
    page_icon="◉",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_theme()



@st.cache_data(show_spinner=False, max_entries=32)
def cached_product_keyword_network(df: pd.DataFrame, metric: str, max_products: int, max_keywords: int):
    return build_product_keyword_network(df, metric=metric, max_products=max_products, max_keywords=max_keywords)


@st.cache_data(show_spinner=False, max_entries=32)
def cached_keyword_similarity_network(df: pd.DataFrame, metric: str, max_keywords: int, threshold: float):
    return build_keyword_similarity_network(df, metric=metric, max_keywords=max_keywords, threshold=threshold)


@st.cache_data(show_spinner=False, max_entries=32)
def cached_product_similarity_network(df: pd.DataFrame, metric: str, max_products: int, threshold: float):
    return build_product_similarity_network(df, metric=metric, max_products=max_products, threshold=threshold)


@st.cache_data(show_spinner=False, max_entries=32)
def cached_term_frequency(df: pd.DataFrame, metric: str, top_n: int):
    return term_frequency_figure(df, metric=metric, top_n=top_n, height=540)


@st.cache_data(show_spinner=False, max_entries=32)
def cached_term_cooccurrence(df: pd.DataFrame, metric: str, top_terms: int):
    return term_cooccurrence_figure(df, metric=metric, top_terms=top_terms, height=540)

PLOTLY_CONFIG = {
    "displaylogo": False,
    "scrollZoom": True,
    "responsive": True,
    "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
    "toImageButtonOptions": {"format": "png", "filename": "ad_network_map", "scale": 2},
}


def yen(value: float) -> str:
    return f"¥{float(value):,.0f}"


def pct(value: float) -> str:
    return f"{float(value):,.1f}%"


def number(value: float) -> str:
    return f"{float(value):,.0f}"


def merge_product_labels(decisions: pd.DataFrame, setting_df: pd.DataFrame | None) -> pd.DataFrame:
    out = decisions.copy()
    if "product_name" not in out:
        out["product_name"] = ""
    out["product_name"] = out["product_name"].fillna("").astype(str)
    if setting_df is not None and not setting_df.empty and {"商品管理番号", "商品名"}.issubset(setting_df.columns):
        names = (
            setting_df[["商品管理番号", "商品名"]]
            .dropna(subset=["商品管理番号"])
            .assign(商品管理番号=lambda x: x["商品管理番号"].astype(str).str.strip())
            .drop_duplicates("商品管理番号", keep="last")
            .set_index("商品管理番号")["商品名"]
            .astype(str)
            .to_dict()
        )
        missing = out["product_name"].str.strip().eq("")
        out.loc[missing, "product_name"] = out.loc[missing, "product_id"].astype(str).map(names).fillna("")
    out.loc[out["product_name"].str.strip().eq(""), "product_name"] = out.loc[
        out["product_name"].str.strip().eq(""), "product_id"
    ].astype(str)
    return out


def render_template_panel(template_key: str) -> None:
    template = TEMPLATES[template_key]
    st.download_button(
        "テンプレートをダウンロード",
        data=template.to_bytes(),
        file_name=template.filename,
        mime="text/csv",
        width="stretch",
        key=f"download_template_{template_key}",
    )
    with st.expander("カラム定義を見る", expanded=False):
        st.dataframe(
            template.columns,
            hide_index=True,
            width="stretch",
            column_config={
                "column": st.column_config.TextColumn("カラム"),
                "requirement": st.column_config.TextColumn("必須度"),
                "type": st.column_config.TextColumn("型"),
                "example": st.column_config.TextColumn("入力例"),
                "definition": st.column_config.TextColumn("定義", width="large"),
            },
        )


def render_input_dock() -> tuple[list, object | None, object | None]:
    section_heading(
        "DATA DOCK",
        "3ファイルを読み込む",
        "各カードからテンプレートとカラム定義を取得できます。楽天RPPの実績CSVはRMS出力をそのまま投入できます。",
    )
    top_left, top_right = st.columns([4, 1])
    with top_left:
        st.markdown(
            "<div class='small-note'>推奨順序: 実績レポート → 現在設定 → 商品マスタ。前回状態は左サイドバーから追加します。</div>",
            unsafe_allow_html=True,
        )
    with top_right:
        st.download_button(
            "3種テンプレート一括",
            data=template_pack_bytes(),
            file_name="ad_calculator_input_templates_v3.zip",
            mime="application/zip",
            width="stretch",
            key="download_all_templates",
        )

    col1, col2, col3 = st.columns(3, gap="medium")
    with col1:
        with st.container(border=True):
            st.markdown("<span class='input-index'>01</span><b>実績レポート</b>", unsafe_allow_html=True)
            st.markdown("<div class='input-meta'>商品別・キーワード別を複数投入。7日/28日等の異なる集計期間も同時に認識します。</div>", unsafe_allow_html=True)
            report_files = st.file_uploader(
                "実績レポートCSV",
                type=["csv"],
                accept_multiple_files=True,
                help="楽天RPP商品別・キーワード別、または標準実績CSV。",
                label_visibility="collapsed",
                key="performance_files",
            )
            st.markdown("<span class='schema-chip'>CSV</span><span class='schema-chip'>複数可</span><span class='schema-chip'>RMS生データ可</span>", unsafe_allow_html=True)
            render_template_panel("performance")
    with col2:
        with st.container(border=True):
            st.markdown("<span class='input-index'>02</span><b>現在の入札設定</b>", unsafe_allow_html=True)
            st.markdown("<div class='input-meta'>実績CPCではなく、媒体に登録されている商品CPC・キーワードCPCを制御基準として確定します。</div>", unsafe_allow_html=True)
            setting_file = st.file_uploader(
                "現在の入札設定CSV",
                type=["csv"],
                label_visibility="collapsed",
                key="setting_file",
            )
            st.markdown("<span class='schema-chip'>CP932対応</span><span class='schema-chip'>商品×KW</span><span class='schema-chip'>差分照合</span>", unsafe_allow_html=True)
            render_template_panel("settings")
    with col3:
        with st.container(border=True):
            st.markdown("<span class='input-index'>03</span><b>商品マスタ</b>", unsafe_allow_html=True)
            st.markdown("<div class='input-meta'>粗利・販促費・カテゴリ・商品名を付与。ネットワークと粗利演算の意味を決める基礎マスタです。</div>", unsafe_allow_html=True)
            product_master_file = st.file_uploader(
                "商品マスタCSV",
                type=["csv"],
                help="粗利率、その他販促費率、商品名、カテゴリ、CPC上下限等。",
                label_visibility="collapsed",
                key="product_master_file",
            )
            st.markdown("<span class='schema-chip'>粗利</span><span class='schema-chip'>商品名</span><span class='schema-chip'>カテゴリ</span>", unsafe_allow_html=True)
            render_template_panel("product_master")
    return report_files or [], setting_file, product_master_file


# ---------------------------
# State and operating policy
# ---------------------------
with st.sidebar:
    st.markdown("### STATE / POLICY")
    st.caption("前回の提案・実適用値・手動LOCKを継続します。")
    state_file = st.file_uploader("前回の state_bundle.zip", type=["zip"], key="state_bundle")

try:
    state = StateBundle.from_zip_bytes(state_file.getvalue()) if state_file else StateBundle.empty()
except Exception as exc:
    st.error(f"状態バンドルを読み込めません: {exc}")
    st.stop()

base = state.policy
with st.sidebar:
    st.divider()
    st.markdown("#### 粗利・CPC制約")
    target_roas_pct = st.number_input("手動目標ROAS（%）", 100.0, 3000.0, float(base.target_roas_pct), 10.0)
    default_margin_rate_pct = st.number_input("粗利率未登録時の試算値（%）", 0.0, 100.0, float(base.default_margin_rate * 100.0), 1.0)
    allow_assumed_sim = st.checkbox("粗利未登録でも試算を表示", value=base.allow_assumed_margin_for_simulation)
    allow_assumed_upload = st.checkbox("粗利仮定値の行も入稿可", value=base.allow_assumed_margin_for_upload, help="通常はOFFを推奨。")
    min_cpc = st.number_input("共通最低CPC", 1.0, 300.0, float(base.min_cpc_default), 1.0)
    max_cpc = st.number_input("共通最高CPC", 1.0, 1000.0, float(base.max_cpc_default), 1.0)
    max_raise_pct = st.slider("1回最大増額率", 0.0, 1.0, float(base.max_raise_pct), 0.05)
    max_down_pct = st.slider("1回最大減額率", 0.0, 1.0, float(base.max_down_pct), 0.05)
    max_abs_change = st.number_input("1回最大変更額（円）", 1.0, 300.0, float(base.max_absolute_change_yen), 1.0)
    deadband_pct = st.slider("据置デッドバンド（率）", 0.0, 0.5, float(base.deadband_pct), 0.01)
    st.divider()
    st.markdown("#### 観測・停止")
    min_clicks = st.number_input("通常判定の最低クリック数", 1, 1000, int(base.min_clicks_for_judgement), 1)
    no_order_stop = st.number_input("注文0の停止クリック閾値", 1, 5000, int(base.no_order_stop_clicks), 5)
    observation_days = st.number_input("変更後の観測日数", 1, 60, int(base.change_observation_days), 1)

policy = Policy.from_dict({
    **base.to_dict(),
    "target_roas_pct": target_roas_pct,
    "default_margin_rate": default_margin_rate_pct / 100.0,
    "allow_assumed_margin_for_simulation": allow_assumed_sim,
    "allow_assumed_margin_for_upload": allow_assumed_upload,
    "min_cpc_default": min_cpc,
    "max_cpc_default": max_cpc,
    "max_raise_pct": max_raise_pct,
    "max_down_pct": max_down_pct,
    "max_absolute_change_yen": max_abs_change,
    "deadband_pct": deadband_pct,
    "min_clicks_for_judgement": int(min_clicks),
    "no_order_stop_clicks": int(no_order_stop),
    "change_observation_days": int(observation_days),
})

header_slot = st.empty()
with header_slot.container():
    brand_header()

has_uploaded_reports = bool(st.session_state.get("performance_files"))
if not has_uploaded_reports:
    hero_empty_state()
with st.expander("DATA DOCK — 3入力ファイル / テンプレート", expanded=not has_uploaded_reports):
    report_files, setting_file, product_master_file = render_input_dock()

if not report_files:
    section_heading(
        "INPUT CONTRACT",
        "入力定義を先に確認する",
        "テンプレートは入力例であり、実データの必須列・意味・型をカラム辞書として固定しています。",
    )
    st.dataframe(
        all_column_dictionary(),
        hide_index=True,
        width="stretch",
        height=460,
        column_config={"definition": st.column_config.TextColumn("定義", width="large")},
    )
    callout("実績レポートを投入すると、ホームが商品×キーワードのネットワークインテリジェンスへ切り替わります。")
    st.stop()

# ---------------------------
# Read and validate uploads
# ---------------------------
loaded_reports = []
errors: list[str] = []
for uploaded in report_files:
    try:
        loaded = read_csv_flexible(uploaded.getvalue())
        loaded.name = uploaded.name
        if loaded.file_type not in {"rakuten_rpp_item_report", "rakuten_rpp_keyword_report", "normalized_performance"}:
            errors.append(f"{uploaded.name}: 実績レポートとして認識できません（{loaded.file_type}）")
        else:
            loaded_reports.append(loaded)
    except Exception as exc:
        errors.append(f"{uploaded.name}: {exc}")

setting_df = None
loaded_setting = None
if setting_file:
    try:
        loaded_setting = read_csv_flexible(setting_file.getvalue())
        loaded_setting.name = setting_file.name
        if loaded_setting.file_type != "rakuten_rpp_setting":
            errors.append(f"{setting_file.name}: 楽天RPP設定CSVとして認識できません（{loaded_setting.file_type}）")
        else:
            setting_df = loaded_setting.dataframe
    except Exception as exc:
        errors.append(f"{setting_file.name}: {exc}")

product_master_df = None
loaded_master = None
if product_master_file:
    try:
        loaded_master = read_csv_flexible(product_master_file.getvalue())
        loaded_master.name = product_master_file.name
        if loaded_master.file_type not in {"product_master", "generic_csv"}:
            errors.append(f"{product_master_file.name}: 商品マスタとして認識できません（{loaded_master.file_type}）")
        else:
            product_master_df = loaded_master.dataframe
    except Exception as exc:
        errors.append(f"{product_master_file.name}: {exc}")

if errors:
    for err in errors:
        st.error(err)
    st.stop()

context = build_pipeline(loaded_reports, setting_df, product_master_df, state, policy)
decisions = merge_product_labels(context.decisions, setting_df)
header_slot.empty()
with header_slot.container():
    brand_header(run_id=context.run_id, quality_status=context.quality.status)

if context.quality.status == "BLOCK":
    callout("データ品質ルールにより自動提案と入稿反映を停止しています。ネットワーク分析は確認できますが、出力はブロックされます。", "error")
elif context.quality.status == "WARN":
    callout("入力に警告があります。品質タブで欠損・行数急減・重複を確認してから判断してください。", "warn")

# ---------------------------
# Workspace navigation
# ---------------------------
home_tab, keyword_tab, product_tab, decision_tab, export_tab, quality_tab = st.tabs([
    "01  NETWORK HOME",
    "02  KEYWORD MINE",
    "03  PRODUCT MAP",
    "04  DECISION STUDIO",
    "05  EXPORT CENTER",
    "06  DATA QUALITY",
])

with home_tab:
    summary = summarize_kpis(context.windows, entity_type="ITEM")
    item_count = int(decisions.loc[decisions["entity_type"].eq("ITEM"), "product_id"].nunique())
    keyword_count = int(decisions.loc[decisions["entity_type"].eq("KEYWORD"), "keyword"].nunique())
    changed_candidates = int(decisions.get("recommended_cpc", pd.Series(index=decisions.index)).ne(decisions.get("current_bid", pd.Series(index=decisions.index))).sum())
    kpi_cards([
        {"label": "PRODUCTS", "value": f"{item_count:,}", "note": "商品単位ノード", "accent": "#0B1220"},
        {"label": "KEYWORDS", "value": f"{keyword_count:,}", "note": "正規化前のユニーク語", "accent": "#79BCE8"},
        {"label": "AD SPEND", "value": yen(summary["cost"]), "note": "商品別レポート基準", "accent": "#D99424"},
        {"label": "ATTR. SALES", "value": yen(summary["sales_attr"]), "note": "楽天720h / Yahoo24h", "accent": "#2F7FE5"},
        {"label": "ROAS", "value": pct(summary["roas_attr_pct"]), "note": "評価指標", "accent": "#128F91"},
        {"label": "CPC MOVES", "value": f"{changed_candidates:,}", "note": "推奨値と基準値の差分", "accent": "#C85A72"},
    ])

    section_heading(
        "RELATIONSHIP MAP / CUSTOM CANVAS",
        "商品管理番号とキーワードの関係を、売上規模と判定で読む",
        "商品管理番号を親ノード、キーワードを接続ノードとして表示します。円サイズは属性売上、色は判定が初期値です。描画・ズーム・検索・選択・ラベル衝突回避は外部グラフUIライブラリを使わず実装しています。",
    )
    ctrl1, ctrl2, ctrl3 = st.columns([1, 1, 1.3])
    with ctrl1:
        home_products = st.slider("商品ノード上限", 10, 100, 48, 2, key="home_products")
    with ctrl2:
        home_keywords = st.slider("KWノード上限", 20, 220, 110, 5, key="home_keywords")
    with ctrl3:
        home_color = st.selectbox(
            "初期色分け",
            ["action", "roas", "community", "entity"],
            format_func={"action": "判定", "roas": "ROAS", "community": "クラスタ", "entity": "商品 / キーワード"}.get,
            key="home_color",
        )

    home_metric = "sales_attr"
    network = cached_product_keyword_network(decisions, home_metric, home_products, home_keywords)
    kpi_cards([
        {"label": "RELATIONSHIPS", "value": f"{network.summary.get('edges', 0):,}", "note": "商品 × キーワード実績", "accent": "#1769C2"},
        {"label": "CLUSTERS", "value": f"{network.summary.get('clusters', 0):,}", "note": "接続構造から検出", "accent": "#087F82"},
        {"label": "TOP10 SHARE", "value": f"{network.summary.get('top10_share_pct', 0.0):.1f}%", "note": "売上集中度", "accent": "#A86407"},
        {"label": "LARGEST CLUSTER", "value": f"{network.summary.get('largest_cluster_share_pct', 0.0):.1f}%", "note": "最大需要群の占有", "accent": "#7B61B4"},
        {"label": "SIZE", "value": "SALES", "note": "円面積 = 属性売上", "accent": "#0B1220"},
        {"label": "DEFAULT COLOR", "value": "ACTION", "note": "増額 / 減額 / 停止 / 維持 / 観測", "accent": "#B33F5A"},
    ])
    render_relationship_canvas(
        network,
        metric="sales_attr",
        graph_kind="relationship",
        title="商品管理番号 × キーワード Relation Map",
        subtitle="円サイズ = 属性売上 / 色 = 判定 / 線 = 商品とキーワードの実績接続",
        default_color_mode=home_color,
        target_roas_pct=float(policy.target_roas_pct),
        height=780,
    )

    hubs = top_network_entities(network, top_n=10)
    with st.expander("関係構造の上位ノードと解析データ", expanded=False):
        if not hubs.empty:
            hub_cols = [c for c in ["node_type", "product_id", "keyword", "sales_attr", "cost", "roas_attr_pct", "weighted_degree", "bridge_score", "community", "action", "decision_status"] if c in hubs]
            st.dataframe(hubs[hub_cols], hide_index=True, width="stretch")
        dl1, dl2 = st.columns(2)
        with dl1:
            st.download_button(
                "ノードCSV",
                data=dataframe_to_csv_bytes(network.nodes, encoding="utf-8-sig"),
                file_name=f"network_nodes_{context.run_id}.csv",
                mime="text/csv",
                width="stretch",
                key="download_network_nodes",
            )
        with dl2:
            st.download_button(
                "エッジCSV",
                data=dataframe_to_csv_bytes(network.edges, encoding="utf-8-sig"),
                file_name=f"network_edges_{context.run_id}.csv",
                mime="text/csv",
                width="stretch",
                key="download_network_edges",
            )

    section_heading(
        "SCALE & CORRELATION",
        "規模と効率を同じキャンバスで見る",
        "面積で事業規模、散布図で売上反応、相関行列で指標同士の連動を確認します。",
    )
    chart1, chart2 = st.columns(2, gap="medium")
    with chart1:
        st.caption("商品ポートフォリオ / 面積 = 規模、色 = ROAS")
        st.plotly_chart(scale_treemap(decisions, metric=home_metric), width="stretch", config=PLOTLY_CONFIG, key="home_treemap")
    with chart2:
        st.caption("商品反応 / x = クリック、y = 属性売上、円 = 広告費")
        st.plotly_chart(performance_scatter(decisions, entity_type="ITEM"), width="stretch", config=PLOTLY_CONFIG, key="home_scatter")
    st.caption("商品指標のSpearman相関。非線形・外れ値の影響を抑えて、増減方向の連動を確認します。")
    st.plotly_chart(correlation_heatmap(decisions, entity_type="ITEM", height=480), width="stretch", config=PLOTLY_CONFIG, key="home_corr")

with keyword_tab:
    section_heading(
        "TEXT MINING",
        "キーワードを文字列ではなく概念群として扱う",
        "NFKC正規化、形態素分解、文字n-gram TF-IDFにより、表記揺れ・近似語・橋渡し語・概念クラスタを抽出します。",
    )
    kc1, kc2, kc3, kc4 = st.columns([1.3, 1, 1, 1])
    kw_metric_options = [m for m in ["sales_attr", "clicks", "cost", "orders_attr"] if m in decisions.columns]
    with kc1:
        kw_metric = st.selectbox("重み指標", kw_metric_options, format_func=lambda x: METRIC_LABELS.get(x, x), key="kw_metric")
    with kc2:
        kw_threshold = st.slider("類似度閾値", 0.15, 0.80, 0.34, 0.01, key="kw_threshold")
    with kc3:
        kw_max_nodes = st.slider("KWノード上限", 20, 220, 120, 5, key="kw_max_nodes")
    with kc4:
        kw_top_terms = st.slider("概念語上限", 10, 40, 24, 2, key="kw_top_terms")

    kw_network = cached_keyword_similarity_network(decisions, kw_metric, kw_max_nodes, kw_threshold)
    render_relationship_canvas(
        kw_network,
        metric=kw_metric,
        graph_kind="keyword_similarity",
        title="Keyword Similarity Graph",
        subtitle="円サイズ = 選択指標 / 線 = 文字n-gram類似度 / ダブルクリック = 近接語だけにフォーカス",
        default_color_mode="community",
        target_roas_pct=float(policy.target_roas_pct),
        height=720,
    )
    bridges = kw_network.nodes.sort_values(["bridge_score", "weighted_degree"], ascending=False).head(9) if not kw_network.nodes.empty else pd.DataFrame()
    st.markdown("#### Bridge keywords")
    insight_rows = [
        {
            "name": str(row.get("full_label", "")),
            "sub": f"cluster {int(row.get('community', 0)) + 1} / bridge",
            "value": f"{float(row.get('bridge_score', 0)):.3f}",
        }
        for _, row in bridges.iterrows()
    ]
    insight_list(insight_rows)
    st.markdown("<div class='small-note'>橋渡しスコアが高い語は、複数の需要クラスタをつなぐため、商品横断の探索・追加候補として優先確認します。</div>", unsafe_allow_html=True)

    m1, m2 = st.columns([1, 1.15], gap="medium")
    with m1:
        st.caption("概念語の規模 / キーワード内の語を重み付き集計")
        term_fig, term_table = cached_term_frequency(decisions, kw_metric, kw_top_terms)
        st.plotly_chart(term_fig, width="stretch", config=PLOTLY_CONFIG, key="term_frequency")
    with m2:
        st.caption("概念語の共起 / 同じキーワード内で一緒に現れる語")
        co_fig, co_matrix = cached_term_cooccurrence(decisions, "clicks", min(18, kw_top_terms))
        st.plotly_chart(co_fig, width="stretch", config=PLOTLY_CONFIG, key="term_cooccurrence")

    kw_dl1, kw_dl2, kw_dl3 = st.columns(3)
    with kw_dl1:
        st.download_button(
            "KWクラスタCSV",
            data=dataframe_to_csv_bytes(kw_network.nodes, encoding="utf-8-sig"),
            file_name=f"keyword_clusters_{context.run_id}.csv",
            mime="text/csv",
            width="stretch",
            key="download_kw_clusters",
        )
    with kw_dl2:
        st.download_button(
            "KW類似エッジCSV",
            data=dataframe_to_csv_bytes(kw_network.edges, encoding="utf-8-sig"),
            file_name=f"keyword_similarity_edges_{context.run_id}.csv",
            mime="text/csv",
            width="stretch",
            key="download_kw_edges",
        )
    with kw_dl3:
        st.download_button(
            "概念語頻度CSV",
            data=dataframe_to_csv_bytes(term_table, encoding="utf-8-sig"),
            file_name=f"term_frequency_{context.run_id}.csv",
            mime="text/csv",
            width="stretch",
            key="download_term_frequency",
        )

    section_heading("KEYWORD TABLE", "クラスタと規模の明細", "ネットワークのノード属性を表形式で検証します。")
    if not kw_network.nodes.empty:
        kw_table = kw_network.nodes.sort_values(["weighted_degree", "value"], ascending=False)[[
            "full_label", "community", "value", "sales_attr", "cost", "clicks", "roas_attr_pct", "weighted_degree", "bridge_score"
        ]].rename(columns={"full_label": "keyword", "community": "cluster"})
        kw_table["cluster"] = kw_table["cluster"] + 1
        st.dataframe(
            kw_table,
            hide_index=True,
            width="stretch",
            height=520,
            column_config={
                "value": st.column_config.NumberColumn(METRIC_LABELS.get(kw_metric, kw_metric), format="%.0f"),
                "sales_attr": st.column_config.NumberColumn("属性売上", format="¥%.0f"),
                "cost": st.column_config.NumberColumn("広告費", format="¥%.0f"),
                "roas_attr_pct": st.column_config.NumberColumn("ROAS", format="%.1f%%"),
                "weighted_degree": st.column_config.NumberColumn("Hub", format="%.2f"),
                "bridge_score": st.column_config.NumberColumn("Bridge", format="%.3f"),
            },
        )

with product_tab:
    section_heading(
        "PRODUCT PORTFOLIO",
        "商品単位の近接・競合・共有需要を可視化",
        "同じキーワード群を持つ商品、商品名が近い商品を接続し、商品ポートフォリオ内の重複・空白・ハブを表示します。",
    )
    pc1, pc2, pc3 = st.columns([1.3, 1, 1])
    with pc1:
        product_metric = st.selectbox(
            "規模指標",
            [m for m in ["sales_attr", "clicks", "cost", "orders_attr"] if m in decisions.columns],
            format_func=lambda x: METRIC_LABELS.get(x, x),
            key="product_metric",
        )
    with pc2:
        product_threshold = st.slider("商品類似度閾値", 0.05, 0.70, 0.18, 0.01, key="product_threshold")
    with pc3:
        product_nodes = st.slider("商品ノード上限", 20, 120, 80, 5, key="product_nodes")

    product_network = cached_product_similarity_network(decisions, product_metric, product_nodes, product_threshold)
    render_relationship_canvas(
        product_network,
        metric=product_metric,
        graph_kind="product_similarity",
        title="Product Affinity Graph",
        subtitle="円サイズ = 選択指標 / 線 = 共有キーワード + 商品名類似度 / 色 = 関係クラスタ",
        default_color_mode="community",
        target_roas_pct=float(policy.target_roas_pct),
        height=700,
    )
    st.markdown("#### Product hubs")
    product_hubs = top_network_entities(product_network, node_type="PRODUCT", top_n=9)
    insight_rows = [
        {
            "name": str(row.get("full_label", "")),
            "sub": str(row.get("product_id", "")),
            "value": f"hub {float(row.get('weighted_degree', 0)):.1f}",
        }
        for _, row in product_hubs.iterrows()
    ]
    insight_list(insight_rows)

    item_rows = decisions[decisions["entity_type"].eq("ITEM")].copy()
    if not item_rows.empty:
        product_labels = (
            item_rows.assign(display=lambda x: x["product_id"].astype(str) + "｜" + x["product_name"].astype(str))
            .sort_values("sales_attr", ascending=False)
            .drop_duplicates("product_id")
        )
        selected_display = st.selectbox("商品を選択して接続キーワードを検証", product_labels["display"].tolist(), key="selected_product")
        selected_product = selected_display.split("｜", 1)[0]
        product_kw = decisions[(decisions["entity_type"].eq("KEYWORD")) & (decisions["product_id"].astype(str).eq(selected_product))].copy()
        if not product_kw.empty:
            show_cols = [
                "keyword", "clicks", "cost", "sales_attr", "orders_attr", "roas_attr_pct", "current_bid", "recommended_cpc",
                "expected_incremental_profit", "decision_status", "reason",
            ]
            st.dataframe(
                product_kw.sort_values("sales_attr", ascending=False)[[c for c in show_cols if c in product_kw]],
                hide_index=True,
                width="stretch",
                height=460,
                column_config={
                    "cost": st.column_config.NumberColumn("広告費", format="¥%.0f"),
                    "sales_attr": st.column_config.NumberColumn("属性売上", format="¥%.0f"),
                    "roas_attr_pct": st.column_config.NumberColumn("ROAS", format="%.1f%%"),
                    "current_bid": st.column_config.NumberColumn("現在CPC", format="¥%.0f"),
                    "recommended_cpc": st.column_config.NumberColumn("推奨CPC", format="¥%.0f"),
                    "expected_incremental_profit": st.column_config.NumberColumn("期待粗利増分", format="¥%.0f"),
                    "reason": st.column_config.TextColumn("根拠", width="large"),
                },
            )

with decision_tab:
    section_heading(
        "DECISION STUDIO",
        "演算結果を人が補正する",
        "異常時は自動反映せず、PENDING / MODIFY / LOCK / FORCE_STOP / REJECTを入力。手動値は次回計算の基準へ継承します。",
    )
    if decisions.empty:
        st.warning("判断対象がありません。")
        st.stop()

    action_counts = decisions["decision_status"].value_counts().rename_axis("decision_status").reset_index(name="count")
    action_counts["share"] = action_counts["count"] / action_counts["count"].sum() * 100
    st.bar_chart(action_counts.set_index("decision_status")["count"], horizontal=True, height=260)

    filter_col1, filter_col2, filter_col3, filter_col4 = st.columns([1, 1.4, 1, 1])
    with filter_col1:
        entity_filter = st.multiselect("粒度", sorted(decisions["entity_type"].unique()), default=sorted(decisions["entity_type"].unique()), key="decision_entity_filter")
    with filter_col2:
        status_filter = st.multiselect("状態", sorted(decisions["decision_status"].unique()), default=sorted(decisions["decision_status"].unique()), key="decision_status_filter")
    with filter_col3:
        min_profit = st.number_input("期待粗利増分 下限", value=float(decisions["expected_incremental_profit"].min()) if "expected_incremental_profit" in decisions else 0.0, step=100.0, key="min_profit_filter")
    with filter_col4:
        text_filter = st.text_input("商品ID / 商品名 / KW", key="decision_text_filter")

    visible = decisions[decisions["entity_type"].isin(entity_filter) & decisions["decision_status"].isin(status_filter)].copy()
    if "expected_incremental_profit" in visible:
        visible = visible[pd.to_numeric(visible["expected_incremental_profit"], errors="coerce").fillna(0) >= min_profit]
    if text_filter.strip():
        query = text_filter.strip().lower()
        haystack = (
            visible["product_id"].astype(str) + " " + visible.get("product_name", "").astype(str) + " " + visible.get("keyword", "").astype(str)
        ).str.lower()
        visible = visible[haystack.str.contains(query, regex=False)]
    visible = visible.sort_values(["expected_incremental_profit", "sales_attr"], ascending=False)

    if st.session_state.get("operator_run_id") != context.run_id:
        st.session_state["operator_run_id"] = context.run_id
        st.session_state["operator_edits"] = {
            str(row["decision_id"]): {
                "operator_action": row.get("operator_action", "PENDING"),
                "operator_cpc": row.get("operator_cpc", row.get("recommended_cpc", np.nan)),
                "operator_reason": row.get("operator_reason", ""),
            }
            for _, row in decisions.iterrows()
        }

    edits = st.session_state.get("operator_edits", {})
    for idx, row in visible.iterrows():
        saved = edits.get(str(row["decision_id"]))
        if saved:
            for col in ["operator_action", "operator_cpc", "operator_reason"]:
                visible.at[idx, col] = saved.get(col, row.get(col))

    editor_cols = [
        "decision_id", "entity_type", "product_id", "product_name", "keyword", "clicks", "cost", "sales_attr", "orders_attr",
        "roas_attr_pct", "current_bid", "recommended_cpc", "expected_incremental_profit", "decision_status",
        "reason", "operator_action", "operator_cpc", "operator_reason",
    ]
    editor_cols = [c for c in editor_cols if c in visible.columns]
    edited = st.data_editor(
        visible[editor_cols],
        hide_index=True,
        width="stretch",
        height=620,
        num_rows="fixed",
        disabled=[c for c in editor_cols if c not in {"operator_action", "operator_cpc", "operator_reason"}],
        column_config={
            "decision_id": None,
            "entity_type": st.column_config.TextColumn("粒度", width="small"),
            "product_id": st.column_config.TextColumn("商品ID"),
            "product_name": st.column_config.TextColumn("商品名", width="medium"),
            "keyword": st.column_config.TextColumn("キーワード", width="medium"),
            "operator_action": st.column_config.SelectboxColumn(
                "手動操作",
                options=["PENDING", "ACCEPT", "MODIFY", "LOCK", "FORCE_STOP", "REJECT"],
                required=True,
            ),
            "operator_cpc": st.column_config.NumberColumn("適用CPC", min_value=0, step=1, format="¥%d"),
            "operator_reason": st.column_config.TextColumn("手動理由", width="medium"),
            "cost": st.column_config.NumberColumn("広告費", format="¥%d"),
            "sales_attr": st.column_config.NumberColumn("属性売上", format="¥%d"),
            "roas_attr_pct": st.column_config.NumberColumn("ROAS", format="%.1f%%"),
            "current_bid": st.column_config.NumberColumn("登録CPC", format="¥%d"),
            "recommended_cpc": st.column_config.NumberColumn("推奨CPC", format="¥%d"),
            "expected_incremental_profit": st.column_config.NumberColumn("期待粗利増分", format="¥%.0f"),
            "reason": st.column_config.TextColumn("演算根拠", width="large"),
        },
        key="decision_editor_v3",
    )
    for _, row in edited.iterrows():
        decision_id = str(row["decision_id"])
        edits[decision_id] = {
            "operator_action": row.get("operator_action", "PENDING"),
            "operator_cpc": row.get("operator_cpc", np.nan),
            "operator_reason": row.get("operator_reason", ""),
        }
    st.session_state["operator_edits"] = edits

    full = decisions.copy()
    for idx, row in full.iterrows():
        saved = edits.get(str(row["decision_id"]))
        if saved:
            for col in ["operator_action", "operator_cpc", "operator_reason"]:
                full.at[idx, col] = saved[col]
    finalized = finalize_operator_decisions(full)
    if context.quality.status == "BLOCK":
        finalized["final_approved"] = False
        finalized["final_changed"] = False
        finalized["upload_eligible"] = False

    with st.expander("計算式と中間値", expanded=False):
        formula_cols = [
            "entity_type", "product_id", "keyword", "prior_cvr", "smoothed_cvr", "cvr_lower_bound",
            "expected_aov", "margin_rate_used", "promo_cost_rate_used", "expected_sales_per_click",
            "expected_contribution_before_ad_per_click", "profit_guard_cpc", "roas_guard_cpc",
            "raw_target_cpc", "trend_multiplier", "attribution_maturity", "current_bid_source",
        ]
        st.dataframe(finalized[[c for c in formula_cols if c in finalized]], hide_index=True, width="stretch", height=440)
        st.code(
            "推奨CPC_raw = min(平滑化CVR × AOV × (粗利率 - その他販促費率) × 安全係数, "
            "平滑化CVR × AOV ÷ 手動目標ROAS倍率)\n"
            "推奨CPC = 商品別上下限・最大増減率・最大変更額・デッドバンドを適用した切上げ値"
        )

# Ensure finalized exists even if tabs are internally rendered differently in future Streamlit versions.
if "finalized" not in locals():
    fallback = decisions.copy()
    edits = st.session_state.get("operator_edits", {})
    for idx, row in fallback.iterrows():
        saved = edits.get(str(row["decision_id"]))
        if saved:
            for col in ["operator_action", "operator_cpc", "operator_reason"]:
                fallback.at[idx, col] = saved[col]
    finalized = finalize_operator_decisions(fallback)
    if context.quality.status == "BLOCK":
        finalized["final_approved"] = False
        finalized["final_changed"] = False
        finalized["upload_eligible"] = False

with export_tab:
    section_heading(
        "EXPORT CENTER",
        "媒体入稿・ロールバック・次回継続を同時生成",
        "CSVをダウンロードしただけでは適用済みにしません。次回の現在設定CSVで一致を確認して初めてCONFIRMEDへ遷移します。",
    )
    approved_count = int(finalized["final_approved"].sum())
    changed_count = int(finalized["final_changed"].sum())
    output_count = int(finalized["upload_eligible"].sum())
    unmatched_count = int((finalized["final_changed"] & ~finalized["upload_match"]).sum())
    kpi_cards([
        {"label": "APPROVED", "value": f"{approved_count:,}", "note": "手動・自動承認", "accent": "#128F91"},
        {"label": "CPC CHANGED", "value": f"{changed_count:,}", "note": "現在値との差分", "accent": "#2F7FE5"},
        {"label": "UPLOAD READY", "value": f"{output_count:,}", "note": "設定行一致・品質通過", "accent": "#0B1220"},
        {"label": "UNMATCHED", "value": f"{unmatched_count:,}", "note": "設定CSVに行なし", "accent": "#C85A72"},
        {"label": "RUN", "value": context.run_id[-10:], "note": "監査・再現用ID", "accent": "#8B6FC8"},
        {"label": "QUALITY", "value": context.quality.status, "note": f"score {context.quality.score:.1f}", "accent": "#D99424"},
    ])

    payloads = make_download_payloads(setting_df, finalized, context.run_id)
    download_cols = st.columns(3)
    for i, (_, (name, data, mime)) in enumerate(payloads.items()):
        with download_cols[i % 3]:
            with st.container(border=True):
                st.markdown(f"**{name}**")
                st.caption("Run ID・媒体仕様・改行/文字コードを固定した生成物")
                st.download_button(
                    "ダウンロード",
                    data=data,
                    file_name=name,
                    mime=mime,
                    width="stretch",
                    key=f"payload_{i}_{name}",
                )

    updated_state = update_state_bundle(state, context, finalized, policy)
    with st.container(border=True):
        st.markdown("### Next state bundle")
        st.caption("適用予定・確認済みCPC・手動LOCK・実績履歴・入力hashを次回へ引き継ぎます。")
        st.download_button(
            "次回用 state_bundle.zip を生成",
            data=updated_state.to_zip_bytes(),
            file_name=f"state_bundle_{context.run_id}.zip",
            mime="application/zip",
            type="primary",
            width="stretch",
            key="download_state_bundle",
        )

    run_profile = {
        "run_id": context.run_id,
        "input_hash": context.input_hash,
        "duplicate_input": context.duplicate_input,
        "quality": {"status": context.quality.status, "score": context.quality.score, **context.quality.summary},
        "policy": policy.to_dict(),
        "decision_status_counts": finalized["decision_status"].value_counts().to_dict(),
        "approved_count": approved_count,
        "changed_count": changed_count,
        "upload_output_count": output_count,
        "unmatched_count": unmatched_count,
    }
    st.download_button(
        "RunプロファイルJSON",
        data=json_bytes(run_profile),
        file_name=f"run_profile_{context.run_id}.json",
        mime="application/json",
        key="run_profile_download",
    )

with quality_tab:
    section_heading(
        "DATA QUALITY",
        "入力・変換・継続状態を監査する",
        "自動推奨より先に、ファイル種別、期間、列、行数、欠損、重複、前回設定との不一致を検証します。",
    )
    q1, q2, q3, q4 = st.columns(4)
    q1.metric("判定", context.quality.status)
    q2.metric("品質スコア", f"{context.quality.score:.1f}/100")
    q3.metric("入力行", f"{context.quality.summary.get('rows', 0):,}")
    q4.metric("重複入力", "あり" if context.duplicate_input else "なし")

    if context.quality.warnings:
        st.warning("\n".join(context.quality.warnings))
    if not context.external_manual_changes.empty:
        st.info(f"媒体管理画面での外部手動変更を {len(context.external_manual_changes):,} 件検出。新しい基準値として観測します。")
    if not context.confirmed_plans.empty:
        st.success(f"前回提案の媒体反映を {len(context.confirmed_plans):,} 件確認。効果観測を開始します。")
    if not context.quality.row_errors.empty:
        st.dataframe(context.quality.row_errors, hide_index=True, width="stretch", height=360)

    profiles = [x.profile() for x in loaded_reports]
    if loaded_setting is not None:
        profiles.append(loaded_setting.profile())
    if loaded_master is not None:
        profiles.append(loaded_master.profile())
    profile_df = pd.DataFrame(profiles)
    if not profile_df.empty:
        st.markdown("#### 読み込みプロファイル")
        profile_cols = [c for c in ["name", "file_type", "encoding", "header_row_index", "rows", "period_start", "period_end", "sha256"] if c in profile_df]
        st.dataframe(profile_df[profile_cols], hide_index=True, width="stretch")

    st.markdown("#### 3入力のカラム辞書")
    st.download_button(
        "カラム辞書CSV",
        data=all_column_dictionary().to_csv(index=False).encode("utf-8-sig"),
        file_name="input_column_dictionary_utf8.csv",
        mime="text/csv",
        key="column_dictionary_download",
    )
    st.dataframe(
        all_column_dictionary(),
        hide_index=True,
        width="stretch",
        height=620,
        column_config={"definition": st.column_config.TextColumn("定義", width="large")},
    )

st.caption(
    "計算機として異常時は対象提案を停止し、人が媒体画面またはGUIで補正します。手動適用後の最新実績・現在設定・商品マスタを再投入しても、state_bundleを介して同じRun契約で継続稼働します。"
)
