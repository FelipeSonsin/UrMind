"""Contratos do núcleo: taxonomia, captura, detecção e evento (MASTER_PLAN §5, §6, §8)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class UrmindClass(StrEnum):
    """Taxonomia canônica (§8.2). Nunca inferir classe fora desta lista."""

    ROAD_D00 = "URMIND_ROAD_D00"
    ROAD_D10 = "URMIND_ROAD_D10"
    ROAD_D20 = "URMIND_ROAD_D20"
    ROAD_D40 = "URMIND_ROAD_D40"
    MANHOLE = "URMIND_MANHOLE"
    SIDEWALK = "URMIND_SIDEWALK"
    SIGNAGE = "URMIND_SIGNAGE"
    UNKNOWN = "URMIND_UNKNOWN"


class CaptureSource(StrEnum):
    SCOUT = "scout"
    PWA_PHOTO = "pwa_photo"
    EXIF_UPLOAD = "exif_upload"
    IMPORT = "import"


class LocationSource(StrEnum):
    """Procedência da coordenada (§5.1); nunca tratada como 'exata'."""

    GPS_SCOUT = "gps_scout"
    GPS_DEVICE = "gps_device"
    EXIF = "exif"
    MANUAL = "manual"
    IMPORTED = "imported"
    UNKNOWN = "unknown"


class EvidenceMode(StrEnum):
    """Modos de evidência (§1.1, §31.9) — limitam o que o sistema pode afirmar."""

    SCOUT_FULL = "scout_full"
    PHOTO = "photo"
    EXIF_PHOTO = "exif_photo"
    IMAGE_ONLY = "image_only"
    SENSOR_ONLY = "sensor_only"


class EventStatus(StrEnum):
    DETECTED = "detected"
    REVIEW = "review"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    TRIAGE_REQUIRED = "triage_required"


class Coordinate(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)


class BoundingBox(BaseModel):
    """Caixa normalizada (0–1) para não depender da resolução do frame."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def inside_frame(self) -> BoundingBox:
        if self.x + self.width > 1.0001 or self.y + self.height > 1.0001:
            raise ValueError("bounding box ultrapassa os limites do frame")
        return self


class DetectionCreate(BaseModel):
    urmind_class: UrmindClass
    confidence: float = Field(ge=0, le=1)
    bbox: BoundingBox
    model_version_id: uuid.UUID | None = None


class CaptureCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capture_key: str = Field(min_length=6, max_length=120)
    source: CaptureSource
    source_location: LocationSource = LocationSource.UNKNOWN
    captured_at: datetime
    mission_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None
    storage_path: str | None = Field(default=None, max_length=500)
    coordinate: Coordinate | None = None
    heading_deg: float | None = Field(default=None, ge=0, le=360)
    speed_mps: float | None = Field(default=None, ge=0)
    quality: dict[str, Any] = Field(default_factory=dict)
    detections: list[DetectionCreate] = Field(default_factory=list)

    @model_validator(mode="after")
    def location_is_declared(self) -> CaptureCreate:
        if self.coordinate is not None and self.source_location is LocationSource.UNKNOWN:
            raise ValueError("informe source_location quando houver coordenada")
        if self.coordinate is None and self.source_location is not LocationSource.UNKNOWN:
            raise ValueError("source_location exige coordenada")
        return self


class EventCreate(BaseModel):
    """Ocorrência consolidada. Sem coordenada não vira evento geográfico (§5, §11.1)."""

    model_config = ConfigDict(extra="forbid")

    event_key: str = Field(min_length=6, max_length=120)
    urmind_class: UrmindClass
    evidence_mode: EvidenceMode
    occurred_at: datetime
    capture_id: uuid.UUID | None = None
    mission_id: uuid.UUID | None = None
    visual_confidence: float | None = Field(default=None, ge=0, le=1)
    fused_confidence: float | None = Field(default=None, ge=0, le=1)
    coordinate: Coordinate | None = None
    factors: dict[str, Any] = Field(default_factory=dict)
    model_version_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def evidence_matches_payload(self) -> EventCreate:
        if self.evidence_mode is EvidenceMode.IMAGE_ONLY and self.coordinate is not None:
            raise ValueError("image_only não pode trazer coordenada")
        if self.evidence_mode is not EvidenceMode.IMAGE_ONLY and self.coordinate is None:
            raise ValueError(f"{self.evidence_mode} exige coordenada")
        if self.evidence_mode is EvidenceMode.SENSOR_ONLY and self.visual_confidence is not None:
            raise ValueError("sensor_only não pode afirmar confiança visual")
        return self


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_key: str
    urmind_class: str
    evidence_mode: str
    status: str
    occurred_at: datetime
    visual_confidence: float | None = None
    fused_confidence: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    location_accuracy_m: float | None = None
    snapped_latitude: float | None = None
    snapped_longitude: float | None = None
    road_segment_id: uuid.UUID | None = None
    distance_to_road_m: float | None = None
    factors: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class NearbyQuery(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_m: float = Field(default=500, gt=0, le=20000)
    limit: int = Field(default=100, gt=0, le=500)
