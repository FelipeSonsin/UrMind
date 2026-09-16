"""Linha de comando da camada de dataset (§25 passo 6).

    python -m app.datasets.cli inventory [--verify] [--json]
    python -m app.datasets.cli read <dataset_id> [--limit N]
    python -m app.datasets.cli register <dataset_id> [--write]

Nenhum comando baixa, extrai ou apaga arquivo. `--verify` lê os arquivos de
ponta a ponta para conferir checksum e é o único que custa tempo de verdade.
`register` grava um manifesto JSON em `datasets/manifests/` — fora de `raw/` —
com o payload exato que iria para `dataset_versions`. A escrita no banco não
acontece aqui: ela é feita quando houver DATABASE_URL e revisão do payload.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.config import datasets_manifests_dir, datasets_raw_dir
from app.datasets.adapters import AdapterError, read_records
from app.datasets.catalog import SOURCES, DatasetRole, get_source
from app.datasets.inventory import DatasetState, inspect_all, inspect_source
from app.datasets.records import AnnotatedImage, MaskSample
from app.datasets.registration import RegistrationRefused, build_dataset_version, summarize
from app.ml.splits import SplitRatios, split_by_group


def _human_bytes(value: int) -> str:
    unidade = ["B", "KB", "MB", "GB", "TB"]
    tamanho = float(value)
    for nome in unidade:
        if tamanho < 1024 or nome == unidade[-1]:
            return f"{tamanho:,.1f} {nome}".replace(",", ".")
        tamanho /= 1024
    return f"{value} B"


def _cmd_inventory(args: argparse.Namespace) -> int:
    inventories = inspect_all(args.raw_root, verify=args.verify)

    if args.json:
        print(json.dumps([i.as_dict() for i in inventories], indent=2, ensure_ascii=False))
        return 0

    print(f"raiz: {args.raw_root or datasets_raw_dir()}\n")
    print(f"{'dataset':<18} {'papel':<14} {'estado':<15} {'em disco':>12}")
    print("-" * 62)
    total = 0
    for inventory in inventories:
        source = get_source(inventory.dataset_id)
        total += inventory.total_bytes
        print(
            f"{inventory.dataset_id:<18} {source.role!s:<14} "
            f"{inventory.state!s:<15} {_human_bytes(inventory.total_bytes):>12}"
        )
        for motivo in inventory.blocking_reasons():
            print(f"{'':<18} -> {motivo}")
    print("-" * 62)
    print(f"{'total':<18} {'':<14} {'':<15} {_human_bytes(total):>12}")
    return 0


def _cmd_read(args: argparse.Namespace) -> int:
    source = get_source(args.dataset_id)
    root = (args.raw_root or datasets_raw_dir()) / source.id

    try:
        records = read_records(source.adapter, root)
    except AdapterError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1

    if args.limit:
        import itertools

        records = itertools.islice(records, args.limit)

    try:
        summary = summarize(source.id, records)
    except AdapterError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary.as_dict(), indent=2, ensure_ascii=False))
    return 0


def _cmd_register(args: argparse.Namespace) -> int:
    source = get_source(args.dataset_id)
    root_dir = args.raw_root or datasets_raw_dir()

    inventory = inspect_source(source, root_dir, verify=not args.skip_checksum)

    records: list = []
    # DECLARED_ONLY é o estado normal de uma fonte sem ZIP oficial (exportação
    # sob demanda); ela também deve ser lida antes de montar o payload.
    legivel = inventory.state in {DatasetState.READY, DatasetState.DECLARED_ONLY}
    if source.adapter is not None and legivel:
        try:
            records = list(read_records(source.adapter, root_dir / source.id))
        except AdapterError as exc:
            print(f"erro ao ler {source.id}: {exc}", file=sys.stderr)
            return 1

    summary = summarize(source.id, records)

    split = None
    if source.role is DatasetRole.TRAINING_V1 and records:
        treinaveis = [r for r in records if isinstance(r, AnnotatedImage | MaskSample) and r.usable]
        # As proporções vêm do uso declarado, não de um padrão fixo. Fonte
        # proibida de avaliar não pode ter lado medido: pedir 70/15/15 para ela
        # abortaria o registro no portão do §8.4 — o comando falharia por uma
        # cota que o próprio catálogo já disse que ela não pode ter.
        ratios = (
            SplitRatios(train=1.0, validation=0.0, test=0.0)
            if source.evaluation_forbidden
            else SplitRatios()
        )
        split = split_by_group(treinaveis, group_key=lambda r: r.group, ratios=ratios)

    try:
        payload = build_dataset_version(
            source,
            inventory,
            summary,
            split,
            require_split=source.role is DatasetRole.TRAINING_V1,
        )
    except RegistrationRefused as exc:
        print(f"recusado: {exc}", file=sys.stderr)
        return 2

    texto = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.write:
        destino = datasets_manifests_dir()
        destino.mkdir(parents=True, exist_ok=True)
        caminho = destino / f"{source.id}.json"
        caminho.write_text(texto + "\n", encoding="utf-8")
        print(f"manifesto gravado em {caminho}")
    else:
        print(texto)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.datasets.cli",
        description="Inventário, leitura e registro dos datasets do UrMind (§25 passo 6).",
    )
    parser.add_argument(
        "--raw-root",
        type=Path,
        default=None,
        help="raiz alternativa de datasets/raw (padrão: URMIND_DATASETS_DIR ou datasets/raw)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    inventory = sub.add_parser("inventory", help="estado real dos arquivos em disco")
    inventory.add_argument(
        "--verify", action="store_true", help="confere checksum publicado (lê tudo; é lento)"
    )
    inventory.add_argument("--json", action="store_true", help="saída em JSON")
    inventory.set_defaults(func=_cmd_inventory)

    read = sub.add_parser("read", help="lê a fonte e resume classes aceitas e recusadas")
    read.add_argument("dataset_id", choices=[s.id for s in SOURCES])
    read.add_argument("--limit", type=int, default=None, help="lê apenas as N primeiras amostras")
    read.set_defaults(func=_cmd_read)

    register = sub.add_parser("register", help="monta o payload de dataset_versions")
    register.add_argument("dataset_id", choices=[s.id for s in SOURCES])
    register.add_argument(
        "--write", action="store_true", help="grava datasets/manifests/<id>.json"
    )
    register.add_argument(
        "--skip-checksum",
        action="store_true",
        help="não confere checksum (use só para inspeção rápida, nunca para registro final)",
    )
    register.set_defaults(func=_cmd_register)

    return parser


def main(argv: list[str] | None = None) -> int:
    # O console do Windows abre em cp1252 e quebra em "não", "§" ou "→". Os
    # motivos de recusa são escritos em português e precisam sair legíveis.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
