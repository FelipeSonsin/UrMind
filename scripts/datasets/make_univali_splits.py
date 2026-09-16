"""Partição do UNIVALI por grupo real, com holdout brasileiro bloqueado.

O UNIVALI inteiro é **EXTERNAL_TEST_BR**: nenhuma amostra entra em treino. É a
decisão conservadora e também a mais útil — é a única fonte com anotação humana
em rodovia brasileira, e gastá-la em treino destruiria o único termômetro
honesto de desempenho no domínio real.

Dentro dela ainda faz sentido partir, e a partição é por **grupo**, nunca por
imagem:

    holdout_br_frozen   congelado. Medição final, uma vez, sem reajuste.
    external_test_br    diagnóstico repetível durante o desenvolvimento.

O agrupamento vem dos identificadores que a fonte publica no nome da pasta
(UF, rodovia, trecho) — não de similaridade visual. Isso importa: dentro de um
trecho as capturas são sequenciais e o dHash NÃO detecta esse parentesco, então
uma proteção baseada em similaridade de pixel deixaria passar exatamente o
vazamento que o §8.4 descreve.

Grupos ligados por duplicata exata ou quase-duplicata são unidos antes da
partição, para que uma imagem repetida não apareça dos dois lados.

    python -B scripts/datasets/make_univali_splits.py
    python -B scripts/datasets/make_univali_splits.py --group-by segment
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict

from _core import (
    DATASETS_DIR,
    PROJECT_ROOT,
    configure_stdout,
    file_sha256,
    provenance,
    require_local,
    write_json_report,
    write_text_safe,
)

BOXES_MANIFEST = DATASETS_DIR / "manifests" / "univali_br_boxes.jsonl"
VALIDATION_REPORT = DATASETS_DIR / "reports" / "univali_box_validation.json"
SPLITS_DIR = DATASETS_DIR / "splits"
REPORT_NAME = "univali_br_external_test_splits.json"

GROUP_FIELDS = {
    "uf": "group_uf",
    "road": "group_road",
    "segment": "group_segment",
}
# NÃO é "EXTERNAL_TEST_BR operacional". É candidato: as caixas são derivação
# não validada, a classe canônica não foi aprovada e 1.671 máscaras vazias
# continuam sem significado declarado. Chamar de teste externo pronto seria
# prometer uma medição que este artefato ainda não sustenta.
USAGE = "CANDIDATE_EXTERNAL_TEST_BR"
EMPTY_MASK_STATUS = "EMPTY_MASK_SEMANTICS_UNRESOLVED"


class UnionFind:
    def __init__(self, items):
        self.parent = {item: item for item in items}

    def find(self, item):
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left, right):
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[b] = a


def load_rows() -> list[dict]:
    return [
        json.loads(line)
        for line in require_local(BOXES_MANIFEST).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class ValidationRequired(SystemExit):
    """Pré-requisito ausente. Bloqueia a publicação em vez de assumir zero."""

    def __init__(self, motivo: str) -> None:
        super().__init__(
            f"VALIDATION_REQUIRED: {motivo}\n"
            "O split do UNIVALI não pode ser publicado como livre de vazamento "
            "sem a validação de duplicatas correspondente a ESTA versão das "
            "caixas. Rode, nesta ordem:\n"
            "  python -B scripts/datasets/convert_univali_masks.py\n"
            "  python -B scripts/datasets/validate_univali_boxes.py\n"
            "  python -B scripts/datasets/make_univali_splits.py"
        )


def require_validation(boxes_sha256: str) -> tuple[list[list[str]], dict]:
    """Exige a validação de duplicatas desta versão das caixas. Fail-closed.

    Ausência de evidência não é evidência de ausência: um relatório faltando
    significava, antes, "zero duplicatas", e o split saía marcado como livre de
    vazamento sem que ninguém tivesse procurado. Numa execução parcial do
    pipeline isso colocaria duplicatas em lados opostos com
    `leakage_check.passed: true` — a garantia viraria decoração.

    O vínculo com a versão dos dados é o SHA-256 do manifesto de caixas, que a
    própria validação grava em `integrity.boxes_manifest_sha256`. Nome de arquivo
    não serve de vínculo: ele continua igual depois de uma reconversão.
    """
    if not VALIDATION_REPORT.is_file():
        raise ValidationRequired(
            f"{VALIDATION_REPORT.relative_to(PROJECT_ROOT).as_posix()} não existe"
        )
    try:
        data = json.loads(require_local(VALIDATION_REPORT).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationRequired(f"relatório de validação ilegível: {exc}") from exc

    duplicatas = data.get("duplicates")
    if not isinstance(duplicatas, dict) or not isinstance(data.get("passed"), bool):
        raise ValidationRequired(
            "relatório de validação incompleto: faltam `passed` e/ou `duplicates`"
        )
    for campo in ("exact_sha256_groups", "near_duplicate_pairs"):
        if not isinstance(duplicatas.get(campo), list):
            raise ValidationRequired(f"relatório de validação sem `duplicates.{campo}`")

    registrado = (data.get("integrity") or {}).get("boxes_manifest_sha256")
    if not registrado:
        raise ValidationRequired(
            "relatório de validação não registra `integrity.boxes_manifest_sha256`; "
            "sem isso não dá para saber que ele descreve estas caixas"
        )
    if registrado != boxes_sha256:
        raise ValidationRequired(
            "a validação descreve outra versão das caixas "
            f"(validação {registrado[:12]}…, manifesto atual {boxes_sha256[:12]}…). "
            "Revalide depois de reconverter"
        )
    if not data["passed"]:
        raise ValidationRequired(
            f"a validação reprovou esta versão das caixas: {data.get('failures')}"
        )

    # `passed` sozinho não basta: a comparação com a fonte de treino podia ter
    # sido pulada. Um split candidato a avaliação externa que nunca olhou para o
    # RDD2022 não pode se declarar livre de vazamento.
    cruzada = data.get("cross_source_contamination") or {}
    if cruzada.get("skipped") is not False:
        raise ValidationRequired(
            "a verificação cruzada contra o RDD2022 não foi executada "
            f"({cruzada.get('reason') or 'skipped'})"
        )
    if not cruzada.get("valid"):
        raise ValidationRequired(
            f"a verificação cruzada não é válida: {cruzada.get('reason')}"
        )
    if not cruzada.get("inventory_sha256"):
        raise ValidationRequired(
            "a verificação cruzada não registra o SHA-256 do inventário do RDD2022; "
            "sem isso não dá para saber contra qual versão a comparação foi feita"
        )
    inventario = (data.get("integrity") or {}).get("rdd_inventory_sha256")
    if inventario != cruzada["inventory_sha256"]:
        raise ValidationRequired(
            "o inventário citado na integridade não é o mesmo usado na comparação "
            f"({inventario} != {cruzada['inventory_sha256']})"
        )

    clusters = [g["directories"] for g in duplicatas["exact_sha256_groups"]]
    clusters += [[p["a"], p["b"]] for p in duplicatas["near_duplicate_pairs"]]
    evidencia = {
        "report": VALIDATION_REPORT.relative_to(PROJECT_ROOT).as_posix(),
        "report_sha256": file_sha256(VALIDATION_REPORT),
        "boxes_manifest_sha256": registrado,
        "passed": data["passed"],
        "exact_duplicate_groups": len(duplicatas["exact_sha256_groups"]),
        "near_duplicate_pairs": len(duplicatas["near_duplicate_pairs"]),
        "generated_at": data.get("generated_at"),
        "cross_source": {
            "performed": True,
            "compared_against": cruzada.get("compared_against"),
            "inventory_sha256": cruzada["inventory_sha256"],
            "inventory_records": cruzada.get("inventory_records"),
            "exact_sha256_matches": cruzada.get("exact_sha256_matches"),
            "nearest_dhash_distance": cruzada.get("nearest_dhash_distance"),
            "threshold": cruzada.get("threshold"),
        },
    }
    return clusters, evidencia


def partition(groups: dict[str, list[dict]], holdout_ratio: float, seed: int) -> dict:
    """Distribuição gulosa determinística: grupo inteiro para um lado só.

    Grupos maiores primeiro; cada um vai para o lado com maior déficit em
    relação à própria cota. Empate resolvido pelo nome, para o resultado não
    depender da ordem em que o sistema de arquivos devolveu as pastas.
    """
    import random

    total_images = sum(len(rows) for rows in groups.values())
    targets = {
        "holdout_br_frozen": holdout_ratio * total_images,
        "external_test_br": (1 - holdout_ratio) * total_images,
    }
    assigned: dict[str, list[dict]] = {name: [] for name in targets}
    assigned_groups: dict[str, list[str]] = {name: [] for name in targets}

    names = list(groups)
    random.Random(seed).shuffle(names)
    names.sort(key=lambda name: len(groups[name]), reverse=True)

    for name in names:
        destination = min(
            targets,
            key=lambda side: (len(assigned[side]) - targets[side], side),
        )
        assigned[destination].extend(groups[name])
        assigned_groups[destination].append(name)
    return {"rows": assigned, "groups": assigned_groups}


def verify_no_leakage(result: dict, rows_by_directory: dict) -> list[str]:
    """Conferência independente, feita no dado produzido e não na implementação."""
    problems = []
    sides = list(result["groups"])

    seen_groups: dict[str, list[str]] = defaultdict(list)
    for side, names in result["groups"].items():
        for name in names:
            seen_groups[name].append(side)
    for name, where in seen_groups.items():
        if len(where) > 1:
            problems.append(f"grupo {name} aparece em {where}")

    seen_dirs: dict[str, list[str]] = defaultdict(list)
    for side, rows in result["rows"].items():
        for row in rows:
            seen_dirs[row["directory"]].append(side)
    for directory, where in seen_dirs.items():
        if len(where) > 1:
            problems.append(f"amostra {directory} aparece em {where}")

    # Nenhum identificador de trecho pode cruzar os lados, mesmo quando o
    # agrupamento escolhido foi mais grosso (rodovia ou UF).
    segment_sides: dict[str, set] = defaultdict(set)
    for side, rows in result["rows"].items():
        for row in rows:
            segment_sides[row["group_segment"]].add(side)
    for segment, where in segment_sides.items():
        if len(where) > 1:
            problems.append(f"trecho {segment} cruza os lados {sorted(where)}")

    for side in sides:
        if not result["rows"][side]:
            problems.append(f"lado {side} ficou vazio")
    return problems


def main() -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group-by", choices=sorted(GROUP_FIELDS), default="road")
    parser.add_argument("--holdout-ratio", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=20260910)
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()

    if not 0 < args.holdout_ratio < 1:
        raise SystemExit("--holdout-ratio precisa ficar entre 0 e 1")

    rows = load_rows()
    field = GROUP_FIELDS[args.group_by]

    # Fail-closed: sem a validação desta versão das caixas, nada é publicado.
    boxes_sha256 = file_sha256(BOXES_MANIFEST)
    clusters, validation_evidence = require_validation(boxes_sha256)

    raw_groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        raw_groups[row[field]].append(row)

    # Une grupos ligados por duplicata antes de partir.
    union = UnionFind(list(raw_groups))
    directory_group = {row["directory"]: row[field] for row in rows}
    merged_by_duplicates = 0
    for cluster in clusters:
        related = sorted({directory_group[d] for d in cluster if d in directory_group})
        for other in related[1:]:
            if union.find(related[0]) != union.find(other):
                merged_by_duplicates += 1
            union.union(related[0], other)

    components: dict[str, list[dict]] = defaultdict(list)
    component_members: dict[str, list[str]] = defaultdict(list)
    for name, members in raw_groups.items():
        root = union.find(name)
        components[root].extend(members)
        component_members[root].append(name)

    if len(components) < 2:
        raise SystemExit(
            f"apenas {len(components)} grupo(s) independente(s) com "
            f"--group-by {args.group_by}; não há como reservar holdout sem dividir "
            "um grupo. Use um agrupamento mais fino ou mantenha a fonte inteira "
            "como EXTERNAL_TEST_BR sem partição."
        )

    result = partition(components, args.holdout_ratio, args.seed)
    problems = verify_no_leakage(result, {r["directory"]: r for r in rows})

    def side_summary(side: str) -> dict:
        side_rows = result["rows"][side]
        boxes = sum(len(r["boxes"]) for r in side_rows)
        positivas = [r for r in side_rows if r.get("mask_status") != EMPTY_MASK_STATUS]
        vazias = [r for r in side_rows if r.get("mask_status") == EMPTY_MASK_STATUS]
        return {
            "images": len(side_rows),
            "positive_mask_images": len(positivas),
            "empty_mask_images": len(vazias),
            "empty_mask_status": EMPTY_MASK_STATUS,
            "usable_for_metrics": len(positivas),
            "usable_note": (
                "só as positivas têm caixa. As vazias viajam com o grupo para não "
                "vazarem depois, mas não medem falso positivo enquanto a semântica "
                "delas estiver pendente."
            ),
            "boxes": boxes,
            "groups": sorted(
                name
                for root in result["groups"][side]
                for name in component_members[root]
            ),
            "group_count": len(result["groups"][side]),
            "segments": sorted({r["group_segment"] for r in side_rows}),
            "ufs": sorted({r["group_uf"] for r in side_rows}),
            "class_counts": dict(
                Counter(b["derived_label"] for r in side_rows for b in r["boxes"])
            ),
            "size_tiers": dict(
                Counter(b["size_tier"] for r in side_rows for b in r["boxes"])
            ),
        }

    summary = {side: side_summary(side) for side in result["rows"]}
    total_images = len(rows)

    report = {
        "version": 1,
        "artifact_kind": "derived_split_manifest",
        "usage": USAGE,
        "scope": (
            "partição do UNIVALI dentro de EXTERNAL_TEST_BR. Nenhuma amostra entra "
            "em treino. Lista caminhos; não copia imagem."
        ),
        **provenance(
            __file__,
            source_dataset="univali_br",
            source_version="mendeley-v4",
            transform="partição por grupo com holdout brasileiro congelado",
            params={
                "group_by": args.group_by,
                "group_field": field,
                "holdout_ratio": args.holdout_ratio,
                "seed": args.seed,
                "duplicate_union": True,
            },
        ),
        "inputs": total_images,
        "outputs": sum(len(v) for v in result["rows"].values()),
        "dropped": 0,
        "drop_reasons": {},
        "integrity": {
            "boxes_manifest_sha256": boxes_sha256,
            # Cadeia conferível: caixas → validação → split. Cada elo cita o
            # SHA-256 do anterior, então um split não pode ser lido como
            # validado por um relatório que descreve outra conversão.
            "validated_by": validation_evidence,
        },
        "raw_modified": False,
        "grouping": {
            "level": args.group_by,
            "basis": (
                "identificador publicado no nome da pasta pela fonte "
                "(UF, rodovia, trecho). Não é inferência visual."
            ),
            "independent_groups": len(components),
            "groups_merged_by_duplicates": merged_by_duplicates,
            "why_not_pixel_similarity": (
                "Capturas do mesmo trecho são sequenciais mas visualmente distintas: "
                "o dHash encontra pouquíssimos pares entre elas. Agrupar por "
                "similaridade deixaria passar o parentesco real."
            ),
        },
        "splits": summary,
        "achieved_ratios": {
            side: round(len(result["rows"][side]) / total_images, 4) for side in result["rows"]
        },
        "leakage_check": {
            "performed": True,
            "duplicate_validation": "REQUIRED_AND_PRESENT",
            "duplicate_validation_evidence": validation_evidence,
            "checks": [
                "validação de duplicatas presente, aprovada e ligada a este manifesto",
                "grupos ligados por duplicata unidos antes da partição",
                "nenhum grupo em mais de um lado",
                "nenhuma amostra em mais de um lado",
                "nenhum trecho cruzando os lados",
                "nenhum lado vazio",
            ],
            "problems": problems,
            # `passed` só existe porque a validação rodou: sem ela o script
            # aborta antes de chegar aqui (ValidationRequired).
            "passed": not problems,
        },
        "holdout_policy": {
            "frozen_side": "holdout_br_frozen",
            "rule": (
                "Congelado antes de qualquer treino. Medido uma vez, sem reajuste "
                "depois de observar resultado. Não participa de escolha de limiar, "
                "de hiperparâmetro nem de seleção de modelo."
            ),
            "training_use": "PROIBIDO em qualquer lado; a fonte inteira é EXTERNAL_TEST_BR",
        },
        "evaluation_readiness": {
            "is_detection_evaluation_ready": False,
            "usage": USAGE,
            "blockers": [
                (
                    "instance_semantics_validated=false: componente conexo não foi "
                    "verificado como objeto independente"
                ),
                "canonical_mapping_validated=false: urmind_class continua null",
                (
                    "empty_mask_semantics_validated=false: máscaras vazias sem "
                    "significado declarado, então falso positivo não é medível"
                ),
                (
                    "cobertura: só as imagens com máscara positiva têm caixa; o "
                    "conjunto mede localização em positivos, não desempenho no domínio"
                ),
            ],
            "training_allowed": False,
        },
        "limitations": [
            (
                "Mede rodovia federal brasileira em poucas UFs e rodovias; não "
                "representa via urbana brasileira nem o Brasil inteiro."
            ),
            (
                "O rótulo da caixa continua sendo UNIVALI_POTHOLE: a correspondência "
                "com URMIND_ROAD_D40 não foi verificada de forma independente."
            ),
            (
                "A fonte não publica GPS, sessão nem vídeo; trecho é o agrupamento "
                "mais fino disponível."
            ),
        ],
    }

    if args.no_report:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if not problems else 1

    for side, side_rows in result["rows"].items():
        listing = "\n".join(sorted(r["image_relpath"] for r in side_rows)) + "\n"
        write_text_safe(SPLITS_DIR / f"univali_br_{side}.txt", listing)
    destination = write_json_report(REPORT_NAME, report, subdir="splits")

    print(f"Relatório: {destination.relative_to(PROJECT_ROOT)}")
    for side, data in summary.items():
        print(
            f"  {side:<20} {data['images']:>5} imagens | {data['boxes']:>5} caixas | "
            f"{data['group_count']} grupos"
        )
    print(f"  vazamento: {'nenhum' if not problems else problems}")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
