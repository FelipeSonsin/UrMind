"""Camada de dataset do UrMind — passo 6 do §25.

Ordem em que as peças se encaixam:

    catalog.py      o que cada fonte é, permite e não resolve
    inventory.py    o que existe de fato em datasets/raw/
    adapters.py     leitura do formato de cada fonte
    records.py      formato canônico comum a todas
    registration.py de pasta verificada para linha em dataset_versions
    cli.py          `python -m app.datasets.cli`

Nada aqui baixa arquivo, extrai ZIP ou grava no banco. São operações que gastam
disco, rede ou cota e continuam sendo decisão explícita de quem opera.
"""

from app.datasets.catalog import SOURCES, DatasetRole, DatasetSource, get_source
from app.datasets.inventory import DatasetInventory, DatasetState, inspect_all, inspect_source
from app.datasets.records import AnnotatedImage, BoundingBox, GeoRecord, MaskSample
from app.datasets.registration import (
    DatasetSummary,
    RegistrationRefused,
    build_dataset_version,
    summarize,
)

__all__ = [
    "SOURCES",
    "AnnotatedImage",
    "BoundingBox",
    "DatasetInventory",
    "DatasetRole",
    "DatasetSource",
    "DatasetState",
    "DatasetSummary",
    "GeoRecord",
    "MaskSample",
    "RegistrationRefused",
    "build_dataset_version",
    "get_source",
    "inspect_all",
    "inspect_source",
    "summarize",
]
