from __future__ import annotations

import numpy as np
from fakes.fake_embedder import fake_embed

from factory import similarity
from factory.media import qa


def test_long_texts_are_embedded_in_chunks_and_averaged() -> None:
    calls: list[list[str]] = []

    def embed(texts: list[str]) -> np.ndarray:
        calls.append(texts)
        return fake_embed(texts)

    long_text = " ".join(["rotor"] * 200 + ["stator"] * 200)  # 400 words → 3 chunks of ≤150

    vectors = similarity.embed_texts([long_text, "rotor"], embed)

    assert len(calls[0]) == 4  # 3 chunks + the short text
    assert vectors.shape[0] == 2
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)


def test_qa_similarity_check() -> None:
    assert qa.similarity(0.42, 0.80) == (True, "similarity 0.42 (at most 0.80)")
    ok, detail = qa.similarity(0.91, 0.80)
    assert ok is False and "needs at most 0.80" in detail
    assert qa.similarity(None, 0.80)[0] is None  # scripts written before task 1.13
