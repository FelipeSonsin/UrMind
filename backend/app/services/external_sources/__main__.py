"""Administra as fontes externas sem executar trabalho destrutivo por padrão."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from app.config import PROJECT_DIR, get_settings
from app.services.external_sources.bulk import (
    CnefeSelection,
    download_cnefe,
    download_geofabrik,
)
from app.services.external_sources.checks import run_live_checks
from app.services.external_sources.http import ExternalHttpClient
from app.services.external_sources.registry import integration_registry


def _run(coro):
    return asyncio.run(coro)


async def _download_geofabrik(destination: Path) -> dict[str, object]:
    settings = get_settings()
    async with ExternalHttpClient.from_settings(settings) as client:
        result = await download_geofabrik(
            destination_dir=destination,
            pbf_url=settings.geofabrik_sudeste_pbf_url,
            md5_url=settings.geofabrik_sudeste_md5_url,
            client=client,
        )
    return asdict(result)


async def _download_cnefe(
    destination: Path,
    selection: CnefeSelection,
) -> dict[str, object]:
    settings = get_settings()
    async with ExternalHttpClient.from_settings(settings) as client:
        result = await download_cnefe(
            destination_dir=destination,
            base_url=settings.ibge_cnefe_2022_base_url,
            selection=selection,
            client=client,
        )
    return asdict(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="diagnóstico offline, sem download")
    check.add_argument("--json", action="store_true")
    live = commands.add_parser("live-check", help="requisições pequenas e opt-in")
    live.add_argument("--json", action="store_true")

    geofabrik = commands.add_parser("download-geofabrik", help="download bulk explícito")
    geofabrik.add_argument("--destination", type=Path, required=True)

    cnefe = commands.add_parser("download-cnefe", help="download CNEFE por UF/município")
    cnefe.add_argument("--destination", type=Path, required=True)
    cnefe.add_argument("--uf")
    cnefe.add_argument("--municipality-code")

    args = parser.parse_args(argv)
    if args.command == "check":
        entries = integration_registry(get_settings(), project_root=PROJECT_DIR)
        payload = [asdict(entry) for entry in entries]
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            for entry in entries:
                print(f"{entry.name:<18} {entry.status}")
        return 0
    if args.command == "live-check":
        results = _run(run_live_checks(get_settings()))
        if args.json:
            print(json.dumps([asdict(result) for result in results], indent=2, ensure_ascii=False))
        else:
            for result in results:
                print(f"{result.name:<18} {result.status:<28} {result.detail}")
        return 0
    if args.command == "download-geofabrik":
        print(json.dumps(_run(_download_geofabrik(args.destination)), indent=2, default=str))
        return 0
    if not args.uf and not args.municipality_code:
        parser.error("download-cnefe exige --uf e/ou --municipality-code")
    selection = CnefeSelection(uf=args.uf, municipality_code=args.municipality_code)
    print(json.dumps(_run(_download_cnefe(args.destination, selection)), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
