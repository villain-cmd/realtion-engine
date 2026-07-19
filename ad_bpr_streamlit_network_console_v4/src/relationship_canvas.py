from __future__ import annotations

import hashlib
import json
import math
from html import escape
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st

from .network_viz import METRIC_LABELS, NetworkData


ACTION_COLORS = {
    "SCALE_UP": "#3DDC97",
    "SCALE_DOWN": "#FFB454",
    "STOP": "#FF637D",
    "KEEP": "#6EA8FE",
    "OBSERVE": "#A78BFA",
    "NEUTRAL": "#94A3B8",
}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def normalize_action(action: Any, status: Any = "") -> str:
    text = f"{action or ''} {status or ''}".upper()
    if any(token in text for token in ("STOP", "PAUSE", "MIN_BID", "FORCE_STOP", "OUT_OF_STOCK", "TERMINATED")):
        return "STOP"
    if any(token in text for token in ("SCALE_UP", "INCREASE", "RAISE", "UP_BID")):
        return "SCALE_UP"
    if any(token in text for token in ("SCALE_DOWN", "DECREASE", "LOWER", "DOWN_BID")):
        return "SCALE_DOWN"
    if any(token in text for token in ("KEEP", "HOLD", "UNCHANGED")):
        return "KEEP"
    if any(token in text for token in ("OBSERVE", "EXPLORE", "INSUFFICIENT", "REVIEW", "PENDING", "UNKNOWN")):
        return "OBSERVE"
    return "NEUTRAL"


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return _finite(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, float):
        return _finite(value)
    if pd.isna(value):
        return None
    return value


def _refine_layout(nodes: pd.DataFrame, edges: pd.DataFrame) -> pd.DataFrame:
    """Deterministic collision relaxation used by the custom canvas renderer.

    This is deliberately independent from the front-end. NetworkX may provide an
    analytical seed position, while this function creates a readable operational
    layout: products are pulled slightly inward, keywords fan outward, and node
    collisions are resolved before the payload reaches the browser.
    """
    if nodes.empty:
        return nodes
    out = nodes.copy().reset_index(drop=True)
    n = len(out)
    if n == 1:
        out["canvas_x"] = 0.0
        out["canvas_y"] = 0.0
        return out

    base_x = pd.to_numeric(out.get("x", pd.Series(np.arange(n), index=out.index)), errors="coerce").fillna(0.0).to_numpy(float)
    base_y = pd.to_numeric(out.get("y", pd.Series(np.zeros(n), index=out.index)), errors="coerce").fillna(0.0).to_numpy(float)
    pos = np.column_stack([base_x, base_y])
    pos -= pos.mean(axis=0, keepdims=True)
    spread = np.ptp(pos, axis=0)
    spread[spread < 1e-6] = 1.0
    pos = pos / spread * np.array([980.0, 650.0])

    node_type = out.get("node_type", pd.Series("KEYWORD", index=out.index)).astype(str).str.upper().to_numpy()
    communities = pd.to_numeric(out.get("community", pd.Series(0, index=out.index)), errors="coerce").fillna(0).astype(int).to_numpy()
    values = pd.to_numeric(out.get("sales_attr", out.get("value", pd.Series(0, index=out.index))), errors="coerce").fillna(0).clip(lower=0).to_numpy(float)
    vmax = max(float(values.max()), 1.0)
    radii = np.where(node_type == "PRODUCT", 18.0, 8.0) + np.sqrt(values / vmax) * np.where(node_type == "PRODUCT", 34.0, 18.0)

    # Products define the core of each community; keywords are placed farther out.
    for community in np.unique(communities):
        members = np.where(communities == community)[0]
        if len(members) == 0:
            continue
        center = np.average(pos[members], axis=0, weights=np.maximum(values[members], 1.0))
        product_mask = members[node_type[members] == "PRODUCT"]
        keyword_mask = members[node_type[members] == "KEYWORD"]
        if len(product_mask):
            pos[product_mask] = center + (pos[product_mask] - center) * 0.72
        if len(keyword_mask):
            pos[keyword_mask] = center + (pos[keyword_mask] - center) * 1.34

    anchor = pos.copy()
    index = {str(node_id): idx for idx, node_id in enumerate(out["node_id"].astype(str))}
    edge_pairs: list[tuple[int, int, float]] = []
    if not edges.empty and {"source", "target"}.issubset(edges.columns):
        edge_values = pd.to_numeric(edges.get("edge_value", edges.get("similarity", 1.0)), errors="coerce").fillna(0.0)
        emax = max(float(edge_values.max()), 1.0)
        for row_idx, row in edges.reset_index(drop=True).iterrows():
            left = index.get(str(row.get("source", "")))
            right = index.get(str(row.get("target", "")))
            if left is None or right is None:
                continue
            weight = 0.45 + math.log1p(max(float(edge_values.iloc[row_idx]), 0.0)) / math.log1p(emax) * 1.55
            edge_pairs.append((left, right, weight))

    # Vectorised collision pass plus light spring relaxation. Cached by Streamlit.
    for iteration in range(92):
        delta = pos[:, None, :] - pos[None, :, :]
        dist = np.sqrt(np.sum(delta * delta, axis=2)) + np.eye(n)
        min_dist = radii[:, None] + radii[None, :] + 7.0
        overlap = np.clip(min_dist - dist, 0.0, None)
        np.fill_diagonal(overlap, 0.0)
        direction = delta / dist[:, :, None]
        repel = np.sum(direction * overlap[:, :, None], axis=1) * (0.038 if iteration < 55 else 0.022)
        pos += np.clip(repel, -15.0, 15.0)

        if edge_pairs:
            for left, right, weight in edge_pairs:
                vec = pos[right] - pos[left]
                distance = float(np.linalg.norm(vec)) or 1.0
                desired = 102.0 + 52.0 / weight
                pull = (distance - desired) * 0.0048 * weight
                move = vec / distance * pull
                pos[left] += move
                pos[right] -= move

        anchor_strength = 0.017 if iteration < 60 else 0.028
        pos += (anchor - pos) * anchor_strength
        pos -= pos.mean(axis=0, keepdims=True) * 0.03

    # Normalise with a little breathing room. Browser fit-to-view does final scaling.
    pos -= pos.mean(axis=0, keepdims=True)
    max_abs = max(float(np.abs(pos[:, 0]).max()), float(np.abs(pos[:, 1]).max()), 1.0)
    pos /= max_abs
    out["canvas_x"] = pos[:, 0]
    out["canvas_y"] = pos[:, 1]
    return out


def prepare_graph_payload(
    network: NetworkData,
    *,
    metric: str = "sales_attr",
    graph_kind: str = "relationship",
    default_color_mode: str = "action",
    target_roas_pct: float = 450.0,
) -> dict[str, Any]:
    nodes = _refine_layout(network.nodes, network.edges)
    edge_rows = network.edges.copy().reset_index(drop=True)

    payload_nodes: list[dict[str, Any]] = []
    for _, row in nodes.iterrows():
        node_type = str(row.get("node_type", "KEYWORD")).upper()
        product_id = str(row.get("product_id", "") or "")
        keyword = str(row.get("keyword", "") or "")
        full_label = str(row.get("full_label", row.get("label", "")) or "")
        display_label = product_id if node_type == "PRODUCT" and product_id else (keyword or full_label)
        action = normalize_action(row.get("action", ""), row.get("decision_status", ""))
        payload_nodes.append(
            {
                "id": str(row.get("node_id", "")),
                "type": node_type,
                "label": display_label,
                "fullLabel": full_label,
                "productId": product_id,
                "keyword": keyword,
                "category": str(row.get("category", "") or ""),
                "group": str(row.get("product_group", "") or ""),
                "value": _finite(row.get("value", 0.0)),
                "sales": _finite(row.get("sales_attr", row.get("value", 0.0))),
                "clicks": _finite(row.get("clicks", 0.0)),
                "cost": _finite(row.get("cost", 0.0)),
                "orders": _finite(row.get("orders_attr", 0.0)),
                "roas": _finite(row.get("roas_attr_pct", 0.0)),
                "profit": _finite(row.get("expected_incremental_profit", 0.0)),
                "degree": _finite(row.get("weighted_degree", 0.0)),
                "bridge": _finite(row.get("bridge_score", 0.0)),
                "community": int(_finite(row.get("community", 0), 0.0)),
                "productCount": int(_finite(row.get("product_count", 0), 0.0)),
                "action": action,
                "actionRaw": str(row.get("action", "") or ""),
                "status": str(row.get("decision_status", "") or ""),
                "currentBid": _finite(row.get("current_bid", 0.0)),
                "recommendedCpc": _finite(row.get("recommended_cpc", 0.0)),
                "x": _finite(row.get("canvas_x", row.get("x", 0.0))),
                "y": _finite(row.get("canvas_y", row.get("y", 0.0))),
            }
        )

    payload_edges: list[dict[str, Any]] = []
    for _, row in edge_rows.iterrows():
        payload_edges.append(
            {
                "source": str(row.get("source", "")),
                "target": str(row.get("target", "")),
                "value": _finite(row.get("edge_value", row.get("similarity", 1.0)), 1.0),
                "sales": _finite(row.get("sales_attr", row.get("edge_value", 0.0))),
                "clicks": _finite(row.get("clicks", 0.0)),
                "cost": _finite(row.get("cost", 0.0)),
                "orders": _finite(row.get("orders_attr", 0.0)),
                "roas": _finite(row.get("roas_attr_pct", 0.0)),
                "relevance": _finite(row.get("semantic_relevance", row.get("similarity", 0.0))),
                "action": normalize_action(row.get("action", ""), row.get("decision_status", "")),
            }
        )

    return {
        "nodes": payload_nodes,
        "edges": payload_edges,
        "summary": {str(k): _json_safe(v) for k, v in network.summary.items()},
        "metric": metric,
        "metricLabel": METRIC_LABELS.get(metric, metric),
        "graphKind": graph_kind,
        "defaultColorMode": default_color_mode,
        "targetRoasPct": float(target_roas_pct),
        "actionColors": ACTION_COLORS,
    }


def _payload_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("</", "<\\/")


def build_relationship_html(
    network: NetworkData,
    *,
    metric: str = "sales_attr",
    graph_kind: str = "relationship",
    title: str = "商品 × キーワード Relation Map",
    subtitle: str = "ノードサイズ = 売上規模 / 色 = 判定 / 線 = 商品とキーワードの実績接続",
    default_color_mode: str = "action",
    target_roas_pct: float = 450.0,
    height: int = 760,
) -> str:
    payload = prepare_graph_payload(
        network,
        metric=metric,
        graph_kind=graph_kind,
        default_color_mode=default_color_mode,
        target_roas_pct=target_roas_pct,
    )
    data_json = _payload_json(payload)
    component_id = hashlib.sha1(data_json.encode("utf-8")).hexdigest()[:10]
    safe_title = escape(title)
    safe_subtitle = escape(subtitle)
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {{
  --bg:#0a0f17; --bg2:#0f1724; --panel:#111b2a; --panel2:#162234;
  --text:#f5f7fb; --muted:#a9b6c8; --dim:#6f7f94; --line:rgba(158,181,210,.18);
  --blue:#6ea8fe; --cyan:#67d9ff; --green:#3ddc97; --amber:#ffb454; --red:#ff637d;
}}
* {{ box-sizing:border-box; }}
html, body {{ margin:0; height:100%; overflow:hidden; background:transparent; font-family:Inter,"Noto Sans JP","Hiragino Sans","Yu Gothic UI",sans-serif; }}
.graph-shell {{
  position:relative; width:100%; height:{int(height)}px; overflow:hidden; color:var(--text);
  background:
    radial-gradient(circle at 22% 18%, rgba(55,107,164,.18), transparent 32%),
    radial-gradient(circle at 78% 72%, rgba(49,112,129,.12), transparent 34%),
    linear-gradient(145deg,#090e16 0%,#0c1420 48%,#0a1019 100%);
  border:1px solid rgba(124,153,190,.24); border-radius:20px;
  box-shadow:0 22px 55px rgba(5,12,23,.28), inset 0 1px 0 rgba(255,255,255,.035);
}}
.graph-shell::before {{
  content:""; position:absolute; inset:0; pointer-events:none; opacity:.34;
  background-image:linear-gradient(rgba(130,158,190,.055) 1px,transparent 1px),linear-gradient(90deg,rgba(130,158,190,.055) 1px,transparent 1px);
  background-size:38px 38px;
}}
#graphCanvas {{ position:absolute; inset:0; width:100%; height:100%; touch-action:none; cursor:grab; }}
#graphCanvas.grabbing {{ cursor:grabbing; }}
.topbar {{ position:absolute; z-index:5; left:16px; right:16px; top:14px; display:flex; gap:12px; align-items:center; justify-content:space-between; pointer-events:none; }}
.title-block {{ min-width:0; pointer-events:auto; padding:11px 14px; border-radius:13px; background:rgba(13,21,33,.88); border:1px solid rgba(154,180,211,.18); backdrop-filter:blur(14px); box-shadow:0 10px 28px rgba(0,0,0,.22); }}
.title {{ font-size:14px; font-weight:780; letter-spacing:.01em; color:#fff; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.subtitle {{ font-size:10px; color:#b7c3d3; margin-top:3px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.toolbar {{ pointer-events:auto; display:flex; gap:7px; align-items:center; padding:7px; border-radius:14px; background:rgba(13,21,33,.9); border:1px solid rgba(154,180,211,.18); backdrop-filter:blur(14px); box-shadow:0 10px 28px rgba(0,0,0,.22); }}
.search {{ width:min(280px,25vw); height:34px; border-radius:9px; border:1px solid rgba(151,178,210,.24); background:#0b1320; color:#eef4fb; padding:0 34px 0 11px; outline:none; font-size:11px; }}
.search::placeholder {{ color:#76879c; }} .search:focus {{ border-color:#67b7ff; box-shadow:0 0 0 3px rgba(71,153,232,.12); }}
.search-wrap {{ position:relative; }} .search-key {{ position:absolute; right:9px; top:8px; color:#6f8198; font-size:10px; border:1px solid #34465d; border-radius:5px; padding:1px 4px; }}
.tool-btn, .tool-select {{ height:34px; border-radius:9px; border:1px solid rgba(151,178,210,.22); background:#111c2b; color:#dce8f5; font-size:10px; font-weight:700; padding:0 10px; cursor:pointer; outline:none; }}
.tool-btn:hover, .tool-select:hover {{ border-color:#5ba9ec; background:#152337; }} .tool-btn:active {{ transform:translateY(1px); }}
.tool-btn.icon {{ width:34px; padding:0; font-size:14px; }}
.legend {{ position:absolute; z-index:4; left:16px; bottom:16px; display:flex; gap:8px; flex-wrap:wrap; max-width:64%; padding:8px 10px; border-radius:12px; background:rgba(10,17,27,.84); border:1px solid rgba(150,177,207,.16); backdrop-filter:blur(12px); }}
.legend-item {{ display:flex; align-items:center; gap:6px; color:#b8c5d5; font-size:9px; font-weight:680; }}
.legend-dot {{ width:8px; height:8px; border-radius:50%; box-shadow:0 0 0 2px rgba(255,255,255,.08); }}
.legend-ring {{ width:11px; height:11px; border:2px solid #dbe8f6; border-radius:50%; }}
.status {{ position:absolute; z-index:4; right:16px; bottom:16px; display:flex; gap:9px; align-items:center; color:#91a1b5; font-size:9px; padding:7px 10px; border-radius:10px; background:rgba(10,17,27,.78); border:1px solid rgba(150,177,207,.14); }}
.status b {{ color:#e8f0f8; font-weight:760; }}
.inspector {{ position:absolute; z-index:6; top:70px; right:16px; width:300px; max-height:calc(100% - 118px); overflow:auto; padding:15px; border-radius:16px; background:rgba(13,21,33,.95); border:1px solid rgba(160,189,222,.21); box-shadow:0 20px 50px rgba(0,0,0,.34); backdrop-filter:blur(18px); transform:translateX(calc(100% + 30px)); opacity:0; transition:transform .22s ease,opacity .18s ease; }}
.inspector.open {{ transform:translateX(0); opacity:1; }}
.inspector::-webkit-scrollbar {{ width:6px; }} .inspector::-webkit-scrollbar-thumb {{ background:#32445b; border-radius:9px; }}
.inspector-head {{ display:flex; justify-content:space-between; gap:10px; align-items:flex-start; }}
.node-badge {{ display:inline-flex; align-items:center; gap:6px; font-size:9px; font-weight:800; letter-spacing:.08em; color:#a8d8ff; text-transform:uppercase; }}
.node-title {{ color:#fff; font-size:16px; line-height:1.38; font-weight:760; margin:6px 0 3px; word-break:break-word; }}
.node-sub {{ color:#91a2b8; font-size:10px; line-height:1.5; }}
.close-btn {{ border:0; background:transparent; color:#8fa0b5; font-size:19px; cursor:pointer; padding:0 2px; }} .close-btn:hover {{ color:white; }}
.action-chip {{ display:inline-flex; align-items:center; gap:6px; margin-top:10px; padding:6px 9px; border-radius:999px; font-size:9px; font-weight:800; color:#0a1018; }}
.metric-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:13px; }}
.metric {{ padding:10px; border-radius:11px; background:#0c1522; border:1px solid rgba(149,178,211,.14); }}
.metric-label {{ font-size:8px; color:#7f91a8; letter-spacing:.08em; font-weight:760; }} .metric-value {{ color:#f3f7fb; font-size:14px; font-weight:770; margin-top:4px; }}
.inspector-section {{ margin-top:14px; padding-top:13px; border-top:1px solid rgba(154,180,211,.13); }}
.inspector-section h4 {{ margin:0 0 8px; color:#aebdd0; font-size:9px; letter-spacing:.1em; text-transform:uppercase; }}
.neighbor {{ display:flex; align-items:center; justify-content:space-between; gap:10px; padding:8px 9px; margin:5px 0; border-radius:9px; background:#0d1724; border:1px solid rgba(146,174,205,.12); cursor:pointer; }}
.neighbor:hover {{ border-color:#4b92cf; background:#122033; }} .neighbor span {{ min-width:0; color:#dce8f4; font-size:10px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }} .neighbor b {{ color:#7f94ac; font-size:9px; }}
.inspector-actions {{ display:flex; gap:7px; margin-top:13px; }} .inspector-actions button {{ flex:1; height:32px; border-radius:9px; border:1px solid #36526f; background:#122236; color:#dceafa; font-size:9px; font-weight:760; cursor:pointer; }} .inspector-actions button:hover {{ border-color:#67b7ff; }}
.tooltip {{ position:absolute; z-index:8; display:none; pointer-events:none; max-width:300px; padding:10px 11px; border-radius:11px; background:rgba(7,13,21,.96); color:#eff5fb; border:1px solid rgba(157,186,219,.25); box-shadow:0 14px 34px rgba(0,0,0,.34); font-size:10px; line-height:1.55; }}
.tooltip .muted {{ color:#8ea0b5; }}
.empty {{ position:absolute; inset:0; display:none; place-items:center; color:#94a4b7; font-size:13px; }}
.minimap {{ position:absolute; z-index:4; right:16px; top:76px; width:128px; height:82px; border-radius:10px; background:rgba(8,14,23,.8); border:1px solid rgba(145,175,209,.15); pointer-events:none; opacity:.84; }}
.help {{ position:absolute; z-index:4; left:16px; top:76px; padding:7px 9px; border-radius:9px; color:#7f90a6; font-size:9px; background:rgba(10,17,27,.68); border:1px solid rgba(145,175,209,.1); }}
@media(max-width:900px) {{ .title-block{{display:none}} .toolbar{{width:100%;overflow-x:auto}} .search{{width:200px}} .inspector{{width:min(300px,calc(100% - 32px))}} .minimap{{display:none}} .help{{display:none}} }}
</style>
</head>
<body>
<div class="graph-shell" id="shell-{component_id}">
  <canvas id="graphCanvas-{component_id}"></canvas>
  <canvas class="minimap" id="miniMap-{component_id}" width="256" height="164"></canvas>
  <div class="topbar">
    <div class="title-block"><div class="title">{safe_title}</div><div class="subtitle">{safe_subtitle}</div></div>
    <div class="toolbar">
      <div class="search-wrap"><input class="search" id="search-{component_id}" placeholder="商品管理番号 / キーワードを検索"><span class="search-key">/</span></div>
      <select class="tool-select" id="colorMode-{component_id}" aria-label="色分け"><option value="action">色：判定</option><option value="roas">色：ROAS</option><option value="community">色：クラスタ</option><option value="entity">色：種別</option></select>
      <select class="tool-select" id="labelMode-{component_id}" aria-label="ラベル"><option value="auto">ラベル：自動</option><option value="dense">ラベル：多め</option><option value="focus">ラベル：選択のみ</option></select>
      <button class="tool-btn" id="showAll-{component_id}" title="絞り込み解除">全体</button>
      <button class="tool-btn icon" id="fit-{component_id}" title="全体を表示">⌖</button>
      <button class="tool-btn icon" id="export-{component_id}" title="PNG保存">⇩</button>
      <button class="tool-btn icon" id="fullscreen-{component_id}" title="全画面">⛶</button>
    </div>
  </div>
  <div class="help">ホイール：ズーム　ドラッグ：移動　クリック：固定　ダブルクリック：1階層フォーカス</div>
  <div class="legend" id="legend-{component_id}"></div>
  <div class="status" id="status-{component_id}"></div>
  <aside class="inspector" id="inspector-{component_id}"></aside>
  <div class="tooltip" id="tooltip-{component_id}"></div>
  <div class="empty" id="empty-{component_id}">表示できる関係データがありません</div>
</div>
<script>
(() => {{
'use strict';
const DATA = {data_json};
const shell = document.getElementById('shell-{component_id}');
const canvas = document.getElementById('graphCanvas-{component_id}');
const ctx = canvas.getContext('2d');
const mini = document.getElementById('miniMap-{component_id}');
const mctx = mini.getContext('2d');
const inspector = document.getElementById('inspector-{component_id}');
const tooltip = document.getElementById('tooltip-{component_id}');
const statusEl = document.getElementById('status-{component_id}');
const legendEl = document.getElementById('legend-{component_id}');
const emptyEl = document.getElementById('empty-{component_id}');
const searchEl = document.getElementById('search-{component_id}');
const colorModeEl = document.getElementById('colorMode-{component_id}');
const labelModeEl = document.getElementById('labelMode-{component_id}');
colorModeEl.value = DATA.defaultColorMode || 'action';

const ACTION_COLORS = DATA.actionColors;
const CLUSTER_COLORS = ['#6EA8FE','#3DDC97','#A78BFA','#FFB454','#FF637D','#67D9FF','#F28ACB','#8BD17C','#FFD166','#6CCFF6','#B8A1FF','#F39C6B','#64D8CB','#88A7D9'];
const nodes = DATA.nodes.map((n,i) => ({{...n,index:i,vx:0,vy:0,r:8,screenX:0,screenY:0}}));
const nodeById = new Map(nodes.map(n => [n.id,n]));
const edges = DATA.edges.map((e,i) => ({{...e,index:i,sourceNode:nodeById.get(e.source),targetNode:nodeById.get(e.target)}})).filter(e => e.sourceNode && e.targetNode);
const adjacency = new Map(nodes.map(n => [n.id,[]]));
edges.forEach(e => {{ adjacency.get(e.source).push({{node:e.targetNode,edge:e}}); adjacency.get(e.target).push({{node:e.sourceNode,edge:e}}); }});
const maxSales = Math.max(1,...nodes.map(n => n.sales || n.value || 0));
const maxEdge = Math.max(1,...edges.map(e => e.sales || e.value || 0));

const state = {{scale:1,fitScale:1,offsetX:0,offsetY:0,hover:null,selected:null,focus:null,search:'',searchSet:null,colorMode:colorModeEl.value,labelMode:'auto',dragNode:null,panning:false,lastX:0,lastY:0,moved:false}};
let dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
let width = 0, heightPx = 0;
let raf = null;

const fmtYen = v => '¥' + Math.round(v || 0).toLocaleString('ja-JP');
const fmtNum = v => Math.round(v || 0).toLocaleString('ja-JP');
const fmtPct = v => (v || 0).toLocaleString('ja-JP',{{maximumFractionDigits:1}}) + '%';
const esc = s => String(s ?? '').replace(/[&<>"']/g,c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}}[c]));
const clamp = (v,a,b) => Math.max(a,Math.min(b,v));

function resize() {{
  const rect = shell.getBoundingClientRect(); width = Math.max(320,rect.width); heightPx = Math.max(360,rect.height);
  canvas.width = Math.floor(width*dpr); canvas.height = Math.floor(heightPx*dpr); canvas.style.width=width+'px'; canvas.style.height=heightPx+'px';
  ctx.setTransform(dpr,0,0,dpr,0,0); fitGraph(false); schedule();
}}
function worldToScreen(x,y) {{ return {{x:x*state.scale+state.offsetX,y:y*state.scale+state.offsetY}}; }}
function screenToWorld(x,y) {{ return {{x:(x-state.offsetX)/state.scale,y:(y-state.offsetY)/state.scale}}; }}
function visibleNodes() {{
  return nodes.filter(n => {{
    if (state.focus && !state.focus.has(n.id)) return false;
    if (state.searchSet && !state.searchSet.has(n.id)) return false;
    return true;
  }});
}}
function searchable(n) {{ return (n.label+' '+n.fullLabel+' '+n.productId+' '+n.keyword+' '+n.category+' '+n.group).toLowerCase(); }}
function buildSearchSet(query) {{
  if(!query) return null;
  const matches=nodes.filter(n=>searchable(n).includes(query));
  const set=new Set(matches.map(n=>n.id));
  matches.forEach(n=>(adjacency.get(n.id)||[]).forEach(x=>set.add(x.node.id)));
  return set;
}}
function fitGraph(animate=true) {{
  const visible = visibleNodes(); if (!visible.length) return;
  let minX=Infinity,maxX=-Infinity,minY=Infinity,maxY=-Infinity;
  visible.forEach(n => {{minX=Math.min(minX,n.x);maxX=Math.max(maxX,n.x);minY=Math.min(minY,n.y);maxY=Math.max(maxY,n.y);}});
  const spanX=Math.max(.3,maxX-minX), spanY=Math.max(.3,maxY-minY); const pad=90;
  const targetScale=Math.min((width-pad*2)/spanX,(heightPx-pad*2)/spanY);
  const targetX=width/2-((minX+maxX)/2)*targetScale; const targetY=heightPx/2-((minY+maxY)/2)*targetScale;
  state.fitScale=targetScale;
  if (!animate) {{state.scale=targetScale;state.offsetX=targetX;state.offsetY=targetY; return;}}
  const start={{s:state.scale,x:state.offsetX,y:state.offsetY,t:performance.now()}};
  const tick=now=>{{const p=clamp((now-start.t)/260,0,1);const e=1-Math.pow(1-p,3);state.scale=start.s+(targetScale-start.s)*e;state.offsetX=start.x+(targetX-start.x)*e;state.offsetY=start.y+(targetY-start.y)*e;schedule();if(p<1)requestAnimationFrame(tick);}}; requestAnimationFrame(tick);
}}
function nodeRadius(n) {{
  const base=n.type==='PRODUCT'?10:5.2; const gain=n.type==='PRODUCT'?31:14;
  return base+Math.sqrt(Math.max(0,n.sales||n.value||0)/maxSales)*gain;
}}
function roasColor(v) {{
  const t=clamp((v||0)/Math.max(DATA.targetRoasPct||450,1),0,2);
  if (t<.55) return '#FF637D'; if (t<.9) return '#FFB454'; if (t<1.2) return '#6EA8FE'; return '#3DDC97';
}}
function colorFor(n) {{
  if(state.colorMode==='roas') return roasColor(n.roas);
  if(state.colorMode==='community') return CLUSTER_COLORS[Math.abs(n.community||0)%CLUSTER_COLORS.length];
  if(state.colorMode==='entity') return n.type==='PRODUCT'?'#E8F0FA':'#67C7F3';
  return ACTION_COLORS[n.action] || ACTION_COLORS.NEUTRAL;
}}
function edgeVisible(e) {{
  return (!state.focus || (state.focus.has(e.source)&&state.focus.has(e.target)))
    && (!state.searchSet || (state.searchSet.has(e.source)&&state.searchSet.has(e.target)));
}}
function isNeighbor(a,b) {{ return adjacency.get(a.id).some(x=>x.node.id===b.id); }}
function activeSet() {{
  const active=state.selected||state.hover; if(!active) return null; const set=new Set([active.id]); adjacency.get(active.id).forEach(x=>set.add(x.node.id)); return set;
}}
function draw() {{
  ctx.clearRect(0,0,width,heightPx);
  ctx.fillStyle='#0A1019'; ctx.fillRect(0,0,width,heightPx);
  ctx.save(); ctx.strokeStyle='rgba(132,159,191,.045)'; ctx.lineWidth=1;
  const grid=38; const gx=((state.offsetX%grid)+grid)%grid, gy=((state.offsetY%grid)+grid)%grid;
  for(let x=gx;x<width;x+=grid){{ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,heightPx);ctx.stroke();}}
  for(let y=gy;y<heightPx;y+=grid){{ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(width,y);ctx.stroke();}} ctx.restore();
  if(!nodes.length){{emptyEl.style.display='grid';return;}} emptyEl.style.display='none';
  const active=activeSet();
  // edges
  edges.forEach(e=>{{if(!edgeVisible(e))return;const a=worldToScreen(e.sourceNode.x,e.sourceNode.y),b=worldToScreen(e.targetNode.x,e.targetNode.y);const highlighted=active&&active.has(e.source)&&active.has(e.target);const dim=active&&!highlighted;const value=e.sales||e.value||0;const w=.45+Math.log1p(value)/Math.log1p(maxEdge)*2.0;ctx.beginPath();const mx=(a.x+b.x)/2,my=(a.y+b.y)/2;const dx=b.x-a.x,dy=b.y-a.y;const bend=Math.min(34,Math.hypot(dx,dy)*.07);const nx=-dy/(Math.hypot(dx,dy)||1),ny=dx/(Math.hypot(dx,dy)||1);ctx.moveTo(a.x,a.y);ctx.quadraticCurveTo(mx+nx*bend,my+ny*bend,b.x,b.y);ctx.strokeStyle=highlighted?'rgba(112,201,255,.82)':dim?'rgba(115,139,166,.035)':'rgba(103,143,181,.23)';ctx.lineWidth=highlighted?Math.max(1.6,w*1.4):w;ctx.stroke();}});
  // nodes
  const visible=visibleNodes(); visible.forEach(n=>{{const p=worldToScreen(n.x,n.y);n.screenX=p.x;n.screenY=p.y;n.r=nodeRadius(n);const selected=state.selected===n,hovered=state.hover===n;const dim=active&&!active.has(n.id);ctx.save();ctx.globalAlpha=dim?.14:1; if(selected||hovered){{ctx.beginPath();ctx.arc(p.x,p.y,n.r+7,0,Math.PI*2);ctx.fillStyle=selected?'rgba(103,183,255,.18)':'rgba(255,255,255,.10)';ctx.fill();}}ctx.beginPath();ctx.arc(p.x,p.y,n.r,0,Math.PI*2);ctx.fillStyle=colorFor(n);ctx.shadowColor=selected||hovered?colorFor(n):'transparent';ctx.shadowBlur=selected||hovered?18:0;ctx.fill();ctx.shadowBlur=0;ctx.lineWidth=n.type==='PRODUCT'?2.4:1.25;ctx.strokeStyle=n.type==='PRODUCT'?'rgba(245,249,255,.92)':'rgba(8,15,24,.92)';ctx.stroke();if(n.type==='PRODUCT'){{ctx.beginPath();ctx.arc(p.x,p.y,Math.max(2,n.r-5),0,Math.PI*2);ctx.strokeStyle='rgba(7,14,23,.42)';ctx.lineWidth=1;ctx.stroke();}}ctx.restore();}});
  drawLabels(visible,active); drawMiniMap(); updateStatus();
}}
function drawLabels(visible,active) {{
  const occupied=[]; const candidates=[]; visible.forEach(n=>{{let priority=0;if(state.selected===n)priority=1000;else if(state.hover===n)priority=900;else if(active&&active.has(n.id))priority=800;else if(n.type==='PRODUCT')priority=400+(n.sales/maxSales)*200;else priority=100+(n.sales/maxSales)*100;if(state.labelMode==='focus'&&!active&&state.selected!==n&&state.hover!==n)return;candidates.push({{n,priority}});}});candidates.sort((a,b)=>b.priority-a.priority);
  const limit=state.labelMode==='dense'?58:state.labelMode==='focus'?80:28;let count=0;ctx.font='600 11px Inter,"Noto Sans JP",sans-serif';ctx.textBaseline='middle';
  for(const item of candidates){{if(count>=limit)break;const n=item.n;const label=n.label||'';if(!label)continue;const p={{x:n.screenX,y:n.screenY}};const text=label.length>22?label.slice(0,21)+'…':label;const w=ctx.measureText(text).width+12,h=19;const spots=[{{x:p.x-w/2,y:p.y-n.r-h-5}},{{x:p.x-w/2,y:p.y+n.r+5}},{{x:p.x+n.r+6,y:p.y-h/2}},{{x:p.x-n.r-w-6,y:p.y-h/2}}];let box=null;for(const s of spots){{const candidate={{x:s.x,y:s.y,w,h}};if(candidate.x<4||candidate.y<62||candidate.x+candidate.w>width-4||candidate.y+candidate.h>heightPx-5)continue;if(!occupied.some(o=>candidate.x<o.x+o.w+4&&candidate.x+candidate.w+4>o.x&&candidate.y<o.y+o.h+3&&candidate.y+candidate.h+3>o.y)){{box=candidate;break;}}}}if(!box)continue;occupied.push(box);const strong=item.priority>=800||n.type==='PRODUCT';if(strong){{ctx.fillStyle='rgba(9,16,26,.84)';roundRect(ctx,box.x,box.y,box.w,box.h,6);ctx.fill();ctx.strokeStyle='rgba(159,189,220,.15)';ctx.lineWidth=1;ctx.stroke();}}ctx.fillStyle=strong?'#F3F7FB':'#B7C4D3';ctx.fillText(text,box.x+6,box.y+h/2+.5);count++;}}
}}
function roundRect(c,x,y,w,h,r){{const rr=Math.min(r,w/2,h/2);c.beginPath();c.moveTo(x+rr,y);c.arcTo(x+w,y,x+w,y+h,rr);c.arcTo(x+w,y+h,x,y+h,rr);c.arcTo(x,y+h,x,y,rr);c.arcTo(x,y,x+w,y,rr);c.closePath();}}
function drawMiniMap() {{
  const W=mini.width,H=mini.height;mctx.clearRect(0,0,W,H);if(!nodes.length)return;const xs=nodes.map(n=>n.x),ys=nodes.map(n=>n.y);const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);const sx=(W-20)/Math.max(.1,maxX-minX),sy=(H-20)/Math.max(.1,maxY-minY);nodes.forEach(n=>{{const x=10+(n.x-minX)*sx,y=10+(n.y-minY)*sy;mctx.beginPath();mctx.arc(x,y,n.type==='PRODUCT'?3.3:1.7,0,Math.PI*2);mctx.fillStyle=colorFor(n);mctx.globalAlpha=.78;mctx.fill();}});mctx.globalAlpha=1;const tl=screenToWorld(0,0),br=screenToWorld(width,heightPx);const vx=10+(tl.x-minX)*sx,vy=10+(tl.y-minY)*sy,vw=(br.x-tl.x)*sx,vh=(br.y-tl.y)*sy;mctx.strokeStyle='rgba(255,255,255,.5)';mctx.lineWidth=1.5;mctx.strokeRect(vx,vy,vw,vh);
}}
function updateStatus() {{
  const visible=visibleNodes(); const visibleIds=new Set(visible.map(n=>n.id)); const linkCount=edges.filter(e=>visibleIds.has(e.source)&&visibleIds.has(e.target)).length;statusEl.innerHTML=`<span><b>${{visible.length}}</b> nodes</span><span><b>${{linkCount}}</b> links</span><span><b>${{Math.round((state.scale/Math.max(state.fitScale,1e-9))*100)}}%</b></span>`;
}}
function schedule() {{ if(raf)return; raf=requestAnimationFrame(()=>{{raf=null;draw();}}); }}
function hitTest(x,y) {{
  let best=null,bestD=Infinity;for(const n of visibleNodes()){{const p=worldToScreen(n.x,n.y),d=Math.hypot(x-p.x,y-p.y);if(d<=nodeRadius(n)+5&&d<bestD){{best=n;bestD=d;}}}}return best;
}}
function showTooltip(n,x,y) {{
  if(!n){{tooltip.style.display='none';return;}}const type=n.type==='PRODUCT'?'商品':'キーワード';tooltip.innerHTML=`<b>${{esc(n.label)}}</b><br><span class="muted">${{type}}${{n.fullLabel&&n.fullLabel!==n.label?' / '+esc(n.fullLabel):''}}</span><br>売上 ${{fmtYen(n.sales)}}　ROAS ${{fmtPct(n.roas)}}<br>クリック ${{fmtNum(n.clicks)}}　広告費 ${{fmtYen(n.cost)}}`;tooltip.style.display='block';const tw=tooltip.offsetWidth,th=tooltip.offsetHeight;tooltip.style.left=clamp(x+14,8,width-tw-8)+'px';tooltip.style.top=clamp(y+14,62,heightPx-th-8)+'px';
}}
function openInspector(n) {{
  if(!n){{inspector.classList.remove('open');inspector.innerHTML='';return;}}const color=colorFor(n),type=n.type==='PRODUCT'?'PRODUCT / 商品管理番号':'KEYWORD / 検索語';const neighbors=(adjacency.get(n.id)||[]).slice().sort((a,b)=>(b.edge.sales||b.edge.value||0)-(a.edge.sales||a.edge.value||0)).slice(0,12);inspector.innerHTML=`<div class="inspector-head"><div><div class="node-badge"><span class="legend-dot" style="background:${{color}}"></span>${{type}}</div><div class="node-title">${{esc(n.label)}}</div><div class="node-sub">${{esc(n.fullLabel||n.category||n.group)}}</div></div><button class="close-btn" id="closeInspector">×</button></div><div class="action-chip" style="background:${{ACTION_COLORS[n.action]||ACTION_COLORS.NEUTRAL}}">${{esc(n.action)}}</div><div class="metric-grid"><div class="metric"><div class="metric-label">ATTR. SALES</div><div class="metric-value">${{fmtYen(n.sales)}}</div></div><div class="metric"><div class="metric-label">ROAS</div><div class="metric-value">${{fmtPct(n.roas)}}</div></div><div class="metric"><div class="metric-label">AD SPEND</div><div class="metric-value">${{fmtYen(n.cost)}}</div></div><div class="metric"><div class="metric-label">CLICKS</div><div class="metric-value">${{fmtNum(n.clicks)}}</div></div></div><div class="inspector-section"><h4>CONNECTED NODES / ${{neighbors.length}}</h4>${{neighbors.map(x=>`<div class="neighbor" data-node="${{esc(x.node.id)}}"><span>${{esc(x.node.label)}}</span><b>${{fmtYen(x.edge.sales||x.edge.value||0)}}</b></div>`).join('')||'<div class="node-sub">接続ノードなし</div>'}}</div><div class="inspector-actions"><button id="focusNode">1階層だけ表示</button><button id="centerNode">中央へ</button></div>`;inspector.classList.add('open');document.getElementById('closeInspector').onclick=()=>{{state.selected=null;state.focus=null;openInspector(null);schedule();}};document.getElementById('focusNode').onclick=()=>focusNode(n);document.getElementById('centerNode').onclick=()=>centerNode(n);inspector.querySelectorAll('.neighbor').forEach(el=>el.onclick=()=>{{const next=nodeById.get(el.dataset.node);if(next)selectNode(next);}});
}}
function selectNode(n) {{state.selected=n;state.hover=null;openInspector(n);schedule();}}
function focusNode(n) {{const set=new Set([n.id]);(adjacency.get(n.id)||[]).forEach(x=>set.add(x.node.id));state.focus=set;fitGraph(true);schedule();}}
function centerNode(n) {{state.offsetX=width/2-n.x*state.scale;state.offsetY=heightPx/2-n.y*state.scale;schedule();}}
function clearFilters() {{state.focus=null;state.search='';state.searchSet=null;searchEl.value='';state.selected=null;openInspector(null);fitGraph(true);schedule();}}
function buildLegend() {{
  if(state.colorMode==='action'){{legendEl.innerHTML=`<span class="legend-item"><span class="legend-ring"></span>商品</span><span class="legend-item"><span class="legend-dot" style="background:${{ACTION_COLORS.SCALE_UP}}"></span>増額</span><span class="legend-item"><span class="legend-dot" style="background:${{ACTION_COLORS.SCALE_DOWN}}"></span>減額</span><span class="legend-item"><span class="legend-dot" style="background:${{ACTION_COLORS.STOP}}"></span>停止</span><span class="legend-item"><span class="legend-dot" style="background:${{ACTION_COLORS.KEEP}}"></span>維持</span><span class="legend-item"><span class="legend-dot" style="background:${{ACTION_COLORS.OBSERVE}}"></span>観測</span><span class="legend-item">円サイズ = 売上</span>`;}}
  else if(state.colorMode==='roas'){{legendEl.innerHTML='<span class="legend-item"><span class="legend-dot" style="background:#FF637D"></span>低ROAS</span><span class="legend-item"><span class="legend-dot" style="background:#FFB454"></span>要改善</span><span class="legend-item"><span class="legend-dot" style="background:#6EA8FE"></span>目標付近</span><span class="legend-item"><span class="legend-dot" style="background:#3DDC97"></span>高ROAS</span><span class="legend-item">円サイズ = 売上</span>';}}
  else if(state.colorMode==='entity'){{legendEl.innerHTML='<span class="legend-item"><span class="legend-ring"></span>商品管理番号</span><span class="legend-item"><span class="legend-dot" style="background:#67C7F3"></span>キーワード</span><span class="legend-item">円サイズ = 売上</span>';}}
  else {{legendEl.innerHTML='<span class="legend-item">色 = 関係クラスタ</span><span class="legend-item">円サイズ = 売上</span>';}}
}}

canvas.addEventListener('pointerdown',e=>{{canvas.setPointerCapture(e.pointerId);const rect=canvas.getBoundingClientRect(),x=e.clientX-rect.left,y=e.clientY-rect.top;const hit=hitTest(x,y);state.lastX=x;state.lastY=y;state.moved=false;if(hit){{state.dragNode=hit;}}else{{state.panning=true;canvas.classList.add('grabbing');}}}});
canvas.addEventListener('pointermove',e=>{{const rect=canvas.getBoundingClientRect(),x=e.clientX-rect.left,y=e.clientY-rect.top;const dx=x-state.lastX,dy=y-state.lastY;if(Math.abs(dx)+Math.abs(dy)>2)state.moved=true;if(state.dragNode){{const w=screenToWorld(x,y);state.dragNode.x=w.x;state.dragNode.y=w.y;schedule();}}else if(state.panning){{state.offsetX+=dx;state.offsetY+=dy;schedule();}}else{{const hit=hitTest(x,y);if(hit!==state.hover){{state.hover=hit;schedule();}}showTooltip(hit,x,y);canvas.style.cursor=hit?'pointer':'grab';}}state.lastX=x;state.lastY=y;}});
canvas.addEventListener('pointerup',e=>{{const rect=canvas.getBoundingClientRect(),x=e.clientX-rect.left,y=e.clientY-rect.top;const hit=hitTest(x,y);if(!state.moved&&hit)selectNode(hit);else if(!state.moved&&!hit){{state.selected=null;openInspector(null);schedule();}}state.dragNode=null;state.panning=false;canvas.classList.remove('grabbing');}});
canvas.addEventListener('pointerleave',()=>{{state.hover=null;tooltip.style.display='none';schedule();}});
canvas.addEventListener('dblclick',e=>{{const rect=canvas.getBoundingClientRect(),hit=hitTest(e.clientX-rect.left,e.clientY-rect.top);if(hit)focusNode(hit);}});
canvas.addEventListener('wheel',e=>{{e.preventDefault();const rect=canvas.getBoundingClientRect(),mx=e.clientX-rect.left,my=e.clientY-rect.top,before=screenToWorld(mx,my);const factor=Math.exp(-e.deltaY*.0012);const minScale=Math.max(5,state.fitScale*.18),maxScale=Math.max(minScale*2,state.fitScale*12);state.scale=clamp(state.scale*factor,minScale,maxScale);state.offsetX=mx-before.x*state.scale;state.offsetY=my-before.y*state.scale;schedule();}},{{passive:false}});
searchEl.addEventListener('input',()=>{{state.search=searchEl.value.trim().toLowerCase();state.searchSet=buildSearchSet(state.search);state.focus=null;fitGraph(true);schedule();}});
searchEl.addEventListener('keydown',e=>{{if(e.key==='Enter'){{const found=visibleNodes()[0];if(found)selectNode(found);}}if(e.key==='Escape')clearFilters();}});
colorModeEl.addEventListener('change',()=>{{state.colorMode=colorModeEl.value;buildLegend();schedule();}});
labelModeEl.addEventListener('change',()=>{{state.labelMode=labelModeEl.value;schedule();}});
document.getElementById('showAll-{component_id}').onclick=clearFilters;
document.getElementById('fit-{component_id}').onclick=()=>fitGraph(true);
document.getElementById('export-{component_id}').onclick=()=>{{const link=document.createElement('a');link.download='relationship_map.png';link.href=canvas.toDataURL('image/png');link.click();}};
document.getElementById('fullscreen-{component_id}').onclick=()=>{{if(!document.fullscreenElement)shell.requestFullscreen?.();else document.exitFullscreen?.();}};
window.addEventListener('keydown',e=>{{if(e.key==='/'&&document.activeElement!==searchEl){{e.preventDefault();searchEl.focus();}}if(e.key==='Escape'&&document.activeElement!==searchEl)clearFilters();if(e.key.toLowerCase()==='f'&&document.activeElement!==searchEl)fitGraph(true);}});
new ResizeObserver(resize).observe(shell); buildLegend(); resize();
}})();
</script>
</body>
</html>"""


def render_relationship_canvas(
    network: NetworkData,
    *,
    metric: str = "sales_attr",
    graph_kind: str = "relationship",
    title: str = "商品 × キーワード Relation Map",
    subtitle: str = "ノードサイズ = 売上規模 / 色 = 判定 / 線 = 商品とキーワードの実績接続",
    default_color_mode: str = "action",
    target_roas_pct: float = 450.0,
    height: int = 760,
) -> None:
    html = build_relationship_html(
        network,
        metric=metric,
        graph_kind=graph_kind,
        title=title,
        subtitle=subtitle,
        default_color_mode=default_color_mode,
        target_roas_pct=target_roas_pct,
        height=height,
    )
    st.iframe(html, width="stretch", height=height + 2)
