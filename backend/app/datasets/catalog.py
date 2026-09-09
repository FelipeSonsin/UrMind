"""Catálogo declarativo das fontes de dataset (MASTER_PLAN §8.3 passo 1, §10).

O §8.3 abre dizendo que *datasets aprovados são registrados em
`dataset_versions`*. Antes de registrar é preciso saber o que cada fonte é, o
que ela permite e o que ela **não** resolve. Esta tabela é esse contrato, em
código, para que a resposta não dependa de alguém lembrar o que leu no site.

Três papéis, e a diferença entre eles importa:

`TRAINING_V1` — a fonte tem imagem e rótulo que o §8.2 aceita hoje.
`GEO_REFERENCE` — a fonte tem geometria e ocorrência, não anotação de treino.
  Serve para contexto e para exercitar consulta espacial; não vira `Capture` nem
  `Detection` reais no banco, porque não foi observação do sistema.
`DEFERRED` — a fonte está no escopo futuro. Fica declarada, sem adaptador, para
  que ninguém a trate como pronta só porque a pasta existe.

Os campos `size_bytes`/`md5`/`sha256` são o que a fonte oficial publica.
Comparar com o disco é trabalho de `inventory.py`; aqui só se declara o esperado.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "SOURCES",
    "DatasetRole",
    "DatasetSource",
    "ExpectedFile",
    "get_source",
    "sources_by_role",
]


class DatasetRole(StrEnum):
    TRAINING_V1 = "training_v1"
    GEO_REFERENCE = "geo_reference"
    DEFERRED = "deferred"


@dataclass(frozen=True)
class ExpectedFile:
    """Um arquivo que a fonte oficial publica, com o que ela declara sobre ele."""

    relative_path: str
    size_bytes: int | None = None
    md5: str | None = None
    sha256: str | None = None
    required: bool = True


@dataclass(frozen=True)
class DatasetSource:
    id: str
    title: str
    homepage: str
    license: str
    role: DatasetRole
    version: str
    """Versão da fonte, não do nosso recorte. Vai para `dataset_versions.version`."""

    adapter: str | None
    """Chave em `app.datasets.adapters`. `None` quando a fonte ainda não é lida."""

    expected_files: tuple[ExpectedFile, ...] = ()
    taxonomy_note: str = ""
    """O que esta fonte pode e não pode virar, na taxonomia do §8.2."""

    group_note: str = ""
    """Como o split por grupo é derivado aqui (§8.4). Vazio quando não há split."""

    caveats: tuple[str, ...] = field(default_factory=tuple)

    @property
    def trainable(self) -> bool:
        return self.role is DatasetRole.TRAINING_V1


SOURCES: tuple[DatasetSource, ...] = (
    DatasetSource(
        id="rdd2022",
        title="RDD2022 — Road Damage Dataset (CRDDC 2022)",
        homepage=(
            "https://figshare.com/articles/dataset/RDD2022_-_The_multi-national_Road_"
            "Damage_Dataset_released_through_CRDDC_2022/21431547"
        ),
        license="CC BY 4.0",
        role=DatasetRole.TRAINING_V1,
        version="2022-crddc",
        adapter="rdd2022",
        expected_files=(
            # O ZIP mestre foi conferido pelo MD5 oficial e depois liberado, com
            # autorização, porque duplicava 13,2 GB do que já estava extraído
            # (reports/rdd_archive_release.json). Continua declarado como o que a
            # fonte publica, e `required=False` porque exigi-lo marcaria o
            # dataset como incompleto por uma ausência deliberada.
            ExpectedFile(
                "RDD2022_released_through_CRDDC2022.zip",
                size_bytes=13_264_172_619,
                md5="b62bd51d2ffcfaa76c60f234f0cc2bb3",
                required=False,
            ),
            ExpectedFile(
                "label_map.pbtxt",
                size_bytes=127,
                md5="1c2f8cabf4217798538989ebe203db2e",
                required=False,
            ),
            ExpectedFile(
                "Directory_Structure_CRDDC_RDD2022.txt",
                size_bytes=1161,
                md5="96dadd450b1122b9c798fac54207ea04",
                required=False,
            ),
            # A lista nominal dos 85.805 arquivos. Não é necessária para treinar,
            # mas é o único jeito de provar que a extração saiu inteira — o MD5
            # do ZIP garante o download, não o que saiu dele.
            ExpectedFile(
                "File_List_CRDDC_RDD2022.txt",
                size_bytes=3_250_534,
                md5="08993a6e2bca4a841c2f37b526e52d96",
                required=False,
            ),
        ),
        taxonomy_note=(
            "Fonte das quatro classes da V1 (§8.2). D00/D10/D20/D40 entram; "
            "D01, D11, D43, D44 e D50 são recusados com motivo em app.ml.taxonomy."
        ),
        group_note=(
            "Grupo = país de origem, e o split oficial train/test é preservado. "
            "O diretório test/ não traz anotação, então só train/ produz amostra "
            "rotulada; a validação sai de dentro de train/ (§8.3 passos 4-7)."
        ),
        caveats=(
            "13,2 GB. O §4.4 proíbe copiar o raw inteiro para o Supabase Free.",
            "Seis países; desempenho no Brasil não é herdado da métrica global.",
        ),
    ),
    DatasetSource(
        id="univali_br",
        title="Cracks and Potholes in Road Images (UNIVALI/DNIT)",
        homepage="https://data.mendeley.com/datasets/t576ydh9v8/4",
        license="conferir a licença na ficha Mendeley antes de publicar resultado",
        role=DatasetRole.TRAINING_V1,
        version="v4",
        adapter="univali_br",
        expected_files=(
            ExpectedFile(
                "Cracks-and-Potholes-in-Road-Images.zip",
                size_bytes=231_728_698,
                sha256="e59bb8d771e18f059ab78d88e640e83869fc8c8fd808697a0c2c48e16eec4f76",
            ),
        ),
        taxonomy_note=(
            "Rodovias federais brasileiras — o contexto mais próximo do uso real. "
            "Máscara POTHOLE vira URMIND_ROAD_D40; CRACK é trinca genérica e é "
            "recusada, porque o §8.2 proíbe escolher entre D00/D10/D20 por semelhança."
        ),
        group_note=(
            "Grupo = trecho de rodovia extraído do nome da pasta "
            "(1007599_RS_386_386RS289112_28920 → RS_386_386RS289112). Fotos do "
            "mesmo trecho são quase duplicadas e não podem se separar (§8.4)."
        ),
        caveats=(
            (
                "Anotação é máscara PNG, não caixa. Treinar detecção exige conversão "
                "explícita, registrada como versão derivada — não acontece na leitura."
            ),
        ),
    ),
    DatasetSource(
        id="urban_community",
        title="Urban Community Issues (Kaggle)",
        homepage="https://www.kaggle.com/datasets/rajeevpaudel1/urban-community-issues",
        license="CC0 conforme a ficha do Kaggle; a origem das imagens não é declarada",
        role=DatasetRole.TRAINING_V1,
        version="kaggle-2025",
        adapter="urban_community",
        expected_files=(ExpectedFile("urban-community-issues.zip"),),
        taxonomy_note=(
            "Sete pastas de classe; só `pothole` entra como URMIND_ROAD_D40. "
            "`open_manhole` NÃO vira URMIND_MANHOLE (§8.2) e `cracks` é genérica."
        ),
        group_note=(
            "Não há sessão, rota nem local no pacote. Grupo = pasta de classe, "
            "o que é fraco: imagens da mesma pasta podem vir da mesma cena sem "
            "que o dataset diga. Usar como reforço, não como conjunto de teste."
        ),
        caveats=(
            (
                "O pacote não traz `data.yaml`; o mapa id→nome foi inferido das pastas "
                "e conferido contra os ids dos .txt (app.ml.taxonomy)."
            ),
            (
                "Traz pesos .pt de um treino de terceiros. Não são modelo do UrMind e "
                "não podem ser promovidos (§9)."
            ),
            (
                "O Kaggle não publica checksum deste pacote: dá para conferir a "
                "integridade pelo CRC interno do ZIP, não contra a fonte."
            ),
        ),
    ),
    DatasetSource(
        id="project_sidewalk",
        title="Project Sidewalk — recorte RampNet round 1 (rampas de calçada)",
        homepage=(
            "https://huggingface.co/datasets/projectsidewalk/"
            "rampnet-crop-model-dataset-round1"
        ),
        license="MIT declarada na ficha oficial do dataset",
        role=DatasetRole.GEO_REFERENCE,
        version="521f74ff752d57824400c8f7d5ca4717efa7bf16",
        adapter="project_sidewalk",
        expected_files=(
            ExpectedFile(
                "acquired_20260908/data/test/test-00000.parquet",
                size_bytes=1_500_576_330,
                sha256="ca5231e729c059ad7ae0be8f998b921b67adda44bb95c7a61175ddae71e58c48",
            ),
            ExpectedFile(
                "acquired_20260908/data/train/train-00000.parquet",
                size_bytes=1_500_828_918,
                sha256="d49c283b17deab2d7a38d7da5cba94014f86318cc95626ef9acbdbbf50ddb4a2",
            ),
            ExpectedFile(
                "acquired_20260908/data/train/train-00003.parquet",
                size_bytes=1_500_555_082,
                sha256="3ad4f9609b0438e046a70cd3ac155038e8bb14106408b4eb2472e22137477ae1",
            ),
            ExpectedFile(
                "acquired_20260908/data/train/train-00006.parquet",
                size_bytes=356_172_777,
                sha256="692ff3cbb9070362c9f08818952ba2a313c7bcb625d1dde7d89a5aed7e722a13",
            ),
            ExpectedFile(
                "acquired_20260908/data/val/val-00001.parquet",
                size_bytes=506_305_947,
                sha256="bfd94df1d276106281fe547438966c8f90862313c2ea042832ea9cde6a91aae9",
            ),
        ),
        taxonomy_note=(
            "Recorte de panorâmica com rampa marcada como keypoint em pixels. "
            "Ponto não é caixa: sai como KeypointSample e é recusado para a V1, "
            "porque o §8.2 não tem classe de acessibilidade e inventar extensão "
            "em volta do ponto seria fabricar anotação."
        ),
        group_note=(
            "Grupo = `crop_uid`, a panorâmica de origem. Vários recortes saem da "
            "mesma panorâmica; agrupar pelo `crop_id` vazaria vizinhança (§8.4)."
        ),
        caveats=(
            (
                "Recorte oficial de 5 shards (5,36 GB) dos 13,4 GB publicados; "
                "cada arquivo confere com o SHA-256 da fonte."
            ),
            (
                "As imagens vêm do Google Street View: a licença MIT cobre o pacote, "
                "não redistribuição da imagem original."
            ),
            (
                "Os splits são os oficiais da fonte; não foram reavaliados contra "
                "vazamento entre si."
            ),
        ),
    ),
    DatasetSource(
        id="rampnet",
        title="RampNet — rampas de calçada em panorâmicas com coordenada real",
        homepage="https://huggingface.co/datasets/projectsidewalk/rampnet-dataset",
        license="MIT declarada na ficha oficial do dataset",
        role=DatasetRole.GEO_REFERENCE,
        version="ee882e3f3c779dc13182f307bca616e50d9b8c5c",
        adapter="rampnet",
        expected_files=(
            ExpectedFile(
                "acquired_20260908/train/data-00018-of-00128.parquet",
                size_bytes=2_520_183_371,
                sha256="a56c7121056f7b7bc005f75deaf3106650279e1d4b67ccd87993ed6b28212273",
            ),
            ExpectedFile(
                "acquired_20260908/train/data-00090-of-00128.parquet",
                size_bytes=2_511_807_273,
                sha256="d00f63948e01ef148c28a651f26bd83b9a845695d353d791c2a1155895b876fc",
            ),
            ExpectedFile(
                "acquired_20260908/val/data-00039-of-00128.parquet",
                size_bytes=718_473_690,
                sha256="f2fae6506382e0f5a45b27046af479c9ad25044eb63a8277f37b720832c4badd",
            ),
            ExpectedFile(
                "acquired_20260908/val/data-00122-of-00128.parquet",
                size_bytes=717_035_291,
                sha256="4620acc3860db82c0249be09a14f9a14653e6ce2de813d56a6bda53011c8674a",
            ),
            ExpectedFile(
                "acquired_20260908/test/data-00061-of-00128.parquet",
                size_bytes=373_869_607,
                sha256="d036dc1ea816dd4cd7ef91454278ee8b6dcbad31ecdcba70a356c34459867887",
            ),
        ),
        taxonomy_note=(
            "Duas anotações da mesma rampa: o ponto na panorâmica, normalizado, "
            "e a coordenada real. O ponto vira KeypointSample recusado para a V1; "
            "a coordenada vira GeoRecord e é o único dado desta fonte que exercita "
            "consulta espacial de verdade."
        ),
        group_note="Grupo = `pano_id`, a captura panorâmica (§8.4).",
        caveats=(
            (
                "Recorte oficial de 5 shards (6,84 GB) dos 462 GB publicados; "
                "cada arquivo confere com o SHA-256 da fonte."
            ),
            (
                "Coordenadas majoritariamente norte-americanas: exercitam PostGIS, "
                "não representam a área de operação do Scout."
            ),
            "GeoRecord não é observação do sistema e não vira Capture/Detection.",
        ),
    ),
    DatasetSource(
        id="camber",
        title="CAMBER — European Road Infrastructure Monitoring",
        homepage="https://zenodo.org/records/21361827",
        license="conferir por registro no Zenodo",
        role=DatasetRole.GEO_REFERENCE,
        version="zenodo-21361827",
        adapter="camber",
        taxonomy_note=(
            "As detecções do CSV vêm de um YOLO de terceiros e a coluna "
            "`user_confirmed` está vazia na amostra. Isso é saída de modelo, não "
            "ground truth: não vira rótulo de treino nem ocorrência real (§8.3)."
        ),
        group_note="Grupo = rota, que corresponde a uma sessão de coleta.",
        caveats=(
            "Um registro Zenodo é um vídeo/rota. A coleção são vários registros.",
            "Coordenadas em Atenas: exercitam PostGIS, não representam o mapa real.",
        ),
    ),
    DatasetSource(
        id="global_streetscapes",
        title="Global Streetscapes — imagem street-level aberta (substitui o MSLS)",
        homepage="https://huggingface.co/datasets/NUS-UAL/global-streetscapes",
        license="CC BY-SA 4.0 declarada na ficha oficial do dataset",
        role=DatasetRole.GEO_REFERENCE,
        version="manual_labels-2024",
        adapter="global_streetscapes",
        taxonomy_note=(
            "Contexto urbano street-level com rótulo humano de cena — clima, "
            "iluminação, plataforma da via, qualidade, reflexo, brilho e direção "
            "de vista. Nenhum desses é classe do §8.2: descrevem a condição da "
            "captura, não o dano. Entram como atributo de contexto, e o §8.2 "
            "continua sem classe de calçada."
        ),
        group_note=(
            "Grupo = `sequence_id`, a sequência de captura. Fotos consecutivas da "
            "mesma rua são a mesma sessão e não podem cair em lados opostos do "
            "split (§8.4)."
        ),
        caveats=(
            (
                "Substitui o Mapillary MSLS, cujo portal oficial exige login. "
                "58.147 das 60.146 imagens rotuladas vêm do próprio Mapillary e "
                "1.999 do KartaView, aqui redistribuídas sob CC BY-SA 4.0 — é a "
                "via pública e gratuita para a mesma imagem."
            ),
            (
                "Recorte de 3 dos 7 tarballs oficiais. Cada tarball é uma "
                "partição uniforme do mesmo corpus, então o recorte não é um "
                "viés geográfico: cobre os seis continentes."
            ),
            (
                "O Hugging Face não publica SHA-256 consultável destes tarballs. "
                "Cada JPEG gravado tem SHA-256 próprio no CSV de rótulos, o que "
                "prova integridade local e não autenticidade da origem."
            ),
            (
                "CC BY-SA 4.0 é compartilhamento pela mesma licença: derivar "
                "desta imagem obriga a atribuir e a manter a licença."
            ),
        ),
    ),
    DatasetSource(
        id="bdd100k",
        title="BDD100K — recorte 10K com mapas de segmentação",
        homepage="https://doc.bdd100k.com/download.html",
        license="BDD100K license; conferir antes de publicar resultado",
        role=DatasetRole.GEO_REFERENCE,
        version="v1-10k",
        adapter="bdd100k",
        expected_files=(
            ExpectedFile(
                "acquired_20260908/bdd100k_images_10k.zip",
                size_bytes=1_103_797_977,
                sha256="fd95e3ba04afeb89f724e080ea738185decaefe0250471a3a340c19f1f79a118",
            ),
            ExpectedFile(
                "acquired_20260908/bdd100k_seg_maps.zip",
                size_bytes=167_867_229,
                sha256="d6642e9efeeb30b4eac351a06f83753b87a6f8bd4def1baf940908159c322efe",
            ),
        ),
        taxonomy_note=(
            "Percepção de condução: via, veículo, pedestre. Nenhuma classe do "
            "§8.2 aparece aqui, então toda amostra sai com `masks` vazio e a "
            "segmentação registrada em `rejected`. É contexto de navegação, "
            "não dado de treino da V1."
        ),
        group_note="Grupo = split oficial. O pacote não publica sessão nem rota.",
        caveats=(
            (
                "O MD5 publicado na documentação oficial tem 31 dígitos hexadecimais "
                "e é inválido; a integridade aqui é CRC do ZIP mais SHA-256 local, "
                "o que não prova autenticidade da origem. Ver "
                "reports/acquisition_result_bdd100k.json."
            ),
            (
                "Os ZIPs não são extraídos: o adaptador lê as entradas fechadas para "
                "não duplicar 1,27 GB em disco."
            ),
            (
                "Os mapas de segmentação cobrem train e val; o split test do 10K vem "
                "sem máscara e sai sem `rejected`."
            ),
        ),
    ),
)

_BY_ID = {source.id: source for source in SOURCES}


def get_source(dataset_id: str) -> DatasetSource:
    """Fonte pelo id, ou erro que lista o que existe."""
    try:
        return _BY_ID[dataset_id]
    except KeyError:
        conhecidas = ", ".join(sorted(_BY_ID))
        raise KeyError(
            f"dataset '{dataset_id}' não está no catálogo; conhecidos: {conhecidas}"
        ) from None


def sources_by_role(role: DatasetRole) -> tuple[DatasetSource, ...]:
    return tuple(s for s in SOURCES if s.role is role)
