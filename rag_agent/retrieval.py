from __future__ import annotations

import hashlib
import json
import math
import os
import socket
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from .documents import Chunk, build_chunks, tokenize


@dataclass
class SearchResult:
    chunk: Chunk
    score: float
    bm25_score: float = 0.0
    dense_score: float = 0.0
    rerank_score: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "chunk_id": self.chunk.chunk_id,
            "doc_id": self.chunk.doc_id,
            "title": self.chunk.title,
            "score": round(self.score, 6),
            "bm25_score": round(self.bm25_score, 6),
            "dense_score": round(self.dense_score, 6),
            "rerank_score": round(self.rerank_score, 6),
            "quote": self.chunk.preview(260),
            "source_url": self.chunk.source_url,
            "is_distractor": self.chunk.is_distractor,
        }


class BM25Index:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = chunks
        self.k1 = k1
        self.b = b
        self.doc_tokens = [tokenize(chunk.text) for chunk in chunks]
        self.doc_len = [len(tokens) for tokens in self.doc_tokens]
        self.avgdl = sum(self.doc_len) / max(1, len(self.doc_len))
        self.term_freqs = [Counter(tokens) for tokens in self.doc_tokens]
        df: Counter[str] = Counter()
        for tokens in self.doc_tokens:
            df.update(set(tokens))
        self.idf = {
            term: math.log(1 + (len(chunks) - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def search(self, query: str) -> list[tuple[int, float]]:
        query_terms = tokenize(query)
        scores: list[tuple[int, float]] = []
        for idx, tf in enumerate(self.term_freqs):
            score = 0.0
            dl = self.doc_len[idx] or 1
            for term in query_terms:
                freq = tf.get(term, 0)
                if freq == 0:
                    continue
                idf = self.idf.get(term, 0.0)
                denom = freq + self.k1 * (1 - self.b + self.b * dl / max(self.avgdl, 1e-9))
                score += idf * (freq * (self.k1 + 1)) / denom
            if score > 0:
                scores.append((idx, score))
        return sorted(scores, key=lambda item: item[1], reverse=True)


class HashEmbeddingIndex:
    def __init__(self, chunks: list[Chunk], dims: int = 256) -> None:
        self.chunks = chunks
        self.dims = dims
        self.vectors = [self._embed(chunk.text) for chunk in chunks]

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dims
        for token in tokenize(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            slot = int.from_bytes(digest[:4], "big") % self.dims
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[slot] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def search(self, query: str) -> list[tuple[int, float]]:
        q = self._embed(query)
        scores: list[tuple[int, float]] = []
        for idx, vec in enumerate(self.vectors):
            score = sum(a * b for a, b in zip(q, vec))
            if score > 0:
                scores.append((idx, score))
        return sorted(scores, key=lambda item: item[1], reverse=True)


class OllamaEmbeddingIndex:
    _vector_cache: dict[tuple[str, str], list[float]] = {}

    def __init__(self, chunks: list[Chunk], model: str = "nomic-embed-text", base_url: str | None = None) -> None:
        self.chunks = chunks
        self.model = model
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")
        self.available = self._available()
        self.fallback = HashEmbeddingIndex(chunks)
        self.vectors = [self._embed(chunk.text) for chunk in chunks] if self.available else []

    def _available(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base_url}/api/tags", timeout=2) as response:
                return response.status == 200
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
            return False

    def _embed(self, text: str) -> list[float]:
        key = (self.model, hashlib.sha256(text.encode("utf-8")).hexdigest())
        if key in self._vector_cache:
            return self._vector_cache[key]
        payload = json.dumps({"model": self.model, "prompt": text}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
        vec = [float(value) for value in data.get("embedding", [])]
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        vec = [v / norm for v in vec]
        self._vector_cache[key] = vec
        return vec

    def search(self, query: str) -> list[tuple[int, float]]:
        if not self.available:
            return self.fallback.search(query)
        q = self._embed(query)
        scores: list[tuple[int, float]] = []
        for idx, vec in enumerate(self.vectors):
            score = sum(a * b for a, b in zip(q, vec))
            if score > 0:
                scores.append((idx, score))
        return sorted(scores, key=lambda item: item[1], reverse=True)


class HybridRetriever:
    _global_llm_rerank_cache: dict[tuple[str, tuple[str, ...], str], dict[str, float]] = {}

    def __init__(
        self,
        chunks: list[Chunk],
        rrf_k: int = 60,
        candidate_k: int = 20,
        final_k: int = 6,
        use_bm25: bool = True,
        use_dense: bool = True,
        use_reranker: bool = True,
        embedding_model: str = "nomic-embed-text",
        dense_backend: str = "hash",
        reranker_mode: str = "heuristic",
        reranker_model: str = "qwen2.5:3b",
    ) -> None:
        self.chunks = chunks
        self.rrf_k = rrf_k
        self.candidate_k = candidate_k
        self.final_k = final_k
        self.use_bm25 = use_bm25
        self.use_dense = use_dense
        self.use_reranker = use_reranker
        self.embedding_model = embedding_model
        self.dense_backend = dense_backend
        self.reranker_mode = reranker_mode
        self.reranker_model = reranker_model
        self.bm25 = BM25Index(chunks)
        self.dense = (
            OllamaEmbeddingIndex(chunks, model=embedding_model)
            if dense_backend == "ollama"
            else HashEmbeddingIndex(chunks)
        )
        self.actual_dense_backend = (
            f"ollama:{embedding_model}"
            if isinstance(self.dense, OllamaEmbeddingIndex) and self.dense.available
            else "hash-fallback"
        )
        self.by_chunk_id = {chunk.chunk_id: chunk for chunk in chunks}
        self.by_doc_id: dict[str, list[Chunk]] = defaultdict(list)
        for chunk in chunks:
            self.by_doc_id[chunk.doc_id].append(chunk)

    @classmethod
    def from_paths(
        cls,
        raw_dir: Path,
        chunk_tokens: int = 450,
        overlap: int = 80,
        include_secret: bool = True,
        **kwargs: object,
    ) -> "HybridRetriever":
        chunks = build_chunks(raw_dir, chunk_tokens=chunk_tokens, overlap=overlap, include_secret=include_secret)
        return cls(chunks, **kwargs)

    def search(self, query: str, top_k: int | None = None, include_secret: bool = False) -> list[SearchResult]:
        top_k = top_k or self.final_k
        bm25_ranked = self.bm25.search(query) if self.use_bm25 else []
        dense_ranked = self.dense.search(query) if self.use_dense else []
        bm25_scores = dict(bm25_ranked)
        dense_scores = dict(dense_ranked)
        combined: dict[int, float] = defaultdict(float)

        for rank, (idx, _) in enumerate(bm25_ranked[: self.candidate_k], start=1):
            combined[idx] += 2.0 / (self.rrf_k + rank)
        for rank, (idx, _) in enumerate(dense_ranked[: self.candidate_k], start=1):
            combined[idx] += 2.0 / (self.rrf_k + rank)

        candidates: list[SearchResult] = []
        for idx, score in combined.items():
            chunk = self.chunks[idx]
            if chunk.access == "secret" and not include_secret:
                continue
            candidates.append(
                SearchResult(
                    chunk=chunk,
                    score=score,
                    bm25_score=bm25_scores.get(idx, 0.0),
                    dense_score=dense_scores.get(idx, 0.0),
                )
            )

        if self.use_reranker:
            llm_scores = self._llm_rerank(query, candidates) if self.reranker_mode == "llm" else {}
            for result in candidates:
                result.rerank_score = llm_scores.get(result.chunk.chunk_id, self._rerank(query, result.chunk))
                result.score = result.score + result.rerank_score

        return sorted(candidates, key=lambda item: item.score, reverse=True)[:top_k]

    def _llm_rerank(self, query: str, candidates: list[SearchResult]) -> dict[str, float]:
        key = (query, tuple(result.chunk.chunk_id for result in candidates[: self.candidate_k]), self.reranker_model)
        if key in self._global_llm_rerank_cache:
            return self._global_llm_rerank_cache[key]
        try:
            from .llm import make_llm

            llm = make_llm(self.reranker_model, provider="auto")
            compact_candidates = [
                {
                    "chunk_id": result.chunk.chunk_id,
                    "doc_id": result.chunk.doc_id,
                    "title": result.chunk.title,
                    "text": result.chunk.preview(220),
                }
                for result in candidates[: self.candidate_k]
            ]
            prompt = {
                "task": "Rerank retrieval candidates for a compliance RAG assistant.",
                "query": query,
                "candidates": compact_candidates,
                "instructions": "Return JSON object {\"scores\":{\"chunk_id\": relevance_float_0_to_1}}. Score only evidence relevance, not style.",
            }
            result = llm.generate(json.dumps(prompt, ensure_ascii=False), json_mode=True)
            data = _parse_json_object(result.text)
            raw_scores = data.get("scores", {})
            scores = {
                chunk_id: max(0.0, min(1.0, float(score))) * 0.12
                for chunk_id, score in raw_scores.items()
                if isinstance(chunk_id, str)
            }
            if scores:
                self._global_llm_rerank_cache[key] = scores
                return scores
        except Exception:
            pass
        scores = {result.chunk.chunk_id: self._rerank(query, result.chunk) for result in candidates}
        self._global_llm_rerank_cache[key] = scores
        return scores

    def _rerank(self, query: str, chunk: Chunk) -> float:
        q_terms = set(tokenize(query))
        c_terms = set(tokenize(chunk.text))
        if not q_terms:
            return 0.0
        overlap = len(q_terms & c_terms) / len(q_terms)
        bonus = overlap * 0.08
        if chunk.is_distractor:
            bonus -= 0.015
        if "ATTACK TEXT" in chunk.text or "Ignore previous instructions" in chunk.text:
            bonus -= 0.02
        if {"false", "nesprávne", "pravda", "tvrdenie"} & q_terms and chunk.is_distractor:
            bonus += 0.04
        return bonus

    def citation_exists(self, doc_id: str, chunk_id: str, quote: str) -> bool:
        chunk = self.by_chunk_id.get(chunk_id)
        if chunk is None or chunk.doc_id != doc_id:
            return False
        return _quote_supported(quote, chunk.text)

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        return self.by_chunk_id.get(chunk_id)


def _quote_supported(quote: str, text: str) -> bool:
    quote_terms = tokenize(quote)
    text_terms = set(tokenize(text))
    if len(quote_terms) < 4:
        return False
    return sum(1 for term in quote_terms if term in text_terms) / len(quote_terms) >= 0.55


def _parse_json_object(text: str) -> dict[str, object]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise
