# Profit Network Console v4

楽天RPP / Yahooショッピングの広告実績を、**商品管理番号 × キーワードの関係構造**として可視化し、粗利制約付きCPC演算、手動補正、入稿CSV、次回継続状態までを1つのStreamlitアプリで処理します。

外部AI API、LLM API、APIキー、常設DBは不要です。GitHubからStreamlit Community Cloudへデプロイできます。

## v4の主変更

### 1. Relation Mapを独自Canvasエンジンへ置換

ネットワーク描画にPlotly等のグラフUIライブラリを使用しません。`src/relationship_canvas.py` が、HTML5 Canvas / CSS / Vanilla JavaScriptだけで次を実装します。

- パン・ホイールズーム
- ノードドラッグ
- マウスオンの高コントラストツールチップ
- クリック選択と固定インスペクタ
- ダブルクリックによる1階層フォーカス
- 商品管理番号 / キーワード検索
- 判定 / ROAS / クラスタ / 種別の色切替
- ラベル密度切替
- ラベル衝突回避
- Fit to view
- 全画面表示
- PNG保存
- ミニマップ
- 接続ノード一覧

Relation Mapの既定の視覚変数は次です。

- 商品ノードの表示名: 商品管理番号
- キーワードノードの表示名: キーワード
- 円サイズ: 属性売上
- 色: 推奨判定
- 線: 商品 × キーワードの実績接続
- 線幅: 接続売上規模

### 2. コントラスト修正

- メイン画面の補助文字を濃色化
- タブ、キャプション、入力ラベル、Expanderの文字色を明示
- サイドバーのラベルと入力値を個別に配色
- HTMLカードがMarkdownコードブロックとして露出する問題を修正
- KPI、Hub一覧、入力カードの文字コントラストを改善

### 3. 3入力テンプレート

アプリ上部から次を個別またはZIPで取得できます。

- 実績レポート
- 現在の入札設定
- 商品マスタ
- 全カラム辞書

## 画面構成

1. `NETWORK HOME`
   - 商品管理番号 × キーワードのRelation Map
   - 売上規模、判定、ROAS、クラスタ、集中度
   - Treemap、散布図、Spearman相関行列
2. `KEYWORD MINE`
   - 独自Canvasによるキーワード類似グラフ
   - 形態素分解、概念語頻度、共起分析
3. `PRODUCT MAP`
   - 独自Canvasによる商品類似グラフ
   - 選択商品の接続キーワード明細
4. `DECISION STUDIO`
   - CPC演算結果
   - ACCEPT / MODIFY / LOCK / FORCE_STOP / REJECT
5. `EXPORT CENTER`
   - 商品CPC、キーワードCPC、ロールバック、判断詳細
   - 次回用 `state_bundle.zip`
6. `DATA QUALITY`
   - ファイル認識、文字コード、期間、欠損、重複、行数急減
   - 3入力のカラム辞書

## 3入力テンプレート

`templates/` に次を同梱しています。

- `01_performance_input_template_utf8.csv`
- `02_current_bid_setting_template_cp932.csv`
- `03_product_master_template_utf8.csv`
- `input_column_dictionary_utf8.csv`
- `input_templates_pack.zip`

## ローカル実行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## GitHub → Streamlit Community Cloud

1. このディレクトリの内容をGitHubリポジトリのルートへpushします。
2. Streamlit Community Cloudで対象リポジトリとbranchを選択します。
3. Main file pathを `streamlit_app.py` にします。
4. Secretsは不要です。

## デザインプレビュー生成

```bash
python scripts/build_design_preview.py \
  --reports item_report.csv keyword_report.csv \
  --setting rpp_setting.csv \
  --product-master product_master.csv \
  --out-dir ./preview
```

生成物:

- `design_preview.html`
- `keyword_graph_preview.html`
- `product_graph_preview.html`
- ノード / エッジCSV

## 毎回保存するもの

入稿CSVと同時に `state_bundle_<run_id>.zip` を保存し、次回の左サイドバーからアップロードします。

CSVをダウンロードしただけでは適用済みにしません。次回の現在設定CSVに前回提案値が存在した時点で `CONFIRMED` へ遷移します。

## バッチ実行

```bash
python scripts/run_batch.py \
  --reports item_report.csv keyword_report.csv \
  --setting rpp_setting.csv \
  --product-master product_master.csv \
  --state previous_state_bundle.zip \
  --policy config/default_policy.json \
  --out-dir ./outputs
```

## 安全条件

- 増額は手動承認。
- 減額・停止は条件を満たす場合だけ初期値 `ACCEPT`。
- アプリから媒体へ自動送信しない。
- 品質BLOCKまたは指標急変時は入稿対象外。
- 粗利率未登録は仮定値で試算できるが、既定では入稿不可。
- 手動変更値は次回の計算基準として引き継ぐ。

詳細:

- `docs/relationship_canvas_spec.md`
- `docs/reconciled_algorithm_spec.md`
- `docs/text_mining_network_logic.md`
- `docs/input_templates.md`
- `docs/stateful_operation.md`
