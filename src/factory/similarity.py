"""How alike a new script is to our recent ones, with local embeddings (ROADMAP 1.13, D-076).

An embedding turns a text into a vector that captures its meaning; the cosine similarity of two
vectors (1.0 = same meaning, ~0 = unrelated) says how alike the texts are. The model
(sentence-transformers all-MiniLM-L6-v2, Apache 2.0) runs on the CPU and is downloaded once.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

log = logging.getLogger("factory.similarity")

MODEL_NAME = "all-MiniLM-L6-v2"
CHUNK_WORDS = 150  # the model reads at most 256 word pieces; longer texts are embedded in chunks
SKIPPED_STATUSES = ("rejected", "archived")  # never went public, so they don't block (D-076)

# Texts in, one unit-length vector per text out.
Embedder = Callable[[list[str]], np.ndarray]

_model_embedder: Embedder | None = None


@dataclass(frozen=True)
class Match:
    """The most similar recent script: its score and content id (None = nothing to compare)."""

    score: float
    content_id: int | None


def default_embedder() -> Embedder:
    """The real model, loaded on first use (a few seconds) and kept for the rest of the run."""
    global _model_embedder
    if _model_embedder is None:
        from sentence_transformers import SentenceTransformer  # slow import: only when needed

        log.info("loading embedding model %s", MODEL_NAME, extra={"event": "embedder_load"})
        model = SentenceTransformer(MODEL_NAME, device="cpu")

        def embed(texts: list[str]) -> np.ndarray:
            return model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

        _model_embedder = embed
    return _model_embedder


def embed_texts(texts: list[str], embed: Embedder) -> np.ndarray:
    """One unit vector per text; a long text is the average of its chunks' vectors."""
    chunks: list[str] = []
    owners: list[int] = []
    for i, text in enumerate(texts):
        words = text.split() or [""]
        for start in range(0, len(words), CHUNK_WORDS):
            chunks.append(" ".join(words[start : start + CHUNK_WORDS]))
            owners.append(i)
    vectors = np.asarray(embed(chunks), dtype=float)
    owner_ids = np.array(owners)
    means = np.stack([vectors[owner_ids == i].mean(axis=0) for i in range(len(texts))])
    norms = np.linalg.norm(means, axis=1, keepdims=True)
    return means / np.where(norms == 0, 1, norms)


def recent_scripts(
    conn: sqlite3.Connection, channel_id: int, content_id: int, window: int
) -> list[sqlite3.Row]:
    """The current script of the channel's last `window` other items (rejected/archived skipped)."""
    return conn.execute(
        f"""
        SELECT s.content_id, s.body_text FROM scripts s JOIN contents c ON c.id = s.content_id
        WHERE s.is_current = 1 AND c.channel_id = ? AND c.id != ?
          AND c.status NOT IN ({",".join("?" * len(SKIPPED_STATUSES))})
        ORDER BY s.created_at DESC, s.id DESC
        LIMIT ?
        """,
        (channel_id, content_id, *SKIPPED_STATUSES, window),
    ).fetchall()


def closest(
    conn: sqlite3.Connection,
    channel_id: int,
    content_id: int,
    text: str,
    *,
    window: int,
    embed: Embedder | None = None,
) -> Match:
    """The recent script most similar to `text`."""
    others = recent_scripts(conn, channel_id, content_id, window)
    if not others:
        return Match(0.0, None)
    texts = [text, *(r["body_text"] or "" for r in others)]
    vectors = embed_texts(texts, embed or default_embedder())
    scores = vectors[1:] @ vectors[0]
    best = int(np.argmax(scores))
    return Match(round(float(scores[best]), 3), others[best]["content_id"])
