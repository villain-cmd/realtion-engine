from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Optional

import pandas as pd

ENCODING_CANDIDATES = ("utf-8-sig", "cp932", "shift_jis", "utf-8")
DATE_RANGE_RE = re.compile(r"(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})日?\s*[～~-]\s*(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})日?")
EXECUTION_RE = re.compile(r"実行日時\s*:\s*(20\d{2})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})")


@dataclass
class LoadedCsv:
    name: str
    dataframe: pd.DataFrame
    encoding: str
    header_row_index: int
    file_type: str
    warnings: list[str]
    sha256: str
    period_start: str | None = None
    period_end: str | None = None
    executed_at: str | None = None

    def profile(self) -> dict:
        data = asdict(self)
        data.pop("dataframe", None)
        data["rows"] = int(len(self.dataframe))
        data["columns"] = list(map(str, self.dataframe.columns))
        return data


def _read_bytes(file_or_path: str | Path | bytes | BinaryIO) -> tuple[str, bytes]:
    if isinstance(file_or_path, bytes):
        return "uploaded.csv", file_or_path
    if isinstance(file_or_path, (str, Path)):
        path = Path(file_or_path)
        return path.name, path.read_bytes()
    name = getattr(file_or_path, "name", "uploaded.csv")
    try:
        file_or_path.seek(0)
    except Exception:
        pass
    return str(name), file_or_path.read()


def decode_csv_bytes(raw: bytes) -> tuple[str, str]:
    last_error: Optional[Exception] = None
    for enc in ENCODING_CANDIDATES:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError as exc:
            last_error = exc
    raise ValueError(f"CSV文字コードを判定できません: {last_error}")


def detect_header_row(text: str) -> int:
    lines = text.splitlines()
    for idx, line in enumerate(lines[:80]):
        if "商品管理番号" in line and ("コントロールカラム" in line or "クリック数" in line):
            return idx
    # Normalized CSV and supporting master files start at row zero.
    return 0


def parse_metadata(text: str) -> dict[str, str | None]:
    period_start = period_end = executed_at = None
    head = "\n".join(text.splitlines()[:30])
    m = EXECUTION_RE.search(head)
    if m:
        executed_at = datetime(*map(int, m.groups())).isoformat(sep=" ")
    m = DATE_RANGE_RE.search(head)
    if m:
        ys, ms, ds, ye, me, de = map(int, m.groups())
        period_start = datetime(ys, ms, ds).date().isoformat()
        period_end = datetime(ye, me, de).date().isoformat()
    return {"period_start": period_start, "period_end": period_end, "executed_at": executed_at}


def classify_csv(df: pd.DataFrame) -> str:
    cols = set(map(str, df.columns))
    if {"common_product_id", "product_id", "gross_margin_rate"}.issubset(cols):
        return "product_master"
    if {"platform", "entity_type", "product_id", "clicks", "cost"}.issubset(cols):
        return "normalized_performance"
    if {"override_mode", "product_id", "active"}.issubset(cols):
        return "manual_overrides"
    if {"商品名", "価格", "商品URL", "商品CPC"}.issubset(cols) and "クリック数(合計)" not in cols:
        return "rakuten_rpp_setting"
    if "キーワード" in cols and "クリック数(合計)" in cols:
        return "rakuten_rpp_keyword_report"
    if "商品管理番号" in cols and "クリック数(合計)" in cols:
        return "rakuten_rpp_item_report"
    return "generic_csv"


def read_csv_flexible(file_or_path: str | Path | bytes | BinaryIO) -> LoadedCsv:
    name, raw = _read_bytes(file_or_path)
    text, encoding = decode_csv_bytes(raw)
    header_idx = detect_header_row(text)
    metadata = parse_metadata(text)
    data_text = "\n".join(text.splitlines()[header_idx:])
    if not data_text.strip():
        raise ValueError(f"{name}: ヘッダー検出後のCSVが空です")
    try:
        df = pd.read_csv(io.StringIO(data_text), dtype=str, keep_default_na=False)
    except Exception as exc:
        raise ValueError(f"{name}: CSV解析に失敗しました（header={header_idx}）: {exc}") from exc
    df.columns = [str(c).strip().replace("\ufeff", "") for c in df.columns]
    df = df.replace({"": pd.NA}).dropna(how="all").reset_index(drop=True)
    warnings: list[str] = []
    if df.empty:
        warnings.append("データ行が0件です。")
    file_type = classify_csv(df)
    return LoadedCsv(
        name=name,
        dataframe=df,
        encoding=encoding,
        header_row_index=header_idx,
        file_type=file_type,
        warnings=warnings,
        sha256=hashlib.sha256(raw).hexdigest(),
        **metadata,
    )


def coerce_numeric(series: pd.Series | None) -> pd.Series:
    if series is None:
        return pd.Series(dtype="float64")
    cleaned = (
        series.astype("string")
        .str.strip()
        .replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "-": pd.NA, "－": pd.NA})
        .str.replace(",", "", regex=False)
        .str.replace("円", "", regex=False)
        .str.replace("%", "", regex=False)
    )
    return pd.to_numeric(cleaned, errors="coerce")


def normalize_text(series: pd.Series | None) -> pd.Series:
    if series is None:
        return pd.Series(dtype="string")
    return series.fillna("").astype(str).str.replace("\u3000", " ", regex=False).str.strip()


def dataframe_to_csv_bytes(df: pd.DataFrame, encoding: str = "utf-8-sig") -> bytes:
    text = df.to_csv(index=False, lineterminator="\r\n")
    return text.encode(encoding, errors="replace")


def json_bytes(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str).encode("utf-8")


def stable_json_hash(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
