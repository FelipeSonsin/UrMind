"""Prepara e valida a decisão humana vinculada ao split RDD2022 exato.

Suporta duas gerações de split. `--generation v1` reproduz exatamente o
comportamento histórico do MODEL V1 e continua sendo o default, para que os
artefatos científicos do V1 nunca mudem de forma. `--generation v2` avalia o
split estratificado do quality rebuild (`rdd2022_v2_splits.json`), escrevendo
artefatos com nomes próprios. A lógica de derivação de status é a mesma nos dois
casos: nenhuma decisão é inferida pelo script.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from _core import (
    DATASETS_DIR,
    PROJECT_ROOT,
    file_sha256,
    is_cloud_only,
    require_local,
    write_json_report,
    write_text_safe,
)

SPLIT = DATASETS_DIR / "splits/rdd2022_subset_splits.json"
SELECTION = DATASETS_DIR / "manifests/rdd2022_subset_selection.jsonl"
SOURCE = DATASETS_DIR / "manifests/rdd2022.json"
SHEET = DATASETS_DIR / "annotations/rdd2022_split_authorization_v1.json"
ALLOWED = {"APPROVED_FOR_MODEL_V1", "REJECTED", "REVISE_REQUIRED"}
POPULATION_ID = "crddc2022-official-all-images-train-test"

V2_SPLIT = DATASETS_DIR / "splits/rdd2022_v2_splits.json"
V2_SHEET = DATASETS_DIR / "annotations/rdd2022_split_authorization_v2.json"
V2_ALLOWED = {"APPROVED_FOR_MODEL_V2", "REJECTED", "REVISE_REQUIRED"}
V2_ROLES = ("train", "validation", "frozen_test")


def derive_status(
    sheet: dict,
    current: dict,
    *,
    allowed: set[str],
    approved_decision: str,
    authorized_status: str,
) -> dict:
    """Única fonte de verdade do status: decisão explícita, atribuível e vinculada."""
    mismatches = [key for key, value in current.items() if sheet.get(key) != value]
    decision = sheet.get("decision")
    attributable = bool(sheet.get("reviewed_by") and sheet.get("reviewed_at"))
    status = (
        "STALE_HUMAN_DECISION"
        if mismatches
        else authorized_status
        if decision == approved_decision and attributable
        else "REJECTED"
        if decision == "REJECTED" and attributable
        else "REVISE_REQUIRED"
        if decision == "REVISE_REQUIRED" and attributable
        else "PENDING_HUMAN_DECISION"
    )
    return {
        "decision": decision if decision in allowed else None,
        "status": status,
        "binding_mismatches": mismatches,
        "attributable": attributable,
    }


def sync_sheet(sheet_path: Path, current: dict, allowed: set[str]) -> dict:
    """Mantém a folha vinculada aos fingerprints correntes sem apagar decisão humana."""

    def template() -> dict:
        return {
            **current,
            "allowed_decisions": sorted(allowed),
            "decision": None,
            "reviewed_by": None,
            "reviewed_at": None,
            "note": None,
        }

    if not sheet_path.exists():
        write_text_safe(
            sheet_path, json.dumps(template(), ensure_ascii=False, indent=2) + "\n"
        )
    sheet = json.loads(require_local(sheet_path).read_text(encoding="utf-8-sig"))
    if not sheet.get("decision") and not sheet.get("reviewed_by") and not sheet.get("reviewed_at"):
        sheet = template()
        write_text_safe(
            sheet_path, json.dumps(sheet, ensure_ascii=False, indent=2) + "\n"
        )
    return sheet


def local_availability(role_files: dict[str, Path]) -> dict:
    result = {}
    for role, path in role_files.items():
        counts = {"records": 0, "local": 0, "cloud_only": 0, "missing": 0}
        for relative in require_local(path).read_text(encoding="utf-8").splitlines():
            if not relative.strip():
                continue
            counts["records"] += 1
            try:
                if is_cloud_only(PROJECT_ROOT / relative.strip()):
                    counts["cloud_only"] += 1
                else:
                    counts["local"] += 1
            except FileNotFoundError:
                counts["missing"] += 1
        result[role] = counts
    return result


def v2_bindings() -> dict:
    if not V2_SPLIT.is_file():
        raise RuntimeError("DATASET_SPLIT_NOT_AVAILABLE: split V2 removido ou indisponível")
    source = json.loads(require_local(SOURCE).read_text(encoding="utf-8-sig"))
    return {
        "dataset_id": "rdd2022",
        "dataset_version": source["version"],
        "population_id": POPULATION_ID,
        "split_manifest_sha256": file_sha256(V2_SPLIT),
        "selection_manifest_sha256": file_sha256(SELECTION),
    }


def run_v2() -> int:
    current = v2_bindings()
    sheet = sync_sheet(V2_SHEET, current, V2_ALLOWED)
    verdict = derive_status(
        sheet,
        current,
        allowed=V2_ALLOWED,
        approved_decision="APPROVED_FOR_MODEL_V2",
        authorized_status="AUTHORIZED_FOR_MODEL_V2",
    )
    split = json.loads(require_local(V2_SPLIT).read_text(encoding="utf-8-sig"))
    totals = {
        "images": sum(split["splits"][role]["images"] for role in V2_ROLES),
        "boxes": sum(split["splits"][role]["objects"] for role in V2_ROLES),
    }
    review = {
        "dataset_id": "rdd2022",
        "dataset_version": current["dataset_version"],
        "dataset_version_name": split["dataset_version_name"],
        "population_id": POPULATION_ID,
        "source_fingerprint": file_sha256(SOURCE),
        "split_manifest_sha256": current["split_manifest_sha256"],
        "selection_manifest_sha256": current["selection_manifest_sha256"],
        "population": totals,
        "splits": split["splits"],
        "distribution": {
            role: {
                "image_pct": round(
                    split["splits"][role]["images"] / totals["images"] * 100, 4
                ),
                "box_pct": round(
                    split["splits"][role]["objects"] / totals["boxes"] * 100, 4
                ),
                "negative_pct": split["splits"][role]["negative_pct"],
                "class_pct": split["splits"][role]["class_distribution_pct"],
            }
            for role in V2_ROLES
        },
        "leakage": {
            **split["leakage_group_policy"],
            "measured_violations": split["leakage_violations"],
        },
        "sequence_proxy_evidence": split["sequence_proxy_evidence"],
        "local_availability": local_availability(
            {role: PROJECT_ROOT / split["manifest_files"][role]["path"] for role in V2_ROLES}
        ),
        "v1_comparability": split["v1_comparability"],
        "changes_relative_to_v1": [
            "estratificação dentro de cada país substitui o holdout por país inteiro",
            "validation deixa de ter 5 negativos (0,13%) e passa a ter taxa de negativos comparável ao train",
            "D40 passa a ter volume utilizável em validation e no holdout congelado",
            "isolamento de leakage por componente conexo de hash com limiar dHash elevado de 4 para 8",
            "população inteira do TEST V1 (Czech + United_States, 7634 imagens) isolada na sonda report-only: nenhuma imagem do HISTORICAL_BASELINE_TEST em train/validation/frozen_test",
        ],
        "risks": [
            "train e validation passam a compartilhar países; o split deixa de medir domain shift entre países",
            "RDD2022 não publica rota/sessão; frames da mesma sessão a mais de 8 bits de distância podem atravessar splits",
            "a ordem dos nomes de arquivo foi medida e rejeitada como proxy de sessão, portanto não há controle temporal",
            "VALIDATION_V2 e FROZEN_INTERNAL_TEST_V2 contêm imagens que o V1 viu em treino: não servem para medir generalização independente do V1",
        ],
        "technical_recommendation": "APPROVE_FOR_MODEL_V2",
        "recommendation_scope": (
            "aprovar o desenho do split V2 para treinar o candidato V2; não autoriza "
            "promoção operacional nem transfere autorização para fontes bloqueadas"
        ),
    }
    write_json_report("rdd2022_v2_split_authorization_review.json", review)
    write_json_report(
        "rdd2022_v2_split_authorization_status.json",
        {
            **current,
            "sheet": V2_SHEET.relative_to(PROJECT_ROOT).as_posix(),
            "sheet_sha256": file_sha256(V2_SHEET),
            **verdict,
        },
    )
    print(f"RDD2022 split V2 authorization: {verdict['status']}")
    return 0


def bindings() -> dict:
    source = json.loads(require_local(SOURCE).read_text(encoding="utf-8-sig"))
    return {
        "dataset_id": "rdd2022",
        "dataset_version": source["version"],
        "population_id": POPULATION_ID,
        "split_manifest_sha256": file_sha256(SPLIT),
        "selection_manifest_sha256": file_sha256(SELECTION),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation", choices=("v1", "v2"), default="v1")
    if parser.parse_args().generation == "v2":
        return run_v2()
    current = bindings()
    sheet = sync_sheet(SHEET, current, ALLOWED)
    verdict = derive_status(
        sheet,
        current,
        allowed=ALLOWED,
        approved_decision="APPROVED_FOR_MODEL_V1",
        authorized_status="AUTHORIZED_FOR_MODEL_V1",
    )
    status = verdict["status"]
    split = json.loads(require_local(SPLIT).read_text(encoding="utf-8-sig"))
    total_images = sum(value["images"] for value in split["splits"].values())
    total_boxes = sum(value["objects"] for value in split["splits"].values())
    duplicates_path = DATASETS_DIR / "reports/rdd2022_duplicates.json"
    duplicates = json.loads(
        require_local(duplicates_path).read_text(encoding="utf-8-sig")
    )
    availability = local_availability(
        {
            role: DATASETS_DIR / f"splits/rdd2022_subset_{role}.txt"
            for role in ("train", "validation", "test")
        }
    )
    report = {
        "dataset_id": "rdd2022",
        "dataset_version": current["dataset_version"],
        "population_id": POPULATION_ID,
        "source_fingerprint": file_sha256(SOURCE),
        "split_manifest_sha256": current["split_manifest_sha256"],
        "selection_manifest_sha256": current["selection_manifest_sha256"],
        "population": {"images": total_images, "boxes": total_boxes},
        "splits": split["splits"],
        "distribution": {
            role: {
                "image_pct": round(values["images"] / total_images * 100, 4),
                "box_pct": round(values["objects"] / total_boxes * 100, 4),
                "class_pct": values["class_distribution_pct"],
            }
            for role, values in split["splits"].items()
        },
        "leakage": {
            **split["leakage_check"],
            "exact_duplicates_between_splits": 0,
            "near_duplicates_between_splits": 0,
            "group_overlap": 0,
            "country_overlap": 0,
            "source_overlap": 0,
            "internal_exact_groups_considered": duplicates["exact_duplicate_groups"],
            "internal_near_groups_considered": duplicates["near_duplicate_groups"],
            "duplicate_report_sha256": file_sha256(duplicates_path),
            "cross_source": {
                "rtk_br": "passed; coverage complete; nearest dHash distance 17",
                "univali_br": "source remains blocked; no authorization transferred",
                "urban_community": "source remains blocked; no authorization transferred",
            },
        },
        "local_availability": availability,
        "risks": [
            "split por país mede domain shift, mas não controla rota/sessão ausente na fonte",
            "TRAIN concentra India/Japan/Norway; VALIDATION somente China; TEST Czech/United States",
            "D40 é minoritária em VALIDATION e TEST",
            "parte dos arquivos RDD2022 está cloud-only e será recusada pelo loader até materialização explícita",
        ],
        "technical_recommendation": "APPROVE",
        "recommendation_scope": "aprovar o desenho do split; disponibilidade local continua gate operacional separado",
    }
    write_json_report("rdd2022_split_authorization_review.json", report)
    write_json_report(
        "rdd2022_split_authorization_status.json",
        {
            **current,
            "sheet": "datasets/annotations/rdd2022_split_authorization_v1.json",
            "sheet_sha256": file_sha256(SHEET),
            **verdict,
        },
    )
    print(f"RDD2022 split authorization: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
