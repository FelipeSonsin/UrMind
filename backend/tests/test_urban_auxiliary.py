"""Sinal auxiliar de categorias urbanas: zero-shot, só sugestão para revisão humana."""

from __future__ import annotations

import numpy as np
import pytest

from app.services import photo_reference
from app.services.photo_reference import (
    URBAN_AUX_BACKGROUND,
    URBAN_AUX_PROMPTS,
    ReferenceUnavailable,
    urban_aux_scores,
    urban_auxiliary_safe,
    validate_urban_aux_manifest,
)


def _unit(index: int, size: int = 8) -> np.ndarray:
    vector = np.zeros(size, dtype=np.float32)
    vector[index] = 1.0
    return vector


PROMPTS = {"URMIND_FALLEN_TREE": ("tree",), "URMIND_FLOODED_ROAD": ("flood", "water")}
BACKGROUND = ("street",)
VECTORS = np.stack([_unit(0), _unit(1), _unit(2), _unit(3)])  # tree, flood, water, street


def test_category_that_matches_the_photo_is_suggested_and_others_are_not() -> None:
    embedding = (0.9 * _unit(1) + 0.1 * _unit(3))[None, :]
    result = urban_aux_scores(embedding, VECTORS, PROMPTS, BACKGROUND)
    assert result["status"] == "UNCALIBRATED_ADVISORY"
    assert [item["code"] for item in result["suggestions"]] == ["URMIND_FLOODED_ROAD"]
    flood = result["scores"]["URMIND_FLOODED_ROAD"]
    assert flood["probability"] > 0.9 and flood["margin"] > 0
    assert result["scores"]["URMIND_FALLEN_TREE"]["probability"] < 0.01


def test_ordinary_street_suggests_nothing() -> None:
    result = urban_aux_scores(_unit(3)[None, :], VECTORS, PROMPTS, BACKGROUND)
    assert result["suggestions"] == []
    assert all(score["margin"] < 0 for score in result["scores"].values())


def test_invalid_vectors_are_refused() -> None:
    with pytest.raises(ReferenceUnavailable):
        urban_aux_scores(_unit(0)[None, :], VECTORS[:3], PROMPTS, BACKGROUND)
    with pytest.raises(ReferenceUnavailable):
        urban_aux_scores(np.full((1, 8), np.nan, dtype=np.float32), VECTORS, PROMPTS, BACKGROUND)


def _manifest() -> dict:
    count = sum(len(v) for v in URBAN_AUX_PROMPTS.values()) + len(URBAN_AUX_BACKGROUND)
    return {
        "schema": "urmind-urban-aux-reference-v1",
        "status": "UNCALIBRATED_ADVISORY",
        "source_sha256": "a" * 64,
        "source_revision": "rev",
        "onnx_sha256": "b" * 64,
        "prompts": {code: list(prompts) for code, prompts in URBAN_AUX_PROMPTS.items()},
        "background": list(URBAN_AUX_BACKGROUND),
        "text_embeddings": np.zeros((count, 512)).tolist(),
    }


def test_manifest_must_match_registered_weights_onnx_and_prompts() -> None:
    entry = {"sha256": "a" * 64, "revision": "rev"}
    vectors = validate_urban_aux_manifest(_manifest(), entry, onnx_sha256="b" * 64)
    assert vectors.shape[1] == 512
    for key, value in (
        ("source_sha256", "c" * 64),
        ("onnx_sha256", "c" * 64),
        ("status", "CALIBRATED"),
        ("background", ["other"]),
    ):
        with pytest.raises(ReferenceUnavailable):
            validate_urban_aux_manifest({**_manifest(), key: value}, entry, onnx_sha256="b" * 64)
    changed = _manifest()
    changed["prompts"]["URMIND_FALLEN_TREE"] = ["a different prompt"]
    with pytest.raises(ReferenceUnavailable):
        validate_urban_aux_manifest(changed, entry, onnx_sha256="b" * 64)


def test_worker_helper_never_raises(monkeypatch) -> None:
    assert urban_auxiliary_safe(b"not an image")["status"] == "unavailable"

    def missing(image):
        raise ReferenceUnavailable("scene_onnx_missing")

    monkeypatch.setattr(photo_reference, "urban_auxiliary", missing)
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (32, 32), "gray").save(buffer, format="JPEG")
    result = urban_auxiliary_safe(buffer.getvalue())
    assert result == {"status": "unavailable", "reason": "scene_onnx_missing"}
