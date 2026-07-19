# Verification Report v4

## 対象

- Repository: Profit Network Console v4
- Renderer: Vanilla Canvas 2D
- Input: 楽天RPP商品別、商品×キーワード別、現在設定、検証用商品マスタ
- Product master: 実粗利未提供のため、検証用35%仮定値

## Static checks

```text
compileall: PASS
pytest: 18 passed
```

## Streamlit AppTest

3入力をテストランナーから投入し、アプリ全体を実行した。

```text
exceptions: 0
tabs: 6
iframes: 3
download_buttons: 19
dataframes: 10
metrics: 4
```

3つのiframeは以下。

1. 商品管理番号 × キーワードRelation Map
2. Keyword Similarity Graph
3. Product Affinity Graph

## Browser test

生成したstandalone HTMLをChromiumへ直接投入し、JavaScriptを実行した。

```text
page_errors: 0
canvas_count: 2
initial_status: 128 nodes / 128 links / 100%
search_status: 12 nodes / 11 links / 100%
inspector_open: True
```

Canvas 2枚の内訳は、メイン描画とミニマップ。

## Real-data network summary

### Relation Map

- Products: 44
- Keywords: 84
- Relationships: 128
- Clusters: 22
- Top 10 product share: 78.8%
- Largest cluster share: 42.4%

### Keyword graph

- Keywords: 120
- Similarity links: 116
- Clusters: 56

### Product graph

- Products: 73
- Similarity links: 76
- Clusters: 34

## Interaction verification

- Search `171-39`: product and 1-hop keyword context remain visible
- Enter: first matching product selected
- Inspector: opened successfully
- Inspector content: product ID, product name, action, sales, ROAS, spend, clicks, connected keywords
- Browser JavaScript errors: 0

## Library boundary

Relation Map、Keyword graph、Product graphの描画・操作は以下だけで構成。

- HTML5 Canvas 2D
- CSS
- Vanilla JavaScript
- Streamlit `st.iframe`

以下は使用していない。

- Plotly network trace
- D3.js
- Cytoscape.js
- vis-network
- Sigma.js
- 外部CDN

Plotlyは、Treemap、散布図、相関行列等の非ネットワークチャートに限って継続使用する。
