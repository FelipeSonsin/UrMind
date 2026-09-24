"""Malha viária da área piloto a partir do OpenStreetMap (MASTER_PLAN §11.3, §25 passo 5).

Só importa o recorte pedido: nada de Brasil inteiro. A fonte é o Overpass API
oficial; cada trecho guarda `osm_id`, tags úteis e a LineString em SRID 4326, e o
lote registra bbox, instante e a data da base OSM usada — proveniência auditável.

    python -m app.services.osm_import --bbox S W N E --label <nome>            # valida e desfaz
    python -m app.services.osm_import --bbox S W N E --label <nome> --commit   # persiste

A área piloto é decisão do projeto e não tem valor padrão aqui.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import selectors
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.services.external_sources.http import ExternalHttpClient

# Vias por onde se circula a pé ou de veículo. Exclui trilhas, escadas e
# construção, que não são alvo de manutenção de pavimento urbano.
HIGHWAYS = (
    "motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|secondary_link|"
    "tertiary|tertiary_link|unclassified|residential|living_street|service|pedestrian|footway"
)
KEPT_TAGS = ("name", "highway", "surface", "lanes", "oneway", "maxspeed", "sidewalk", "ref")
MAX_AREA_DEG2 = 0.01  # ~11 km x 11 km no equador: recorte de piloto, não de estado.


class OsmImportError(RuntimeError):
    pass


@dataclass(frozen=True)
class BBox:
    south: float
    west: float
    north: float
    east: float

    def __post_init__(self) -> None:
        if not (-90 <= self.south < self.north <= 90 and -180 <= self.west < self.east <= 180):
            raise OsmImportError("bbox inválida: use S W N E com S<N e W<E")
        if (self.north - self.south) * (self.east - self.west) > MAX_AREA_DEG2:
            raise OsmImportError("bbox grande demais para área piloto")


@dataclass(frozen=True)
class RoadWay:
    osm_id: int
    name: str | None
    highway: str
    jurisdiction: str | None
    wkt: str
    attributes: dict[str, Any]


def overpass_query(bbox: BBox) -> str:
    box = f"{bbox.south},{bbox.west},{bbox.north},{bbox.east}"
    return f'[out:json][timeout:120];way["highway"~"^({HIGHWAYS})$"]({box});out tags geom;'


FEDERAL = "BR-rodovia-federal"
ESTADUAL = "BR-rodovia-estadual"
MUNICIPAL = "BR-via-urbana-municipal"
# Leito carroçável de via urbana. Ficam de fora, e seguem sem jurisdição:
#
# - `service`: acesso, pátio e estacionamento, boa parte em área privada, fora
#   da via terrestre aberta à circulação pública do CTB (Anexo I);
# - `footway`/`pedestrian`: passeio não é leito carroçável. Em várias cidades a
#   conservação do passeio recai sobre o proprietário lindeiro (em São Paulo,
#   Lei municipal 15.442/2011), então apontar o Município seria apontar o
#   responsável errado. Vai para triagem até haver regra local levantada.
URBAN_HIGHWAYS = frozenset(
    {
        "trunk",
        "trunk_link",
        "primary",
        "primary_link",
        "secondary",
        "secondary_link",
        "tertiary",
        "tertiary_link",
        "unclassified",
        "residential",
        "living_street",
    }
)
# fmt: off
UF = frozenset((
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA",
    "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
))
# fmt: on


def jurisdiction_from_tags(tags: dict[str, str]) -> str | None:
    """Jurisdição derivada só do que a tag afirma (MASTER_PLAN §14.3).

    `ref=BR-xxx` é rodovia federal e `ref=SP-xxx` é estadual — a sigla identifica
    o ente. Sem nenhuma dessas referências, uma via de circulação urbana está na
    circunscrição do Município (CTB, Lei 9.503/1997, art. 24). O que não se
    encaixa em nenhum dos casos continua desconhecido e vai para triagem.
    """
    refs = [ref.strip().upper() for ref in tags.get("ref", "").split(";") if ref.strip()]
    if any(ref.startswith("BR-") for ref in refs):
        return FEDERAL
    if any(len(ref) > 3 and ref[:2] in UF and ref[2] == "-" for ref in refs):
        return ESTADUAL
    return MUNICIPAL if tags.get("highway") in URBAN_HIGHWAYS else None


def parse_ways(payload: dict[str, Any], *, batch: dict[str, Any]) -> list[RoadWay]:
    """Converte a resposta do Overpass. Way com menos de 2 nós não vira LineString."""
    ways: list[RoadWay] = []
    for element in payload.get("elements", []):
        if element.get("type") != "way":
            continue
        tags = element.get("tags") or {}
        geometry = element.get("geometry") or []
        if "highway" not in tags or len(geometry) < 2:
            continue
        coords = ", ".join(f"{node['lon']} {node['lat']}" for node in geometry)
        ways.append(
            RoadWay(
                osm_id=int(element["id"]),
                name=tags.get("name"),
                highway=tags["highway"],
                jurisdiction=jurisdiction_from_tags(tags),
                wkt=f"LINESTRING({coords})",
                attributes={
                    "osm_tags": {key: tags[key] for key in KEPT_TAGS if key in tags},
                    "osm_import": batch,
                },
            )
        )
    return ways


async def fetch_ways(
    bbox: BBox,
    label: str,
    *,
    client: ExternalHttpClient,
    overpass_url: str | None = None,
) -> list[RoadWay]:
    url = overpass_url or get_settings().overpass_api_url
    response = await client.post(
        url,
        data={"data": overpass_query(bbox)},
        timeout=180,
        provider="overpass",
    )
    if response.status_code != 200:
        raise OsmImportError(f"Overpass respondeu HTTP {response.status_code}")
    payload = response.json()
    batch = {
        "label": label,
        "bbox": [bbox.south, bbox.west, bbox.north, bbox.east],
        "source": url,
        "osm_base": (payload.get("osm3s") or {}).get("timestamp_osm_base"),
        "fetched_at": datetime.now(UTC).isoformat(),
        "correlation_id": response.extensions.get("urmind_correlation_id"),
        "license": "ODbL 1.0 — © OpenStreetMap contributors",
    }
    return parse_ways(payload, batch=batch)


async def _main(args: argparse.Namespace) -> int:
    from app.db.session import Database
    from app.repositories.core import RoadSegmentRepository

    bbox = BBox(*args.bbox)
    settings = get_settings()
    async with ExternalHttpClient.from_settings(settings) as client:
        ways = await fetch_ways(
            bbox,
            args.label,
            client=client,
            overpass_url=settings.overpass_api_url,
        )
    database = Database(settings)
    try:
        async with database.sessionmaker() as session:
            repository = RoadSegmentRepository(session)
            written = await repository.upsert_osm(ways)
            summary = await repository.batch_summary(args.label)
            if args.commit:
                await session.commit()
            else:
                await session.rollback()
    finally:
        await database.close()
    print(
        json.dumps(
            {"ways": len(ways), "written": written, "committed": args.commit, **summary},
            default=str,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("S", "W", "N", "E"))
    parser.add_argument("--label", required=True)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args(argv)
    if sys.platform == "win32":
        return asyncio.run(
            _main(args), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        )
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
