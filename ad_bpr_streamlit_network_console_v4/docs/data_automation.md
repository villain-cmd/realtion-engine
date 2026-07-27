# Data Automation / API・PostgreSQL

## 構成

CSVアップロードを必須工程から外し、次の流れで日次データを蓄積する。

```text
Google広告 / GA4 / Shopify / Yahoo!ショッピング / Airレジ
  → source別connector
  → source固有のraw dataset
  → 共通 business_metrics_daily
  → Supabase PostgreSQL
  → StreamlitでDB読込・可視化
```

CSVは商品マスタの初期投入、過去データ移行、APIで取得できない広告レポートの補完にだけ残す。

## なぜSupabaseか

- PostgreSQLなのでSheetsよりデータ型・一意制約・検索が安定する
- Data REST APIを使うため、Streamlit側にDB接続ドライバを追加しなくてよい
- 小規模店舗のデータ量ならFree枠の500MBで十分余裕がある
- `source_records` 1テーブルだけで開始できる

Freeプロジェクトは非アクティブ時に一時停止する。毎日同期が動いていれば通常は非アクティブにならないが、無停止保証が必要になった時だけ有料化を検討する。

## 初期設定

1. SupabaseでFreeプロジェクトを1つ作る
2. SQL Editorで `supabase/schema.sql` を実行する
3. Streamlit Cloud Secretsへ `.streamlit/secrets.example.toml` と同じ項目を登録する
4. GitHub Actionsの日次同期を使う場合は、同じ値をRepository Secretsへ登録する

`service_role_key` はサーバー専用で、公開リポジトリ、画面、ログ、クライアントJavaScriptへ出さない。
Shopify/Yahoo!は顧客名・住所・メールを取得せず、注文ID・商品・金額だけを選択する。Airレジも連携仕様のうち分析に必要な売上・商品項目だけのendpointを使い、不要な顧客情報を保存しない。

## 保存形式

`source_records` の主キーは `source + dataset + record_key`。

- `record_key`: 注文ID×明細ID、日付×広告グループ等の安定キー
- `record_hash`: 内容のハッシュ
- `payload`: source固有列を失わないJSONB
- `occurred_at`: 売上・広告実績の発生日
- `ingested_at`: DB取込日時

日次同期は直近3日を取り直し、Upsertする。注文ステータスや売上値の遅延更新を取り込みつつ重複を増やさない。

API別のraw dataset:

- `ga4_channel_daily`
- `google_ads_ad_group_daily`
- `shopify_order_lines`
- `yahoo_shopping_order_lines`
- `airregi_transactions`

加えて全sourceを `business_metrics_daily` の共通列へ変換する。入札計算専用の既存 `performance_input` は別契約のまま残し、無理に売上APIの行を広告キーワード実績へ見せかけない。

## API別の準備

### GA4

- Google Analytics Data APIを有効化
- GA4プロパティへサービスアカウントを閲覧者として追加
- `GA4_PROPERTY_ID`
- `GCP_SERVICE_ACCOUNT_JSON`

既定粒度は日付×デフォルトチャネルグループ。

### Google広告

- Google Ads Manager Account
- developer token
- customer ID
- OAuth client ID / secret / refresh token

既定粒度は日付×広告グループ。小規模運用では1日1回・3日lookbackで十分。

### Shopify

- Shopify Dev Dashboardで自社ストア用アプリを作成・インストール
- アプリのバージョン設定でAdmin APIの `read_orders` scopeを付与
- `SHOPIFY_SHOP_DOMAIN`
- `SHOPIFY_CLIENT_ID`
- `SHOPIFY_CLIENT_SECRET`

日次ジョブはClient Credentials Grantで24時間有効のアクセストークンを毎回取得し、
注文と明細をAdmin GraphQL APIでページング取得する。固定アクセストークンをSecretsへ
保存する必要はない。60日より古い注文を初回移行する場合は `read_all_orders` の追加承認が必要。

### Yahoo!ショッピング

- Yahoo!ショッピング向けClient IDを申請
- 注文APIの利用申請
- Seller ID
- Yahoo! ID連携 v2のClient ID / Secret / Refresh Token

注文APIは申請時に送信元グローバルIPの登録を求める。GitHub-hosted runnerのIPは固定ではないため、Yahoo!ショッピングを完全自動化する場合は店舗PC等にself-hosted runnerを置き、Repository Variable `SYNC_RUNNER=self-hosted` を設定する。ほか4sourceは通常のGitHub-hosted runnerで実行可能。

Refresh Tokenの期限が切れた場合は再認可が必要。公開鍵認証なしでは注文API利用時に最大12時間へ短縮されるため、本番自動同期では公開鍵とバージョンも設定する。公開鍵認証を使うと再認可間隔は最大4週間になる。

### Airレジ

Airレジ バックオフィスの `設定 → 他システム連携 → データ連携API` でAPIを有効化し、店舗ごとのAPIキーとAPIトークンを発行する。

公開FAQはキー発行を案内しているが、実エンドポイント・レスポンス項目は連携システム向け仕様に従う。このためBase URL、取引path、認証header、項目mappingをSecrets/Variablesで設定する。未公開URLはコードへ固定しない。

## 日次同期

`.github/workflows/daily-data-sync.yml` は毎日05:15 JSTに実行する。公開リポジトリの標準GitHub-hosted runnerは無料。

手動確認:

```bash
python scripts/sync_data_sources.py \
  --sources ga4,google_ads,shopify,airregi \
  --lookback-days 3 \
  --skip-unconfigured
```

本番Secretsをローカルファイルへ保存する必要はない。GitHub Actions SecretsまたはStreamlit Cloud Secretsで管理する。

## コスト目安

- Supabase Free: 0円
- GitHub Actions: 公開リポジトリの標準runnerは0円
- Streamlit Community Cloud: 既存無料運用を継続
- 各API: API自体の従量課金ではなく、利用申請・quota・契約条件に従う

現在の売上規模・EC件数ではデータ容量よりも、API申請とトークン更新の運用が主なコストになる。
