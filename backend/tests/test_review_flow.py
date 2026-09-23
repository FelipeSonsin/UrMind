"""Revisão humana (§16.2): correção explícita e candidatos rastreáveis. Sem banco."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1.core import review_event as review_endpoint
from app.auth import AuthenticatedUser
from app.schemas.core import Coordinate, ReviewCreate, ReviewDecision, UrmindClass
from app.services.core import CoreService
from app.services.review_export import (
    TRAINING_STATUS,
    candidate_record,
    eligible_candidates,
    review_resolution,
    verify_candidate_objects,
    write_batch,
)

ROW = {
    "review_id": "r1",
    "decision": "correct",
    "reviewer": "revisor",
    "reviewer_role": "reviewer",
    "review_schema_version": "urmind-review-v1",
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
    "evidence": {"capture_ids": ["c1"], "detection_ids": ["d1"]},
    "capture_key": "photo-abc",
    "storage_path": "u/2026/09/abc.jpg",
    "image_sha256": "abc",
    "source": "pwa_photo",
    "source_location": "gps_device",
    "captured_at": datetime(2026, 9, 17, tzinfo=UTC),
    "original_detections": [
        {
            "id": "d1",
            "capture_id": "c1",
            "model_version_id": "m1",
            "urmind_class": "URMIND_ROAD_D10",
            "confidence": 0.58,
        }
    ],
    "model_name": "yolox-s-model-v1",
    "model_version_id": "m1",
    "model_version": "baseline_early-epoch9-3c57b897fe38",
    "model_checksum": "3c57",
    "dataset_name": "rdd2022-model-v1-authorized",
    "dataset_version_id": "ds1",
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


def test_ground_truth_requires_independent_agreement() -> None:
    first = {**ROW, "reviewer": "u1"}
    second = {**ROW, "reviewer": "u2", "review_id": "r2"}
    assert review_resolution([first])["status"] == "requires_second_review"
    resolution = review_resolution([first, second])
    assert resolution["status"] == "consensus"
    assert resolution["selected"]["review_id"] == "r2"


def test_conflict_requires_adjudication_and_reject_is_not_exportable() -> None:
    first = {**ROW, "reviewer": "u1"}
    opposed = {**ROW, "reviewer": "u2", "corrected_class": "URMIND_ROAD_D40"}
    assert review_resolution([first, opposed])["status"] == "conflicted"
    adjudicated = {**first, "reviewer": "admin", "reviewer_role": "admin", "adjudicated": True}
    assert review_resolution([first, opposed, adjudicated])["status"] == "adjudicated"
    assert review_resolution([first, opposed, adjudicated, opposed])["status"] == "conflicted"
    assert (
        review_resolution([{**first, "order_source": "legacy_backfill"}, opposed])["status"]
        == "legacy_order_uncertain"
    )
    rejected = [{**ROW, "reviewer": reviewer, "decision": "reject"} for reviewer in ("u1", "u2")]
    assert review_resolution(rejected)["selected"]["decision"] == "reject"
    assert eligible_candidates([first, opposed]) == []
    assert eligible_candidates(rejected) == []
    exported = eligible_candidates([first, opposed, adjudicated])
    assert len(exported) == 1
    assert exported[0]["dataset_eligibility"] is True
    assert exported[0]["training_status"] == TRAINING_STATUS
    assert len(exported[0]["review_provenance"]) == 3


def test_prior_conflict_cannot_be_erased_by_recasting_a_vote() -> None:
    first = {**ROW, "reviewer": "u1"}
    opposed = {**ROW, "reviewer": "u2", "corrected_class": "URMIND_ROAD_D40"}
    recast = {**opposed, "reviewer": "u1", "review_id": "r3"}
    assert review_resolution([first, opposed, recast])["status"] == "conflicted"
    assert eligible_candidates([first, opposed, recast]) == []


def test_unverified_reviewer_role_cannot_create_ground_truth() -> None:
    votes = [{**ROW, "reviewer": reviewer, "reviewer_role": None} for reviewer in ("u1", "u2")]
    assert review_resolution(votes)["status"] == "role_unverified"
    assert eligible_candidates(votes) == []


def test_export_rejects_cross_capture_or_model_lineage() -> None:
    votes = [{**ROW, "reviewer": reviewer} for reviewer in ("u1", "u2")]
    assert len(eligible_candidates(votes)) == 1
    mixed = [
        {**row, "original_detections": [{**row["original_detections"][0], "capture_id": "other"}]}
        for row in votes
    ]
    assert eligible_candidates(mixed) == []
    mixed_model = [
        {
            **row,
            "original_detections": [{**row["original_detections"][0], "model_version_id": "other"}],
        }
        for row in votes
    ]
    assert eligible_candidates(mixed_model) == []
    missing_detection = [
        {**row, "evidence": {"capture_ids": ["c1"], "detection_ids": ["d1", "missing"]}}
        for row in votes
    ]
    assert eligible_candidates(missing_detection) == []


@pytest.mark.asyncio
async def test_export_checks_storage_object_and_checksum() -> None:
    payload = b"test image bytes"
    candidate = candidate_record({**ROW, "image_sha256": hashlib.sha256(payload).hexdigest()})

    class Storage:
        async def download(self, _path):
            return payload

    await verify_candidate_objects([candidate], Storage())

    class WrongStorage:
        async def download(self, _path):
            return b"different"

    with pytest.raises(ValueError, match="checksum mismatch"):
        await verify_candidate_objects([candidate], WrongStorage())


def test_review_schema_supports_combined_correction_without_payload_identity() -> None:
    correction = ReviewCreate(
        decision=ReviewDecision.CORRECT,
        corrected_class=UrmindClass.ROAD_D20,
        corrected_location=Coordinate(latitude=-23.5, longitude=-46.6),
    )
    assert correction.corrected_class is UrmindClass.ROAD_D20
    assert correction.corrected_location is not None
    with pytest.raises(ValidationError):
        ReviewCreate.model_validate({"decision": "confirm", "reviewer": "forged"})


@pytest.mark.asyncio
async def test_endpoint_uses_authenticated_identity_and_admin_only_adjudication() -> None:
    calls = []

    async def record(event_id, payload, *, reviewer, reviewer_role):
        calls.append((reviewer, reviewer_role))
        return {"status": "confirmed"}

    service = SimpleNamespace(review_event=record)
    event_id = uuid4()
    ordinary = AuthenticatedUser(id="u1", email=None, role="authenticated")
    reviewer = AuthenticatedUser(id="u2", email=None, role="authenticated", urmind_role="reviewer")
    admin = AuthenticatedUser(id="u3", email=None, role="authenticated", urmind_role="admin")
    with pytest.raises(HTTPException) as denied:
        await review_endpoint(
            event_id, ReviewCreate(decision=ReviewDecision.CONFIRM), ordinary, service
        )
    assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as denied:
        await review_endpoint(
            event_id,
            ReviewCreate(decision=ReviewDecision.CONFIRM, adjudicate=True),
            reviewer,
            service,
        )
    assert denied.value.status_code == 403
    await review_endpoint(
        event_id, ReviewCreate(decision=ReviewDecision.CONFIRM), reviewer, service
    )
    await review_endpoint(
        event_id, ReviewCreate(decision=ReviewDecision.CONFIRM, adjudicate=True), admin, service
    )
    assert calls == [("u2", "reviewer"), ("u3", "admin")]


@pytest.mark.asyncio
async def test_service_preserves_inference_and_audits_conflict() -> None:
    event = SimpleNamespace(id=uuid4(), status="detected", urmind_class="URMIND_ROAD_D40")
    audit = []

    class Events:
        async def get_for_review(self, event_id):
            return event

        async def coordinates(self, event_id):
            return {"latitude": -23.5, "longitude": -46.6}

        async def set_status(self, item, status):
            item.status = status

    class Decisions:
        async def review_votes(self, event_id):
            return [
                {
                    "decision": "correct",
                    "corrected_class": "URMIND_ROAD_D20",
                    "corrected_latitude": None,
                    "corrected_longitude": None,
                    "reviewer": "u1",
                    "reviewer_role": "reviewer",
                    "adjudicated": False,
                }
            ]

        async def add_review(self, **kwargs):
            return SimpleNamespace(
                id=uuid4(),
                decision=kwargs["decision"],
                corrected_class=kwargs["corrected_class"],
                order_source="serialized_commit_order",
            )

        async def add_audit(self, **kwargs):
            audit.append(kwargs)

    service = CoreService(SimpleNamespace(), Events(), Decisions())
    result = await service.review_event(
        event.id,
        ReviewCreate(decision=ReviewDecision.CORRECT, corrected_class=UrmindClass.ROAD_D10),
        reviewer="u2",
        reviewer_role="reviewer",
    )
    assert result["ground_truth_status"] == "conflicted"
    assert event.status == "review"
    assert event.urmind_class == "URMIND_ROAD_D40"
    assert audit[0]["before"]["urmind_class"] == "URMIND_ROAD_D40"
    assert audit[0]["after"]["corrected_class"] == "URMIND_ROAD_D10"
    assert audit[0]["after"]["ground_truth_status"] == "conflicted"


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
