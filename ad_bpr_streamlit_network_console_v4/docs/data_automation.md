# Data Automation / API・Google Sheets DB

## 目的

CSVアップロードを必須工程から外し、次の対話フローでデータを扱う。

1. APIまたはCSVから取得
2. 取得結果を画面でプレビュー
3. Google Sheets DBへ確定保存
4. DBの `performance_input` / `bid_settings` / `product_master` を読み込んで分析

CSVは、初期マスタ・過去データ・API非対応データを投入する任意オプションとして残す。

## Google Sheets DB

既定テーブル:

- `performance_input`: 既存の標準実績CSVと同じ列
- `bid_settings`: 楽天RPP現在設定と同じ列
- `product_master`: 商品マスタと同じ列
- `ga4_channel_daily`: GA4 APIの取得結果
- `google_ads_ad_group_daily`: Google広告 APIの取得結果
- `airregi_transactions`: Airレジ データ連携APIの取得結果

API・CSVから保存する行には `_source`、`_dataset`、`_ingested_at`、`_record_hash` を付与する。既定のUpsertは `_record_hash` で同一レコードを重複保存しない。

サービスアカウントのメールアドレスを対象スプレッドシートへ編集者として共有すること。

## GA4

Google Analytics Data APIの `runReport` を使用する。既定取得単位は日付×セッションのデフォルトチャネルグループ、指標はセッション・ユーザー・新規ユーザー・キーイベント・総収益。

必要設定:

- Google Analytics Data APIの有効化
- GA4プロパティにサービスアカウントを追加
- `[ga4].property_id`
- `[gcp_service_account]`

## Google広告

Google Ads APIのGAQLを使い、日次×広告グループで表示回数、クリック、費用、コンバージョン、コンバージョン値を取得する。

必要設定:

- developer token
- customer ID（MCC経由時は login customer IDも）
- OAuth 2.0 client ID / secret / refresh token、または公式に対応するADC・サービスアカウント設定

## Airレジ

Airレジ バックオフィスの `設定 → 他システム連携 → データ連携API` で、店舗ごとにAPI利用を有効化し、APIキーとAPIトークンを発行する。

Airレジの公開FAQはキー発行と連携対象データを案内しているが、連携システムが利用するエンドポイント仕様は接続先側の案内に従う構成になっている。このためコードには未公開URLを固定せず、Base URL、取引パス、認証ヘッダー名、日付・カーソルパラメータをSecretsで設定する。

## セキュリティ

- 実値は `.streamlit/secrets.toml` またはStreamlit Cloud Secretsへ登録する。
- `.streamlit/secrets.toml` はgit管理しない。
- APIトークンを画面やログへ出さない。
- 漏洩時はAirレジ バックオフィス等の発行元で失効・再発行する。

