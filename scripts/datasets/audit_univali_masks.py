"""Mede a semântica real das máscaras do UNIVALI/DNIT antes de qualquer conversão.

Este script responde, com pixel medido em vez de nome de arquivo:

- que valores/cores/IDs as máscaras usam;
- quais categorias existem e quantas máscaras de cada uma têm conteúdo;
- quantas regiões desconectadas cada máscara contém;
- como as regiões se distribuem em área, forma e posição;
- se a máscara está alinhada à imagem RAW;
- que metadados a fonte oferece.

Ele **não converte nada** e **não decide limiar**. A distribuição que ele mede é
a entrada de `convert_univali_masks.py`, que calcula o limiar a partir dela.

Escreve dois artefatos derivados:

    datasets/manifests/univali_br_mask_scan.jsonl   uma linha por amostra
    datasets/reports/univali_mask_semantics.json    agregado + evidência

    python -B scripts/datasets/audit_univali_masks.py
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

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
from _univali import MASK_SUFFIXES, scan_sample

RAW_ROOT = DATASETS_DIR / "raw" / "univali_br"
SAMPLES_ROOT = RAW_ROOT / "v1"
SCAN_MANIFEST = DATASETS_DIR / "manifests" / "univali_br_mask_scan.jsonl"
REPORT_NAME = "univali_mask_semantics.json"

# A fonte declara estes três sufixos e nada mais. Ver `raw/univali_br/README.md`
# e a ficha Mendeley V4. Nenhum deles é traduzido aqui para a taxonomia V1.
DECLARED_SEMANTICS = {
    "CRACK": "trinca, sem separação de subtipo declarada pela fonte",
    "LANE": "faixa de rolamento / superfície da via",
    "POTHOLE": "buraco",
}


def percentiles(values: list[int], points=(0, 1, 5, 25, 50, 75, 95, 99, 100)) -> dict:
    import numpy as np

    if not values:
        return {}
    array = np.array(sorted(values))
    return {f"p{p}": int(np.percentile(array, p)) for p in points}


def scan_all(connectivity: int, limit: int | None) -> list:
    directories = sorted(p for p in require_local(SAMPLES_ROOT).iterdir() if p.is_dir())
    if limit is not None:
        directories = directories[:limit]
    total = len(directories)
    scans = []
    for index, directory in enumerate(directories, start=1):
        scans.append(scan_sample(directory, PROJECT_ROOT, connectivity=connectivity))
        if index % 250 == 0 or index == total:
            print(f"  {index}/{total} amostras medidas")
    return scans


# Só a categoria efetivamente convertida carrega a geometria região a região no
# manifesto. CRACK é uma anotação fina e fragmentada — 323 mil regiões nas 2.235
# imagens — e gravar todas somava 55 MB a uma pasta versionada, para descrever a
# única categoria que a conversão recusa por princípio. As contagens agregadas
# de CRACK e LANE continuam no relatório de semântica, que é onde são lidas.
GEOMETRY_IN_MANIFEST = ("POTHOLE",)


def scan_row(scan) -> dict:
    """Linha do manifesto de varredura: tudo que a conversão vai precisar."""
    return {
        "directory": scan.directory,
        "image_relpath": scan.image_relpath,
        "image_width": scan.image_width,
        "image_height": scan.image_height,
        "image_error": scan.image_error,
        "name_parsed": scan.name.parsed,
        "image_id": scan.name.image_id,
        "uf": scan.name.uf,
        "road": scan.name.road,
        "segment": scan.name.segment,
        "position": scan.name.position,
        "group_segment": scan.name.group_segment,
        "group_road": scan.name.group_road,
        "group_uf": scan.name.group_uf,
        "missing_masks": scan.missing_masks,
        "extra_files": scan.extra_files,
        "masks": {
            label: {
                "present": stats.present,
                "error": stats.error,
                "foreground_px": stats.foreground_px,
                "observed": stats.observed,
                "component_count": len(stats.components),
                "components": (
                    [c.as_dict() for c in stats.components]
                    if label in GEOMETRY_IN_MANIFEST
                    else None
                ),
                "geometry_omitted": label not in GEOMETRY_IN_MANIFEST,
            }
            for label, stats in scan.masks.items()
        },
    }


def build_report(scans: list, connectivity: int, args) -> dict:
    pixel_values: Counter = Counter()
    modes: Counter = Counter()
    palettes = 0
    non_binary = []
    per_label = {}
    image_sizes: Counter = Counter()
    misaligned = []
    image_errors = []
    mask_errors = []
    missing = Counter()
    extras = []
    unparsed = []

    for scan in scans:
        if scan.image_error:
            image_errors.append({"directory": scan.directory, "error": scan.image_error})
        if scan.image_width:
            image_sizes[(scan.image_width, scan.image_height)] += 1
        if not scan.name.parsed:
            unparsed.append(scan.directory)
        for label in scan.missing_masks:
            missing[label] += 1
        if scan.extra_files:
            extras.append({"directory": scan.directory, "files": scan.extra_files})

    for label in MASK_SUFFIXES:
        areas: list[int] = []
        widths: list[int] = []
        heights: list[int] = []
        fills: list[float] = []
        components_per_image: Counter = Counter()
        non_empty = 0
        empty = 0
        border = 0
        total_components = 0
        foreground_fraction: list[float] = []

        for scan in scans:
            stats = scan.masks.get(label)
            if stats is None or not stats.present:
                continue
            if stats.error:
                mask_errors.append(
                    {"directory": scan.directory, "label": label, "error": stats.error}
                )
                continue
            observed = stats.observed
            modes[(label, observed.get("mode"))] += 1
            for value in observed.get("unique_values", []):
                pixel_values[(label, value)] += 1
            if observed.get("has_palette"):
                palettes += 1
            if not observed.get("is_binary_0_255"):
                non_binary.append(
                    {
                        "directory": scan.directory,
                        "label": label,
                        "unique_values": observed.get("unique_values"),
                    }
                )
            if scan.image_width and (
                observed.get("width") != scan.image_width
                or observed.get("height") != scan.image_height
            ):
                misaligned.append(
                    {
                        "directory": scan.directory,
                        "label": label,
                        "mask": [observed.get("width"), observed.get("height")],
                        "image": [scan.image_width, scan.image_height],
                    }
                )

            components_per_image[len(stats.components)] += 1
            total_components += len(stats.components)
            if stats.foreground_px:
                non_empty += 1
                if scan.image_width:
                    foreground_fraction.append(
                        stats.foreground_px / (scan.image_width * scan.image_height)
                    )
            else:
                empty += 1
            for component in stats.components:
                areas.append(component.area_px)
                widths.append(component.width)
                heights.append(component.height)
                fills.append(component.fill_ratio)
                if component.touches_border:
                    border += 1

        per_label[label] = {
            "declared_meaning_by_source": DECLARED_SEMANTICS[label],
            "urmind_class": None,
            "mapping_status": "nao_verificado_independentemente",
            "masks_present": non_empty + empty,
            "masks_with_content": non_empty,
            "masks_empty": empty,
            "components_total": total_components,
            "components_per_image": dict(sorted(components_per_image.items())),
            "images_with_multiple_components": sum(
                count for size, count in components_per_image.items() if size > 1
            ),
            "components_touching_border": border,
            "area_px_percentiles": percentiles(areas),
            "component_width_percentiles": percentiles(widths),
            "component_height_percentiles": percentiles(heights),
            "fill_ratio_mean": round(sum(fills) / len(fills), 4) if fills else None,
            "foreground_fraction_mean": (
                round(sum(foreground_fraction) / len(foreground_fraction), 6)
                if foreground_fraction
                else None
            ),
            "smallest_components": sorted(areas)[:10],
        }

    # Relação entre máscaras: evidência de que não são mutuamente exclusivas e de
    # que POTHOLE fica sobre a superfície descrita por LANE.
    containment = overlap_evidence(scans, args.overlap_sample)

    groups_segment = Counter(s.name.group_segment for s in scans)
    groups_road = Counter(s.name.group_road for s in scans)
    groups_uf = Counter(s.name.group_uf for s in scans)

    return {
        "version": 1,
        "scope": (
            "semântica medida das máscaras do UNIVALI/DNIT. Mede pixels, regiões e "
            "identificadores; NÃO converte, NÃO decide limiar e NÃO mapeia para a "
            "taxonomia V1."
        ),
        **provenance(
            __file__,
            source_dataset="univali_br",
            source_version="mendeley-v4",
            transform="medição de semântica de máscara (valores, regiões, alinhamento)",
            params={
                "connectivity": connectivity,
                "overlap_sample": args.overlap_sample,
                "overlap_sample_seed": args.seed,
                "limit": args.limit,
                "geometry_in_manifest": list(GEOMETRY_IN_MANIFEST),
            },
        ),
        "inputs": len(scans),
        "outputs": len(scans),
        "dropped": 0,
        "drop_reasons": {},
        "integrity": {
            "package_sha256_declared": package_sha256(),
            "scan_manifest": SCAN_MANIFEST.relative_to(PROJECT_ROOT).as_posix(),
        },
        "raw_modified": False,
        "source_metadata_available": source_metadata(),
        "pixel_encoding": {
            "modes": {f"{label}:{mode}": count for (label, mode), count in sorted(modes.items())},
            "distinct_values_seen": sorted({value for _, value in pixel_values}),
            "value_counts": {
                f"{label}:{value}": count
                for (label, value), count in sorted(pixel_values.items())
            },
            "masks_with_palette": palettes,
            "masks_not_binary_0_255": non_binary[:20],
            "masks_not_binary_count": len(non_binary),
            "foreground_convention": (
                "255 = anotado. Registrado como observação: LANE cobre fração muito "
                "maior do quadro que POTHOLE/CRACK, o que descarta inversão."
            ),
        },
        "image_geometry": {
            "sizes": {f"{w}x{h}": count for (w, h), count in image_sizes.most_common()},
            "mask_image_misaligned": misaligned[:20],
            "mask_image_misaligned_count": len(misaligned),
        },
        "integrity_problems": {
            "image_errors": image_errors,
            "mask_errors": mask_errors,
            "missing_masks": dict(missing),
            "unexpected_files": extras[:20],
            "unparsed_directory_names": unparsed,
        },
        "labels": per_label,
        "mask_relationships": containment,
        "grouping_candidates": {
            "note": (
                "Identificadores lidos do nome da pasta publicado pela fonte, não "
                "inferidos de conteúdo visual."
            ),
            "by_segment": {"groups": len(groups_segment), "sizes": dict(groups_segment.most_common())},
            "by_road": {"groups": len(groups_road), "sizes": dict(groups_road.most_common())},
            "by_uf": {"groups": len(groups_uf), "sizes": dict(groups_uf.most_common())},
        },
        "conclusions": conclusions(per_label, non_binary, misaligned, unparsed),
    }


def overlap_evidence(scans: list, sample_size: int) -> dict:
    """Sobreposição entre máscaras, em amostra determinística.

    Reler as três máscaras de todas as amostras dobraria o custo de E/S sem mudar
    a conclusão, então a relação é medida numa amostra fixa e o tamanho dela fica
    registrado no relatório.
    """
    import numpy as np
    from _univali import load_binary_mask

    candidates = [
        s
        for s in scans
        if s.masks.get("POTHOLE")
        and s.masks["POTHOLE"].foreground_px > 0
        and not s.masks["POTHOLE"].error
    ]
    chosen = candidates[:: max(1, len(candidates) // sample_size)][:sample_size]

    inside_lane, with_crack = [], []
    skipped: list[dict] = []
    for scan in chosen:
        base = require_local(SAMPLES_ROOT / scan.directory)
        prefix = scan.directory
        try:
            pothole, _ = load_binary_mask(base / f"{prefix}_POTHOLE.png")
            lane, _ = load_binary_mask(base / f"{prefix}_LANE.png")
            crack, _ = load_binary_mask(base / f"{prefix}_CRACK.png")
        except Exception as exc:  # noqa: BLE001 - erro da fonte é dado, não exceção
            # Não engolir: uma máscara ilegível aqui muda o denominador da
            # evidência, então a amostra descartada vai para o relatório.
            skipped.append({"directory": scan.directory, "error": f"{type(exc).__name__}: {exc}"})
            continue
        total = int(pothole.sum())
        if not total:
            continue
        inside_lane.append(float((pothole & lane).sum()) / total)
        with_crack.append(float((pothole & crack).sum()) / total)

    def stats(values):
        if not values:
            return {}
        array = np.array(values)
        return {
            "mean": round(float(array.mean()), 4),
            "median": round(float(np.median(array)), 4),
            "fraction_above_0_9": round(float((array > 0.9).mean()), 4),
            "fraction_above_0": round(float((array > 0).mean()), 4),
        }

    return {
        "sampled_images": len(inside_lane),
        "skipped_unreadable": skipped,
        "pothole_pixels_inside_lane": stats(inside_lane),
        "pothole_pixels_also_in_crack": stats(with_crack),
        "interpretation": (
            "POTHOLE fica praticamente contido em LANE, o que é consistente com "
            "LANE = superfície da via. POTHOLE e CRACK NÃO são mutuamente "
            "exclusivas. Nenhuma das duas observações prova o significado do "
            "rótulo: significado continua vindo da declaração da fonte."
        ),
    }


def conclusions(per_label: dict, non_binary: list, misaligned: list, unparsed: list) -> dict:
    pothole = per_label.get("POTHOLE", {})
    return {
        "encoding_is_binary": not non_binary,
        "masks_aligned_to_image": not misaligned,
        "categories_found": list(per_label),
        "convertible_geometry": bool(pothole.get("components_total")),
        "semantic_mapping_to_v1": (
            "NÃO ESTABELECIDO. A correspondência POTHOLE→URMIND_ROAD_D40 é "
            "declarada pela fonte (README do pacote e ficha Mendeley) e continua "
            "sem verificação independente: nenhuma documentação oficial foi "
            "consultada nesta execução e nenhuma inspeção humana foi registrada. "
            "Enquanto isso, a caixa derivada carrega o rótulo nativo UNIVALI_POTHOLE."
        ),
        "crack_mapping": (
            "IMPOSSÍVEL sem inventar rótulo. A fonte publica uma única categoria "
            "de trinca e não declara subtipo; escolher entre D00/D10/D20 por "
            "geometria ou aparência seria fabricar anotação (§8.2)."
        ),
        "lane_mapping": "Não é dano. Nenhuma classe da V1 corresponde.",
        "empty_masks_are_not_negatives": (
            "Máscara vazia significa 'sem região anotada neste arquivo'. A fonte "
            "não declara se a imagem foi examinada e considerada sem buraco, então "
            "tratá-la como negativo confirmado seria transformar desconhecido em fato."
        ),
        "directory_names_unparsed": unparsed,
    }


def source_metadata() -> dict:
    files = {}
    for name in ("README.md", "source.mendeley.files.json"):
        path = RAW_ROOT / name
        files[name] = path.is_file()
    return {
        "files_present": files,
        "fields": (
            "nome da pasta (id, UF, rodovia, trecho, posição); SHA-256 e tamanho do "
            "pacote oficial. NÃO há GPS, timestamp de captura, id de sessão/vídeo, "
            "nem CSV de anotação."
        ),
    }


def package_sha256() -> str | None:
    metadata = RAW_ROOT / "source.mendeley.files.json"
    if not metadata.is_file():
        return None
    try:
        data = json.loads(metadata.read_text(encoding="utf-8"))
        return data[0]["content_details"]["sha256_hash"]
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connectivity", type=int, default=8, choices=(4, 8))
    parser.add_argument("--overlap-sample", type=int, default=120)
    parser.add_argument("--seed", type=int, default=20260910)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()

    print(f"Medindo máscaras do UNIVALI em {SAMPLES_ROOT.relative_to(PROJECT_ROOT)}")
    scans = scan_all(args.connectivity, args.limit)
    report = build_report(scans, args.connectivity, args)

    if args.no_report:
        print(json.dumps(report["labels"], indent=2, ensure_ascii=False))
        return 0

    lines = "\n".join(json.dumps(scan_row(s), ensure_ascii=False) for s in scans) + "\n"
    write_text_safe(SCAN_MANIFEST, lines)
    report["integrity"]["scan_manifest_sha256"] = file_sha256(SCAN_MANIFEST)
    destination = write_json_report(REPORT_NAME, report)

    print(f"\nVarredura: {SCAN_MANIFEST.relative_to(PROJECT_ROOT)}")
    print(f"Relatório: {destination.relative_to(PROJECT_ROOT)}")
    for label, data in report["labels"].items():
        print(
            f"  {label:<8} conteúdo em {data['masks_with_content']:>5} de "
            f"{data['masks_present']:>5} | regiões: {data['components_total']:>5}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
