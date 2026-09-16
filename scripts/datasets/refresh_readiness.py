"""Regera datasets/reports/dataset_readiness.json a partir do catálogo e do disco.

Este script existe porque a versão anterior do relatório era escrita à mão e
envelheceu: continuou declarando `project_sidewalk`, `rampnet` e `bdd100k` como
`no_dataset_data` depois de os três estarem em disco, com adaptador e com
checksum oficial conferido, e continuou listando `mapillary_msls`, aposentado
em 2026-09-08.

A causa não foi descuido — foi arquitetura. Existiam três descrições paralelas
das mesmas oito fontes (o catálogo em código, as linhas fixas de
`refresh_sources.py` e a lista fixa de `audit_readiness.py`) e nenhuma delas
sabia da existência das outras. Agora há uma:

    backend/app/datasets/catalog.py   ← declara identidade, papel e uso
    backend/app/datasets/inventory.py ← mede o disco
    backend/app/datasets/adapters.py  ← lê o conteúdo
    backend/app/datasets/readiness.py ← junta os três e aponta divergência
    este script                       ← publica o resultado, sem opinar

Nenhum número deste relatório é digitado. Se o catálogo discordar do disco, a
divergência aparece no campo `divergences` em vez de ser resolvida em silêncio.

Uso:

    python -B scripts/datasets/refresh_readiness.py            # sem checksum
    python -B scripts/datasets/refresh_readiness.py --verify   # confere checksum

Exige o ambiente do backend (`backend/.venv`), porque a leitura das fontes é a
mesma que o sistema usa — reimplementá-la aqui recriaria o problema que o
script resolve.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

from _core import (
    DATASETS_DIR,
    PROJECT_ROOT,
    configure_stdout,
    file_sha256,
    write_json_report,
)

sys.path.insert(0, str(PROJECT_ROOT / "backend"))

try:
    from app.datasets.catalog import get_source
    from app.datasets.readiness import profile_all, totals
except ModuleNotFoundError as exc:  # pragma: no cover - depende do ambiente
    raise SystemExit(
        "este script precisa do ambiente do backend; rode com "
        "backend/.venv/Scripts/python (Windows) ou backend/.venv/bin/python "
        f"— import falhou: {exc}"
    ) from exc

FORMAT_AUDIT = DATASETS_DIR / "reports" / "dataset_format_audit.json"


def _catalog_block(dataset_id: str) -> dict:
    """O que o catálogo declara. Copiado, nunca reescrito aqui."""
    source = get_source(dataset_id)
    return {
        "homepage": source.homepage,
        "license": source.license,
        "version": source.version,
        "adapter": source.adapter,
        "usage_note": source.usage_note,
        "potential_usage": [u.value for u in source.potential_usage],
        "unlock_requirement": source.unlock_requirement,
        "planned_step": source.planned_step,
        "taxonomy_note": source.taxonomy_note,
        "group_note": source.group_note,
        "caveats": list(source.caveats),
    }


def main() -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="confere o checksum publicado de cada arquivo (lê tudo; é lento)",
    )
    args = parser.parse_args()

    profiles = profile_all(verify=args.verify)
    agregado = totals(profiles)

    payload = {
        "version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "generated_by": "scripts/datasets/refresh_readiness.py",
        "source_of_truth": "backend/app/datasets/catalog.py",
        "scope": (
            "estado local, formato e contagem de anotações. NÃO mede desempenho "
            "de detecção: nenhum treino ou inferência foi executado."
        ),
        "checksums_verified": args.verify,
        "raw_modified": False,
        "units": "bytes; GB decimal = 1.000.000.000 bytes",
        "definitions": {
            "box_annotations": (
                "caixas aceitas na taxonomia V1 (§8.2). É o único número que o "
                "detector YOLOX da V1 treina."
            ),
            "mask_annotations": (
                "máscaras aceitas na taxonomia V1. Anotação humana real, em outra "
                "geometria; exige conversão registrada antes de virar treino."
            ),
            "negative_images": (
                "imagem sem nenhuma anotação aceita e sem nenhuma recusada: a fonte "
                "olhou e não havia nada. Material legítimo de treino (taxonomy.yaml). "
                "Só é interpretável como negativo de treino em fonte que produz "
                "anotação da V1 — em fonte de contexto significa apenas 'sem "
                "anotação nenhuma'."
            ),
            "geo_kinds": (
                "vocabulário próprio dos registros com coordenada. Não é rótulo "
                "recusado: é categoria da fonte que nunca se candidatou a classe."
            ),
            "out_of_scope_images": (
                "imagem cuja anotação existia e foi recusada por estar fora do §8.2. "
                "NÃO é negativa: tratá-la como tal ensinaria o detector que aquele "
                "objeto não existe."
            ),
            "unverified_negative_images": (
                "imagem sem anotação cuja ausência a fonte NÃO comprova ser "
                "negativa: não há protocolo de anotação declarado. Fica retida e "
                "fora de `negative_images` — contá-la como negativa ensinaria o "
                "detector que o objeto não estava lá."
            ),
            "unmeasured_mask_samples": (
                "amostra cuja máscara foi anexada por existir em disco, sem que o "
                "pixel fosse lido. Usabilidade desconhecida, não confirmada: fica "
                "fora de `usable_samples` até alguém medir o conteúdo."
            ),
            "feeds_detector": "produz ao menos uma caixa na taxonomia V1.",
            "cloud_only_files": (
                "arquivos que existem como marcador do OneDrive, sem conteúdo "
                "local. `exists()` diz True e o tamanho é o da nuvem; abrir cada "
                "um dispara download. `locally_available: false` significa que a "
                "fonte NÃO está pronta para treino offline."
            ),
        },
        "totals": agregado,
        "datasets": {
            profile.dataset_id: {**profile.as_dict(), "catalog": _catalog_block(profile.dataset_id)}
            for profile in profiles
        },
    }

    # A auditoria profunda de formato (decodificação de imagem/máscara, validação
    # das linhas YOLO, estrutura do MP4) continua em audit_readiness.py. Ela é
    # referenciada por checksum, não copiada: duas cópias do mesmo fato é o que
    # produziu o relatório obsoleto.
    if FORMAT_AUDIT.is_file():
        payload["format_audit"] = {
            "path": FORMAT_AUDIT.relative_to(PROJECT_ROOT).as_posix(),
            "sha256": file_sha256(FORMAT_AUDIT),
        }
    else:
        payload["format_audit"] = {
            "path": None,
            "note": "rode scripts/datasets/audit_readiness.py para gerar",
        }

    destino = write_json_report("dataset_readiness.json", payload)

    print(f"{destino.relative_to(PROJECT_ROOT).as_posix()}: {len(profiles)} fontes")
    print(
        f"  acervo {agregado['stored_gb_decimal']:.2f} GB | "
        f"treinável {agregado['trainable_stored_gb_decimal']:.2f} GB em "
        f"{len(agregado['trainable_sources'])} fonte(s)"
    )
    print(f"  caixas por classe: {json.dumps(agregado['class_counts_boxes'], ensure_ascii=False)}")
    if agregado["sources_not_fully_local"]:
        print(
            f"  NÃO ESTÃO INTEIRAS EM DISCO: "
            f"{', '.join(agregado['sources_not_fully_local'])} — "
            f"{agregado['cloud_only_files']} arquivo(s), "
            f"{agregado['cloud_only_gb_decimal']:.2f} GB só na nuvem"
        )
    if agregado["divergences"]:
        print("  DIVERGÊNCIAS catálogo × disco:")
        for dataset_id, itens in agregado["divergences"].items():
            for item in itens:
                print(f"    {dataset_id}: {item}")
    if agregado["read_errors"]:
        print("  ERROS DE LEITURA:")
        for dataset_id, erro in agregado["read_errors"].items():
            print(f"    {dataset_id}: {erro}")
    return 1 if (agregado["divergences"] or agregado["read_errors"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
