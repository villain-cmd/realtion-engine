# Verification Report v3

## 対象

- 楽天RPP 商品別実績: 494行
- 楽天RPP 商品×キーワード実績: 203行
- 現在設定CSV
- UI検証用商品マスタ
  - 商品名は現在設定CSVから取得
  - 粗利率35%を仮置き
  - 商品名から簡易カテゴリを付与

粗利率とカテゴリの仮値は、UI・ネットワーク構造・処理継続の検証にのみ使用し、本番入札値の妥当性評価には使用しない。

## Python test

```text
15 passed
```

対象:

- 既存意思決定エンジン
- 状態照合
- state bundle往復
- 3入力テンプレート
- カラム辞書ZIP
- 文字列正規化・概念語抽出
- 商品×キーワードネットワーク
- キーワード類似ネットワーク

## Streamlit AppTest

```text
exceptions:       0
navigation tabs:  6
Plotly charts:    8
download buttons: 19
```

3入力をAppTestからアップロードし、全タブのコードを実行した。

## データ品質

```text
status: PASS
score:  100.0 / 100
```

## 判断データ

```text
ITEM:     494
KEYWORD:  203
TOTAL:    697
```

## 商品×キーワードネットワーク

既定ホーム表示の上限条件で確認:

```text
product nodes: 44
keyword nodes: 84
edges:         128
clusters:      22
```

表示上限は全件判断を削除するものではない。Decision Studioと出力は697件を保持する。

## キーワード類似ネットワーク

```text
keyword nodes: 120
similarity edges: 116
clusters: 56
threshold: 0.34
```

## 商品類似ネットワーク

```text
product nodes: 73
similarity edges: 76
clusters: 34
threshold: 0.18
```

## 概念語の例

売上重み付き上位:

1. 収納
2. 布団
3. 着物
4. ケース
5. 敷布団

## 配布プレビュー

`ad_bpr_network_v3_verified_preview` に次を生成した。

- スタンドアロンHTML
- network nodes / edges
- keyword clusters / similarity edges
- term frequency
- verification summary JSON
