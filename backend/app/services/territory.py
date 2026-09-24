"""Versioned IBGE country geometry for operational capture admission only.

This policy never selects or excludes scientific training examples.
"""

import asyncio
import hashlib
import json
import selectors
import sys
from typing import Literal
from urllib.parse import urlsplit

import httpx
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import URMIND_DEV_SHADOW_REF, get_settings
from app.db.session import connect_args, normalize_database_url
from app.schemas.core import Coordinate

TerritoryStatus = Literal["inside", "uncertain", "outside"]

IBGE_GEOJSON_URL = (
    "https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR?formato=application%2Fvnd.geo%2Bjson"
)
IBGE_GEOJSON_SHA256 = "933da368ffec297004c1d934c7e4bb3653b9bd5ac8bc5c1d830458f187059aee"


class TerritoryUnavailable(RuntimeError):
    """No validated Brazil geometry is installed in the operational database."""


def classify_location(
    *, covers: bool | None, distance_m: float | None, accuracy_m: float | None
) -> TerritoryStatus:
    if covers is None or distance_m is None:
        raise TerritoryUnavailable("malha territorial do Brasil indisponivel")
    if covers:
        return "inside"
    # A simplified coastline and device uncertainty must not turn a border
    # observation into a definitive foreign location. Cap untrusted accuracy.
    tolerance_m = max(500.0, min(accuracy_m or 0.0, 5000.0))
    return "uncertain" if distance_m <= tolerance_m else "outside"


async def assess_brazil_location(
    session: AsyncSession, coordinate: Coordinate
) -> tuple[TerritoryStatus, str]:
    point_sql = "ST_SetSRID(ST_MakePoint(:longitude,:latitude),4326)"
    try:
        result = await session.execute(
            text(
                "select source_sha256, "
                f"ST_Covers(geom,{point_sql}) as covers, "
                f"ST_Distance(geom::geography,{point_sql}::geography) as distance_m "
                "from public.operational_territory where code='BR'"
            ),
            {"longitude": coordinate.longitude, "latitude": coordinate.latitude},
        )
    except SQLAlchemyError as exc:
        raise TerritoryUnavailable("consulta territorial indisponivel") from exc
    row = result.mappings().first()
    if row is None:
        raise TerritoryUnavailable("malha territorial do Brasil nao instalada")
    status = classify_location(
        covers=row["covers"],
        distance_m=row["distance_m"],
        accuracy_m=coordinate.accuracy_m,
    )
    return status, str(row["source_sha256"])


async def install_ibge_boundary() -> None:
    """Install one pinned official geometry in DEV without rewriting an existing one."""
    settings = get_settings()
    if urlsplit(settings.supabase_url or "").hostname != f"{URMIND_DEV_SHADOW_REF}.supabase.co":
        raise RuntimeError("importacao territorial permitida apenas no Urmind DEV")
    database_url = settings.migration_database_url
    if not database_url:
        raise RuntimeError("MIGRATION_DATABASE_URL ausente")
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(IBGE_GEOJSON_URL)
    response.raise_for_status()
    digest = hashlib.sha256(response.content).hexdigest()
    if digest != IBGE_GEOJSON_SHA256:
        raise RuntimeError("malha IBGE mudou; revisar fonte antes de importar")
    document = response.json()
    features = document.get("features") if isinstance(document, dict) else None
    if (
        not isinstance(document, dict)
        or document.get("type") != "FeatureCollection"
        or not isinstance(features, list)
        or len(features) != 1
        or not isinstance(features[0], dict)
        or features[0].get("properties", {}).get("codarea") != "BR"
        or features[0].get("geometry", {}).get("type") != "MultiPolygon"
    ):
        raise RuntimeError("malha IBGE nao corresponde ao pais BR")
    geometry = json.dumps(features[0]["geometry"], separators=(",", ":"))
    engine = create_async_engine(
        normalize_database_url(database_url), connect_args=connect_args(database_url)
    )
    try:
        async with engine.begin() as connection:
            existing = await connection.scalar(
                text("select source_sha256 from public.operational_territory where code='BR'")
            )
            if existing is not None:
                if existing != digest:
                    raise RuntimeError(
                        "malha territorial diferente ja instalada; revisao necessaria"
                    )
            else:
                inserted = await connection.scalar(
                    text("""with boundary as (
                    select ST_Multi(ST_CollectionExtract(
                        ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(:geometry),4326)),3
                    )) as geom
                )
                insert into public.operational_territory
                    (code,source_url,source_sha256,geom)
                    select 'BR',:source_url,:sha,geom from boundary
                    where ST_IsValid(geom) and not ST_IsEmpty(geom)
                    returning source_sha256"""),
                    {"source_url": IBGE_GEOJSON_URL, "sha": digest, "geometry": geometry},
                )
                if inserted != digest:
                    raise RuntimeError("malha IBGE nao pode ser reparada com ST_MakeValid")
            samples = await connection.execute(
                text("""select
                    ST_Covers(geom,ST_SetSRID(ST_MakePoint(-46.63,-23.55),4326)) as sao_paulo,
                    ST_Covers(geom,ST_SetSRID(ST_MakePoint(-9.14,38.72),4326)) as lisbon,
                    ST_IsValid(geom) as valid
                    from public.operational_territory where code='BR'""")
            )
            sample = samples.mappings().one()
            if not sample["sao_paulo"] or sample["lisbon"] or not sample["valid"]:
                raise RuntimeError("malha IBGE falhou nos pontos de controle")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    loop_factory = (
        (lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
        if sys.platform == "win32"
        else None
    )
    asyncio.run(install_ibge_boundary(), loop_factory=loop_factory)
