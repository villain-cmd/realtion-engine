from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.io_utils import read_csv_flexible
from src.network_viz import (
    build_keyword_similarity_network,
    build_product_keyword_network,
    build_product_similarity_network,
)
from src.pipeline import build_pipeline
from src.policy import Policy
from src.relationship_canvas import build_relationship_html
from src.state_bundle import StateBundle


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build standalone custom-canvas previews from uploaded CSV files.")
    parser.add_argument("--reports", nargs="+", required=True)
    parser.add_argument("--setting")
    parser.add_argument("--product-master")
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    reports = [read_csv_flexible(path) for path in args.reports]
    setting_df = read_csv_flexible(args.setting).dataframe if args.setting else None
    master_df = read_csv_flexible(args.product_master).dataframe if args.product_master else None
    policy = Policy()
    context = build_pipeline(reports, setting_df, master_df, StateBundle.empty(), policy)
    decisions = context.decisions.copy()

    if setting_df is not None and {"商品管理番号", "商品名"}.issubset(setting_df.columns):
        names = setting_df.drop_duplicates("商品管理番号").set_index("商品管理番号")["商品名"].astype(str).to_dict()
        decisions["product_name"] = decisions.get("product_name", "").fillna("").astype(str)
        missing = decisions["product_name"].str.strip().eq("")
        decisions.loc[missing, "product_name"] = decisions.loc[missing, "product_id"].astype(str).map(names).fillna(decisions.loc[missing, "product_id"])

    relationship = build_product_keyword_network(decisions, metric="sales_attr", max_products=48, max_keywords=110)
    keyword = build_keyword_similarity_network(decisions, metric="sales_attr", max_keywords=120, threshold=0.34)
    product = build_product_similarity_network(decisions, metric="sales_attr", max_products=80, threshold=0.18)

    previews = {
        "design_preview.html": build_relationship_html(
            relationship,
            metric="sales_attr",
            graph_kind="relationship",
            title="商品管理番号 × キーワード Relation Map",
            subtitle="円サイズ = 属性売上 / 色 = 判定 / 線 = 商品とキーワードの実績接続",
            default_color_mode="action",
            target_roas_pct=policy.target_roas_pct,
            height=860,
        ),
        "keyword_graph_preview.html": build_relationship_html(
            keyword,
            metric="sales_attr",
            graph_kind="keyword_similarity",
            title="Keyword Similarity Graph",
            subtitle="円サイズ = 属性売上 / 線 = 文字n-gram類似度 / 色 = クラスタ",
            default_color_mode="community",
            target_roas_pct=policy.target_roas_pct,
            height=820,
        ),
        "product_graph_preview.html": build_relationship_html(
            product,
            metric="sales_attr",
            graph_kind="product_similarity",
            title="Product Affinity Graph",
            subtitle="円サイズ = 属性売上 / 線 = 共有キーワード + 商品名類似度 / 色 = クラスタ",
            default_color_mode="community",
            target_roas_pct=policy.target_roas_pct,
            height=820,
        ),
    }
    for name, html in previews.items():
        (out_dir / name).write_text(html, encoding="utf-8")

    relationship.nodes.to_csv(out_dir / "network_nodes.csv", index=False, encoding="utf-8-sig")
    relationship.edges.to_csv(out_dir / "network_edges.csv", index=False, encoding="utf-8-sig")
    keyword.nodes.to_csv(out_dir / "keyword_nodes.csv", index=False, encoding="utf-8-sig")
    keyword.edges.to_csv(out_dir / "keyword_edges.csv", index=False, encoding="utf-8-sig")
    product.nodes.to_csv(out_dir / "product_nodes.csv", index=False, encoding="utf-8-sig")
    product.edges.to_csv(out_dir / "product_edges.csv", index=False, encoding="utf-8-sig")

    summary = {
        "run_id": context.run_id,
        "quality_status": context.quality.status,
        "quality_score": context.quality.score,
        "relationship": relationship.summary,
        "keyword": keyword.summary,
        "product": product.summary,
        "renderer": "vanilla_canvas_2d_no_graph_ui_library",
    }
    (out_dir / "verification_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out_dir / "design_preview.html")


if __name__ == "__main__":
    main()
