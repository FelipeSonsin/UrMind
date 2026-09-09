"""Auditoria de armazenamento dos datasets (read-only).

Responde três perguntas que costumam ser confundidas entre si:

1. quanto cada dataset ocupa — em bytes lógicos e em disco;
2. quanto sobra no disco e no orçamento de 40 GB;
3. o projeto está numa pasta sincronizada em nuvem?

Não escreve nada dentro de `raw/`, não move, não apaga e não hidrata arquivo
cloud-only. A única escrita é o relatório em `datasets/reports/`.

    python scripts/datasets/audit_storage.py [--json] [--no-report]
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _budget import GB, load_budget, ml_inventory, preflight
from _core import (
    DATASETS_DIR,
    PROJECT_ROOT,
    RAW_DIR,
    configure_stdout,
    detect_cloud_sync,
    free_disk_bytes,
    human_bytes,
    measure_dir,
    relative_to_project,
    write_json_report,
)

SUBPASTAS = (
    "raw",
    "processed",
    "annotations",
    "metadata",
    "splits",
    "downloads",
    "reports",
    "manifests",
)


def coletar() -> dict:
    budget = load_budget()

    por_dataset = {}
    if RAW_DIR.is_dir():
        for pasta in sorted(p for p in RAW_DIR.iterdir() if p.is_dir()):
            por_dataset[pasta.name] = measure_dir(pasta).as_dict()

    por_subpasta = {
        nome: measure_dir(DATASETS_DIR / nome).as_dict()
        for nome in SUBPASTAS
        if (DATASETS_DIR / nome).exists()
    }

    total = measure_dir(DATASETS_DIR)
    livre = free_disk_bytes()

    # Estouros por dataset, comparando com o teto declarado no orçamento.
    # Um teto de 0 GB marca placeholder legado: pasta que só tem README e cuja
    # consolidação depende de autorização. Cobrar teto dela seria ruído.
    estouros = []
    for nome, medida in por_dataset.items():
        teto = budget.dataset_caps.get(nome)
        if teto is None:
            estouros.append(f"{nome}: sem teto declarado em storage_budget.yaml")
        elif teto == 0:
            continue
        elif medida["logical_size"] > teto * GB:
            estouros.append(
                f"{nome}: {human_bytes(medida['logical_size'])} acima da referência orientativa de {teto} GB; redistribuição permitida"
            )

    alertas = list(estouros)
    if total.logical_size > budget.max_local_dataset_bytes:
        alertas.append(
            f"datasets/ com {human_bytes(total.logical_size)} acima do teto de "
            f"{budget.max_local_dataset_gb} GB"
        )
    if livre < budget.min_free_disk_bytes:
        alertas.append(
            f"disco com {human_bytes(livre)} livres, abaixo do mínimo de "
            f"{budget.min_free_disk_gb} GB"
        )

    ml = ml_inventory()
    if ml["total_ml_bytes"] > budget.max_total_ml_bytes:
        alertas.append("ecossistema ML acima de 40 GB; novas escritas bloqueadas")
    if total.logical_size > 35 * GB:
        alertas.append(
            "datasets acima do alvo orientativo de 35 GB; verificar margem para modelos"
        )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "project_root": relative_to_project(PROJECT_ROOT) or ".",
        "cloud_sync": detect_cloud_sync(),
        "budget": {
            "MAX_TOTAL_ML_STORAGE_GB": budget.max_total_ml_gb,
            "MAX_LOCAL_DATASET_SIZE_GB": budget.max_local_dataset_gb,
            "MIN_FREE_DISK_GB": budget.min_free_disk_gb,
            "dataset_caps_gb": budget.dataset_caps,
        },
        "disk": {
            "free_bytes": livre,
            "free_human": human_bytes(livre),
            "min_free_required_bytes": budget.min_free_disk_bytes,
            "headroom_bytes": livre - budget.min_free_disk_bytes,
        },
        "datasets_total": total.as_dict(),
        "ml_inventory": ml,
        "size_on_disk_method": "GetCompressedFileSizeW no Windows; st_blocks*512 em POSIX; sem abrir conteúdo",
        "units": "bytes; GB decimal (1e9); exibição humana GiB (2**30)",
        "budget_remaining_bytes": budget.max_local_dataset_bytes - total.logical_size,
        "ml_budget_remaining_bytes": budget.max_total_ml_bytes - ml["total_ml_bytes"],
        "target_datasets_bytes": 35 * GB,
        "individual_allocations_are_advisory": True,
        "by_dataset": por_dataset,
        "by_subfolder": por_subpasta,
        "warnings": alertas,
    }


def imprimir(dados: dict) -> None:
    print(f"Auditoria de armazenamento — {dados['generated_at']}")
    sync = dados["cloud_sync"]
    print(
        f"Projeto em pasta sincronizada: "
        f"{'SIM (' + ', '.join(sync['providers']) + ')' if sync['project_root_is_synced'] else 'não'}"
    )
    print()
    print(
        f"{'dataset':<20} {'lógico':>14} {'em disco':>14} {'arquivos':>9} {'ref.':>7}"
    )
    print("-" * 68)
    caps = dados["budget"]["dataset_caps_gb"]
    for nome, m in dados["by_dataset"].items():
        teto = caps.get(nome)
        print(
            f"{nome:<20} {human_bytes(m['logical_size']):>14} "
            f"{human_bytes(m['size_on_disk']):>14} {m['file_count']:>9} "
            f"{(str(teto) + ' GB') if teto is not None else '—':>7}"
        )
    print("-" * 68)
    t = dados["datasets_total"]
    print(
        f"{'TOTAL datasets/':<20} {human_bytes(t['logical_size']):>14} "
        f"{human_bytes(t['size_on_disk']):>14} {t['file_count']:>9}"
    )
    if t["cloud_only_count"]:
        print(f"  arquivos cloud-only (não hidratados): {t['cloud_only_count']}")
    print()
    print(f"Livre no disco       : {dados['disk']['free_human']}")
    print(f"Sobra no orçamento   : {human_bytes(dados['budget_remaining_bytes'])}")
    if dados["warnings"]:
        print("\nAvisos:")
        for aviso in dados["warnings"]:
            print(f"  - {aviso}")


def main(argv: list[str] | None = None) -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--json", action="store_true", help="imprime JSON em vez da tabela"
    )
    parser.add_argument(
        "--no-report", action="store_true", help="não grava em datasets/reports/"
    )
    args = parser.parse_args(argv)

    dados = coletar()
    if args.json:
        import json

        print(json.dumps(dados, indent=2, ensure_ascii=False))
    else:
        imprimir(dados)

    if not args.no_report:
        preflight("relatório de armazenamento", 100_000, 200_000, raise_on_block=True)
        caminho = write_json_report("storage_audit.json", dados)
        if not args.json:
            print(f"\nRelatório: {relative_to_project(caminho)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
