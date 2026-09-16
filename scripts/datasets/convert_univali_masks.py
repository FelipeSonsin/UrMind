"""Versão DERIVADA do UNIVALI/DNIT: máscara de segmentação → caixas envolventes.

    máscara original → regiões conexas válidas → bounding boxes → anotação

Nada é sobrescrito: `datasets/raw/univali_br/` é lido e a derivada é um arquivo
novo em `datasets/manifests/`. A derivada guarda **caminho relativo**, nunca
cópia de imagem.

Três decisões que este script NÃO toma sozinho, por escrito:

1. **Classe.** A caixa sai com o rótulo nativo `UNIVALI_POTHOLE`, não com
   `URMIND_ROAD_D40`. A correspondência é declarada pela fonte e ainda não foi
   verificada de forma independente; promover a caixa a D40 aqui seria mapear
   por nome. `--assert-v1-mapping` existe para quando a verificação humana
   estiver registrada, e recusa rodar sem o arquivo de evidência.
2. **Trinca.** `CRACK` não é convertido para nada. A fonte publica uma única
   categoria de trinca e não declara subtipo: escolher entre D00/D10/D20 seria
   inventar rótulo (§8.2).
3. **Limiar de área.** O padrão é `--min-area 0`: nenhuma região é descartada
   por tamanho. Descartar é irreversível e o número teria de vir de algum lugar;
   em vez disso cada caixa carrega sua área e um `size_tier` calculado dos
   quantis do próprio dataset, e o relatório mede quanto cada limiar candidato
   custaria. A escolha de um limiar >0 é decisão humana registrada.

    python -B scripts/datasets/convert_univali_masks.py
    python -B scripts/datasets/convert_univali_masks.py --min-area 12
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

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

SCAN_MANIFEST = DATASETS_DIR / "manifests" / "univali_br_mask_scan.jsonl"
BOXES_MANIFEST = DATASETS_DIR / "manifests" / "univali_br_boxes.jsonl"
REPORT_NAME = "univali_conversion.json"

# Rótulo nativo da fonte. NÃO é classe da taxonomia V1 e não deve ser tratado
# como tal por nenhum consumidor até a verificação semântica existir.
NATIVE_LABEL = "UNIVALI_POTHOLE"
SOURCE_MASK = "POTHOLE"

# Categorias que a fonte publica e que esta conversão recusa, com o motivo.
NOT_CONVERTED = {
    "CRACK": (
        "categoria única de trinca; a fonte não declara subtipo e o §8.2 proíbe "
        "escolher entre D00/D10/D20 por geometria ou aparência"
    ),
    "LANE": "superfície da via; delimitação, não dano",
}

# Limiares candidatos apenas para MEDIR o custo de cada um. Nenhum é aplicado
# por padrão. Cobrem de um pixel isolado até o limiar de "objeto pequeno" da
# convenção COCO (32x32 = 1024 px), para que a decisão humana veja a escala.
CANDIDATE_THRESHOLDS = (1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024)

# Estado da derivada. A transformação componente conexo → objeto é uma
# HIPÓTESE de derivação, não verdade verificada: a fonte publica máscara
# SEMÂNTICA, sem id de instância. Dois fragmentos podem ser um buraco ocluído
# por um risco de tinta, um buraco só, ou ruído de anotação — e o pixel não
# distingue. Enquanto ninguém olhar, a caixa é candidata.
DERIVATION_STATUS = "CANDIDATE_DERIVED_BOXES"
INSTANCE_STATUS = "UNVALIDATED_COMPONENT"

# Máscara sem foreground. A fonte não declara se a imagem foi examinada e não
# tinha buraco, ou se simplesmente não foi anotada. As duas leituras levam a
# métricas opostas, então o status fica pendente até haver documentação.
EMPTY_MASK_STATUS = "EMPTY_MASK_SEMANTICS_UNRESOLVED"


def _percentile(values: list[int], q: float) -> float:
    import numpy as np

    return float(np.percentile(np.array(values), q)) if values else 0.0


def _overlapping_pairs(boxes: list[dict]) -> int:
    """Pares de caixas que se cruzam na mesma imagem.

    Registrado porque é justamente o sintoma de que dois componentes podem ser
    o mesmo objeto partido. Não filtra nada.
    """
    total = 0
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            if (
                a["xmin"] < b["xmax"]
                and b["xmin"] < a["xmax"]
                and a["ymin"] < b["ymax"]
                and b["ymin"] < a["ymax"]
            ):
                total += 1
    return total


def load_scan(path: Path) -> list[dict]:
    return [json.loads(line) for line in require_local(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def size_tiers(areas: list[int]) -> dict:
    """Faixas de tamanho a partir dos quantis do próprio dataset.

    Não é filtro: é vocabulário para descrever a distribuição real, de modo que
    uma auditoria consiga pedir "as regiões pequenas" sem inventar um número.
    """
    import numpy as np

    if not areas:
        return {}
    array = np.array(sorted(areas))
    return {
        "q25": int(np.percentile(array, 25)),
        "q50": int(np.percentile(array, 50)),
        "q75": int(np.percentile(array, 75)),
    }


def tier_of(area: int, tiers: dict) -> str:
    if not tiers:
        return "unknown"
    if area <= tiers["q25"]:
        return "small"
    if area <= tiers["q50"]:
        return "medium_small"
    if area <= tiers["q75"]:
        return "medium_large"
    return "large"


def validate_box(box: dict, width: int, height: int) -> list[str]:
    """As invariantes geométricas exigidas. Falha é motivo de descarte, não crash."""
    problems = []
    if not box["xmin"] < box["xmax"]:
        problems.append("x1 >= x2")
    if not box["ymin"] < box["ymax"]:
        problems.append("y1 >= y2")
    if not 0 <= box["xmin"] < width:
        problems.append("x1 fora de [0, largura)")
    if not 0 < box["xmax"] <= width:
        problems.append("x2 fora de (0, largura]")
    if not 0 <= box["ymin"] < height:
        problems.append("y1 fora de [0, altura)")
    if not 0 < box["ymax"] <= height:
        problems.append("y2 fora de (0, altura]")
    return problems


def convert(rows: list[dict], min_area: int, mapping_evidence: dict | None) -> tuple[list[dict], dict]:
    areas = [
        component["area_px"]
        for row in rows
        for component in row["masks"].get(SOURCE_MASK, {}).get("components", [])
    ]
    tiers = size_tiers(areas)

    manifest: list[dict] = []
    drops: Counter = Counter()
    dropped_examples: dict = {}
    class_counts: Counter = Counter()
    per_image_boxes: Counter = Counter()
    images_with_boxes = 0
    empty_mask_samples = 0
    images_foreground_without_box = 0
    border_boxes = 0
    overlapping_pairs = 0
    noise_candidates = 0

    # Percentil 1 da área, medido neste dataset. NÃO é filtro e não descarta
    # nada: é o corte que a própria distribuição sugere para "olhar com atenção",
    # e existe para que uma auditoria consiga pedir "as menores" sem alguém
    # inventar um número redondo.
    noise_floor = int(_percentile(areas, 1)) if areas else 0

    for row in rows:
        directory = row["directory"]
        mask = row["masks"].get(SOURCE_MASK, {})
        width, height = row.get("image_width"), row.get("image_height")

        if row.get("image_error"):
            drops["imagem ilegível ou ausente"] += 1
            dropped_examples.setdefault("imagem ilegível ou ausente", []).append(directory)
            continue
        if not mask.get("present"):
            drops[f"máscara {SOURCE_MASK} ausente"] += 1
            dropped_examples.setdefault(f"máscara {SOURCE_MASK} ausente", []).append(directory)
            continue
        if mask.get("error"):
            drops[f"máscara {SOURCE_MASK} ilegível"] += 1
            dropped_examples.setdefault(f"máscara {SOURCE_MASK} ilegível", []).append(directory)
            continue
        if width is None or height is None:
            drops["dimensão da imagem desconhecida"] += 1
            continue
        observed = mask.get("observed") or {}
        if observed.get("width") != width or observed.get("height") != height:
            drops["máscara desalinhada da imagem"] += 1
            dropped_examples.setdefault("máscara desalinhada da imagem", []).append(directory)
            continue
        # Máscara vazia NÃO é descarte e NÃO é negativo: é semântica pendente.
        # Antes ela sumia do manifesto, e 1.671 das 2.235 imagens desapareciam de
        # um conjunto descrito como domínio brasileiro. Agora a amostra fica
        # registrada com o status da pendência, sem caixa e sem virar negativo.
        vazia = not mask.get("foreground_px")

        boxes = []
        for component in mask.get("components", []):
            if component["area_px"] < min_area:
                drops[f"região com área < {min_area} px (limiar declarado)"] += 1
                continue
            problems = validate_box(component, width, height)
            if problems:
                drops[f"caixa inválida: {'; '.join(problems)}"] += 1
                dropped_examples.setdefault("caixa inválida", []).append(directory)
                continue
            if component["touches_border"]:
                border_boxes += 1
            e_ruido = component["area_px"] <= noise_floor
            noise_candidates += int(e_ruido)
            boxes.append(
                {
                    "source_label": SOURCE_MASK,
                    "derived_label": NATIVE_LABEL,
                    "urmind_class": (mapping_evidence or {}).get("urmind_class"),
                    # A caixa envolve UM COMPONENTE CONEXO. Que ele seja um
                    # objeto independente é o que falta verificar.
                    "instance_status": INSTANCE_STATUS,
                    "component_index": len(boxes),
                    "noise_candidate": e_ruido,
                    "single_pixel": component["area_px"] == 1,
                    "xmin": component["xmin"],
                    "ymin": component["ymin"],
                    "xmax": component["xmax"],
                    "ymax": component["ymax"],
                    "width": component["width"],
                    "height": component["height"],
                    "mask_area_px": component["area_px"],
                    "box_area_px": component["box_area_px"],
                    "fill_ratio": component["fill_ratio"],
                    "size_tier": tier_of(component["area_px"], tiers),
                    "touches_border": component["touches_border"],
                }
            )

        overlapping_pairs += _overlapping_pairs(boxes)

        if vazia:
            empty_mask_samples += 1
        elif boxes:
            images_with_boxes += 1
            per_image_boxes[len(boxes)] += 1
            class_counts[NATIVE_LABEL] += len(boxes)
        else:
            # Tinha foreground e nenhuma caixa sobreviveu: é perda real, não
            # ausência de anotação. Fica no manifesto com o motivo já contado.
            images_foreground_without_box += 1

        manifest.append(
            {
                "dataset_id": "univali_br",
                "derived": True,
                "derivation_status": DERIVATION_STATUS,
                "instance_semantics_validated": False,
                "mask_status": EMPTY_MASK_STATUS if vazia else "POSITIVE_MASK",
                "derived_from": SCAN_MANIFEST.relative_to(PROJECT_ROOT).as_posix(),
                "directory": directory,
                "image_relpath": row["image_relpath"],
                "mask_relpath": (
                    f"datasets/raw/univali_br/v1/{directory}/{directory}_{SOURCE_MASK}.png"
                ),
                "image_width": width,
                "image_height": height,
                "group_segment": row["group_segment"],
                "group_road": row["group_road"],
                "group_uf": row["group_uf"],
                "uf": row["uf"],
                "road": row["road"],
                "segment": row["segment"],
                "position": row["position"],
                "name_parsed": row["name_parsed"],
                "boxes": boxes,
                "rejected_labels": [
                    {"label": label, "reason": reason} for label, reason in NOT_CONVERTED.items()
                ],
            }
        )

    summary = {
        "tiers": tiers,
        "drops": dict(drops),
        "dropped_examples": {k: v[:5] for k, v in dropped_examples.items()},
        "class_counts": dict(class_counts),
        "images_with_boxes": images_with_boxes,
        "empty_mask_samples": empty_mask_samples,
        "images_foreground_without_box": images_foreground_without_box,
        "boxes_per_image": dict(sorted(per_image_boxes.items())),
        "max_boxes_in_one_image": max(per_image_boxes) if per_image_boxes else 0,
        "overlapping_box_pairs": overlapping_pairs,
        "noise_candidates": noise_candidates,
        "noise_floor_px": noise_floor,
        "single_pixel_components": sum(1 for a in areas if a == 1),
        "border_boxes": border_boxes,
        "threshold_cost": {
            str(threshold): sum(1 for a in areas if a < threshold)
            for threshold in CANDIDATE_THRESHOLDS
        },
        "components_total": len(areas),
    }
    return manifest, summary


SOURCE_DATASET = "univali_br"
SOURCE_VERSION = "mendeley-v4"


def _allowed_class_for(source_label: str) -> str:
    """A única classe da V1 que a declaração do projeto permite para este rótulo.

    Vem de `datasets/metadata/class_mapping.yaml`, lido por `_taxonomy`, que já
    valida a classe contra `taxonomy.yaml`. Nenhuma regra semântica é inventada
    aqui e nenhuma lista de classes é redigitada: se a declaração mudar, isto
    muda junto. Reescrever D00/D10/D20/D40 neste script criaria uma segunda
    verdade que divergiria da primeira em silêncio.
    """
    from _taxonomy import load_class_mapping

    aceitos = load_class_mapping(SOURCE_DATASET)["accepted"]
    permitida = aceitos.get(source_label.upper())
    if permitida is None:
        raise SystemExit(
            f"class_mapping.yaml não declara nenhuma classe da V1 para "
            f"'{SOURCE_DATASET}.{source_label}'; não há o que promover."
        )
    return permitida


def _declared_class_or_none() -> str | None:
    """A classe que a declaração permite para POTHOLE, só para registro.

    Aparece no relatório mesmo quando nenhuma evidência foi apresentada, para
    que fique explícito qual promoção estaria disponível — e que ela não foi
    aplicada. Falha de leitura do metadado não derruba a conversão, que não
    depende dele quando não há evidência.
    """
    try:
        return _allowed_class_for(SOURCE_MASK)
    except SystemExit:
        return None


def load_mapping_evidence(path: Path | None) -> dict | None:
    """Só aceita promover a caixa a classe da V1 com evidência específica em arquivo.

    Antes bastava uma string não vazia em `urmind_class`, e qualquer valor era
    copiado para as 1.490 caixas — inclusive um typo ou `URMIND_ROAD_D00`, que é
    classe canônica e semanticamente errada para buraco. Uma evidência produzida
    para "POTHOLE é buraco" não autoriza promover o mesmo dado a trinca.

    A classe permitida não é decidida aqui: é lida da declaração do projeto para
    o par (dataset, rótulo de origem).
    """
    if path is None:
        return None
    try:
        data = json.loads(require_local(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"evidência de mapeamento ilegível: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("evidência de mapeamento deve ser um objeto JSON")

    # `approved` fica fora da checagem por veracidade: `false` é um valor
    # legítimo e informativo, e tratá-lo como "campo faltando" esconderia a
    # recusa real atrás de uma mensagem de formulário incompleto.
    required = (
        "source_dataset",
        "source_version",
        "source_label",
        "urmind_class",
        "evidence_id",
        "verified_by",
        "verified_at",
        "method",
        "sample_size",
    )
    missing = [campo for campo in required if not data.get(campo)]
    if "approved" not in data:
        missing.append("approved")
    if missing:
        raise SystemExit(
            f"evidência de mapeamento incompleta; faltam {missing}. "
            f"Sem verificação registrada a caixa continua {NATIVE_LABEL}."
        )

    if data["source_dataset"] != SOURCE_DATASET:
        raise SystemExit(
            f"evidência é do dataset '{data['source_dataset']}', não de "
            f"'{SOURCE_DATASET}'. Evidência de outra fonte não promove esta."
        )
    if data["source_version"] != SOURCE_VERSION:
        raise SystemExit(
            f"evidência é da versão '{data['source_version']}', não de "
            f"'{SOURCE_VERSION}'. Revalide contra a versão em uso."
        )
    if str(data["source_label"]).upper() != SOURCE_MASK:
        raise SystemExit(
            f"evidência valida o rótulo '{data['source_label']}', e esta conversão "
            f"produz caixas de '{SOURCE_MASK}'."
        )
    if data["approved"] is not True:
        raise SystemExit(
            "evidência não está aprovada (`approved` precisa ser exatamente true)."
        )

    permitida = _allowed_class_for(SOURCE_MASK)
    if data["urmind_class"] != permitida:
        raise SystemExit(
            f"evidência pede promover para '{data['urmind_class']}', mas a única "
            f"classe declarada para {SOURCE_DATASET}.{SOURCE_MASK} é '{permitida}'. "
            "Uma evidência de buraco não autoriza rotular trinca (§8.2)."
        )
    return data


def main() -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--min-area",
        type=int,
        default=0,
        help="área mínima da região, em pixels. 0 = não descartar por tamanho (padrão).",
    )
    parser.add_argument(
        "--assert-v1-mapping",
        type=Path,
        default=None,
        help="JSON com a verificação humana que autoriza mapear para a taxonomia V1.",
    )
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()

    if args.min_area < 0:
        raise SystemExit("--min-area não pode ser negativo")

    rows = load_scan(SCAN_MANIFEST)
    evidence = load_mapping_evidence(args.assert_v1_mapping)
    manifest, summary = convert(rows, args.min_area, evidence)

    report = {
        "version": 1,
        "artifact_kind": "derived_dataset_version",
        "scope": (
            "conversão de máscara de segmentação em caixa envolvente. Não treina, "
            "não altera raw e não promove o rótulo à taxonomia V1."
        ),
        **provenance(
            __file__,
            source_dataset="univali_br",
            source_version="mendeley-v4",
            transform="máscara POTHOLE → componentes conexos → bounding boxes",
            params={
                "min_area_px": args.min_area,
                "connectivity": 8,
                "coordinate_convention": "meio-aberto: [xmin, xmax), [ymin, ymax)",
                "source_mask": SOURCE_MASK,
                "derived_label": NATIVE_LABEL,
                "v1_mapping_asserted": evidence is not None,
                "scan_manifest_sha256": file_sha256(SCAN_MANIFEST),
            },
        ),
        "inputs": len(rows),
        "outputs": len(manifest),
        "dropped": sum(summary["drops"].values()),
        "drop_reasons": summary["drops"],
        "dropped_examples": summary["dropped_examples"],
        "integrity": {
            "scan_manifest_sha256": file_sha256(SCAN_MANIFEST),
            "boxes_manifest": BOXES_MANIFEST.relative_to(PROJECT_ROOT).as_posix(),
        },
        "raw_modified": False,
        "counts": {
            "source_images": len(rows),
            "manifest_samples": len(manifest),
            "positive_mask_samples": summary["images_with_boxes"],
            "empty_mask_samples": summary["empty_mask_samples"],
            "images_foreground_without_box": summary["images_foreground_without_box"],
            "boxes": sum(summary["class_counts"].values()),
            "components_available": summary["components_total"],
            "boxes_per_image": summary["boxes_per_image"],
            "boxes_touching_border": summary["border_boxes"],
        },
        # Três perguntas diferentes que um relatório anterior respondia como se
        # fossem uma. Ter máscara não é ter caixa; ter caixa não é ter avaliação.
        "capability": {
            "has_semantic_mask": True,
            "has_candidate_detection_boxes": bool(sum(summary["class_counts"].values())),
            "is_detection_evaluation_ready": False,
            "blockers": [
                (
                    "instance_semantics_validated=false: componente conexo ainda não foi "
                    "verificado como objeto independente por revisão humana"
                ),
                "canonical_mapping_validated=false: urmind_class continua null",
                (
                    "empty_mask_semantics_validated=false: "
                    f"{summary['empty_mask_samples']} máscaras vazias sem significado declarado"
                ),
            ],
        },
        "instance_derivation": {
            "status": DERIVATION_STATUS,
            "hypothesis": (
                "cada componente conexo da máscara é um objeto independente"
            ),
            "evidence": "NENHUMA. A fonte publica máscara semântica, sem id de instância.",
            "connectivity": 8,
            "components_total": summary["components_total"],
            "max_boxes_in_one_image": summary["max_boxes_in_one_image"],
            "overlapping_box_pairs": summary["overlapping_box_pairs"],
            "single_pixel_components": summary["single_pixel_components"],
            "noise_candidates": summary["noise_candidates"],
            "noise_floor_px": summary["noise_floor_px"],
            "known_limitations": [
                (
                    "Fragmentos podem ser partes do mesmo buraco separadas por oclusão, "
                    "remendo ou falha de anotação; 8-conectividade une diagonal mas não "
                    "atravessa vão."
                ),
                (
                    "Caixas sobrepostas podem duplicar o mesmo objeto no ground truth e "
                    "inflar contagem, o que distorce AP sem aparecer como erro."
                ),
                (
                    "Componentes de 1 px produzem caixa 1x1 válida e praticamente "
                    "indetectável; deprimem recall sem medir capacidade."
                ),
                (
                    "Nenhum filtro de área foi aplicado: `noise_candidate` é marca para "
                    "revisão, não regra."
                ),
            ],
        },
        "empty_masks": {
            "status": EMPTY_MASK_STATUS,
            "count": summary["empty_mask_samples"],
            "kept_in_manifest": True,
            "meaning_candidates": [
                "negativo verdadeiro: a imagem foi examinada e não havia buraco",
                "imagem sem buraco, mas não necessariamente examinada",
                "anotação ausente para este quadro",
                "quadro não anotado nesta campanha",
            ],
            "why_unresolved": (
                "A fonte não declara qual delas vale. Tratar como negativo mediria "
                "falso positivo em cima de suposição; excluir do conjunto esconde "
                "75% do domínio. As duas escolhas mudam a métrica em direções "
                "opostas, então nenhuma é feita aqui."
            ),
            "usable_as_negative": False,
        },
        "class_distribution": summary["class_counts"],
        "taxonomy": {
            "derived_label": NATIVE_LABEL,
            "source_label": SOURCE_MASK,
            "urmind_class": (evidence or {}).get("urmind_class"),
            "class_allowed_by_declaration": _declared_class_or_none(),
            "mapping_status": (
                "verificado" if evidence else "nao_verificado_independentemente"
            ),
            "evidence": (
                {
                    "evidence_id": evidence["evidence_id"],
                    "verified_by": evidence["verified_by"],
                    "verified_at": evidence["verified_at"],
                    "method": evidence["method"],
                    "sample_size": evidence["sample_size"],
                    "source_dataset": evidence["source_dataset"],
                    "source_version": evidence["source_version"],
                    "source_label": evidence["source_label"],
                }
                if evidence
                else None
            ),
            "note": (
                "A fonte declara POTHOLE = buraco e o projeto já registra a "
                "correspondência candidata com URMIND_ROAD_D40 em "
                "datasets/metadata/class_mapping.yaml. Esta derivada NÃO a aplica: "
                "sem verificação independente registrada, a caixa permanece com o "
                "rótulo nativo da fonte."
            ),
            "not_converted": NOT_CONVERTED,
        },
        "size_policy": {
            "min_area_applied_px": args.min_area,
            "rationale": (
                "Padrão 0: nenhuma região é descartada por tamanho. Descarte é "
                "irreversível e qualquer corte precisa de um número justificado; "
                "em vez disso a área de cada região fica no manifesto e o custo de "
                "cada limiar candidato é medido abaixo."
            ),
            "tier_quantiles_px": summary["tiers"],
            "components_below_threshold": summary["threshold_cost"],
        },
    }

    if args.no_report:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    lines = "\n".join(json.dumps(row, ensure_ascii=False) for row in manifest) + "\n"
    write_text_safe(BOXES_MANIFEST, lines)
    report["integrity"]["boxes_manifest_sha256"] = file_sha256(BOXES_MANIFEST)
    destination = write_json_report(REPORT_NAME, report)

    print(f"Derivada: {BOXES_MANIFEST.relative_to(PROJECT_ROOT)}")
    print(f"Relatório: {destination.relative_to(PROJECT_ROOT)}")
    contagens = report["counts"]
    print(
        f"  {contagens['positive_mask_samples']} imagens com caixa | "
        f"{contagens['boxes']} caixas candidatas ({NATIVE_LABEL})"
    )
    print(
        f"  {contagens['empty_mask_samples']} máscaras vazias registradas como "
        f"{EMPTY_MASK_STATUS}"
    )
    print(f"  derivação: {DERIVATION_STATUS} | evaluation_ready: False")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
