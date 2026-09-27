"""Retrieval utilities for the offline ``LocalMemoryStore``.

The local store approximates Hindsight's fused TEMPR retrieval so the app behaves
sensibly offline. Four scorers + a fusion step, all pure-Python (no model download,
no third-party dependency), deterministic (seedless hashing) so demos reproduce:

  1. semantic   — hashed word+char-trigram bag embedding + cosine similarity.
  2. keyword    — BM25 over tokenized memory text.
  3. entity     — overlap of extracted identifier entities (services, error stems, tags).
  4. temporal   — recency decay on memory ``created_at`` (newer resolved incidents rank up).

Fusion: weighted sum with keyword max-scaled to [0,1] (the other three are already
bounded in [0,1]); weights semantic .45, keyword .30, entity .15, temporal .10. A
per-strategy contribution breakdown is returned for UI explainability.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Iterable

_TOKEN_RE = re.compile(r"[a-z0-9_]+")

# Fusion weights (documented + asserted by tests). Sum to 1.0.
WEIGHTS: dict[str, float] = {
    "semantic": 0.45,
    "keyword": 0.30,
    "entity": 0.15,
    "temporal": 0.10,
}

_EMBED_DIM = 1024
_DAY_MS = 86_400_000
_TEMPORAL_HALF_LIFE_DAYS = 45.0

# very common english/log words that carry little retrieval signal as "entities"
_STOP = {
    "the", "and", "for", "with", "after", "from", "into", "that", "this", "was",
    "were", "has", "had", "not", "but", "via", "per", "inc", "svc", "api",
    "error", "warn", "info", "fatal", "incident", "symptom", "root", "cause",
    "fix", "outcome", "resolved", "resolver", "runbook", "mttr", "min", "tags",
    "signature", "sev1", "sev2", "sev3",
}


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric/underscore tokenizer."""
    return _TOKEN_RE.findall((text or "").lower())


def _char_ngrams(token: str, n: int = 3) -> Iterable[str]:
    if len(token) < n:
        yield token
        return
    for i in range(len(token) - n + 1):
        yield token[i : i + n]


def _hash(feature: str) -> int:
    """Deterministic non-negative hash (blake2b — stable across processes/runs)."""
    h = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(h, "big")


def embed(text: str, dim: int = _EMBED_DIM) -> list[float]:
    """Fixed-dim, L2-normalized hashed bag of word tokens + char trigrams.

    Word tokens are weighted more heavily than character trigrams so that genuinely
    unrelated phrases score low while morphological variants still overlap. Signed
    hashing spreads collisions. Deterministic: no random seed, no model.
    """
    vec = [0.0] * dim
    tokens = tokenize(text)
    for tok in tokens:
        # word unigram — the dominant signal
        idx = _hash("w:" + tok) % dim
        sign = 1.0 if (_hash("s:" + tok) & 1) else -1.0
        vec[idx] += 2.0 * sign
        # character trigrams — fuzzy lexical similarity
        for g in _char_ngrams(tok):
            gi = _hash("c:" + g) % dim
            gs = 1.0 if (_hash("cs:" + g) & 1) else -1.0
            vec[gi] += 1.0 * gs
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors, clamped to [0, 1]."""
    dot = sum(x * y for x, y in zip(a, b))
    # inputs are L2-normalized, so dot is already the cosine; clamp negatives to 0.
    return max(0.0, min(1.0, dot))


def semantic_score(a: str, b: str) -> float:
    """Semantic similarity of two texts in [0, 1] (1.0 for identical text)."""
    return cosine(embed(a), embed(b))


class Bm25:
    """Okapi BM25 over a fixed corpus of tokenized documents."""

    def __init__(self, corpus_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.corpus = corpus_tokens
        self.n = len(corpus_tokens)
        self.doc_len = [len(d) for d in corpus_tokens]
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 0.0
        self.tf: list[dict[str, int]] = []
        df: dict[str, int] = {}
        for doc in corpus_tokens:
            counts: dict[str, int] = {}
            for t in doc:
                counts[t] = counts.get(t, 0) + 1
            self.tf.append(counts)
            for t in counts:
                df[t] = df.get(t, 0) + 1
        # BM25+ idf (always positive) so common terms never drive the score negative
        self.idf = {
            t: math.log(1 + (self.n - freq + 0.5) / (freq + 0.5)) for t, freq in df.items()
        }

    def score(self, query_tokens: list[str], doc_index: int) -> float:
        if not self.n or self.avgdl == 0:
            return 0.0
        counts = self.tf[doc_index]
        dl = self.doc_len[doc_index]
        score = 0.0
        for t in query_tokens:
            if t not in counts:
                continue
            idf = self.idf.get(t, 0.0)
            freq = counts[t]
            denom = freq + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
            score += idf * (freq * (self.k1 + 1)) / denom
        return score


def temporal_decay(age_ms: float, half_life_days: float = _TEMPORAL_HALF_LIFE_DAYS) -> float:
    """Recency weight in (0, 1]: 1.0 at age 0, halving every ``half_life_days``."""
    if age_ms <= 0:
        return 1.0
    age_days = age_ms / _DAY_MS
    return float(0.5 ** (age_days / half_life_days))


def extract_entities(text: str) -> set[str]:
    """Identifier-like entities: signature stems, service names, and typed tokens.

    Keeps tokens that look like machine identifiers (contain ``_`` or a digit) or that
    are distinctive words (length >= 4, not a stopword). This favours error signatures
    (``http_5xx``, ``conn_pool``), status codes (``5xx``, ``502``) and service stems.
    """
    ents: set[str] = set()
    for tok in tokenize(text):
        if tok in _STOP:
            continue
        if "_" in tok or any(c.isdigit() for c in tok) or len(tok) >= 5:
            ents.add(tok)
    return ents


def entity_overlap(query_entities: set[str], doc_entities: set[str]) -> float:
    """Recall-oriented overlap in [0, 1]: fraction of query entities present in doc."""
    if not query_entities:
        return 0.0
    inter = query_entities & doc_entities
    return len(inter) / len(query_entities)


def fuse(scores_by_strategy: dict[str, float]) -> tuple[float, dict[str, float]]:
    """Weighted fusion of per-strategy scores (each expected in [0, 1]).

    Returns ``(fused_score, breakdown)`` where ``breakdown[strategy]`` is that
    strategy's weighted contribution to the fused score (for UI explainability).
    """
    fused = 0.0
    breakdown: dict[str, float] = {}
    for strat, weight in WEIGHTS.items():
        s = float(scores_by_strategy.get(strat, 0.0))
        s = max(0.0, min(1.0, s))
        contrib = weight * s
        breakdown[strat] = round(contrib, 6)
        fused += contrib
    return max(0.0, min(1.0, fused)), breakdown


def rank(
    query: str,
    docs: list[dict[str, Any]],
    *,
    now_ms: int,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Score ``docs`` against ``query`` with the fused retriever.

    ``docs`` is a list of dicts each carrying at least ``content`` and ``created_at``.
    Returns up to ``top_k`` result dicts (highest fused first), each:
        {"index": int, "score": float, "signals": {...}, "breakdown": {...}}
    ``signals`` holds the raw per-strategy scores; ``breakdown`` the weighted parts.
    """
    if not docs:
        return []

    q_vec = embed(query)
    q_tokens = tokenize(query)
    q_ents = extract_entities(query)

    corpus_tokens = [tokenize(d.get("content", "")) for d in docs]
    bm25 = Bm25(corpus_tokens)

    raw: list[dict[str, float]] = []
    for i, d in enumerate(docs):
        content = d.get("content", "")
        sem = cosine(q_vec, embed(content))
        kw = bm25.score(q_tokens, i)
        ent = entity_overlap(q_ents, extract_entities(content))
        created = d.get("created_at") or now_ms
        temporal = temporal_decay(max(0, now_ms - int(created)))
        raw.append({"semantic": sem, "keyword": kw, "entity": ent, "temporal": temporal})

    # keyword is unbounded — max-scale it into [0, 1] across candidates.
    kw_max = max((r["keyword"] for r in raw), default=0.0)
    results: list[dict[str, Any]] = []
    for i, r in enumerate(raw):
        signals = dict(r)
        signals["keyword"] = (r["keyword"] / kw_max) if kw_max > 0 else 0.0
        fused, breakdown = fuse(signals)
        results.append(
            {"index": i, "score": fused, "signals": signals, "breakdown": breakdown}
        )

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]
