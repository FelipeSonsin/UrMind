"""Prepara a folha de auditoria HUMANA das caixas candidatas do UNIVALI.

O que falta para as caixas deixarem de ser candidatas não é código: é alguém
olhar. A hipótese em julgamento é *cada componente conexo da máscara é um objeto
independente*, e a fonte publica máscara semântica, sem id de instância — logo o
pixel não responde. Dois fragmentos podem ser um buraco partido por um remendo,
dois buracos vizinhos, ou ruído de anotação.

Este script **não decide nada**. Ele monta uma amostra estratificada, gera a
folha com uma linha por componente e deixa `decision` vazio. Preencher é trabalho
humano; enquanto estiver vazio, a auditoria conta como não realizada, e
`validate_univali_boxes.py` continua reportando `instance_semantics_validated`
como falso.

    python -B scripts/datasets/prepare_univali_human_audit.py
    python -B scripts/datasets/prepare_univali_human_audit.py --per-stratum 8
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict

from _core import (
    DATASETS_DIR,
    PROJECT_ROOT,
    configure_stdout,
    file_sha256,
    provenance,
    require_local,
    write_json_report,
    write_text_safe,
)

BOXES_MANIFEST = DATASETS_DIR / "manifests" / "univali_br_boxes.jsonl"
SHEET = DATASETS_DIR / "annotations" / "univali_instance_audit_v1.jsonl"
REPORT_NAME = "univali_human_audit_status.json"
AUDIT_VERSION = "univali-instance-audit-v1"
EMPTY_MASK_STATUS = "EMPTY_MASK_SEMANTICS_UNRESOLVED"

# Vocabulário fechado de decisão. Fechado de propósito: uma resposta livre não
# se agrega, e "aprovado" precisa significar a mesma coisa para todo revisor.
DECISIONS = (
    "independent_object",  # é um objeto por si só
    "fragment_of_same_object",  # é parte de outro componente da mesma imagem
    "noise",  # não é dano; artefato de anotação
    "ambiguous",  # a imagem não permite decidir
    "invalid_annotation",  # a máscara está errada aqui
    "other_problem",  # descrever em `note`
)

# Estratos que precisam aparecer na amostra. Cada um é um jeito diferente de a
# hipótese falhar, então nenhum pode ficar de fora por sorteio.
STRATA = (
    "single_component",
    "multi_component",
    "many_components",
    "tiny_component",
    "single_pixel",
    "large_component",
    "touches_border",
    "overlapping_boxes",
)


def load_rows() -> list[dict]:
    return [
        json.loads(line)
        for line in require_local(BOXES_MANIFEST).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _overlaps(boxes: list[dict]) -> set[int]:
    """Índices de caixas que cruzam alguma outra caixa da mesma imagem."""
    marcados: set[int] = set()
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            if (
                a["xmin"] < b["xmax"]
                and b["xmin"] < a["xmax"]
                and a["ymin"] < b["ymax"]
                and b["ymin"] < a["ymax"]
            ):
                marcados.update((i, j))
    return marcados


def strata_of(row: dict, index: int, box: dict, overlapping: set[int], tiers: dict) -> list[str]:
    total = len(row["boxes"])
    estratos = []
    if total == 1:
        estratos.append("single_component")
    elif total >= 10:
        estratos.append("many_components")
    else:
        estratos.append("multi_component")
    if box.get("single_pixel"):
        estratos.append("single_pixel")
    if box.get("noise_candidate"):
        estratos.append("tiny_component")
    if box.get("size_tier") == "large":
        estratos.append("large_component")
    if box.get("touches_border"):
        estratos.append("touches_border")
    if index in overlapping:
        estratos.append("overlapping_boxes")
    return estratos


def build_sheet(rows: list[dict], per_stratum: int) -> tuple[list[dict], dict]:
    """Amostra estratificada determinística: ordenação, nunca sorteio."""
    candidatos: list[dict] = []
    for row in rows:
        if row.get("mask_status") == EMPTY_MASK_STATUS:
            continue
        overlapping = _overlaps(row["boxes"])
        for index, box in enumerate(row["boxes"]):
            candidatos.append(
                {
                    "row": row,
                    "index": index,
                    "box": box,
                    "strata": strata_of(row, index, box, overlapping, {}),
                }
            )

    por_estrato: dict[str, list[dict]] = defaultdict(list)
    for item in candidatos:
        for estrato in item["strata"]:
            por_estrato[estrato].append(item)

    escolhidos: dict[str, dict] = {}
    cobertura: dict[str, int] = {}
    for estrato in STRATA:
        disponiveis = sorted(
            por_estrato.get(estrato, []),
            key=lambda i: (-i["box"]["mask_area_px"], i["row"]["directory"], i["index"]),
        )
        # Extremos e meio: a amostra precisa das maiores, das menores e do miolo
        # do estrato, senão descreve só uma ponta da distribuição.
        selecao = disponiveis[: max(1, per_stratum // 2)]
        selecao += disponiveis[-(per_stratum - len(selecao)) :] if disponiveis else []
        cobertura[estrato] = len(disponiveis)
        for item in selecao[:per_stratum]:
            chave = f"{item['row']['directory']}#{item['index']}"
            escolhidos.setdefault(chave, item)

    linhas = []
    for chave, item in sorted(escolhidos.items()):
        row, box = item["row"], item["box"]
        linhas.append(
            {
                "audit_version": AUDIT_VERSION,
                "component_id": chave,
                "image_id": row["directory"],
                "image_relpath": row["image_relpath"],
                "mask_relpath": row["mask_relpath"],
                "image_width": row["image_width"],
                "image_height": row["image_height"],
                "group_road": row["group_road"],
                "group_segment": row["group_segment"],
                "component_index": item["index"],
                "components_in_image": len(row["boxes"]),
                "bbox_xyxy": [box["xmin"], box["ymin"], box["xmax"], box["ymax"]],
                "mask_area_px": box["mask_area_px"],
                "box_area_px": box["box_area_px"],
                "fill_ratio": box["fill_ratio"],
                "size_tier": box["size_tier"],
                "touches_border": box["touches_border"],
                "noise_candidate": box.get("noise_candidate"),
                "single_pixel": box.get("single_pixel"),
                "strata": item["strata"],
                "visual_evidence": (
                    "datasets/processed/univali_br/visual_audit/"
                    f"<caso>__{row['directory']}.png (gerar com render_univali_audit.py)"
                ),
                # A decidir por humano. Vazio = não revisado.
                "decision": None,
                "note": None,
                "reviewed_by": None,
                "reviewed_at": None,
            }
        )
    return linhas, cobertura


def audit_status(linhas: list[dict]) -> dict:
    decisoes = Counter(linha["decision"] for linha in linhas)
    pendentes = decisoes.get(None, 0)
    invalidas = sorted(
        {
            str(linha["decision"])
            for linha in linhas
            if linha["decision"] is not None and linha["decision"] not in DECISIONS
        }
    )
    completa = pendentes == 0 and not invalidas and bool(linhas)

    # `ambiguous` e `other_problem` são respostas honestas que **não** resolvem a
    # hipótese: quem revisou olhou e não conseguiu decidir. Uma folha inteira de
    # ambíguas está completa e não valida nada. Por isso a validação sai das
    # decisões registradas, e não de o script ter rodado — mas também não é um
    # `False` fixo, que negava à auditoria qualquer estado terminal de sucesso.
    inconclusivas = sum(
        decisoes.get(d, 0) for d in ("ambiguous", "other_problem")
    )
    conclusivas = (len(linhas) - pendentes) - inconclusivas
    validada = completa and inconclusivas == 0

    if validada:
        motivo = None
    elif not linhas:
        motivo = "a folha está vazia: não há o que revisar nem o que concluir."
    elif pendentes:
        motivo = (
            f"{pendentes} linha(s) sem decisão humana. Enquanto houver pendência, a "
            "hipótese componente=instância continua sem verificação."
        )
    elif invalidas:
        motivo = (
            f"decisões fora do vocabulário fechado: {invalidas}. Elas não se "
            "agregam, então a auditoria não fecha."
        )
    else:
        motivo = (
            f"{inconclusivas} decisão(ões) `ambiguous`/`other_problem`: a revisão "
            "aconteceu e não concluiu. Resolver estes casos é o que falta."
        )

    return {
        "audit_version": AUDIT_VERSION,
        "sheet": SHEET.relative_to(PROJECT_ROOT).as_posix(),
        "rows": len(linhas),
        "reviewed": len(linhas) - pendentes,
        "pending": pendentes,
        "decision_counts": {str(k): v for k, v in sorted(decisoes.items(), key=str)},
        "invalid_decisions": invalidas,
        "allowed_decisions": list(DECISIONS),
        "complete": completa,
        "instance_semantics_validated": validada,
        "why_not_validated": motivo,
        "conclusive_decisions": conclusivas,
        "inconclusive_decisions": inconclusivas,
    }


def main() -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-stratum", type=int, default=6)
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="não regrava a folha; só relata o andamento da revisão humana.",
    )
    args = parser.parse_args()

    if SHEET.is_file():
        linhas = [
            json.loads(line)
            for line in require_local(SHEET).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        cobertura = {}
        if not args.status_only:
            print(
                f"Folha já existe com {len(linhas)} linha(s); preservada para não "
                "apagar revisão humana. Use --status-only para só conferir."
            )
    else:
        if args.status_only:
            raise SystemExit(f"{SHEET.relative_to(PROJECT_ROOT)} ainda não existe")
        rows = load_rows()
        linhas, cobertura = build_sheet(rows, args.per_stratum)
        write_text_safe(
            SHEET, "\n".join(json.dumps(linha, ensure_ascii=False) for linha in linhas) + "\n"
        )
        print(f"Folha criada: {SHEET.relative_to(PROJECT_ROOT)} ({len(linhas)} componentes)")

    estado = audit_status(linhas)
    report = {
        "version": 1,
        "scope": (
            "estado da auditoria humana da hipótese componente conexo = instância. "
            "Este artefato NÃO contém decisão gerada por máquina."
        ),
        **provenance(
            __file__,
            source_dataset="univali_br",
            source_version="mendeley-v4",
            transform="preparação de folha de auditoria humana estratificada",
            params={
                "per_stratum": args.per_stratum,
                "strata": list(STRATA),
                "selection": "determinística por ordenação de área; sem sorteio",
                "audit_version": AUDIT_VERSION,
            },
        ),
        "inputs": len(linhas),
        "outputs": len(linhas),
        "dropped": 0,
        "drop_reasons": {},
        "integrity": {
            "boxes_manifest_sha256": file_sha256(BOXES_MANIFEST),
            "sheet_sha256": file_sha256(SHEET) if SHEET.is_file() else None,
        },
        "raw_modified": False,
        "status": estado,
        "stratum_population": cobertura,
        "how_to_fill": (
            "Para cada linha, abra a evidência visual, decida entre "
            f"{list(DECISIONS)} e preencha `decision`, `note`, `reviewed_by` e "
            "`reviewed_at`. Não deixe decisão fora do vocabulário: ela é contada "
            "como inválida e a auditoria não fecha."
        ),
    }

    if args.no_report:
        print(json.dumps(report["status"], indent=2, ensure_ascii=False))
        return 0

    destino = write_json_report(REPORT_NAME, report)
    print(f"Relatório: {destino.relative_to(PROJECT_ROOT)}")
    print(
        f"  revisadas {estado['reviewed']}/{estado['rows']} | "
        f"instance_semantics_validated={estado['instance_semantics_validated']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
