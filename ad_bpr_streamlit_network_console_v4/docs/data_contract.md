# データ契約

## 商品マスタ

必須キー:

- `platform`
- `common_product_id`
- `product_id`

推奨列:

- `product_name`: 商品ノード表示と商品名×キーワード関連度
- `gross_margin_rate`: 0.35または35
- `other_promo_cost_rate`: ポイント・クーポン・アフィリエイト等の売上比率
- `item_min_cpc`, `item_max_cpc`
- `stock_qty`
- `product_status`: ACTIVE / OUT_OF_STOCK / DISCONTINUED / RESERVED / INACTIVE
- `launch_date`
- `category`, `product_group`

粗利率未登録でも試算表示は可能だが、既定では入稿不可。

## 正規化実績CSV

楽天RPP以外は次の標準列へ変換する。

- `platform`
- `entity_type`: ITEM / KEYWORD
- `product_id`
- `keyword`
- `period_start`, `period_end`
- `registered_bid`
- `impressions`
- `clicks`
- `cost`
- `actual_cpc`
- `sales_short`, `orders_short`
- `sales_attr`, `orders_attr`
- `ctr_pct`

Yahooショッピングの媒体固有列は、この標準形式へ変換するadapterで吸収する。

## 手動オーバーライド

- `BASELINE`: 手動値を計算基準にするが自動計算へ復帰可能
- `LOCK_CPC`: 解除まで固定
- `FORCE_STOP`: 解除まで停止候補
- `EXCLUDE`: 計算対象外

