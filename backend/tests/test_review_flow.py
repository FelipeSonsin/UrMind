"""Revisão humana (§16.2): correção explícita e candidatos rastreáveis. Sem banco."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.core import Coordinate, ReviewCreate, ReviewDecision, UrmindClass
from app.services.review_export import TRAINING_STATUS, candidate_record, write_batch

ROW = {
    "review_id": "r1",
    "decision": "correct",
    "reviewer": "revisor",
    "reviewed_at": datetime(2026, 9, 17, tzinfo=UTC),
    "notes": None,
    "corrected_class": "URMIND_ROAD_D20",
    "corrected_latitude": -23.5601,
    "corrected_longitude": -46.6551,
    "event_id": "e1",
    "event_key": "cap-x-URMIND_ROAD_D10",
    "inferred_class": "URMIND_ROAD_D10",
    "visual_confidence": 0.58,
    "latitude": -23.5613,
    "longitude": -46.656,
    "capture_id": "c1",
    "capture_key": "photo-abc",
    "storage_path": "u/2026/09/abc.jpg",
    "image_sha256": "abc",
    "source": "pwa_photo",
    "source_location": "gps_device",
    "captured_at": datetime(2026, 9, 17, tzinfo=UTC),
    "original_detections": [{"id": "d1", "urmind_class": "URMIND_ROAD_D10", "confidence": 0.58}],
    "model_name": "yolox-s-model-v1",
    "model_version": "baseline_early-epoch9-3c57b897fe38",
    "model_checksum": "3c57",
    "dataset_name": "rdd2022-model-v1-authorized",
    "dataset_version": "0685cce617713034",
}


def test_correcao_exige_conteudo_e_so_vale_para_correct() -> None:
    with pytest.raises(ValidationError):
        ReviewCreate(decision=ReviewDecision.CORRECT)
    with pytest.raises(ValidationError):
        ReviewCreate(
            decision=ReviewDecision.CONFIRM,
            corrected_location=Coordinate(latitude=0, longitude=0),
        )
    only_location = ReviewCreate(
        decision=ReviewDecision.CORRECT,
        corrected_location=Coordinate(latitude=-23.5, longitude=-46.6),
    )
    assert only_location.corrected_class is None


def test_candidato_preserva_inferencia_original_e_linhagem() -> None:
    record = candidate_record(ROW)
    assert record["label"] == {
        "urmind_class": UrmindClass.ROAD_D20.value,
        "source": "human_correction",
        "inferred_class": "URMIND_ROAD_D10",
    }
    assert record["location"]["original"] == {"latitude": -23.5613, "longitude": -46.656}
    assert record["location"]["corrected"] == {"latitude": -23.5601, "longitude": -46.6551}
    assert record["original_detections"][0]["urmind_class"] == "URMIND_ROAD_D10"
    assert record["inference_lineage"]["model_version"].startswith("baseline_early")
    assert record["training_status"] == TRAINING_STATUS


def test_confirmacao_usa_label_inferida() -> None:
    confirmed = candidate_record(
        {**ROW, "decision": "confirm", "corrected_class": None, "corrected_latitude": None}
    )
    assert confirmed["label"]["urmind_class"] == "URMIND_ROAD_D10"
    assert confirmed["label"]["source"] == "human_confirmation"
    assert confirmed["location"]["corrected"] is None


def test_lote_tem_manifesto_com_hash_e_sem_retreino(tmp_path) -> None:
    manifest = write_batch([candidate_record(ROW)], tmp_path)
    data = (tmp_path / manifest["file"]).read_bytes()
    assert manifest["sha256"] == hashlib.sha256(data).hexdigest()
    assert manifest["records"] == 1
    assert manifest["retraining_triggered"] is False
    assert json.loads(data.decode().splitlines()[0])["review"]["id"] == "r1"


def test_casos_dificeis_e_f1_agregado() -> None:
    from app.ml.metrics import Box, GroundTruth, Prediction, evaluate, hard_cases

    truths = [
        GroundTruth("img-a", "D00", Box(0, 0, 10, 10)),
        GroundTruth("img-b", "D40", Box(0, 0, 10, 10)),
    ]
    predictions = [
        Prediction("img-a", "D00", Box(0, 0, 10, 10), score=0.9),  # acerto
        Prediction("img-a", "D10", Box(50, 50, 60, 60), score=0.8),  # FP de alta confiança
        Prediction("img-c", "D20", Box(0, 0, 5, 5), score=0.3),  # FP de baixa confiança
    ]
    cases = hard_cases(predictions, truths, score_threshold=0.25, iou_threshold=0.5)
    worst = {row["image_id"]: row for row in cases["worst_images"]}
    assert worst["img-b"]["false_negatives"] == 1
    assert worst["img-a"]["false_positives"] == 1
    assert cases["high_confidence_false_positives"] == [
        {"image_id": "img-a", "label": "D10", "score": 0.8}
    ]
    assert cases["images_with_missed_ground_truth"] == 1

    result = evaluate(
        predictions, truths, labels=["D00", "D10", "D20", "D40"], score_threshold=0.25
    )
    persisted = result.as_persisted()
    precision, recall = persisted["precision"], persisted["recall"]
    assert persisted["f1"] == round(2 * precision * recall / (precision + recall), 6)


def test_revogar_papel_envia_null_porque_a_api_faz_merge() -> None:
    """Omitir a chave não apaga nada no Supabase: a revogação precisa mandar null."""
    import httpx

    from app.config import JWKS_PATH, Settings
    from app.services.reviewer_admin import ROLE_KEY, set_role

    settings = Settings.model_validate(
        {
            "SUPABASE_URL": "https://refficticio.supabase.co",
            "SUPABASE_JWKS_URL": "https://refficticio.supabase.co" + JWKS_PATH,
            "SUPABASE_SECRET_KEY": "sb_secret_FICTICIA_nao_usar",
        }
    )
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "users": [
                        {
                            "id": "u1",
                            "email": "p@example.invalid",
                            "app_metadata": {"urmind_role": "reviewer", "outro": "x"},
                        }
                    ]
                },
            )
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "u1",
                "email": "p@example.invalid",
                "app_metadata": sent[-1]["app_metadata"],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        revoked = set_role(settings, client, "p@example.invalid", None)
        granted = set_role(settings, client, "p@example.invalid", "reviewer")

    assert sent[0]["app_metadata"] == {"outro": "x", ROLE_KEY: None}  # null explícito
    assert revoked["can_review"] is False
    assert sent[1]["app_metadata"][ROLE_KEY] == "reviewer"
    assert granted["can_review"] is True
