"""Candidatos de dataset para a taxonomia V3 (Fase 10A, pesquisa — sem download).

O arquivo `datasets/metadata/taxonomy_v2_dataset_candidates.json` é o registro
pesquisado. Este módulo o valida e deriva o estado de dado por classe, que
precisa coincidir com `dataset_status` em `app.schemas.issue_taxonomy`.

Regras conservadoras: campo desconhecido é `null`, nunca "permitido"; domínio
aéreo não vira READY; licença não comercial ou proveniência não verificada
não chegam a READY_FOR_CURATION.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.schemas.issue_taxonomy import ISSUES, DatasetReadiness, ModelSupportStatus

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CANDIDATES_PATH = PROJECT_ROOT / "datasets" / "metadata" / "taxonomy_v2_dataset_candidates.json"

REQUIRED_FIELDS = (
    "SOURCE",
    "SOURCE_URL",
    "OWNER",
    "VERSION",
    "PUBLICATION",
    "LICENSE",
    "LICENSE_URL",
    "IMAGE_DOMAIN",
    "CAPTURE_DEVICE",
    "GEOGRAPHY",
    "ANNOTATION_TYPE",
    "IMAGE_COUNT",
    "INSTANCE_COUNT",
    "CLASS_LIST",
    "CLASS_MAPPING",
    "NEGATIVE_IMAGES",
    "EMPTY_IMAGES",
    "DOMAIN_MATCH",
    "TRAINING_ALLOWED",
    "COMMERCIAL_USE_ALLOWED",
    "REDISTRIBUTION_ALLOWED",
    "ATTRIBUTION_REQUIRED",
    "PROVENANCE_VERIFIED",
    "KNOWN_LIMITATIONS",
)
DOMAIN_MATCHES = frozenset({"GOOD_MATCH", "PARTIAL_MATCH", "DOMAIN_MISMATCH"})


def load_candidates(path: Path = CANDIDATES_PATH) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["candidates"]


def validate_candidates(candidates: list[dict[str, Any]]) -> list[str]:
    """Devolve a lista de violações; vazia quando o registro é coerente."""
    candidate_codes = {
        issue.issue_code
        for issue in ISSUES
        if issue.model_support_status is not ModelSupportStatus.EXPERIMENTAL_MODEL
    }
    problems: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        cid = item.get("id", "<sem id>")
        if cid in seen:
            problems.append(f"{cid}: id duplicado")
        seen.add(cid)
        missing = [field for field in REQUIRED_FIELDS if field not in item]
        if missing:
            problems.append(f"{cid}: campos ausentes {missing}")
            continue
        readiness = item.get("READINESS")
        if readiness not in {r.value for r in DatasetReadiness} or readiness == "CURATED_IN_USE":
            problems.append(f"{cid}: READINESS inválido {readiness!r}")
        source_missing = item.get("SOURCE_STATUS") == "NEEDS_DATA_SOURCE"
        if source_missing and (
            readiness != "NEEDS_MORE_DATA"
            or item["SOURCE_URL"] is not None
            or item["LICENSE"] is not None
            or item["PROVENANCE_VERIFIED"] is not False
            or item["TRAINING_ALLOWED"] is not None
        ):
            problems.append(f"{cid}: fonte ausente não autoriza licença, provenance ou treinamento")
        if item["DOMAIN_MATCH"] not in DOMAIN_MATCHES and not (
            source_missing and item["DOMAIN_MATCH"] is None
        ):
            problems.append(f"{cid}: DOMAIN_MATCH inválido")
        if item["DOMAIN_MATCH"] == "DOMAIN_MISMATCH" and readiness != "DOMAIN_MISMATCH":
            problems.append(f"{cid}: domínio incompatível não pode ter READINESS {readiness}")
        if readiness == DatasetReadiness.READY_FOR_CURATION.value and not (
            item["PROVENANCE_VERIFIED"] is True
            and item["TRAINING_ALLOWED"] is True
            and item["LICENSE"]
            and item["COMMERCIAL_USE_ALLOWED"] is not False
        ):
            problems.append(f"{cid}: READY_FOR_CURATION exige licença e proveniência verificadas")
        unknown = set(item.get("target_issue_codes") or []) - candidate_codes
        if unknown or not item.get("target_issue_codes"):
            problems.append(f"{cid}: target_issue_codes fora das classes candidatas {unknown}")
    return problems


def readiness_by_class(candidates: list[dict[str, Any]]) -> dict[str, DatasetReadiness]:
    """Melhor estado alcançável por classe candidata, a partir do registro pesquisado."""
    result: dict[str, DatasetReadiness] = {}
    for issue in ISSUES:
        if issue.model_support_status is ModelSupportStatus.EXPERIMENTAL_MODEL:
            continue
        states = {
            c["READINESS"]
            for c in candidates
            if issue.issue_code in c.get("target_issue_codes", [])
        }
        if DatasetReadiness.READY_FOR_CURATION.value in states:
            result[issue.issue_code] = DatasetReadiness.READY_FOR_CURATION
        elif DatasetReadiness.NEEDS_HUMAN_REVIEW.value in states:
            result[issue.issue_code] = DatasetReadiness.NEEDS_HUMAN_REVIEW
        else:
            result[issue.issue_code] = DatasetReadiness.NEEDS_MORE_DATA
    return result
