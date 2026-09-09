from datetime import UTC, datetime
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
from app.services.core import CoreService, DuplicateKeyError, decide_status

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
SP = Coordinate(latitude=-23.5505, longitude=-46.6333, accuracy_m=6)
SNAP = {"road_segment_id": uuid4(), "distance_m": 3.2, "latitude": -23.5505, "longitude": -46.6334}


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
