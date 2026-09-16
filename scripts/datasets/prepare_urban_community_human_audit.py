"""Folha de auditoria HUMANA, caixa a caixa, do reforço do Urban Community.

O mapeamento `pothole → URMIND_ROAD_D40` está aprovado **como classe**: o rótulo
de origem corresponde à definição canônica da classe. Isso é
`semantic_mapping_approved`, e não é o mesmo que dizer que cada uma das 451
caixas está correta.

A própria fonte já mostrou por que a distinção importa:

- trinca sem cavidade rotulada dentro de `pothole`;
- bueiro aberto rotulado dentro de `pothole`;
- imagem com vários buracos visíveis e só parte deles anotada — que é o defeito
  mais caro dos três, porque ensina falso negativo.

Nenhum desses três é detectável por geometria. A quarentena automática não os
pega, o dHash não os pega, e a inspeção assistida por modelo que sustentou a
categoria não responde por caixa individual. Só revisão humana responde.

Este script **não decide nada e não inventa resultado**. Ele monta uma amostra
estratificada determinística, grava uma linha por caixa com `decision` vazio, e
relata o andamento. Enquanto houver linha pendente,
`box_level_human_validation_complete` é falso e as caixas continuam **candidatas**
a TRAIN_REINFORCEMENT — não ground truth humano certificado.

    python -B scripts/datasets/prepare_urban_community_human_audit.py
    python -B scripts/datasets/prepare_urban_community_human_audit.py --status-only
"""

from __future__ import annotations

import argparse
import json
import sys
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

sys.path.insert(0, str(PROJECT_ROOT / "backend"))
from app.datasets.authorization import (
    DUPLICATE_DECISIONS as AUTH_DUPLICATE_DECISIONS,
)
from app.datasets.authorization import (
    DUPLICATE_PENDING as AUTH_DUPLICATE_PENDING,
)
from app.datasets.authorization import (
    HUMAN_DECISIONS,
    HUMAN_REVIEW_PENDING,
)

BOXES_MANIFEST = DATASETS_DIR / "manifests" / "urban_community_boxes.jsonl"
SCAN_MANIFEST = DATASETS_DIR / "manifests" / "urban_community_scan.jsonl"
CONVERSION_REPORT = DATASETS_DIR / "reports" / "urban_community_conversion.json"
VALIDATION_REPORT = DATASETS_DIR / "reports" / "urban_community_validation.json"
SHEET = DATASETS_DIR / "annotations" / "urban_community_box_audit_v1.jsonl"
DUP_SHEET = DATASETS_DIR / "annotations" / "urban_community_duplicate_audit_v1.jsonl"
REPORT_NAME = "urban_community_human_audit_status.json"
AUDIT_VERSION = "urban-community-box-audit-v1"

# O vocabulário é o do módulo de autorização — o mesmo que decide os portões.
# Duplicá-lo aqui deixaria a folha aceitar uma palavra que o portão não conhece.
DECISIONS = HUMAN_DECISIONS

# Decisões que **aprovam** a caixa. As demais a tiram, ou a deixam sem resposta
# — e sem resposta não é aprovação.
CONCLUSIVE_APPROVALS = ("approved_pothole",)
CONCLUSIVE_REJECTIONS = ("wrong_class", "annotation_incomplete", "bad_box", "duplicate")
INCONCLUSIVE = ("ambiguous", "other")

# Decisão de DUPLICATA é outra pergunta, e por isso tem folha própria.
#
# Aprovar uma caixa responde "isto é um buraco?". Não responde "estas duas
# imagens são a mesma cena?". Reaproveitar a primeira como resposta da segunda
# deixaria um par dHash 0 ser liberado sem que ninguém olhasse a duplicação — e
# a mesma cena entraria duas vezes no reforço, que é exatamente o peso inflado
# que a remoção das cópias byte a byte existiu para evitar.
# O vocabulário vem do módulo de autorização, que é quem aplica a decisão.
DUPLICATE_DECISIONS = AUTH_DUPLICATE_DECISIONS
DUPLICATE_PENDING = AUTH_DUPLICATE_PENDING

# Protocolo de aceitação, declarado ANTES de qualquer decisão. Escrever isto
# depois de olhar os resultados seria escolher o critério que produz o número
# desejado; por isso ele nasce junto da folha e vai para o relatório.
ACCEPTANCE_PROTOCOL = {
    "approved_pothole": [
        "a região mostra claramente cavidade ou depressão do pavimento;",
        (
            "a caixa envolve o objeto de forma suficiente — sem cortar metade "
            "dele nem cobrir meio quadro;"
        ),
        "não é apenas trinca, mesmo trinca larga, sem perda de material;",
        "não é bueiro, tampa, grelha ou boca de lobo;",
        "não é remendo, sombra, mancha de óleo, poça ou emenda de asfalto;",
        "não é ambígua a ponto de deixar o rótulo inseguro.",
    ],
    "wrong_class": "o conteúdo é outro objeto — trinca, bueiro, remendo, sombra.",
    "annotation_incomplete": (
        "há buraco visível NESTA imagem sem caixa. É defeito da IMAGEM: marque "
        "mesmo quando a caixa desta linha estiver correta, porque a imagem "
        "inteira ensina falso negativo."
    ),
    "bad_box": "é buraco, mas a caixa está mal colocada ou mal dimensionada.",
    "ambiguous": "a imagem não permite decidir. Não é aprovação nem recusa.",
    "duplicate": "a mesma cena já aparece em outra amostra do reforço.",
    "other": "qualquer outro problema; descreva em `note`.",
    "multiple_problems": (
        "escolha a decisão mais grave e descreva as demais em `note`. Nenhuma "
        "observação se perde."
    ),
    "image_level_rule": (
        "uma caixa correta NÃO torna a imagem segura. Se houver buraco não "
        "anotado, a imagem inteira fica com `annotation_completeness=INCOMPLETE` "
        "e nenhuma caixa dela é autorizada."
    ),
    "declared_before_review": True,
}

# Cada estrato é um modo diferente de a caixa estar errada. Nenhum pode faltar
# por sorteio — por isso a seleção é por ordenação, nunca aleatória.
STRATA = (
    "near_area_floor",  # logo acima do piso heurístico: o limiar é arbitrário
    "quarantined",  # abaixo do piso: retidas, precisam de veredito humano
    "small_box",
    "medium_box",
    "large_box",
    "near_full_frame",
    "single_box_image",  # onde anotação incompleta é mais provável
    "many_boxes_image",
    "near_duplicate",  # cenas quase repetidas apontadas pela validação
)


def load_rows() -> list[dict]:
    return [
        json.loads(line)
        for line in require_local(BOXES_MANIFEST)
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]


def identical_pairs() -> list[dict]:
    """Pares dHash 0 apontados pela validação. Sem ela, não há o que revisar."""
    if not VALIDATION_REPORT.is_file():
        return []
    try:
        relatorio = json.loads(
            require_local(VALIDATION_REPORT).read_text(encoding="utf-8-sig")
        )
    except (OSError, RuntimeError, json.JSONDecodeError):
        return []
    return relatorio.get("duplicates", {}).get("identical_dhash_pairs", [])


def build_duplicate_sheet(rows: list[dict]) -> list[dict]:
    """Uma linha por par idêntico, com o metadado que a decisão exige.

    dHash 0 diz que as duas imagens produzem o mesmo hash perceptual. Isso é
    forte, e não é prova: decidir automaticamente que são a mesma cena seria
    inventar a decisão humana que este projeto se recusa a inventar.
    """
    por_stem = {row["stem"]: row for row in rows}
    # Mesma âncora das decisões de caixa: o scan, que não é reescrito pela
    # conversão. Sem ela a decisão não prova sobre qual par foi tomada.
    scan_sha = file_sha256(SCAN_MANIFEST)
    linhas = []
    for par in identical_pairs():
        a, b = str(par["a"]), str(par["b"])
        linha_a, linha_b = por_stem.get(a), por_stem.get(b)
        linhas.append(
            {
                "audit_version": AUDIT_VERSION,
                "pair_id": f"{a}__{b}",
                "source_scan": SCAN_MANIFEST.relative_to(PROJECT_ROOT).as_posix(),
                "source_scan_sha256": scan_sha,
                "a": a,
                "b": b,
                "dhash_distance": par["distance"],
                "a_image_relpath": linha_a["image_relpath"] if linha_a else None,
                "b_image_relpath": linha_b["image_relpath"] if linha_b else None,
                "a_boxes": len(linha_a["boxes"]) if linha_a else None,
                "b_boxes": len(linha_b["boxes"]) if linha_b else None,
                "a_size": [linha_a["image_width"], linha_a["image_height"]]
                if linha_a
                else None,
                "b_size": [linha_b["image_width"], linha_b["image_height"]]
                if linha_b
                else None,
                "visual_evidence": (
                    "datasets/processed/urban_community/visual_audit/duplicates/"
                    f"pair_{a}__{b}.jpg"
                ),
                "visual_evidence_howto": (
                    "gerar com: python -B scripts/datasets/"
                    "render_urban_community_audit.py --duplicate-panels"
                ),
                "status": DUPLICATE_PENDING,
                "allowed_decisions": list(DUPLICATE_DECISIONS),
                "decision_effect": (
                    "same_scene_keep_a: `a` segue para os demais portões e `b` "
                    "sai do treino com todas as caixas; same_scene_keep_b: o "
                    "inverso; different_scenes: as duas seguem; ambiguous ou "
                    "vazio: as duas continuam bloqueadas"
                ),
                # A decidir por humano. Vazio = par continua bloqueando.
                "decision": None,
                "note": None,
                "reviewed_by": None,
                "reviewed_at": None,
            }
        )
    return linhas


def _near_duplicate_stems() -> set[str]:
    """Stems que a validação apontou como quase-duplicata, quando ela existe."""
    if not VALIDATION_REPORT.is_file():
        return set()
    try:
        relatorio = json.loads(
            require_local(VALIDATION_REPORT).read_text(encoding="utf-8-sig")
        )
    except (OSError, RuntimeError, json.JSONDecodeError):
        return set()
    pares = relatorio.get("duplicates", {}).get("near_duplicate_pairs", [])
    return {str(p[lado]) for p in pares for lado in ("a", "b") if lado in p}


def _area_floor() -> int | None:
    """Piso heurístico usado na conversão. É gatilho de revisão, não regra."""
    if not CONVERSION_REPORT.is_file():
        return None
    try:
        conversao = json.loads(
            require_local(CONVERSION_REPORT).read_text(encoding="utf-8-sig")
        )
    except (OSError, RuntimeError, json.JSONDecodeError):
        return None
    valor = conversao.get("params", {}).get("tiny_floor_px")
    return int(valor) if isinstance(valor, int) else None


def strata_of(row: dict, box: dict, floor: int | None, quase: set[str]) -> list[str]:
    area = box["width"] * box["height"]
    estratos: list[str] = []

    if box["quarantine_reasons"]:
        estratos.append("quarantined")
    elif floor and area < floor * 3:
        # A vizinhança imediata do piso é onde a arbitrariedade do percentil
        # aparece: 224 px² fica retido e 240 px² passa, sem nada que separe os
        # dois além da estatística da pasta.
        estratos.append("near_area_floor")

    if box["area_fraction"] > 0.80:
        estratos.append("near_full_frame")
    elif area < 5_000:
        estratos.append("small_box")
    elif area < 60_000:
        estratos.append("medium_box")
    else:
        estratos.append("large_box")

    if len(row["boxes"]) == 1:
        estratos.append("single_box_image")
    elif len(row["boxes"]) >= 5:
        estratos.append("many_boxes_image")

    if row["stem"] in quase:
        estratos.append("near_duplicate")
    return estratos


def build_sheet(rows: list[dict], per_stratum: int) -> tuple[list[dict], dict]:
    """Amostra estratificada determinística: ordenação por área, sem sorteio."""
    floor = _area_floor()
    quase = _near_duplicate_stems()
    # Identidade do que está sendo auditado. `stem#index` é reutilizável: se as
    # caixas de uma imagem forem reordenadas ou substituídas, o mesmo id passa a
    # apontar para outra geometria, e uma aprovação antiga migraria para uma
    # caixa que ninguém olhou. O hash fixa a versão; a bbox fixa o objeto.
    #
    # A âncora é o SCAN, não a derivada. A derivada é reescrita pelo próprio
    # conversor que consome estas decisões: ancorar nela invalidaria todo o
    # trabalho humano a cada regeneração, e uma trava que pune quem revisa é uma
    # trava que as pessoas aprendem a contornar. O scan é a entrada imutável, e é
    # dele que as coordenadas vêm.
    scan_sha = file_sha256(SCAN_MANIFEST)
    manifest_sha = file_sha256(BOXES_MANIFEST)

    candidatos = [
        {
            "row": row,
            "index": indice,
            "box": box,
            "strata": strata_of(row, box, floor, quase),
        }
        for row in rows
        for indice, box in enumerate(row["boxes"])
    ]

    por_estrato: dict[str, list[dict]] = defaultdict(list)
    for item in candidatos:
        for estrato in item["strata"]:
            por_estrato[estrato].append(item)

    escolhidos: dict[str, dict] = {}
    cobertura: dict[str, int] = {}
    for estrato in STRATA:
        disponiveis = sorted(
            por_estrato.get(estrato, []),
            key=lambda i: (
                -(i["box"]["width"] * i["box"]["height"]),
                i["row"]["stem"],
                i["index"],
            ),
        )
        cobertura[estrato] = len(disponiveis)
        # Extremos e meio do estrato: uma amostra só das maiores descreveria
        # uma ponta da distribuição e chamaria isso de auditoria.
        selecao = disponiveis[: max(1, per_stratum // 2)]
        selecao += disponiveis[-(per_stratum - len(selecao)) :] if disponiveis else []
        for item in selecao[:per_stratum]:
            escolhidos.setdefault(f"{item['row']['stem']}#{item['index']}", item)

    linhas = []
    for chave, item in sorted(escolhidos.items()):
        row, box = item["row"], item["box"]
        linhas.append(
            {
                "audit_version": AUDIT_VERSION,
                "box_id": chave,
                # Proveniência da decisão: sem estes campos ela não é aplicável.
                "source_scan": SCAN_MANIFEST.relative_to(PROJECT_ROOT).as_posix(),
                "source_scan_sha256": scan_sha,
                # Informativo: diz de qual derivada a amostra foi tirada. Não é
                # o vínculo, porque esta é regravada a cada conversão.
                "source_manifest": BOXES_MANIFEST.relative_to(PROJECT_ROOT).as_posix(),
                "source_manifest_sha256": manifest_sha,
                "image_id": row["stem"],
                "image_relpath": row["image_relpath"],
                "label_relpath": row["label_relpath"],
                "image_width": row["image_width"],
                "image_height": row["image_height"],
                "group": row["group"],
                "box_index": item["index"],
                "boxes_in_image": len(row["boxes"]),
                "bbox_xyxy": [box["xmin"], box["ymin"], box["xmax"], box["ymax"]],
                "box_area_px": box["width"] * box["height"],
                "area_fraction": box["area_fraction"],
                "semantic_status": box["semantic_status"],
                "training_allowed": box["training_allowed"],
                "blocking_gates": box["blocking_gates"],
                "quarantine_kind": box.get("quarantine_kind"),
                "quarantine_reasons": box.get("quarantine_reasons", []),
                "source_label": box["source_label"],
                "proposed_urmind_class": box["urmind_class"],
                "strata": item["strata"],
                "visual_evidence": (
                    "datasets/processed/urban_community/visual_audit/box_audit/"
                    f"{chave.replace('#', '_box')}.jpg"
                ),
                "visual_evidence_howto": (
                    "gerar com: python -B scripts/datasets/"
                    "render_urban_community_audit.py --audit-panels"
                ),
                # A decidir por humano. Vazio = não revisado. Nada aqui é
                # preenchido por máquina, em nenhuma circunstância.
                "decision": None,
                # Nível de IMAGEM: COMPLETE só quando o revisor confirmar que
                # não há buraco sem caixa. UNKNOWN reprova o portão — "ninguém
                # verificou" não é "está completo".
                "image_annotation_completeness": "UNKNOWN",
                "note": None,
                "reviewed_by": None,
                "reviewed_at": None,
            }
        )
    return linhas, cobertura


def audit_status(linhas: list[dict], total_aceitas: int) -> dict:
    decisoes = Counter(linha["decision"] for linha in linhas)
    pendentes = decisoes.get(None, 0)
    invalidas = sorted(
        {
            str(linha["decision"])
            for linha in linhas
            if linha["decision"] is not None and linha["decision"] not in DECISIONS
        }
    )
    aprovadas = sum(decisoes.get(d, 0) for d in CONCLUSIVE_APPROVALS)
    reprovadas = sum(decisoes.get(d, 0) for d in CONCLUSIVE_REJECTIONS)
    inconclusivas = sum(decisoes.get(d, 0) for d in INCONCLUSIVE)

    folha_completa = bool(linhas) and pendentes == 0 and not invalidas
    # A folha é uma AMOSTRA. Fechá-la não valida as caixas que ficaram de fora:
    # a validação caixa a caixa do conjunto inteiro só é completa quando toda
    # caixa aceita tiver decisão humana própria.
    cobertura_total = folha_completa and len(linhas) >= total_aceitas
    completa = cobertura_total and inconclusivas == 0

    if completa:
        motivo = None
    elif not linhas:
        motivo = "a folha está vazia: nenhuma caixa foi proposta para revisão."
    elif pendentes:
        motivo = f"{pendentes} de {len(linhas)} linha(s) da amostra sem decisão humana."
    elif invalidas:
        motivo = f"decisões fora do vocabulário fechado: {invalidas}."
    elif not cobertura_total:
        motivo = (
            f"a amostra revisada cobre {len(linhas)} de {total_aceitas} caixas "
            "aceitas. Amostra fechada mede a taxa de erro da fonte; não certifica "
            "as caixas que ninguém olhou."
        )
    else:
        motivo = (
            f"{inconclusivas} decisão(ões) `ambiguous`/`other`: a revisão aconteceu "
            "e não concluiu."
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
        "sample_complete": folha_completa,
        "boxes_with_individual_human_decision": aprovadas + reprovadas + inconclusivas,
        "boxes_without_individual_human_decision": total_aceitas
        - (aprovadas + reprovadas + inconclusivas),
        "approved_boxes": aprovadas,
        "rejected_boxes": reprovadas,
        "inconclusive_boxes": inconclusivas,
        "human_review_status": (HUMAN_REVIEW_PENDING if pendentes else "REVIEWED"),
        "acceptance_protocol": ACCEPTANCE_PROTOCOL,
        # As duas afirmações que este projeto não pode confundir.
        "semantic_mapping_approved": True,
        "box_level_human_validation_complete": completa,
        "why_not_complete": motivo,
        "meaning": (
            "`semantic_mapping_approved` é sobre a CLASSE: `pothole` corresponde à "
            "definição de URMIND_ROAD_D40. `box_level_human_validation_complete` é "
            "sobre CADA CAIXA. A primeira não implica a segunda, e enquanto a "
            "segunda for falsa as caixas são candidatas a reforço de treino."
        ),
    }


def _dup_rows() -> list[dict]:
    if not DUP_SHEET.is_file():
        return []
    return [
        json.loads(line)
        for line in require_local(DUP_SHEET).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-stratum", type=int, default=8)
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="não regrava a folha; só relata o andamento da revisão humana.",
    )
    args = parser.parse_args()

    rows = load_rows()
    total_aceitas = sum(
        1 for row in rows for box in row["boxes"] if not box["quarantine_reasons"]
    )

    if SHEET.is_file():
        linhas = [
            json.loads(line)
            for line in require_local(SHEET).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        cobertura: dict[str, int] = {}
        if not args.status_only:
            print(
                f"Folha já existe com {len(linhas)} linha(s); preservada para não "
                "apagar revisão humana. Use --status-only para só conferir."
            )
    else:
        if args.status_only:
            raise SystemExit(f"{SHEET.relative_to(PROJECT_ROOT)} ainda não existe")
        linhas, cobertura = build_sheet(rows, args.per_stratum)
        write_text_safe(
            SHEET,
            "\n".join(json.dumps(linha, ensure_ascii=False) for linha in linhas) + "\n",
        )
        print(f"Folha criada: {SHEET.relative_to(PROJECT_ROOT)} ({len(linhas)} caixas)")

    if not DUP_SHEET.is_file():
        pares = build_duplicate_sheet(rows)
        if pares:
            write_text_safe(
                DUP_SHEET,
                "\n".join(json.dumps(par, ensure_ascii=False) for par in pares) + "\n",
            )
            print(
                f"Folha de duplicatas: {DUP_SHEET.relative_to(PROJECT_ROOT)} "
                f"({len(pares)} par(es) dHash 0)"
            )

    estado = audit_status(linhas, total_aceitas)
    report = {
        "version": 1,
        "scope": (
            "estado da auditoria humana caixa a caixa do reforço do Urban "
            "Community. Este artefato NÃO contém decisão gerada por máquina."
        ),
        **provenance(
            __file__,
            source_dataset="urban_community",
            source_version="kaggle-2025",
            transform="preparação de folha de auditoria humana estratificada por caixa",
            params={
                "per_stratum": args.per_stratum,
                "strata": list(STRATA),
                "selection": "determinística por ordenação de área; sem sorteio",
                "audit_version": AUDIT_VERSION,
            },
        ),
        "inputs": total_aceitas,
        "outputs": len(linhas),
        "dropped": 0,
        "drop_reasons": {},
        "duplicate_review": {
            "sheet": DUP_SHEET.relative_to(PROJECT_ROOT).as_posix(),
            "pairs": len(_dup_rows()),
            "pending": sum(1 for x in _dup_rows() if not x.get("decision")),
            "status": DUPLICATE_PENDING,
            "allowed_decisions": list(DUPLICATE_DECISIONS),
            "rule": (
                "decisão de duplicata é por PAR e não se deduz de aprovação de "
                "caixa: aprovar um buraco não responde se duas imagens são a "
                "mesma cena"
            ),
        },
        "integrity": {
            "boxes_manifest_sha256": file_sha256(BOXES_MANIFEST),
            "duplicate_sheet_sha256": file_sha256(DUP_SHEET)
            if DUP_SHEET.is_file()
            else None,
            "conversion_report_sha256": (
                file_sha256(CONVERSION_REPORT) if CONVERSION_REPORT.is_file() else None
            ),
            "sheet_sha256": file_sha256(SHEET) if SHEET.is_file() else None,
        },
        "raw_modified": False,
        "status": estado,
        "stratum_population": cobertura,
        "sample_vs_population": (
            "A folha é uma AMOSTRA estratificada. Fechá-la ESTIMA a taxa de erro "
            "da fonte — aprovadas, wrong_class, annotation_incomplete, bad_box, "
            "ambiguous — e serve para decidir entre (A) ampliar a revisão, (B) "
            "revisar todas as caixas ou (C) manter a fonte bloqueada. Ela NÃO "
            "promove as caixas que ninguém olhou: `box_level_validation` é por "
            "caixa, e cada caixa autorizada precisa da própria decisão."
        ),
        "how_to_fill": (
            "Para cada linha, abra a evidência visual, decida entre "
            f"{list(DECISIONS)} e preencha `decision`, `note`, `reviewed_by` e "
            "`reviewed_at`. `annotation_incomplete` é sobre a IMAGEM: use quando "
            "houver buraco visível sem caixa, mesmo que a caixa desta linha esteja "
            "correta — é o defeito que ensina falso negativo. Decisão fora do "
            "vocabulário é contada como inválida e a auditoria não fecha."
        ),
    }

    if args.no_report:
        print(json.dumps(report["status"], indent=2, ensure_ascii=False))
        return 0

    destino = write_json_report(REPORT_NAME, report)
    print(f"Relatório: {destino.relative_to(PROJECT_ROOT)}")
    print(
        f"  revisadas {estado['reviewed']}/{estado['rows']} da amostra | "
        f"caixas aceitas sem decisão individual: "
        f"{estado['boxes_without_individual_human_decision']}/{total_aceitas}"
    )
    print(
        f"  semantic_mapping_approved=True | "
        f"box_level_human_validation_complete={estado['box_level_human_validation_complete']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
