from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.schemas.core import (
    CaptureCreate,
    CaptureSource,
    Coordinate,
    EventCreate,
    EventStatus,
    EvidenceMode,
    LocationSource,
    UrmindClass,
)
from app.services.core import (
    ACTION_BY_SEVERITY,
    CoreService,
    DuplicateKeyError,
    decide_status,
)
from app.services.risk import Severity

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
SP = Coordinate(latitude=-23.5505, longitude=-46.6333, accuracy_m=6)
SNAP = {"road_segment_id": uuid4(), "distance_m": 3.2, "latitude": -23.5505, "longitude": -46.6334}


@pytest.mark.asyncio
@pytest.mark.parametrize("same_model", [True, False])
async def test_private_dossier_model_status_bound_to_capture_inference(same_model):
    model_id = uuid4()
    cap = SimpleNamespace(
        id=uuid4(),
        capture_key="fixture",
        source="pwa_photo",
        source_location="manual",
        captured_at=NOW,
        storage_path="private/fixture.jpg",
        detections=[],
        quality={
            "inference": {
                "model_version_id": str(model_id if same_model else uuid4()),
                "model_status": "EXPERIMENTAL_SHADOW",
            }
        },
    )
    row = SimpleNamespace(
        capture_id=cap.id, factors={}, model_version_id=model_id, road_segment_id=None
    )
    service = CoreService(
        SimpleNamespace(get=AsyncMock(return_value=cap)),
        SimpleNamespace(
            get=AsyncMock(return_value=row),
            coordinates=AsyncMock(return_value={}),
            contexts=AsyncMock(return_value=[]),
        ),
        SimpleNamespace(
            latest_risk=AsyncMock(return_value=None), reviews=AsyncMock(return_value=[])
        ),
    )
    service.detail = AsyncMock(return_value={})
    result = await service.event_dossier(uuid4())
    assert result["model_status"] == ("EXPERIMENTAL_SHADOW" if same_model else None)


def test_localizacao_autodeclarada_exige_revisao_mesmo_com_snap():
    payload = EventCreate(
        event_key="event-client-location-001",
        capture_id=uuid4(),
        urmind_class=UrmindClass.ROAD_D40,
        evidence_mode=EvidenceMode.PHOTO,
        occurred_at=NOW,
        coordinate=SP,
        visual_confidence=0.9,
        factors={"location_attestation": "unverified_client_claim"},
    )
    assert decide_status(payload, SNAP) is EventStatus.REVIEW


@pytest.mark.asyncio
async def test_client_gps_accuracy_persists_on_capture_but_not_as_verified_event_accuracy():
    capture_id, detection_id, model_id = uuid4(), uuid4(), uuid4()
    capture = SimpleNamespace(
        id=capture_id,
        source=CaptureSource.PWA_PHOTO.value,
        captured_at=NOW,
        quality={"location_attestation": "unverified_client_claim"},
        detections=[
            SimpleNamespace(
                id=detection_id,
                urmind_class=UrmindClass.ROAD_D40.value,
                confidence=0.8,
                model_version_id=model_id,
            )
        ],
    )
    captures = SimpleNamespace(
        get=AsyncMock(return_value=capture),
        location=AsyncMock(
            return_value={"latitude": SP.latitude, "longitude": SP.longitude, "accuracy_m": 1}
        ),
    )
    events = SimpleNamespace(
        get_by_key=AsyncMock(return_value=None), find_open_near=AsyncMock(return_value=None)
    )
    service = CoreService(captures, events)
    service.register_event = AsyncMock(return_value={"id": uuid4()})
    service.assess_event = AsyncMock(return_value={})

    await service.consolidate_capture(capture_id)

    payload = service.register_event.await_args.args[0]
    assert payload.coordinate.accuracy_m is None
    assert payload.factors["location_attestation"] == "unverified_client_claim"
    events.find_open_near.assert_not_awaited()
    assert captures.location.await_args.args == (capture_id,)


def event(**overrides) -> EventCreate:
    payload = {
        "event_key": "evt-0001",
        "urmind_class": UrmindClass.ROAD_D40,
        "evidence_mode": EvidenceMode.PHOTO,
        "occurred_at": NOW,
        "coordinate": SP,
        "visual_confidence": 0.9,
    }
    payload.update(overrides)
    return EventCreate(**payload)


class FakeEventRow:
    def __init__(self) -> None:
        self.id = uuid4()
        self.event_key = "evt-0001"
        self.status = "detected"
        self.road_segment_id = None
        self.distance_to_road_m = None
        self.snapped_point = None


class FakeEventRepo:
    def __init__(self, *, existing: bool = False, snap: dict | None = SNAP) -> None:
        self.existing = existing
        self.snap = snap
        self.row = FakeEventRow()

    async def get_by_key(self, _key):
        return self.row if self.existing else None

    async def create(self, _payload):
        return self.row

    async def snap_to_road(self, _coordinate, _max_distance_m=50):
        return self.snap

    async def attach_road(self, row, *, road_segment_id, distance_m, latitude, longitude):
        row.road_segment_id = road_segment_id
        row.distance_to_road_m = distance_m
        row.snapped_point = (latitude, longitude)
        return row

    async def set_status(self, row, status):
        row.status = status
        return row


class FakeCaptureRepo:
    def __init__(self, existing=None) -> None:
        self.existing = existing
        self.created = None

    async def get_by_key(self, _key):
        return self.existing

    async def create(self, payload):
        self.created = FakeEventRow()
        self.created.capture_key = payload.capture_key
        return self.created


def capture() -> CaptureCreate:
    return CaptureCreate(
        capture_key="cap-0001",
        source=CaptureSource.PWA_PHOTO,
        source_location=LocationSource.GPS_DEVICE,
        captured_at=NOW,
        coordinate=SP,
    )


@pytest.mark.asyncio
async def test_capture_reenvio_e_idempotente():
    existing = FakeEventRow()
    existing.capture_key = "cap-0001"
    existing.protocol_code = "URM-7K3Q9XYZ"
    service = CoreService(FakeCaptureRepo(existing=existing), FakeEventRepo())
    result = await service.register_capture(capture())
    assert result["created"] is False


@pytest.mark.asyncio
async def test_evento_com_gps_bom_recebe_snap_e_fica_detected():
    repo = FakeEventRepo()
    service = CoreService(FakeCaptureRepo(), repo)
    result = await service.register_event(event())
    assert result["status"] == EventStatus.DETECTED.value
    assert result["road_segment_id"] == SNAP["road_segment_id"]
    assert result["distance_to_road_m"] == SNAP["distance_m"]


@pytest.mark.asyncio
async def test_evento_sem_via_proxima_vai_para_revisao():
    service = CoreService(FakeCaptureRepo(), FakeEventRepo(snap=None))
    result = await service.register_event(event())
    assert result["status"] == EventStatus.REVIEW.value


@pytest.mark.asyncio
async def test_event_key_repetida_e_rejeitada():
    service = CoreService(FakeCaptureRepo(), FakeEventRepo(existing=True))
    with pytest.raises(DuplicateKeyError):
        await service.register_event(event())


def test_gps_impreciso_vai_para_revisao():
    payload = event(coordinate=Coordinate(latitude=-23.55, longitude=-46.63, accuracy_m=120))
    assert decide_status(payload, SNAP) is EventStatus.REVIEW


def test_confianca_baixa_vai_para_revisao():
    assert decide_status(event(visual_confidence=0.2), SNAP) is EventStatus.REVIEW


def test_classe_desconhecida_vai_para_revisao():
    payload = event(urmind_class=UrmindClass.UNKNOWN)
    assert decide_status(payload, SNAP) is EventStatus.REVIEW


def test_imagem_sem_localizacao_exige_triagem():
    payload = event(evidence_mode=EvidenceMode.IMAGE_ONLY, coordinate=None)
    assert decide_status(payload, None) is EventStatus.TRIAGE_REQUIRED


def test_sensor_sem_camera_gera_candidato_para_revisao():
    payload = event(evidence_mode=EvidenceMode.SENSOR_ONLY, visual_confidence=None)
    assert decide_status(payload, SNAP) is EventStatus.REVIEW


@pytest.mark.parametrize(
    ("severity", "esperado"),
    [
        (Severity.CRITICAL, "sinalizacao_temporaria"),
        (Severity.HIGH, "sinalizacao_temporaria"),
        (Severity.MEDIUM, "inspecao_tecnica"),
        (Severity.LOW, "inspecao_tecnica"),
        (Severity.UNKNOWN, None),
    ],
)
def test_acao_sugerida_acompanha_a_severidade(severity, esperado) -> None:
    """Defeito grave pede proteção do ponto; sem severidade, nada é sugerido."""
    assert ACTION_BY_SEVERITY.get(severity) == esperado


def test_reparo_definitivo_nunca_e_sugerido_pelo_sistema() -> None:
    # A intervenção definitiva é decisão do órgão após inspeção (§14.4).
    assert "reparo_pavimento" not in ACTION_BY_SEVERITY.values()


@pytest.mark.asyncio
async def test_capture_marker_without_model_has_no_invented_analysis():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    row = {
        "id": "capture",
        "public_id": "a732cb867fd24d188f0f234afde8a664",
        "latitude": -23,
        "longitude": -46,
        "processing_status": "model_not_available",
        "user_description": "<script>x</script>",
        "urmind_class": "must-not-leak",
        "severity": "high",
        "priority_score": 99,
    }
    repository = SimpleNamespace(report_markers=AsyncMock(return_value=[row]))
    service = CoreService(repository, None)
    result = (await service.capture_markers("owner"))[0]
    repository.report_markers.assert_awaited_with(
        "owner", False, public=False, include_unlocated=False
    )
    assert result["report_status"] == "model_not_available"
    assert result["urmind_class"] is None and result["severity"] is None
    assert result["priority_score"] is None
    assert result["user_description"] == "<script>x</script>"
    public = (await service.capture_markers("", public=True))[0]
    assert set(public) == {"id", "latitude", "longitude", "report_status"}
    assert public["report_status"] == "received"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("inference", "has_point", "event_status", "expected"),
    [
        ({}, True, None, "queued"),
        ({"status": "processing_detection"}, True, None, "processing_detection"),
        ({"status": "model_not_available"}, True, None, "model_not_available"),
        ({"status": "inference_failed"}, True, None, "failed"),
        ({"status": "needs_review"}, True, None, "needs_review"),
        ({"status": "no_detection"}, True, None, "no_supported_detection"),
        ({"status": "no_supported_detection"}, True, None, "no_supported_detection"),
        ({"status": "inference_completed", "detections": 2}, True, None, "detection_completed"),
        ({"status": "no_event"}, True, None, "no_event"),
        ({"status": "inference_completed"}, True, "detected", "detection_completed"),
        ({"status": "inference_completed"}, True, "triage_required", "detection_completed"),
        ({}, False, None, "location_required"),
    ],
)
async def test_capture_processing_reports_only_observed_state(
    inference, has_point, event_status, expected
):
    capture_id = uuid4()
    capture = SimpleNamespace(
        id=capture_id,
        quality={"uploaded_by": "owner", "inference": inference},
        point=object() if has_point else None,
    )
    event_rows = [SimpleNamespace(id=uuid4(), status=event_status)] if event_status else []
    captures = SimpleNamespace(get=AsyncMock(return_value=capture))
    events = SimpleNamespace(for_capture=AsyncMock(return_value=event_rows))
    service = CoreService(captures, events)

    result = await service.capture_processing(capture_id, "owner", False)

    assert result["status"] == expected
    assert result["requires_manual_location"] is (not has_point)
    assert result["event_ids"] == [row.id for row in event_rows]


@pytest.mark.asyncio
async def test_capture_processing_does_not_reveal_other_user_capture():
    captures = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(quality={"uploaded_by": "owner"}))
    )
    events = SimpleNamespace(for_capture=AsyncMock())
    service = CoreService(captures, events)

    from app.services.core import EventNotFoundError

    with pytest.raises(EventNotFoundError):
        await service.capture_processing(uuid4(), "other", False)
    events.for_capture.assert_not_awaited()


@pytest.mark.asyncio
async def test_capture_processing_reviewer_cannot_read_another_users_capture():
    captures = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(quality={"uploaded_by": "owner"}))
    )
    events = SimpleNamespace(for_capture=AsyncMock())
    service = CoreService(captures, events)

    from app.services.core import EventNotFoundError

    with pytest.raises(EventNotFoundError):
        await service.capture_processing(uuid4(), "reviewer", True)
    events.for_capture.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("factors", "event_status", "expected"),
    [
        ({}, "detected", "assessing"),
        (
            {"phase4_snapshot": {"features": {}}, "decision_trace": {"rules": []}},
            "detected",
            "completed",
        ),
        (
            {"phase4_snapshot": {"features": {}}, "decision_trace": {"rules": []}},
            "review",
            "needs_review",
        ),
    ],
)
async def test_capture_processing_requires_persisted_features_and_trace(
    factors, event_status, expected
):
    capture_id = uuid4()
    capture = SimpleNamespace(
        id=capture_id,
        quality={"uploaded_by": "owner", "inference": {"status": "analysis_completed"}},
        point=object(),
    )
    event = SimpleNamespace(id=uuid4(), status=event_status)
    decisions = SimpleNamespace(
        latest_risk=AsyncMock(return_value=SimpleNamespace(factors=factors))
    )
    service = CoreService(
        SimpleNamespace(get=AsyncMock(return_value=capture)),
        SimpleNamespace(for_capture=AsyncMock(return_value=[event])),
        decisions,
    )

    result = await service.capture_processing(capture_id, "owner", False)
    assert result["status"] == expected


@pytest.mark.asyncio
async def test_capture_processing_keeps_deduplicated_event_lineage():
    capture_id = uuid4()
    event = SimpleNamespace(
        id=uuid4(),
        status="detected",
        capture_id=uuid4(),
        factors={"evidence": {"capture_ids": [str(capture_id)]}},
    )
    capture = SimpleNamespace(
        id=capture_id,
        quality={
            "uploaded_by": "owner",
            "inference": {"status": "analysis_completed", "event_ids": [str(event.id)]},
        },
        point=object(),
    )
    service = CoreService(
        SimpleNamespace(get=AsyncMock(return_value=capture)),
        SimpleNamespace(for_capture=AsyncMock(return_value=[]), get=AsyncMock(return_value=event)),
        SimpleNamespace(
            latest_risk=AsyncMock(
                return_value=SimpleNamespace(
                    factors={
                        "phase4_snapshot": {"features": {}},
                        "decision_trace": {"rules": []},
                    }
                )
            )
        ),
    )
    result = await service.capture_processing(capture_id, "owner", False)
    assert result["status"] == "completed"
    assert result["event_ids"] == [event.id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored", "has_events", "expected"),
    [
        (None, False, "queued"),
        ("processing_detection", False, "processing_detection"),
        ("building_event", False, "building_event"),
        ("detection_completed", True, "detection_completed"),
        ("detection_completed", False, "detection_completed"),
        ("enriching_context", True, "enriching_context"),
        ("building_features", True, "building_features"),
        ("assessing", True, "assessing"),
        ("model_not_available", False, "model_not_available"),
        ("inference_failed", False, "failed"),
        ("unexpected-legacy-value", False, "queued"),
    ],
)
async def test_capture_processing_maps_every_stored_stage_to_canonical_status(
    stored, has_events, expected
):
    from app.schemas.core import CaptureProcessingStatus

    capture_id = uuid4()
    inference = {} if stored is None else {"status": stored}
    capture = SimpleNamespace(
        id=capture_id,
        quality={"uploaded_by": "owner", "inference": inference},
        point=object(),
    )
    events = [SimpleNamespace(id=uuid4(), status="detected")] if has_events else []
    service = CoreService(
        SimpleNamespace(get=AsyncMock(return_value=capture)),
        SimpleNamespace(for_capture=AsyncMock(return_value=events)),
    )
    result = await service.capture_processing(capture_id, "owner", False)
    assert result["status"] == expected
    assert CaptureProcessingStatus(result["status"])  # always a canonical value


@pytest.mark.asyncio
async def test_detection_completed_is_never_reported_as_completed():
    capture_id = uuid4()
    capture = SimpleNamespace(
        id=capture_id,
        quality={"uploaded_by": "owner", "inference": {"status": "detection_completed"}},
        point=object(),
    )
    service = CoreService(
        SimpleNamespace(get=AsyncMock(return_value=capture)),
        SimpleNamespace(
            for_capture=AsyncMock(return_value=[SimpleNamespace(id=uuid4(), status="detected")])
        ),
        SimpleNamespace(latest_risk=AsyncMock(return_value=None)),
    )
    result = await service.capture_processing(capture_id, "owner", False)
    assert result["status"] != "completed"


@pytest.mark.asyncio
async def test_owner_report_list_retains_unlocated_capture_without_inventing_a_point():
    captures = SimpleNamespace(
        report_markers=AsyncMock(
            return_value=[
                {
                    "id": uuid4(),
                    "latitude": None,
                    "longitude": None,
                    "user_description": "Calçada",
                    "created_at": "2026-09-24T12:00:00Z",
                }
            ]
        )
    )
    service = CoreService(captures, SimpleNamespace())
    result = await service.capture_markers("owner", include_unlocated=True)
    assert result[0]["latitude"] is None
    assert result[0]["report_status"] == "location_required"
    assert result[0]["created_at"] == "2026-09-24T12:00:00Z"
    captures.report_markers.assert_awaited_once_with(
        "owner", False, public=False, include_unlocated=True
    )


@pytest.mark.asyncio
async def test_generic_public_marker_uses_independent_identity_and_rounded_location():
    internal_id = uuid4()
    captures = SimpleNamespace(
        report_markers=AsyncMock(
            return_value=[
                {
                    "id": internal_id,
                    "public_id": "K7HZP3XQ9M4RT6WY",
                    "latitude": -23.123456,
                    "longitude": -46.654321,
                    "accuracy_m": 3,
                    "user_description": "private",
                    "protocol_code": "URM-7K3Q9",
                }
            ]
        )
    )
    result = await CoreService(captures, SimpleNamespace()).capture_markers("", public=True)
    assert result == [
        {
            "id": "K7HZP3XQ9M4RT6WY",
            "latitude": -23.1235,
            "longitude": -46.6543,
            "report_status": "received",
        }
    ]
    assert str(internal_id) not in str(result)


@pytest.mark.asyncio
async def test_citizen_cannot_use_capture_human_review_service():
    from app.schemas.core import CaptureReviewCreate

    service = CoreService(SimpleNamespace(), SimpleNamespace())
    with pytest.raises(PermissionError):
        await service.review_capture(
            uuid4(), CaptureReviewCreate(decision="reject"), reviewer="citizen", reviewer_role=None
        )
