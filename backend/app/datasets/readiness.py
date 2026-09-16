"""Perfil medido de cada fonte: o que existe, o que é treinável e o que diverge.

Este módulo existe para acabar com um problema concreto do projeto: havia três
descrições paralelas das mesmas oito fontes — o catálogo em código, as linhas
fixas de `refresh_sources.py` e a lista fixa de `audit_readiness.py` — e elas
podiam divergir sem que nada quebrasse. Foi o que aconteceu: o relatório
continuou declarando `project_sidewalk`, `rampnet` e `bdd100k` como
`no_dataset_data` meses depois de os três estarem em disco e com adaptador.

A saída é uma só: **o catálogo declara, o disco mede, e a divergência entre os
dois é reportada em vez de escolhida**. Nenhum número aqui é escrito à mão —
todos vêm de `inspect_source` (disco) e do adaptador da fonte (conteúdo).

A distinção que o relatório precisa sustentar, e que GB nenhum responde:

`stored_bytes`       — o que a fonte ocupa. É orçamento, não capacidade de treino.
`samples`            — registros que o adaptador produziu.
`usable_samples`     — registros com ao menos uma anotação aceita no §8.2.
`box_annotations`    — caixas por classe canônica. **É só isto que treina.**
`mask_annotations`   — anotação humana real, em geometria que o detector não lê.

Uma fonte pode ter 6,8 GB, 17 mil amostras e **zero** caixas. Isso não é defeito
dela: é a resposta correta para "quanto deste dado o detector da V1 consegue
aprender". As colunas ficam lado a lado justamente para que ninguém confunda
uma com a outra — foi confundi-las que produziu a leitura de que o projeto
tinha 35 GB de dados de treino.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.datasets.adapters import (
    ADAPTERS,
    DERIVED_ONLY_ADAPTERS,
    AdapterError,
    read_records,
)
from app.datasets.catalog import (
    EVALUATION_USAGES,
    SOURCES,
    DatasetSource,
    DatasetUsage,
    get_source,
)
from app.datasets.inventory import DatasetInventory, DatasetState, inspect_source
from app.datasets.records import (
    NEGATIVE_SEMANTICS_UNVERIFIED,
    AnnotatedImage,
    GeoRecord,
    KeypointSample,
    MaskSample,
)

__all__ = [
    "SourceProfile",
    "profile_all",
    "profile_source",
    "totals",
]


@dataclass(frozen=True)
class SourceProfile:
    """Tudo que se sabe de uma fonte depois de olhar o disco e ler o conteúdo."""

    dataset_id: str
    title: str
    path: str
    role: str
    usage: tuple[str, ...]
    annotation_format: str
    state: str

    stored_bytes: int
    file_count: int

    samples: int
    """Registros produzidos pelo adaptador. Nem todo registro é uma imagem."""

    images: int
    """Registros que referenciam uma imagem: `AnnotatedImage`, `MaskSample`, `KeypointSample`."""

    box_annotations: int
    """Caixas aceitas na taxonomia V1. **É o único número que o detector treina.**"""

    mask_annotations: int
    """Máscaras aceitas na taxonomia V1.

    Anotação real e humana, mas em outra geometria: o YOLOX da V1 consome caixa.
    Contada à parte de propósito — somá-la às caixas faria o acervo treinável
    parecer maior do que é, e é exatamente essa soma indevida que faz uma fonte
    de segmentação passar por fonte de detecção.
    """

    usable_samples: int
    """Amostras com ao menos uma anotação aceita — caixa ou máscara."""

    negative_images: int
    """Imagens sem nenhuma anotação aceita **e** sem nenhuma recusada.

    Negativo de verdade: a fonte olhou e não havia nada. Diferente de uma imagem
    cuja anotação existia e foi recusada por estar fora do §8.2 — essa é
    `out_of_scope_images`, e tratá-la como negativa ensinaria o detector que
    aquele objeto não existe.
    """

    out_of_scope_images: int
    unverified_negative_images: int = 0
    """Imagens sem anotação cuja ausência a fonte não comprova ser negativa.

    Separadas de `negative_images` porque uma imagem que ninguém examinou não é
    material de treino: contá-la como negativa ensinaria o detector que o objeto
    não estava lá. Enquanto o protocolo da fonte não for comprovado, este número
    fica retido e não soma em lugar nenhum.
    """

    unmeasured_mask_samples: int = 0
    """Amostras de máscara cujo conteúdo de pixel não foi medido.

    O adaptador anexa o caminho da máscara porque o arquivo existe; existir não
    é conter anotação. Enquanto ninguém medir o pixel, estas amostras ficam fora
    de `usable_samples` — usabilidade desconhecida não é usabilidade.
    """

    class_counts_boxes: dict[str, int] = field(default_factory=dict)
    """Caixas por classe canônica. A base de qualquer contagem de treino."""

    class_counts_masks: dict[str, int] = field(default_factory=dict)
    rejected_counts: dict[str, int] = field(default_factory=dict)
    """Rótulos que a fonte trazia e o §8.2 não aceita, com a contagem de cada um."""

    geo_kinds: dict[str, int] = field(default_factory=dict)
    """Vocabulário próprio dos registros georreferenciados, contado à parte.

    `GeoRecord.kind` não é rótulo recusado: é a categoria da fonte para uma
    ocorrência que nunca se candidatou a ser classe da V1 — a plataforma da via
    no Global Streetscapes, o tipo de ponto no CAMBER. Somá-lo às recusas
    inflaria a contagem de rejeição com coisas que ninguém propôs aceitar.
    """

    geo_records: int = 0
    keypoints: int = 0
    groups: int = 0

    scan_outcome: str = "SUCCESS"
    """Por que a varredura aconteceu ou não. Um nome por causa, nunca um só.

    `SUCCESS`, `CLOUD_ONLY_SKIP`, `ADAPTER_ERROR`, `READ_ERROR`,
    `SOURCE_NOT_READY`, `NO_ADAPTER`.
    """

    cloud_only_files: int = 0
    """Arquivos que existem como marcador do OneDrive, sem conteúdo local.

    `Path.exists()` responde `True` para eles e o tamanho declarado é o da nuvem,
    então um inventário ingênuo os conta como presentes. Para o treino não estão:
    abrir cada um dispara download. Contá-los à parte é a diferença entre "o dado
    existe" e "o dado está aqui".
    """

    cloud_only_bytes: int = 0
    read_error: str | None = None
    divergences: tuple[str, ...] = ()

    scan_performed: bool = False
    """O adaptador chegou a percorrer os arquivos desta fonte.

    Contar arquivo não é ler arquivo. `_count_files` consulta o atributo do
    sistema de arquivos, que não hidrata nada; o adaptador **abre** o arquivo, e
    abrir um marcador do OneDrive dispara o download. Quando há marcador, a
    varredura não acontece — e a diferença entre "varreu e não achou" e "não
    varreu" precisa estar no relatório, senão zero anotações parece medição.
    """

    scan_skipped_reason: str | None = None

    @property
    def feeds_detector(self) -> bool:
        """Contribui caixa que o detector da V1 consegue treinar ou avaliar."""
        return sum(self.class_counts_boxes.values()) > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "title": self.title,
            "path": self.path,
            "role": self.role,
            "usage": list(self.usage),
            "annotation_format": self.annotation_format,
            "state": self.state,
            "stored_bytes": self.stored_bytes,
            "stored_gb_decimal": round(self.stored_bytes / 1_000_000_000, 3),
            "file_count": self.file_count,
            "local_files": self.file_count - self.cloud_only_files,
            "cloud_only_files": self.cloud_only_files,
            "cloud_only_bytes": self.cloud_only_bytes,
            "locally_available": self.cloud_only_files == 0,
            "scan_performed": self.scan_performed,
            "scan_outcome": self.scan_outcome,
            "scan_skipped_reason": self.scan_skipped_reason,
            # A varredura só roda quando não há marcador nenhum, então quando ela
            # roda todo arquivo aberto era local. Quando não roda, nenhum foi.
            "files_scanned": self.file_count - self.cloud_only_files if self.scan_performed else 0,
            "files_skipped_to_avoid_hydration": 0 if self.scan_performed else self.file_count,
            # Separa "medido nesta execução" de "conhecido por inventário
            # anterior". Sem esta linha, `box_annotations: 0` de uma fonte
            # bloqueada por cloud-only se lê igual a uma fonte medida que não
            # tem caixa nenhuma — e as duas afirmações são opostas.
            "counts_measured_this_run": self.scan_performed,
            "counts_meaning": (
                "medido nesta execução"
                if self.scan_performed
                else "NÃO medido: contagens em zero por ausência de medição, "
                "não por ausência de anotação"
            ),
            "samples": self.samples,
            "images": self.images,
            "box_annotations": self.box_annotations,
            "mask_annotations": self.mask_annotations,
            "usable_samples": self.usable_samples,
            "negative_images": self.negative_images,
            "out_of_scope_images": self.out_of_scope_images,
            "unverified_negative_images": self.unverified_negative_images,
            "unmeasured_mask_samples": self.unmeasured_mask_samples,
            "class_counts_boxes": dict(sorted(self.class_counts_boxes.items())),
            "class_counts_masks": dict(sorted(self.class_counts_masks.items())),
            "rejected_counts": dict(sorted(self.rejected_counts.items())),
            "geo_kinds": dict(sorted(self.geo_kinds.items())),
            "geo_records": self.geo_records,
            "keypoints": self.keypoints,
            "groups": self.groups,
            "feeds_detector": self.feeds_detector,
            "read_error": self.read_error,
            "divergences": list(self.divergences),
        }


def _check_divergences(
    source: DatasetSource,
    profile_data: dict[str, Any],
    *,
    scanned: bool = True,
    scan_outcome: str = "SUCCESS",
    scan_reason: str = "",
) -> tuple[str, ...]:
    """Compara o que o catálogo declara com o que a leitura mediu.

    Divergência não é exceção: ela é reportada e o artefato continua sendo
    gerado, porque esconder o conflito é exatamente o que produziu o relatório
    obsoleto que este módulo substitui.

    `scanned=False` significa que a varredura foi bloqueada (marcador do
    OneDrive). Aí as contagens não são medição, e compará-las com o catálogo
    acusaria a fonte de não ter anotação quando ninguém olhou. A única
    divergência honesta nesse caso é a própria falta de medição.
    """
    problemas: list[str] = []
    caixas = sum(profile_data["class_counts_boxes"].values())
    mascaras = sum(profile_data["class_counts_masks"].values())

    if not scanned:
        if source.adapter is not None:
            problemas.append(
                f"fonte não medida ({scan_outcome}): {scan_reason}; contagens de "
                "anotação ficam em zero por AUSÊNCIA DE MEDIÇÃO, não por ausência "
                "de anotação"
            )
        return tuple(problemas)

    # TRAIN/VALIDATION/TEST são afirmações sobre o detector, e o detector da V1
    # treina caixa. Máscara aceita não sustenta nenhuma das três.
    if source.feeds_training and caixas == 0:
        problemas.append(
            f"catálogo declara {[u.value for u in source.usage]} mas a leitura não "
            "encontrou nenhuma caixa na taxonomia V1"
        )
    if DatasetUsage.UNUSABLE in source.usage and caixas > 0:
        problemas.append(
            f"catálogo declara UNUSABLE mas a leitura encontrou {caixas} "
            "caixa(s) na taxonomia V1"
        )
    if DatasetUsage.UNUSABLE in source.usage and mascaras > 0 and not source.unlock_requirement:
        problemas.append(
            f"UNUSABLE com {mascaras} máscara(s) aceitas e sem unlock_requirement: "
            "o bloqueio é de formato e precisa dizer o que o removeria"
        )
    if DatasetUsage.GEO_REFERENCE in source.usage and profile_data["geo_records"] == 0:
        problemas.append(
            "catálogo declara GEO_REFERENCE mas a leitura não encontrou nenhum "
            "registro com coordenada"
        )
    if source.adapter is not None and source.adapter not in ADAPTERS:
        problemas.append(f"adaptador '{source.adapter}' declarado e não implementado")
    if profile_data["unmeasured_mask_samples"]:
        problemas.append(
            f"{profile_data['unmeasured_mask_samples']} amostra(s) de máscara com "
            "conteúdo não medido: o arquivo existe e o pixel não foi lido, então a "
            "usabilidade é desconhecida, não confirmada"
        )
    if profile_data["unverified_negative_images"]:
        problemas.append(
            f"{profile_data['unverified_negative_images']} imagem(ns) sem anotação "
            "retidas como NEGATIVE_SEMANTICS_UNVERIFIED: a fonte não comprova o "
            "protocolo de anotação, então a ausência de caixa não vale como negativa"
        )
    if source.evaluation_forbidden and EVALUATION_USAGES & set(source.usage):
        problemas.append(
            "fonte evaluation_forbidden declarando uso de avaliação no catálogo"
        )
    return tuple(problemas)


def profile_source(
    source: DatasetSource | str,
    raw_root: Path | None = None,
    *,
    verify: bool = False,
    limit: int | None = None,
) -> SourceProfile:
    """Mede uma fonte. Não baixa, não converte e não escreve em `raw/`.

    `limit` existe para inspeção rápida e para os testes; um perfil truncado
    **não** deve ser publicado como relatório, porque as contagens ficariam
    parciais sem dizer que ficaram.
    """
    if isinstance(source, str):
        source = get_source(source)

    inventory: DatasetInventory = inspect_source(source, raw_root, verify=verify)
    arquivos, nuvem, bytes_nuvem = _count_files(inventory.root)

    dados: dict[str, Any] = {
        "samples": 0,
        "images": 0,
        "box_annotations": 0,
        "mask_annotations": 0,
        "usable_samples": 0,
        "negative_images": 0,
        "out_of_scope_images": 0,
        "unverified_negative_images": 0,
        "unmeasured_mask_samples": 0,
        "class_counts_boxes": {},
        "class_counts_masks": {},
        "rejected_counts": {},
        "geo_kinds": {},
        "geo_records": 0,
        "keypoints": 0,
        "groups": 0,
    }
    erro: str | None = None
    varreu = False
    varredura_bloqueada: str | None = None
    # A causa precisa ser a causa. Antes, qualquer motivo para não varrer caía na
    # mesma mensagem de cloud-only: um adaptador que levantava AdapterError com
    # zero marcador do OneDrive produzia um relatório culpando o OneDrive, e o
    # erro real ficava escondido atrás de um problema de hidratação inventado.
    resultado = "SUCCESS"

    legivel = inventory.state in {DatasetState.READY, DatasetState.DECLARED_ONLY}

    # Fail-safe do §4.4: marcador do OneDrive presente ⇒ a fonte NÃO é legível
    # localmente, e varrê-la baixaria o conteúdo. Este módulo promete não baixar
    # dataset; a promessa só vale se a varredura for bloqueada antes de abrir o
    # primeiro arquivo. Bloqueia a fonte inteira: os adaptadores navegam por glob
    # e não têm como pular marcador arquivo a arquivo sem mudar o que medem.
    if nuvem and source.adapter not in DERIVED_ONLY_ADAPTERS:
        resultado = "CLOUD_ONLY_SKIP"
        varredura_bloqueada = (
            f"{nuvem} arquivo(s) cloud-only ({bytes_nuvem} bytes) nesta fonte; "
            "abrir qualquer um dispara download do OneDrive. Varredura bloqueada: "
            "as contagens de anotação ficam em zero por AUSÊNCIA DE MEDIÇÃO, não "
            "por ausência de anotação. Hidrate a fonte sob autorização explícita "
            "para medi-la."
        )

    if source.adapter is None:
        resultado = "NO_ADAPTER"
        varredura_bloqueada = varredura_bloqueada or (
            "fonte sem adaptador declarado: nada a ler"
        )
    elif not legivel and varredura_bloqueada is None:
        resultado = "SOURCE_NOT_READY"
        varredura_bloqueada = (
            f"estado do inventário é {inventory.state}: a fonte não está completa "
            "em disco e a leitura não foi tentada"
        )

    if source.adapter is not None and legivel and varredura_bloqueada is None:
        try:
            dados = _scan(source, inventory.root, limit=limit)
            varreu = True
        except AdapterError as exc:
            # Cadeia de proveniência quebrada, manifesto ausente ou desatualizado.
            # Nada disso tem a ver com hidratação, e chamar de cloud-only mandaria
            # quem for corrigir procurar no lugar errado.
            resultado = "ADAPTER_ERROR"
            erro = f"{type(exc).__name__}: {exc}"
            varredura_bloqueada = str(exc)
        except (OSError, ValueError) as exc:
            resultado = "READ_ERROR"
            erro = f"{type(exc).__name__}: {exc}"
            varredura_bloqueada = f"{type(exc).__name__}: {exc}"

    return SourceProfile(
        dataset_id=source.id,
        title=source.title,
        path=inventory.as_dict()["root"],
        role=str(source.role),
        usage=tuple(u.value for u in source.usage),
        annotation_format=source.annotation_format,
        state=str(inventory.state),
        stored_bytes=inventory.total_bytes,
        file_count=arquivos,
        cloud_only_files=nuvem,
        cloud_only_bytes=bytes_nuvem,
        read_error=erro,
        scan_performed=varreu,
        scan_skipped_reason=varredura_bloqueada,
        scan_outcome=resultado,
        divergences=_check_divergences(
            source,
            dados,
            scanned=varreu,
            scan_outcome=resultado,
            scan_reason=varredura_bloqueada or "",
        ),
        **dados,
    )


# Windows: atributos que marcam arquivo do OneDrive ainda não baixado. Os mesmos
# usados por `scripts/datasets/_core.py`; conferir o atributo não hidrata nada,
# enquanto abrir o arquivo hidrataria.
_RECALL_ON_DATA_ACCESS = 0x00400000
_RECALL_ON_OPEN = 0x00040000
_OFFLINE = 0x00001000
_PLACEHOLDER = _RECALL_ON_DATA_ACCESS | _RECALL_ON_OPEN | _OFFLINE


def _count_files(root: Path) -> tuple[int, int, int]:
    """Total de arquivos, quantos são marcador de nuvem e quantos bytes são."""
    if not root.is_dir():
        return 0, 0, 0
    total = nuvem = bytes_nuvem = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        total += 1
        stat = path.stat()
        if getattr(stat, "st_file_attributes", 0) & _PLACEHOLDER:
            nuvem += 1
            bytes_nuvem += stat.st_size
    return total, nuvem, bytes_nuvem


def _scan(source: DatasetSource, root: Path, *, limit: int | None) -> dict[str, Any]:
    """Percorre os registros uma única vez e conta tudo que o relatório precisa."""
    caixas_por_classe: Counter[str] = Counter()
    mascaras_por_classe: Counter[str] = Counter()
    rejeitados: Counter[str] = Counter()
    tipos_geo: Counter[str] = Counter()
    grupos: set[str] = set()
    samples = images = caixas = mascaras = usable = negativas = fora_escopo = 0
    geo = keypoints = 0
    negativas_nao_verificadas = mascaras_nao_medidas = 0

    registros = read_records(source.adapter, root)
    for index, record in enumerate(registros):
        if limit is not None and index >= limit:
            break
        samples += 1
        grupos.add(record.group)

        if isinstance(record, AnnotatedImage):
            images += 1
            caixas += len(record.boxes)
            caixas_por_classe.update(str(b.urmind_class) for b in record.boxes)
            rejeitados.update(r.source_label for r in record.rejected)
            if record.boxes:
                usable += 1
            elif record.rejected:
                fora_escopo += 1
            elif record.negative_status == NEGATIVE_SEMANTICS_UNVERIFIED:
                # Sem anotação e sem protocolo comprovado: retida, não negativa.
                negativas_nao_verificadas += 1
            else:
                negativas += 1
        elif isinstance(record, MaskSample):
            images += 1
            mascaras += len(record.masks)
            mascaras_por_classe.update(str(k) for k in record.masks)
            rejeitados.update(r.source_label for r in record.rejected)
            if record.masks:
                # Máscara anexada é arquivo existente, não conteúdo medido: o
                # adaptador não abre o PNG. Contar isto como amostra utilizável
                # afirmaria conteúdo que ninguém leu — e no UNIVALI a medição de
                # pixel mostrou máscara vazia em boa parte dos arquivos. Fica
                # como não medida até alguém medir.
                mascaras_nao_medidas += 1
            elif record.rejected:
                fora_escopo += 1
            else:
                negativas += 1
        elif isinstance(record, KeypointSample):
            images += 1
            keypoints += len(record.keypoints)
            rejeitados.update(r.source_label for r in record.rejected)
            # Keypoint não é anotação da V1: não conta como usable nem negativa.
            # Com rótulo recusado a imagem é fora de escopo — tinha anotação e
            # ela não coube no §8.2 —, e é assim que o campo é definido. Sem
            # rótulo nenhum, é negativa.
            if record.rejected:
                fora_escopo += 1
            elif not record.keypoints:
                negativas += 1
        elif isinstance(record, GeoRecord):
            geo += 1
            tipos_geo.update([record.kind])

    return {
        "samples": samples,
        "images": images,
        "box_annotations": caixas,
        "mask_annotations": mascaras,
        "usable_samples": usable,
        "negative_images": negativas,
        "out_of_scope_images": fora_escopo,
        "unverified_negative_images": negativas_nao_verificadas,
        "unmeasured_mask_samples": mascaras_nao_medidas,
        "class_counts_boxes": dict(caixas_por_classe),
        "class_counts_masks": dict(mascaras_por_classe),
        "rejected_counts": dict(rejeitados),
        "geo_kinds": dict(tipos_geo),
        "geo_records": geo,
        "keypoints": keypoints,
        "groups": len(grupos),
    }


def profile_all(
    raw_root: Path | None = None, *, verify: bool = False, limit: int | None = None
) -> tuple[SourceProfile, ...]:
    return tuple(profile_source(s, raw_root, verify=verify, limit=limit) for s in SOURCES)


def totals(profiles: tuple[SourceProfile, ...]) -> dict[str, Any]:
    """Agregados do conjunto, com a separação que dá sentido ao número.

    `trainable_*` conta apenas as fontes que produzem objeto na taxonomia V1. É
    a linha que responde quanto do acervo o detector realmente consegue usar.
    """
    caixas: Counter[str] = Counter()
    mascaras: Counter[str] = Counter()
    for profile in profiles:
        caixas.update(profile.class_counts_boxes)
        mascaras.update(profile.class_counts_masks)

    treinaveis = [p for p in profiles if p.feeds_detector]
    return {
        "sources": len(profiles),
        "stored_bytes": sum(p.stored_bytes for p in profiles),
        "stored_gb_decimal": round(sum(p.stored_bytes for p in profiles) / 1e9, 3),
        "samples": sum(p.samples for p in profiles),
        "box_annotations": sum(p.box_annotations for p in profiles),
        "mask_annotations": sum(p.mask_annotations for p in profiles),
        "class_counts_boxes": dict(sorted(caixas.items())),
        "class_counts_masks": dict(sorted(mascaras.items())),
        "trainable_sources": sorted(p.dataset_id for p in treinaveis),
        "trainable_stored_bytes": sum(p.stored_bytes for p in treinaveis),
        "trainable_stored_gb_decimal": round(
            sum(p.stored_bytes for p in treinaveis) / 1e9, 3
        ),
        "trainable_usable_samples": sum(p.usable_samples for p in treinaveis),
        # Fonte não medida não é fonte sem anotação. Separar as duas impede que
        # um total agregado apresente ausência de medição como resultado.
        "unmeasured_sources": sorted(
            p.dataset_id for p in profiles if p.scan_skipped_reason is not None
        ),
        "unmeasured_reasons": {
            p.dataset_id: p.scan_outcome for p in profiles if p.scan_skipped_reason
        },
        "non_contributing_sources": sorted(
            p.dataset_id for p in profiles if not p.feeds_detector
        ),
        "non_contributing_stored_gb_decimal": round(
            sum(p.stored_bytes for p in profiles if not p.feeds_detector) / 1e9, 3
        ),
        "cloud_only_files": sum(p.cloud_only_files for p in profiles),
        "cloud_only_gb_decimal": round(
            sum(p.cloud_only_bytes for p in profiles) / 1e9, 3
        ),
        "sources_not_fully_local": sorted(
            p.dataset_id for p in profiles if p.cloud_only_files
        ),
        "divergences": {
            p.dataset_id: list(p.divergences) for p in profiles if p.divergences
        },
        "read_errors": {p.dataset_id: p.read_error for p in profiles if p.read_error},
    }
