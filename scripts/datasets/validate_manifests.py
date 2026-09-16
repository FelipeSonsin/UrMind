"""Confere cada versão derivada contra o contrato de proveniência (§10.2).

Uma derivada — poda, subconjunto, split, reconciliação — só é reproduzível se
disser de onde veio, com que parâmetros, o que descartou e por quê. O contrato
está em `datasets/metadata/artifact_contract.yaml`, seção
`derived_manifest_contract`; este script diz quais artefatos o cumprem.

Ele **não corrige** artefato histórico. Reescrever a posteriori um relatório que
registrou uma execução real transformaria evidência em narrativa: o que faltou
naquela execução faltou mesmo. O que o script produz é a lista do que falta, para
que os geradores passem a emitir o campo nas próximas execuções.

Os nomes dos campos variam entre os artefatos porque foram escritos em momentos
diferentes; `FIELD_ALIASES` mapeia as grafias já usadas para o nome do contrato,
em vez de exigir que os arquivos existentes sejam renomeados.

A busca é **por caminho declarado**, nunca por chave solta em qualquer
profundidade. A diferença não é estética: com busca achatada, o `version` do
cabeçalho de todo relatório satisfazia `source_version`, e qualquer `note`
enterrado em um bloco de comentário satisfazia `drop_reasons`. O artefato saía
conforme sem ter declarado nem a versão da fonte nem o motivo dos descartes — que
é exatamente o que o contrato existe para exigir.

    python -B scripts/datasets/validate_manifests.py
"""

from __future__ import annotations

import json
from pathlib import Path

from _core import (
    DATASETS_DIR,
    PROJECT_ROOT,
    configure_stdout,
    require_local,
    write_json_report,
)

# Artefatos que são versões derivadas de um dataset. Relatório de auditoria
# (que apenas descreve o disco, sem produzir recorte) não entra aqui.
DERIVED = (
    "reports/rdd2022_reduction.json",
    "reports/rdd2022_reconciliation.json",
    "reports/rdd2022_subset_proposal.json",
    "splits/rdd2022_subset_splits.json",
    # Derivadas do UNIVALI. Estas nascem com o bloco de `_core.provenance()`, que
    # emite os campos do contrato — inclusive `script`, que faltava em todas as
    # quatro derivadas históricas acima.
    "reports/univali_mask_semantics.json",
    "reports/univali_conversion.json",
    "reports/univali_box_validation.json",
    "reports/univali_visual_audit.json",
    "reports/univali_human_audit_status.json",
    "reports/univali_crack_inventory.json",
    "splits/univali_br_external_test_splits.json",
    # Derivadas do Urban Community (reforço de treino).
    "reports/urban_community_audit.json",
    "reports/urban_community_conversion.json",
    "reports/urban_community_validation.json",
    "reports/urban_community_visual_audit.json",
    "reports/urban_community_human_audit_status.json",
)

# Nome do contrato → caminhos aceitos, em ordem de preferência. Cada caminho é
# uma posição concreta no documento: `"version"` é a chave de topo, e
# `"source.version"` é `version` dentro do bloco `source`. Nada é procurado
# "em algum lugar" — um campo enterrado onde o contrato não previu não conta.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "source_dataset": ("source_dataset", "dataset", "source.dataset", "source.id"),
    "source_version": ("source_version", "source.version", "dataset_version"),
    "transform": ("transform", "algorithm", "selection_policy"),
    "params": ("params", "seed", "algorithm_version", "size_policy"),
    "script": ("script", "generator", "generated_by"),
    "generated_at": ("generated_at", "updated", "date"),
    "inputs": ("inputs", "before", "full_set", "source_inventory_sha256"),
    "outputs": ("outputs", "after", "kept", "subset", "splits"),
    "dropped": ("dropped", "removed", "excluded_exact_copies"),
    "drop_reasons": ("drop_reasons", "reasons"),
    "integrity": (
        "integrity",
        "manifest_sha256",
        "integrity_ok",
        "leakage_check",
        "archive_md5",
    ),
}

# `null` não é ausência: um campo pode existir e estar declaradamente vazio
# (`drop_reasons: {}` quando nada foi descartado). O que não conta é a chave não
# existir. `None` como valor conta como declarado; a distinção é registrada para
# quem ler o relatório.
_MISSING = object()


def _at(payload: object, dotted: str) -> object:
    """Valor no caminho pontuado, ou `_MISSING` se o caminho não existir."""
    atual = payload
    for parte in dotted.split("."):
        if not isinstance(atual, dict) or parte not in atual:
            return _MISSING
        atual = atual[parte]
    return atual


def _check(path: Path) -> dict:
    payload = json.loads(require_local(path).read_text(encoding="utf-8-sig"))

    campos, faltando, vazios = {}, [], []
    for campo, caminhos in FIELD_ALIASES.items():
        achado = next((c for c in caminhos if _at(payload, c) is not _MISSING), None)
        campos[campo] = achado
        if achado is None:
            faltando.append(campo)
        elif _at(payload, achado) is None:
            vazios.append(campo)

    return {
        "path": path.relative_to(PROJECT_ROOT).as_posix(),
        "fields": campos,
        "missing": faltando,
        "declared_null": vazios,
        "compliant": not faltando,
    }


def main() -> int:
    configure_stdout()
    resultados = []
    for rel in DERIVED:
        caminho = DATASETS_DIR / rel
        if not caminho.is_file():
            resultados.append(
                {
                    "path": f"datasets/{rel}",
                    "missing": ["ARQUIVO AUSENTE"],
                    "compliant": False,
                }
            )
            continue
        resultados.append(_check(caminho))

    conformes = [r for r in resultados if r["compliant"]]
    payload = {
        "version": 1,
        "contract": "datasets/metadata/artifact_contract.yaml#derived_manifest_contract",
        "generated_by": "scripts/datasets/validate_manifests.py",
        "scope": (
            "presença dos campos de proveniência exigidos, conferida no caminho "
            "em que o contrato os espera — não em qualquer profundidade. Não "
            "valida o conteúdo deles, e não corrige artefato histórico."
        ),
        "matching": "por caminho declarado (`a.b`), nunca por chave solta aninhada",
        "compliant": len(conformes),
        "total": len(resultados),
        "artifacts": resultados,
    }
    destino = write_json_report("derived_manifest_validation.json", payload)

    print(f"{destino.relative_to(PROJECT_ROOT).as_posix()}")
    print(f"  {len(conformes)}/{len(resultados)} derivadas cumprem o contrato")
    for resultado in resultados:
        if not resultado["compliant"]:
            print(f"  {resultado['path']}: falta {', '.join(resultado['missing'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
