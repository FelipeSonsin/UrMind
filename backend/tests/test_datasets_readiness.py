"""Perfil medido das fontes e detecção de divergência catálogo × disco.

O que estes testes protegem é a lição do relatório que envelheceu: enquanto o
estado das fontes era escrito à mão, ele podia contradizer o disco sem que nada
falhasse. Aqui a contradição é o resultado que se verifica.
"""

from __future__ import annotations

import pytest

from app.datasets.catalog import (
    SOURCES,
    DatasetRole,
    DatasetSource,
    DatasetUsage,
    ExpectedFile,
)
from app.datasets.readiness import profile_source, totals
from app.datasets.records import (
    AnnotatedImage,
    BoundingBox,
    GeoRecord,
    KeypointSample,
    MaskSample,
    RejectedLabel,
)
from app.schemas.core import UrmindClass


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


def _com_registros(monkeypatch, registros):
    """Instala um adaptador falso que devolve exatamente estes registros.

    O adaptador é de teste; os registros são objetos reais do domínio. Nenhuma
    anotação é inventada em cima de dado de fonte — o que se exercita aqui é a
    contagem, não a leitura.
    """
    from app.datasets import adapters, readiness

    monkeypatch.setitem(adapters.ADAPTERS, "fonte_teste", lambda _root: iter(registros))
    monkeypatch.setattr(readiness, "ADAPTERS", adapters.ADAPTERS, raising=False)


def _raiz(tmp_path, dataset_id="fonte_teste"):
    raiz = tmp_path / dataset_id
    raiz.mkdir(exist_ok=True)
    (raiz / "pacote.zip").write_bytes(b"conteudo")
    return tmp_path


def _imagem(group="a", *, boxes=(), rejected=()):
    return AnnotatedImage(
        dataset_id="fonte_teste",
        image_path=f"{group}/x.jpg",
        width=100,
        height=100,
        group=group,
        boxes=boxes,
        rejected=rejected,
    )


CAIXA = (BoundingBox(UrmindClass.ROAD_D40, "D40", 1, 1, 10, 10),)


# --------------------------------------------------------- caixa não é máscara


def test_caixa_e_mascara_sao_contadas_separadamente(tmp_path, monkeypatch):
    """Somar as duas faria uma fonte de segmentação passar por fonte de detecção."""
    _com_registros(
        monkeypatch,
        [
            _imagem(boxes=CAIXA),
            MaskSample(
                dataset_id="fonte_teste",
                image_path="b/y.jpg",
                group="b",
                masks={UrmindClass.ROAD_D40: "b/y_POTHOLE.png"},
            ),
        ],
    )

    perfil = profile_source(_fonte(), _raiz(tmp_path))

    assert perfil.box_annotations == 1
    assert perfil.mask_annotations == 1
    assert perfil.class_counts_boxes == {"URMIND_ROAD_D40": 1}
    assert perfil.class_counts_masks == {"URMIND_ROAD_D40": 1}


def test_fonte_so_de_mascara_nao_alimenta_o_detector(tmp_path, monkeypatch):
    """É o caso do UNIVALI: anotação humana real que o YOLOX da V1 não consome."""
    _com_registros(
        monkeypatch,
        [
            MaskSample(
                dataset_id="fonte_teste",
                image_path="a/y.jpg",
                group="a",
                masks={UrmindClass.ROAD_D40: "a/y_POTHOLE.png"},
                rejected=(RejectedLabel("CRACK", "trinca genérica"),),
            )
        ],
    )

    perfil = profile_source(
        _fonte(usage=(DatasetUsage.UNUSABLE,), unlock_requirement="converter máscara"),
        _raiz(tmp_path),
    )

    # Máscara ANEXADA não é máscara MEDIDA: o adaptador viu o arquivo existir e
    # não abriu o PNG. No UNIVALI a medição de pixel encontrou 1.671 máscaras
    # vazias, então contar a amostra como utilizável afirmaria conteúdo que
    # ninguém leu. Usabilidade desconhecida fica fora de `usable_samples` e é
    # reportada como divergência, não escondida.
    assert perfil.usable_samples == 0
    assert perfil.unmeasured_mask_samples == 1
    assert perfil.mask_annotations == 1
    assert not perfil.feeds_detector
    assert any("não medido" in d for d in perfil.divergences)


# ------------------------------------------------ negativa não é fora de escopo


def test_negativa_e_fora_de_escopo_nao_se_misturam(tmp_path, monkeypatch):
    """taxonomy.yaml: negativa é ausência de objeto; recusada é objeto fora do §8.2.

    Contar a segunda como negativa ensinaria o detector que aquele objeto não
    existe na imagem — quando ele existe e só não é classe da V1.
    """
    _com_registros(
        monkeypatch,
        [
            _imagem("neg"),
            _imagem("fora", rejected=(RejectedLabel("D50", "tampa de poço"),)),
            _imagem("ok", boxes=CAIXA),
        ],
    )

    perfil = profile_source(_fonte(), _raiz(tmp_path))

    assert perfil.negative_images == 1
    assert perfil.out_of_scope_images == 1
    assert perfil.usable_samples == 1


# ------------------------------------------------------ divergência é reportada


def test_declarar_train_sem_caixa_e_divergencia(tmp_path, monkeypatch):
    _com_registros(monkeypatch, [_imagem(rejected=(RejectedLabel("D50", "tampa"),))])

    perfil = profile_source(_fonte(usage=(DatasetUsage.TRAIN,)), _raiz(tmp_path))

    assert perfil.divergences
    assert "nenhuma caixa" in perfil.divergences[0]


def test_declarar_unusable_com_caixa_e_divergencia(tmp_path, monkeypatch):
    _com_registros(monkeypatch, [_imagem(boxes=CAIXA)])

    perfil = profile_source(_fonte(usage=(DatasetUsage.UNUSABLE,)), _raiz(tmp_path))

    assert any("UNUSABLE" in d for d in perfil.divergences)


def test_declarar_geo_reference_sem_coordenada_e_divergencia(tmp_path, monkeypatch):
    _com_registros(monkeypatch, [_imagem(boxes=CAIXA)])

    perfil = profile_source(
        _fonte(role=DatasetRole.GEO_REFERENCE, usage=(DatasetUsage.GEO_REFERENCE,)),
        _raiz(tmp_path),
    )

    assert any("coordenada" in d for d in perfil.divergences)


def test_unusable_precisa_dizer_o_que_desbloqueia(tmp_path, monkeypatch):
    """Bloqueio de formato sem saída registrada vira dado esquecido."""
    _com_registros(
        monkeypatch,
        [
            MaskSample(
                dataset_id="fonte_teste",
                image_path="a/y.jpg",
                group="a",
                masks={UrmindClass.ROAD_D40: "a/y.png"},
            )
        ],
    )

    perfil = profile_source(
        _fonte(usage=(DatasetUsage.UNUSABLE,), unlock_requirement=""), _raiz(tmp_path)
    )

    assert any("unlock_requirement" in d for d in perfil.divergences)


# ------------------------------------------------------- keypoint e georreferência


def test_keypoint_nao_vira_anotacao_da_v1(tmp_path, monkeypatch):
    """Ponto não é caixa: inventar extensão em volta seria fabricar anotação."""
    _com_registros(
        monkeypatch,
        [
            KeypointSample(
                dataset_id="fonte_teste",
                image_ref="a.parquet#row=0",
                width=10,
                height=10,
                group="pano:1",
                keypoints=(),
                rejected=(RejectedLabel("curb_ramp", "acessibilidade"),),
            )
        ],
    )

    perfil = profile_source(
        _fonte(role=DatasetRole.GEO_REFERENCE, usage=(DatasetUsage.CONTEXT_ONLY,)),
        _raiz(tmp_path),
    )

    assert perfil.box_annotations == 0
    assert perfil.usable_samples == 0
    # Tinha anotação e ela não coube no §8.2: é fora de escopo, que é a própria
    # definição do campo. Contá-la como negativa diria que não havia nada ali.
    assert perfil.out_of_scope_images == 1
    assert perfil.negative_images == 0
    assert not perfil.feeds_detector


def test_georreferencia_conta_coordenada_e_nao_classe(tmp_path, monkeypatch):
    _com_registros(
        monkeypatch,
        [
            GeoRecord(
                dataset_id="fonte_teste",
                external_id="1",
                latitude=-23.5,
                longitude=-46.6,
                group="route:1",
                kind="camber_track_point",
                human_confirmed=None,
            )
        ],
    )

    perfil = profile_source(
        _fonte(role=DatasetRole.GEO_REFERENCE, usage=(DatasetUsage.GEO_REFERENCE,)),
        _raiz(tmp_path),
    )

    assert perfil.geo_records == 1
    assert perfil.class_counts_boxes == {}
    assert perfil.divergences == ()


# --------------------------------------------------------------------- agregado


def test_totais_separam_acervo_de_treinavel(tmp_path, monkeypatch):
    """GB armazenado e GB treinável são perguntas diferentes."""
    from app.datasets import adapters

    monkeypatch.setitem(
        adapters.ADAPTERS, "fonte_teste", lambda _r: iter([_imagem(boxes=CAIXA)])
    )
    monkeypatch.setitem(adapters.ADAPTERS, "so_contexto", lambda _r: iter([_imagem()]))

    treinavel = profile_source(_fonte(), _raiz(tmp_path))
    vazia = profile_source(
        _fonte(
            id="so_contexto",
            adapter="so_contexto",
            role=DatasetRole.GEO_REFERENCE,
            usage=(DatasetUsage.CONTEXT_ONLY,),
        ),
        _raiz(tmp_path, "so_contexto"),
    )

    agregado = totals((treinavel, vazia))

    assert agregado["trainable_sources"] == ["fonte_teste"]
    assert agregado["non_contributing_sources"] == ["so_contexto"]
    assert agregado["class_counts_boxes"] == {"URMIND_ROAD_D40": 1}


# --------------------------------------------------- contrato do catálogo real


def test_toda_fonte_do_catalogo_declara_uso_e_formato():
    """Sem uso declarado não há como conferir o catálogo contra o disco."""
    for source in SOURCES:
        assert source.usage, f"{source.id} sem `usage`"
        assert source.annotation_format, f"{source.id} sem `annotation_format`"
        assert source.usage_note, f"{source.id} sem `usage_note`"


def test_fonte_inutilizavel_registra_como_deixar_de_ser():
    for source in SOURCES:
        if DatasetUsage.UNUSABLE in source.usage:
            assert source.potential_usage, f"{source.id}: UNUSABLE sem potential_usage"
            assert source.unlock_requirement, f"{source.id}: UNUSABLE sem unlock_requirement"


def test_so_o_rdd2022_declara_alimentar_teste_do_detector():
    """Guarda contra promover a teste uma fonte cujo agrupamento não separa cena.

    Se outra fonte passar a declarar TEST/VALIDATION, este teste falha e obriga
    a revisar o §8.4 para ela — que é exatamente a conversa que precisa
    acontecer antes, e não depois, de a métrica ser publicada.
    """
    avaliadas = {
        s.id
        for s in SOURCES
        if {DatasetUsage.TEST, DatasetUsage.VALIDATION} & set(s.usage)
    }
    assert avaliadas == {"rdd2022"}


@pytest.mark.parametrize("source", SOURCES, ids=[s.id for s in SOURCES])
def test_uso_declarado_e_coerente_com_o_papel(source):
    """`GEO_REFERENCE` no papel nunca pode declarar uso de treino do detector."""
    if source.role is DatasetRole.GEO_REFERENCE:
        assert not source.feeds_training, (
            f"{source.id}: papel geo_reference não pode alimentar treino/teste"
        )


# ------------------- a causa relatada precisa ser a causa real


def test_scan_bem_sucedido_se_declara_sucesso(tmp_path, monkeypatch):
    _com_registros(monkeypatch, [_imagem(boxes=CAIXA)])

    perfil = profile_source(_fonte(), _raiz(tmp_path))

    assert perfil.scan_outcome == "SUCCESS"
    assert perfil.scan_performed
    assert perfil.scan_skipped_reason is None


def test_erro_do_adaptador_nao_e_relatado_como_cloud_only(tmp_path, monkeypatch):
    """Era o defeito: qualquer falha virava "o OneDrive bloqueou", com nuvem=0.

    Quem fosse corrigir procuraria hidratação e não encontraria nada, porque o
    problema real era outro — manifesto ausente, cadeia quebrada, leitura falha.
    """
    from app.datasets import adapters, readiness

    def quebrado(_root):
        raise adapters.AdapterError("manifesto autorizado ausente")
        yield  # pragma: no cover

    monkeypatch.setitem(adapters.ADAPTERS, "fonte_teste", quebrado)
    monkeypatch.setattr(readiness, "ADAPTERS", adapters.ADAPTERS, raising=False)

    perfil = profile_source(_fonte(), _raiz(tmp_path))

    assert perfil.cloud_only_files == 0
    assert perfil.scan_outcome == "ADAPTER_ERROR"
    assert "manifesto autorizado ausente" in perfil.read_error
    assert "OneDrive" not in (perfil.scan_skipped_reason or "")
    assert not any("cloud-only" in d for d in perfil.divergences)


def test_erro_de_leitura_tem_causa_propria(tmp_path, monkeypatch):
    from app.datasets import adapters, readiness

    def quebrado(_root):
        raise OSError("disco recusou a leitura")
        yield  # pragma: no cover

    monkeypatch.setitem(adapters.ADAPTERS, "fonte_teste", quebrado)
    monkeypatch.setattr(readiness, "ADAPTERS", adapters.ADAPTERS, raising=False)

    perfil = profile_source(_fonte(), _raiz(tmp_path))

    assert perfil.scan_outcome == "READ_ERROR"
    assert "disco recusou" in perfil.read_error


def test_cloud_only_continua_bloqueando_a_varredura(tmp_path, monkeypatch):
    """A proteção do §4.4 não pode ter sido perdida na separação das causas."""
    from app.datasets import readiness

    _com_registros(monkeypatch, [_imagem(boxes=CAIXA)])
    monkeypatch.setattr(
        readiness, "_count_files", lambda root: (10, 3, 1_000_000), raising=False
    )

    perfil = profile_source(_fonte(), _raiz(tmp_path))

    assert perfil.scan_outcome == "CLOUD_ONLY_SKIP"
    assert not perfil.scan_performed
    assert "OneDrive" in perfil.scan_skipped_reason
    assert perfil.box_annotations == 0
