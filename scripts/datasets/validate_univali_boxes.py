"""Validação automática da derivada do UNIVALI: geometria, pares, duplicatas.

Um script que termina sem exception não provou nada. Este confere, item a item:

- invariantes geométricas de cada caixa (x1<x2, y1<y2 e limites da imagem);
- rótulos: nenhum rótulo fora do vocabulário declarado da derivada;
- par imagem/anotação: a imagem citada existe e decodifica;
- máscara citada existe;
- anotações vazias;
- duplicatas exatas (SHA-256) e quase-duplicatas (dHash-128, o mesmo algoritmo
  e o mesmo limiar que o projeto já usa no RDD2022);
- contaminação contra o RDD2022, que é a fonte dos splits de treino.

Escreve `datasets/reports/univali_box_validation.json` e sai com código 1 se
alguma verificação obrigatória falhar.

    python -B scripts/datasets/validate_univali_boxes.py
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from _core import (
    DATASETS_DIR,
    PROJECT_ROOT,
    configure_stdout,
    file_sha256,
    provenance,
    require_local,
    write_json_report,
)

BOXES_MANIFEST = DATASETS_DIR / "manifests" / "univali_br_boxes.jsonl"
SCAN_MANIFEST = DATASETS_DIR / "manifests" / "univali_br_mask_scan.jsonl"
RDD_INVENTORY = DATASETS_DIR / "manifests" / "rdd2022_inventory.jsonl"
REPORT_NAME = "univali_box_validation.json"

CONVERSION_REPORT = DATASETS_DIR / "reports" / "univali_conversion.json"
EMPTY_MASK_STATUS = "EMPTY_MASK_SEMANTICS_UNRESOLVED"
ALLOWED_LABELS = {"UNIVALI_POTHOLE"}
SOURCE_DATASET = "univali_br"

# Mesmo limiar do `find_duplicates.py` do projeto, para que "quase-duplicata"
# signifique a mesma coisa nas duas fontes.
DHASH_THRESHOLD = 4


def taxonomy_problems(rows: list[dict]) -> tuple[list[str], dict]:
    """Confere evidência ↔ declaração ↔ caixas produzidas.

    Conferir só `derived_label` deixava passar a promoção inválida: o rótulo
    nativo continuava certo enquanto `urmind_class` levava uma classe canônica
    porém semanticamente errada para todas as caixas. As três pontas precisam
    concordar, e a classe permitida vem de `class_mapping.yaml` — não de uma
    lista redigitada aqui.
    """
    from _taxonomy import load_class_mapping

    problemas: list[str] = []
    aceitos = load_class_mapping(SOURCE_DATASET)["accepted"]

    classes_nas_caixas = {
        (b.get("source_label"), b.get("urmind_class")) for r in rows for b in r["boxes"]
    }
    for rotulo, classe in sorted(classes_nas_caixas, key=lambda t: (str(t[0]), str(t[1]))):
        if classe is None:
            continue  # não promovido: é o estado conservador esperado
        permitida = aceitos.get(str(rotulo).upper())
        if permitida is None:
            problemas.append(
                f"caixa de '{rotulo}' promovida a '{classe}', mas class_mapping.yaml "
                f"não declara classe da V1 para esse rótulo"
            )
        elif classe != permitida:
            problemas.append(
                f"caixa de '{rotulo}' promovida a '{classe}', mas a declaração "
                f"permite apenas '{permitida}'"
            )

    conversao: dict = {}
    if CONVERSION_REPORT.is_file():
        try:
            conversao = json.loads(
                require_local(CONVERSION_REPORT).read_text(encoding="utf-8")
            ).get("taxonomy") or {}
        except (OSError, json.JSONDecodeError) as exc:
            problemas.append(f"relatório de conversão ilegível: {exc}")
    else:
        problemas.append("relatório de conversão ausente; origem das caixas não conferível")

    declarada = conversao.get("urmind_class")
    nas_caixas = {c for _, c in classes_nas_caixas}
    if declarada is not None and nas_caixas != {declarada}:
        problemas.append(
            f"conversão declara urmind_class '{declarada}' e as caixas trazem "
            f"{sorted(str(c) for c in nas_caixas)}"
        )
    if declarada is None and nas_caixas - {None}:
        problemas.append(
            "conversão declara mapeamento não verificado, mas há caixa com classe da V1"
        )
    evidencia = conversao.get("evidence")
    if declarada is not None and not evidencia:
        problemas.append("classe da V1 aplicada sem bloco de evidência no relatório")

    return problemas, {
        "classes_in_boxes": sorted(str(c) for c in nas_caixas),
        "declared_by_conversion": declarada,
        "allowed_by_declaration": {k: v for k, v in aceitos.items() if k.isupper()},
        "evidence": evidencia,
        "mapping_status": conversao.get("mapping_status"),
    }


def dhash128(path: Path) -> tuple[int, float]:
    """dHash de 128 bits idêntico ao de `audit_rdd2022.py`.

    Reimplementar com outro redimensionamento produziria números incomparáveis
    entre as duas fontes, que é exatamente o que a verificação cruzada precisa
    comparar.
    """
    from PIL import Image, ImageStat

    with Image.open(path) as image:
        image.load()
        gray = image.convert("L")
        stddev = round(ImageStat.Stat(gray).stddev[0], 4)
        horizontal = list(gray.resize((9, 8), Image.Resampling.BILINEAR).getdata())
        vertical = list(gray.resize((8, 9), Image.Resampling.BILINEAR).getdata())
        bits = 0
        for y in range(8):
            for x in range(8):
                bits = (bits << 1) | int(horizontal[y * 9 + x] > horizontal[y * 9 + x + 1])
        for y in range(8):
            for x in range(8):
                bits = (bits << 1) | int(vertical[y * 8 + x] > vertical[(y + 1) * 8 + x])
    return bits, stddev


def cross_source_check(
    sha_index: dict, hashes: dict, threshold: int, *, skip: bool
) -> dict:
    """Compara UNIVALI × RDD2022 e diz se a comparação vale.

    `valid` é a resposta que importa: ela é falsa quando a verificação foi
    pulada, quando o inventário não existe, não abre, está vazio ou não traz os
    campos de comparação — e também quando a contaminação foi de fato
    encontrada. Um artefato candidato a avaliação externa não pode ser aprovado
    sem essa comparação ter acontecido de verdade.

    O inventário usado é identificado pelo próprio SHA-256, para que o relatório
    diga contra QUAL versão da fonte de treino a comparação foi feita.
    """
    base = {
        "skipped": True,
        "valid": False,
        "compared_against": RDD_INVENTORY.relative_to(PROJECT_ROOT).as_posix(),
        "inventory_sha256": None,
        "inventory_records": 0,
        "threshold": threshold,
    }
    if skip:
        return {**base, "reason": "--skip-cross-source foi usado"}
    if not RDD_INVENTORY.is_file():
        return {**base, "reason": "inventário do RDD2022 não existe"}

    try:
        texto = require_local(RDD_INVENTORY).read_text(encoding="utf-8")
    except OSError as exc:
        return {**base, "reason": f"inventário ilegível: {exc}"}

    rdd_sha: set[str] = set()
    rdd_hashes: list[int] = []
    linhas = 0
    for line in texto.splitlines():
        if not line.strip():
            continue
        linhas += 1
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            return {**base, "reason": f"inventário com linha inválida: {exc}"}
        if record.get("sha256"):
            rdd_sha.add(record["sha256"])
        if record.get("dhash128"):
            rdd_hashes.append(int(record["dhash128"], 16))

    if not linhas:
        return {**base, "reason": "inventário vazio"}
    if not rdd_sha or not rdd_hashes:
        return {
            **base,
            "inventory_records": linhas,
            "reason": "inventário sem sha256/dhash128; comparação impossível",
        }

    exact_cross = sorted(set(sha_index) & rdd_sha)
    minimum = 128
    below = 0
    for value in hashes.values():
        best = min((value ^ other).bit_count() for other in rdd_hashes)
        minimum = min(minimum, best)
        if best <= threshold:
            below += 1

    contaminado = bool(exact_cross or below)
    return {
        "skipped": False,
        "valid": not contaminado,
        "compared_against": RDD_INVENTORY.relative_to(PROJECT_ROOT).as_posix(),
        "inventory_sha256": file_sha256(RDD_INVENTORY),
        "inventory_records": linhas,
        "rdd_images_with_hash": len(rdd_hashes),
        "univali_images_compared": len(hashes),
        "exact_sha256_matches": len(exact_cross),
        "exact_match_examples": exact_cross[:5],
        "nearest_dhash_distance": minimum,
        "images_within_threshold": below,
        "threshold": threshold,
        "reason": None if not contaminado else "contaminação detectada",
        "verdict": (
            "sem contaminação detectável entre UNIVALI e RDD2022"
            if not contaminado
            else "CONTAMINAÇÃO DETECTADA: revisar antes de qualquer uso"
        ),
    }


def geometry_problems(box: dict, width: int, height: int) -> list[str]:
    problems = []
    if not box["xmin"] < box["xmax"]:
        problems.append("x1 < x2 violado")
    if not box["ymin"] < box["ymax"]:
        problems.append("y1 < y2 violado")
    if not 0 <= box["xmin"] < width:
        problems.append("0 <= x1 < largura violado")
    if not 0 < box["xmax"] <= width:
        problems.append("0 < x2 <= largura violado")
    if not 0 <= box["ymin"] < height:
        problems.append("0 <= y1 < altura violado")
    if not 0 < box["ymax"] <= height:
        problems.append("0 < y2 <= altura violado")
    return problems


def overlap_pairs(boxes: list[dict]) -> int:
    """Pares de caixas que se sobrepõem na mesma imagem.

    Não é erro: duas regiões conexas distintas podem ter caixas envolventes que
    se cruzam. É medido para ficar registrado, não para descartar.
    """
    count = 0
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            if (
                a["xmin"] < b["xmax"]
                and b["xmin"] < a["xmax"]
                and a["ymin"] < b["ymax"]
                and b["ymin"] < a["ymax"]
            ):
                count += 1
    return count


def hamming_pairs(hashes: dict[str, int], threshold: int) -> list[dict]:
    """Pares abaixo do limiar, por bandas disjuntas (mesma técnica do projeto)."""
    bands = threshold + 1
    widths = [128 // bands + int(i < 128 % bands) for i in range(bands)]
    buckets: dict = defaultdict(list)
    pairs = []
    seen = set()
    for name, value in hashes.items():
        offset = 0
        candidates = set()
        keys = []
        for index, width in enumerate(widths):
            key = (index, (value >> offset) & ((1 << width) - 1))
            keys.append(key)
            candidates.update(buckets[key])
            offset += width
        for other in candidates:
            distance = (value ^ hashes[other]).bit_count()
            if distance <= threshold:
                pair = tuple(sorted((name, other)))
                if pair not in seen:
                    seen.add(pair)
                    pairs.append({"a": pair[0], "b": pair[1], "distance": distance})
        for key in keys:
            buckets[key].append(name)
    return pairs


def main() -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dhash-threshold", type=int, default=DHASH_THRESHOLD)
    parser.add_argument("--skip-cross-source", action="store_true")
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()

    rows = [
        json.loads(line)
        for line in require_local(BOXES_MANIFEST).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    failures: list[str] = []
    geometry_errors: list[dict] = []
    label_errors: list[dict] = []
    missing_images: list[str] = []
    unreadable_images: list[dict] = []
    missing_masks: list[str] = []
    empty_annotations: list[str] = []
    empty_mask_samples: list[str] = []
    overlaps = 0
    boxes_total = 0
    labels: Counter = Counter()
    hashes: dict[str, int] = {}
    sha_index: dict[str, list[str]] = defaultdict(list)
    stddevs: dict[str, float] = {}

    print(f"Validando {len(rows)} amostras derivadas")
    for index, row in enumerate(rows, start=1):
        directory = row["directory"]
        width, height = row["image_width"], row["image_height"]

        if not row["boxes"] and row.get("mask_status") != EMPTY_MASK_STATUS:
            # Amostra sem caixa só é legítima quando o manifesto declara que a
            # máscara está vazia e a semântica disso continua pendente. Sem essa
            # declaração, é anotação sumida.
            empty_annotations.append(directory)
        if row.get("mask_status") == EMPTY_MASK_STATUS:
            empty_mask_samples.append(directory)

        for box in row["boxes"]:
            boxes_total += 1
            labels[box["derived_label"]] += 1
            if box["derived_label"] not in ALLOWED_LABELS:
                label_errors.append({"directory": directory, "label": box["derived_label"]})
            problems = geometry_problems(box, width, height)
            if problems:
                geometry_errors.append({"directory": directory, "problems": problems, "box": box})
        overlaps += overlap_pairs(row["boxes"])

        image_path = PROJECT_ROOT / row["image_relpath"]
        mask_path = PROJECT_ROOT / row["mask_relpath"]
        if not image_path.is_file():
            missing_images.append(directory)
            continue
        if not mask_path.is_file():
            missing_masks.append(directory)
        try:
            value, stddev = dhash128(image_path)
            hashes[directory] = value
            stddevs[directory] = stddev
            sha_index[file_sha256(image_path)].append(directory)
        except Exception as exc:  # noqa: BLE001
            unreadable_images.append({"directory": directory, "error": f"{type(exc).__name__}: {exc}"})
        if index % 100 == 0 or index == len(rows):
            print(f"  {index}/{len(rows)}")

    exact_duplicates = [
        {"sha256": sha, "directories": sorted(names)}
        for sha, names in sorted(sha_index.items())
        if len(names) > 1
    ]
    # Imagem de baixo contraste faz o dHash colidir sem que as cenas sejam
    # parecidas; o projeto já descarta stddev < 10 no RDD2022.
    near_input = {k: v for k, v in hashes.items() if stddevs.get(k, 0) >= 10}
    near_duplicates = hamming_pairs(near_input, args.dhash_threshold)

    # Fail-CLOSED. Antes, `--skip-cross-source` ou um inventário ausente deixavam
    # `skipped: true` e o relatório ainda saía `passed: true` — ausência de
    # comparação passava por ausência de contaminação, e o split publicava-se
    # como livre de vazamento sem nunca ter olhado para a fonte de treino.
    cross_source = cross_source_check(
        sha_index, hashes, args.dhash_threshold, skip=args.skip_cross_source
    )
    if not cross_source["valid"]:
        failures.append(
            f"verificação cruzada contra o RDD2022 inválida: {cross_source['reason']}"
        )

    taxonomia_problemas, taxonomia = taxonomy_problems(rows)
    if taxonomia_problemas:
        failures.extend(taxonomia_problemas)

    if geometry_errors:
        failures.append(f"{len(geometry_errors)} caixas violam invariantes geométricas")
    if label_errors:
        failures.append(f"{len(label_errors)} caixas com rótulo fora do vocabulário declarado")
    if missing_images:
        failures.append(f"{len(missing_images)} imagens citadas não existem")
    if unreadable_images:
        failures.append(f"{len(unreadable_images)} imagens não decodificam")
    if missing_masks:
        failures.append(f"{len(missing_masks)} máscaras citadas não existem")
    if empty_annotations:
        failures.append(
            f"{len(empty_annotations)} amostras sem caixa e sem declarar {EMPTY_MASK_STATUS}"
        )

    report = {
        "version": 1,
        "scope": "validação automática da derivada univali_br_boxes.jsonl",
        **provenance(
            __file__,
            source_dataset="univali_br",
            source_version="mendeley-v4",
            transform="validação de geometria, pares, rótulos e duplicatas da derivada",
            params={
                "dhash_threshold": args.dhash_threshold,
                "allowed_labels": sorted(ALLOWED_LABELS),
                "low_contrast_stddev_floor": 10,
            },
        ),
        "inputs": len(rows),
        "outputs": len(rows) - len(missing_images) - len(unreadable_images),
        "dropped": 0,
        "drop_reasons": {},
        # Cadeia conferível de ponta a ponta: varredura → caixas → inventário
        # externo → resultado da deduplicação. Sem o hash do inventário, dizer
        # "sem contaminação" não identifica contra o que a comparação foi feita.
        "integrity": {
            "boxes_manifest_sha256": file_sha256(BOXES_MANIFEST),
            "scan_manifest_sha256": file_sha256(SCAN_MANIFEST) if SCAN_MANIFEST.is_file() else None,
            "rdd_inventory_sha256": cross_source.get("inventory_sha256"),
            "cross_source_valid": cross_source.get("valid", False),
        },
        "raw_modified": False,
        "passed": not failures,
        "failures": failures,
        "geometry": {
            "boxes_checked": boxes_total,
            "invariants": [
                "x1 < x2",
                "y1 < y2",
                "0 <= x1 < largura",
                "0 < x2 <= largura",
                "0 <= y1 < altura",
                "0 < y2 <= altura",
            ],
            "violations": geometry_errors[:20],
            "violation_count": len(geometry_errors),
            "overlapping_box_pairs": overlaps,
            "overlap_note": (
                "Sobreposição entre caixas de regiões conexas distintas é esperada "
                "e registrada; não invalida a anotação."
            ),
        },
        "labels": {
            "allowed": sorted(ALLOWED_LABELS),
            "counts": dict(labels),
            "violations": label_errors[:20],
        },
        "taxonomy_consistency": {
            **taxonomia,
            "problems": taxonomia_problemas,
            "passed": not taxonomia_problemas,
            "checks": [
                "urmind_class de cada caixa é nula ou a classe declarada para o rótulo",
                "classe nas caixas concorda com a declarada no relatório de conversão",
                "classe da V1 só aparece acompanhada de bloco de evidência",
            ],
        },
        "pairing": {
            "missing_images": missing_images[:20],
            "missing_images_count": len(missing_images),
            "unreadable_images": unreadable_images[:20],
            "missing_masks": missing_masks[:20],
            "missing_masks_count": len(missing_masks),
            "empty_annotations": empty_annotations[:20],
            "empty_annotations_count": len(empty_annotations),
            "empty_mask_samples_count": len(empty_mask_samples),
            "empty_mask_status": EMPTY_MASK_STATUS,
        },
        "duplicates": {
            "exact_sha256_groups": exact_duplicates,
            "exact_group_count": len(exact_duplicates),
            "near_duplicate_pairs": near_duplicates,
            "near_duplicate_count": len(near_duplicates),
            "low_contrast_excluded": len(hashes) - len(near_input),
            "note": (
                "Quase-duplicata por dHash é candidato para revisão humana, não "
                "prova de duplicação. No UNIVALI a relação forte entre imagens é o "
                "trecho de rodovia, que o dHash NÃO detecta — por isso o split "
                "agrupa por identificador, não por similaridade de pixel."
            ),
        },
        "cross_source_contamination": cross_source,
    }

    if args.no_report:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        destination = write_json_report(REPORT_NAME, report)
        print(f"\nRelatório: {destination.relative_to(PROJECT_ROOT)}")

    print(f"  caixas verificadas : {boxes_total}")
    print(f"  duplicatas exatas  : {len(exact_duplicates)}")
    print(f"  quase-duplicatas   : {len(near_duplicates)}")
    if not cross_source.get("skipped"):
        print(f"  contra RDD2022     : {cross_source['verdict']}")
    print(f"  resultado          : {'APROVADO' if not failures else 'FALHOU'}")
    for failure in failures:
        print(f"    - {failure}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
