from __future__ import annotations

import pytest

from factory import similarity


@pytest.mark.integration
def test_real_model_tells_a_repeat_from_a_different_machine() -> None:
    alternator = (
        "A belt from the engine spins the alternator. Inside, a rotor electromagnet sweeps its "
        "magnetic field past copper stator coils, making alternating current. Diodes turn it into "
        "direct current that charges the battery."
    )
    reworded = (
        "The engine belt turns the alternator. Its spinning rotor is an electromagnet whose field "
        "passes the copper stator windings and induces alternating current, which diodes rectify "
        "into direct current for the battery."
    )
    jet = (
        "A jet engine pulls in air with a fan, compresses it, burns it with fuel and blasts the "
        "hot gas through a turbine that keeps the fan turning."
    )
    vectors = similarity.embed_texts([alternator, reworded, jet], _real_model())

    assert vectors[0] @ vectors[1] > 0.80
    assert vectors[0] @ vectors[2] < 0.50


def _real_model() -> similarity.Embedder:
    """The conftest fake replaces default_embedder, so the real model is loaded here directly."""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(similarity.MODEL_NAME, device="cpu")
    return lambda texts: model.encode(texts, normalize_embeddings=True)
