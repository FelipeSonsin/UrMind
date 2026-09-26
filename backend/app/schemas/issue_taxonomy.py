"""Registro canônico `urmind-issue-taxonomy-v3` (candidatas, sem ativação visual).

Este módulo declara *o que o UrMind pretende reconhecer*, não o que ele já
reconhece. A diferença é o campo `model_support_status`:

- D00/D10/D20/D40 continuam as únicas classes com modelo, e o único modelo
  existente é `EXPERIMENTAL_SHADOW` rejeitado no Frozen Test. Por isso elas são
  `EXPERIMENTAL_MODEL`, nunca `ACTIVE_MODEL`.
- Toda classe nova nasce `DATA_REQUIRED`: sem dataset curado, protocolo de
  anotação e validação, nenhum detector pode emiti-la (`model_may_emit`).

Exemplos reais enviados por usuários (por exemplo, a foto de árvore caída) só
podem virar `candidate_real_world_example` depois de revisão humana. Isso não
cria Ground Truth, autorização de treino nem prova de capacidade do modelo.

O `UrmindClass` do V1 fica intacto. Eventos históricos D00–D40 não são
reclassificados por esta taxonomia.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from app.schemas.core import UrmindClass

__all__ = [
    "ISSUES",
    "TAXONOMY_VERSION",
    "DatasetReadiness",
    "IssueDefinition",
    "IssueFamily",
    "ModelSupportStatus",
    "ResponsibilityDomain",
    "TaxonomyReviewStatus",
    "get_issue",
    "model_may_emit",
    "taxonomy_payload",
]

TAXONOMY_VERSION = "urmind-issue-taxonomy-v3"


class IssueFamily(StrEnum):
    ROAD_SURFACE = "ROAD_SURFACE"
    VEGETATION_OBSTRUCTION = "VEGETATION_OBSTRUCTION"
    WASTE_OBSTRUCTION = "WASTE_OBSTRUCTION"
    DRAINAGE = "DRAINAGE"
    PEDESTRIAN_INFRASTRUCTURE = "PEDESTRIAN_INFRASTRUCTURE"
    TRAFFIC_INFRASTRUCTURE = "TRAFFIC_INFRASTRUCTURE"
    URBAN_INFRASTRUCTURE = "URBAN_INFRASTRUCTURE"


class ModelSupportStatus(StrEnum):
    ACTIVE_MODEL = "ACTIVE_MODEL"
    EXPERIMENTAL_MODEL = "EXPERIMENTAL_MODEL"
    DATA_REQUIRED = "DATA_REQUIRED"
    REVIEW_ONLY = "REVIEW_ONLY"
    DISABLED = "DISABLED"


class ResponsibilityDomain(StrEnum):
    """Technical routing domain; never the identity of a responsible agency."""

    ROAD_MAINTENANCE = "ROAD_MAINTENANCE"
    URBAN_FORESTRY = "URBAN_FORESTRY"
    DRAINAGE = "DRAINAGE"
    URBAN_CLEANING = "URBAN_CLEANING"
    PEDESTRIAN_INFRASTRUCTURE = "PEDESTRIAN_INFRASTRUCTURE"
    TRAFFIC_AUTHORITY = "TRAFFIC_AUTHORITY"
    PUBLIC_LIGHTING = "PUBLIC_LIGHTING"
    CIVIL_DEFENSE = "CIVIL_DEFENSE"
    GENERAL_INSPECTION = "GENERAL_INSPECTION"


_LEGACY_DOMAINS = {
    "pavement": ResponsibilityDomain.ROAD_MAINTENANCE,
    "urban_forestry": ResponsibilityDomain.URBAN_FORESTRY,
    "solid_waste": ResponsibilityDomain.URBAN_CLEANING,
    "road_operations": ResponsibilityDomain.ROAD_MAINTENANCE,
    "drainage_sewer": ResponsibilityDomain.DRAINAGE,
    "sidewalk": ResponsibilityDomain.PEDESTRIAN_INFRASTRUCTURE,
    "traffic_signage": ResponsibilityDomain.TRAFFIC_AUTHORITY,
    "street_lighting": ResponsibilityDomain.PUBLIC_LIGHTING,
}

# Applicability is catalog metadata, not a claim that a context fact was observed.
_FAMILY_CONTEXT = {
    IssueFamily.ROAD_SURFACE: (
        "near_school",
        "near_health_unit",
        "crossing_nearby",
        "previous_events_same_segment",
    ),
    IssueFamily.VEGETATION_OBSTRUCTION: ("crossing_nearby", "near_school"),
    IssueFamily.WASTE_OBSTRUCTION: ("crossing_nearby", "near_health_unit"),
    IssueFamily.DRAINAGE: ("rain_mm_24h", "previous_events_same_segment"),
    IssueFamily.PEDESTRIAN_INFRASTRUCTURE: ("crossing_nearby", "near_school", "near_health_unit"),
    IssueFamily.TRAFFIC_INFRASTRUCTURE: ("crossing_nearby", "near_school"),
    IssueFamily.URBAN_INFRASTRUCTURE: ("crossing_nearby", "previous_events_same_segment"),
}


class DatasetReadiness(StrEnum):
    """Estado do dado para a classe. `CURATED_IN_USE` só vale para dado já curado."""

    CURATED_IN_USE = "CURATED_IN_USE"
    READY_FOR_CURATION = "READY_FOR_CURATION"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"
    NEEDS_MORE_DATA = "NEEDS_MORE_DATA"
    LICENSE_BLOCKED = "LICENSE_BLOCKED"
    DOMAIN_MISMATCH = "DOMAIN_MISMATCH"
    NOT_USABLE = "NOT_USABLE"


class TaxonomyReviewStatus(StrEnum):
    """Revisão humana da *definição* da classe, não de amostras."""

    REVIEWED = "REVIEWED"
    PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"


# Estados em que um detector pode produzir Detection desta classe.
_EMITTABLE = frozenset({ModelSupportStatus.ACTIVE_MODEL, ModelSupportStatus.EXPERIMENTAL_MODEL})


@dataclass(frozen=True)
class IssueDefinition:
    issue_code: str
    family: IssueFamily
    display_name_pt: str
    display_name_en: str
    description: str
    visual_definition: str
    included_examples: tuple[str, ...]
    excluded_examples: tuple[str, ...]
    model_support_status: ModelSupportStatus
    dataset_status: DatasetReadiness
    review_status: TaxonomyReviewStatus
    # Domínio técnico do ativo. Não é órgão responsável: quem responde continua
    # saindo de `responsibility_rules` (§14.3).
    responsibility_domain: ResponsibilityDomain
    legacy_responsibility_domain: str
    possible_impact_domains: tuple[str, ...]
    applicable_context_features: tuple[str, ...]
    version: int = 1
    taxonomy_version: str = TAXONOMY_VERSION
    # Códigos V1 relacionados, só para rastreabilidade. Nada é reclassificado.
    related_legacy_codes: tuple[str, ...] = ()
    risk_groups: tuple[str, ...] = ("safety", "mobility", "accessibility")
    photo_detectable: bool | str = "limited"
    limitations: tuple[str, ...] = (
        "Necessita revisão; foto isolada não comprova causa ou extensão.",
    )
    triage_priority_hint: str | None = None


def _road(
    code: UrmindClass, pt: str, en: str, visual: str, inc: tuple[str, ...], exc: tuple[str, ...]
) -> IssueDefinition:
    return IssueDefinition(
        issue_code=code.value,
        family=IssueFamily.ROAD_SURFACE,
        display_name_pt=pt,
        display_name_en=en,
        description=f"Categoria RDD2022 preservada da taxonomia V1 ({code.value}).",
        visual_definition=visual,
        included_examples=inc,
        excluded_examples=exc,
        model_support_status=ModelSupportStatus.EXPERIMENTAL_MODEL,
        dataset_status=DatasetReadiness.CURATED_IN_USE,
        review_status=TaxonomyReviewStatus.REVIEWED,
        responsibility_domain=ResponsibilityDomain.ROAD_MAINTENANCE,
        legacy_responsibility_domain="pavement",
        possible_impact_domains=("mobility", "infrastructure"),
        applicable_context_features=_FAMILY_CONTEXT[IssueFamily.ROAD_SURFACE],
    )


def _candidate(
    code: str,
    family: IssueFamily,
    pt: str,
    en: str,
    description: str,
    visual: str,
    inc: tuple[str, ...],
    exc: tuple[str, ...],
    dataset_status: DatasetReadiness,
    domain: str,
    legacy: tuple[str, ...] = (),
    *,
    photo_detectable: bool | str = "limited",
    limitations: tuple[str, ...] = ("Necessita revisão; evidência fotográfica limitada.",),
    triage_priority_hint: str | None = None,
) -> IssueDefinition:
    return IssueDefinition(
        issue_code=code,
        family=family,
        display_name_pt=pt,
        display_name_en=en,
        description=description,
        visual_definition=visual,
        included_examples=inc,
        excluded_examples=exc,
        model_support_status=ModelSupportStatus.DATA_REQUIRED,
        dataset_status=dataset_status,
        review_status=TaxonomyReviewStatus.PENDING_HUMAN_REVIEW,
        responsibility_domain=_LEGACY_DOMAINS[domain],
        legacy_responsibility_domain=domain,
        possible_impact_domains=("mobility", "infrastructure"),
        applicable_context_features=_FAMILY_CONTEXT[family],
        related_legacy_codes=legacy,
        photo_detectable=photo_detectable,
        limitations=limitations,
        triage_priority_hint=triage_priority_hint,
    )


_F = IssueFamily
_D = DatasetReadiness

ISSUES: tuple[IssueDefinition, ...] = (
    _road(
        UrmindClass.ROAD_D00,
        "Trinca longitudinal",
        "Longitudinal crack",
        "Fissura contínua aproximadamente paralela ao sentido da via.",
        ("trinca na trilha de roda", "trinca ao longo da faixa"),
        ("junta de construção (D01)", "faixa pintada apagada"),
    ),
    _road(
        UrmindClass.ROAD_D10,
        "Trinca transversal",
        "Transverse crack",
        "Fissura aproximadamente perpendicular ao sentido da via.",
        ("trinca atravessando a faixa",),
        ("junta de construção (D11)", "sombra de poste"),
    ),
    _road(
        UrmindClass.ROAD_D20,
        "Trinca em malha",
        "Alligator crack",
        "Rede de fissuras interligadas formando blocos pequenos.",
        ("área de fissuras em malha",),
        ("trinca em bloco (Block crack)", "remendo executado"),
    ),
    _road(
        UrmindClass.ROAD_D40,
        "Buraco",
        "Pothole",
        "Depressão com perda de material do pavimento e borda definida.",
        ("buraco com ou sem água", "panela no pavimento"),
        ("tampa de poço de visita (D50)", "remendo executado", "bueiro aberto"),
    ),
    _candidate(
        "URMIND_FALLEN_TREE",
        _F.VEGETATION_OBSTRUCTION,
        "Árvore caída",
        "Fallen tree",
        "Árvore tombada ou tronco principal caído sobre via, calçada ou rede.",
        "Tronco principal fora da vertical, total ou parcialmente sobre a área de circulação.",
        ("árvore tombada sobre a pista", "tronco caído sobre a calçada"),
        ("galho isolado (FALLEN_BRANCH)", "árvore em pé inclinada", "poda empilhada"),
        _D.NEEDS_HUMAN_REVIEW,
        "urban_forestry",
    ),
    _candidate(
        "URMIND_FALLEN_BRANCH",
        _F.VEGETATION_OBSTRUCTION,
        "Galho caído",
        "Fallen branch",
        "Galho solto obstruindo parcialmente a circulação.",
        "Galho destacado da árvore, no chão da via ou calçada.",
        ("galho grande sobre a faixa",),
        ("árvore inteira caída (FALLEN_TREE)", "folhas soltas", "resíduo de poda ensacado"),
        _D.NEEDS_MORE_DATA,
        "urban_forestry",
    ),
    _candidate(
        "URMIND_ILLEGAL_DUMPING",
        _F.WASTE_OBSTRUCTION,
        "Descarte irregular",
        "Illegal dumping",
        "Acúmulo de resíduos ou entulho fora de ponto de coleta.",
        "Pilha de resíduos, entulho ou volumosos em via/calçada, fora de contêiner.",
        ("entulho na calçada", "sofá abandonado na via"),
        ("lixeira pública cheia", "contêiner de coleta", "lixo isolado pequeno"),
        _D.NEEDS_HUMAN_REVIEW,
        "solid_waste",
    ),
    _candidate(
        "URMIND_ROAD_DEBRIS",
        _F.WASTE_OBSTRUCTION,
        "Detrito na pista",
        "Road debris",
        "Objeto solto sobre a pista que pode causar acidente.",
        "Objeto discreto sobre a superfície de rolamento, sem fazer parte da via.",
        ("pneu solto na pista", "carga caída"),
        ("buraco (D40)", "tampa de poço no nível", "veículo estacionado"),
        _D.NEEDS_HUMAN_REVIEW,
        "road_operations",
    ),
    _candidate(
        "URMIND_OPEN_MANHOLE",
        _F.DRAINAGE,
        "Bueiro/poço aberto",
        "Open manhole",
        "Poço de visita ou boca de lobo sem tampa, com abertura exposta.",
        "Abertura escura circular/retangular no piso, sem tampa encaixada.",
        ("poço sem tampa na pista", "boca de lobo sem grelha"),
        ("tampa fechada (D50 do RDD)", "buraco no pavimento (D40)", "tampa desnivelada"),
        _D.NEEDS_HUMAN_REVIEW,
        "drainage_sewer",
        legacy=(UrmindClass.MANHOLE.value,),
    ),
    _candidate(
        "URMIND_BLOCKED_DRAIN",
        _F.DRAINAGE,
        "Boca de lobo obstruída",
        "Blocked drain",
        "Dispositivo de drenagem com entrada obstruída por resíduos ou sedimentos.",
        "Grelha/boca de lobo visível com a abertura coberta.",
        ("boca de lobo tomada por folhas e lixo",),
        ("boca de lobo livre", "via alagada sem drenagem visível (FLOODED_ROAD)"),
        _D.NEEDS_MORE_DATA,
        "drainage_sewer",
    ),
    _candidate(
        "URMIND_FLOODED_ROAD",
        _F.DRAINAGE,
        "Via alagada",
        "Flooded road",
        "Água acumulada cobrindo a superfície da via.",
        "Lâmina d'água contínua sobre a pista, a partir de câmera ao nível da rua.",
        ("rua com lâmina d'água cobrindo a faixa",),
        ("poça em buraco (D40)", "pista molhada sem acúmulo", "imagem aérea/drone"),
        _D.NEEDS_HUMAN_REVIEW,
        "drainage_sewer",
    ),
    _candidate(
        "URMIND_SIDEWALK_DAMAGE",
        _F.PEDESTRIAN_INFRASTRUCTURE,
        "Calçada danificada",
        "Sidewalk damage",
        "Piso de calçada quebrado, afundado ou com desnível perigoso.",
        "Placas soltas, quebradas ou desniveladas no passeio.",
        ("placa de concreto levantada por raiz", "buraco no passeio"),
        ("obstrução móvel (SIDEWALK_OBSTRUCTION)", "rampa de acessibilidade íntegra"),
        _D.NEEDS_MORE_DATA,
        "sidewalk",
        legacy=(UrmindClass.SIDEWALK.value,),
    ),
    _candidate(
        "URMIND_SIDEWALK_OBSTRUCTION",
        _F.PEDESTRIAN_INFRASTRUCTURE,
        "Calçada obstruída",
        "Sidewalk obstruction",
        "Objeto que bloqueia a faixa livre de circulação do passeio.",
        "Objeto ocupando a largura útil da calçada.",
        ("material de obra ocupando a calçada",),
        ("pedestre", "mobiliário urbano regular", "calçada danificada (SIDEWALK_DAMAGE)"),
        _D.NEEDS_MORE_DATA,
        "sidewalk",
        legacy=(UrmindClass.SIDEWALK.value,),
    ),
    _candidate(
        "URMIND_DAMAGED_TRAFFIC_SIGN",
        _F.TRAFFIC_INFRASTRUCTURE,
        "Placa de trânsito danificada",
        "Damaged traffic sign",
        "Placa em pé mas com leitura comprometida (dobrada, pichada, desbotada).",
        "Placa vertical fixada com face fisicamente deformada ou danificada.",
        ("placa entortada", "placa pichada"),
        ("placa caída (FALLEN_TRAFFIC_SIGN)", "placa encoberta sem dano (OBSTRUCTED_TRAFFIC_SIGN)"),
        _D.NEEDS_HUMAN_REVIEW,
        "traffic_signage",
        legacy=(UrmindClass.SIGNAGE.value,),
    ),
    _candidate(
        "URMIND_FALLEN_TRAFFIC_SIGN",
        _F.TRAFFIC_INFRASTRUCTURE,
        "Placa de trânsito caída",
        "Fallen traffic sign",
        "Placa ou suporte tombado, fora da posição de leitura.",
        "Placa/suporte no chão ou fortemente inclinado.",
        ("placa derrubada na calçada",),
        ("placa danificada em pé (DAMAGED_TRAFFIC_SIGN)",),
        _D.NEEDS_MORE_DATA,
        "traffic_signage",
        legacy=(UrmindClass.SIGNAGE.value,),
    ),
    _candidate(
        "URMIND_DAMAGED_STREETLIGHT_POLE",
        _F.URBAN_INFRASTRUCTURE,
        "Poste de iluminação danificado",
        "Damaged streetlight pole",
        "Poste inclinado, quebrado ou com luminária pendurada.",
        "Poste fora da vertical, fraturado ou com componente solto.",
        ("poste inclinado após colisão",),
        ("poste íntegro", "fiação solta sem poste danificado"),
        _D.NEEDS_MORE_DATA,
        "street_lighting",
    ),
    _candidate(
        "URMIND_DAMAGED_BARRIER",
        _F.URBAN_INFRASTRUCTURE,
        "Defensa/barreira danificada",
        "Damaged barrier",
        "Defensa metálica, guarda-corpo ou barreira de concreto danificada.",
        "Barreira deformada, rompida ou deslocada.",
        ("defensa amassada", "guarda-corpo rompido"),
        ("barreira provisória de obra", "cone de sinalização"),
        _D.NEEDS_MORE_DATA,
        "road_operations",
    ),
    _candidate(
        "URMIND_HAZARDOUS_TREE",
        _F.VEGETATION_OBSTRUCTION,
        "Árvore com sinais de risco",
        "Hazardous tree",
        "Árvore em pé inclinada, rachada ou com raízes expostas levantando o pavimento.",
        "Árvore em pé inclinada, rachada ou com raízes expostas levantando o pavimento.",
        ("árvore inclinada com rachadura visível",),
        ("árvore caída (FALLEN_TREE)",),
        _D.NEEDS_MORE_DATA,
        "urban_forestry",
        photo_detectable="limited",
        limitations=("Foto não comprova risco de queda; necessita inspeção especializada.",),
    ),
    _candidate(
        "URMIND_VEGETATION_ON_POWER_LINES",
        _F.VEGETATION_OBSTRUCTION,
        "Vegetação junto à fiação",
        "Vegetation on power lines",
        "Galhos ou vegetação aparentemente em contato com a fiação.",
        "Galhos ou vegetação aparentemente em contato com a fiação.",
        ("galhos junto aos cabos",),
        ("vegetação distante da fiação",),
        _D.NEEDS_MORE_DATA,
        "urban_forestry",
        photo_detectable="limited",
        limitations=(
            "Se houver possível contato com a rede, não se aproxime. Foto não comprova energização.",
        ),
    ),
    _candidate(
        "URMIND_VEGETATION_OBSTRUCTION",
        _F.VEGETATION_OBSTRUCTION,
        "Obstrução por vegetação",
        "Vegetation obstruction",
        "Vegetação bloqueando a calçada ou a visão de sinalização ou cruzamento.",
        "Vegetação bloqueando a calçada ou a visão de sinalização ou cruzamento.",
        ("folhagem cobrindo passagem ou placa",),
        ("árvore caída; obstrução por material de obra",),
        _D.NEEDS_MORE_DATA,
        "urban_forestry",
        photo_detectable=True,
        limitations=(
            "Registrar a vegetação como causa visível; não inferir visibilidade fora do enquadramento.",
        ),
    ),
    _candidate(
        "URMIND_FALLEN_POWER_LINE",
        _F.URBAN_INFRASTRUCTURE,
        "Fio ou cabo caído",
        "Fallen power line",
        "Cabo caído ou pendurado baixo com possível risco à vida.",
        "Cabo caído ou pendurado baixo com possível risco à vida.",
        ("cabo sobre a calçada",),
        ("cabo instalado em altura regular",),
        _D.NEEDS_MORE_DATA,
        "street_lighting",
        photo_detectable="limited",
        limitations=(
            "Se houver suspeita de cabo elétrico caído, não se aproxime nem toque. Foto não comprova energização; encaminhamento especializado pendente de validação.",
        ),
        triage_priority_hint="maximum_pending_validation",
    ),
    _candidate(
        "URMIND_EXPOSED_WIRING",
        _F.URBAN_INFRASTRUCTURE,
        "Fiação exposta",
        "Exposed wiring",
        "Fiação exposta em poste, caixa ou luminária aberta.",
        "Fiação exposta em poste, caixa ou luminária aberta.",
        ("caixa aberta com fios visíveis",),
        ("cabos protegidos por invólucro intacto",),
        _D.NEEDS_MORE_DATA,
        "street_lighting",
        photo_detectable="limited",
        limitations=(
            "Se houver suspeita de fiação exposta, não se aproxime nem toque. Foto não comprova tensão; necessita inspeção.",
        ),
    ),
    _candidate(
        "URMIND_SINKHOLE",
        _F.ROAD_SURFACE,
        "Cratera ou afundamento",
        "Sinkhole",
        "Afundamento ou colapso aparente do pavimento, distinto de buraco superficial.",
        "Afundamento ou colapso aparente do pavimento, distinto de buraco superficial.",
        ("colapso amplo com bordas abatidas",),
        ("buraco superficial D40",),
        _D.NEEDS_MORE_DATA,
        "road_operations",
        photo_detectable="limited",
        limitations=("Foto não mede profundidade nem comprova causa geotécnica.",),
    ),
    _candidate(
        "URMIND_OPEN_TRENCH",
        _F.ROAD_SURFACE,
        "Vala ou obra aberta",
        "Open trench",
        "Vala ou escavação aberta sem proteção ou sinalização visível.",
        "Vala ou escavação aberta sem proteção ou sinalização visível.",
        ("vala atravessando circulação",),
        ("escavação protegida e sinalizada",),
        _D.NEEDS_MORE_DATA,
        "road_operations",
        photo_detectable=True,
        limitations=(
            "Ausência de proteção só pode ser descrita no enquadramento; necessita revisão.",
        ),
    ),
    _candidate(
        "URMIND_DAMAGED_MANHOLE_COVER",
        _F.DRAINAGE,
        "Tampa de bueiro danificada",
        "Damaged manhole cover",
        "Tampa quebrada, afundada ou desnivelada ainda presente.",
        "Tampa quebrada, afundada ou desnivelada ainda presente.",
        ("tampa partida ou inclinada",),
        ("abertura sem tampa (OPEN_MANHOLE)",),
        _D.NEEDS_MORE_DATA,
        "drainage_sewer",
        photo_detectable=True,
        limitations=("Não inferir estabilidade da tampa; foto não mede desnível.",),
    ),
    _candidate(
        "URMIND_FADED_ROAD_MARKING",
        _F.TRAFFIC_INFRASTRUCTURE,
        "Sinalização horizontal apagada",
        "Faded road marking",
        "Faixa de pedestre ou marcação viária aparentemente desgastada.",
        "Faixa de pedestre ou marcação viária aparentemente desgastada.",
        ("faixa com pintura descontínua por desgaste",),
        ("trinca transversal D10; reflexo",),
        _D.NEEDS_MORE_DATA,
        "traffic_signage",
        photo_detectable="limited",
        limitations=("Iluminação e perspectiva podem ocultar pintura; confirmar em revisão.",),
    ),
    _candidate(
        "URMIND_ROAD_EROSION",
        _F.ROAD_SURFACE,
        "Erosão na via ou encosta",
        "Road erosion",
        "Perda aparente de material na borda da via ou encosta.",
        "Perda aparente de material na borda da via ou encosta.",
        ("borda da pista erodida",),
        ("buraco isolado no pavimento D40",),
        _D.NEEDS_MORE_DATA,
        "road_operations",
        photo_detectable="limited",
        limitations=("Foto não comprova estabilidade nem evolução da erosão.",),
    ),
    _candidate(
        "URMIND_DAMAGED_CURB",
        _F.PEDESTRIAN_INFRASTRUCTURE,
        "Meio-fio danificado ou ausente",
        "Damaged curb",
        "Meio-fio quebrado ou com trecho aparentemente ausente.",
        "Meio-fio quebrado ou com trecho aparentemente ausente.",
        ("guia partida junto ao passeio",),
        ("rebaixamento projetado para rampa",),
        _D.NEEDS_MORE_DATA,
        "sidewalk",
        photo_detectable="limited",
        limitations=("Ausência pode ser projeto legítimo; confirmar contexto.",),
    ),
    _candidate(
        "URMIND_MISSING_CURB_RAMP",
        _F.PEDESTRIAN_INFRASTRUCTURE,
        "Travessia sem rampa visível",
        "Missing curb ramp",
        "Ausência aparente de rampa acessível em travessia.",
        "Ausência aparente de rampa acessível em travessia.",
        ("travessia com guia sem rebaixamento visível",),
        ("rampa fora do enquadramento",),
        _D.NEEDS_MORE_DATA,
        "sidewalk",
        photo_detectable="limited",
        limitations=("Foto parcial não comprova ausência em toda a travessia.",),
    ),
    _candidate(
        "URMIND_DAMAGED_TACTILE_PAVING",
        _F.PEDESTRIAN_INFRASTRUCTURE,
        "Piso tátil danificado ou interrompido",
        "Damaged tactile paving",
        "Piso tátil danificado, ausente em continuidade aparente ou interrompido.",
        "Piso tátil danificado, ausente em continuidade aparente ou interrompido.",
        ("placas táteis quebradas",),
        ("pavimento sem requisito tátil confirmado",),
        _D.NEEDS_MORE_DATA,
        "sidewalk",
        photo_detectable="limited",
        limitations=("Não inferir exigência normativa nem continuidade fora da foto.",),
    ),
    _candidate(
        "URMIND_DAMAGED_BUS_STOP",
        _F.URBAN_INFRASTRUCTURE,
        "Ponto de ônibus danificado",
        "Damaged bus stop",
        "Abrigo ou elementos do ponto de ônibus visivelmente danificados.",
        "Abrigo ou elementos do ponto de ônibus visivelmente danificados.",
        ("abrigo quebrado",),
        ("ponto sem abrigo previsto",),
        _D.NEEDS_MORE_DATA,
        "road_operations",
        photo_detectable=True,
        limitations=("Foto não determina órgão responsável nem comprova falha operacional.",),
    ),
    _candidate(
        "URMIND_OBSTRUCTED_TRAFFIC_SIGN",
        _F.TRAFFIC_INFRASTRUCTURE,
        "Placa encoberta",
        "Obstructed traffic sign",
        "Placa encoberta por vegetação, adesivo ou sujeira sem dano estrutural demonstrado.",
        "Placa encoberta por vegetação, adesivo ou sujeira sem dano estrutural demonstrado.",
        ("placa coberta por folhas",),
        ("placa deformada (DAMAGED_TRAFFIC_SIGN)",),
        _D.NEEDS_MORE_DATA,
        "traffic_signage",
        photo_detectable=True,
        limitations=("Distinguir obstrução visível de dano físico; causas podem coexistir.",),
    ),
    _candidate(
        "URMIND_DAMAGED_TRAFFIC_LIGHT",
        _F.TRAFFIC_INFRASTRUCTURE,
        "Semáforo danificado",
        "Damaged traffic light",
        "Semáforo com dano físico aparente ou tombado.",
        "Semáforo com dano físico aparente ou tombado.",
        ("semáforo tombado",),
        ("sinal sem luz em uma foto isolada",),
        _D.NEEDS_MORE_DATA,
        "traffic_signage",
        photo_detectable="limited",
        limitations=(
            "Foto não prova que o semáforo está apagado ou com defeito de funcionamento.",
        ),
    ),
    _candidate(
        "URMIND_ABANDONED_VEHICLE",
        _F.WASTE_OBSTRUCTION,
        "Possível veículo abandonado",
        "Abandoned vehicle",
        "Carcaça ou veículo com sinais visuais compatíveis com abandono.",
        "Carcaça ou veículo com sinais visuais compatíveis com abandono.",
        ("carcaça sem componentes",),
        ("veículo estacionado sem evidência adicional",),
        _D.NEEDS_MORE_DATA,
        "road_operations",
        photo_detectable="limited",
        limitations=(
            "Foto não comprova abandono. Nunca armazenar ou exibir placa; revisão e sanitização necessárias antes de ativação.",
        ),
    ),
    _candidate(
        "URMIND_WATER_LEAK",
        _F.DRAINAGE,
        "Possível vazamento de água",
        "Water leak",
        "Escoamento aparente de água com possível ponto de vazamento.",
        "Escoamento aparente de água com possível ponto de vazamento.",
        ("água saindo de tubulação aparente",),
        ("via alagada sem origem visível (FLOODED_ROAD)",),
        _D.NEEDS_MORE_DATA,
        "drainage_sewer",
        photo_detectable="limited",
        limitations=("Foto não confirma origem, duração, potabilidade nem causa.",),
    ),
)

_BY_CODE: dict[str, IssueDefinition] = {issue.issue_code: issue for issue in ISSUES}


def get_issue(code: str) -> IssueDefinition | None:
    return _BY_CODE.get(code)


def model_may_emit(code: str) -> bool:
    """Fail-closed: classe desconhecida ou sem modelo nunca vira Detection."""
    issue = _BY_CODE.get(code)
    return issue is not None and issue.model_support_status in _EMITTABLE


def taxonomy_payload() -> dict[str, Any]:
    """Forma pública e estável do registro, consumida pelo frontend."""
    return {
        "taxonomy_version": TAXONOMY_VERSION,
        "issues": [
            {
                **asdict(issue),
                "included_examples": list(issue.included_examples),
                "excluded_examples": list(issue.excluded_examples),
                "related_legacy_codes": list(issue.related_legacy_codes),
                "possible_impact_domains": list(issue.possible_impact_domains),
                "applicable_context_features": list(issue.applicable_context_features),
                "risk_groups": list(issue.risk_groups),
                "limitations": list(issue.limitations),
                "model_may_emit": model_may_emit(issue.issue_code),
            }
            for issue in ISSUES
        ],
    }
