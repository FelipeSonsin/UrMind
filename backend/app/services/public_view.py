"""Montagem do painel público a partir de dado real (§13, §15, §16, §17).

Regras que este módulo respeita, e que valem mais que o layout:

- nenhuma LLM participa; texto vem de rótulo fixo e número vem do banco;
- campo sem dado é `None` com motivo, nunca um valor plausível inventado;
- `RiskAssessment` não é previsão: previsão só sai de `predictions` real;
- imagem só é publicada quando a sanitização estiver registrada na captura.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.schemas.core import UrmindClass
from app.schemas.issue_taxonomy import get_issue
from app.schemas.public import (
    ActionPublic,
    ComponentStatus,
    ContextSourcePublic,
    DetectionPublic,
    EventSummaryPublic,
    ImageAvailability,
    ModelLatencyPublic,
    ModelMetricsPublic,
    PredictionPublic,
    ResponsibilityPublic,
    RiskExplanationPublic,
    RiskPublic,
    ScoutCameraPublic,
    ScoutPublic,
    StatusPublic,
    TraceStep,
    TransparencyPublic,
)
from app.services.context import STATUS_OK
from app.services.risk import explain_priority, uncertainty_band

CLASS_LABELS: dict[str, str] = {
    UrmindClass.ROAD_D00.value: "Trinca longitudinal",
    UrmindClass.ROAD_D10.value: "Trinca transversal",
    UrmindClass.ROAD_D20.value: "Trinca em malha",
    UrmindClass.ROAD_D40.value: "Buraco",
    UrmindClass.MANHOLE.value: "Bueiro",
    UrmindClass.SIDEWALK.value: "Calçada",
    UrmindClass.SIGNAGE.value: "Sinalização",
    UrmindClass.UNKNOWN.value: "Não classificado",
}
PRIVACY_PENDING = (
    "imagem não publicada: o frame ainda não passou por sanitização (rostos, placas "
    "e dados pessoais), então só o dado estruturado é público"
)
NO_PREDICTION = "ainda não há histórico validado suficiente para estimar a evolução deste problema"
CONTEXT_LABELS = {
    "nominatim_reverse": "Endereço aproximado (Nominatim/OpenStreetMap)",
    "overpass_pois": "Equipamentos próximos (Overpass/OpenStreetMap)",
    "open_meteo_rain": "Chuva nas 24 h anteriores (Open-Meteo)",
}


def class_label(urmind_class: str) -> str:
    if urmind_class in CLASS_LABELS:
        return CLASS_LABELS[urmind_class]
    issue = get_issue(urmind_class)
    return issue.display_name_pt if issue else urmind_class


def summary(row: dict[str, Any]) -> EventSummaryPublic:
    phase5 = (row.get("factors") or {}).get("phase5") or {}
    return EventSummaryPublic(
        id=row["id"],
        occurred_at=row["occurred_at"],
        urmind_class=row["urmind_class"],
        status=row["status"],
        evidence_mode=row["evidence_mode"],
        visual_confidence=row["visual_confidence"],
        severity=row.get("severity"),
        priority_score=row.get("priority_score"),
        risk_level=(phase5.get("risk") or {}).get("ordinal_level"),
        priority_lane=(phase5.get("priority") or {}).get("attention_lane"),
        latitude=row.get("latitude"),
        longitude=row.get("longitude"),
        snapped_latitude=row.get("snapped_latitude"),
        snapped_longitude=row.get("snapped_longitude"),
        road_name=row.get("road_name"),
    )


def image_availability(
    capture: dict[str, Any] | None, quality: dict[str, Any] | None
) -> ImageAvailability:
    """Only a separately stored, metadata-stripped, reviewed derivative may leave."""
    derivative = (quality or {}).get("public_image")
    derivative = derivative if isinstance(derivative, dict) else {}
    path = derivative.get("storage_path")
    digest = derivative.get("sha256")
    redacted = bool(
        derivative.get("metadata_stripped") is True
        and derivative.get("visible_content_reviewed") is True
        and derivative.get("content_type") == "image/jpeg"
        and isinstance(path, str)
        and path
        and path != (capture or {}).get("storage_path")
        and isinstance(digest, str)
        and len(digest) == 64
        and all(char in "0123456789abcdef" for char in digest)
        and derivative.get("source_sha256") == (quality or {}).get("sha256")
        and isinstance(derivative.get("source_sha256"), str)
        and bool(derivative.get("review_id"))
    )
    if capture is None:
        return ImageAvailability(
            available=False, privacy_redacted=False, reason="evento sem captura associada"
        )
    if not redacted:
        return ImageAvailability(available=False, privacy_redacted=False, reason=PRIVACY_PENDING)
    return ImageAvailability(available=True, privacy_redacted=True)


def context_public(rows: list[dict[str, Any]]) -> list[ContextSourcePublic]:
    """Resumo por fonte: só os campos que o painel mostra, nunca o payload cru."""
    public: list[ContextSourcePublic] = []
    for row in rows:
        payload = row.get("payload") or {}
        data = payload.get("data") or {}
        source = row["source"]
        item: dict[str, Any] = {}
        if source == "nominatim_reverse":
            # Endereço é contexto e fica no nível da via: sem número, sem CEP.
            item = {key: data.get(key) for key in ("road", "suburb", "city") if data.get(key)}
        elif source == "overpass_pois":
            nearest = data.get("nearest") or {}
            item = {
                "radius_m": data.get("radius_m"),
                **{
                    key: (value or {}).get("distance_m") if value else None
                    for key, value in nearest.items()
                },
            }
        elif source == "open_meteo_rain":
            item = {
                "rain_mm_24h": data.get("rain_mm_24h"),
                "hours_observed": data.get("hours_observed"),
            }
        public.append(
            ContextSourcePublic(
                source=source,
                label=CONTEXT_LABELS.get(source, source),
                status=payload.get("status", "context_unavailable"),
                fetched_at=row.get("fetched_at"),
                attribution=data.get("attribution"),
                summary=item if payload.get("status") == STATUS_OK else {},
            )
        )
    return public


def assessed_context_records(
    risk_factors: dict[str, Any] | None, current_records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Do not present newer provider data as an earlier assessment's evidence."""
    if risk_factors is None:
        return current_records
    snapshot = risk_factors.get("phase4_snapshot")
    if not isinstance(snapshot, dict):
        return []  # legacy assessment: temporal lineage is unavailable
    records = snapshot.get("context_records")
    return records if isinstance(records, list) else []


def risk_public(row: dict[str, Any] | None) -> RiskPublic | None:
    if row is None:
        return None
    factors = dict(row.get("factors") or {})
    phase5 = factors.get("phase5") or {}
    explanation = (
        explain_priority(factors)
        if not phase5
        else {"increased": [], "decreased": [], "unavailable": [], "baseline": None}
    )
    return RiskPublic(
        assessment_source="phase5" if phase5 else "legacy",
        severity=row["severity"],
        priority_score=row.get("priority_score"),
        impact=list((phase5.get("impact") or {}).get("potential_domains") or []),
        risk_level=(phase5.get("risk") or {}).get("ordinal_level"),
        priority_lane=(phase5.get("priority") or {}).get("attention_lane"),
        uncertainty=row.get("uncertainty"),
        uncertainty_band=uncertainty_band(row.get("uncertainty")),
        coverage=factors.get("coverage"),
        ruleset_version=phase5.get("ruleset_version") or factors.get("ruleset_version"),
        thresholds_are_calibrated=not bool(
            (phase5.get("decision_trace") or {})
            .get("provisional_parameters", {})
            .get("calibration_required", True)
        )
        if phase5
        else bool(factors.get("thresholds_are_calibrated", False)),
        explanation=RiskExplanationPublic.model_validate(explanation),
        limitations=list(factors.get("limitations") or []),
        assessed_at=row.get("created_at"),
    )


def responsibility_public(rule: dict[str, Any] | None) -> ResponsibilityPublic:
    if rule is None:
        return ResponsibilityPublic(
            status="requires_triage",
            note="é necessária validação da competência administrativa para este trecho",
        )
    return ResponsibilityPublic(
        status="assigned",
        responsible=rule["responsible"],
        source=rule["source"],
        version=rule.get("version"),
    )


def prediction_public(row: dict[str, Any] | None) -> PredictionPublic:
    if row is None:
        return PredictionPublic(available=False, reason=NO_PREDICTION)
    return PredictionPublic(
        available=True,
        task=row.get("task"),
        horizon_days=row.get("horizon_days"),
        value=row.get("value"),
        uncertainty=row.get("uncertainty"),
        model_version=row.get("version"),
        generated_at=row.get("created_at"),
    )


def decision_trace(
    *,
    capture: dict[str, Any] | None,
    detections: list[DetectionPublic],
    context: list[ContextSourcePublic],
    risk: RiskPublic | None,
    responsibility: ResponsibilityPublic,
    action: ActionPublic | None,
    model_version: str | None,
    event: EventSummaryPublic,
) -> list[TraceStep]:
    """Como o UrMind chegou à decisão, etapa a etapa, com fonte e instante."""
    ok_context = [item for item in context if item.status == STATUS_OK]
    best = max((d.confidence for d in detections), default=None)
    return [
        TraceStep(
            step="capture",
            status="done" if capture else "unavailable",
            title="Captura",
            source=capture.get("source") if capture else None,
            detail={"location_source": capture.get("source_location")} if capture else {},
            at=capture.get("captured_at") if capture else None,
        ),
        TraceStep(
            step="detection",
            status="done" if detections else "unavailable",
            title="Detecção visual",
            source=f"YOLOX · {model_version}" if model_version else "YOLOX",
            detail={
                "classe": class_label(event.urmind_class),
                "detecções": len(detections),
                "confiança": best,
            },
        ),
        TraceStep(
            step="context",
            status="done" if ok_context else "unavailable",
            title="Contexto urbano",
            source=", ".join(item.source for item in ok_context) or None,
            detail={"fontes_consultadas": len(context), "fontes_disponíveis": len(ok_context)},
        ),
        TraceStep(
            step="risk",
            status="done" if risk else "unavailable",
            title="Avaliação de risco",
            source=risk.ruleset_version if risk else None,
            detail=(
                {
                    "severidade": risk.severity,
                    "prioridade": risk.priority_lane or risk.priority_score,
                    "incerteza": risk.uncertainty_band,
                }
                if risk
                else {}
            ),
            at=risk.assessed_at if risk else None,
        ),
        TraceStep(
            step="decision",
            status="done" if responsibility.status == "assigned" else "unavailable",
            title="Competência",
            source=responsibility.source,
            detail=(
                {"responsável": responsibility.responsible}
                if responsibility.status == "assigned"
                else {"estado": "em triagem"}
            ),
        ),
        TraceStep(
            step="action",
            status="done" if action else "unavailable",
            title="Ação sugerida",
            source=f"actions_catalog {action.version}" if action else None,
            detail={"ação": action.label} if action else {},
        ),
    ]


def status_public(
    *,
    overview: dict[str, Any],
    database_ok: bool,
    detector: dict[str, Any] | None,
    scout: ScoutPublic,
) -> StatusPublic:
    return StatusPublic(
        api=ComponentStatus(name="API", status="ok"),
        database=ComponentStatus(
            name="Banco e PostGIS",
            status="ok" if database_ok else "unavailable",
            detail=None if database_ok else "persistência indisponível",
        ),
        detector=ComponentStatus(
            name="Detector",
            status=("degraded" if detector.get("stage") == "EXPERIMENTAL_SHADOW" else "ok")
            if detector
            else "unavailable",
            detail=(
                f"{detector['version']} ({detector.get('stage', 'sem estágio')})"
                if detector
                else "nenhum modelo autorizado para este modo"
            ),
        ),
        scout=ComponentStatus(
            name="Scout",
            status="ok"
            if scout.status == "live"
            else "degraded"
            if scout.status == "degraded"
            else "unavailable",
            detail=scout.camera.reason if scout.status != "live" else None,
        ),
        last_event_at=overview.get("last_event_at"),
        events_total=int(overview.get("events_total") or 0),
        road_segments_total=int(overview.get("road_segments_total") or 0),
        pilot_area=overview.get("pilot_area"),
        checked_at=datetime.now(UTC),
    )


def scout_public(device: dict[str, Any] | None, camera: ScoutCameraPublic) -> ScoutPublic:
    """Estado do Scout a partir do que existe. Sensor ausente não vira número."""
    if device is None:
        return ScoutPublic(
            status="no_device",
            camera=camera,
            telemetry={},
        )
    last_seen = device.get("last_capture_at")
    live = camera.mode != "unavailable"
    return ScoutPublic(
        status="live" if live else "degraded" if last_seen else "offline",
        device_code=device.get("code"),
        last_seen=last_seen,
        camera=camera,
        telemetry={
            key: device[key] for key in ("kind", "firmware_version") if device.get(key) is not None
        },
    )


def transparency_public(
    model: dict[str, Any] | None, dataset: dict[str, Any] | None
) -> TransparencyPublic:
    metrics = (model or {}).get("metrics") or {}
    evaluation = metrics.get("evaluation_full") or metrics.get("validation") or {}
    benchmark = metrics.get("benchmark") or {}
    serving = metrics.get("serving") or {}
    latency = benchmark.get("latency_ms") or {}
    limitations = [
        "modelo em estágio inicial: as métricas abaixo são as medidas, sem arredondamento para cima",
        "limiares de risco ainda não calibrados com eventos revisados em campo",
        "o sistema não mede profundidade em centímetros nem afirma gravidade física",
    ]
    if metrics.get("stage_note"):
        limitations.insert(0, str(metrics["stage_note"]))
    return TransparencyPublic(
        model_name=(model or {}).get("name"),
        model_version=(model or {}).get("version"),
        stage=(model or {}).get("operational_status") or metrics.get("stage"),
        stage_note=(
            "Análise experimental; rejeitado para produção no Frozen Test."
            if (model or {}).get("operational_status") == "EXPERIMENTAL_SHADOW"
            and metrics.get("quality_classification") == "REJECTED"
            else metrics.get("stage_note")
        ),
        classes=list(serving.get("class_names") or []),
        input_size=list(serving.get("input_size") or []),
        score_threshold=serving.get("score_threshold"),
        metrics=ModelMetricsPublic(
            map50=evaluation.get("map50"),
            map50_95=evaluation.get("map50_95"),
            precision=evaluation.get("precision"),
            recall=evaluation.get("recall"),
            f1=evaluation.get("f1"),
            per_class={
                label: {
                    key: values.get(key) for key in ("precision", "recall", "f1", "ap50", "ap50_95")
                }
                for label, values in (evaluation.get("per_class") or {}).items()
            },
            not_computed=list(metrics.get("metrics_not_computed") or []),
            samples=evaluation.get("samples"),
        ),
        latency=ModelLatencyPublic(
            mean_ms=latency.get("mean"),
            p50_ms=latency.get("p50"),
            p95_ms=latency.get("p95"),
            fps_approx=benchmark.get("fps_approx"),
            execution_provider=benchmark.get("execution_provider"),
            hardware=(benchmark.get("hardware") or {}).get("cpu"),
        ),
        dataset_name=(dataset or {}).get("name"),
        dataset_version=(dataset or {}).get("version"),
        dataset_license=(dataset or {}).get("license"),
        dataset_source=(dataset or {}).get("source"),
        context_sources=[
            {"source": "OpenStreetMap / Overpass", "use": "malha viária e equipamentos próximos"},
            {"source": "Nominatim", "use": "endereço aproximado (contexto, não é a coordenada)"},
            {"source": "Open-Meteo", "use": "chuva nas 24 h anteriores"},
        ],
        rules={
            "severidade_e_prioridade": "regras versionadas, sem LLM",
            "responsável": "tabela de competência com fundamento legal; sem regra, requer triagem",
            "ação": "catálogo versionado de ações; sempre sugestão",
        },
        limitations=limitations,
    )
