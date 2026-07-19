from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from functools import lru_cache
from typing import Iterable, Sequence

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

try:
    from janome.tokenizer import Tokenizer
except Exception:  # pragma: no cover - fallback for constrained environments
    Tokenizer = None  # type: ignore[assignment]


PUNCT_RE = re.compile(r"[\s\u3000,，.。/／・|｜+＋:：;；!?！？()（）\[\]【】{}「」『』<>＜＞\-_]+")
VALID_TERM_RE = re.compile(r"^[0-9A-Za-zぁ-んァ-ヶ一-龠々ー]+$")
JAPANESE_RE = re.compile(r"[ぁ-んァ-ヶ一-龠々ー]")

STOP_TERMS = {
    "する", "ある", "いる", "なる", "できる", "付き", "つき", "用", "対応", "タイプ", "セット",
    "商品", "楽天", "送料無料", "アストロ", "公式", "限定", "人気", "おすすめ", "ランキング",
    "サイズ", "カラー", "入り", "組", "枚", "個", "本", "cm", "mm", "kg", "new",
    "られる", "れる", "せる", "ため", "これ", "もの", "こと",
}


@lru_cache(maxsize=1)
def _tokenizer():
    return Tokenizer() if Tokenizer is not None else None


def normalize_phrase(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    text = unicodedata.normalize("NFKC", str(value)).strip().lower()
    text = PUNCT_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _fallback_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for token in PUNCT_RE.split(text):
        token = token.strip()
        if not token:
            continue
        if len(token) >= 2:
            tokens.append(token)
        # Japanese strings without spaces still benefit from stable character concepts.
        if JAPANESE_RE.search(token) and len(token) >= 5:
            tokens.extend(token[i : i + 3] for i in range(len(token) - 2))
    return tokens


def extract_terms(value: object, *, min_length: int = 2, max_terms: int = 12) -> list[str]:
    text = normalize_phrase(value)
    if not text:
        return []
    tokenizer = _tokenizer()
    candidates: list[str] = []
    if tokenizer is not None:
        try:
            for token in tokenizer.tokenize(text):
                pos = token.part_of_speech.split(",")[0]
                base = token.base_form if token.base_form not in {"*", ""} else token.surface
                base = normalize_phrase(base).replace(" ", "")
                if pos not in {"名詞", "動詞", "形容詞"}:
                    continue
                if len(base) < min_length or base in STOP_TERMS:
                    continue
                if not VALID_TERM_RE.match(base):
                    continue
                if base.isdigit():
                    continue
                candidates.append(base)
        except Exception:
            candidates = []
    if not candidates:
        candidates = _fallback_tokens(text)

    deduped: list[str] = []
    seen: set[str] = set()
    for term in candidates:
        term = normalize_phrase(term).replace(" ", "")
        if len(term) < min_length or term in STOP_TERMS or term in seen:
            continue
        seen.add(term)
        deduped.append(term)
        if len(deduped) >= max_terms:
            break
    return deduped


def enrich_keyword_text(df: pd.DataFrame, keyword_col: str = "keyword") -> pd.DataFrame:
    out = df.copy()
    out["keyword_normalized"] = out.get(keyword_col, pd.Series("", index=out.index)).map(normalize_phrase)
    out["terms"] = out.get(keyword_col, pd.Series("", index=out.index)).map(extract_terms)
    out["term_count"] = out["terms"].map(len)
    return out


def build_term_frequency(
    keyword_df: pd.DataFrame,
    *,
    keyword_col: str = "keyword",
    weight_col: str = "sales_attr",
    top_n: int = 30,
) -> pd.DataFrame:
    if keyword_df.empty or keyword_col not in keyword_df:
        return pd.DataFrame(columns=["term", "keyword_count", "weighted_value", "share_pct"])
    counter: Counter[str] = Counter()
    weighted: defaultdict[str, float] = defaultdict(float)
    source = keyword_df.copy()
    values = pd.to_numeric(source.get(weight_col, 1.0), errors="coerce").fillna(0.0)
    if values.sum() <= 0:
        values = pd.Series(1.0, index=source.index)
    for idx, text in source[keyword_col].items():
        terms = extract_terms(text)
        for term in set(terms):
            counter[term] += 1
            weighted[term] += float(values.loc[idx])
    rows = [
        {"term": term, "keyword_count": int(count), "weighted_value": float(weighted[term])}
        for term, count in counter.items()
    ]
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=["term", "keyword_count", "weighted_value", "share_pct"])
    total = float(out["weighted_value"].sum()) or 1.0
    out["share_pct"] = out["weighted_value"] / total * 100.0
    return out.sort_values(["weighted_value", "keyword_count"], ascending=False).head(top_n).reset_index(drop=True)


def build_term_cooccurrence(
    keyword_df: pd.DataFrame,
    *,
    keyword_col: str = "keyword",
    weight_col: str = "clicks",
    top_terms: int = 16,
) -> tuple[list[str], np.ndarray]:
    freq = build_term_frequency(keyword_df, keyword_col=keyword_col, weight_col=weight_col, top_n=top_terms)
    terms = freq["term"].tolist() if not freq.empty else []
    if not terms:
        return [], np.zeros((0, 0))
    term_index = {term: i for i, term in enumerate(terms)}
    matrix = np.zeros((len(terms), len(terms)), dtype=float)
    values = pd.to_numeric(keyword_df.get(weight_col, 1.0), errors="coerce").fillna(0.0)
    if values.sum() <= 0:
        values = pd.Series(1.0, index=keyword_df.index)
    for idx, text in keyword_df[keyword_col].items():
        row_terms = [t for t in set(extract_terms(text)) if t in term_index]
        weight = float(values.loc[idx])
        for i, left in enumerate(row_terms):
            li = term_index[left]
            matrix[li, li] += weight
            for right in row_terms[i + 1 :]:
                ri = term_index[right]
                matrix[li, ri] += weight
                matrix[ri, li] += weight
    # Convert to a normalized association score so large generic terms do not dominate.
    diag = np.diag(matrix).copy()
    for i in range(len(terms)):
        for j in range(len(terms)):
            if i == j:
                matrix[i, j] = 1.0 if diag[i] > 0 else 0.0
            elif diag[i] > 0 and diag[j] > 0:
                matrix[i, j] = matrix[i, j] / math.sqrt(diag[i] * diag[j])
    return terms, matrix


def _tfidf(texts: Sequence[str]):
    normalized = [normalize_phrase(x) for x in texts]
    vectorizer = TfidfVectorizer(
        analyzer="char",
        ngram_range=(2, 4),
        min_df=1,
        sublinear_tf=True,
        norm="l2",
    )
    matrix = vectorizer.fit_transform(normalized)
    return vectorizer, matrix


def keyword_similarity_edges(
    keyword_df: pd.DataFrame,
    *,
    keyword_col: str = "keyword",
    value_col: str = "sales_attr",
    max_keywords: int = 120,
    threshold: float = 0.34,
    top_k_per_keyword: int = 3,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if keyword_df.empty or keyword_col not in keyword_df:
        return pd.DataFrame(), pd.DataFrame(columns=["source", "target", "similarity"])
    work = keyword_df.copy()
    work[keyword_col] = work[keyword_col].map(normalize_phrase)
    work = work[work[keyword_col].ne("")]
    if work.empty:
        return pd.DataFrame(), pd.DataFrame(columns=["source", "target", "similarity"])
    agg_cols = {
        "clicks": "sum",
        "cost": "sum",
        "sales_attr": "sum",
        "orders_attr": "sum",
        "roas_attr_pct": "mean",
    }
    available = {k: v for k, v in agg_cols.items() if k in work.columns}
    grouped = work.groupby(keyword_col, as_index=False).agg(available)
    if value_col not in grouped.columns:
        grouped[value_col] = 1.0
    grouped[value_col] = pd.to_numeric(grouped[value_col], errors="coerce").fillna(0.0)
    grouped = grouped.sort_values(value_col, ascending=False).head(max_keywords).reset_index(drop=True)
    if len(grouped) < 2:
        grouped["node_id"] = "K|" + grouped[keyword_col].astype(str)
        return grouped, pd.DataFrame(columns=["source", "target", "similarity"])

    _, matrix = _tfidf(grouped[keyword_col].tolist())
    similarity = cosine_similarity(matrix)
    rows: list[dict[str, object]] = []
    for i in range(len(grouped)):
        order = np.argsort(similarity[i])[::-1]
        used = 0
        for j in order:
            if i == j:
                continue
            score = float(similarity[i, j])
            if score < threshold:
                break
            left = str(grouped.loc[i, keyword_col])
            right = str(grouped.loc[j, keyword_col])
            source, target = sorted((left, right))
            rows.append({"source": f"K|{source}", "target": f"K|{target}", "similarity": score})
            used += 1
            if used >= top_k_per_keyword:
                break
    edges = pd.DataFrame(rows).drop_duplicates(["source", "target"], keep="first") if rows else pd.DataFrame(columns=["source", "target", "similarity"])
    grouped["node_id"] = "K|" + grouped[keyword_col].astype(str)
    return grouped, edges.sort_values("similarity", ascending=False).reset_index(drop=True)


def paired_text_similarity(left: Iterable[object], right: Iterable[object]) -> np.ndarray:
    left_texts = [normalize_phrase(x) for x in left]
    right_texts = [normalize_phrase(x) for x in right]
    if len(left_texts) != len(right_texts):
        raise ValueError("left and right must have the same length")
    if not left_texts:
        return np.array([], dtype=float)
    corpus = left_texts + right_texts
    try:
        _, matrix = _tfidf(corpus)
    except ValueError:
        return np.zeros(len(left_texts), dtype=float)
    n = len(left_texts)
    left_m = matrix[:n]
    right_m = matrix[n:]
    scores = np.asarray(left_m.multiply(right_m).sum(axis=1)).reshape(-1)
    return np.clip(scores, 0.0, 1.0)
