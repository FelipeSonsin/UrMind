"""Quem pode treinar: os portões que uma caixa derivada precisa passar (§8.2, §8.4).

Este módulo existe porque havia **duas** decisões semânticas paralelas sobre a
mesma caixa, e elas discordavam. `scripts/datasets/convert_urban_community.py`
produzia uma derivada com quarentena, duplicatas bloqueadas e ressalvas; e
`app.datasets.adapters.read_urban_community` lia o raw de novo, do zero,
traduzindo `class_id → URMIND_ROAD_D40` por conta própria. O resultado medido
foi exatamente a divergência que se esperaria: a derivada declarava 290 imagens
e 451 caixas candidatas, e o relatório operacional media 300 imagens e 478
caixas D40 — inclusive as dez imagens duplicadas e as cinco em quarentena que a
derivada dizia reter.

Duas pipelines semânticas paralelas não é redundância, é contradição com duas
respostas. Aqui existe **uma** regra, e ela é importada pelos dois lados.

A regra separa quatro coisas que estavam colapsadas em "ACCEPTED":

`geometricamente válida`  — a caixa existe e cabe na imagem. Não diz nada sobre
                            o que ela contém.
`semanticamente candidata` — o rótulo de origem corresponde à definição da
                            classe. É afirmação sobre a CLASSE, não sobre esta
                            caixa.
`semanticamente validada` — uma pessoa olhou ESTA caixa e disse que é um buraco.
`aprovada para treino`    — passou por todos os portões acima e mais os de
                            duplicata, quarentena e contaminação cruzada.

Uma caixa pode ser geometricamente perfeita e não valer nada como rótulo: foi o
que a inspeção encontrou na pasta `pothole`, que traz trinca sem cavidade, bueiro
aberto e imagens com buracos visíveis sem anotação. Geometria não vê conteúdo.

Nada aqui é otimista. Portão sem resposta **reprova** — ausência de evidência
não é evidência de ausência de problema.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "DUPLICATE_DECISIONS",
    "DUPLICATE_DIFFERENT",
    "DUPLICATE_INVALID",
    "DUPLICATE_KEPT",
    "DUPLICATE_NOT_IN_PAIR",
    "DUPLICATE_PENDING",
    "DUPLICATE_REJECTED",
    "DUPLICATE_REJECTION_REASON",
    "DUPLICATE_RESOLVED",
    "DUPLICATE_STALE",
    "HUMAN_DECISIONS",
    "HUMAN_REVIEW_PENDING",
    "HUMAN_REVIEW_STALE",
    "REQUIRED_GATES",
    "SEMANTIC_CANDIDATE",
    "SEMANTIC_HEURISTIC_QUARANTINE",
    "SEMANTIC_REJECTED",
    "AuthorizationError",
    "GateResult",
    "authorize_rows",
    "evaluate_gates",
    "human_review_status_of",
    "resolve_duplicate_pair",
    "resolve_duplicate_reviews",
]

# Vocabulário fechado da revisão humana. Fechado porque resposta livre não se
# agrega, e porque "aprovado" precisa significar a mesma coisa para todo revisor.
HUMAN_DECISIONS: tuple[str, ...] = (
    "approved_pothole",
    "wrong_class",
    "annotation_incomplete",
    "bad_box",
    "ambiguous",
    "duplicate",
    "other",
)

HUMAN_REVIEW_PENDING = "PENDING"
"""Ninguém olhou ainda. Não é dúvida sobre a caixa: é ausência de revisão."""

HUMAN_REVIEW_STALE = "STALE_HUMAN_REVIEW"
"""Havia decisão, e ela não é mais desta caixa.

O portão trata como pendente — que é o comportamento correto, porque ninguém
revisou *esta* geometria. O estado é distinto de `PENDING` só para o relatório:
"existe uma decisão órfã aqui" é um problema a resolver, e desapareceria se as
duas situações recebessem o mesmo nome.
"""

APPROVING_DECISION = "approved_pothole"
"""A única decisão que aprova. Todas as outras reprovam ou não concluem."""

# ------------------------------------------------------- revisão de duplicata
#
# Pergunta diferente da semântica, e por isso vocabulário próprio: aprovar um
# buraco não responde se duas imagens são a mesma cena.
DUPLICATE_DECISIONS: tuple[str, ...] = (
    "same_scene_keep_a",  # mesma cena; fica `a`, sai `b`
    "same_scene_keep_b",  # mesma cena; fica `b`, sai `a`
    "different_scenes",  # hash perceptual igual, cenas distintas
    "ambiguous",  # não dá para decidir: continua bloqueando
)

DUPLICATE_NOT_IN_PAIR = "NOT_IN_DUPLICATE_PAIR"
DUPLICATE_PENDING = "PENDING_HUMAN_DUPLICATE_REVIEW"
DUPLICATE_KEPT = "DUPLICATE_KEPT"
DUPLICATE_DIFFERENT = "DUPLICATE_DIFFERENT_SCENES"
DUPLICATE_REJECTED = "DUPLICATE_REJECTED"
DUPLICATE_STALE = "STALE_DUPLICATE_REVIEW"
DUPLICATE_INVALID = "INVALID_DUPLICATE_DECISION"
DUPLICATE_REJECTION_REASON = "near_duplicate_human_rejected"

DUPLICATE_RESOLVED: frozenset[str] = frozenset(
    {DUPLICATE_NOT_IN_PAIR, DUPLICATE_KEPT, DUPLICATE_DIFFERENT}
)
"""Estados de imagem que deixam o portão de duplicata abrir. Nenhum outro."""

# Quando a mesma imagem aparece em mais de um par, vence o estado mais
# restritivo: ser mantida num par não desfaz ter sido rejeitada em outro.
_DUPLICATE_PRECEDENCE = {
    DUPLICATE_REJECTED: 0,
    DUPLICATE_INVALID: 1,
    DUPLICATE_STALE: 2,
    DUPLICATE_PENDING: 3,
    DUPLICATE_KEPT: 4,
    DUPLICATE_DIFFERENT: 5,
}


def resolve_duplicate_pair(
    pair: Mapping[str, Any],
    *,
    scan_sha: str | None,
    image_paths: Mapping[str, str],
) -> tuple[str, str]:
    """Estado de `a` e de `b` depois de aplicar a decisão humana do par.

    `same_scene_keep_a` não significa "o conflito deixou de existir": significa
    "a mesma cena entra uma vez, por `a`". A outra imagem sai — inteira, com
    todas as caixas — e é por isso que o resultado é um estado POR IMAGEM, lido
    pelo portão `duplicate_review_resolved`, e não um par riscado num relatório.

    Fail-closed em todos os caminhos: sem vínculo com o scan auditado, ou com
    imagem diferente da que foi comparada, a decisão é de outro par e não vale.
    """
    a, b = str(pair.get("a")), str(pair.get("b"))
    registrado = pair.get("source_scan_sha256")
    if (
        not registrado
        or scan_sha is None
        or registrado != scan_sha
        or pair.get("a_image_relpath") != image_paths.get(a)
        or pair.get("b_image_relpath") != image_paths.get(b)
    ):
        return DUPLICATE_STALE, DUPLICATE_STALE

    decisao = pair.get("decision")
    if decisao is None or decisao == "ambiguous":
        return DUPLICATE_PENDING, DUPLICATE_PENDING
    if decisao not in DUPLICATE_DECISIONS:
        return DUPLICATE_INVALID, DUPLICATE_INVALID
    if decisao == "same_scene_keep_a":
        return DUPLICATE_KEPT, DUPLICATE_REJECTED
    if decisao == "same_scene_keep_b":
        return DUPLICATE_REJECTED, DUPLICATE_KEPT
    return DUPLICATE_DIFFERENT, DUPLICATE_DIFFERENT


def resolve_duplicate_reviews(
    pairs: Sequence[Mapping[str, Any]],
    *,
    scan_sha: str | None,
    image_paths: Mapping[str, str],
) -> dict[str, str]:
    """Estado de duplicata de cada imagem citada em algum par. As demais: fora."""
    estados: dict[str, str] = {}
    for pair in pairs:
        for stem, estado in zip(
            (str(pair.get("a")), str(pair.get("b"))),
            resolve_duplicate_pair(pair, scan_sha=scan_sha, image_paths=image_paths),
            strict=True,
        ):
            atual = estados.get(stem)
            if atual is None or _DUPLICATE_PRECEDENCE[estado] < _DUPLICATE_PRECEDENCE[atual]:
                estados[stem] = estado
    return estados


SEMANTIC_CANDIDATE = "SEMANTIC_CANDIDATE"
SEMANTIC_HEURISTIC_QUARANTINE = "HEURISTIC_QUARANTINE"
SEMANTIC_REJECTED = "SEMANTIC_REJECTED"

# Ordem importa só para a leitura do relatório; a conjunção é a mesma.
REQUIRED_GATES: tuple[str, ...] = (
    "geometry_valid",
    "source_category_validated",
    "taxonomy_mapping_validated",
    "not_quarantined",
    "duplicate_check_passed",
    "duplicate_review_resolved",
    "cross_source_check_passed",
    "box_semantic_validation_passed",
    "image_annotation_complete",
)


class AuthorizationError(RuntimeError):
    """O manifesto autorizado não pôde ser usado como fonte de verdade.

    Levantado quando o artefato está ausente, desatualizado, com hash divergente
    ou incompleto. Nunca é capturado para seguir em frente com o raw: o fallback
    para o dado bruto é justamente o caminho que produziu a divergência que este
    módulo existe para impedir.
    """


@dataclass(frozen=True)
class GateResult:
    """O veredito de uma caixa, com a conta de como se chegou nele."""

    gates: dict[str, bool] = field(default_factory=dict)
    blocking_gates: tuple[str, ...] = ()
    urmind_class: str | None = None
    training_allowed: bool = False
    semantic_status: str = SEMANTIC_CANDIDATE
    human_review_status: str = HUMAN_REVIEW_PENDING

    def as_dict(self) -> dict[str, Any]:
        return {
            "gates": dict(self.gates),
            "blocking_gates": list(self.blocking_gates),
            "urmind_class": self.urmind_class,
            "training_allowed": self.training_allowed,
            "semantic_status": self.semantic_status,
            "human_review_status": self.human_review_status,
        }


def human_review_status_of(decision: str | None) -> str:
    """Traduz a decisão registrada na folha em estado de revisão.

    Decisão fora do vocabulário não vira "outra coisa aprovada": vira `INVALID`,
    e portão nenhum passa com ela.
    """
    if decision is None:
        return HUMAN_REVIEW_PENDING
    if decision not in HUMAN_DECISIONS:
        return "INVALID"
    return decision.upper()


def evaluate_gates(
    box: Mapping[str, Any],
    *,
    image: Mapping[str, Any],
    source_checks: Mapping[str, Any],
    urmind_class: str,
) -> GateResult:
    """Decide se ESTA caixa pode treinar, e com que classe.

    `box` é a entrada do manifesto derivado; `image` é a linha que a contém; e
    `source_checks` traz os resultados que valem para a derivada inteira —
    duplicata e contaminação cruzada — porque nenhum dos dois é propriedade de
    uma caixa isolada.

    `urmind_class` é a classe que a caixa **receberia** se passasse por tudo. Ela
    só é devolvida quando passa: enquanto houver portão aberto, a caixa sai com
    `urmind_class = None`, e é isso que impede o consumidor de treiná-la achando
    que é ground truth.
    """
    decisao = box.get("human_decision")
    revisao = box.get("human_review_status") or human_review_status_of(decisao)
    quarentena = bool(box.get("quarantine_reasons"))

    gates = {
        # Geometria: a caixa existe e cabe na imagem. Necessário, longe de
        # suficiente — foi confundir isto com validade que gerou 451 "aceitas".
        "geometry_valid": bool(box.get("geometry_valid")),
        # A amostra veio da pasta cujo rótulo foi examinado, com o id que aquela
        # pasta usa. Id fora do mapa inferido não se conserta aqui.
        "source_category_validated": bool(box.get("source_category_validated")),
        # `pothole` corresponde à definição canônica de URMIND_ROAD_D40. É
        # afirmação sobre a classe, e é a única destas que já está resolvida.
        "taxonomy_mapping_validated": bool(source_checks.get("taxonomy_mapping_validated")),
        "not_quarantined": not quarentena,
        "duplicate_check_passed": bool(source_checks.get("duplicate_check_passed")),
        # Portão da IMAGEM, complementar ao de conjunto acima. O de conjunto diz
        # "a validação não achou par sem decisão"; este diz "e ESTA imagem não é
        # o lado rejeitado de nenhum par". Sem ele, `same_scene_keep_a` fechava o
        # par no relatório e deixava `b` treinar do mesmo jeito. Campo ausente
        # reprova: manifesto que não declara o estado não prova nada.
        "duplicate_review_resolved": image.get("duplicate_review_status") in DUPLICATE_RESOLVED,
        "cross_source_check_passed": bool(source_checks.get("cross_source_check_passed")),
        # O portão que hoje reprova tudo: ninguém revisou nenhuma caixa.
        "box_semantic_validation_passed": decisao == APPROVING_DECISION,
        # Anotação faltando é defeito da IMAGEM, não da caixa: uma caixa correta
        # numa imagem com buracos não anotados ensina falso negativo do mesmo
        # jeito. Por isso o portão é avaliado no nível da imagem — e exige
        # `COMPLETE` afirmado, não a mera ausência de `INCOMPLETE`: "ninguém
        # verificou" não é "está completo".
        "image_annotation_complete": image.get("annotation_completeness") == "COMPLETE",
    }

    bloqueando = tuple(nome for nome in REQUIRED_GATES if not gates[nome])
    permitido = not bloqueando

    if quarentena:
        status = SEMANTIC_HEURISTIC_QUARANTINE
    elif decisao in ("wrong_class", "bad_box", "duplicate"):
        status = SEMANTIC_REJECTED
    else:
        status = SEMANTIC_CANDIDATE

    return GateResult(
        gates=gates,
        blocking_gates=bloqueando,
        urmind_class=urmind_class if permitido else None,
        training_allowed=permitido,
        semantic_status=status,
        human_review_status=revisao,
    )


def authorize_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    source_checks: Mapping[str, Any],
    urmind_class: str,
) -> list[dict[str, Any]]:
    """Reavalia os portões de todo o manifesto e devolve as linhas decididas.

    O manifesto guarda os fatos por caixa; os portões de conjunto — duplicata e
    contaminação cruzada — só existem depois da validação, que roda *depois* da
    conversão. Recalcular aqui é o que impede a ordem das etapas de virar uma
    autorização velha: a decisão vale para o estado atual dos artefatos, não para
    o estado que havia quando a derivada foi escrita.

    A função é a mesma que o conversor usa. Uma regra, dois chamadores.
    """
    decididas: list[dict[str, Any]] = []
    for row in rows:
        caixas = []
        for box in row.get("boxes", ()):
            resultado = evaluate_gates(
                box, image=row, source_checks=source_checks, urmind_class=urmind_class
            )
            caixas.append({**dict(box), **resultado.as_dict()})
        decididas.append({**dict(row), "boxes": caixas})
    return decididas
