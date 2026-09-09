"""Modelos ORM do núcleo do UrMind (espelham migrations/0001_core_geospatial.sql).

As migrations SQL continuam sendo a fonte da verdade do esquema; estes modelos
existem para consultas tipadas e para o teste de paridade em tests/test_migrations.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from geoalchemy2 import Geography, Geometry
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

_UUID = UUID(as_uuid=True)


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(_UUID, primary_key=True, server_default=func.gen_random_uuid())


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AuditLog(Base):
    """Histórico de alterações relevantes (§5). Não é log de debug."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = _pk()
    operation: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    actor: Mapped[str | None] = mapped_column(Text)
    before_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    license: Mapped[str | None] = mapped_column(Text)
    classes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    split: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    dvc_revision: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str | None] = mapped_column(Text)
    dataset_version_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("public.dataset_versions.id", ondelete="SET NULL")
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class ActionCatalog(Base):
    __tablename__ = "actions_catalog"

    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    version: Mapped[str] = mapped_column(Text, nullable=False, default="v1")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _created_at()


class ResponsibilityRule(Base):
    __tablename__ = "responsibility_rules"

    id: Mapped[uuid.UUID] = _pk()
    jurisdiction: Mapped[str] = mapped_column(Text, nullable=False)
    asset_type: Mapped[str] = mapped_column(Text, nullable=False)
    urmind_class: Mapped[str] = mapped_column(Text, nullable=False)
    responsible: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False, default="v1")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _created_at()


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    firmware_version: Mapped[str | None] = mapped_column(Text)
    calibration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _created_at()


class Mission(Base):
    __tablename__ = "missions"

    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("public.devices.id", ondelete="SET NULL")
    )
    operator: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="planned")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    route = mapped_column(Geography("LINESTRING", srid=4326), nullable=True)
    created_at: Mapped[datetime] = _created_at()


class RoadSegment(Base):
    __tablename__ = "road_segments"

    id: Mapped[uuid.UUID] = _pk()
    osm_id: Mapped[int | None] = mapped_column(unique=True)
    name: Mapped[str | None] = mapped_column(Text)
    highway: Mapped[str | None] = mapped_column(Text)
    jurisdiction: Mapped[str | None] = mapped_column(Text)
    geom = mapped_column(Geometry("LINESTRING", srid=4326), nullable=False)
    geog = mapped_column(Geography("LINESTRING", srid=4326), nullable=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _created_at()


class Capture(Base):
    __tablename__ = "captures"

    id: Mapped[uuid.UUID] = _pk()
    capture_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    mission_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("public.missions.id", ondelete="SET NULL")
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("public.devices.id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_location: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    storage_path: Mapped[str | None] = mapped_column(Text)
    point = mapped_column(Geography("POINT", srid=4326), nullable=True)
    accuracy_m: Mapped[float | None] = mapped_column(Float)
    heading_deg: Mapped[float | None] = mapped_column(Float)
    speed_mps: Mapped[float | None] = mapped_column(Float)
    quality: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _created_at()

    detections: Mapped[list[Detection]] = relationship(
        back_populates="capture", cascade="all, delete-orphan", lazy="selectin"
    )


class SensorAsset(Base):
    __tablename__ = "sensor_assets"

    id: Mapped[uuid.UUID] = _pk()
    mission_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("public.missions.id", ondelete="SET NULL")
    )
    capture_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("public.captures.id", ondelete="CASCADE")
    )
    modality: Mapped[str] = mapped_column(Text, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    format: Mapped[str] = mapped_column(Text, nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sync_quality: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = _created_at()


class Detection(Base):
    __tablename__ = "detections"

    id: Mapped[uuid.UUID] = _pk()
    capture_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("public.captures.id", ondelete="CASCADE"), nullable=False
    )
    urmind_class: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    bbox: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model_version_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("public.model_versions.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created_at()

    capture: Mapped[Capture] = relationship(back_populates="detections")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = _pk()
    event_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    capture_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("public.captures.id", ondelete="SET NULL")
    )
    mission_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("public.missions.id", ondelete="SET NULL")
    )
    urmind_class: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_mode: Mapped[str] = mapped_column(Text, nullable=False)
    visual_confidence: Mapped[float | None] = mapped_column(Float)
    fused_confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="detected")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    point = mapped_column(Geography("POINT", srid=4326), nullable=True)
    snapped_point = mapped_column(Geography("POINT", srid=4326), nullable=True)
    road_segment_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("public.road_segments.id", ondelete="SET NULL")
    )
    distance_to_road_m: Mapped[float | None] = mapped_column(Float)
    location_accuracy_m: Mapped[float | None] = mapped_column(Float)
    factors: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    model_version_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("public.model_versions.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _created_at()

    capture: Mapped[Capture | None] = relationship(lazy="selectin")
    context: Mapped[list[EventContext]] = relationship(
        back_populates="event", cascade="all, delete-orphan", lazy="selectin"
    )
    risk_assessments: Mapped[list[RiskAssessment]] = relationship(
        back_populates="event", cascade="all, delete-orphan", lazy="selectin"
    )


class EventContext(Base):
    __tablename__ = "event_context"

    id: Mapped[uuid.UUID] = _pk()
    event_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("public.events.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    fetched_at: Mapped[datetime] = _created_at()

    event: Mapped[Event] = relationship(back_populates="context")


class RiskAssessment(Base):
    __tablename__ = "risk_assessments"

    id: Mapped[uuid.UUID] = _pk()
    event_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("public.events.id", ondelete="CASCADE"), nullable=False
    )
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    priority_score: Mapped[float | None] = mapped_column(Float)
    uncertainty: Mapped[float | None] = mapped_column(Float)
    factors: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    responsibility_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("public.responsibility_rules.id", ondelete="SET NULL")
    )
    action_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("public.actions_catalog.id", ondelete="SET NULL")
    )
    model_version_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("public.model_versions.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created_at()

    event: Mapped[Event] = relationship(back_populates="risk_assessments")


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[uuid.UUID] = _pk()
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("public.events.id", ondelete="CASCADE")
    )
    road_segment_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("public.road_segments.id", ondelete="CASCADE")
    )
    task: Mapped[str] = mapped_column(Text, nullable=False)
    horizon_days: Mapped[int | None] = mapped_column(Integer)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    uncertainty: Mapped[float | None] = mapped_column(Float)
    model_version_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("public.model_versions.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created_at()


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = _pk()
    event_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("public.events.id", ondelete="CASCADE"), nullable=False
    )
    reviewer: Mapped[str] = mapped_column(String, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_class: Mapped[str | None] = mapped_column(Text)
    corrected_point = mapped_column(Geography("POINT", srid=4326), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
