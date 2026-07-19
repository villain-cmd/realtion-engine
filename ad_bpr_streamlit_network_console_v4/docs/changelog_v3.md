# v3 Change Log

## Input contract

- 3つのアップローダーへ個別テンプレートダウンロードを追加
- 3種一括ZIPを追加
- 必須度、型、例、定義のカラム辞書を追加
- 商品マスタへ `product_name` を追加

## Network home

- 商品 × キーワードの二部グラフ
- 規模指標切替
- 商品/KW、クラスタ、ROASの色分け
- Hub、Bridge、Community
- ノード・エッジCSV出力

## Keyword mine

- NFKC正規化
- Janome形態素分解
- 文字2〜4gram TF-IDF
- キーワード類似ネットワーク
- 概念語頻度
- 概念語共起行列
- クラスタ、類似エッジ、概念語CSV出力

## Product map

- 共有キーワードJaccardと商品名類似度の合成
- 商品類似ネットワーク
- 選択商品の接続キーワード明細

## Frontend

- 独自CSSとデザイントークン
- ダークブランドヘッダー
- KPIカード
- Data Dock
- Network pulse
- 6タブの情報設計
- レスポンシブ調整

## Verification

- Python test: 15 passed
- Streamlit AppTest: exceptions 0
- 実データで697判断行、8 Plotly charts、19 download buttonsを確認
