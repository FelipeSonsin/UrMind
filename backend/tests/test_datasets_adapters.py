"""Leitura de cada fonte para o formato canônico (§8.2, §8.3 passo 2, §8.4).

As fixtures reproduzem o layout real de cada pacote em miniatura. Nenhum teste
depende dos arquivos baixados: o conjunto de testes precisa rodar em máquina que
nunca viu 13 GB de RDD2022.
"""

from __future__ import annotations

import pytest

from app.datasets.adapters import (
    AdapterError,
    extract_image,
    read_bdd100k,
    read_camber_detections,
    read_camber_route,
    read_global_streetscapes,
    read_project_sidewalk,
    read_rampnet,
    read_rdd2022,
    read_univali_br,
    read_urban_community,
    univali_group,
)
from app.schemas.core import UrmindClass

# --------------------------------------------------------------------- RDD2022

VOC = """<annotation>
  <filename>{filename}</filename>
  <size><width>600</width><height>600</height><depth>3</depth></size>
{objects}
</annotation>
"""

OBJ = """  <object>
    <name>{name}</name>
    <bndbox><xmin>{x0}</xmin><ymin>{y0}</ymin><xmax>{x1}</xmax><ymax>{y1}</ymax></bndbox>
  </object>
"""


@pytest.fixture
def rdd_root(tmp_path):
    raiz = tmp_path / "rdd2022"
    for pais, rotulos in (("Japan", ["D00", "D40"]), ("Czech", ["D20", "D50"])):
        xmls = raiz / "RDD2022" / pais / "train" / "annotations" / "xmls"
        imagens = raiz / "RDD2022" / pais / "train" / "images"
        xmls.mkdir(parents=True)
        imagens.mkdir(parents=True)
        nome = f"{pais}_000001.jpg"
        (imagens / nome).write_bytes(b"jpeg falso")
        objetos = "".join(
            OBJ.format(name=r, x0=10 + i, y0=20, x1=100 + i, y1=140)
            for i, r in enumerate(rotulos)
        )
        (xmls / f"{pais}_000001.xml").write_text(
            VOC.format(filename=nome, objects=objetos), encoding="utf-8"
        )
        # test/ existe e não tem anotação — não pode virar amostra
        (raiz / "RDD2022" / pais / "test" / "images").mkdir(parents=True)
        (raiz / "RDD2022" / pais / "test" / "images" / f"{pais}_900001.jpg").write_bytes(b"x")
    return raiz


def test_rdd_traduz_as_quatro_classes_e_recusa_o_resto(rdd_root):
    amostras = {a.group: a for a in read_rdd2022(rdd_root)}

    japao = amostras["Japan"]
    assert [b.urmind_class for b in japao.boxes] == [
        UrmindClass.ROAD_D00,
        UrmindClass.ROAD_D40,
    ]

    czech = amostras["Czech"]
    assert [b.urmind_class for b in czech.boxes] == [UrmindClass.ROAD_D20]
    assert [r.source_label for r in czech.rejected] == ["D50"]
    assert "protocolo" in czech.rejected[0].reason


def test_rdd_nao_produz_amostra_do_split_de_teste(rdd_root):
    """test/ é submissão do desafio e não tem rótulo (§8.3)."""
    assert all(a.official_split == "train" for a in read_rdd2022(rdd_root))
    assert len(list(read_rdd2022(rdd_root))) == 2


def test_rdd_agrupa_por_pais(rdd_root):
    assert {a.group for a in read_rdd2022(rdd_root)} == {"Japan", "Czech"}


def test_rdd_caminho_da_imagem_e_relativo(rdd_root):
    for amostra in read_rdd2022(rdd_root):
        assert not amostra.image_path.startswith("/")
        assert amostra.image_path.startswith("RDD2022/")


def test_rdd_anotacao_sem_imagem_nao_vira_amostra(rdd_root):
    for imagem in (rdd_root / "RDD2022" / "Japan" / "train" / "images").iterdir():
        imagem.unlink()

    assert {a.group for a in read_rdd2022(rdd_root)} == {"Czech"}


def test_rdd_pasta_errada_falha_com_mensagem_util(tmp_path):
    with pytest.raises(AdapterError, match="extraia o pacote oficial"):
        list(read_rdd2022(tmp_path))


# ------------------------------------------------------------- Urban Community


@pytest.fixture
def urban_root(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    raiz = tmp_path / "urban_community"
    base = raiz / "Data_sets" / "Data_sets"
    for classe, class_id in (("pothole", 3), ("open_manhole", 5), ("cracks", 4)):
        (base / classe / "images").mkdir(parents=True)
        (base / classe / "labels").mkdir(parents=True)
        Image.new("RGB", (200, 100)).save(base / classe / "images" / "1.jpg")
        (base / classe / "labels" / "1.txt").write_text(
            f"{class_id} 0.5 0.5 0.2 0.4\n", encoding="utf-8"
        )
    return raiz


def test_urban_converte_yolo_normalizado_para_pixels(urban_root):
    amostras = {a.group: a for a in read_urban_community(urban_root)}
    caixa = amostras["pothole"].boxes[0]

    assert caixa.urmind_class is UrmindClass.ROAD_D40
    assert (caixa.xmin, caixa.xmax) == (80.0, 120.0)  # 200 * (0,5 ∓ 0,1)
    assert (caixa.ymin, caixa.ymax) == (30.0, 70.0)  # 100 * (0,5 ∓ 0,2)


def test_open_manhole_nao_vira_urmind_manhole(urban_root):
    """§8.2: bueiro só entra com dataset e protocolo próprios."""
    amostras = {a.group: a for a in read_urban_community(urban_root)}
    manhole = amostras["open_manhole"]

    assert manhole.boxes == ()
    assert not manhole.usable
    assert manhole.rejected[0].source_label == "open_manhole"
    assert "protocolo" in manhole.rejected[0].reason


def test_trinca_generica_do_kaggle_e_recusada(urban_root):
    amostras = {a.group: a for a in read_urban_community(urban_root)}

    assert amostras["cracks"].boxes == ()
    assert "D00/D10/D20" in amostras["cracks"].rejected[0].reason


# -------------------------------------------------------------- UNIVALI / DNIT


@pytest.fixture
def univali_root(tmp_path):
    raiz = tmp_path / "univali_br"
    pasta = raiz / "v1" / "1007599_RS_386_386RS289112_28920"
    pasta.mkdir(parents=True)
    prefixo = "1007599_RS_386_386RS289112_28920"
    (pasta / f"{prefixo}_RAW.jpg").write_bytes(b"jpeg falso")
    for tipo in ("CRACK", "LANE", "POTHOLE"):
        (pasta / f"{prefixo}_{tipo}.png").write_bytes(b"png falso")
    return raiz


def test_univali_aceita_pothole_e_recusa_trinca_generica(univali_root):
    amostra = next(iter(read_univali_br(univali_root)))

    assert list(amostra.masks) == [UrmindClass.ROAD_D40]
    assert {r.source_label for r in amostra.rejected} == {"CRACK", "LANE"}
    assert amostra.usable


def test_univali_agrupa_por_trecho_de_rodovia(univali_root):
    """§8.4: fotos do mesmo trecho não podem cair em splits diferentes."""
    amostra = next(iter(read_univali_br(univali_root)))

    assert amostra.group == "RS_386_386RS289112"


@pytest.mark.parametrize(
    ("pasta", "esperado"),
    [
        ("1007599_RS_386_386RS289112_28920", "RS_386_386RS289112"),
        ("994588_RS_386_386RS191729_09705", "RS_386_386RS191729"),
        ("semunderline", "semunderline"),
    ],
)
def test_extracao_do_grupo_do_univali(pasta, esperado):
    assert univali_group(pasta) == esperado


def test_univali_nao_converte_mascara_em_caixa(univali_root):
    """A conversão é decisão registrada à parte, não efeito da leitura."""
    amostra = next(iter(read_univali_br(univali_root)))

    assert not hasattr(amostra, "boxes")
    assert amostra.masks[UrmindClass.ROAD_D40].endswith("_POTHOLE.png")


# ---------------------------------------------------------------------- CAMBER

CSV_CAMBER = (
    "detection_id,timecode_seconds,frame,latitude,longitude,malfunction_type,"
    "user_confirmed,field_confirmed,field_solved,created_at\n"
    "289,1.4,42,37.9774,23.7712,0,,,,2026-07-14T16:49:52+00:00\n"
    "290,7.3,220,37.9775,23.7712,1,true,,,2026-07-14T16:49:52+00:00\n"
)

GPX = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><trkseg>
    <trkpt lat="37.9774" lon="23.7712"><time>2026-05-15T16:13:04Z</time></trkpt>
    <trkpt lat="37.9775" lon="23.7713"><time>2026-05-15T16:13:06Z</time></trkpt>
  </trkseg></trk>
</gpx>
"""


@pytest.fixture
def camber_root(tmp_path):
    raiz = tmp_path / "camber"
    (raiz / "detections").mkdir(parents=True)
    (raiz / "routes").mkdir(parents=True)
    (raiz / "detections" / "detections_50.csv").write_text(CSV_CAMBER, encoding="utf-8")
    (raiz / "routes" / "ride_1778861898555.gpx").write_text(GPX, encoding="utf-8")
    return raiz


def test_deteccao_sem_confirmacao_humana_fica_none(camber_root):
    """Coluna vazia é 'ninguém respondeu', não 'não confirmado' (§8.3)."""
    registros = list(read_camber_detections(camber_root))

    assert registros[0].human_confirmed is None
    assert registros[1].human_confirmed is True


def test_deteccao_do_camber_nao_recebe_classe_do_urmind(camber_root):
    """Código de detector de outro projeto não é traduzido sem protocolo (§8.2)."""
    tipos = {r.kind for r in read_camber_detections(camber_root)}

    assert tipos == {"camber_malfunction_0", "camber_malfunction_1"}
    assert not any(t.startswith("URMIND_") for t in tipos)


def test_gpx_vira_pontos_de_trajeto_agrupados_por_rota(camber_root):
    pontos = list(read_camber_route(camber_root))

    assert len(pontos) == 2
    assert {p.group for p in pontos} == {"route:ride_1778861898555"}
    assert all(p.kind == "camber_track_point" for p in pontos)
    assert all(p.human_confirmed is None for p in pontos)


def test_camber_sem_pasta_esperada_falha(tmp_path):
    with pytest.raises(AdapterError, match="detections"):
        list(read_camber_detections(tmp_path))


# ---------------------------------- Project Sidewalk / RampNet (Parquet oficial)


def _parquet(path, rows, schema):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)


def _jpeg_bytes():
    """JPEG mínimo de verdade, para provar que `extract_image` devolve imagem."""
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _sidewalk_fixture(tmp_path):
    pa = pytest.importorskip("pyarrow")
    raiz = tmp_path / "project_sidewalk"
    schema = pa.schema(
        [
            ("crop_id", pa.string()),
            ("crop_uid", pa.string()),
            ("image", pa.struct([("bytes", pa.binary()), ("path", pa.string())])),
            ("keypoints", pa.list_(pa.struct([("x", pa.int32()), ("y", pa.int32())]))),
            ("n_keypoints", pa.int32()),
            ("width", pa.int32()),
            ("height", pa.int32()),
            ("sha256", pa.string()),
        ]
    )
    rows = [
        {
            "crop_id": "pano_a_-_10_20",
            "crop_uid": "pano_a",
            "image": {"bytes": _jpeg_bytes(), "path": "a.jpg"},
            "keypoints": [{"x": 10, "y": 20}, {"x": 30, "y": 40}],
            "n_keypoints": 2,
            "width": 683,
            "height": 2048,
            "sha256": "abc",
        },
        {
            "crop_id": "pano_a_-_50_60",
            "crop_uid": "pano_a",
            "image": {"bytes": _jpeg_bytes(), "path": "b.jpg"},
            "keypoints": [],
            "n_keypoints": 0,
            "width": 683,
            "height": 2048,
            "sha256": "def",
        },
    ]
    _parquet(raiz / "acquired_20260908" / "data" / "val" / "val-00000.parquet", rows, schema)
    return raiz


def test_project_sidewalk_le_keypoints_do_parquet(tmp_path):
    amostras = list(read_project_sidewalk(_sidewalk_fixture(tmp_path)))

    assert len(amostras) == 2
    primeira = amostras[0]
    assert primeira.group == "pano:pano_a"  # grupo é a panorâmica, não o recorte
    assert primeira.official_split == "validation"
    assert [(k.x, k.y) for k in primeira.keypoints] == [(10.0, 20.0), (30.0, 40.0)]
    assert primeira.width == 683
    assert primeira.image_ref.endswith("val-00000.parquet#row=0")


def test_project_sidewalk_recusa_keypoint_para_treino_v1(tmp_path):
    """Rampa é dado real, mas não é classe do §8.2: a recusa fica registrada."""
    primeira, segunda = list(read_project_sidewalk(_sidewalk_fixture(tmp_path)))

    assert primeira.usable is True
    assert [r.source_label for r in primeira.rejected] == ["curb_ramp"]
    assert "§8.2" in primeira.rejected[0].reason
    assert segunda.usable is False and segunda.rejected == ()


def test_extract_image_devolve_o_jpeg_da_linha_citada(tmp_path):
    raiz = _sidewalk_fixture(tmp_path)
    amostra = next(iter(read_project_sidewalk(raiz)))

    dados = extract_image(raiz, amostra.image_ref)

    assert dados.startswith(b"\xff\xd8\xff")  # a imagem sai do Parquet, não é inventada


def test_project_sidewalk_sem_recorte_baixado_diz_o_que_fazer(tmp_path):
    raiz = tmp_path / "project_sidewalk"
    (raiz / "acquired_20260908").mkdir(parents=True)

    with pytest.raises(AdapterError, match="acquire_registered"):
        list(read_project_sidewalk(raiz))


def test_rampnet_separa_ponto_na_imagem_de_coordenada_no_mundo(tmp_path):
    pa = pytest.importorskip("pyarrow")
    raiz = tmp_path / "rampnet"
    schema = pa.schema(
        [
            ("image", pa.struct([("bytes", pa.binary()), ("path", pa.string())])),
            ("pano_id", pa.string()),
            ("record_creation_time", pa.int64()),
            ("curb_ramp_points_normalized", pa.list_(pa.list_(pa.float32()))),
            ("pano_coord", pa.list_(pa.float64(), 2)),
            ("curb_ramp_coords", pa.list_(pa.list_(pa.float64()))),
            ("pano_azimuth", pa.float64()),
        ]
    )
    rows = [
        {
            "image": {"bytes": _jpeg_bytes(), "path": "p.jpg"},
            "pano_id": "PANO1",
            "record_creation_time": 1749975494,
            "curb_ramp_points_normalized": [[0.5, 0.6]],
            "pano_coord": [40.7, -73.8],
            "curb_ramp_coords": [[40.71, -73.79], [40.72, -73.78]],
            "pano_azimuth": -66.1,
        }
    ]
    _parquet(raiz / "acquired_20260908" / "train" / "data-00000.parquet", rows, schema)

    registros = list(read_rampnet(raiz))
    pontos = [r for r in registros if type(r).__name__ == "KeypointSample"]
    geo = [r for r in registros if type(r).__name__ == "GeoRecord"]

    assert len(pontos) == 1 and len(geo) == 2
    assert pontos[0].attributes["coordinates_are_normalized"] is True
    assert pontos[0].width == 0  # a fonte não publica dimensão; não se inventa
    assert {g.group for g in geo} == {"pano:PANO1"}
    assert geo[0].latitude == 40.71 and geo[0].human_confirmed is True
    assert geo[0].attributes["pano_latitude"] == 40.7


# -------------------------------------------------------------------- BDD100K


def test_bdd100k_pareia_imagem_e_mascara_sem_extrair(tmp_path):
    import zipfile

    raiz = tmp_path / "bdd100k"
    pasta = raiz / "acquired_20260908"
    pasta.mkdir(parents=True)
    with zipfile.ZipFile(pasta / "bdd100k_images_10k.zip", "w") as zf:
        zf.writestr("10k/train/abc.jpg", _jpeg_bytes())
        zf.writestr("10k/test/zzz.jpg", _jpeg_bytes())
    with zipfile.ZipFile(pasta / "bdd100k_seg_maps.zip", "w") as zf:
        zf.writestr("color_labels/train/abc_train_color.png", b"\x89PNG")

    amostras = {a.image_path.split("#")[1]: a for a in read_bdd100k(raiz)}

    assert set(amostras) == {"10k/train/abc.jpg", "10k/test/zzz.jpg"}
    com_mascara = amostras["10k/train/abc.jpg"]
    assert com_mascara.group == "split:train"
    assert com_mascara.masks == {}  # nenhuma classe do BDD100K é classe da V1
    assert [r.source_label for r in com_mascara.rejected] == ["bdd100k_semantic_segmentation"]
    assert amostras["10k/test/zzz.jpg"].rejected == ()  # o split test não tem máscara


def test_bdd100k_sem_pacote_oficial_falha(tmp_path):
    raiz = tmp_path / "bdd100k"
    (raiz / "acquired_20260908").mkdir(parents=True)

    with pytest.raises(AdapterError, match="pacote oficial ausente"):
        list(read_bdd100k(raiz))


# ------------------------------------------------------- Global Streetscapes


GS_HEADER = (
    "uuid,source,orig_id,city,country,continent,lat,lon,datetime_local,sequence_id,"
    "sequence_index,split,img_path,glare,lighting_condition,pano_status,platform,"
    "quality,reflection,view_direction,weather,rel_path,sha256,size_bytes\n"
)


def _gs_fixture(tmp_path, *, com_imagem=True):
    raiz = tmp_path / "global_streetscapes"
    (raiz / "labels").mkdir(parents=True)
    if com_imagem:
        destino = raiz / "acquired_20260908" / "img" / "1"
        destino.mkdir(parents=True)
        (destino / "u1.jpeg").write_bytes(b"\xff\xd8\xff jpeg")
    (raiz / "labels" / "global_streetscapes_selected.csv").write_text(
        GS_HEADER
        + (
            "u1,Mapillary,999,Curitiba,Brazil,South America,-25.43,-49.27,"
            "2019-03-01 10:00:00+00:00,seq-a,4,train,img/1/u1.jpeg,no,day,False,"
            "walking surface,good,no,front/back,rainy,"
            "acquired_20260908/img/1/u1.jpeg,deadbeef,1234\n"
        ),
        encoding="utf-8",
    )
    return raiz


def test_global_streetscapes_le_contexto_urbano_com_coordenada(tmp_path):
    registro = next(iter(read_global_streetscapes(_gs_fixture(tmp_path))))

    assert registro.dataset_id == "global_streetscapes"
    assert (registro.latitude, registro.longitude) == (-25.43, -49.27)
    assert registro.group == "sequence:seq-a"  # sequência de captura, não a foto
    assert registro.kind == "walking surface"
    assert registro.human_confirmed is True
    assert registro.attributes["weather"] == "rainy"
    assert registro.attributes["upstream_source"] == "Mapillary"
    assert registro.attributes["country"] == "Brazil"


def test_global_streetscapes_ignora_linha_sem_imagem_em_disco(tmp_path):
    """O CSV descreve o recorte; linha sem arquivo não vira registro fantasma."""
    raiz = _gs_fixture(tmp_path, com_imagem=False)

    assert list(read_global_streetscapes(raiz)) == []


def test_global_streetscapes_sem_recorte_diz_o_que_rodar(tmp_path):
    raiz = tmp_path / "global_streetscapes"
    raiz.mkdir()

    with pytest.raises(AdapterError, match="acquire_global_streetscapes"):
        list(read_global_streetscapes(raiz))
