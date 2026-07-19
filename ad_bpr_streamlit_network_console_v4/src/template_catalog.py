from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .io_utils import dataframe_to_csv_bytes


@dataclass(frozen=True)
class InputTemplate:
    key: str
    title: str
    filename: str
    description: str
    accepted_formats: str
    dataframe: pd.DataFrame
    columns: pd.DataFrame
    encoding: str = "utf-8-sig"

    def to_bytes(self) -> bytes:
        return dataframe_to_csv_bytes(self.dataframe, encoding=self.encoding)


PERFORMANCE_TEMPLATE = pd.DataFrame(
    [
        {
            "platform": "rakuten",
            "entity_type": "ITEM",
            "product_id": "860-05",
            "keyword": "",
            "period_start": "2026-07-01",
            "period_end": "2026-07-07",
            "registered_bid": 36,
            "impressions": 12000,
            "clicks": 420,
            "cost": 12600,
            "actual_cpc": 30,
            "sales_short": 68200,
            "orders_short": 12,
            "sales_attr": 119800,
            "orders_attr": 21,
            "ctr_pct": 3.5,
        },
        {
            "platform": "rakuten",
            "entity_type": "KEYWORD",
            "product_id": "860-05",
            "keyword": "布団 収納袋",
            "period_start": "2026-07-01",
            "period_end": "2026-07-07",
            "registered_bid": 40,
            "impressions": 4200,
            "clicks": 180,
            "cost": 7200,
            "actual_cpc": 40,
            "sales_short": 42100,
            "orders_short": 8,
            "sales_attr": 77800,
            "orders_attr": 14,
            "ctr_pct": 4.29,
        },
        {
            "platform": "yahoo",
            "entity_type": "KEYWORD",
            "product_id": "860-05",
            "keyword": "布団収納袋",
            "period_start": "2026-07-01",
            "period_end": "2026-07-07",
            "registered_bid": 35,
            "impressions": 3000,
            "clicks": 120,
            "cost": 3900,
            "actual_cpc": 32.5,
            "sales_short": 31000,
            "orders_short": 6,
            "sales_attr": 31000,
            "orders_attr": 6,
            "ctr_pct": 4.0,
        },
    ]
)

PERFORMANCE_COLUMNS = pd.DataFrame(
    [
        ("platform", "必須", "string", "rakuten / yahoo", "媒体。楽天RPPの生レポートは自動判定されるため変換不要。"),
        ("entity_type", "必須", "enum", "ITEM / KEYWORD", "商品単位または商品×キーワード単位。"),
        ("product_id", "必須", "string", "860-05", "媒体の商品管理番号。共通商品IDと異なる場合は商品マスタで紐付ける。"),
        ("keyword", "KEYWORD時必須", "string", "布団 収納袋", "ITEM行は空欄。表記揺れはテキストマイニングで正規化する。"),
        ("period_start", "必須", "date", "2026-07-01", "集計開始日 YYYY-MM-DD。"),
        ("period_end", "必須", "date", "2026-07-07", "集計終了日 YYYY-MM-DD。"),
        ("registered_bid", "推奨", "number", "40", "レポート上の登録入札。現在設定CSVがある場合はそちらを優先。"),
        ("impressions", "推奨", "number", "4200", "表示回数。CTR・露出規模・関係性分析に使用。"),
        ("clicks", "必須", "number", "180", "クリック数。"),
        ("cost", "必須", "number", "7200", "広告費。税抜の同一定義へ統一。"),
        ("actual_cpc", "推奨", "number", "40", "実績CPC。空欄の場合は cost / clicks を使用可能。"),
        ("sales_short", "推奨", "number", "42100", "短期アトリビューション売上。楽天12h / Yahoo24h等。"),
        ("orders_short", "推奨", "number", "8", "短期アトリビューション注文数。"),
        ("sales_attr", "必須", "number", "77800", "主判定売上。楽天720h / Yahoo24hの正規化値。"),
        ("orders_attr", "必須", "number", "14", "主判定注文数。"),
        ("ctr_pct", "推奨", "number", "4.29", "CTR%。空欄時は表示回数があれば再計算。"),
    ],
    columns=["column", "requirement", "type", "example", "definition"],
)

SETTING_TEMPLATE = pd.DataFrame(
    [
        {
            "コントロールカラム": "",
            "商品管理番号": "860-05",
            "商品名": "立てて収納できる布団収納袋",
            "価格": 4980,
            "商品URL": "https://item.rakuten.co.jp/example/860-05/",
            "商品CPC": 36,
            "キーワード": "布団 収納袋",
            "キーワードCPC": 40,
            "目安CPC": 56,
        },
        {
            "コントロールカラム": "",
            "商品管理番号": "860-05",
            "商品名": "立てて収納できる布団収納袋",
            "価格": 4980,
            "商品URL": "https://item.rakuten.co.jp/example/860-05/",
            "商品CPC": 36,
            "キーワード": "敷布団 収納",
            "キーワードCPC": 40,
            "目安CPC": 62,
        },
    ]
)

SETTING_COLUMNS = pd.DataFrame(
    [
        ("コントロールカラム", "出力時必須", "string", "u", "RMS反映用。入力時は空欄で可。出力アダプタが変更行へ制御値を設定。"),
        ("商品管理番号", "必須", "string", "860-05", "商品キー。実績・商品マスタと完全一致させる。"),
        ("商品名", "推奨", "string", "布団収納袋", "GUIのノードラベル・検索・テキストマイニングに使用。"),
        ("価格", "推奨", "number", "4980", "商品価格。AOVや商品ポートフォリオ表示の補助。"),
        ("商品URL", "推奨", "url", "https://...", "GUIから媒体商品ページを確認するための参照URL。"),
        ("商品CPC", "商品行必須", "number", "36", "商品単位の現在登録CPC。"),
        ("キーワード", "キーワード行必須", "string", "布団 収納袋", "空欄行は商品CPC行、入力ありは商品×キーワード行。"),
        ("キーワードCPC", "キーワード行必須", "number", "40", "商品×キーワード単位の現在登録CPC。"),
        ("目安CPC", "任意", "number", "56", "媒体提示の参考CPC。演算上は制約ではなく補助情報。"),
    ],
    columns=["column", "requirement", "type", "example", "definition"],
)

PRODUCT_MASTER_TEMPLATE = pd.DataFrame(
    [
        {
            "platform": "rakuten",
            "common_product_id": "CP-00086005",
            "product_id": "860-05",
            "product_name": "立てて収納できる布団収納袋",
            "category": "寝具収納",
            "product_group": "布団収納",
            "gross_margin_rate": 0.35,
            "other_promo_cost_rate": 0.08,
            "item_min_cpc": 24,
            "item_max_cpc": 100,
            "stock_qty": 420,
            "product_status": "ACTIVE",
            "launch_date": "2025-01-15",
            "role": "PROFIT",
        },
        {
            "platform": "rakuten",
            "common_product_id": "CP-00051512",
            "product_id": "515-12",
            "product_name": "大型コレクションケース",
            "category": "コレクション収納",
            "product_group": "フィギュアケース",
            "gross_margin_rate": 0.28,
            "other_promo_cost_rate": 0.05,
            "item_min_cpc": 24,
            "item_max_cpc": 100,
            "stock_qty": 85,
            "product_status": "ACTIVE",
            "launch_date": "2024-09-01",
            "role": "PROFIT",
        },
    ]
)

PRODUCT_MASTER_COLUMNS = pd.DataFrame(
    [
        ("platform", "必須", "string", "rakuten", "媒体。"),
        ("common_product_id", "必須", "string", "CP-00086005", "楽天・Yahoo横断の共通商品ID。"),
        ("product_id", "必須", "string", "860-05", "媒体の商品管理番号。"),
        ("product_name", "推奨", "string", "布団収納袋", "商品ノードの表示名、商品名テキストマイニングに使用。"),
        ("category", "推奨", "string", "寝具収納", "規模・ポートフォリオ・クラスタ集計。"),
        ("product_group", "推奨", "string", "布団収納", "ベイズ事前分布と類似商品群。"),
        ("gross_margin_rate", "強く推奨", "rate", "0.35 / 35", "標準粗利率。未登録行は既定で入稿をブロック。"),
        ("other_promo_cost_rate", "推奨", "rate", "0.08 / 8", "ポイント・クーポン・アフィリエイト等の売上比率。"),
        ("item_min_cpc", "任意", "number", "24", "商品固有のCPC下限。"),
        ("item_max_cpc", "任意", "number", "100", "商品固有のCPC上限。"),
        ("stock_qty", "任意", "number", "420", "在庫。0以下は停止制約に利用可能。"),
        ("product_status", "推奨", "enum", "ACTIVE", "ACTIVE / OUT_OF_STOCK / DISCONTINUED / RESERVED / INACTIVE。"),
        ("launch_date", "任意", "date", "2025-01-15", "新商品・観測期間の判定補助。"),
        ("role", "任意", "enum", "PROFIT", "商品役割。現行方針では均一にPROFIT。"),
    ],
    columns=["column", "requirement", "type", "example", "definition"],
)


TEMPLATES: dict[str, InputTemplate] = {
    "performance": InputTemplate(
        key="performance",
        title="01 / 実績レポート",
        filename="01_performance_input_template_utf8.csv",
        description="楽天RPPの生CSVをそのまま投入可能。Yahoo等は標準カラムへ正規化して投入します。",
        accepted_formats="楽天RPP商品別・キーワード別 / 標準実績CSV",
        dataframe=PERFORMANCE_TEMPLATE,
        columns=PERFORMANCE_COLUMNS,
    ),
    "settings": InputTemplate(
        key="settings",
        title="02 / 現在の入札設定",
        filename="02_current_bid_setting_template_cp932.csv",
        description="現在の登録CPCを基準値として使用し、出力時には元列を維持して差分を反映します。",
        accepted_formats="楽天RPP 商品・キーワードCPC設定CSV",
        dataframe=SETTING_TEMPLATE,
        columns=SETTING_COLUMNS,
        encoding="cp932",
    ),
    "product_master": InputTemplate(
        key="product_master",
        title="03 / 商品マスタ",
        filename="03_product_master_template_utf8.csv",
        description="粗利・販促費・商品名・カテゴリを付与し、利益演算とネットワーク表示を成立させます。",
        accepted_formats="本アプリ標準商品マスタCSV",
        dataframe=PRODUCT_MASTER_TEMPLATE,
        columns=PRODUCT_MASTER_COLUMNS,
    ),
}


def all_column_dictionary() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for key, template in TEMPLATES.items():
        frame = template.columns.copy()
        frame.insert(0, "input_key", key)
        frame.insert(1, "input_title", template.title)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def template_pack_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for template in TEMPLATES.values():
            zf.writestr(template.filename, template.to_bytes())
        zf.writestr(
            "input_column_dictionary_utf8.csv",
            dataframe_to_csv_bytes(all_column_dictionary(), encoding="utf-8-sig"),
        )
        readme = (
            "広告運用計算機 v3 入力テンプレート\n\n"
            "01_performance: 楽天RPPの生レポートは変換せずアップロード可能です。\n"
            "02_current_bid_setting: RMSの商品・キーワードCPC設定CSVの列構成です。\n"
            "03_product_master: 粗利・販促費・商品名・カテゴリ等を付与する独自マスタです。\n"
            "input_column_dictionary: 必須度・型・定義を一覧化しています。\n"
        )
        zf.writestr("README.txt", readme.encode("utf-8"))
    return buffer.getvalue()


def write_templates(root: str | Path) -> None:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    for template in TEMPLATES.values():
        (root_path / template.filename).write_bytes(template.to_bytes())
    (root_path / "input_column_dictionary_utf8.csv").write_bytes(
        dataframe_to_csv_bytes(all_column_dictionary(), encoding="utf-8-sig")
    )
    (root_path / "input_templates_pack.zip").write_bytes(template_pack_bytes())
