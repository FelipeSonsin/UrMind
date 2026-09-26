"""Inferência fatiada do mesmo ONNX YOLOX: FULL, TILED e HYBRID.

Não é outro detector. É o mesmo modelo 640×640 rodando em recortes diferentes do
mesmo quadro:

- ``full``: o quadro inteiro reduzido por letterbox (o comportamento histórico);
- ``tiled``: só recortes ``tile_size``×``tile_size`` com sobreposição, na escala nativa
  do quadro (sem ampliar), para que objetos pequenos não encolham no tensor;
- ``hybrid``: a vista global **mais** os recortes. A global mantém o contexto e os
  objetos grandes que um recorte cortaria; os recortes preservam detalhe.

Cada vista passa pelo pós-processamento do perfil (limiar por classe + NMS por classe)
exatamente como hoje, no próprio sistema de coordenadas e recortada aos próprios
limites. Depois as detecções são levadas ao quadro e a fusão decide **somente
conflitos entre vistas diferentes**: duas caixas da mesma vista já sobreviveram ao NMS
do perfil e nunca se suprimem de novo. Com uma única vista (``full`` ou imagem menor que
o tile) a saída é idêntica à do perfil sem fatiamento.

Fusões disponíveis, sempre dentro da mesma classe (classes diferentes nunca se fundem):

- ``nms``: a caixa de maior score remove as de outras vistas com IoU > limiar;
- ``nms_ios``: idem com interseção sobre a menor área (IOS), que remove a metade
  cortada de um objeto contida na caixa da vista global;
- ``nmm``: fusão gulosa (non-maximum merging): a caixa de maior score absorve, por IOS,
  as de outras vistas e cresce até a união, reconstruindo objeto dividido entre tiles;
- ``wbf``: Weighted Boxes Fusion; caixa = média ponderada pelo score, score = máximo do
  grupo (sem a penalidade por número de "modelos", que puniria justamente o detalhe
  visto só num tile).

Geometria contínua (sem o ``+1`` de área do NMS do YOLOX, que continua valendo dentro
de cada vista). O espelho TypeScript fica em ``frontend/src/domain/slicedInference.ts``.

Estado (sprint visual de 26/09/2026): medido no CAMERA_DEV e REJEITADO para o ONNX
``d429bde8…`` — ganha recall, mas multiplica os alarmes falsos e derruba o F1
(``docs/ml/VISUAL_SPRINT_2026-09-26.md``). Nenhum perfil publicado usa fatiamento; o
Worker chama ``detect`` sem ``slicing`` (caminho histórico, uma vista).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

__all__ = [
    "INFERENCE_MODES",
    "MERGE_METHODS",
    "SlicingConfig",
    "SlicingConfigError",
    "View",
    "finalize_detections",
    "merge_view_detections",
    "offset_detections",
    "plan_views",
    "tile_grid",
]

INFERENCE_MODES: tuple[str, ...] = ("full", "tiled", "hybrid")
MERGE_METHODS: tuple[str, ...] = ("nms", "nms_ios", "nmm", "wbf")

View = tuple[int, int, int, int]
"""Recorte ``(x0, y0, x1, y1)`` em pixels do quadro, fim exclusivo."""


class SlicingConfigError(ValueError):
    """Configuração de fatiamento inválida: o perfil é recusado, nunca corrigido."""


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class SlicingConfig:
    mode: str = "full"
    tile_size: int = 640
    overlap: float = 0.2
    merge: str = "nms"
    merge_threshold: float = 0.5

    def __post_init__(self) -> None:
        if self.mode not in INFERENCE_MODES:
            raise SlicingConfigError(f"modo de inferência desconhecido: {self.mode!r}")
        # Mesmo contrato do `slicingSchema` TS (`z.number().int()`): inteiro de verdade,
        # nunca float truncado nem texto convertido.
        if not _is_int(self.tile_size) or not 64 <= self.tile_size <= 4096:
            raise SlicingConfigError("tile_size precisa ser inteiro em 64..4096")
        if not _is_number(self.overlap) or not 0.0 <= self.overlap <= 0.5:
            raise SlicingConfigError("overlap precisa ser número em 0..0,5")
        if self.merge not in MERGE_METHODS:
            raise SlicingConfigError(f"fusão desconhecida: {self.merge!r}")
        if not _is_number(self.merge_threshold) or not 0.0 < self.merge_threshold <= 1.0:
            raise SlicingConfigError("merge_threshold precisa ser número em (0, 1]")

    def profile_fields(self) -> dict[str, object]:
        """Campos que entram no perfil de inferência (e portanto no seu hash)."""
        return {
            "mode": self.mode,
            "tile_size": self.tile_size,
            "overlap": float(self.overlap),
            "merge": self.merge,
            "merge_threshold": float(self.merge_threshold),
        }


def _starts(length: int, tile: int, stride: int) -> list[int]:
    if length <= tile:
        return [0]
    starts = list(range(0, length - tile, stride))
    starts.append(length - tile)  # último recorte encostado na borda, nada fica de fora
    return starts


def tile_grid(width: int, height: int, *, tile_size: int, overlap: float) -> list[View]:
    """Recortes que cobrem o quadro inteiro; vazio se o quadro cabe num tile.

    O passo é ``tile_size × (1 − overlap)`` arredondado; o último recorte de cada eixo é
    encostado na borda (a sobreposição ali pode ser maior). Um eixo menor que o tile
    fica inteiro, sem ampliar.
    """
    if width <= tile_size and height <= tile_size:
        return []
    # floor(x + 0,5), não round() (bancário): a mesma grade do espelho TypeScript.
    stride = max(1, math.floor(tile_size * (1.0 - overlap) + 0.5))
    return [
        (x, y, min(x + tile_size, width), min(y + tile_size, height))
        for y in _starts(height, tile_size, stride)
        for x in _starts(width, tile_size, stride)
    ]


def plan_views(width: int, height: int, config: SlicingConfig) -> list[View]:
    """Vistas a inferir, a global primeiro. Imagem pequena: só a global, em todo modo."""
    full: View = (0, 0, int(width), int(height))
    if config.mode == "full":
        return [full]
    tiles = tile_grid(width, height, tile_size=config.tile_size, overlap=config.overlap)
    if not tiles:
        return [full]
    return tiles if config.mode == "tiled" else [full, *tiles]


def offset_detections(detections: np.ndarray, view: View) -> np.ndarray:
    """Leva ``[x0, y0, x1, y1, score, classe]`` da vista para o quadro."""
    moved = np.array(detections, dtype=np.float64, copy=True).reshape(-1, 6)
    moved[:, [0, 2]] += view[0]
    moved[:, [1, 3]] += view[1]
    return moved


def _overlaps(box: np.ndarray, boxes: np.ndarray, metric: str) -> np.ndarray:
    """IoU ou IOS de uma caixa contra várias (vetorizado; geometria contínua)."""
    width = np.minimum(box[2], boxes[:, 2]) - np.maximum(box[0], boxes[:, 0])
    height = np.minimum(box[3], boxes[:, 3]) - np.maximum(box[1], boxes[:, 1])
    inter = np.where((width > 0) & (height > 0), width * height, 0.0)
    area = max(0.0, float(box[2] - box[0])) * max(0.0, float(box[3] - box[1]))
    areas = np.clip(boxes[:, 2] - boxes[:, 0], 0, None) * np.clip(
        boxes[:, 3] - boxes[:, 1], 0, None
    )
    denominator = np.minimum(area, areas) if metric == "ios" else area + areas - inter
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where((inter > 0) & (denominator > 0), inter / denominator, 0.0)


def _merge_class(
    boxes: np.ndarray, scores: np.ndarray, views: np.ndarray, method: str, threshold: float
) -> list[tuple[np.ndarray, float]]:
    """Fusão gulosa na ordem (score desc., posição). Semântica sequencial do espelho TS:
    em NMM/WBF cada caixa elegível, na ordem, entra no grupo e muda o grupo antes da
    próxima; a varredura recomeça enquanto o grupo cresce."""
    order = np.array(sorted(range(len(scores)), key=lambda i: (-scores[i], i)), dtype=int)
    rank = np.empty(len(order), dtype=int)
    rank[order] = np.arange(len(order))
    used = np.zeros(len(scores), dtype=bool)
    result: list[tuple[np.ndarray, float]] = []
    metric = "ios" if method in ("nms_ios", "nmm") else "iou"
    for i in order:
        if used[i]:
            continue
        used[i] = True
        if method in ("nms", "nms_ios"):
            used |= ~used & (views != views[i]) & (_overlaps(boxes[i], boxes, metric) > threshold)
            result.append((boxes[i].copy(), float(scores[i])))
            continue
        members = [int(i)]
        member_views = {int(views[i])}
        current = boxes[i].copy()
        grew = True
        while grew:
            grew = False
            position = -1  # varre `rest` na ordem, a partir da última caixa absorvida
            while True:
                reference = current if method == "nmm" else _weighted(boxes, scores, members)
                eligible = (
                    ~used
                    & ~np.isin(views, list(member_views))
                    & (rank > position)
                    & (_overlaps(reference, boxes, metric) > threshold)
                )
                if not eligible.any():
                    break
                j = int(np.flatnonzero(eligible)[np.argmin(rank[eligible])])
                used[j] = True
                members.append(j)
                member_views.add(int(views[j]))
                position = int(rank[j])
                if method == "nmm":
                    current = np.array(
                        [
                            min(current[0], boxes[j][0]),
                            min(current[1], boxes[j][1]),
                            max(current[2], boxes[j][2]),
                            max(current[3], boxes[j][3]),
                        ]
                    )
                grew = True
        fused = current if method == "nmm" else _weighted(boxes, scores, members)
        result.append((fused, float(max(scores[m] for m in members))))
    return result


def _weighted(boxes: np.ndarray, scores: np.ndarray, members: list[int]) -> np.ndarray:
    weights = np.asarray([scores[m] for m in members], dtype=np.float64)
    stacked = np.asarray([boxes[m] for m in members], dtype=np.float64)
    return (stacked * weights[:, None]).sum(axis=0) / weights.sum()


def merge_view_detections(
    per_view: Sequence[np.ndarray], *, method: str, threshold: float
) -> np.ndarray:
    """Funde detecções já no quadro, vindas de vistas diferentes.

    Com no máximo uma vista não vazia devolve essa vista intacta (mesma ordem): é o
    que garante FULL, imagem pequena e HYBRID sem contribuição dos tiles idênticos ao
    perfil sem fatiamento.
    """
    if method not in MERGE_METHODS:
        raise SlicingConfigError(f"fusão desconhecida: {method!r}")
    arrays = [np.asarray(d, dtype=np.float64).reshape(-1, 6) for d in per_view]
    non_empty = [a for a in arrays if len(a)]
    if len(non_empty) <= 1:
        return non_empty[0].copy() if non_empty else np.empty((0, 6), dtype=np.float64)
    stacked = np.concatenate(arrays, axis=0)
    views = np.concatenate([np.full(len(a), index) for index, a in enumerate(arrays)])
    merged: list[np.ndarray] = []
    for class_index in sorted({int(c) for c in stacked[:, 5]}):
        mask = stacked[:, 5] == class_index
        for box, score in _merge_class(
            stacked[mask, :4], stacked[mask, 4], views[mask], method, threshold
        ):
            merged.append(np.array([*box, score, class_index], dtype=np.float64))
    if not merged:
        return np.empty((0, 6), dtype=np.float64)
    return np.vstack(merged)


def finalize_detections(detections: np.ndarray, *, max_detections: int) -> np.ndarray:
    """Ordena por score (estável) e aplica o teto de detecções do perfil."""
    detections = np.asarray(detections, dtype=np.float64).reshape(-1, 6)
    order = np.argsort(-detections[:, 4], kind="stable")
    return detections[order[:max_detections]]
