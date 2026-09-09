"""Leitura da taxonomia e do mapeamento canônico (datasets/metadata/).

Os scripts de auditoria não podem carregar o mapa de classes em código: se o
mapa vivesse dentro do script, mudar a taxonomia exigiria mudar o auditor, e as
duas coisas passariam a divergir sem ninguém perceber. O mapa é dado, versionado
em YAML, e este módulo é a única porta de entrada dele.

Uma validação acontece no carregamento e não é opcional: **nenhum rótulo externo
pode ser mapeado para `URMIND_UNKNOWN`**. O §31.8 do MASTER_PLAN trata esse id
como estado de aplicação, não como classe treinável, e um mapeamento que o
usasse produziria um dataset com uma classe que o sistema nunca deveria treinar.
Se isso aparecer no YAML, o carregamento falha — não avisa e continua.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from _core import DATASETS_DIR, require_local

__all__ = ["load_class_mapping", "load_taxonomy", "trainable_classes"]

TAXONOMY_PATH = DATASETS_DIR / "metadata" / "taxonomy.yaml"
MAPPING_PATH = DATASETS_DIR / "metadata" / "class_mapping.yaml"


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"metadado ausente: {path}")
    texto = require_local(path).read_text(encoding="utf-8-sig")
    try:
        import yaml  # type: ignore[import-untyped]

        return yaml.safe_load(texto) or {}
    except ImportError:
        from _budget import _minimal_yaml

        return _minimal_yaml(texto)


@lru_cache(maxsize=1)
def load_taxonomy() -> dict:
    return _read_yaml(TAXONOMY_PATH)


@lru_cache(maxsize=1)
def trainable_classes() -> tuple[str, ...]:
    dados = load_taxonomy()
    return tuple(c["id"] for c in dados.get("classes", []) if c.get("trainable_v1"))


@lru_cache(maxsize=8)
def load_class_mapping(dataset_id: str) -> dict[str, Any]:
    """Mapa de um dataset, já validado contra a taxonomia.

    Devolve `accepted` (rótulo → classe canônica, com as chaves também em
    maiúsculas para tolerar variação de caixa) e `rejected` (rótulo → motivo).
    """
    dados = _read_yaml(MAPPING_PATH)
    cfg = (dados.get("datasets") or {}).get(dataset_id)
    if cfg is None:
        conhecidos = ", ".join(sorted(dados.get("datasets") or {}))
        raise KeyError(
            f"dataset '{dataset_id}' não está em class_mapping.yaml; conhecidos: {conhecidos}"
        )

    aceitos_brutos = cfg.get("accepted") or {}
    treinaveis = set(trainable_classes())

    aceitos: dict[str, str] = {}
    for rotulo, canonica in aceitos_brutos.items():
        if canonica == "URMIND_UNKNOWN":
            raise ValueError(
                f"class_mapping.yaml: '{dataset_id}.{rotulo}' aponta para URMIND_UNKNOWN, "
                "que é estado de aplicação e não classe treinável (§31.8)"
            )
        if canonica not in treinaveis:
            raise ValueError(
                f"class_mapping.yaml: '{dataset_id}.{rotulo}' aponta para '{canonica}', "
                f"que não é classe treinável da V1; treináveis: {sorted(treinaveis)}"
            )
        aceitos[str(rotulo)] = canonica
        aceitos[str(rotulo).upper()] = canonica

    return {
        "dataset_id": dataset_id,
        "accepted": aceitos,
        "rejected": {str(k): str(v) for k, v in (cfg.get("rejected") or {}).items()},
        "annotation_format": cfg.get("annotation_format"),
        "status": cfg.get("status"),
        "raw": cfg,
    }
