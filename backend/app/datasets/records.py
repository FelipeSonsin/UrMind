"""Formato canônico em que qualquer dataset entra no UrMind (§8.3 passo 2).

Cada fonte tem o seu formato: VOC XML, YOLO txt, máscara PNG, CSV com GPS. O
sistema não precisa conhecer nenhum deles depois da leitura — precisa de três
coisas, e são estas as três classes abaixo.

`AnnotatedImage` é o que pode virar treino: imagem, caixas já traduzidas para a
taxonomia do §8.2 e a lista dos rótulos que foram **recusados**. A recusa fica
no registro de propósito: sem ela, um dataset com 90% de classes fora do escopo
pareceria idêntico a um dataset totalmente aproveitado.

`MaskSample` existe porque máscara não é caixa. O UNIVALI/DNIT anota por
segmentação, e transformar máscara em caixa é uma decisão de preparo de dados
— com perda de informação — que precisa ser tomada e registrada, não embutida
no leitor.

`KeypointSample` existe porque ponto não é caixa, pelo mesmo motivo que máscara
não é. O Project Sidewalk/RampNet anota rampa de calçada como um ponto único;
inventar largura e altura em volta dele para virar `BoundingBox` seria fabricar
extensão que a fonte não mediu. O ponto fica ponto, e a conversão — se algum dia
for feita — vira decisão registrada de preparo de dados.

`GeoRecord` é ocorrência com coordenada e sem imagem anotada: CAMBER e as
coordenadas reais de rampa do RampNet. Não é rótulo de treino e não vira
`Capture`/`Detection` no banco, porque não foi observação feita pelo sistema.

Todo registro carrega `group`, a chave que o `app.ml.splits` usa para impedir
vazamento entre train/validation/test (§8.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.schemas.core import UrmindClass

__all__ = [
    "AnnotatedImage",
    "BoundingBox",
    "GeoRecord",
    "Keypoint",
    "KeypointSample",
    "MaskSample",
    "RejectedLabel",
]


@dataclass(frozen=True)
class BoundingBox:
    """Caixa em pixels, canto superior esquerdo → canto inferior direito."""

    urmind_class: UrmindClass
    source_label: str
    xmin: float
    ymin: float
    xmax: float
    ymax: float

    def __post_init__(self) -> None:
        if self.xmax <= self.xmin or self.ymax <= self.ymin:
            raise ValueError(
                f"caixa degenerada em {self.source_label}: "
                f"({self.xmin}, {self.ymin}) → ({self.xmax}, {self.ymax})"
            )

    @property
    def width(self) -> float:
        return self.xmax - self.xmin

    @property
    def height(self) -> float:
        return self.ymax - self.ymin


@dataclass(frozen=True)
class RejectedLabel:
    """Rótulo que existia na fonte e não entrou. Contabilizado, não descartado."""

    source_label: str
    reason: str


@dataclass(frozen=True)
class AnnotatedImage:
    dataset_id: str
    image_path: str
    """Caminho relativo à raiz do dataset em `datasets/raw/`. Nunca absoluto."""

    width: int
    height: int
    group: str
    """Chave de split (§8.4): sessão, rota, local ou origem — nunca o frame."""

    boxes: tuple[BoundingBox, ...] = ()
    rejected: tuple[RejectedLabel, ...] = ()
    official_split: str | None = None
    """Split publicado pela fonte, quando existe. Preservá-lo é o §8.3 passo 4."""

    @property
    def usable(self) -> bool:
        """Tem ao menos uma caixa na taxonomia V1."""
        return bool(self.boxes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "image_path": self.image_path,
            "width": self.width,
            "height": self.height,
            "group": self.group,
            "official_split": self.official_split,
            "boxes": [
                {
                    "class": str(b.urmind_class),
                    "source_label": b.source_label,
                    "bbox": [b.xmin, b.ymin, b.xmax, b.ymax],
                }
                for b in self.boxes
            ],
            "rejected": [{"label": r.source_label, "reason": r.reason} for r in self.rejected],
        }


@dataclass(frozen=True)
class MaskSample:
    """Imagem com máscaras de segmentação, uma por tipo anotado."""

    dataset_id: str
    image_path: str
    group: str
    masks: dict[UrmindClass, str] = field(default_factory=dict)
    """Máscaras aceitas, já na taxonomia canônica."""

    rejected: tuple[RejectedLabel, ...] = ()

    @property
    def usable(self) -> bool:
        return bool(self.masks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "image_path": self.image_path,
            "group": self.group,
            "masks": {str(k): v for k, v in self.masks.items()},
            "rejected": [{"label": r.source_label, "reason": r.reason} for r in self.rejected],
        }


@dataclass(frozen=True)
class GeoRecord:
    """Ocorrência georreferenciada sem anotação de imagem utilizável."""

    dataset_id: str
    external_id: str
    latitude: float
    longitude: float
    group: str
    kind: str
    """Rótulo da fonte, no vocabulário dela. Não é classe do UrMind."""

    human_confirmed: bool | None
    """`None` quando a fonte não informa. Saída de modelo não vira ground truth."""

    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not -90.0 <= self.latitude <= 90.0:
            raise ValueError(f"latitude fora de faixa: {self.latitude}")
        if not -180.0 <= self.longitude <= 180.0:
            raise ValueError(f"longitude fora de faixa: {self.longitude}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "external_id": self.external_id,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "group": self.group,
            "kind": self.kind,
            "human_confirmed": self.human_confirmed,
            "attributes": self.attributes,
        }


@dataclass(frozen=True)
class Keypoint:
    """Ponto anotado em pixels da imagem a que pertence."""

    x: float
    y: float
    kind: str
    """Vocabulário da fonte (ex.: `curb_ramp`). Não é classe do §8.2."""


@dataclass(frozen=True)
class KeypointSample:
    """Imagem com pontos anotados, sem extensão de caixa nem de máscara.

    `image_ref` não é um caminho de arquivo comum: nos pacotes Parquet do
    Project Sidewalk e do RampNet a imagem vive *dentro* da linha. A referência
    é `<parquet relativo ao raw>#row=<índice>`, que é reproduzível e não exige
    extrair 12 GB de JPEG para o disco só para poder citá-los.
    """

    dataset_id: str
    image_ref: str
    width: int
    height: int
    group: str
    keypoints: tuple[Keypoint, ...] = ()
    official_split: str | None = None
    rejected: tuple[RejectedLabel, ...] = ()
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        """Tem ao menos um ponto. Não implica ser treinável na V1 (§8.2)."""
        return bool(self.keypoints)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "image_ref": self.image_ref,
            "width": self.width,
            "height": self.height,
            "group": self.group,
            "official_split": self.official_split,
            "keypoints": [{"x": k.x, "y": k.y, "kind": k.kind} for k in self.keypoints],
            "rejected": [{"label": r.source_label, "reason": r.reason} for r in self.rejected],
            "attributes": self.attributes,
        }
