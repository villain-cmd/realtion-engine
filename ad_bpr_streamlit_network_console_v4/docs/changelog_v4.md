# v4 Change Log

## Contrast

- 背景と同化していた補助テキスト、タブ、キャプション、入力ラベルを高コントラスト化
- サイドバーの暗色テーマと入力コントロールを別ルールに分離
- カスタムHTMLを `textwrap.dedent()` して、コードブロックとして露出する問題を解消
- KPIカード、Insight一覧、Expander、Uploader、DataFrameの境界と文字色を強化

## Relation Map

- Plotlyのnetwork scatterを廃止
- HTML5 Canvas / Vanilla JavaScriptの独自レンダラーを実装
- 商品管理番号を商品ノードの表示ラベルに固定
- キーワードを接続ノードとして表示
- 円サイズを属性売上へ固定
- 初期色を判定へ固定
- ROAS、クラスタ、商品/KW種別へ切替可能
- ラベル衝突回避を実装
- パン、ズーム、ノードドラッグ、検索、クリック選択、ダブルクリックフォーカス、ミニマップ、全画面、PNG保存を実装
- 右側インスペクタに売上、広告費、ROAS、クリック、判定、接続ノードを表示
- バックエンドでノード衝突緩和と商品中心・キーワード外周化を実施

## Keyword / Product graph

- キーワード類似ネットワークも独自Canvasへ置換
- 商品類似ネットワークも独自Canvasへ置換
- 非ネットワークのTreemap、散布図、相関行列はPlotlyを継続利用

## Verification

- 外部CDNなし
- 外部グラフUIライブラリなし
- `relationship_canvas.py` のHTMLにPlotly / Cytoscape / D3 / vis.js依存なし
