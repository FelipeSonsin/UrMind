"""O readiness não pode hidratar marcador do OneDrive (§4.4).

Contar arquivo consulta o atributo do sistema de arquivos e não baixa nada;
**abrir** o arquivo baixa. Entre uma coisa e outra estava o adaptador: ele era
chamado mesmo quando a fonte tinha marcador, e um `refresh_readiness` numa fonte
como o RDD2022 poderia puxar vários GB sem ninguém ter pedido.

O que estes testes fixam é o comportamento fail-safe: marcador presente ⇒ a
varredura não acontece, e o relatório diz que não aconteceu em vez de publicar
zero anotações como se fosse medição.
"""

from __future__ import annotations

import pytest

from app.datasets import readiness
from app.datasets.catalog import (
    DatasetRole,
    DatasetSource,
    DatasetUsage,
    ExpectedFile,
)
from app.datasets.readiness import profile_source, totals
from app.datasets.records import AnnotatedImage, BoundingBox
from app.schemas.core import UrmindClass

CAIXA = (BoundingBox(UrmindClass.ROAD_D40, "D40", 1, 1, 10, 10),)


def _fonte(**kwargs) -> DatasetSource:
    base = {
        "id": "fonte_teste",
        "title": "Fonte de teste",
        "homepage": "https://example.invalid",
        "license": "CC BY 4.0",
        "role": DatasetRole.TRAINING_V1,
        "version": "v1",
        "adapter": "fonte_teste",
        "expected_files": (ExpectedFile("pacote.zip"),),
        "taxonomy_note": "nota",
        "group_note": "grupo",
        "usage": (DatasetUsage.TRAIN,),
    }
    return DatasetSource(**{**base, **kwargs})


def _raiz(tmp_path, dataset_id="fonte_teste"):
    raiz = tmp_path / dataset_id
    raiz.mkdir(exist_ok=True)
    (raiz / "pacote.zip").write_bytes(b"conteudo")
    return tmp_path


@pytest.fixture
def adaptador_espiao(monkeypatch):
    """Registra se o adaptador chegou a ser percorrido, e falha se for aberto.

    Não basta contar chamadas: o teste precisa provar que nenhuma leitura de
    arquivo aconteceu. Este adaptador levanta se alguém o percorrer.
    """
    chamadas: list[str] = []

    def adaptador(_root):
        chamadas.append("scan")
        yield AnnotatedImage(
            dataset_id="fonte_teste",
            image_path="a/x.jpg",
            width=100,
            height=100,
            group="a",
            boxes=CAIXA,
        )

    from app.datasets import adapters

    monkeypatch.setitem(adapters.ADAPTERS, "fonte_teste", adaptador)
    monkeypatch.setattr(readiness, "ADAPTERS", adapters.ADAPTERS, raising=False)
    return chamadas


def _com_marcadores(monkeypatch, *, total=10, nuvem=4, bytes_nuvem=4096):
    """Simula a contagem de arquivos com marcadores do OneDrive.

    O atributo real (`st_file_attributes`) só existe no Windows e não pode ser
    fabricado em disco pelo teste; o que importa aqui é o efeito da contagem
    sobre a decisão de varrer, então a contagem é o ponto de substituição.
    """
    monkeypatch.setattr(
        readiness, "_count_files", lambda _root: (total, nuvem, bytes_nuvem)
    )


# ------------------------------------------------------- varredura bloqueada


def test_nao_varre_fonte_com_arquivo_cloud_only(tmp_path, monkeypatch, adaptador_espiao):
    _com_marcadores(monkeypatch)

    perfil = profile_source(_fonte(), _raiz(tmp_path))

    assert adaptador_espiao == [], "o adaptador foi percorrido e teria hidratado o OneDrive"
    assert perfil.scan_performed is False
    assert perfil.scan_skipped_reason is not None
    assert "cloud-only" in perfil.scan_skipped_reason


def test_varre_normalmente_quando_tudo_esta_local(tmp_path, monkeypatch, adaptador_espiao):
    monkeypatch.setattr(readiness, "_count_files", lambda _root: (10, 0, 0))

    perfil = profile_source(_fonte(), _raiz(tmp_path))

    assert adaptador_espiao == ["scan"]
    assert perfil.scan_performed is True
    assert perfil.scan_skipped_reason is None
    assert perfil.box_annotations == 1


def test_um_unico_marcador_ja_bloqueia(tmp_path, monkeypatch, adaptador_espiao):
    """Fail-safe: a fonte não vira download parcial por causa de um arquivo."""
    _com_marcadores(monkeypatch, total=5000, nuvem=1, bytes_nuvem=10)

    perfil = profile_source(_fonte(), _raiz(tmp_path))

    assert adaptador_espiao == []
    assert perfil.scan_performed is False


# ----------------------------------------------------- o relatório não mente


def test_relatorio_separa_local_de_nuvem_e_do_que_foi_varrido(
    tmp_path, monkeypatch, adaptador_espiao
):
    _com_marcadores(monkeypatch, total=10, nuvem=4)

    dados = profile_source(_fonte(), _raiz(tmp_path)).as_dict()

    assert dados["file_count"] == 10
    assert dados["local_files"] == 6
    assert dados["cloud_only_files"] == 4
    assert dados["locally_available"] is False
    assert dados["files_scanned"] == 0
    assert dados["files_skipped_to_avoid_hydration"] == 10
    assert dados["scan_performed"] is False


def test_zero_anotacoes_sem_varredura_nao_vira_divergencia_de_conteudo(
    tmp_path, monkeypatch, adaptador_espiao
):
    """Acusar a fonte de não ter caixa sem ter olhado seria inventar resultado."""
    _com_marcadores(monkeypatch)

    perfil = profile_source(_fonte(usage=(DatasetUsage.TRAIN,)), _raiz(tmp_path))

    assert perfil.box_annotations == 0
    assert not any("não encontrou nenhuma caixa" in d for d in perfil.divergences)
    assert any("não medida" in d for d in perfil.divergences)


def test_totais_listam_a_fonte_como_nao_medida(tmp_path, monkeypatch, adaptador_espiao):
    _com_marcadores(monkeypatch)

    agregado = totals((profile_source(_fonte(), _raiz(tmp_path)),))

    assert agregado["unmeasured_sources"] == ["fonte_teste"]
