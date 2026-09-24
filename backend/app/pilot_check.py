"""Confere a cadeia completa de uma captura real do piloto (MASTER_PLAN §25 passo 15).

Não envia foto e não cria dado: a captura entra pelo caminho normal (PWA ou
`POST /api/v1/captures/photo`), e este comando responde o que aconteceu com ela
depois, etapa por etapa.

    python -m app.pilot_check                 # a captura mais recente
    python -m app.pilot_check --capture <id>  # uma captura específica

Cada etapa sai como `ok`, `pendente` ou `falta`, com o motivo. Nenhuma etapa é
dada como boa por suposição: o que não estiver no banco aparece como ausente.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import selectors
import sys
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.core import PublicRepository

OK = "ok"
PENDING = "pendente"
MISSING = "falta"


def _step(name: str, status: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"etapa": name, "status": status, "detalhe": detail, **extra}


async def inspect(session: AsyncSession, capture_id: uuid.UUID | None) -> dict[str, Any]:
    capture = (
        (
            await session.execute(
                text(
                    "select id, capture_key, source, source_location, captured_at, storage_path, "
                    "quality, point is not null as tem_ponto "
                    "from public.captures "
                    + ("where id = :id " if capture_id else "")
                    + "order by created_at desc limit 1"
                ),
                {"id": capture_id} if capture_id else {},
            )
        )
        .mappings()
        .first()
    )
    if capture is None:
        return {
            "pronto": False,
            "motivo": (
                "nenhuma captura registrada ainda — envie uma foto real pelo app e rode de novo"
            ),
            "etapas": [],
        }

    quality = capture["quality"] or {}
    inference = quality.get("inference") or {}
    steps: list[dict[str, Any]] = [
        _step(
            "captura",
            OK,
            f"{capture['source']} em {capture['captured_at']}",
            capture_id=str(capture["id"]),
        ),
        _step(
            "localização",
            OK if capture["tem_ponto"] else MISSING,
            f"origem {capture['source_location']}"
            if capture["tem_ponto"]
            else "sem coordenada: o evento não entra no mapa",
        ),
        _step(
            "storage",
            OK if capture["storage_path"] else MISSING,
            "objeto no bucket privado" if capture["storage_path"] else "captura sem arquivo",
        ),
    ]

    queued = await session.scalar(
        text(
            "select count(*) from pgmq.q_inference_jobs where (message->>'capture_id')::uuid = :id"
        ),
        {"id": capture["id"]},
    )
    archived = await session.scalar(
        text(
            "select count(*) from pgmq.a_inference_jobs where (message->>'capture_id')::uuid = :id"
        ),
        {"id": capture["id"]},
    )
    steps.append(
        _step(
            "fila",
            OK if (queued or archived) else MISSING,
            f"{queued} na fila, {archived} arquivados"
            if (queued or archived)
            else "nenhum job enfileirado para esta captura",
        )
    )
    steps.append(
        _step(
            "worker",
            OK
            if inference.get("status")
            in {
                "inference_completed",  # legacy detection-stage record
                "detection_completed",
                "building_event",
                "enriching_context",
                "building_features",
                "assessing",
                "analysis_completed",
                "no_detection",
                "no_supported_detection",
                "no_event",
                "needs_review",
            }
            else PENDING,
            inference.get("status") or "ainda não processada",
            latency_ms=inference.get("latency_ms"),
            model_version=inference.get("model_version"),
        )
    )

    detections = (
        (
            await session.execute(
                text(
                    "select urmind_class, round(confidence::numeric, 4) as confidence "
                    "from public.detections where capture_id = :id order by confidence desc"
                ),
                {"id": capture["id"]},
            )
        )
        .mappings()
        .all()
    )
    steps.append(
        _step(
            "detecção",
            OK if detections else PENDING,
            f"{len(detections)} detecção(ões)" if detections else "nenhuma detecção persistida",
            classes=[d["urmind_class"] for d in detections],
        )
    )

    event = (
        (
            await session.execute(
                text(
                    "select e.id, e.urmind_class, e.status, e.road_segment_id, "
                    "e.distance_to_road_m, r.name as via "
                    "from public.events e "
                    "left join public.road_segments r on r.id = e.road_segment_id "
                    "where e.factors->'evidence'->'capture_ids' ? :cid "
                    "order by e.created_at desc limit 1"
                ),
                {"cid": str(capture["id"])},
            )
        )
        .mappings()
        .first()
    )
    if event is None:
        steps.append(_step("evento", PENDING, "nenhum evento consolidado a partir desta captura"))
        return {"pronto": False, "capture_id": str(capture["id"]), "etapas": steps}

    steps.append(
        _step(
            "evento", OK, f"{event['urmind_class']} ({event['status']})", event_id=str(event["id"])
        )
    )
    steps.append(
        _step(
            "road segment",
            OK if event["road_segment_id"] else MISSING,
            f"{event['via'] or 'via sem nome'} a {event['distance_to_road_m']} m"
            if event["road_segment_id"]
            else "sem trecho viário associado: fora da malha importada ou longe demais",
        )
    )

    contexts = (
        (
            await session.execute(
                text(
                    "select source, payload->>'status' as status from public.event_context "
                    "where event_id = :id order by source"
                ),
                {"id": event["id"]},
            )
        )
        .mappings()
        .all()
    )
    disponiveis = [c["source"] for c in contexts if c["status"] == "ok"]
    steps.append(
        _step(
            "contexto",
            OK if contexts else PENDING,
            f"{len(disponiveis)} de {len(contexts)} fontes responderam"
            if contexts
            else "contexto ainda não coletado",
            fontes=disponiveis,
        )
    )

    risk = (
        (
            await session.execute(
                text(
                    "select severity, priority_score, uncertainty, factors, responsibility_rule_id, "
                    "action_id from public.risk_assessments where event_id = :id "
                    "order by created_at desc limit 1"
                ),
                {"id": event["id"]},
            )
        )
        .mappings()
        .first()
    )
    if risk is None:
        steps.append(_step("risco", PENDING, "nenhuma avaliação de risco gravada"))
    else:
        factors = risk["factors"] or {}
        snapshot_ready = bool(factors.get("phase4_snapshot") and factors.get("decision_trace"))
        steps.append(
            _step(
                "risco",
                OK if snapshot_ready else PENDING,
                (
                    f"severidade {risk['severity']}, prioridade {risk['priority_score']}"
                    if snapshot_ready
                    else "avaliação sem Feature Snapshot ou DecisionTrace"
                ),
                incerteza=float(risk["uncertainty"]) if risk["uncertainty"] is not None else None,
            )
        )
        rule = (
            (
                await session.execute(
                    text(
                        "select responsible, source, version from public.responsibility_rules "
                        "where id = :id"
                    ),
                    {"id": risk["responsibility_rule_id"]},
                )
            )
            .mappings()
            .first()
            if risk["responsibility_rule_id"]
            else None
        )
        steps.append(
            _step(
                "competência",
                OK if rule else PENDING,
                f"{rule['responsible']} ({rule['version']})"
                if rule
                else "requires_triage: sem regra para este trecho",
            )
        )
        action = (
            (
                await session.execute(
                    text("select code, label from public.actions_catalog where id = :id"),
                    {"id": risk["action_id"]},
                )
            )
            .mappings()
            .first()
            if risk["action_id"]
            else None
        )
        steps.append(
            _step(
                "ação",
                OK if action else PENDING,
                action["label"] if action else "nenhuma ação sugerida",
            )
        )

    publico = await session.scalar(
        text(
            "select count(*) from public.events e "
            "join public.risk_assessments r on r.event_id = e.id where e.id = :id and "
            + PublicRepository._PUBLISHED
        ),
        {"id": event["id"]},
    )
    steps.append(
        _step(
            "painel público",
            OK if publico else PENDING,
            "evento aparece em /api/v1/public/events com risco"
            if publico
            else "risco ou autorização de publicação pendente; resultado do autor não implica mapa público",
        )
    )

    pendentes = [s["etapa"] for s in steps if s["status"] != OK]
    return {
        "pronto": not pendentes,
        "capture_id": str(capture["id"]),
        "event_id": str(event["id"]),
        "pendentes": pendentes,
        "etapas": steps,
    }


async def _main(capture_id: uuid.UUID | None) -> int:
    from app.config import get_settings
    from app.db.session import Database

    database = Database(get_settings())
    try:
        async with database.sessionmaker() as session:
            report = await inspect(session, capture_id)
    finally:
        await database.close()
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0 if report.get("pronto") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=uuid.UUID, default=None)
    args = parser.parse_args(argv)
    if sys.platform == "win32":
        return asyncio.run(
            _main(args.capture),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    return asyncio.run(_main(args.capture))


if __name__ == "__main__":
    raise SystemExit(main())
