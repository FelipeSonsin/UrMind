"""Inventário das máscaras CRACK do UNIVALI: torná-las conhecidas, não utilizáveis.

O pacote do UNIVALI publica **três** máscaras por amostra — `LANE`, `CRACK` e
`POTHOLE` — e o pipeline só consumiu `POTHOLE`. A auditoria final mediu o que
estava parado: 1.921 máscaras `CRACK` com conteúdo, contra 564 imagens com buraco.
São anotações humanas brasileiras, já licenciadas (CC BY 4.0) e já em disco, que
o sistema não enxergava em manifesto nenhum.

Este script fecha a distância entre

    dado existente no disco  →  dado conhecido pelo sistema

e **não** a distância para `dado utilizável`. Ele não mapeia nada:

- `CRACK` é categoria única de trinca. A fonte não declara subtipo, e o §8.2
  proíbe escolher entre D00/D10/D20 por geometria ou aparência — escolher seria
  inventar o rótulo. Foi por isso que a pasta `cracks` do Urban Community
  também foi recusada;
- portanto `urmind_class` sai `null`, `training_eligible` sai `false` e
  `canonical_evaluation_eligible` sai `false`;
- e as amostras herdam o grupo de rodovia/segmento do UNIVALI, porque, se um dia
  forem usadas, elas precisam viajar com o mesmo grupo do holdout brasileiro —
  caso contrário a mesma cena cairia dos dois lados.

    python -B scripts/datasets/audit_univali_cracks.py
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
    is_cloud_only,
    provenance,
    require_local,
    write_json_report,
    write_text_safe,
)

RAW_DIR = DATASETS_DIR / "raw" / "univali_br" / "v1"
BOXES_MANIFEST = DATASETS_DIR / "manifests" / "univali_br_boxes.jsonl"
SCAN_MANIFEST = DATASETS_DIR / "manifests" / "univali_br_crack_scan.jsonl"
REPORT_NAME = "univali_crack_inventory.json"

# As duas máscaras que o pipeline nunca consumiu. `POTHOLE` fica de fora porque
# já tem derivada própria; estas duas não tinham nada — nem sequer o arquivo
# ligado a um registro de recusa.
LABELS = {
    "CRACK": (
        "categoria única de trinca. A fonte não declara subtipo e o §8.2 proíbe "
        "escolher entre D00/D10/D20 por geometria ou aparência: escolher seria "
        "inventar o rótulo, não derivá-lo."
    ),
    "LANE": (
        "delimitação da superfície trafegável, não dano. Não há classe da V1 que "
        "corresponda, e nem deveria haver: é contexto geométrico, não avaria."
    ),
}


def load_boxes() -> dict[str, dict]:
    """Metadados por amostra, reaproveitados da derivada já validada."""
    linhas = {}
    for line in require_local(BOXES_MANIFEST).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        registro = json.loads(line)
        linhas[registro["directory"]] = registro
    return linhas


def measure(path) -> dict:
    """Mede o pixel da máscara. Não escreve nada e não toca no raw."""
    import numpy as np
    from PIL import Image

    with Image.open(path) as handle:
        imagem = handle.convert("L")
        largura, altura = imagem.size
        dados = np.array(imagem)
    frente = int((dados > 0).sum())
    return {
        "mask_width": largura,
        "mask_height": altura,
        "foreground_px": frente,
        "foreground_fraction": round(frente / (largura * altura), 6),
        "read_status": "READ_OK",
    }


def build() -> tuple[list[dict], dict]:
    metadados = load_boxes()
    linhas: list[dict] = []
    positivas = vazias = ilegiveis = nuvem = 0
    sem_metadado = 0
    areas: list[int] = []
    por_rotulo: Counter = Counter()
    positivas_por_rotulo: Counter = Counter()

    for amostra in sorted(p for p in RAW_DIR.iterdir() if p.is_dir()):
        base = metadados.get(amostra.name)
        if base is None:
            sem_metadado += 1

        for rotulo, motivo in LABELS.items():
            mascara = amostra / f"{amostra.name}_{rotulo}.png"
            if not mascara.is_file():
                continue

            if is_cloud_only(mascara):
                nuvem += 1
                medida = {
                    "mask_width": None,
                    "mask_height": None,
                    "foreground_px": None,
                    "foreground_fraction": None,
                    "read_status": "SKIPPED_CLOUD_ONLY",
                }
            else:
                try:
                    medida = measure(mascara)
                except (OSError, ValueError) as exc:
                    ilegiveis += 1
                    medida = {
                        "mask_width": None,
                        "mask_height": None,
                        "foreground_px": None,
                        "foreground_fraction": None,
                        "read_status": f"READ_ERROR: {type(exc).__name__}",
                    }

            frente = medida["foreground_px"]
            por_rotulo[rotulo] += 1
            if frente:
                positivas += 1
                positivas_por_rotulo[rotulo] += 1
                if rotulo == "CRACK":
                    areas.append(frente)
            elif frente == 0:
                vazias += 1

            linhas.append(
                {
                    "dataset_id": "univali_br",
                    "derived": True,
                    "artifact_kind": "provenance_inventory",
                    "source_label": rotulo,
                    "mask_kind": "binary_png_semantic",
                    # O ponto inteiro deste arquivo: registrar sem autorizar.
                    "urmind_class": None,
                    "no_mapping_reason": motivo,
                    "training_eligible": False,
                    "canonical_evaluation_eligible": False,
                    "eligibility_note": (
                        "sem mapeamento canônico não há classe para treinar nem "
                        "para medir; enquanto o §8.2 não tiver subtipo de trinca, "
                        "as duas elegibilidades continuam falsas"
                    ),
                    "directory": amostra.name,
                    "mask_relpath": mascara.relative_to(PROJECT_ROOT).as_posix(),
                    "image_relpath": (
                        base["image_relpath"]
                        if base
                        else (amostra / f"{amostra.name}_RAW.jpg")
                        .relative_to(PROJECT_ROOT)
                        .as_posix()
                    ),
                    "image_width": base["image_width"] if base else None,
                    "image_height": base["image_height"] if base else None,
                    # Mesmo grupo da derivada de buraco: se um dia entrarem, têm
                    # de cair do mesmo lado do split que o holdout brasileiro.
                    "group_segment": base["group_segment"] if base else None,
                    "group_road": base["group_road"] if base else None,
                    "group_uf": base["group_uf"] if base else None,
                    **medida,
                }
            )

    resumo = {
        "samples": len(linhas),
        "by_label": dict(sorted(por_rotulo.items())),
        "positive_by_label": dict(sorted(positivas_por_rotulo.items())),
        "positive_masks": positivas,
        "empty_masks": vazias,
        "unreadable_masks": ilegiveis,
        "cloud_only_masks": nuvem,
        "samples_without_derived_metadata": sem_metadado,
        "crack_foreground_px": {
            "min": min(areas) if areas else None,
            "median": sorted(areas)[len(areas) // 2] if areas else None,
            "max": max(areas) if areas else None,
        },
        "ufs": dict(Counter(linha["group_uf"] for linha in linhas if linha["group_uf"])),
        "roads": len({linha["group_road"] for linha in linhas if linha["group_road"]}),
        "segments": len({linha["group_segment"] for linha in linhas if linha["group_segment"]}),
    }
    return linhas, resumo


def coverage_check(linhas: list[dict]) -> dict:
    """Nenhum arquivo relevante do UNIVALI pode ficar invisível aos manifestos.

    Era o defeito de fundo: 2.235 máscaras existiam em disco e nenhum manifesto
    as citava, então elas não apareciam em relatório nenhum — nem como recusadas,
    nem como não medidas. Ausência silenciosa é pior que recusa registrada.
    """
    conhecidos: set[str] = set()
    for manifesto in ("univali_br_boxes.jsonl", "univali_br_mask_scan.jsonl"):
        caminho = DATASETS_DIR / "manifests" / manifesto
        if not caminho.is_file() or is_cloud_only(caminho):
            continue
        conhecidos.update(
            parte
            for line in caminho.read_text(encoding="utf-8").splitlines()
            if line.strip()
            for parte in json.loads(line).values()
            if isinstance(parte, str) and parte.startswith("datasets/raw/")
        )
    conhecidos.update(linha["mask_relpath"] for linha in linhas)
    conhecidos.update(linha["image_relpath"] for linha in linhas)

    invisiveis = []
    for caminho in RAW_DIR.rglob("*"):
        if not caminho.is_file():
            continue
        relativo = caminho.relative_to(PROJECT_ROOT).as_posix()
        if relativo not in conhecidos:
            invisiveis.append(relativo)

    return {
        "raw_files": sum(1 for p in RAW_DIR.rglob("*") if p.is_file()),
        "referenced_by_manifests": len(conhecidos),
        "invisible_files": len(invisiveis),
        "invisible_sample": sorted(invisiveis)[:20],
        "passed": not invisiveis,
        "rule": (
            "todo arquivo de raw/univali_br precisa ser citado por algum "
            "manifesto — como usado, recusado ou apenas inventariado"
        ),
    }


def main() -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()

    linhas, resumo = build()
    cobertura = coverage_check(linhas)

    report = {
        "version": 1,
        "artifact_kind": "provenance_inventory",
        "scope": (
            "inventário das máscaras CRACK do UNIVALI. Registra existência, "
            "conteúdo medido e proveniência. NÃO mapeia, NÃO autoriza treino e "
            "NÃO habilita avaliação."
        ),
        **provenance(
            __file__,
            source_dataset="univali_br",
            source_version="mendeley-v4",
            transform="medição de pixel das máscaras não consumidas e registro de proveniência",
            params={
                "source_labels": sorted(LABELS),
                "mapped_to": None,
                "no_mapping_reason": LABELS,
            },
        ),
        "inputs": resumo["samples"],
        "outputs": resumo["samples"],
        "dropped": 0,
        "drop_reasons": {
            "none": "inventário não descarta: registrar é o objetivo"
        },
        "integrity": {
            "scan_manifest": SCAN_MANIFEST.relative_to(PROJECT_ROOT).as_posix(),
            "boxes_manifest_sha256": file_sha256(BOXES_MANIFEST),
        },
        "raw_modified": False,
        "counts": resumo,
        "taxonomy": {
            "source_labels": LABELS,
            "source_taxonomy": (
                "CRACK é categoria única de trinca, sem subtipo declarado; LANE é "
                "delimitação de superfície, não avaria"
            ),
            "urmind_class": None,
            "training_eligible": False,
            "canonical_evaluation_eligible": False,
            "what_would_unlock": (
                "o §8.2 ganhar classe de trinca compatível — genérica, ou com "
                "protocolo próprio que separe D00/D10/D20. Sem isso, nenhuma "
                "quantidade de máscara resolve, porque o que falta é a classe."
            ),
        },
        "coverage": cobertura,
        "why_this_exists": (
            "1.921 anotações humanas brasileiras de trinca estavam em disco e "
            "invisíveis ao sistema. Registrá-las não as torna utilizáveis; torna "
            "possível decidir sobre elas."
        ),
    }

    if args.no_report:
        print(json.dumps({"counts": resumo, "coverage": cobertura}, indent=2, ensure_ascii=False))
        return 0

    write_text_safe(
        SCAN_MANIFEST, "\n".join(json.dumps(linha, ensure_ascii=False) for linha in linhas) + "\n"
    )
    report["integrity"]["scan_manifest_sha256"] = file_sha256(SCAN_MANIFEST)
    destino = write_json_report(REPORT_NAME, report)

    print(f"Inventário: {SCAN_MANIFEST.relative_to(PROJECT_ROOT)}")
    print(f"Relatório : {destino.relative_to(PROJECT_ROOT)}")
    print(
        f"  {resumo['samples']} máscaras {sorted(LABELS)} | "
        f"{resumo['positive_masks']} com conteúdo | {resumo['empty_masks']} vazias | "
        "urmind_class=null em todas"
    )
    print(
        f"  cobertura: {cobertura['invisible_files']} arquivo(s) invisível(is) "
        f"de {cobertura['raw_files']}"
    )
    return 0 if cobertura["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
