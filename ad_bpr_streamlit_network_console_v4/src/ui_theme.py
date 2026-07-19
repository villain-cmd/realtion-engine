from __future__ import annotations

from html import escape
from textwrap import dedent
from typing import Iterable

import streamlit as st


THEME_CSS = r"""
<style>
:root {
  --ink: #07111f;
  --ink-2: #15243a;
  --muted: #40536b;
  --muted-2: #5b6c82;
  --line: #cedae7;
  --line-strong: #b9c9da;
  --panel: #ffffff;
  --canvas: #eef3f8;
  --blue: #1769c2;
  --blue-bright: #2f7fe5;
  --blue-soft: #e7f1fd;
  --teal: #087f82;
  --amber: #a86407;
  --rose: #b33f5a;
  --radius-xl: 22px;
  --radius-lg: 16px;
  --shadow: 0 12px 34px rgba(17, 36, 64, 0.09);
}
html, body, [class*="css"] { font-family: Inter, "Noto Sans JP", "Hiragino Sans", "Yu Gothic UI", sans-serif; }
.stApp { background: var(--canvas); color: var(--ink); }
[data-testid="stHeader"] { background: rgba(238, 243, 248, 0.92); backdrop-filter: blur(14px); }
[data-testid="stToolbar"] { right: 1.5rem; }
#MainMenu, footer { visibility: hidden; }
.block-container { max-width: 1600px; padding-top: 1.1rem; padding-bottom: 4rem; }

/* Global text contrast */
[data-testid="stMarkdownContainer"], [data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li, [data-testid="stMarkdownContainer"] span,
[data-testid="stWidgetLabel"] p, .stCaption, [data-testid="stCaptionContainer"] p {
  color: var(--ink-2);
}
[data-testid="stCaptionContainer"] p, .stCaption { color: var(--muted) !important; font-weight: 520; }
h1, h2, h3, h4, h5, h6 { color: var(--ink) !important; }

/* Sidebar: explicit high-contrast controls without leaking dark text into labels */
[data-testid="stSidebar"] { background: #091321; border-right: 1px solid #15263b; }
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3, [data-testid="stSidebar"] h4,
[data-testid="stSidebar"] label { color: #eaf1f8 !important; }
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p { color: #c7d3e1 !important; font-size: .78rem; letter-spacing: .02em; }
[data-testid="stSidebar"] input, [data-testid="stSidebar"] textarea {
  color: #f4f7fb !important; background: #0e1a2b !important; border-color: #2a4059 !important;
}
[data-testid="stSidebar"] div[data-baseweb="select"] > div,
[data-testid="stSidebar"] div[data-baseweb="base-input"] > div { background: #0e1a2b !important; border-color: #2a4059 !important; }
[data-testid="stSidebar"] div[data-baseweb="select"] span,
[data-testid="stSidebar"] div[data-baseweb="select"] input,
[data-testid="stSidebar"] div[data-baseweb="select"] svg { color: #eef5fc !important; fill: #eef5fc !important; }
[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,.14); }
[data-testid="stSidebar"] [data-testid="stNumberInput"] button { background:#122238 !important; color:#f3f7fb !important; border-color:#2d4662 !important; }
[data-testid="stSidebar"] [data-testid="stSlider"] [role="slider"] { background:#ff5c6f !important; }

.brand-shell {
  display: flex; align-items: center; justify-content: space-between; gap: 18px;
  padding: 18px 22px; margin: 0 0 18px 0; border-radius: 20px;
  background: linear-gradient(118deg, #07111f 0%, #11233c 62%, #17466d 100%);
  box-shadow: 0 18px 42px rgba(6, 19, 38, .2); color: white; overflow: hidden; position: relative;
}
.brand-shell:after { content:""; position:absolute; width:280px; height:280px; right:-90px; top:-120px; border-radius:50%; background:radial-gradient(circle, rgba(120,190,235,.38), rgba(120,190,235,0)); }
.brand-left { display:flex; align-items:center; gap:14px; z-index:1; }
.brand-mark { width:44px; height:44px; border-radius:14px; display:grid; place-items:center; background:rgba(255,255,255,.13); border:1px solid rgba(255,255,255,.25); box-shadow: inset 0 0 18px rgba(255,255,255,.08); }
.brand-mark svg { width:27px; height:27px; }
.brand-kicker { font-size:.69rem; letter-spacing:.16em; text-transform:uppercase; color:#a9d8f6; font-weight:780; margin-bottom:3px; }
.brand-title { font-size:1.16rem; line-height:1.25; font-weight:800; letter-spacing:-.015em; color:#fff; }
.brand-sub { font-size:.78rem; color:#d0dceb; margin-top:2px; }
.brand-meta { display:flex; align-items:center; gap:8px; z-index:1; flex-wrap:wrap; justify-content:flex-end; }
.pill { display:inline-flex; align-items:center; gap:7px; padding:7px 10px; border-radius:999px; font-size:.72rem; font-weight:750; border:1px solid rgba(255,255,255,.2); background:rgba(255,255,255,.1); color:#f4f8fc; }
.pill-dot { width:7px; height:7px; border-radius:50%; background:#79c7ec; box-shadow:0 0 0 4px rgba(121,199,236,.13); }

.hero {
  padding: 28px 30px; border-radius: var(--radius-xl); background: var(--panel); border: 1px solid var(--line);
  box-shadow: var(--shadow); margin-bottom: 16px; position: relative; overflow:hidden;
}
.hero:after { content:""; position:absolute; right:-60px; bottom:-100px; width:300px; height:300px; border-radius:50%; background:radial-gradient(circle, rgba(47,127,229,.13), rgba(47,127,229,0)); }
.hero-kicker { color: var(--blue); font-weight: 800; font-size: .72rem; letter-spacing: .16em; text-transform: uppercase; }
.hero h1 { font-size: clamp(1.75rem, 3vw, 3.1rem); letter-spacing:-.045em; line-height:1.08; margin:.45rem 0 .65rem; max-width:900px; color:var(--ink); }
.hero p { max-width:850px; color:var(--muted); font-size:.98rem; line-height:1.75; margin:0; }
.hero-grid { display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap:12px; margin-top:22px; }
.hero-step { border:1px solid var(--line); border-radius:14px; padding:14px 15px; background:#f8fafc; }
.hero-step b { display:block; font-size:.78rem; margin-bottom:5px; color:var(--ink); }
.hero-step span { font-size:.74rem; line-height:1.55; color:var(--muted); }

.section-head { display:flex; justify-content:space-between; align-items:flex-end; gap:18px; margin:26px 2px 12px; }
.section-kicker { font-size:.68rem; letter-spacing:.14em; font-weight:820; color:var(--blue); text-transform:uppercase; }
.section-title { font-size:1.25rem; font-weight:800; color:var(--ink); letter-spacing:-.02em; margin-top:3px; }
.section-desc { color:var(--muted); font-size:.8rem; font-weight:530; max-width:780px; line-height:1.65; }

.kpi-grid { display:grid; grid-template-columns: repeat(6, minmax(0,1fr)); gap:10px; margin: 8px 0 16px; }
.kpi-card { background:var(--panel); border:1px solid var(--line); border-radius:16px; padding:15px 16px; min-height:104px; box-shadow:0 7px 20px rgba(25,48,78,.055); }
.kpi-label { color:var(--muted); font-size:.69rem; letter-spacing:.055em; text-transform:uppercase; font-weight:760; }
.kpi-value { color:var(--ink); font-size:1.45rem; font-weight:810; letter-spacing:-.035em; margin-top:9px; white-space:nowrap; }
.kpi-note { color:var(--muted-2); font-size:.67rem; font-weight:530; margin-top:4px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.kpi-accent { width:26px; height:3px; border-radius:999px; background:var(--blue); margin-top:11px; }

.input-card { border:1px solid var(--line); border-radius:18px; background:var(--panel); padding:4px 3px 2px; box-shadow:0 8px 24px rgba(18,38,65,.055); min-height:100%; }
.input-index { display:inline-grid; place-items:center; width:25px; height:25px; border-radius:8px; background:var(--blue-soft); color:var(--blue); font-size:.7rem; font-weight:820; margin-right:7px; }
.input-meta { color:var(--muted); font-size:.71rem; font-weight:520; line-height:1.58; margin:2px 0 6px; }
.schema-chip { display:inline-block; padding:4px 7px; margin:2px 4px 2px 0; border-radius:7px; background:#eaf0f6; color:#32465d; font-size:.65rem; font-weight:650; border:1px solid #d4e0eb; }

.insight-list { display:flex; flex-direction:column; gap:9px; }
.insight-row { display:grid; grid-template-columns:32px 1fr auto; align-items:center; gap:10px; padding:10px 11px; border:1px solid var(--line); border-radius:12px; background:#f8fafc; }
.insight-rank { width:29px; height:29px; border-radius:9px; display:grid; place-items:center; color:var(--blue); background:var(--blue-soft); font-weight:820; font-size:.7rem; }
.insight-main { min-width:0; }
.insight-name { font-size:.76rem; font-weight:760; color:var(--ink); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.insight-sub { font-size:.65rem; color:var(--muted); margin-top:2px; }
.insight-value { font-size:.72rem; font-weight:780; color:var(--ink-2); }

.legend-row { display:flex; gap:12px; align-items:center; flex-wrap:wrap; font-size:.68rem; color:var(--muted); }
.legend-dot { width:9px; height:9px; display:inline-block; border-radius:50%; margin-right:5px; }

/* Streamlit controls */
[data-testid="stMetric"] { background:var(--panel); border:1px solid var(--line); border-radius:15px; padding:12px 14px; box-shadow:0 7px 20px rgba(25,48,78,.05); }
[data-testid="stMetricLabel"] p { color:var(--muted) !important; font-weight:680; }
[data-testid="stMetricValue"] { color:var(--ink) !important; font-weight:800; letter-spacing:-.03em; }
[data-testid="stFileUploader"] { border-radius:14px; }
[data-testid="stFileUploaderDropzone"] { background:#f8fafc; border:1.3px dashed #9eb2c7; border-radius:13px; min-height:112px; }
[data-testid="stFileUploaderDropzone"] * { color:#23364d !important; }
[data-testid="stDataFrame"], [data-testid="stDataEditor"] { border:1px solid var(--line); border-radius:15px; overflow:hidden; background:white; }
[data-testid="stPlotlyChart"] { border:1px solid var(--line); border-radius:18px; background:white; box-shadow:0 8px 24px rgba(18,38,65,.055); overflow:hidden; }
[data-testid="stExpander"] { border:1px solid var(--line); border-radius:14px; background:var(--panel); }
[data-testid="stExpander"] summary, [data-testid="stExpander"] summary * { color:var(--ink-2) !important; font-weight:680; }
.stButton > button, .stDownloadButton > button { border-radius:11px; min-height:39px; font-weight:740; border:1px solid #b8c7d7; color:#17283d; background:#fff; }
.stDownloadButton > button:hover, .stButton > button:hover { border-color:var(--blue); color:var(--blue); }
button[kind="primary"] { background:var(--ink) !important; border-color:var(--ink) !important; color:#fff !important; }
button[kind="primary"]:hover { background:#15233b !important; }

/* Main-area select, inputs and sliders */
.main div[data-baseweb="select"] > div, .main div[data-baseweb="base-input"] > div,
.main [data-testid="stTextInput"] input, .main [data-testid="stNumberInput"] input {
  background:#fff !important; color:var(--ink) !important; border-color:var(--line-strong) !important;
}
.main div[data-baseweb="select"] span, .main div[data-baseweb="select"] input { color:var(--ink) !important; }
.main [data-testid="stWidgetLabel"] p { color:var(--ink-2) !important; font-weight:650; }

.stTabs [data-baseweb="tab-list"] { gap:7px; background:#dfe7f0; border:1px solid #ced9e5; border-radius:14px; padding:5px; }
.stTabs [data-baseweb="tab"] { height:42px; padding:0 17px; border-radius:10px; color:#31455d !important; font-size:.75rem; font-weight:760; }
.stTabs [data-baseweb="tab"] p { color:#31455d !important; }
.stTabs [aria-selected="true"] { background:#fff; color:var(--ink) !important; box-shadow:0 4px 12px rgba(20,38,62,.1); }
.stTabs [aria-selected="true"] p { color:var(--ink) !important; }
.stTabs [data-baseweb="tab-highlight"] { display:none; }

[data-testid="stIFrame"] { border-radius:20px; overflow:hidden; background:transparent; box-shadow:0 16px 36px rgba(8,20,36,.10); }
[data-testid="stIFrame"] iframe { border-radius:20px; background:transparent; }
div[data-testid="stHorizontalBlock"] { align-items:stretch; }
.small-note { color:var(--muted); font-size:.7rem; font-weight:520; line-height:1.6; }
.callout { padding:13px 15px; border-radius:13px; border:1px solid #b9d2ec; background:#edf6ff; color:#233f5c; font-size:.76rem; font-weight:540; line-height:1.65; }
.callout.warn { border-color:#ddc99f; background:#fff7e7; color:#5f4315; }
.callout.error { border-color:#e2b7c1; background:#fff1f4; color:#712d3b; }

@media (max-width: 1050px) {
  .kpi-grid { grid-template-columns: repeat(3, minmax(0,1fr)); }
  .hero-grid { grid-template-columns:1fr; }
}
@media (max-width: 720px) {
  .block-container { padding-left:.8rem; padding-right:.8rem; }
  .brand-shell { align-items:flex-start; }
  .brand-meta { display:none; }
  .kpi-grid { grid-template-columns: repeat(2, minmax(0,1fr)); }
  .hero { padding:22px 20px; }
}
</style>
"""


def _render_html(html: str) -> None:
    """Render custom HTML without Markdown treating indentation as a code block."""
    st.markdown(dedent(html).strip(), unsafe_allow_html=True)


def inject_theme() -> None:
    _render_html(THEME_CSS)


def brand_header(*, run_id: str | None = None, quality_status: str | None = None) -> None:
    quality = escape(quality_status or "READY")
    run = escape(run_id[-12:] if run_id else "NO RUN")
    dot_color = {"PASS": "#68d3a1", "WARN": "#f0bd63", "BLOCK": "#ef8193"}.get(quality_status or "", "#79c7ec")
    _render_html(
        f"""
        <div class="brand-shell">
          <div class="brand-left">
            <div class="brand-mark">
              <svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
                <circle cx="6" cy="16" r="3" fill="#8FD1F4"/><circle cx="16" cy="7" r="4" fill="#FFFFFF"/><circle cx="25" cy="17" r="3.5" fill="#6FB7E5"/><circle cx="15" cy="25" r="3" fill="#9AD8D7"/>
                <path d="M8.5 14.5L13.2 9.7M19.7 9.2L22.8 14.1M22.3 19.3L17.6 23.1M12.4 23.1L7.8 18.2M9 16H21.2" stroke="white" stroke-width="1.4" stroke-linecap="round" opacity=".75"/>
              </svg>
            </div>
            <div>
              <div class="brand-kicker">Commerce Ads Intelligence</div>
              <div class="brand-title">Profit Network Console</div>
              <div class="brand-sub">商品 × キーワードの構造を読み、粗利演算から入稿まで接続する運用計算機</div>
            </div>
          </div>
          <div class="brand-meta">
            <span class="pill"><span class="pill-dot" style="background:{dot_color}"></span>{quality}</span>
            <span class="pill">RUN&nbsp; {run}</span>
            <span class="pill">LOCAL ML / NO AI API</span>
          </div>
        </div>
        """
    )


def hero_empty_state() -> None:
    _render_html(
        """
        <div class="hero">
          <div class="hero-kicker">Operational Intelligence, not a demo</div>
          <h1>広告実績を、商品とキーワードの<br>“関係の構造”として読む。</h1>
          <p>3つの入力ファイルから、規模・接続・クラスタ・相関・粗利インパクトを同時に可視化します。解析後は同じ画面でCPC判断を補正し、媒体アップロード用ファイルと次回継続用の状態バンドルを生成します。</p>
          <div class="hero-grid">
            <div class="hero-step"><b>01 / MAP</b><span>商品管理番号とキーワードを接続し、売上規模と判定を一つの関係図で把握。</span></div>
            <div class="hero-step"><b>02 / MINE</b><span>日本語キーワードを正規化・形態素分解・類似度計算し、概念群と橋渡し語を抽出。</span></div>
            <div class="hero-step"><b>03 / ACT</b><span>粗利制約付き推奨CPCを人が確認し、入稿CSV・ロールバック・次回状態へ接続。</span></div>
          </div>
        </div>
        """
    )


def section_heading(kicker: str, title: str, description: str = "") -> None:
    _render_html(
        f"""
        <div class="section-head">
          <div><div class="section-kicker">{escape(kicker)}</div><div class="section-title">{escape(title)}</div></div>
          <div class="section-desc">{escape(description)}</div>
        </div>
        """
    )


def kpi_cards(cards: Iterable[dict[str, str]]) -> None:
    blocks = []
    for card in cards:
        accent = card.get("accent", "#2f7fe5")
        blocks.append(
            dedent(
                f"""
                <div class="kpi-card">
                  <div class="kpi-label">{escape(card.get('label', ''))}</div>
                  <div class="kpi-value">{escape(card.get('value', ''))}</div>
                  <div class="kpi-note">{escape(card.get('note', ''))}</div>
                  <div class="kpi-accent" style="background:{accent}"></div>
                </div>
                """
            ).strip()
        )
    _render_html(f"<div class='kpi-grid'>{''.join(blocks)}</div>")


def insight_list(rows: Iterable[dict[str, str]], *, empty_text: str = "該当データなし") -> None:
    items = []
    for idx, row in enumerate(rows, start=1):
        items.append(
            dedent(
                f"""
                <div class="insight-row">
                  <div class="insight-rank">{idx:02d}</div>
                  <div class="insight-main"><div class="insight-name">{escape(row.get('name', ''))}</div><div class="insight-sub">{escape(row.get('sub', ''))}</div></div>
                  <div class="insight-value">{escape(row.get('value', ''))}</div>
                </div>
                """
            ).strip()
        )
    if not items:
        items.append(f"<div class='small-note'>{escape(empty_text)}</div>")
    _render_html(f"<div class='insight-list'>{''.join(items)}</div>")


def callout(text: str, level: str = "info") -> None:
    suffix = "" if level == "info" else f" {level}"
    _render_html(f"<div class='callout{suffix}'>{escape(text)}</div>")
