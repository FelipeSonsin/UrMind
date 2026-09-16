"""Mapeamento de rótulos externos para a taxonomia canônica (MASTER_PLAN §8.2, §8.3).

O passo 2 do §8.3 é mapear rótulos de dataset externo para as classes do UrMind.
A regra que sustenta este módulo está no §8.2: *não reutilizar uma classe só
porque visualmente "parece parecida"*.

Por isso o mapa é fechado. Um rótulo que o UrMind ainda não treinou não vira
`URMIND_UNKNOWN` nem some da contagem: ele é recusado com o motivo, e quem está
preparando o dataset decide o que fazer. `URMIND_UNKNOWN` é estado de aplicação,
não classe de treino (§31.8) — nenhum rótulo de dataset pode ser mapeado nele.

O RDD2022 traz seis países e mais de 55 mil instâncias, mas apenas quatro
categorias principais entram na V1 [R36][R39]. As demais existem em parte das
edições do dataset e ficam de fora até haver protocolo de anotação próprio.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.core import UrmindClass

__all__ = [
    "MODEL_V1_CANONICAL_CLASS_ORDER",
    "MODEL_V1_CLASS_ORDER",
    "RDD2022_TO_URMIND",
    "REJECTED_RDD2022_LABELS",
    "REJECTED_UNIVALI_LABELS",
    "REJECTED_URBAN_COMMUNITY_LABELS",
    "UNIVALI_TO_URMIND",
    "URBAN_COMMUNITY_CLASS_IDS",
    "URBAN_COMMUNITY_TO_URMIND",
    "LabelMapping",
    "map_dataset_label",
    "map_rdd2022_labels",
    "map_univali_label",
    "map_urban_community_label",
]

# §8.2: as quatro categorias principais do RDD2022 que formam a taxonomia V1.
RDD2022_TO_URMIND: dict[str, UrmindClass] = {
    "D00": UrmindClass.ROAD_D00,  # trinca longitudinal
    "D10": UrmindClass.ROAD_D10,  # trinca transversal
    "D20": UrmindClass.ROAD_D20,  # trinca em malha / alligator
    "D40": UrmindClass.ROAD_D40,  # buraco / pothole
}

MODEL_V1_CLASS_ORDER: tuple[str, ...] = tuple(RDD2022_TO_URMIND)
MODEL_V1_CANONICAL_CLASS_ORDER: tuple[str, ...] = tuple(
    member.value for member in RDD2022_TO_URMIND.values()
)

# Rótulos que aparecem em parte das edições do RDD e que **não** entram na V1.
# Cada um traz o motivo, para a recusa não parecer esquecimento.
#
# As chaves são maiúsculas porque `map_dataset_label` normaliza o rótulo de
# entrada com `.strip().upper()` antes de procurar aqui.
#
# Este mapa é o par executável de `datasets/metadata/class_mapping.yaml`, e os
# dois precisam listar os mesmos rótulos: um rótulo declarado lá e ausente aqui
# continua sendo recusado, mas cai no motivo genérico e perde a explicação que
# alguém escreveu justamente para ela não se perder.
REJECTED_RDD2022_LABELS: dict[str, str] = {
    "D01": "trinca longitudinal em junta de construção; subtipo não separado na V1",
    "D11": "trinca transversal em junta de construção; subtipo não separado na V1",
    "D43": "faixa de pedestre apagada; é sinalização horizontal, não dano de pavimento",
    "D44": "linha de bordo apagada; é sinalização horizontal, não dano de pavimento",
    "D50": (
        "tampa de poço de visita no RDD; não confundir com URMIND_MANHOLE, "
        "que exige dataset e protocolo próprios (§8.2)"
    ),
    "REPAIR": "remendo/reparo já executado; é intervenção, não dano ativo",
    "BLOCK CRACK": (
        "trinca em bloco; aparece só em parte das edições e não corresponde a "
        "nenhuma das quatro categorias da V1"
    ),
    "D0W0": (
        "erro de digitação de D00 presente na anotação oficial; corrigir seria "
        "adivinhar a intenção do anotador, então é recusado e reportado"
    ),
}


@dataclass(frozen=True)
class LabelMapping:
    """Resultado do mapeamento de um rótulo. Recusa é resultado, não exceção."""

    source_label: str
    urmind_class: UrmindClass | None
    accepted: bool
    reason: str | None = None


GENERIC_REJECTION = (
    "rótulo fora da taxonomia V1; exige dataset e protocolo de anotação próprios"
)


def map_dataset_label(
    label: str,
    mapping: dict[str, UrmindClass],
    rejection_reasons: dict[str, str] | None = None,
) -> LabelMapping:
    """Traduz um rótulo externo, ou explica por que ele não entra.

    `rejection_reasons` é o dicionário de motivos da fonte em questão. Quando
    omitido, valem os motivos do RDD2022 — o comportamento histórico deste
    módulo, preservado para quem já chamava a função com dois argumentos.
    """
    normalized = label.strip().upper()

    if normalized in mapping:
        return LabelMapping(
            source_label=label, urmind_class=mapping[normalized], accepted=True
        )

    reasons = REJECTED_RDD2022_LABELS if rejection_reasons is None else rejection_reasons
    reason = reasons.get(normalized, GENERIC_REJECTION)
    return LabelMapping(source_label=label, urmind_class=None, accepted=False, reason=reason)


def map_rdd2022_labels(labels: list[str]) -> list[LabelMapping]:
    """Mapeia uma lista de rótulos do RDD2022 preservando a ordem de entrada."""
    return [map_dataset_label(label, RDD2022_TO_URMIND) for label in labels]


# --------------------------------------------------------------------- UNIVALI
# "Cracks and Potholes in Road Images" (UNIVALI/DNIT). Rotulagem por máscara de
# segmentação, uma máscara por tipo. As máscaras não são caixas: quem quiser
# treinar detecção precisa converter, e a conversão é decisão registrada, não
# efeito colateral deste mapa.
UNIVALI_TO_URMIND: dict[str, UrmindClass] = {
    "POTHOLE": UrmindClass.ROAD_D40,
}

REJECTED_UNIVALI_LABELS: dict[str, str] = {
    "CRACK": (
        "trinca genérica; o dataset não separa longitudinal, transversal e malha, "
        "e o §8.2 proíbe escolher entre D00/D10/D20 por semelhança visual"
    ),
    "LANE": "faixa de rolamento; é delimitação de via, não dano de pavimento",
    "RAW": "é a imagem original do par, não um rótulo",
}


def map_univali_label(label: str) -> LabelMapping:
    """Mapeia um sufixo de máscara do UNIVALI/DNIT (RAW, LANE, CRACK, POTHOLE)."""
    return map_dataset_label(label, UNIVALI_TO_URMIND, REJECTED_UNIVALI_LABELS)


# ------------------------------------------------------- Urban Community Issues
# Dataset YOLO do Kaggle. Não acompanha `data.yaml`: os nomes abaixo foram lidos
# das pastas do próprio pacote e conferidos contra os ids que aparecem nos .txt.
# É inferência documentada, não um mapa oficial da fonte.
URBAN_COMMUNITY_CLASS_IDS: dict[int, str] = {
    0: "animal",
    1: "traffic_lights",
    2: "waste_container",
    3: "pothole",
    4: "cracks",
    5: "open_manhole",
}

URBAN_COMMUNITY_TO_URMIND: dict[str, UrmindClass] = {
    "POTHOLE": UrmindClass.ROAD_D40,
}

REJECTED_URBAN_COMMUNITY_LABELS: dict[str, str] = {
    "CRACKS": (
        "trinca genérica em classe única; não distingue D00/D10/D20 (§8.2)"
    ),
    "OPEN_MANHOLE": (
        "bueiro aberto só vira URMIND_MANHOLE com dataset e protocolo de anotação "
        "próprios (§8.2); 152 imagens de uma classe herdada não substituem isso"
    ),
    "GOOD_ROAD": "classe negativa do pacote original; não é dano nem objeto da V1",
    "ANIMAL": "fora do escopo funcional do UrMind",
    "TRAFFIC_LIGHTS": (
        "semáforo é objeto de sinalização vertical; URMIND_SIGNAGE exige dataset "
        "e protocolo próprios (§8.2)"
    ),
    "WASTE_CONTAINER": "fora do escopo funcional do UrMind",
}


def map_urban_community_label(label: str) -> LabelMapping:
    """Mapeia um nome de classe do Urban Community Issues."""
    return map_dataset_label(label, URBAN_COMMUNITY_TO_URMIND, REJECTED_URBAN_COMMUNITY_LABELS)
