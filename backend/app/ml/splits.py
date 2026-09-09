"""Split de dataset por grupo, sem vazamento (MASTER_PLAN §8.3 passo 4 e §8.4).

O §8.4 explica o problema com uma frase que vale mais que qualquer métrica:
*frames vizinhos são quase duplicados*. Se um frame cai no treino e o seguinte
no teste, o modelo é avaliado em cima de uma cena que ele já viu, e a métrica
sobe sozinha — sem que o detector tenha ficado melhor em nada.

A defesa é não sortear frames, e sim **grupos**: sessão, rota, local ou origem.
Um grupo inteiro vai para um único split, nunca se divide. É por isso que este
módulo não tem uma função "split aleatório": ela seria a forma mais fácil de
produzir um número bonito e falso.

O resultado é determinístico dada a mesma semente, para que o split possa ser
registrado em `dataset_versions.split` e reproduzido depois (§10.2).
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

__all__ = ["DatasetSplit", "SplitRatios", "find_leakage", "split_by_group"]


@dataclass(frozen=True)
class SplitRatios:
    """Proporções alvo. São alvo, não garantia: grupos são indivisíveis."""

    train: float = 0.70
    validation: float = 0.15
    test: float = 0.15

    def __post_init__(self) -> None:
        total = self.train + self.validation + self.test
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"as proporções precisam somar 1,0; somaram {total}")
        if min(self.train, self.validation, self.test) < 0:
            raise ValueError("proporção negativa não faz sentido")

    def as_dict(self) -> dict[str, float]:
        return {"train": self.train, "validation": self.validation, "test": self.test}


@dataclass(frozen=True)
class DatasetSplit:
    """Os três conjuntos e a prestação de contas de como ficaram."""

    train: list
    validation: list
    test: list
    groups: dict[str, list[str]]
    """Quais grupos caíram em cada split. É isto que vai para `dataset_versions`."""

    achieved_ratios: dict[str, float]
    """Proporção real obtida. Difere do alvo quando há poucos grupos ou grupos grandes."""

    def __len__(self) -> int:
        return len(self.train) + len(self.validation) + len(self.test)

    @property
    def empty_splits(self) -> list[str]:
        """Splits que ficaram sem nenhum item.

        Acontece quando há menos grupos que splits, ou quando poucos grupos
        muito grandes esgotam as cotas. O §8.3 precisa dos três conjuntos:
        treino para ajustar, validação para escolher e teste congelado para
        medir. Um deles vazio invalida a etapa, então isso não pode passar
        despercebido.
        """
        return [
            name
            for name, members in (
                ("train", self.train),
                ("validation", self.validation),
                ("test", self.test),
            )
            if not members
        ]

    @property
    def warnings(self) -> list[str]:
        """Problemas que não impedem o split, mas invalidam o uso dele."""
        problems: list[str] = []
        if not len(self):
            return problems
        vazios = self.empty_splits
        if vazios:
            total_grupos = sum(len(items) for items in self.groups.values())
            problems.append(
                f"split(s) sem nenhum item: {', '.join(vazios)}; "
                f"há apenas {total_grupos} grupo(s) e grupos não se dividem (§8.4). "
                "Agrupe por um recorte mais fino ou colete mais sessões/rotas."
            )
        return problems

    def summary(self) -> dict[str, object]:
        """Formato compacto para registrar em `dataset_versions.split`."""
        return {
            "counts": {
                "train": len(self.train),
                "validation": len(self.validation),
                "test": len(self.test),
            },
            "groups": {name: sorted(items) for name, items in self.groups.items()},
            "achieved_ratios": self.achieved_ratios,
            "warnings": self.warnings,
        }


def split_by_group[T](
    items: Iterable[T],
    group_key: Callable[[T], str],
    ratios: SplitRatios | None = None,
    seed: int = 20260906,
) -> DatasetSplit:
    """Distribui itens em train/validation/test sem quebrar nenhum grupo.

    `group_key` extrai a chave que não pode se dividir: id da sessão de coleta,
    da rota, do local ou da origem do dataset. Frames da mesma cena precisam
    devolver a mesma chave — é isso que impede o vazamento do §8.4.

    A distribuição é gulosa: grupos maiores primeiro, cada um para o split que
    está mais longe da própria cota. Com poucos grupos as proporções obtidas
    ficam longe do alvo, e `achieved_ratios` mostra isso em vez de esconder.
    """
    ratios = ratios or SplitRatios()

    buckets: dict[str, list[T]] = defaultdict(list)
    for item in items:
        buckets[group_key(item)].append(item)

    if not buckets:
        return DatasetSplit(
            train=[],
            validation=[],
            test=[],
            groups={"train": [], "validation": [], "test": []},
            achieved_ratios={"train": 0.0, "validation": 0.0, "test": 0.0},
        )

    total = sum(len(members) for members in buckets.values())
    targets = {name: fraction * total for name, fraction in ratios.as_dict().items()}

    # Embaralha antes de ordenar para que grupos de mesmo tamanho não fiquem
    # sempre na mesma ordem de inserção; a semente mantém tudo reproduzível.
    names = list(buckets)
    random.Random(seed).shuffle(names)
    names.sort(key=lambda name: len(buckets[name]), reverse=True)

    assigned: dict[str, list[T]] = {"train": [], "validation": [], "test": []}
    assigned_groups: dict[str, list[str]] = {"train": [], "validation": [], "test": []}

    for name in names:
        members = buckets[name]
        # Split com maior déficit em relação à própria cota recebe o grupo.
        # O desempate por nome mantém o resultado estável entre execuções.
        destination = min(
            ("train", "validation", "test"),
            key=lambda split: (len(assigned[split]) - targets[split], split),
        )
        assigned[destination].extend(members)
        assigned_groups[destination].append(name)

    achieved = {
        name: round(len(members) / total, 4) for name, members in assigned.items()
    }

    return DatasetSplit(
        train=assigned["train"],
        validation=assigned["validation"],
        test=assigned["test"],
        groups=assigned_groups,
        achieved_ratios=achieved,
    )


def find_leakage(split: DatasetSplit) -> dict[str, list[str]]:
    """Grupos que aparecem em mais de um split. Vazio é o único resultado aceitável.

    Serve como verificação independente antes de congelar o conjunto de teste
    (§8.3 passo 7): a garantia não fica só na implementação do split, ela é
    conferida no dado produzido.
    """
    seen: dict[str, list[str]] = defaultdict(list)
    for split_name, group_names in split.groups.items():
        for name in group_names:
            seen[name].append(split_name)
    return {name: splits for name, splits in seen.items() if len(splits) > 1}


def group_by_prefix(separator: str = "/", parts: int = 1) -> Callable[[str], str]:
    """Extrator pronto para caminhos como `japan/rota_12/frame_0007.jpg`.

    Com `parts=2` o grupo passa a ser `japan/rota_12`: país e rota juntos, que é
    o recorte por origem/rota que o §8.3 passo 4 pede.
    """

    def extract(path: str) -> str:
        segments: Sequence[str] = path.split(separator)
        return separator.join(segments[:parts])

    return extract
