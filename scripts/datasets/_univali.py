"""Primitivas do UNIVALI/DNIT: nome de pasta, máscara binária e componentes.

Este módulo existe para que a auditoria de semântica, a conversão derivada, a
validação e o split leiam o dataset **do mesmo jeito**. Duas implementações da
mesma regra divergem em silêncio, e a regra aqui é a que sustenta a garantia de
não-vazamento do §8.4.

O que este módulo NÃO faz:

- não traduz `POTHOLE` para nenhuma classe da taxonomia V1. A correspondência
  com `URMIND_ROAD_D40` é declarada pela fonte e ainda não foi verificada de
  forma independente (documentação oficial + inspeção humana). Enquanto isso
  não acontecer, a caixa derivada carrega o rótulo nativo da fonte;
- não decide limiar de área. O limiar é parâmetro do chamador, calculado a
  partir da distribuição real medida — nunca embutido aqui.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "MASK_SUFFIXES",
    "ComponentBox",
    "MaskStats",
    "SampleScan",
    "connected_components",
    "load_binary_mask",
    "parse_sample_name",
    "scan_sample",
]

# Sufixos publicados pelo pacote Mendeley V4. RAW é a imagem; os outros três são
# máscaras. Nenhum outro sufixo aparece nas 2.235 amostras.
MASK_SUFFIXES = ("CRACK", "LANE", "POTHOLE")

# `1007599_RS_386_386RS289112_28920` → id, UF, rodovia, trecho, posição.
#
# Quatro pastas do pacote trazem um campo extra antes da posição
# (`1050564_DF_080_080BDF0050_1_00368`); o `.+?` não-guloso o absorve dentro do
# trecho em vez de deslocar a posição. Para a string do trecho isso dá
# exatamente o mesmo resultado que o fatiamento por `_` de
# `app.datasets.adapters.univali_group` — conferido nas 2.235 pastas, zero
# divergências. O motivo de existir esta regex é outro: ela também devolve UF,
# rodovia e posição em campos separados, que o fatiamento não dá e de que o
# split por rodovia depende.
#
# O que fica registrado como DESCONHECIDO: se `080BDF0050_1` e `080BDF0050`
# nomeiam o mesmo trecho. A fonte não diz. Uni-los seria supor; por isso o split
# padrão agrupa por rodovia, nível em que os dois caem juntos sem adivinhação.
_NAME = re.compile(
    r"^(?P<image_id>\d+)_(?P<uf>[A-Z]{2})_(?P<road>\d+)_(?P<segment>.+?)_(?P<position>\d+)$"
)


@dataclass(frozen=True)
class SampleName:
    """Identificadores extraídos do nome da pasta. `parsed=False` → não confie."""

    directory: str
    parsed: bool
    image_id: str | None = None
    uf: str | None = None
    road: str | None = None
    segment: str | None = None
    position: int | None = None

    @property
    def group_segment(self) -> str:
        """Trecho de rodovia: o agrupamento mais fino que a fonte declara."""
        if not self.parsed:
            return self.directory
        return f"{self.uf}_{self.road}_{self.segment}"

    @property
    def group_road(self) -> str:
        """Rodovia inteira: agrupamento mais conservador que o trecho."""
        if not self.parsed:
            return self.directory
        return f"{self.uf}_{self.road}"

    @property
    def group_uf(self) -> str:
        """Unidade federativa: o agrupamento mais grosso disponível."""
        if not self.parsed:
            return self.directory
        return str(self.uf)


def parse_sample_name(directory_name: str) -> SampleName:
    """Lê os identificadores do nome da pasta sem inventar os que faltarem."""
    match = _NAME.match(directory_name)
    if match is None:
        return SampleName(directory=directory_name, parsed=False)
    data = match.groupdict()
    return SampleName(
        directory=directory_name,
        parsed=True,
        image_id=data["image_id"],
        uf=data["uf"],
        road=data["road"],
        segment=data["segment"],
        position=int(data["position"]),
    )


@dataclass(frozen=True)
class ComponentBox:
    """Uma região conexa da máscara e a caixa que a envolve.

    Coordenadas em pixels, meio-aberto: `xmin`/`ymin` inclusivos, `xmax`/`ymax`
    exclusivos. Assim `xmax - xmin` é a largura em pixels e uma região de um
    único pixel vira uma caixa 1x1 válida, em vez de degenerada.
    """

    xmin: int
    ymin: int
    xmax: int
    ymax: int
    area_px: int
    touches_border: bool

    @property
    def width(self) -> int:
        return self.xmax - self.xmin

    @property
    def height(self) -> int:
        return self.ymax - self.ymin

    @property
    def box_area(self) -> int:
        return self.width * self.height

    @property
    def fill_ratio(self) -> float:
        """Área da região sobre a área da caixa. Baixo = região alongada/diagonal."""
        return self.area_px / self.box_area if self.box_area else 0.0

    def as_dict(self) -> dict:
        return {
            "xmin": self.xmin,
            "ymin": self.ymin,
            "xmax": self.xmax,
            "ymax": self.ymax,
            "width": self.width,
            "height": self.height,
            "area_px": self.area_px,
            "box_area_px": self.box_area,
            "fill_ratio": round(self.fill_ratio, 4),
            "touches_border": self.touches_border,
        }


def load_binary_mask(path: Path) -> tuple[Any, dict]:
    """Devolve a máscara booleana e o que foi observado nos pixels.

    Não assume que branco é foreground: reporta os valores encontrados para que
    a convenção seja registrada como evidência, não como suposição.
    """
    import numpy as np
    from PIL import Image

    with Image.open(path) as image:
        image.load()
        mode = image.mode
        size = image.size
        array = np.asarray(image)

    if array.ndim == 3:
        flat = array.reshape(-1, array.shape[2])
        channels_equal = bool((flat[:, 0] == flat[:, 1]).all() and (flat[:, 1] == flat[:, 2]).all())
        intensity = array[:, :, 0]
    else:
        flat = array.reshape(-1, 1)
        channels_equal = True
        intensity = array

    unique = sorted(int(v) for v in set(flat[:, 0].tolist()))
    observed = {
        "mode": mode,
        "width": size[0],
        "height": size[1],
        "unique_values": unique[:16],
        "unique_value_count": len(unique),
        "channels_equal": channels_equal,
        "is_binary_0_255": unique in ([0], [255], [0, 255]),
        "has_palette": mode == "P",
    }
    return intensity > 0, observed


def connected_components(mask, *, connectivity: int = 8) -> list[ComponentBox]:
    """Regiões conexas da máscara booleana, como caixas envolventes.

    `connectivity=8` une pixels que se tocam na diagonal. É a escolha registrada
    para o UNIVALI: uma região anotada à mão frequentemente se estreita até uma
    ponte diagonal de um pixel, e com 4-conectividade ela viraria duas caixas
    sobre o mesmo objeto — o que inflaria a contagem sem que a fonte tenha
    anotado dois objetos. `connectivity=4` fica disponível para comparação.

    Implementação por união de intervalos (run-length) linha a linha: evita
    dependência de scipy/OpenCV, que não estão no ambiente do projeto.
    """
    import numpy as np

    if connectivity not in (4, 8):
        raise ValueError("connectivity precisa ser 4 ou 8")

    height, width = mask.shape
    parent: dict[int, int] = {}

    def find(item: int) -> int:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[b] = a

    runs: list[tuple[int, int, int]] = []
    previous: list[tuple[int, int, int]] = []
    slack = 1 if connectivity == 8 else 0

    for y in range(height):
        row = mask[y]
        edges = np.flatnonzero(np.diff(np.concatenate(([0], row.view(np.int8), [0]))))
        current: list[tuple[int, int, int]] = []
        for index in range(0, len(edges), 2):
            x0, x1 = int(edges[index]), int(edges[index + 1]) - 1
            run_id = len(runs)
            runs.append((y, x0, x1))
            parent[run_id] = run_id
            for previous_id, px0, px1 in previous:
                if px0 - slack <= x1 and x0 <= px1 + slack:
                    union(previous_id, run_id)
            current.append((run_id, x0, x1))
        previous = current

    grouped: dict[int, list[int]] = defaultdict(lambda: [10**9, 10**9, -1, -1, 0])
    for run_id, (y, x0, x1) in enumerate(runs):
        root = find(run_id)
        box = grouped[root]
        box[0] = min(box[0], x0)
        box[1] = min(box[1], y)
        box[2] = max(box[2], x1)
        box[3] = max(box[3], y)
        box[4] += x1 - x0 + 1

    components = []
    for xmin, ymin, xmax_inclusive, ymax_inclusive, area in grouped.values():
        xmax, ymax = xmax_inclusive + 1, ymax_inclusive + 1
        components.append(
            ComponentBox(
                xmin=xmin,
                ymin=ymin,
                xmax=xmax,
                ymax=ymax,
                area_px=area,
                touches_border=(
                    xmin == 0 or ymin == 0 or xmax == width or ymax == height
                ),
            )
        )
    components.sort(key=lambda c: (-c.area_px, c.ymin, c.xmin))
    return components


@dataclass
class MaskStats:
    """O que foi medido numa máscara. Vazia é resultado, não erro."""

    label: str
    present: bool
    foreground_px: int = 0
    observed: dict = field(default_factory=dict)
    components: list[ComponentBox] = field(default_factory=list)
    error: str | None = None

    @property
    def empty(self) -> bool:
        return self.present and self.error is None and self.foreground_px == 0


@dataclass
class SampleScan:
    """Uma amostra do UNIVALI, medida. Nada aqui é inferido do nome do arquivo."""

    directory: str
    name: SampleName
    image_relpath: str
    image_width: int | None = None
    image_height: int | None = None
    image_error: str | None = None
    masks: dict = field(default_factory=dict)
    missing_masks: list = field(default_factory=list)
    extra_files: list = field(default_factory=list)


def scan_sample(sample_dir: Path, project_root: Path, *, connectivity: int = 8) -> SampleScan:
    """Mede uma amostra inteira: imagem, três máscaras e as regiões de cada uma."""
    from PIL import Image

    name = parse_sample_name(sample_dir.name)
    prefix = sample_dir.name
    image_path = sample_dir / f"{prefix}_RAW.jpg"

    scan = SampleScan(
        directory=sample_dir.name,
        name=name,
        image_relpath=image_path.relative_to(project_root).as_posix(),
    )

    if not image_path.is_file():
        scan.image_error = "imagem RAW ausente"
    else:
        try:
            with Image.open(image_path) as image:
                image.load()  # decodificação completa: detecta truncamento
                scan.image_width, scan.image_height = image.size
        except Exception as exc:  # noqa: BLE001 - erro da fonte é dado, não exceção
            scan.image_error = f"{type(exc).__name__}: {exc}"

    known = {f"{prefix}_RAW.jpg"}
    for label in MASK_SUFFIXES:
        mask_path = sample_dir / f"{prefix}_{label}.png"
        known.add(mask_path.name)
        if not mask_path.is_file():
            scan.missing_masks.append(label)
            scan.masks[label] = MaskStats(label=label, present=False)
            continue
        try:
            mask, observed = load_binary_mask(mask_path)
            scan.masks[label] = MaskStats(
                label=label,
                present=True,
                foreground_px=int(mask.sum()),
                observed=observed,
                components=connected_components(mask, connectivity=connectivity),
            )
        except Exception as exc:  # noqa: BLE001
            scan.masks[label] = MaskStats(
                label=label, present=True, error=f"{type(exc).__name__}: {exc}"
            )

    scan.extra_files = sorted(p.name for p in sample_dir.iterdir() if p.name not in known)
    return scan
