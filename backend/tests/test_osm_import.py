"""Parser e limites do importador OSM (§11.3). Sem rede."""

from __future__ import annotations

import pytest

from app.services.osm_import import (
    BBox,
    OsmImportError,
    jurisdiction_from_tags,
    overpass_query,
    parse_ways,
)

BATCH = {"label": "teste", "bbox": [0, 0, 0.01, 0.01]}


def test_way_vira_linestring_lon_lat_com_tags_uteis() -> None:
    payload = {
        "elements": [
            {
                "type": "way",
                "id": 42,
                "tags": {"highway": "residential", "name": "Rua A", "surface": "asphalt", "fixme": "x"},
                "geometry": [{"lat": -23.5, "lon": -46.6}, {"lat": -23.51, "lon": -46.61}],
            }
        ]
    }
    (way,) = parse_ways(payload, batch=BATCH)
    assert way.osm_id == 42
    assert way.wkt == "LINESTRING(-46.6 -23.5, -46.61 -23.51)"
    assert way.attributes["osm_tags"] == {"name": "Rua A", "highway": "residential", "surface": "asphalt"}
    assert way.attributes["osm_import"] == BATCH


def test_way_degenerado_ou_sem_highway_e_ignorado() -> None:
    payload = {
        "elements": [
            {"type": "way", "id": 1, "tags": {"highway": "service"}, "geometry": [{"lat": 0, "lon": 0}]},
            {"type": "way", "id": 2, "tags": {"building": "yes"}, "geometry": [{"lat": 0, "lon": 0}, {"lat": 1, "lon": 1}]},
            {"type": "node", "id": 3},
        ]
    }
    assert parse_ways(payload, batch=BATCH) == []


@pytest.mark.parametrize(
    "coords",
    [(-23.5, -46.6, -23.6, -46.5), (-23.5, -46.6, -23.4, -46.7), (-24.0, -47.0, -23.0, -46.0)],
    ids=["sul>norte", "oeste>leste", "area-grande"],
)
def test_bbox_invalida_ou_grande_e_recusada(coords) -> None:
    with pytest.raises(OsmImportError):
        BBox(*coords)


def test_query_restrita_ao_recorte() -> None:
    query = overpass_query(BBox(-23.56, -46.64, -23.55, -46.63))
    assert "(-23.56,-46.64,-23.55,-46.63)" in query
    assert "out tags geom" in query


@pytest.mark.parametrize(
    ("tags", "esperado"),
    [
        ({"highway": "trunk", "ref": "BR-116"}, "BR-rodovia-federal"),
        ({"highway": "primary", "ref": "SP-270;BR-116"}, "BR-rodovia-federal"),
        ({"highway": "primary", "ref": "SP-270"}, "BR-rodovia-estadual"),
        ({"highway": "secondary", "ref": "MG-050"}, "BR-rodovia-estadual"),
        ({"highway": "residential"}, "BR-via-urbana-municipal"),
        # Passeio não é leito carroçável e a conservação pode ser do proprietário
        # lindeiro: sem regra local levantada, fica em triagem.
        ({"highway": "footway"}, None),
        ({"highway": "pedestrian"}, None),
        ({"highway": "trunk", "ref": ""}, "BR-via-urbana-municipal"),
        # `service` é acesso, pátio ou estacionamento: fora da circunscrição
        # presumida do Município, então continua desconhecido.
        ({"highway": "service"}, None),
        ({"name": "sem classificação"}, None),
        # `ref` que não é sigla de rodovia não muda o ente responsável.
        ({"highway": "tertiary", "ref": "Rota 3"}, "BR-via-urbana-municipal"),
    ],
)
def test_jurisdicao_sai_da_tag_e_nunca_do_palpite(tags, esperado) -> None:
    assert jurisdiction_from_tags(tags) == esperado


def test_via_urbana_importada_ja_nasce_com_competencia_municipal() -> None:
    payload = {
        "elements": [
            {
                "type": "way",
                "id": 7,
                "tags": {"highway": "residential", "name": "Rua da Glória"},
                "geometry": [{"lat": -23.55, "lon": -46.63}, {"lat": -23.56, "lon": -46.64}],
            }
        ]
    }
    (way,) = parse_ways(payload, batch=BATCH)
    assert way.jurisdiction == "BR-via-urbana-municipal"
