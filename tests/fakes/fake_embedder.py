"""A tiny offline stand-in for the embedding model: a hashed bag of words.

Texts that share most of their words get a high cosine similarity, unrelated texts a low one,
which is all the similarity logic needs to be tested without downloading the real model.
"""

from __future__ import annotations

import re
import zlib

import numpy as np

DIMENSIONS = 512


def fake_embed(texts: list[str]) -> np.ndarray:
    vectors = np.zeros((len(texts), DIMENSIONS))
    for row, text in enumerate(texts):
        for word in re.findall(r"[a-z]+", text.lower()):
            vectors[row, zlib.crc32(word.encode()) % DIMENSIONS] += 1
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)
