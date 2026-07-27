from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src.connectors import ConnectorConfigurationError  # noqa: E402
from src.database import SupabaseStore  # noqa: E402
from src.ingestion import SOURCE_LABELS, fetch_source, persist_result  # noqa: E402


ALL_SOURCES = ("ga4", "google_ads", "shopify", "yahoo_shopping", "airregi")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _json_env(name: str) -> dict[str, Any]:
    raw = env(name)
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"{name} はJSONオブジェクトで指定してください。")
    return parsed


def source_config(source: str) -> dict[str, Any]:
    if source == "ga4":
        return {"property_id": env("GA4_PROPERTY_ID")}
    if source == "google_ads":
        return {
            "customer_id": env("GOOGLE_ADS_CUSTOMER_ID"),
            "login_customer_id": env("GOOGLE_ADS_LOGIN_CUSTOMER_ID"),
            "developer_token": env("GOOGLE_ADS_DEVELOPER_TOKEN"),
            "client_id": env("GOOGLE_ADS_CLIENT_ID"),
            "client_secret": env("GOOGLE_ADS_CLIENT_SECRET"),
            "refresh_token": env("GOOGLE_ADS_REFRESH_TOKEN"),
        }
    if source == "shopify":
        return {
            "shop_domain": env("SHOPIFY_SHOP_DOMAIN"),
            "access_token": env("SHOPIFY_ACCESS_TOKEN"),
            "api_version": env("SHOPIFY_API_VERSION", "2026-07"),
        }
    if source == "yahoo_shopping":
        return {
            "seller_id": env("YAHOO_SHOPPING_SELLER_ID"),
            "access_token": env("YAHOO_SHOPPING_ACCESS_TOKEN"),
            "client_id": env("YAHOO_SHOPPING_CLIENT_ID"),
            "client_secret": env("YAHOO_SHOPPING_CLIENT_SECRET"),
            "refresh_token": env("YAHOO_SHOPPING_REFRESH_TOKEN"),
            "public_key": env("YAHOO_SHOPPING_PUBLIC_KEY"),
            "public_key_version": env("YAHOO_SHOPPING_PUBLIC_KEY_VERSION") or "1",
            "order_endpoint": env("YAHOO_SHOPPING_ORDER_ENDPOINT"),
        }
    if source == "airregi":
        return {
            "base_url": env("AIRREGI_BASE_URL"),
            "transactions_path": env("AIRREGI_TRANSACTIONS_PATH"),
            "api_key": env("AIRREGI_API_KEY"),
            "api_token": env("AIRREGI_API_TOKEN"),
            "api_key_header": env("AIRREGI_API_KEY_HEADER", "X-API-Key"),
            "api_token_header": env("AIRREGI_API_TOKEN_HEADER", "Authorization"),
            "api_token_prefix": os.environ.get("AIRREGI_API_TOKEN_PREFIX", "Bearer "),
            "record_id_field": env("AIRREGI_RECORD_ID_FIELD", "transactionId"),
            "date_field": env("AIRREGI_DATE_FIELD"),
            "occurred_at_field": env("AIRREGI_OCCURRED_AT_FIELD"),
            "product_id_field": env("AIRREGI_PRODUCT_ID_FIELD"),
            "product_name_field": env("AIRREGI_PRODUCT_NAME_FIELD"),
            "quantity_field": env("AIRREGI_QUANTITY_FIELD"),
            "amount_field": env("AIRREGI_AMOUNT_FIELD"),
        }
    raise ValueError(f"未対応のデータソースです: {source}")


def required_configuration_present(source: str, config: dict[str, Any]) -> bool:
    required = {
        "ga4": ("property_id",),
        "google_ads": ("customer_id", "developer_token"),
        "shopify": ("shop_domain", "access_token"),
        "yahoo_shopping": ("seller_id",),
        "airregi": ("base_url", "transactions_path", "api_key", "api_token"),
    }[source]
    if not all(str(config.get(key, "")).strip() for key in required):
        return False
    if source == "yahoo_shopping":
        direct = bool(str(config.get("access_token", "")).strip())
        refresh = all(
            str(config.get(key, "")).strip()
            for key in ("client_id", "client_secret", "refresh_token")
        )
        return direct or refresh
    if source == "google_ads":
        oauth = all(
            str(config.get(key, "")).strip()
            for key in ("client_id", "client_secret", "refresh_token")
        )
        return oauth
    return True


def parse_args() -> argparse.Namespace:
    yesterday = date.today() - timedelta(days=1)
    parser = argparse.ArgumentParser(description="APIデータをSupabaseへ日次同期します。")
    parser.add_argument(
        "--sources",
        default=env("SYNC_SOURCES", ",".join(ALL_SOURCES)),
        help="カンマ区切り: " + ",".join(ALL_SOURCES),
    )
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default=yesterday.isoformat())
    parser.add_argument("--lookback-days", type=int, default=int(env("SYNC_LOOKBACK_DAYS", "3")))
    parser.add_argument("--skip-unconfigured", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    end_date = date.fromisoformat(args.end_date)
    start_date = (
        date.fromisoformat(args.start_date)
        if args.start_date
        else end_date - timedelta(days=max(args.lookback_days - 1, 0))
    )
    sources = [source.strip() for source in args.sources.split(",") if source.strip()]
    unknown = sorted(set(sources) - set(ALL_SOURCES))
    if unknown:
        raise ValueError("未対応のsource: " + ", ".join(unknown))

    store = SupabaseStore(
        url=env("SUPABASE_URL"),
        service_role_key=env("SUPABASE_SERVICE_ROLE_KEY"),
        table=env("SUPABASE_TABLE", "source_records"),
    )
    service_account_info = _json_env("GCP_SERVICE_ACCOUNT_JSON")
    failed: list[str] = []
    for source in sources:
        config = source_config(source)
        configured = required_configuration_present(source, config)
        if source == "ga4" and not service_account_info:
            configured = False
        if not configured:
            if args.skip_unconfigured:
                print(f"SKIP {source}: configuration is incomplete")
                continue
            failed.append(source)
            print(f"ERROR {source}: configuration is incomplete", file=sys.stderr)
            continue
        try:
            result = fetch_source(
                source=source,
                config=config,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                service_account_info=service_account_info,
            )
            run = persist_result(store, result)
            print(
                f"OK {SOURCE_LABELS[source]}: {run.persisted_rows} rows "
                f"({start_date.isoformat()}..{end_date.isoformat()})"
            )
        except (ConnectorConfigurationError, Exception) as exc:
            failed.append(source)
            print(f"ERROR {source}: {type(exc).__name__}: {exc}", file=sys.stderr)
    if failed:
        print("Failed sources: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
