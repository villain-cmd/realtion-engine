# 3入力テンプレート仕様

## 1. 実績レポート

### 受入形式

- 楽天RPP 商品別実績CSV
- 楽天RPP 商品×キーワード実績CSV
- 標準実績CSV

楽天RPPの生CSVは、先頭の実行日時・検索条件・説明行を残したまま投入できます。アプリがヘッダー行、文字コード、集計期間を検出します。

標準形式の中核列:

- `platform`
- `entity_type`
- `product_id`
- `keyword`
- `period_start`, `period_end`
- `registered_bid`
- `impressions`, `clicks`, `cost`, `actual_cpc`
- `sales_short`, `orders_short`
- `sales_attr`, `orders_attr`
- `ctr_pct`

## 2. 現在の入札設定

楽天RPPの商品・キーワードCPC設定CSVを使用します。

中核列:

- `商品管理番号`
- `商品名`
- `商品CPC`
- `キーワード`
- `キーワードCPC`
- `目安CPC`

登録CPCは演算の制御基準です。実績CPCはオークション結果の評価値として分離します。

## 3. 商品マスタ

中核列:

- `platform`
- `common_product_id`
- `product_id`
- `product_name`
- `category`
- `product_group`
- `gross_margin_rate`
- `other_promo_cost_rate`
- `item_min_cpc`, `item_max_cpc`
- `stock_qty`
- `product_status`
- `launch_date`
- `role`

`product_name` は表示だけでなく、商品名とキーワードの文字n-gram関連度計算に利用します。`category` と `product_group` はTreemap、クラスタ集計、ベイズ事前分布に利用します。

## 文字コード

- 標準実績: UTF-8 BOM
- 現在設定: CP932
- 商品マスタ: UTF-8 BOM
- 楽天入稿出力: CP932 / CRLF

全カラムの必須度・型・例・定義は `templates/input_column_dictionary_utf8.csv` を参照してください。
