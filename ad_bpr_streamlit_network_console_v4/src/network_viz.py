from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

import networkx as nx
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from .text_mining import (
    build_term_cooccurrence,
    build_term_frequency,
    keyword_similarity_edges,
    normalize_phrase,
    paired_text_similarity,
)


NAVY = "#0B1220"
INK = "#182235"
BLUE = "#3B82F6"
SKY = "#79BCE8"
CYAN = "#18A6A6"
LIME = "#97C95C"
AMBER = "#E7A43B"
ROSE = "#D8667B"
MUTED = "#6E7B8F"
GRID = "#DDE6F0"
PANEL = "#FFFFFF"
BG = "#F3F6FA"

COMMUNITY_COLORS = [
    "#3B82F6", "#18A6A6", "#8B6FC8", "#E7A43B", "#D8667B", "#5D8F62",
    "#5A7D9A", "#9B7B50", "#7568B4", "#4C9F9A", "#C86F4A", "#6A8CC7",
]

METRIC_LABELS = {
    "sales_attr": "属性売上",
    "clicks": "クリック",
    "cost": "広告費",
    "orders_attr": "注文",
    "expected_incremental_profit": "期待粗利増分",
    "roas_attr_pct": "ROAS",
}


@dataclass
class NetworkData:
    nodes: pd.DataFrame
    edges: pd.DataFrame
    graph: nx.Graph
    summary: dict[str, float | int | str]



def _numeric(df: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    if column not in df:
        return pd.Series(default, index=df.index, dtype=float)
    return pd.to_numeric(df[column], errors="coerce").fillna(default)



def _safe_label(value: object, limit: int = 26) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"



def _community_map(graph: nx.Graph) -> dict[str, int]:
    if graph.number_of_nodes() == 0:
        return {}
    if graph.number_of_edges() == 0:
        return {str(node): i for i, node in enumerate(graph.nodes())}
    try:
        communities = list(nx.algorithms.community.greedy_modularity_communities(graph, weight="layout_weight"))
    except Exception:
        communities = [set(c) for c in nx.connected_components(graph)]
    mapping: dict[str, int] = {}
    for idx, members in enumerate(communities):
        for node in members:
            mapping[str(node)] = idx
    for node in graph.nodes():
        mapping.setdefault(str(node), len(mapping))
    return mapping



def _centrality(graph: nx.Graph) -> tuple[dict[str, float], dict[str, float]]:
    if graph.number_of_nodes() == 0:
        return {}, {}
    degree = {
        str(node): float(sum(float(data.get("layout_weight", 1.0)) for _, _, data in graph.edges(node, data=True)))
        for node in graph.nodes()
    }
    if graph.number_of_edges() == 0:
        return degree, {str(node): 0.0 for node in graph.nodes()}
    working = graph.copy()
    for left, right, data in working.edges(data=True):
        weight = max(float(data.get("layout_weight", 1.0)), 1e-9)
        data["distance"] = 1.0 / weight
    # Exact betweenness is useful at dashboard scale; use sampling for larger graphs.
    k = None if working.number_of_nodes() <= 180 else min(80, working.number_of_nodes())
    try:
        between = nx.betweenness_centrality(working, k=k, weight="distance", normalized=True, seed=17)
    except Exception:
        between = {node: 0.0 for node in working.nodes()}
    return degree, {str(k): float(v) for k, v in between.items()}



def _layout_graph(graph: nx.Graph, seed: int = 17) -> dict[str, np.ndarray]:
    if graph.number_of_nodes() == 0:
        return {}
    if graph.number_of_nodes() == 1:
        node = next(iter(graph.nodes()))
        return {str(node): np.array([0.0, 0.0])}
    n = graph.number_of_nodes()
    k = 1.55 / math.sqrt(max(n, 2))
    iterations = 160 if n <= 180 else 95
    try:
        positions = nx.spring_layout(
            graph,
            seed=seed,
            k=k,
            iterations=iterations,
            weight="layout_weight",
            threshold=1e-4,
            scale=1.0,
        )
    except Exception:
        positions = nx.kamada_kawai_layout(graph, weight="distance")
    return {str(node): np.asarray(pos, dtype=float) for node, pos in positions.items()}



def _network_summary(nodes: pd.DataFrame, edges: pd.DataFrame, graph: nx.Graph, metric: str) -> dict[str, float | int | str]:
    if nodes.empty:
        return {
            "products": 0, "keywords": 0, "edges": 0, "clusters": 0, "density": 0.0,
            "top10_share_pct": 0.0, "largest_cluster_share_pct": 0.0, "metric": METRIC_LABELS.get(metric, metric),
        }
    products = int((nodes["node_type"] == "PRODUCT").sum())
    keywords = int((nodes["node_type"] == "KEYWORD").sum())
    clusters = int(nodes["community"].nunique()) if "community" in nodes else 0
    total_value = float(nodes.loc[nodes["node_type"] == "PRODUCT", "value"].sum())
    top10 = float(nodes.loc[nodes["node_type"] == "PRODUCT"].nlargest(10, "value")["value"].sum())
    community_value = nodes.groupby("community")["value"].sum() if "community" in nodes else pd.Series(dtype=float)
    largest = float(community_value.max()) if not community_value.empty else 0.0
    return {
        "products": products,
        "keywords": keywords,
        "edges": int(len(edges)),
        "clusters": clusters,
        "density": float(nx.density(graph)) if graph.number_of_nodes() > 1 else 0.0,
        "top10_share_pct": top10 / total_value * 100.0 if total_value > 0 else 0.0,
        "largest_cluster_share_pct": largest / float(nodes["value"].sum()) * 100.0 if float(nodes["value"].sum()) > 0 else 0.0,
        "metric": METRIC_LABELS.get(metric, metric),
    }



def build_product_keyword_network(
    decisions: pd.DataFrame,
    *,
    metric: str = "sales_attr",
    max_products: int = 50,
    max_keywords: int = 110,
    min_edge_value: float = 0.0,
    selected_products: Iterable[str] | None = None,
) -> NetworkData:
    if decisions.empty:
        return NetworkData(pd.DataFrame(), pd.DataFrame(), nx.Graph(), _network_summary(pd.DataFrame(), pd.DataFrame(), nx.Graph(), metric))
    work = decisions.copy()
    kw = work[work.get("entity_type", "").astype(str).str.upper().eq("KEYWORD")].copy()
    if kw.empty:
        return NetworkData(pd.DataFrame(), pd.DataFrame(), nx.Graph(), _network_summary(pd.DataFrame(), pd.DataFrame(), nx.Graph(), metric))

    kw["product_id"] = kw["product_id"].fillna("").astype(str).str.strip()
    kw["keyword_display"] = kw["keyword"].fillna("").astype(str).str.strip()
    kw["keyword_normalized"] = kw["keyword_display"].map(normalize_phrase)
    kw[metric] = _numeric(kw, metric)
    for col in ["clicks", "cost", "sales_attr", "orders_attr", "roas_attr_pct", "expected_incremental_profit"]:
        kw[col] = _numeric(kw, col)
    kw = kw[kw["product_id"].ne("") & kw["keyword_normalized"].ne("")]
    if selected_products:
        selected = {str(x) for x in selected_products}
        kw = kw[kw["product_id"].isin(selected)]
    if kw.empty:
        return NetworkData(pd.DataFrame(), pd.DataFrame(), nx.Graph(), _network_summary(pd.DataFrame(), pd.DataFrame(), nx.Graph(), metric))

    agg_spec = {
        metric: "sum",
        "clicks": "sum",
        "cost": "sum",
        "sales_attr": "sum",
        "orders_attr": "sum",
        "roas_attr_pct": "mean",
        "expected_incremental_profit": "sum",
    }
    agg_spec = {k: v for k, v in agg_spec.items() if k in kw.columns}
    first_cols = [c for c in ["keyword_display", "product_name", "category", "product_group", "decision_status", "action"] if c in kw.columns]
    for c in first_cols:
        agg_spec[c] = "first"
    edges = kw.groupby(["product_id", "keyword_normalized"], as_index=False).agg(agg_spec)
    if metric not in edges:
        edges[metric] = 1.0
    edges["edge_value"] = _numeric(edges, metric)
    if edges["edge_value"].sum() <= 0:
        edges["edge_value"] = _numeric(edges, "clicks", 1.0).clip(lower=1.0)
    edges = edges[edges["edge_value"] >= float(min_edge_value)]

    product_rank = edges.groupby("product_id")["edge_value"].sum().nlargest(max_products).index
    keyword_rank = edges.groupby("keyword_normalized")["edge_value"].sum().nlargest(max_keywords).index
    edges = edges[edges["product_id"].isin(product_rank) & edges["keyword_normalized"].isin(keyword_rank)].copy()
    if edges.empty:
        return NetworkData(pd.DataFrame(), pd.DataFrame(), nx.Graph(), _network_summary(pd.DataFrame(), pd.DataFrame(), nx.Graph(), metric))

    # Product labels are sourced from the product master first, then the current setting file.
    product_name = edges.get("product_name", pd.Series("", index=edges.index)).fillna("").astype(str)
    product_name = np.where(pd.Series(product_name).str.strip().ne(""), product_name, edges["product_id"])
    edges["product_name_resolved"] = product_name
    edges["semantic_relevance"] = paired_text_similarity(edges["product_name_resolved"], edges["keyword_display"])
    max_edge = float(edges["edge_value"].max()) or 1.0
    edges["layout_weight"] = 0.25 + np.log1p(edges["edge_value"]) / np.log1p(max_edge) * 2.75
    edges["source"] = "P|" + edges["product_id"].astype(str)
    edges["target"] = "K|" + edges["keyword_normalized"].astype(str)

    graph = nx.Graph()
    for _, row in edges.iterrows():
        graph.add_edge(
            row["source"], row["target"],
            edge_value=float(row["edge_value"]),
            layout_weight=float(row["layout_weight"]),
            semantic_relevance=float(row["semantic_relevance"]),
            product_id=str(row["product_id"]),
            keyword=str(row["keyword_display"]),
        )

    item = work[work.get("entity_type", "").astype(str).str.upper().eq("ITEM")].copy()
    item[metric] = _numeric(item, metric)
    item_map = item.sort_values(metric, ascending=False).drop_duplicates("product_id").set_index("product_id") if not item.empty else pd.DataFrame()

    degree, between = _centrality(graph)
    communities = _community_map(graph)
    positions = _layout_graph(graph)
    nodes: list[dict[str, object]] = []

    product_agg = edges.groupby("product_id", as_index=False).agg(
        value=("edge_value", "sum"),
        clicks=("clicks", "sum"),
        cost=("cost", "sum"),
        sales_attr=("sales_attr", "sum"),
        orders_attr=("orders_attr", "sum"),
        roas_attr_pct=("roas_attr_pct", "mean"),
        product_name=("product_name_resolved", "first"),
        category=("category", "first") if "category" in edges else ("product_id", "first"),
        product_group=("product_group", "first") if "product_group" in edges else ("product_id", "first"),
    )
    for _, row in product_agg.iterrows():
        product_id = str(row["product_id"])
        node_id = f"P|{product_id}"
        values = row.to_dict()
        if not item_map.empty and product_id in item_map.index:
            item_row = item_map.loc[product_id]
            for col in [
                metric,
                "clicks",
                "cost",
                "sales_attr",
                "orders_attr",
                "roas_attr_pct",
                "expected_incremental_profit",
                "current_bid",
                "recommended_cpc",
            ]:
                if col in item_row and pd.notna(item_row[col]):
                    values[col] = float(item_row[col])
            values["value"] = float(item_row.get(metric, values["value"]))
            values["product_name"] = str(item_row.get("product_name", values["product_name"]) or values["product_name"])
            values["category"] = str(item_row.get("category", values["category"]) or values["category"])
            values["product_group"] = str(item_row.get("product_group", values["product_group"]) or values["product_group"])
            values["action"] = str(item_row.get("action", "") or "")
            values["decision_status"] = str(item_row.get("decision_status", "") or "")
        pos = positions.get(node_id, np.array([0.0, 0.0]))
        nodes.append({
            "node_id": node_id,
            "node_type": "PRODUCT",
            "label": _safe_label(values.get("product_name") or product_id, 24),
            "full_label": str(values.get("product_name") or product_id),
            "product_id": product_id,
            "keyword": "",
            "category": str(values.get("category") or "UNKNOWN"),
            "product_group": str(values.get("product_group") or "UNKNOWN"),
            "value": float(values.get("value", 0.0) or 0.0),
            "clicks": float(values.get("clicks", 0.0) or 0.0),
            "cost": float(values.get("cost", 0.0) or 0.0),
            "sales_attr": float(values.get("sales_attr", 0.0) or 0.0),
            "orders_attr": float(values.get("orders_attr", 0.0) or 0.0),
            "roas_attr_pct": float(values.get("roas_attr_pct", 0.0) or 0.0),
            "expected_incremental_profit": float(values.get("expected_incremental_profit", 0.0) or 0.0),
            "current_bid": float(values.get("current_bid", 0.0) or 0.0),
            "recommended_cpc": float(values.get("recommended_cpc", 0.0) or 0.0),
            "action": str(values.get("action", "") or ""),
            "decision_status": str(values.get("decision_status", "") or ""),
            "weighted_degree": degree.get(node_id, 0.0),
            "bridge_score": between.get(node_id, 0.0),
            "community": communities.get(node_id, 0),
            "x": float(pos[0]),
            "y": float(pos[1]),
        })

    keyword_agg = edges.groupby("keyword_normalized", as_index=False).agg(
        value=("edge_value", "sum"),
        clicks=("clicks", "sum"),
        cost=("cost", "sum"),
        sales_attr=("sales_attr", "sum"),
        orders_attr=("orders_attr", "sum"),
        roas_attr_pct=("roas_attr_pct", "mean"),
        keyword=("keyword_display", "first"),
        product_count=("product_id", "nunique"),
    )
    keyword_dominant = (
        edges.sort_values("edge_value", ascending=False)
        .drop_duplicates("keyword_normalized", keep="first")
        .set_index("keyword_normalized")
    )
    for _, row in keyword_agg.iterrows():
        normalized = str(row["keyword_normalized"])
        node_id = f"K|{normalized}"
        pos = positions.get(node_id, np.array([0.0, 0.0]))
        dominant = keyword_dominant.loc[normalized] if normalized in keyword_dominant.index else pd.Series(dtype=object)
        nodes.append({
            "node_id": node_id,
            "node_type": "KEYWORD",
            "label": _safe_label(row["keyword"], 18),
            "full_label": str(row["keyword"]),
            "product_id": "",
            "keyword": str(row["keyword"]),
            "category": "KEYWORD",
            "product_group": "KEYWORD",
            "value": float(row["value"]),
            "clicks": float(row["clicks"]),
            "cost": float(row["cost"]),
            "sales_attr": float(row["sales_attr"]),
            "orders_attr": float(row["orders_attr"]),
            "roas_attr_pct": float(row["roas_attr_pct"]),
            "expected_incremental_profit": float(pd.to_numeric(pd.Series([dominant.get("expected_incremental_profit", 0.0)]), errors="coerce").fillna(0).iloc[0]),
            "current_bid": float(pd.to_numeric(pd.Series([dominant.get("current_bid", 0.0)]), errors="coerce").fillna(0).iloc[0]),
            "recommended_cpc": float(pd.to_numeric(pd.Series([dominant.get("recommended_cpc", 0.0)]), errors="coerce").fillna(0).iloc[0]),
            "action": str(dominant.get("action", "") or ""),
            "decision_status": str(dominant.get("decision_status", "") or ""),
            "product_count": int(row["product_count"]),
            "weighted_degree": degree.get(node_id, 0.0),
            "bridge_score": between.get(node_id, 0.0),
            "community": communities.get(node_id, 0),
            "x": float(pos[0]),
            "y": float(pos[1]),
        })

    nodes_df = pd.DataFrame(nodes)
    summary = _network_summary(nodes_df, edges, graph, metric)
    return NetworkData(nodes_df, edges.reset_index(drop=True), graph, summary)



def build_keyword_similarity_network(
    decisions: pd.DataFrame,
    *,
    metric: str = "sales_attr",
    max_keywords: int = 120,
    threshold: float = 0.34,
) -> NetworkData:
    kw = decisions[decisions.get("entity_type", "").astype(str).str.upper().eq("KEYWORD")].copy()
    if kw.empty:
        return NetworkData(pd.DataFrame(), pd.DataFrame(), nx.Graph(), _network_summary(pd.DataFrame(), pd.DataFrame(), nx.Graph(), metric))
    nodes_base, edges = keyword_similarity_edges(
        kw,
        value_col=metric,
        max_keywords=max_keywords,
        threshold=threshold,
        top_k_per_keyword=4,
    )
    graph = nx.Graph()
    if not nodes_base.empty:
        for node_id in nodes_base["node_id"]:
            graph.add_node(str(node_id))
    for _, row in edges.iterrows():
        graph.add_edge(
            str(row["source"]), str(row["target"]),
            edge_value=float(row["similarity"]),
            layout_weight=0.25 + float(row["similarity"]) * 3.5,
            similarity=float(row["similarity"]),
        )
    communities = _community_map(graph)
    degree, between = _centrality(graph)
    positions = _layout_graph(graph, seed=23)
    nodes: list[dict[str, object]] = []
    keyword_col = "keyword"
    kw_meta = kw.copy()
    for col in ["sales_attr", "clicks", "cost", "orders_attr", "roas_attr_pct", "expected_incremental_profit"]:
        kw_meta[col] = _numeric(kw_meta, col)
    kw_meta["keyword_normalized"] = kw_meta["keyword"].map(normalize_phrase)
    kw_meta = kw_meta.sort_values(metric if metric in kw_meta else "sales_attr", ascending=False).drop_duplicates("keyword_normalized")
    kw_meta = kw_meta.set_index("keyword_normalized") if not kw_meta.empty else pd.DataFrame()
    for _, row in nodes_base.iterrows():
        node_id = str(row["node_id"])
        pos = positions.get(node_id, np.array([0.0, 0.0]))
        value = float(pd.to_numeric(pd.Series([row.get(metric, 0.0)]), errors="coerce").fillna(0).iloc[0])
        normalized = normalize_phrase(row.get(keyword_col, ""))
        meta_row = kw_meta.loc[normalized] if not kw_meta.empty and normalized in kw_meta.index else pd.Series(dtype=object)
        nodes.append({
            "node_id": node_id,
            "node_type": "KEYWORD",
            "label": _safe_label(row.get(keyword_col, ""), 18),
            "full_label": str(row.get(keyword_col, "")),
            "keyword": str(row.get(keyword_col, "")),
            "product_id": "",
            "category": "KEYWORD",
            "product_group": "KEYWORD",
            "value": value,
            "clicks": float(row.get("clicks", 0.0) or 0.0),
            "cost": float(row.get("cost", 0.0) or 0.0),
            "sales_attr": float(row.get("sales_attr", 0.0) or 0.0),
            "orders_attr": float(row.get("orders_attr", 0.0) or 0.0),
            "roas_attr_pct": float(row.get("roas_attr_pct", 0.0) or 0.0),
            "expected_incremental_profit": float(pd.to_numeric(pd.Series([meta_row.get("expected_incremental_profit", 0.0)]), errors="coerce").fillna(0).iloc[0]),
            "current_bid": float(pd.to_numeric(pd.Series([meta_row.get("current_bid", 0.0)]), errors="coerce").fillna(0).iloc[0]),
            "recommended_cpc": float(pd.to_numeric(pd.Series([meta_row.get("recommended_cpc", 0.0)]), errors="coerce").fillna(0).iloc[0]),
            "action": str(meta_row.get("action", "") or ""),
            "decision_status": str(meta_row.get("decision_status", "") or ""),
            "weighted_degree": degree.get(node_id, 0.0),
            "bridge_score": between.get(node_id, 0.0),
            "community": communities.get(node_id, 0),
            "x": float(pos[0]),
            "y": float(pos[1]),
        })
    nodes_df = pd.DataFrame(nodes)
    summary = _network_summary(nodes_df, edges, graph, metric)
    summary["products"] = 0
    return NetworkData(nodes_df, edges, graph, summary)



def build_product_similarity_network(
    decisions: pd.DataFrame,
    *,
    metric: str = "sales_attr",
    max_products: int = 80,
    threshold: float = 0.18,
) -> NetworkData:
    kw = decisions[decisions.get("entity_type", "").astype(str).str.upper().eq("KEYWORD")].copy()
    if kw.empty:
        return NetworkData(pd.DataFrame(), pd.DataFrame(), nx.Graph(), _network_summary(pd.DataFrame(), pd.DataFrame(), nx.Graph(), metric))
    kw["keyword_normalized"] = kw["keyword"].map(normalize_phrase)
    kw = kw[kw["keyword_normalized"].ne("")]
    rank = kw.groupby("product_id")[_numeric(kw, metric).name if False else metric].sum() if metric in kw else kw.groupby("product_id").size()
    products = list(rank.sort_values(ascending=False).head(max_products).index.astype(str))
    kw = kw[kw["product_id"].astype(str).isin(products)]
    keyword_sets = kw.groupby("product_id")["keyword_normalized"].agg(lambda x: set(x)).to_dict()
    product_meta = decisions[decisions.get("entity_type", "").astype(str).str.upper().eq("ITEM")].copy()
    if product_meta.empty:
        product_meta = kw.copy()
    product_meta = product_meta.sort_values(metric if metric in product_meta else "clicks", ascending=False).drop_duplicates("product_id")
    meta = product_meta.set_index(product_meta["product_id"].astype(str))
    names = [str(meta.loc[p].get("product_name", p)) if p in meta.index else p for p in products]
    name_similarity = paired_text_similarity(names, names)  # diagonal only; full matrix built below separately
    del name_similarity
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), sublinear_tf=True)
        name_matrix = vectorizer.fit_transform([normalize_phrase(x) for x in names])
        name_sim = cosine_similarity(name_matrix)
    except Exception:
        name_sim = np.eye(len(products))

    edge_rows: list[dict[str, object]] = []
    for i, left in enumerate(products):
        left_set = keyword_sets.get(left, set())
        for j in range(i + 1, len(products)):
            right = products[j]
            right_set = keyword_sets.get(right, set())
            union = len(left_set | right_set)
            jaccard = len(left_set & right_set) / union if union else 0.0
            semantic = float(name_sim[i, j])
            similarity = 0.7 * jaccard + 0.3 * semantic
            if similarity >= threshold:
                edge_rows.append({
                    "source": f"P|{left}", "target": f"P|{right}",
                    "similarity": similarity, "shared_keyword_jaccard": jaccard,
                    "name_similarity": semantic, "edge_value": similarity,
                    "layout_weight": 0.25 + similarity * 4.0,
                })
    edges = pd.DataFrame(edge_rows)
    graph = nx.Graph()
    for product in products:
        graph.add_node(f"P|{product}")
    for _, row in edges.iterrows():
        graph.add_edge(row["source"], row["target"], **row.to_dict())
    communities = _community_map(graph)
    degree, between = _centrality(graph)
    positions = _layout_graph(graph, seed=31)
    node_rows: list[dict[str, object]] = []
    for product in products:
        row = meta.loc[product] if product in meta.index else pd.Series(dtype=object)
        node_id = f"P|{product}"
        pos = positions.get(node_id, np.array([0.0, 0.0]))
        node_rows.append({
            "node_id": node_id,
            "node_type": "PRODUCT",
            "label": _safe_label(row.get("product_name", product), 22),
            "full_label": str(row.get("product_name", product)),
            "product_id": product,
            "keyword": "",
            "category": str(row.get("category", "UNKNOWN")),
            "product_group": str(row.get("product_group", "UNKNOWN")),
            "value": float(pd.to_numeric(pd.Series([row.get(metric, 0.0)]), errors="coerce").fillna(0).iloc[0]),
            "clicks": float(pd.to_numeric(pd.Series([row.get("clicks", 0.0)]), errors="coerce").fillna(0).iloc[0]),
            "cost": float(pd.to_numeric(pd.Series([row.get("cost", 0.0)]), errors="coerce").fillna(0).iloc[0]),
            "sales_attr": float(pd.to_numeric(pd.Series([row.get("sales_attr", 0.0)]), errors="coerce").fillna(0).iloc[0]),
            "orders_attr": float(pd.to_numeric(pd.Series([row.get("orders_attr", 0.0)]), errors="coerce").fillna(0).iloc[0]),
            "roas_attr_pct": float(pd.to_numeric(pd.Series([row.get("roas_attr_pct", 0.0)]), errors="coerce").fillna(0).iloc[0]),
            "expected_incremental_profit": float(pd.to_numeric(pd.Series([row.get("expected_incremental_profit", 0.0)]), errors="coerce").fillna(0).iloc[0]),
            "current_bid": float(pd.to_numeric(pd.Series([row.get("current_bid", 0.0)]), errors="coerce").fillna(0).iloc[0]),
            "recommended_cpc": float(pd.to_numeric(pd.Series([row.get("recommended_cpc", 0.0)]), errors="coerce").fillna(0).iloc[0]),
            "action": str(row.get("action", "") or ""),
            "decision_status": str(row.get("decision_status", "") or ""),
            "weighted_degree": degree.get(node_id, 0.0),
            "bridge_score": between.get(node_id, 0.0),
            "community": communities.get(node_id, 0),
            "x": float(pos[0]), "y": float(pos[1]),
        })
    nodes = pd.DataFrame(node_rows)
    summary = _network_summary(nodes, edges, graph, metric)
    summary["keywords"] = 0
    return NetworkData(nodes, edges, graph, summary)



def scale_treemap(decisions: pd.DataFrame, *, metric: str = "sales_attr", height: int = 430) -> go.Figure:
    item = decisions[decisions.get("entity_type", "").astype(str).str.upper().eq("ITEM")].copy()
    if item.empty:
        item = decisions[decisions.get("entity_type", "").astype(str).str.upper().eq("KEYWORD")].copy()
        item = item.groupby("product_id", as_index=False).agg({
            metric: "sum" if metric in item else "size",
            "sales_attr": "sum", "cost": "sum", "clicks": "sum", "roas_attr_pct": "mean",
            **({"product_name": "first"} if "product_name" in item else {}),
            **({"category": "first"} if "category" in item else {}),
        })
    if item.empty:
        return go.Figure()
    for col in [metric, "sales_attr", "cost", "clicks", "roas_attr_pct"]:
        item[col] = _numeric(item, col)
    item["category"] = item.get("category", "UNKNOWN").fillna("UNKNOWN").astype(str)
    item["product_name"] = item.get("product_name", item["product_id"]).fillna(item["product_id"]).astype(str)
    item["size_value"] = item[metric].clip(lower=0)
    if item["size_value"].sum() <= 0:
        item["size_value"] = item["clicks"].clip(lower=0) + 1
    fig = px.treemap(
        item,
        path=[px.Constant("全商品"), "category", "product_name"],
        values="size_value",
        color="roas_attr_pct",
        color_continuous_scale="RdYlGn",
        custom_data=["product_id", "sales_attr", "cost", "clicks", "roas_attr_pct"],
    )
    fig.update_traces(
        hovertemplate=(
            "<b>%{label}</b><br>商品ID: %{customdata[0]}<br>"
            "属性売上: ¥%{customdata[1]:,.0f}<br>広告費: ¥%{customdata[2]:,.0f}<br>"
            "クリック: %{customdata[3]:,.0f}<br>ROAS: %{customdata[4]:,.1f}%<extra></extra>"
        ),
        marker=dict(line=dict(width=1.5, color="#FFFFFF")),
    )
    fig.update_layout(
        height=height, paper_bgcolor=PANEL, plot_bgcolor=PANEL,
        margin=dict(l=8, r=8, t=12, b=8),
        coloraxis_colorbar=dict(title="ROAS%", thickness=10),
    )
    return fig



def performance_scatter(decisions: pd.DataFrame, *, entity_type: str = "ITEM", height: int = 430) -> go.Figure:
    df = decisions[decisions.get("entity_type", "").astype(str).str.upper().eq(entity_type.upper())].copy()
    if df.empty:
        return go.Figure()
    for col in ["clicks", "sales_attr", "cost", "roas_attr_pct", "orders_attr", "expected_incremental_profit"]:
        df[col] = _numeric(df, col)
    df["display_name"] = np.where(
        df.get("product_name", "").fillna("").astype(str).str.strip().ne(""),
        df.get("product_name", "").fillna("").astype(str),
        np.where(df["entity_type"].eq("KEYWORD"), df["keyword"].astype(str), df["product_id"].astype(str)),
    )
    size_col = "cost"
    fig = px.scatter(
        df,
        x="clicks",
        y="sales_attr",
        size=size_col,
        color="roas_attr_pct",
        color_continuous_scale="RdYlGn",
        hover_name="display_name",
        hover_data={
            "product_id": True,
            "keyword": entity_type.upper() == "KEYWORD",
            "clicks": ":,.0f",
            "sales_attr": ":,.0f",
            "cost": ":,.0f",
            "roas_attr_pct": ":.1f",
            "orders_attr": ":,.0f",
        },
        labels={"clicks": "クリック", "sales_attr": "属性売上", "roas_attr_pct": "ROAS%"},
    )
    fig.update_traces(marker=dict(opacity=0.82, line=dict(width=0.8, color="#FFFFFF")))
    fig.update_layout(
        height=height,
        paper_bgcolor=PANEL,
        plot_bgcolor=PANEL,
        margin=dict(l=18, r=12, t=16, b=12),
        xaxis=dict(gridcolor=GRID, zeroline=False),
        yaxis=dict(gridcolor=GRID, zeroline=False, tickprefix="¥"),
        coloraxis_colorbar=dict(title="ROAS%", thickness=10),
    )
    return fig



def correlation_heatmap(decisions: pd.DataFrame, *, entity_type: str = "ITEM", height: int = 430) -> go.Figure:
    df = decisions[decisions.get("entity_type", "").astype(str).str.upper().eq(entity_type.upper())].copy()
    metrics = ["impressions", "clicks", "cost", "actual_cpc", "sales_attr", "orders_attr", "cvr_attr", "roas_attr_pct", "expected_incremental_profit"]
    available = [c for c in metrics if c in df.columns]
    if len(available) < 2:
        return go.Figure()
    numeric = df[available].apply(pd.to_numeric, errors="coerce")
    corr = numeric.corr(method="spearman").fillna(0.0)
    labels = [METRIC_LABELS.get(c, {"impressions": "表示", "actual_cpc": "実績CPC", "cvr_attr": "CVR"}.get(c, c)) for c in available]
    fig = go.Figure(go.Heatmap(
        z=corr.values,
        x=labels,
        y=labels,
        zmin=-1,
        zmax=1,
        colorscale=[[0, "#B34C5E"], [0.5, "#F6F8FB"], [1, "#2E85C7"]],
        text=np.round(corr.values, 2),
        texttemplate="%{text}",
        hovertemplate="%{x} × %{y}<br>Spearman: %{z:.3f}<extra></extra>",
        colorbar=dict(title="ρ", thickness=10),
    ))
    fig.update_layout(
        height=height, paper_bgcolor=PANEL, plot_bgcolor=PANEL,
        margin=dict(l=18, r=12, t=16, b=12),
        xaxis=dict(side="bottom"),
    )
    return fig



def term_frequency_figure(decisions: pd.DataFrame, *, metric: str = "sales_attr", top_n: int = 25, height: int = 500) -> tuple[go.Figure, pd.DataFrame]:
    kw = decisions[decisions.get("entity_type", "").astype(str).str.upper().eq("KEYWORD")].copy()
    freq = build_term_frequency(kw, weight_col=metric, top_n=top_n)
    if freq.empty:
        return go.Figure(), freq
    plot_df = freq.sort_values("weighted_value", ascending=True)
    fig = go.Figure(go.Bar(
        x=plot_df["weighted_value"],
        y=plot_df["term"],
        orientation="h",
        marker=dict(color=plot_df["share_pct"], colorscale=[[0, "#B6D8ED"], [1, "#2E85C7"]], line=dict(width=0)),
        customdata=np.stack([plot_df["keyword_count"], plot_df["share_pct"]], axis=-1),
        hovertemplate="<b>%{y}</b><br>規模: %{x:,.0f}<br>接続キーワード: %{customdata[0]:,.0f}<br>構成比: %{customdata[1]:.1f}%<extra></extra>",
    ))
    fig.update_layout(
        height=height, paper_bgcolor=PANEL, plot_bgcolor=PANEL,
        margin=dict(l=12, r=12, t=10, b=12),
        xaxis=dict(gridcolor=GRID, title=METRIC_LABELS.get(metric, metric)),
        yaxis=dict(title=""),
    )
    return fig, freq



def term_cooccurrence_figure(decisions: pd.DataFrame, *, metric: str = "clicks", top_terms: int = 16, height: int = 520) -> tuple[go.Figure, pd.DataFrame]:
    kw = decisions[decisions.get("entity_type", "").astype(str).str.upper().eq("KEYWORD")].copy()
    terms, matrix = build_term_cooccurrence(kw, weight_col=metric, top_terms=top_terms)
    if not terms:
        return go.Figure(), pd.DataFrame()
    fig = go.Figure(go.Heatmap(
        z=matrix,
        x=terms,
        y=terms,
        zmin=0,
        zmax=1,
        colorscale=[[0, "#F6F8FB"], [0.4, "#B6D8ED"], [1, "#245C8A"]],
        hovertemplate="%{x} × %{y}<br>共起関連度: %{z:.3f}<extra></extra>",
        colorbar=dict(title="関連度", thickness=10),
    ))
    fig.update_layout(
        height=height, paper_bgcolor=PANEL, plot_bgcolor=PANEL,
        margin=dict(l=18, r=12, t=12, b=12),
    )
    matrix_df = pd.DataFrame(matrix, index=terms, columns=terms)
    return fig, matrix_df



def top_network_entities(network: NetworkData, *, node_type: str | None = None, top_n: int = 10) -> pd.DataFrame:
    nodes = network.nodes.copy()
    if nodes.empty:
        return nodes
    if node_type:
        nodes = nodes[nodes["node_type"].eq(node_type)]
    columns = [
        "node_type", "full_label", "product_id", "keyword", "value", "weighted_degree",
        "bridge_score", "community", "sales_attr", "cost", "clicks", "roas_attr_pct",
    ]
    return nodes.sort_values(["weighted_degree", "value"], ascending=False).head(top_n)[[c for c in columns if c in nodes]].reset_index(drop=True)
