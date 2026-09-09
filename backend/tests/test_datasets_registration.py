"""Registro em `dataset_versions` (§8.3 passo 1, §8.4, §26)."""

from __future__ import annotations

import hashlib

import pytest

from app.datasets.catalog import DatasetRole, DatasetSource, ExpectedFile, get_source
from app.datasets.inventory import inspect_source
from app.datasets.records import AnnotatedImage, BoundingBox, GeoRecord, RejectedLabel
from app.datasets.registration import (
    RegistrationRefused,
    build_dataset_version,
    summarize,
)
from app.ml.splits import split_by_group
from app.schemas.core import UrmindClass

CONTEUDO = b"pacote de teste"
SHA256 = hashlib.sha256(CONTEUDO).hexdigest()


def _fonte(**kwargs) -> DatasetSource:
    base = {
        "id": "fonte_teste",
        "title": "Fonte de teste",
        "homepage": "https://example.invalid",
        "license": "CC BY 4.0",
        "role": DatasetRole.TRAINING_V1,
        "version": "v1",
        "adapter": None,
        "expected_files": (
            ExpectedFile("pacote.zip", size_bytes=len(CONTEUDO), sha256=SHA256),
        ),
        "taxonomy_note": "nota",
        "group_note": "grupo",
    }
    return DatasetSource(**{**base, **kwargs})


def _pronta(tmp_path):
    raiz = tmp_path / "fonte_teste"
    raiz.mkdir()
    (raiz / "pacote.zip").write_bytes(CONTEUDO)
    return inspect_source(_fonte(), tmp_path, verify=True)


def _imagem(group: str, indice: int, classe=UrmindClass.ROAD_D40) -> AnnotatedImage:
    return AnnotatedImage(
        dataset_id="fonte_teste",
        image_path=f"{group}/{indice}.jpg",
        width=100,
        height=100,
        group=group,
        boxes=(BoundingBox(classe, "D40", 1, 1, 10, 10),),
    )


# ------------------------------------------------------------------- summarize


def test_summarize_conta_aceitos_e_recusados_lado_a_lado():
    registros = [
        _imagem("a", 1),
        AnnotatedImage(
            dataset_id="fonte_teste",
            image_path="b/1.jpg",
            width=10,
            height=10,
            group="b",
            rejected=(RejectedLabel("D50", "tampa"),),
        ),
    ]

    resumo = summarize("fonte_teste", registros)

    assert resumo.total_samples == 2
    assert resumo.usable_samples == 1
    assert resumo.class_counts == {"URMIND_ROAD_D40": 1}
    assert resumo.rejected_counts == {"D50": 1}
    assert resumo.groups == ("a", "b")


def test_summarize_de_geo_record_nao_produz_classe_do_urmind():
    registros = [
        GeoRecord("camber", "1", 37.9, 23.7, "route:50", "camber_malfunction_0", None)
    ]

    resumo = summarize("camber", registros)

    assert resumo.class_counts == {}
    assert resumo.classes == []


# --------------------------------------------------------------------- recusas


def test_fonte_adiada_nao_pode_ser_registrada(tmp_path):
    inventory = _pronta(tmp_path)
    fonte = _fonte(role=DatasetRole.DEFERRED)

    with pytest.raises(RegistrationRefused, match="adiado"):
        build_dataset_version(fonte, inventory, summarize("fonte_teste", []))


def test_download_incompleto_nao_vira_dataset_version(tmp_path):
    raiz = tmp_path / "fonte_teste"
    raiz.mkdir()
    (raiz / "pacote.zip.part").write_bytes(CONTEUDO)
    inventory = inspect_source(_fonte(), tmp_path)

    with pytest.raises(RegistrationRefused, match="download incompleto"):
        build_dataset_version(_fonte(), inventory, summarize("fonte_teste", [_imagem("a", 1)]))


def test_sem_amostra_util_nao_registra(tmp_path):
    """Um dataset em que nada entrou na taxonomia V1 não tem o que versionar."""
    inventory = _pronta(tmp_path)

    with pytest.raises(RegistrationRefused, match="nenhuma amostra"):
        build_dataset_version(_fonte(), inventory, summarize("fonte_teste", []))


def test_treino_sem_split_e_recusado(tmp_path):
    inventory = _pronta(tmp_path)
    resumo = summarize("fonte_teste", [_imagem("a", 1)])

    with pytest.raises(RegistrationRefused, match="§8.4"):
        build_dataset_version(_fonte(), inventory, resumo, split=None)


def test_split_com_conjunto_vazio_e_recusado(tmp_path):
    """Um grupo só não produz train/validation/test — e isso invalida a etapa."""
    inventory = _pronta(tmp_path)
    registros = [_imagem("unico", i) for i in range(5)]
    split = split_by_group(registros, group_key=lambda r: r.group)

    with pytest.raises(RegistrationRefused, match="sem nenhum item"):
        build_dataset_version(_fonte(), inventory, summarize("fonte_teste", registros), split)


# --------------------------------------------------------------------- payload


def test_payload_tem_as_colunas_de_dataset_versions(tmp_path):
    inventory = _pronta(tmp_path)
    registros = [_imagem(f"g{g}", i) for g in range(6) for i in range(4)]
    split = split_by_group(registros, group_key=lambda r: r.group)

    payload = build_dataset_version(
        _fonte(), inventory, summarize("fonte_teste", registros), split
    )

    assert set(payload) == {
        "name",
        "version",
        "source",
        "license",
        "classes",
        "split",
        "dvc_revision",
    }
    assert payload["classes"] == ["URMIND_ROAD_D40"]
    assert payload["dvc_revision"] is None


def test_payload_guarda_os_grupos_de_cada_split(tmp_path):
    """§10.2: o recorte precisa ser reproduzível depois."""
    inventory = _pronta(tmp_path)
    registros = [_imagem(f"g{g}", i) for g in range(6) for i in range(4)]
    split = split_by_group(registros, group_key=lambda r: r.group)

    payload = build_dataset_version(
        _fonte(), inventory, summarize("fonte_teste", registros), split
    )
    gravado = payload["split"]["split"]["groups"]

    assert set(gravado) == {"train", "validation", "test"}
    todos = [g for grupos in gravado.values() for g in grupos]
    assert len(todos) == len(set(todos)) == 6  # nenhum grupo em dois splits


def test_payload_carrega_ressalvas_e_estado_do_disco(tmp_path):
    inventory = _pronta(tmp_path)
    registros = [_imagem(f"g{g}", i) for g in range(6) for i in range(4)]
    split = split_by_group(registros, group_key=lambda r: r.group)
    fonte = _fonte(caveats=("13,2 GB não vai para o Supabase Free",))

    payload = build_dataset_version(
        fonte, inventory, summarize("fonte_teste", registros), split
    )

    assert payload["split"]["caveats"] == ["13,2 GB não vai para o Supabase Free"]
    assert payload["split"]["inventory"]["checksums_verified"] is True
    assert payload["split"]["taxonomy_note"] == "nota"


def test_geo_reference_registra_sem_split(tmp_path):
    """CAMBER não gera conjunto de treino; não há o que separar."""
    inventory = _pronta(tmp_path)
    fonte = _fonte(role=DatasetRole.GEO_REFERENCE)
    registros = [
        GeoRecord("camber", "1", 37.9, 23.7, "route:50", "camber_malfunction_0", None)
    ]

    payload = build_dataset_version(
        fonte, inventory, summarize("camber", registros), split=None, require_split=False
    )

    assert payload["classes"] == []
    assert payload["split"]["role"] == "geo_reference"
    assert "split" not in payload["split"]


def test_geo_reference_sem_zip_oficial_ainda_registra(tmp_path):
    """Exportação sob demanda não tem arquivo publicado a esperar."""
    raiz = tmp_path / "fonte_teste"
    (raiz / "labels").mkdir(parents=True)
    (raiz / "labels" / "seattle.csv").write_text("lat,lng\n", encoding="utf-8")
    fonte = _fonte(role=DatasetRole.GEO_REFERENCE, expected_files=())
    inventory = inspect_source(fonte, tmp_path)

    payload = build_dataset_version(
        fonte, inventory, summarize("fonte_teste", []), require_split=False
    )

    assert payload["split"]["inventory"]["state"] == "declared_only"


def test_fonte_de_treino_nao_aceita_declared_only(tmp_path):
    """Para treinar, o pacote oficial precisa estar completo — não basta ter pasta."""
    raiz = tmp_path / "fonte_teste"
    (raiz / "algo").mkdir(parents=True)
    fonte = _fonte(expected_files=())
    inventory = inspect_source(fonte, tmp_path)

    with pytest.raises(RegistrationRefused, match="não está completo"):
        build_dataset_version(fonte, inventory, summarize("fonte_teste", [_imagem("a", 1)]))


def test_fontes_reais_do_catalogo_ainda_nao_registram(tmp_path):
    """Sem os arquivos em disco, toda fonte real é recusada — inclusive a de treino."""
    for dataset_id in ("rdd2022", "univali_br", "urban_community"):
        fonte = get_source(dataset_id)
        inventory = inspect_source(fonte, tmp_path)
        with pytest.raises(RegistrationRefused):
            build_dataset_version(fonte, inventory, summarize(dataset_id, []))
