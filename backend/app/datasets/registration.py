"""De pasta no disco para linha em `dataset_versions` (§8.3 passo 1, §10.2).

O §8.3 diz que datasets **aprovados** são registrados. Aprovado tem significado
concreto aqui: os arquivos obrigatórios estão completos no disco, os rótulos
foram traduzidos pela taxonomia do §8.2 e o split foi feito por grupo (§8.4).
Faltando qualquer um dos três, `build_dataset_version` recusa e diz o motivo —
registrar um download pela metade produziria uma linha de banco que mente sobre
o que foi treinado.

O `split` gravado no JSONB não é decoração: ele é o que permite repetir o
recorte depois. Guarda a estratégia, a semente, quais grupos caíram em cada
lado e a contagem por classe, para que um resultado publicado possa ser
reproduzido ou contestado.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.datasets.catalog import DatasetRole, DatasetSource
from app.datasets.inventory import DatasetInventory, DatasetState
from app.datasets.records import AnnotatedImage, GeoRecord, KeypointSample, MaskSample
from app.ml.splits import DatasetSplit

__all__ = [
    "DatasetSummary",
    "RegistrationRefused",
    "build_dataset_version",
    "summarize",
]


class RegistrationRefused(RuntimeError):
    """A fonte não está em condição de virar `dataset_version`."""


@dataclass(frozen=True)
class DatasetSummary:
    """O que a leitura encontrou. Aceito e recusado, lado a lado."""

    dataset_id: str
    total_samples: int
    usable_samples: int
    class_counts: dict[str, int] = field(default_factory=dict)
    rejected_counts: dict[str, int] = field(default_factory=dict)
    groups: tuple[str, ...] = ()

    @property
    def classes(self) -> list[str]:
        return sorted(self.class_counts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "total_samples": self.total_samples,
            "usable_samples": self.usable_samples,
            "class_counts": dict(sorted(self.class_counts.items())),
            "rejected_counts": dict(sorted(self.rejected_counts.items())),
            "group_count": len(self.groups),
            "groups": list(self.groups),
        }


def summarize(
    dataset_id: str,
    records: Iterable[AnnotatedImage | MaskSample | KeypointSample | GeoRecord],
) -> DatasetSummary:
    """Percorre os registros uma vez e conta o que importa para o registro."""
    total = usable = 0
    classes: Counter[str] = Counter()
    rejeitados: Counter[str] = Counter()
    grupos: set[str] = set()

    for record in records:
        total += 1
        grupos.add(record.group)

        if isinstance(record, AnnotatedImage):
            classes.update(str(b.urmind_class) for b in record.boxes)
            rejeitados.update(r.source_label for r in record.rejected)
            usable += int(record.usable)
        elif isinstance(record, MaskSample):
            classes.update(str(k) for k in record.masks)
            rejeitados.update(r.source_label for r in record.rejected)
            usable += int(record.usable)
        elif isinstance(record, KeypointSample):
            rejeitados.update(r.source_label for r in record.rejected)
        else:  # GeoRecord: não tem classe do UrMind, por definição
            rejeitados.update([record.kind])

    return DatasetSummary(
        dataset_id=dataset_id,
        total_samples=total,
        usable_samples=usable,
        class_counts=dict(classes),
        rejected_counts=dict(rejeitados),
        groups=tuple(sorted(grupos)),
    )


def build_dataset_version(
    source: DatasetSource,
    inventory: DatasetInventory,
    summary: DatasetSummary,
    split: DatasetSplit | None = None,
    *,
    require_split: bool = True,
) -> dict[str, Any]:
    """Monta o payload de `dataset_versions`, ou recusa explicando o que falta.

    `require_split` só é dispensado para fontes `GEO_REFERENCE`: elas não geram
    conjunto de treino, então não há o que separar em train/validation/test.
    """
    if source.role is DatasetRole.DEFERRED:
        raise RegistrationRefused(
            f"{source.id} está declarado como adiado no catálogo; "
            "não há adaptador nem protocolo definido para registrá-lo"
        )

    # Fonte de treino precisa dos arquivos publicados completos. Fonte
    # geográfica costuma ser exportação sob demanda — não há ZIP oficial a
    # esperar, e `DECLARED_ONLY` já significa "o que existe é o que a fonte dá".
    aceitos = (
        {DatasetState.READY}
        if source.role is DatasetRole.TRAINING_V1
        else {DatasetState.READY, DatasetState.DECLARED_ONLY}
    )
    if inventory.state not in aceitos:
        motivos = "; ".join(inventory.blocking_reasons()) or f"estado {inventory.state}"
        raise RegistrationRefused(f"{source.id} não está completo no disco: {motivos}")

    if source.role is DatasetRole.TRAINING_V1:
        if summary.usable_samples == 0:
            raise RegistrationRefused(
                f"{source.id}: nenhuma amostra com classe da taxonomia V1; "
                "registrar produziria uma versão sem nada treinável"
            )
        if require_split and split is None:
            raise RegistrationRefused(
                f"{source.id}: split por grupo é obrigatório antes do registro (§8.4)"
            )
        # Conjunto vazio normalmente invalida a etapa: o §8.3 precisa dos três.
        # A exceção é a fonte proibida de avaliar — nela, validação e teste
        # vazios são a consequência pretendida da proibição, não uma falha.
        # Treino vazio continua sendo falha em qualquer caso.
        vazios = set(split.empty_splits) if split is not None else set()
        if split is not None:
            vazios -= set(split.expected_empty)
        if source.evaluation_forbidden:
            vazios -= {"validation", "test"}
        if vazios:
            raise RegistrationRefused(
                f"{source.id}: split(s) sem nenhum item: {', '.join(sorted(vazios))}"
            )

    split_payload: dict[str, Any] = {
        "role": str(source.role),
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": summary.as_dict(),
        "inventory": inventory.as_dict(),
        "taxonomy_note": source.taxonomy_note,
        "group_note": source.group_note,
        "caveats": list(source.caveats),
    }
    if split is not None:
        split_payload["split"] = split.summary()

    return {
        "name": source.id,
        "version": source.version,
        "source": source.homepage,
        "license": source.license,
        "classes": summary.classes,
        "split": split_payload,
        "dvc_revision": None,  # preenchido quando o remote DVC do §10.2 existir
    }
