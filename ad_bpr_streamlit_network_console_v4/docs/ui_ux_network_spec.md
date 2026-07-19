# UI / UX 設計仕様 v4

## 1. 認知順序

画面は利用者が広告構造を理解し、異常時に手動介入し、入稿ファイルを生成する順で構成する。

1. 全体構造を把握
2. 商品管理番号とキーワードの関係を絞る
3. キーワードの文脈を掘る
4. 商品ポートフォリオを掘る
5. 推奨値を補正
6. 入稿・状態を出力
7. データ品質を監査

## 2. ホームの主役

ホームの主役はKPIカードではなく、独自CanvasのRelation Mapとする。

- 商品は商品管理番号を表示
- キーワードは登録キーワードを表示
- 円サイズは属性売上
- 初期色は判定
- 線は商品×キーワード実績
- 商品は二重リング、キーワードは単一円
- ラベルは衝突回避し、常時全表示しない
- hover、click、double clickで段階的に情報を開く

KPIはRelation Mapを読むための補助情報として上部に配置する。

## 3. Relation Mapの操作

### Hover

- 対象ノードと接続線を強調
- 他ノードを減光
- 商品名、売上、広告費、ROAS、クリックをツールチップ表示

### Click

- 選択状態を固定
- 右側インスペクタを開く
- 接続ノード上位12件を表示

### Double click

- 選択ノードと1階層の接続だけに絞る
- Fit to viewを自動実行

### Search

- 商品管理番号、商品名、キーワード、カテゴリ、商品群を検索
- 一致ノードだけでなく、その隣接ノードも残す
- Enterで最初の候補を選択

### Canvas

- 空白ドラッグでパン
- ノードドラッグで位置調整
- ホイールでポインタ中心ズーム
- Fit、全画面、PNG保存、ミニマップを搭載

## 4. ラベル設計

- 商品名を常時表示しない
- 商品ラベルは短い商品管理番号を優先
- キーワードラベルは売上上位、選択、hover、近接を優先
- `measureText()` で矩形を計算し、衝突するラベルを省略
- 選択中のラベルには暗色背景を付ける

## 5. コントラスト

### Light application shell

- 背景: `#EEF3F8`
- Primary text: `#07111F`
- Secondary text: `#40536B`
- Border: `#CEDAE7`
- Panel: `#FFFFFF`

### Dark graph canvas

- Background: `#0A1019`
- Primary text: `#F5F7FB`
- Secondary text: `#A9B6C8`
- Edge: `rgba(103,143,181,.23)`
- Selected edge: `rgba(112,201,255,.82)`

補助文字も背景に対して十分な明度差を持たせる。薄い灰色を主要説明文に使用しない。

## 6. 入力UX

3入力を同じ視覚フォーマットへ統一する。

- 番号
- 役割説明
- Drag & Drop
- 受入形式チップ
- テンプレートダウンロード
- カラム定義の展開

## 7. Decision Studio

- 期待粗利増分順
- 商品ID、商品名、キーワード検索
- `operator_action`, `operator_cpc`, `operator_reason` のみ編集可能
- 異常・品質BLOCK時は出力不可
- 手動編集をsession stateで保持

## 8. Export Center

- 商品CPC
- キーワードCPC
- ロールバック
- 判断詳細
- バリデーション
- 次回state bundle
- Run profile

## 9. レスポンシブ

- Desktop: Relation Map 760〜780px高
- Tablet: Toolbarを横スクロール
- Mobile: タイトルブロック、ミニマップ、操作ヘルプを省略
- Inspector: 画面幅から32pxを差し引いた最大300px

## 10. 技術制約

- Graph UI: HTML5 Canvas / Vanilla JavaScript / CSS
- 埋め込み: Streamlit `st.iframe` のraw HTML
- 外部CDNなし
- 外部グラフUIライブラリなし
- 非ネットワークのTreemap、散布図、相関行列のみPlotlyを使用
- 表示ノードには上限を設定
- 判断・出力データは全件保持
