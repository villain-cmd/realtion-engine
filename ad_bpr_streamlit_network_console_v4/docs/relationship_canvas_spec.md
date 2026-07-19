# Relationship Canvas v4 Specification

## 目的

広告運用の全体像を「表」ではなく、商品管理番号とキーワードの関係構造として把握する。装飾的なネットワーク図ではなく、次の運用判断を高速化する。

- 売上規模の大きい商品とキーワードの把握
- 増額、減額、停止、維持、観測の分布把握
- 一つの商品に依存するキーワード群の把握
- 複数商品をつなぐ橋渡しキーワードの把握
- 売上集中、クラスタ集中、孤立ノードの把握
- マニュアル介入対象へのフォーカス

## データモデル

### Node

- `id`
- `type`: PRODUCT / KEYWORD
- `productId`
- `keyword`
- `fullLabel`
- `sales`
- `cost`
- `clicks`
- `orders`
- `roas`
- `action`
- `status`
- `community`
- `degree`
- `bridge`
- `x`, `y`

### Edge

- `source`
- `target`
- `sales`
- `cost`
- `clicks`
- `orders`
- `roas`
- `relevance`
- `action`

## 視覚変数

### Relation Map

- 商品表示: 商品管理番号
- キーワード表示: 登録キーワード
- ノード面積: 属性売上
- 既定色: 判定
- 線幅: 商品 × キーワードの売上規模
- 商品: 二重リング
- キーワード: 単一円

### 判定色

- SCALE_UP: green
- SCALE_DOWN: amber
- STOP: red
- KEEP: blue
- OBSERVE: purple
- NEUTRAL: slate

## レイアウト

1. Python側の分析レイアウトを初期シードにする。
2. 商品ノードをクラスタ中心へ寄せる。
3. キーワードノードをクラスタ外周へ広げる。
4. 売上に応じた半径を計算する。
5. ノード半径を考慮した衝突緩和を92反復する。
6. エッジ長を軽く正規化する。
7. ブラウザ側でFit to viewする。

## ラベル

- 商品ラベルは商品名ではなく商品管理番号。
- 商品名はhover / inspectorで表示。
- ラベル候補を選択、hover、近接、商品、売上の順で優先する。
- Canvasの `measureText()` で矩形を計算する。
- 既に配置済みラベルと重なる候補は非表示にする。
- 選択ノードと近接ノードは優先表示する。

## マウス・キーボード

- Hover: ノードを強調、ツールチップ表示、接続線強調
- Click: 選択を固定、インスペクタ表示
- Double click: 選択ノードと1階層だけにフォーカス
- Drag blank: キャンバス移動
- Drag node: ノード位置の手動調整
- Wheel: ポインタ位置を中心にズーム
- `/`: 検索へフォーカス
- `F`: Fit to view
- `Esc`: 選択・検索・フォーカス解除

## インスペクタ

- 商品管理番号またはキーワード
- 商品名
- 判定
- 売上
- 広告費
- ROAS
- クリック
- 接続ノード上位12件
- 1階層フォーカス
- 中央寄せ

## 技術制約

- HTML5 Canvas 2D
- Vanilla JavaScript
- CSS
- Streamlit `st.iframe` with raw HTML
- 外部CDNなし
- D3.jsなし
- Cytoscape.jsなし
- vis-networkなし
- Sigma.jsなし
- Plotly network traceなし

NetworkXはバックエンドの分析値、クラスタ、中心性の算出に使用できるが、グラフUIの描画・操作には使用しない。

## パフォーマンス

- Home既定: 商品48、キーワード110
- Keyword既定: 120
- Product既定: 80
- Device Pixel Ratioは最大2へ制限
- 再描画は `requestAnimationFrame` で統合
- 検索・フォーカス時のみ表示ノードを縮小
- Edge hover target用の透明トレースは生成しない

## アクセシビリティ

- グラフ背景と文字のコントラストを確保
- 色だけでなく商品二重リング、ラベル、インスペクタで意味を補完
- 検索とキーボード操作を提供
- 主要操作にtitle属性を付与
