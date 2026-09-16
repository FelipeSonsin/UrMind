"""Métricas de detecção obrigatórias (MASTER_PLAN §8.5).

Precision, recall e F1 por classe, AP@50 e AP@50:95 por classe, mAP agregado e
matriz de confusão. Tudo calculado aqui, sem dependência de framework: as
métricas precisam existir **antes** do primeiro treino, senão não há como medir
baseline nenhum — e o §8.5 fecha dizendo que nenhum limiar mínimo é inventado
antes desse baseline.

Duas decisões que mudam o que os números significam:

**Classe sem ground truth devolve `None`, não zero.** Se o conjunto de teste não
tem nenhuma instância de buraco, o AP de buraco é indefinido — reportar 0,0 diria
que o modelo falhou, e reportar 1,0 diria que acertou. Ambos seriam mentira. O
mAP agregado também ignora essas classes, senão o número global passa a depender
de quantas classes vazias existem no recorte.

**Precision/recall dependem de limiar de confiança; AP não.** São perguntas
diferentes: P/R/F1 medem o comportamento de um limiar operacional escolhido, AP
mede a qualidade do ranqueamento em todos os limiares. Por isso `score_threshold`
aparece explicitamente na chamada e vai gravado no resultado.

Interpolação: AP usa todos os pontos (área sob a envoltória de precisão), que é o
padrão do VOC 2010+ e da avaliação COCO. AP@50:95 é a média sobre IoU de 0,50 a
0,95 em passos de 0,05, como no COCO.

Latência e FPS, que o §8.5 também exige, não estão aqui: dependem de medição no
hardware real de inferência e entram junto com o ONNX Runtime (§9).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

__all__ = [
    "IOU_THRESHOLDS_COCO",
    "Box",
    "ClassMetrics",
    "EvaluationResult",
    "GroundTruth",
    "Prediction",
    "evaluate",
    "iou",
]

IOU_THRESHOLDS_COCO: tuple[float, ...] = tuple(round(0.50 + 0.05 * i, 2) for i in range(10))
"""0,50 a 0,95 em passos de 0,05 — a faixa que compõe o AP@50:95."""

BACKGROUND = "__background__"
"""Pseudo-classe da matriz de confusão: falso positivo sem GT, ou GT sem detecção."""


@dataclass(frozen=True)
class Box:
    """Caixa em coordenadas absolutas de pixel, canto superior esquerdo na origem."""

    x1: float
    y1: float
    x2: float
    y2: float

    def __post_init__(self) -> None:
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError(f"caixa degenerada: ({self.x1}, {self.y1}, {self.x2}, {self.y2})")

    @property
    def area(self) -> float:
        return (self.x2 - self.x1) * (self.y2 - self.y1)


@dataclass(frozen=True)
class GroundTruth:
    image_id: str
    label: str
    box: Box
    class_id: int | None = None
    source_fingerprint: str | None = None
    annotation_fingerprint: str | None = None

    @property
    def class_name(self) -> str:
        return self.label

    @property
    def bbox(self) -> Box:
        return self.box


@dataclass(frozen=True)
class Prediction:
    image_id: str
    label: str
    box: Box
    score: float
    class_id: int | None = None
    objectness: float | None = None
    class_confidence: float | None = None

    @property
    def class_name(self) -> str:
        return self.label

    @property
    def confidence(self) -> float:
        return self.score

    @property
    def bbox(self) -> Box:
        return self.box


@dataclass(frozen=True)
class ClassMetrics:
    """Métricas de uma classe. `None` significa indefinido, nunca zero."""

    label: str
    support: int
    """Instâncias no ground truth. Zero torna precision/recall/AP indefinidos."""

    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float | None
    recall: float | None
    f1: float | None
    ap50: float | None
    ap50_95: float | None

    @property
    def is_measurable(self) -> bool:
        return self.support > 0


@dataclass(frozen=True)
class EvaluationResult:
    per_class: dict[str, ClassMetrics]
    precision: float | None
    recall: float | None
    map50: float | None
    map50_95: float | None
    confusion: dict[str, dict[str, int]]
    score_threshold: float
    iou_threshold: float
    measured_classes: list[str] = field(default_factory=list)
    skipped_classes: list[str] = field(default_factory=list)
    """Classes sem ground truth, fora do mAP por serem indefinidas."""

    def as_persisted(self) -> dict[str, object]:
        """Formato para `model_versions.metrics` (§9)."""
        return {
            "score_threshold": self.score_threshold,
            "iou_threshold": self.iou_threshold,
            "precision": self.precision,
            "recall": self.recall,
            "map50": self.map50,
            "map50_95": self.map50_95,
            "measured_classes": self.measured_classes,
            "skipped_classes_without_ground_truth": self.skipped_classes,
            "per_class": {
                label: {
                    "support": m.support,
                    "true_positives": m.true_positives,
                    "false_positives": m.false_positives,
                    "false_negatives": m.false_negatives,
                    "precision": m.precision,
                    "recall": m.recall,
                    "f1": m.f1,
                    "ap50": m.ap50,
                    "ap50_95": m.ap50_95,
                }
                for label, m in self.per_class.items()
            },
            "confusion": self.confusion,
        }


def iou(a: Box, b: Box) -> float:
    """Intersecção sobre união. Zero quando não há sobreposição."""
    left = max(a.x1, b.x1)
    top = max(a.y1, b.y1)
    right = min(a.x2, b.x2)
    bottom = min(a.y2, b.y2)

    if right <= left or bottom <= top:
        return 0.0

    intersection = (right - left) * (bottom - top)
    union = a.area + b.area - intersection
    return intersection / union if union > 0 else 0.0


def _match_greedy(
    predictions: list[Prediction],
    ground_truths: list[GroundTruth],
    iou_threshold: float,
) -> tuple[list[int], int]:
    """Casa predições com ground truth, da mais confiante para a menos.

    Cada GT só pode ser casado uma vez: a segunda detecção do mesmo defeito é
    falso positivo, não um acerto extra. Devolve a lista de acertos (1/0) na
    ordem de confiança decrescente e a contagem de GT casados.
    """
    by_image: dict[str, list[GroundTruth]] = defaultdict(list)
    for truth in ground_truths:
        by_image[truth.image_id].append(truth)

    taken: set[int] = set()
    hits: list[int] = []
    matched = 0

    for prediction in sorted(predictions, key=lambda p: p.score, reverse=True):
        best_iou = 0.0
        best_index = -1
        for index, truth in enumerate(by_image.get(prediction.image_id, [])):
            key = id(truth)
            if key in taken:
                continue
            overlap = iou(prediction.box, truth.box)
            if overlap > best_iou:
                best_iou = overlap
                best_index = index

        if best_index >= 0 and best_iou >= iou_threshold:
            taken.add(id(by_image[prediction.image_id][best_index]))
            hits.append(1)
            matched += 1
        else:
            hits.append(0)

    return hits, matched


def _average_precision(hits: list[int], support: int) -> float | None:
    """Área sob a curva precision-recall, interpolando por todos os pontos.

    `hits` vem ordenado por confiança decrescente. Sem ground truth o AP é
    indefinido — devolve `None` em vez de fingir um valor.
    """
    if support == 0:
        return None
    if not hits:
        return 0.0

    true_positives = 0
    false_positives = 0
    recalls = [0.0]
    precisions = [1.0]

    for hit in hits:
        if hit:
            true_positives += 1
        else:
            false_positives += 1
        recalls.append(true_positives / support)
        precisions.append(true_positives / (true_positives + false_positives))

    # Envoltória: a precisão em cada recall vira o máximo daí para a frente,
    # removendo os dentes de serra da curva bruta.
    for index in range(len(precisions) - 2, -1, -1):
        precisions[index] = max(precisions[index], precisions[index + 1])

    area = 0.0
    for index in range(1, len(recalls)):
        delta = recalls[index] - recalls[index - 1]
        if delta > 0:
            area += delta * precisions[index]
    return round(area, 6)


def _confusion_matrix(
    predictions: list[Prediction],
    ground_truths: list[GroundTruth],
    labels: list[str],
    iou_threshold: float,
) -> dict[str, dict[str, int]]:
    """Matriz de confusão da detecção, com `__background__` nas duas pontas.

    O casamento aqui é agnóstico de classe: primeiro se decide *qual* GT a caixa
    encontrou, depois se compara o rótulo. É assim que uma trinca longitudinal
    detectada como transversal aparece como confusão entre as duas, em vez de
    virar um falso positivo e um falso negativo sem relação.
    """
    rows = [*labels, BACKGROUND]
    matrix: dict[str, dict[str, int]] = {
        row: dict.fromkeys(rows, 0) for row in rows
    }

    by_image: dict[str, list[GroundTruth]] = defaultdict(list)
    for truth in ground_truths:
        by_image[truth.image_id].append(truth)

    taken: set[int] = set()
    for prediction in sorted(predictions, key=lambda p: p.score, reverse=True):
        best_iou = 0.0
        best: GroundTruth | None = None
        for truth in by_image.get(prediction.image_id, []):
            if id(truth) in taken:
                continue
            overlap = iou(prediction.box, truth.box)
            if overlap > best_iou:
                best_iou = overlap
                best = truth

        if best is not None and best_iou >= iou_threshold:
            taken.add(id(best))
            matrix[best.label][prediction.label] += 1
        else:
            # Detecção que não encontrou nada: alarme falso.
            matrix[BACKGROUND][prediction.label] += 1

    for truth in ground_truths:
        if id(truth) not in taken:
            # Defeito real que ninguém detectou.
            matrix[truth.label][BACKGROUND] += 1

    return matrix


def evaluate(
    predictions: list[Prediction],
    ground_truths: list[GroundTruth],
    labels: list[str] | None = None,
    score_threshold: float = 0.5,
    iou_threshold: float = 0.5,
) -> EvaluationResult:
    """Calcula o conjunto de métricas do §8.5.

    `score_threshold` afeta precision, recall, F1 e a matriz de confusão — são as
    métricas de um ponto de operação. AP e mAP percorrem todos os limiares de
    confiança e ignoram esse parâmetro, de propósito.
    """
    if labels is None:
        labels = sorted({t.label for t in ground_truths} | {p.label for p in predictions})

    confident = [p for p in predictions if p.score >= score_threshold]
    per_class: dict[str, ClassMetrics] = {}

    for label in labels:
        class_truths = [t for t in ground_truths if t.label == label]
        support = len(class_truths)

        # Ponto de operação: só as detecções acima do limiar.
        operating = [p for p in confident if p.label == label]
        _, matched = _match_greedy(operating, class_truths, iou_threshold)
        true_positives = matched
        false_positives = len(operating) - matched
        false_negatives = support - matched

        precision = (
            true_positives / (true_positives + false_positives)
            if operating
            else (None if support == 0 else 0.0)
        )
        recall = true_positives / support if support else None
        if precision is None or recall is None or (precision + recall) == 0:
            f1 = None if (precision is None or recall is None) else 0.0
        else:
            f1 = 2 * precision * recall / (precision + recall)

        # Ranqueamento: todas as detecções da classe, sem corte de confiança.
        ranked = [p for p in predictions if p.label == label]
        ap50 = _average_precision(_match_greedy(ranked, class_truths, 0.50)[0], support)

        aps = [
            _average_precision(_match_greedy(ranked, class_truths, threshold)[0], support)
            for threshold in IOU_THRESHOLDS_COCO
        ]
        measured = [value for value in aps if value is not None]
        ap50_95 = round(sum(measured) / len(measured), 6) if measured else None

        per_class[label] = ClassMetrics(
            label=label,
            support=support,
            true_positives=true_positives,
            false_positives=false_positives,
            false_negatives=false_negatives,
            precision=None if precision is None else round(precision, 6),
            recall=None if recall is None else round(recall, 6),
            f1=None if f1 is None else round(f1, 6),
            ap50=ap50,
            ap50_95=ap50_95,
        )

    measurable = [label for label in labels if per_class[label].is_measurable]
    skipped = [label for label in labels if not per_class[label].is_measurable]

    def _mean(values: list[float | None]) -> float | None:
        present = [v for v in values if v is not None]
        return round(sum(present) / len(present), 6) if present else None

    total_true_positives = sum(per_class[label].true_positives for label in labels)
    total_predictions = sum(
        per_class[label].true_positives + per_class[label].false_positives
        for label in labels
    )
    total_support = sum(per_class[label].support for label in labels)

    return EvaluationResult(
        per_class=per_class,
        precision=(
            round(total_true_positives / total_predictions, 6)
            if total_predictions
            else (0.0 if total_support else None)
        ),
        recall=(
            round(total_true_positives / total_support, 6)
            if total_support
            else None
        ),
        map50=_mean([per_class[label].ap50 for label in measurable]),
        map50_95=_mean([per_class[label].ap50_95 for label in measurable]),
        confusion=_confusion_matrix(confident, ground_truths, labels, iou_threshold),
        score_threshold=score_threshold,
        iou_threshold=iou_threshold,
        measured_classes=measurable,
        skipped_classes=skipped,
    )
