"""Observabilidade sem infraestrutura nova (MASTER_PLAN §19).

O plano é explícito: nada de pilha de monitoramento separada. O que existe aqui
lê estruturas que já estão no banco — `pg_stat_statements` para SQL caro e as
próprias tabelas do domínio para responder se há material suficiente para
falar de drift. Métrica de fila e latência do Worker fica em
`InferenceRepository.stats()`, que já as mede.

Nenhuma função inventa número: quando falta dado, o retorno diz que falta.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import ModelVersion

# Amostra mínima para comparar duas distribuições sem que o resultado seja
# ruído. PROVISÓRIO (§31.16): o número ainda não nasceu de medição no piloto —
# revisar quando houver histórico revisado.
MIN_SAMPLES_FOR_DRIFT = 200
NOT_ENOUGH_DATA = "DRIFT_NOT_ENOUGH_DATA"

_SLOW_QUERIES = """
select calls,
       round(mean_exec_time::numeric, 2) as mean_ms,
       round(total_exec_time::numeric, 2) as total_ms,
       rows,
       left(regexp_replace(query, '\\s+', ' ', 'g'), 160) as query
  from extensions.pg_stat_statements
 where query not ilike '%%pg_stat_statements%%'
 order by mean_exec_time desc
 limit :limit
"""


async def slow_queries(session: AsyncSession, limit: int = 10) -> dict[str, Any]:
    """SQL mais caro por tempo médio, direto do `pg_stat_statements` do Supabase.

    A extensão vive no schema `extensions`. Se não estiver instalada, o retorno
    diz isso em vez de estourar: observabilidade não pode derrubar operação.
    """
    installed = await session.scalar(
        text("select count(*) from pg_extension where extname = 'pg_stat_statements'")
    )
    if not installed:
        return {
            "available": False,
            "reason": "pg_stat_statements não está instalada neste projeto",
            "queries": [],
        }
    rows = (await session.execute(text(_SLOW_QUERIES), {"limit": limit})).mappings().all()
    return {"available": True, "queries": [dict(row) for row in rows]}


async def drift_status(session: AsyncSession) -> dict[str, Any]:
    """Há material para afirmar drift? Enquanto não houver, ninguém afirma.

    Comparar a distribuição de produção com a de treino exige duas amostras
    reais. O UrMind ainda não tem detecções suficientes em produção, então o
    estado correto é `DRIFT_NOT_ENOUGH_DATA` — e não um gráfico de ruído
    apresentado como monitoramento.
    """
    counts = (
        (
            await session.execute(
                text(
                    "select (select count(*) from public.detections) as detections, "
                    "(select count(*) from public.events) as events, "
                    "(select count(*) from public.reviews) as reviews, "
                    "(select count(distinct model_version_id) from public.detections) as models"
                )
            )
        )
        .mappings()
        .one()
    )
    detections = int(counts["detections"])
    if detections < MIN_SAMPLES_FOR_DRIFT:
        return {
            "status": NOT_ENOUGH_DATA,
            "detections": detections,
            "events": int(counts["events"]),
            "reviews": int(counts["reviews"]),
            "minimum_samples": MIN_SAMPLES_FOR_DRIFT,
            "missing": MIN_SAMPLES_FOR_DRIFT - detections,
            "reason": (
                "comparação de distribuição exige amostra real de produção; "
                f"há {detections} detecções e o mínimo declarado é {MIN_SAMPLES_FOR_DRIFT}"
            ),
        }
    by_class = (
        (
            await session.execute(
                text(
                    "select urmind_class, count(*) as total, "
                    "round(avg(confidence)::numeric, 4) as mean_confidence "
                    "from public.detections group by 1 order by 2 desc"
                )
            )
        )
        .mappings()
        .all()
    )
    return {
        "status": "DRIFT_BASELINE_READY",
        "detections": detections,
        "events": int(counts["events"]),
        "reviews": int(counts["reviews"]),
        "model_versions": int(counts["models"]),
        "by_class": [dict(row) for row in by_class],
        "next_step": (
            "há amostra suficiente para comparar produção contra a distribuição de "
            "validação do modelo promovido"
        ),
    }


async def model_lineage(session: AsyncSession) -> dict[str, Any]:
    """Cadeia DatasetVersion → TrainingRun → Checkpoint → ONNX → ModelVersion → Detection.

    Só leitura: o registry continua sendo `model_versions`, e nada aqui cria uma
    segunda fonte de verdade. Cada elo ausente aparece como `null`, para que a
    falta seja visível em vez de presumida.
    """
    row = (
        (
            await session.execute(
                text(
                    "select m.id, m.name, m.version, m.checksum, m.promoted_at, m.metrics, "
                    "d.name as dataset_name, d.version as dataset_version "
                    "from public.model_versions m "
                    "left join public.dataset_versions d on d.id = m.dataset_version_id "
                    "order by m.promoted_at desc nulls last limit 1"
                )
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return {"promoted": False, "reason": "nenhum modelo promovido"}
    metrics = row["metrics"] or {}
    status = ModelVersion(metrics=metrics, promoted_at=row["promoted_at"]).operational_status
    serving = metrics.get("serving") or {}
    training = metrics.get("training") or {}
    detections = await session.scalar(
        text("select count(*) from public.detections where model_version_id = :id"),
        {"id": row["id"]},
    )
    chain = {
        "dataset_version": (
            f"{row['dataset_name']} {row['dataset_version']}" if row["dataset_name"] else None
        ),
        "split_fingerprint": (metrics.get("fingerprints") or {}).get("split_fingerprint"),
        "training_run": (training.get("run") or {}).get("mlflow_run_id"),
        "checkpoint_sha256": serving.get("checkpoint_sha256"),
        "onnx_path": serving.get("onnx_path"),
        "onnx_sha256": row["checksum"],
        "parity_passed": (serving.get("parity") or {}).get("passed"),
        "model_version": row["version"],
        "stage": status,
        "git_commit": (metrics.get("code") or {}).get("git_commit"),
        "detections_attributed": int(detections or 0),
    }
    missing = [name for name, value in chain.items() if value is None]
    return {
        "promoted": status == "PRODUCTION_APPROVED",
        "chain": chain,
        "missing_links": missing,
        "complete": not missing,
    }
