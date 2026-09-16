"""Leitores de cada fonte para o formato canônico (§8.3 passo 2, §8.4).

Um adaptador aqui faz três coisas e só três: encontra os arquivos, traduz o
rótulo pelo mapa de `app.ml.taxonomy` e devolve o grupo de split. Ele **não**
converte máscara em caixa, não filtra por confiança e não inventa coordenada.

Todos os leitores são geradores: o RDD2022 tem dezenas de milhares de imagens e
nenhum deles carrega o dataset inteiro em memória.

Nem toda fonte chega como pasta de arquivos soltos. O Project Sidewalk e o
RampNet publicam Parquet com a imagem embutida na linha, e o BDD100K publica
ZIP. Nesses três casos o leitor abre o pacote oficial *no lugar*, lote a lote,
em vez de extrair — extrair dobraria o espaço em disco para não acrescentar
nenhuma informação. Quem quiser o JPEG em arquivo usa `extract_image`.
"""

from __future__ import annotations

import csv
import json
import os
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from app.datasets.records import (
    NEGATIVE_SEMANTICS_UNVERIFIED,
    AnnotatedImage,
    BoundingBox,
    GeoRecord,
    Keypoint,
    KeypointSample,
    MaskSample,
    RejectedLabel,
)
from app.ml.taxonomy import (
    RDD2022_TO_URMIND,
    URBAN_COMMUNITY_CLASS_IDS,
    map_dataset_label,
    map_univali_label,
    map_urban_community_label,
)

__all__ = [
    "ADAPTERS",
    "DERIVED_ONLY_ADAPTERS",
    "AdapterError",
    "extract_image",
    "read_bdd100k",
    "read_camber_detections",
    "read_camber_route",
    "read_global_streetscapes",
    "read_project_sidewalk",
    "read_rampnet",
    "read_rdd2022",
    "read_records",
    "read_univali_br",
    "read_urban_community",
]

_GPX_NS = {"gpx": "http://www.topografix.com/GPX/1/1"}
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")


class AdapterError(RuntimeError):
    """A pasta não tem o formato que a fonte publica. Erro explícito, não `pass`."""


def _find_marker(root: Path, name: str, max_depth: int = 3) -> Path:
    """Localiza uma pasta âncora, tolerando um nível a mais de aninhamento no ZIP."""
    if (root / name).is_dir():
        return root / name
    for depth in range(1, max_depth + 1):
        for candidate in root.glob("/".join(["*"] * depth) + f"/{name}"):
            if candidate.is_dir():
                return candidate
    raise AdapterError(
        f"pasta '{name}' não encontrada sob {root}; extraia o pacote oficial antes de ler"
    )


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


# ------------------------------------------------------------------- RDD2022


def read_rdd2022(root: Path, *, countries: set[str] | None = None) -> Iterator[AnnotatedImage]:
    """Lê o RDD2022 extraído (anotação PASCAL VOC).

    Só `train/` traz XML; `test/` é submissão do desafio e não tem rótulo, então
    não produz amostra nenhuma — reportar uma imagem de teste como anotada seria
    inventar ground truth.

    Grupo = país. É o agrupamento honesto que o pacote oferece: não há id de
    sessão nem de rota nos arquivos (§8.4).
    """
    base = _find_marker(root, "RDD2022")
    for country_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        if countries is not None and country_dir.name not in countries:
            continue
        xml_dir = country_dir / "train" / "annotations" / "xmls"
        if not xml_dir.is_dir():
            continue
        image_dir = country_dir / "train" / "images"
        for xml_path in sorted(xml_dir.glob("*.xml")):
            sample = _parse_voc(
                xml_path,
                dataset_id="rdd2022",
                root=root,
                image_dir=image_dir,
                group=country_dir.name,
                official_split="train",
            )
            if sample is not None:
                yield sample


def _parse_voc(
    xml_path: Path,
    *,
    dataset_id: str,
    root: Path,
    image_dir: Path,
    group: str,
    official_split: str | None,
) -> AnnotatedImage | None:
    tree = ET.parse(xml_path)
    node = tree.getroot()

    size = node.find("size")
    if size is None:
        raise AdapterError(f"{xml_path}: XML sem <size>")
    width = int(float(size.findtext("width", "0")))
    height = int(float(size.findtext("height", "0")))
    if width <= 0 or height <= 0:
        raise AdapterError(f"{xml_path}: dimensão inválida {width}x{height}")

    filename = node.findtext("filename") or f"{xml_path.stem}.jpg"
    image_path = image_dir / filename
    if not image_path.exists():
        # A anotação aponta para uma imagem que não veio no pacote. Não é erro
        # fatal do conjunto todo, mas esta amostra não existe.
        return None

    boxes: list[BoundingBox] = []
    rejected: list[RejectedLabel] = []
    for obj in node.findall("object"):
        label = (obj.findtext("name") or "").strip()
        mapping = map_dataset_label(label, RDD2022_TO_URMIND)
        if not mapping.accepted or mapping.urmind_class is None:
            rejected.append(RejectedLabel(label, mapping.reason or "sem motivo registrado"))
            continue
        box = obj.find("bndbox")
        if box is None:
            rejected.append(RejectedLabel(label, "objeto sem <bndbox>"))
            continue
        try:
            boxes.append(
                BoundingBox(
                    urmind_class=mapping.urmind_class,
                    source_label=label,
                    xmin=float(box.findtext("xmin", "0")),
                    ymin=float(box.findtext("ymin", "0")),
                    xmax=float(box.findtext("xmax", "0")),
                    ymax=float(box.findtext("ymax", "0")),
                )
            )
        except ValueError as exc:  # caixa degenerada
            rejected.append(RejectedLabel(label, f"caixa inválida: {exc}"))

    return AnnotatedImage(
        dataset_id=dataset_id,
        image_path=_relative(image_path, root),
        width=width,
        height=height,
        group=group,
        boxes=tuple(boxes),
        rejected=tuple(rejected),
        official_split=official_split,
    )


# ----------------------------------------------------------- Urban Community

URBAN_COMMUNITY_LABEL = "pothole"
"""Única pasta da fonte cujo rótulo corresponde a uma classe da V1 (§8.2)."""


def _urban_community_artifacts(root: Path) -> Path:
    """Diretório `datasets/` a partir da raiz da fonte em `datasets/raw/<id>`."""
    return root.parent.parent


_CLOUD_ONLY_ATTRIBUTES = 0x00000400 | 0x00001000 | 0x00400000


def _is_cloud_only(path: Path) -> bool:
    """Detecta placeholder sem abrir o arquivo e sem provocar hidratação."""
    attributes = getattr(path.stat(), "st_file_attributes", 0)
    return os.name == "nt" and bool(attributes & _CLOUD_ONLY_ATTRIBUTES)


def _require_local_artifact(path: Path) -> Path:
    """Libera leitura/hash somente de arquivo regular materializado localmente."""
    if not path.exists():
        raise AdapterError(f"artefato ausente: {path}")
    if not path.is_file():
        raise AdapterError(f"artefato não é arquivo regular: {path}")
    if _is_cloud_only(path):
        raise AdapterError(f"artefato cloud-only: leitura recusada sem hidratação: {path}")
    return path


def _read_artifact_text(path: Path, *, encoding: str = "utf-8") -> str:
    return _require_local_artifact(path).read_text(encoding=encoding)


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in _read_artifact_text(path).splitlines() if line.strip()]


def _is_sha256(value: object) -> bool:
    """64 dígitos hexadecimais minúsculos — o formato que `_sha256` produz."""
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with _require_local_artifact(path).open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _urban_community_chain(datasets_dir: Path) -> tuple[list[dict], list[dict], dict]:
    """Carrega a derivada autorizada, conferindo a cadeia antes de devolver nada.

    Fail-closed em todos os elos. Artefato ausente, ilegível ou com hash de outra
    versão levanta `AdapterError` — **nunca** cai de volta no raw. O fallback para
    o dado bruto é exatamente o caminho que produziu a divergência que este
    adaptador existe para eliminar: a derivada dizia 290 imagens e 451 candidatas,
    e a leitura direta do raw media 300 imagens e 478 caixas promovidas a D40,
    incluindo as dez duplicatas e as cinco em quarentena.
    """
    manifests = datasets_dir / "manifests"
    reports = datasets_dir / "reports"
    scan_path = manifests / "urban_community_scan.jsonl"
    boxes_path = manifests / "urban_community_boxes.jsonl"
    conversion_path = reports / "urban_community_conversion.json"

    for caminho in (scan_path, boxes_path, conversion_path):
        try:
            _require_local_artifact(caminho)
        except AdapterError as exc:
            raise AdapterError(
                f"urban_community: artefato autorizado indisponível ({caminho.name}): "
                f"{exc}. "
                "A fonte fica bloqueada; ler o raw diretamente recriaria a "
                "decisão paralela que o manifesto derivado substituiu."
            ) from exc

    try:
        conversao = json.loads(_read_artifact_text(conversion_path, encoding="utf-8-sig"))
        scan = _load_jsonl(scan_path)
        rows = _load_jsonl(boxes_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise AdapterError(f"urban_community: derivada ilegível: {exc}") from exc

    integridade = conversao.get("integrity", {})
    for nome, caminho in (("scan_manifest", scan_path), ("boxes_manifest", boxes_path)):
        registrado = integridade.get(f"{nome}_sha256")
        atual = _sha256(caminho)
        if not registrado:
            raise AdapterError(
                f"urban_community: a conversão não registrou o sha256 de {nome}; "
                "sem vínculo, não há como saber que esta é a versão validada"
            )
        if registrado != atual:
            raise AdapterError(
                f"urban_community: {nome} diverge do que a conversão registrou "
                f"({atual[:12]} != {registrado[:12]}); a derivada está "
                "desatualizada e a fonte fica bloqueada"
            )

    # Duplicata e contaminação cruzada são propriedades do conjunto e só existem
    # depois da validação. Ausente, reprovada ou de outra versão do manifesto: os
    # portões correspondentes ficam fechados e nada é autorizado.
    validation_path = reports / "urban_community_validation.json"
    checks: dict[str, Any] = {
        "taxonomy_mapping_validated": bool(
            conversao.get("mapping", {}).get("semantic_mapping_approved")
        ),
        "duplicate_check_passed": False,
        "cross_source_check_passed": False,
        "validation_report": None,
    }
    if validation_path.is_file():
        try:
            validacao = json.loads(_read_artifact_text(validation_path, encoding="utf-8-sig"))
        except AdapterError:
            raise
        except (OSError, json.JSONDecodeError) as exc:
            raise AdapterError(f"urban_community: validação ilegível: {exc}") from exc
        integridade_validacao = validacao.get("integrity", {})

        # Dois elos, não um. Conferir só o manifesto de caixas deixava passar o
        # caso em que alguém edita `urban_community_conversion.json` depois da
        # validação: as caixas continuam idênticas, a validação continua dizendo
        # "aprovado", e o `semantic_mapping_approved` que abre o portão de
        # taxonomia passa a vir de um arquivo que ninguém validou. A validação
        # já grava o SHA-256 da conversão que ela leu — é esse que vale.
        mesma_versao = integridade_validacao.get("boxes_manifest_sha256") == _sha256(boxes_path)
        conversao_registrada = integridade_validacao.get("conversion_report_sha256")
        conversao_atual = _sha256(conversion_path)
        # Ausente, vazio ou malformado reprova como divergente. Pular a comparação
        # quando o campo falta era fail-open: um relatório antigo, ou escrito à
        # mão, abria os portões de duplicata e contaminação cruzada com resultados
        # que ninguém provou pertencerem à conversão em disco.
        if not _is_sha256(conversao_registrada):
            raise AdapterError(
                "urban_community: a validação não registra um "
                "`integrity.conversion_report_sha256` válido "
                f"(valor: {conversao_registrada!r}). Sem esse vínculo não há como "
                "provar que duplicata, contaminação e mapeamento pertencem à "
                "mesma conversão; nenhuma caixa é autorizada."
            )
        if conversao_registrada != conversao_atual:
            raise AdapterError(
                "urban_community: o relatório de conversão mudou depois da "
                f"validação ({conversao_atual[:12]} != {conversao_registrada[:12]}). "
                "A cadeia está quebrada e nenhuma caixa é autorizada: revalide "
                "antes de consumir a derivada."
            )

        if mesma_versao and validacao.get("passed"):
            duplicatas = validacao.get("duplicates", {})
            checks["duplicate_check_passed"] = not duplicatas.get(
                "exact_sha256_groups"
            ) and not duplicatas.get("blocking_near_duplicate_pairs")
            checks["cross_source_check_passed"] = bool(
                integridade_validacao.get("cross_source_valid")
            )
        checks["validation_report"] = {
            "same_version": mesma_versao,
            "passed": bool(validacao.get("passed")),
            "conversion_report_linked": bool(conversao_registrada),
            "conversion_report_sha256": conversao_atual,
        }
    else:
        # Sem validação não há como saber se a conversão em disco é a auditada,
        # então o portão de taxonomia também fecha. O relatório de conversão
        # sozinho afirma sobre si mesmo, e isso não é verificação.
        checks["taxonomy_mapping_validated"] = False
        checks["validation_report"] = {
            "same_version": False,
            "passed": False,
            "reason": "relatório de validação ausente",
        }

    return scan, rows, checks


def read_urban_community(root: Path) -> Iterator[AnnotatedImage]:
    """Lê o Urban Community pela **derivada autorizada**, nunca pelo raw.

    A fonte de verdade é `datasets/manifests/urban_community_boxes.jsonl`, com a
    cadeia de hashes conferida antes de qualquer coisa. Só vira `BoundingBox` a
    caixa que passou por todos os portões de `app.datasets.authorization` — hoje,
    nenhuma: ninguém revisou caixa alguma, e o rótulo `pothole` da pasta sustenta
    a CLASSE, não cada anotação feita sob aquele nome.

    O que a fonte trazia continua contabilizado. Candidata não autorizada sai como
    `RejectedLabel` com o portão que a barrou — some de `usable`, não some da
    conta. `good_road` sai marcada como negativa de semântica não verificada, e
    as cinco pastas fora do escopo continuam com a recusa de sempre. O adaptador
    lê o *scan* derivado para isso, e não o rótulo bruto: nenhuma classe da V1
    nasce aqui.
    """
    from app.datasets.authorization import authorize_rows

    scan, rows, checks = _urban_community_chain(_urban_community_artifacts(root))

    mapeamento = map_urban_community_label(URBAN_COMMUNITY_LABEL)
    classe = mapeamento.urmind_class
    if not mapeamento.accepted or classe is None:  # pragma: no cover
        raise AdapterError("urban_community: taxonomia não declara classe para `pothole`")

    autorizadas: dict[str, list[dict]] = {}
    barradas: dict[str, list[dict]] = {}
    for linha in authorize_rows(rows, source_checks=checks, urmind_class=str(classe)):
        stem = linha["stem"]
        autorizadas[stem] = [b for b in linha["boxes"] if b["training_allowed"]]
        barradas[stem] = [b for b in linha["boxes"] if not b["training_allowed"]]

    derivadas = {linha["stem"] for linha in rows}

    for linha in scan:
        pasta, stem = linha["folder"], linha["stem"]
        boxes: list[BoundingBox] = []
        rejected: list[RejectedLabel] = []
        negative_status: str | None = None

        if pasta == URBAN_COMMUNITY_LABEL:
            if stem not in derivadas:
                # Imagem bloqueada por duplicata na conversão. Continua contada,
                # com o motivo, para que 478 → 456 permaneça auditável.
                rejected.extend(
                    RejectedLabel(
                        URBAN_COMMUNITY_LABEL,
                        "imagem removida da derivada por duplicata exata",
                    )
                    for _ in linha["boxes"]
                )
            else:
                for box in autorizadas[stem]:
                    boxes.append(
                        BoundingBox(
                            classe,
                            URBAN_COMMUNITY_LABEL,
                            box["xmin"],
                            box["ymin"],
                            box["xmax"],
                            box["ymax"],
                        )
                    )
                rejected.extend(
                    RejectedLabel(
                        URBAN_COMMUNITY_LABEL,
                        "candidata sem autorização: " + ", ".join(box["blocking_gates"]),
                    )
                    for box in barradas[stem]
                )
        else:
            for box in linha["boxes"]:
                nome = URBAN_COMMUNITY_CLASS_IDS.get(box["class_id"])
                if nome is None:
                    rejected.append(
                        RejectedLabel(str(box["class_id"]), "id fora do mapa inferido das pastas")
                    )
                    continue
                mapping = map_urban_community_label(nome)
                rejected.append(RejectedLabel(nome, mapping.reason or ""))
            if not linha["boxes"]:
                # `good_road`: .txt vazio. A fonte não publica protocolo de
                # anotação, então a ausência de caixa não prova que alguém olhou
                # e não havia nada. Retida, nunca negativa de treino.
                negative_status = NEGATIVE_SEMANTICS_UNVERIFIED

        yield AnnotatedImage(
            dataset_id="urban_community",
            image_path=linha["image_relpath"].split("urban_community/", 1)[-1],
            width=linha["image_width"],
            height=linha["image_height"],
            group=pasta,
            boxes=tuple(boxes),
            rejected=tuple(rejected),
            negative_status=negative_status,
        )


# ------------------------------------------------------------- UNIVALI / DNIT


def read_univali_br(root: Path) -> Iterator[MaskSample]:
    """Lê os pares imagem/máscara do UNIVALI/DNIT.

    Devolve `MaskSample`, não `AnnotatedImage`: a anotação é máscara e a
    conversão para caixa é decisão registrada à parte (ver catálogo).

    Grupo = trecho de rodovia no nome da pasta. `1007599_RS_386_386RS289112_28920`
    vira `RS_386_386RS289112`: o primeiro campo é o id da foto e o último, a
    posição — o miolo identifica o trecho, e fotos do mesmo trecho não podem
    cair em splits diferentes (§8.4).
    """
    base = _find_marker(root, "v1")
    for sample_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        raw = next(
            (p for p in sample_dir.glob("*_RAW.*") if p.suffix.lower() in _IMAGE_SUFFIXES),
            None,
        )
        if raw is None:
            continue

        masks: dict = {}
        rejected: list[RejectedLabel] = []
        for mask_path in sorted(sample_dir.glob("*.png")):
            label = mask_path.stem.rsplit("_", 1)[-1]
            mapping = map_univali_label(label)
            if mapping.accepted and mapping.urmind_class is not None:
                masks[mapping.urmind_class] = _relative(mask_path, root)
            else:
                rejected.append(RejectedLabel(label, mapping.reason or ""))

        yield MaskSample(
            dataset_id="univali_br",
            image_path=_relative(raw, root),
            group=univali_group(sample_dir.name),
            masks=masks,
            rejected=tuple(rejected),
        )


def univali_group(directory_name: str) -> str:
    """Extrai o trecho de rodovia do nome da pasta; devolve o nome inteiro se não der.

    As quatro pastas com um campo extra antes da posição
    (`1050564_DF_080_080BDF0050_1_00368`) caem aqui como
    `DF_080_080BDF0050_1`, um trecho distinto de `DF_080_080BDF0050`. Se os dois
    nomeiam o mesmo trecho, a fonte não diz — e supor que sim uniria grupos sem
    evidência. O split do UNIVALI agrupa por rodovia justamente por isso: no
    nível de rodovia os dois ficam juntos sem ninguém precisar adivinhar
    (ver `scripts/datasets/make_univali_splits.py`).
    """
    parts = directory_name.split("_")
    return "_".join(parts[1:-1]) if len(parts) > 2 else directory_name


# -------------------------------------------------------------------- CAMBER


def read_camber_detections(root: Path) -> Iterator[GeoRecord]:
    """Lê `detections/detections_<rota>.csv`.

    `malfunction_type` é o código do detector do CAMBER, preservado como veio.
    Não existe tradução para a taxonomia do UrMind: o §8.2 não aceita mapear
    código de outro projeto sem protocolo, e a coluna `user_confirmed` vazia
    significa que ninguém confirmou nada — é saída de modelo, não ground truth.
    """
    detections_dir = root / "detections"
    if not detections_dir.is_dir():
        raise AdapterError(f"{root}: falta a pasta detections/")

    for csv_path in sorted(detections_dir.glob("*.csv")):
        route = csv_path.stem.replace("detections_", "") or csv_path.stem
        with csv_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                lat, lon = row.get("latitude"), row.get("longitude")
                if not lat or not lon:
                    continue
                yield GeoRecord(
                    dataset_id="camber",
                    external_id=row.get("detection_id", ""),
                    latitude=float(lat),
                    longitude=float(lon),
                    group=f"route:{route}",
                    kind=f"camber_malfunction_{row.get('malfunction_type', 'unknown')}",
                    human_confirmed=_optional_bool(row.get("user_confirmed")),
                    attributes={
                        "timecode_seconds": row.get("timecode_seconds"),
                        "frame": row.get("frame"),
                        "field_confirmed": row.get("field_confirmed") or None,
                        "field_solved": row.get("field_solved") or None,
                        "created_at": row.get("created_at"),
                        "source_csv": _relative(csv_path, root),
                    },
                )


def read_camber_route(root: Path) -> Iterator[GeoRecord]:
    """Lê os pontos do GPX da rota. Trajeto percorrido, não ocorrência de dano."""
    routes_dir = root / "routes"
    if not routes_dir.is_dir():
        raise AdapterError(f"{root}: falta a pasta routes/")

    for gpx_path in sorted(routes_dir.glob("*.gpx")):
        tree = ET.parse(gpx_path)
        for index, point in enumerate(tree.getroot().iterfind(".//gpx:trkpt", _GPX_NS)):
            time_node = point.find("gpx:time", _GPX_NS)
            yield GeoRecord(
                dataset_id="camber",
                external_id=f"{gpx_path.stem}#{index}",
                latitude=float(point.attrib["lat"]),
                longitude=float(point.attrib["lon"]),
                group=f"route:{gpx_path.stem}",
                kind="camber_track_point",
                human_confirmed=None,
                attributes={
                    "time": time_node.text if time_node is not None else None,
                    "source_gpx": _relative(gpx_path, root),
                },
            )


def _optional_bool(value: str | None) -> bool | None:
    """Coluna vazia é 'ninguém respondeu', não 'não confirmado'."""
    if value is None or value.strip() == "":
        return None
    return value.strip().lower() in {"1", "true", "t", "yes", "y"}


# ----------------------------------------------------------- Project Sidewalk


def _parquet_files(root: Path, *, subdir: str) -> list[Path]:
    """Todos os shards oficiais baixados, em ordem estável."""
    base = root / subdir
    if not base.is_dir():
        raise AdapterError(
            f"{base}: recorte oficial ausente. Baixe com "
            "`python scripts/datasets/acquire_registered.py --dataset <id> --execute`"
        )
    files = sorted(base.rglob("*.parquet"))
    if not files:
        raise AdapterError(f"{base}: nenhum .parquet encontrado (arquivos .part não contam)")
    return files


def _acquired_dir(root: Path) -> str:
    """Nome da pasta de aquisição, que carrega a data do recorte."""
    for child in sorted(root.iterdir()):
        if child.is_dir() and child.name.startswith("acquired_"):
            return child.name
    raise AdapterError(f"{root}: nenhuma pasta acquired_* encontrada")


def _official_split(path: Path) -> str | None:
    """Split publicado pela fonte, lido da pasta em que o shard está."""
    for part in path.parts:
        if part in ("train", "val", "validation", "test"):
            return "validation" if part == "val" else part
    return None


def extract_image(root: Path, image_ref: str) -> bytes:
    """Devolve os bytes do JPEG citado por um `KeypointSample.image_ref`.

    É o contrário de extrair o pacote inteiro: paga-se a leitura de um row group
    para obter uma imagem, quando alguém realmente precisa dela.
    """
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    rel, _, row_part = image_ref.partition("#row=")
    if not row_part:
        raise AdapterError(f"referência sem índice de linha: {image_ref}")
    target = int(row_part)
    path = root / rel
    if not path.is_file():
        raise AdapterError(f"parquet não encontrado para {image_ref}")

    handle = pq.ParquetFile(path)
    seen = 0
    for batch in handle.iter_batches(batch_size=256, columns=["image"]):
        if seen + batch.num_rows <= target:
            seen += batch.num_rows
            continue
        return batch.column("image")[target - seen].as_py()["bytes"]
    raise AdapterError(f"linha {target} não existe em {rel}")


def read_project_sidewalk(root: Path) -> Iterator[KeypointSample]:
    """Lê o recorte oficial `rampnet-crop-model-dataset-round1` (Parquet).

    Cada linha é um recorte de panorâmica do Street View com os pontos de rampa
    de calçada marcados em pixels. Ponto é ponto: o §8.2 não tem classe de rampa
    e transformar keypoint em caixa exigiria inventar extensão, então isto entra
    como `KeypointSample` e é recusado para treino V1, com o motivo registrado.

    Grupo = `crop_uid`, o identificador da panorâmica de origem. Recortes da
    mesma panorâmica ficam no mesmo lado do split (§8.4); usar o `crop_id` como
    grupo deixaria vazar vizinhança entre train e validation.
    """
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    acquired = _acquired_dir(root)
    rejected = (
        RejectedLabel(
            "curb_ramp",
            "keypoint de acessibilidade; a taxonomia V1 do §8.2 só tem dano de via",
        ),
    )
    for path in _parquet_files(root, subdir=f"{acquired}/data"):
        rel = _relative(path, root)
        split = _official_split(path.relative_to(root))
        row = 0
        handle = pq.ParquetFile(path)
        for batch in handle.iter_batches(
            batch_size=256,
            columns=["crop_id", "crop_uid", "keypoints", "width", "height", "sha256"],
        ):
            for item in batch.to_pylist():
                points = tuple(
                    Keypoint(x=float(k["x"]), y=float(k["y"]), kind="curb_ramp")
                    for k in (item.get("keypoints") or [])
                    if k.get("x") is not None and k.get("y") is not None
                )
                yield KeypointSample(
                    dataset_id="project_sidewalk",
                    image_ref=f"{rel}#row={row}",
                    width=int(item["width"]),
                    height=int(item["height"]),
                    group=f"pano:{item['crop_uid']}",
                    keypoints=points,
                    official_split=split,
                    rejected=rejected if points else (),
                    attributes={
                        "crop_id": item["crop_id"],
                        "image_sha256": item.get("sha256"),
                    },
                )
                row += 1


def read_rampnet(root: Path) -> Iterator[KeypointSample | GeoRecord]:
    """Lê o recorte oficial `rampnet-dataset` (Parquet).

    Cada linha traz a panorâmica inteira e duas anotações da mesma rampa:
    `curb_ramp_points_normalized`, em fração da imagem, e `curb_ramp_coords`,
    em latitude/longitude reais. São coisas diferentes e saem como registros
    diferentes — o ponto na imagem vira `KeypointSample`, a coordenada no mundo
    vira `GeoRecord`. Juntá-los num registro só esconderia que a segunda serve
    para consulta espacial e a primeira não.

    Grupo = `pano_id`. É a captura, o agrupamento honesto contra vazamento.
    """
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    acquired = _acquired_dir(root)
    rejected = (
        RejectedLabel(
            "curb_ramp",
            "keypoint de acessibilidade; a taxonomia V1 do §8.2 só tem dano de via",
        ),
    )
    for path in _parquet_files(root, subdir=acquired):
        rel = _relative(path, root)
        split = _official_split(path.relative_to(root))
        row = 0
        handle = pq.ParquetFile(path)
        for batch in handle.iter_batches(
            batch_size=64,
            columns=[
                "pano_id",
                "record_creation_time",
                "curb_ramp_points_normalized",
                "pano_coord",
                "curb_ramp_coords",
                "pano_azimuth",
            ],
        ):
            for item in batch.to_pylist():
                pano_id = item["pano_id"]
                normalized = item.get("curb_ramp_points_normalized") or []
                # A fonte não publica largura/altura. Os pontos vêm normalizados
                # e assim ficam: converter para pixel exigiria decodificar cada
                # panorâmica. `width`/`height` em 0 sinalizam essa ausência, e o
                # atributo abaixo diz explicitamente em que escala está o ponto.
                points = tuple(
                    Keypoint(x=float(pt[0]), y=float(pt[1]), kind="curb_ramp_normalized")
                    for pt in normalized
                    if pt and len(pt) >= 2
                )
                yield KeypointSample(
                    dataset_id="rampnet",
                    image_ref=f"{rel}#row={row}",
                    width=0,
                    height=0,
                    group=f"pano:{pano_id}",
                    keypoints=points,
                    official_split=split,
                    rejected=rejected if points else (),
                    attributes={
                        "coordinates_are_normalized": True,
                        "pano_azimuth": item.get("pano_azimuth"),
                        "record_creation_time": item.get("record_creation_time"),
                    },
                )
                row += 1

                for order, coord in enumerate(item.get("curb_ramp_coords") or []):
                    if not coord or len(coord) < 2:
                        continue
                    yield GeoRecord(
                        dataset_id="rampnet",
                        external_id=f"{pano_id}:{order}",
                        latitude=float(coord[0]),
                        longitude=float(coord[1]),
                        group=f"pano:{pano_id}",
                        kind="curb_ramp",
                        human_confirmed=True,  # rótulo humano do Project Sidewalk
                        attributes={
                            "pano_id": pano_id,
                            "pano_latitude": (item.get("pano_coord") or [None, None])[0],
                            "pano_longitude": (item.get("pano_coord") or [None, None])[1],
                            "source_parquet": rel,
                        },
                    )


_BDD_SEG_SUFFIX = "_train_color.png"


def read_bdd100k(root: Path) -> Iterator[MaskSample]:
    """Pareia o recorte oficial 10K com os mapas de segmentação, sem extrair.

    Os dois ZIPs oficiais somam 1,27 GB e ficam fechados: `zipfile` lista e lê
    entradas sob demanda, então o pareamento imagem↔máscara não custa outro
    1,27 GB de disco.

    Nenhuma classe do BDD100K é classe do §8.2 — o pacote é percepção de
    condução, não dano de pavimento. Por isso toda amostra sai com `masks`
    vazio e a máscara registrada em `rejected`: o dado é real, serve de contexto
    de navegação, e o registro deixa explícito que não é treino V1.

    Grupo = split oficial do BDD100K. O pacote não publica sessão nem rota.
    """
    import zipfile

    acquired = _acquired_dir(root)
    images_zip = root / acquired / "bdd100k_images_10k.zip"
    seg_zip = root / acquired / "bdd100k_seg_maps.zip"
    for required in (images_zip, seg_zip):
        if not required.is_file():
            raise AdapterError(f"{required}: pacote oficial ausente")

    with zipfile.ZipFile(seg_zip) as seg:
        seg_by_stem = {
            Path(name).name[: -len(_BDD_SEG_SUFFIX)]: name
            for name in seg.namelist()
            if name.endswith(_BDD_SEG_SUFFIX)
        }

    rejected = (
        RejectedLabel(
            "bdd100k_semantic_segmentation",
            "classes de condução (via, veículo, pedestre); o §8.2 só aceita dano de via",
        ),
    )
    with zipfile.ZipFile(images_zip) as images:
        for name in sorted(images.namelist()):
            if not name.lower().endswith(".jpg"):
                continue
            stem = Path(name).stem
            split = _official_split(Path(name))
            yield MaskSample(
                dataset_id="bdd100k",
                image_path=f"{acquired}/bdd100k_images_10k.zip#{name}",
                group=f"split:{split or 'unknown'}",
                masks={},
                rejected=rejected if stem in seg_by_stem else (),
            )


_GS_CONTEXT_ATTRS = (
    "glare",
    "lighting_condition",
    "pano_status",
    "platform",
    "quality",
    "reflection",
    "view_direction",
    "weather",
)


def read_global_streetscapes(root: Path) -> Iterator[GeoRecord]:
    """Lê o recorte do Global Streetscapes que substitui o Mapillary MSLS.

    O CSV gerado na aquisição já é a união dos rótulos oficiais com o caminho
    e o SHA-256 de cada JPEG efetivamente gravado. Ler daqui, e não dos 16 CSV
    de origem, garante que só sai registro para imagem que existe em disco.

    Sai como `GeoRecord` porque é isso que a fonte é: ocorrência com coordenada
    e rótulo humano de *cena* — clima, luz, tipo de via. Nenhum desses é dano,
    então `kind` guarda a plataforma da via e o resto vai para `attributes`.
    Nada aqui vira classe do §8.2.

    Grupo = `sequence_id`, a sequência de captura (§8.4).
    """
    labels = root / "labels" / "global_streetscapes_selected.csv"
    if not labels.is_file():
        raise AdapterError(
            f"{labels}: recorte ausente. Gere com "
            "`python scripts/datasets/acquire_global_streetscapes.py --execute`"
        )

    with labels.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if not row.get("lat") or not row.get("lon"):
                continue
            image = root / row["rel_path"]
            if not image.is_file():
                continue  # o CSV lista o recorte; imagem ausente não vira registro
            yield GeoRecord(
                dataset_id="global_streetscapes",
                external_id=row["uuid"],
                latitude=float(row["lat"]),
                longitude=float(row["lon"]),
                group=f"sequence:{row['sequence_id']}",
                kind=row.get("platform") or "unknown",
                human_confirmed=True,  # rótulo manual declarado pela fonte
                attributes={
                    "image_path": row["rel_path"],
                    "image_sha256": row.get("sha256"),
                    "upstream_source": row.get("source"),
                    "city": row.get("city"),
                    "country": row.get("country"),
                    "continent": row.get("continent"),
                    "datetime_local": row.get("datetime_local"),
                    "official_split": row.get("split"),
                    **{a: (row.get(a) or None) for a in _GS_CONTEXT_ATTRS},
                },
            )


# ------------------------------------------------------------------- despacho


def _read_camber(root: Path) -> Iterator[GeoRecord]:
    """Detecções e trajeto no mesmo fluxo: os dois vêm do mesmo registro Zenodo."""
    yield from read_camber_detections(root)
    yield from read_camber_route(root)


DatasetRecord = AnnotatedImage | GeoRecord | KeypointSample | MaskSample
DatasetReader = Callable[[Path], Iterator[DatasetRecord]]

ADAPTERS: dict[str, DatasetReader] = {
    "rdd2022": read_rdd2022,
    "univali_br": read_univali_br,
    "urban_community": read_urban_community,
    "camber": _read_camber,
    "project_sidewalk": read_project_sidewalk,
    "rampnet": read_rampnet,
    "bdd100k": read_bdd100k,
    "global_streetscapes": read_global_streetscapes,
}
"""Chave do campo `adapter` do catálogo → leitor. Fonte adiada não aparece aqui."""

DERIVED_ONLY_ADAPTERS: frozenset[str] = frozenset({"urban_community"})
"""Adaptadores que não abrem nenhum arquivo dentro de `raw/`.

Eles leem apenas artefato derivado — manifesto de varredura e derivada
autorizada, ambos pequenos e versionados. A trava do §4.4 que bloqueia a
varredura inteira quando existe marcador do OneDrive foi escrita porque os
adaptadores navegam por glob e abrem imagem a imagem; para estes, abrir nada do
raw é justamente o desenho. Bloqueá-los faria o relatório dizer "não medido"
sobre uma medição que não depende de hidratar byte nenhum.
"""


def read_records(adapter: str | None, root: Path) -> Iterator[DatasetRecord]:
    """Resolve o leitor pelo nome declarado no catálogo."""
    if adapter is None:
        raise AdapterError(
            "fonte sem adaptador: está declarada como adiada no catálogo e não é lida"
        )
    try:
        reader = ADAPTERS[adapter]
    except KeyError:
        raise AdapterError(f"adaptador '{adapter}' não implementado") from None
    return reader(root)
