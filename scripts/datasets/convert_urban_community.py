"""Versão DERIVADA do Urban Community: `pothole` → reforço de treino D40.

    .txt YOLO normalizado → caixa em pixels → anotação de reforço

Somente a pasta `pothole` entra. As outras seis são recusadas com o motivo, e a
recusa é confrontada com a evidência medida em `urban_community_audit.json` e
vista em `processed/urban_community/visual_audit/`.

**O mapeamento `pothole → URMIND_ROAD_D40` é aplicado aqui**, e a justificativa
é registrada no relatório. Ele não vem de semelhança visual entre classes: vem da
definição canônica. `datasets/metadata/taxonomy.yaml` define `URMIND_ROAD_D40`
como *buraco / pothole*, com origem em `RDD2022 D40`; a pasta de origem se chama
`pothole`; e a inspeção estratificada dos recortes confirmou que o conteúdo é
buraco de pavimento. É equivalência entre um rótulo e a definição oficial da
classe — não é escolher classe por aparência.

O que a inspeção também mostrou, e por isso nada aqui é aceito em bloco:

- a pasta tem contaminação — apareceram trinca sem cavidade e um bueiro aberto
  entre os recortes de `pothole`;
- há imagem com vários buracos visíveis e só parte deles anotada, o que ensina
  falso negativo;
- há duplicata exata cruzando pastas (`cracks/536.jpg` == `pothole/202.jpg`).

Por isso a derivada separa **aceitas** de **em quarentena**: a quarentena espera
revisão humana e não entra em treino. Uso máximo desta fonte é
TRAIN_REINFORCEMENT; ela nunca entra em teste (§8.4 — o único agrupamento
disponível é a pasta, que não separa cena).

    python -B scripts/datasets/convert_urban_community.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

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

# A decisão sobre o que pode treinar mora em UM lugar só. Este script e o
# adaptador operacional importam a mesma função; antes cada um tinha a sua, e
# elas discordavam em dez imagens e vinte e sete caixas.
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
from app.datasets.authorization import (
    DUPLICATE_NOT_IN_PAIR,
    DUPLICATE_REJECTED,
    DUPLICATE_REJECTION_REASON,
    DUPLICATE_RESOLVED,
    HUMAN_REVIEW_PENDING,
    HUMAN_REVIEW_STALE,
    REQUIRED_GATES,
    evaluate_gates,
    human_review_status_of,
    resolve_duplicate_reviews,
)

SCAN_MANIFEST = DATASETS_DIR / "manifests" / "urban_community_scan.jsonl"
BOXES_MANIFEST = DATASETS_DIR / "manifests" / "urban_community_boxes.jsonl"
AUDIT_REPORT = DATASETS_DIR / "reports" / "urban_community_audit.json"
REPORT_NAME = "urban_community_conversion.json"

SOURCE_FOLDER = "pothole"
SOURCE_LABEL = "pothole"
SOURCE_CLASS_ID = 3
USAGE = "CANDIDATE_TRAIN_REINFORCEMENT"
TRAINING_STATUS = "BLOCKED_PENDING_BOX_VALIDATION"
MANIFEST_VERSION = 2
AUDIT_SHEET = DATASETS_DIR / "annotations" / "urban_community_box_audit_v1.jsonl"
DUP_SHEET = DATASETS_DIR / "annotations" / "urban_community_duplicate_audit_v1.jsonl"

# O que já está resolvido no momento da conversão, e o que não está.
#
# O mapeamento `pothole → URMIND_ROAD_D40` é afirmação sobre a CLASSE e está
# sustentado por evidência registrada. Duplicata e contaminação cruzada, não:
# quem responde por eles é `validate_urban_community.py`, que roda DEPOIS desta
# etapa. Declará-los verdadeiros aqui seria autorizar com base em uma verificação
# que ainda não aconteceu — então saem falsos, e o adaptador os reavalia contra o
# relatório de validação da versão corrente.
CONVERSION_TIME_CHECKS = {
    "taxonomy_mapping_validated": True,
    "duplicate_check_passed": False,
    "cross_source_check_passed": False,
}

# Recusas, com a natureza de cada uma. `escopo` e `taxonomia` não se resolvem com
# mais trabalho técnico: dependem de a V1 ganhar uma classe, que é decisão de
# produto. `sem_anotacao` é outra coisa — ver o relatório.
REFUSED = {
    "cracks": (
        "taxonomia",
        (
            "classe única de trinca. A inspeção visual confirmou o problema em vez de "
            "só declará-lo: a mesma pasta traz trinca em malha, longitudinal e "
            "transversal. Escolher entre D00/D10/D20 seria inventar o subtipo (§8.2)."
        ),
    ),
    "open_manhole": (
        "taxonomia",
        (
            "URMIND_MANHOLE é classe futura bloqueada; exige dataset e protocolo de "
            "anotação próprios (§8.2). 152 imagens herdadas não substituem isso."
        ),
    ),
    "good_road": (
        "sem_anotacao",
        (
            "100 imagens com .txt vazio, RETIDAS como NEGATIVE_SEMANTICS_UNVERIFIED. "
            "A ausência de caixa não prova que a imagem seja negativa confiável: "
            "enquanto o protocolo de anotação da fonte não estiver comprovado, ela "
            "também é compatível com anotador que simplesmente não anotou — e "
            "treinar com isso ensina o detector que o objeto não estava ali. Não "
            "entram em treino agora."
        ),
    ),
    "animal": (
        "escopo",
        (
            "Animal em via não é dano de infraestrutura: é ocorrência transitória, e o "
            "UrMind detecta condição do pavimento e do mobiliário urbano. Não existe "
            "classe da V1 nem futura que corresponda, então não há o que desbloquear. "
            "1.113 caixas que o detector não deve aprender a procurar."
        ),
    ),
    "traffic_lights": (
        "taxonomia",
        "URMIND_SIGNAGE é classe futura bloqueada (§8.2).",
    ),
    "waste_container": (
        "escopo",
        (
            "Contêiner de lixo é mobiliário urbano em estado normal, não avaria. O "
            "escopo do §0 cita buraco, calçada, bueiro e sinalização; coleta de "
            "resíduo não está lá e não tem classe futura reservada. 1.112 caixas "
            "fora do domínio."
        ),
    ),
}


def load_human_decisions() -> tuple[dict[str, dict], dict[str, str]]:
    """Folha de auditoria, indexada por caixa — **sem** aplicar nada ainda.

    O script não escreve decisão nenhuma: ele lê o que uma pessoa preencheu.
    Folha ausente ou linha em branco significam revisão pendente, e pendente
    reprova o portão semântico.

    Devolve a LINHA inteira, não só a decisão, porque aplicar uma decisão exige
    conferir a que caixa ela pertence — ver `bind_decision`.
    """
    if not AUDIT_SHEET.is_file():
        return {}, {}
    linhas: dict[str, dict] = {}
    completude: dict[str, str] = {}
    for line in require_local(AUDIT_SHEET).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        linha = json.loads(line)
        linhas[linha["box_id"]] = linha

        imagem = linha["image_id"]
        declarada = linha.get("image_annotation_completeness") or "UNKNOWN"
        if linha.get("decision") == "annotation_incomplete":
            declarada = "INCOMPLETE"
        anterior = completude.get(imagem)
        if anterior == "INCOMPLETE" or declarada == "INCOMPLETE":
            completude[imagem] = "INCOMPLETE"
        elif anterior == "UNKNOWN" or declarada == "UNKNOWN":
            completude[imagem] = "UNKNOWN"
        else:
            completude[imagem] = declarada
    return linhas, completude


def bind_decision(
    folha: dict | None, row: dict, box: dict, *, scan_sha: str | None
) -> tuple[str | None, str]:
    """Decide se uma decisão humana pertence a ESTA caixa. Fail-closed.

    `stem#index` é identificador reutilizável: reordenar as caixas de uma imagem,
    ou substituir uma por outra, faz o mesmo id apontar para outra geometria. Uma
    aprovação antiga migraria para uma caixa que ninguém olhou, e o portão
    semântico — o único que exige pessoa — abriria sozinho.

    Por isso a decisão só vale quando o vínculo inteiro confere: a versão do
    SCAN auditado, a imagem, o rótulo de origem e as coordenadas. Qualquer
    divergência devolve `STALE_HUMAN_REVIEW`, que o portão trata como pendente.

    A âncora é o scan e não a derivada porque a derivada é reescrita por esta
    própria etapa: ancorar nela apagaria toda revisão humana a cada execução.

    Decisão sem proveniência registrada também não passa: folha antiga, gravada
    antes de o vínculo existir, não prova sobre o que era.
    """
    if folha is None:
        return None, HUMAN_REVIEW_PENDING
    if folha.get("decision") is None:
        return None, HUMAN_REVIEW_PENDING

    registrado = folha.get("source_scan_sha256")
    if not registrado:
        return None, HUMAN_REVIEW_STALE
    if scan_sha is not None and registrado != scan_sha:
        return None, HUMAN_REVIEW_STALE
    if folha.get("image_relpath") != row["image_relpath"]:
        return None, HUMAN_REVIEW_STALE
    if folha.get("source_label") != SOURCE_LABEL:
        return None, HUMAN_REVIEW_STALE

    esperada = [box["xmin"], box["ymin"], box["xmax"], box["ymax"]]
    if list(folha.get("bbox_xyxy") or []) != esperada:
        return None, HUMAN_REVIEW_STALE

    return folha["decision"], human_review_status_of(folha["decision"])


def load_duplicate_reviews() -> list[dict]:
    """Decisões humanas por par dHash 0. Só lê; nunca escreve decisão."""
    if not DUP_SHEET.is_file():
        return []
    return [
        json.loads(line)
        for line in require_local(DUP_SHEET).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_scan() -> list[dict]:
    return [
        json.loads(line)
        for line in require_local(SCAN_MANIFEST).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def allowed_class() -> str:
    """Classe permitida para `pothole`, lida da declaração versionada do projeto."""
    from _taxonomy import load_class_mapping

    aceitos = load_class_mapping("urban_community")["accepted"]
    classe = aceitos.get(SOURCE_LABEL.upper()) or aceitos.get(SOURCE_LABEL)
    if classe is None:
        raise SystemExit(
            "class_mapping.yaml não declara classe da V1 para urban_community.pothole"
        )
    return classe


# Quarentena heurística ≠ rejeição semântica, e o projeto precisa saber qual das
# duas está aplicando.
#
# `heuristic`  — a caixa é *estatisticamente atípica* na própria pasta. Isso não
#   diz que ela está errada. O piso de 236 px² (percentil 1) separa 224 px² de
#   240 px², uma diferença de 7% em área: um buraco distante e pequeno cai desse
#   lado sem ter nada de inválido. Reproduzível não é o mesmo que semanticamente
#   fundamentado, e tratar o percentil como regra da classe seria inventar um
#   limite de tamanho que a definição de URMIND_ROAD_D40 não tem.
# `semantic` — a caixa viola algo que a definição realmente exige (não existe
#   critério assim aqui hoje; contaminação de classe só sai por revisão humana).
QUARANTINE_HEURISTIC = "heuristic"
QUARANTINE_SEMANTIC = "semantic"


def quality_quarantine(box: dict, row: dict, tiny_floor: int) -> list[dict]:
    """Motivos para segurar a caixa fora do treino até alguém olhar.

    Nenhum critério aqui é opinião sobre o conteúdo: são fatos geométricos e o
    piso de área calculado da própria distribuição da pasta. Contaminação de
    classe — trinca ou bueiro rotulado como buraco — NÃO é detectável assim, e é
    por isso que existe a folha de revisão humana.

    Cada motivo sai com o seu `kind`, porque "esta caixa é atípica no conjunto" e
    "esta caixa não é um buraco" são afirmações diferentes, com força diferente.
    Hoje **todos** os critérios são heurísticos: nenhum deles observa o conteúdo.
    """
    motivos: list[dict] = []
    largura = box["xmax"] - box["xmin"]
    altura = box["ymax"] - box["ymin"]
    if largura < 2 or altura < 2:
        motivos.append(
            {
                "reason": "caixa com menos de 2 px de lado",
                "kind": QUARANTINE_HEURISTIC,
                "basis": "geometria; caixa desse tamanho não sustenta objeto algum",
            }
        )
    if box["area_fraction"] > 0.95:
        motivos.append(
            {
                "reason": "caixa cobre quase o quadro inteiro",
                "kind": QUARANTINE_HEURISTIC,
                "basis": "geometria; caixa do tamanho da imagem não localiza nada",
            }
        )
    if largura * altura < tiny_floor:
        motivos.append(
            {
                "reason": f"área abaixo do percentil 1 da pasta ({tiny_floor} px²)",
                "kind": QUARANTINE_HEURISTIC,
                "basis": (
                    "limiar estatístico da própria pasta, reproduzível e "
                    "SEM fundamento semântico: não há evidência de que caixa "
                    "abaixo dele seja inválida. Segura para análise, não rejeita."
                ),
            }
        )
    if (
        box["xmin"] < -1
        or box["ymin"] < -1
        or box["xmax"] > row["image_width"] + 1
        or box["ymax"] > row["image_height"] + 1
    ):
        motivos.append(
            {
                "reason": "caixa extrapola a imagem",
                "kind": QUARANTINE_HEURISTIC,
                "basis": "geometria; coordenada fora do quadro é erro de anotação",
            }
        )
    return motivos


def geometry_ok(box: dict, width: int, height: int) -> list[str]:
    """Invariantes que uma caixa precisa cumprir para existir. Falhar = descarte."""
    problemas = []
    if not box["xmin"] < box["xmax"]:
        problemas.append("x1 >= x2")
    if not box["ymin"] < box["ymax"]:
        problemas.append("y1 >= y2")
    if not 0 <= box["xmin"] < width:
        problemas.append("x1 fora de [0, largura)")
    if not 0 < box["xmax"] <= width:
        problemas.append("x2 fora de (0, largura]")
    if not 0 <= box["ymin"] < height:
        problemas.append("y1 fora de [0, altura)")
    if not 0 < box["ymax"] <= height:
        problemas.append("y2 fora de (0, altura]")
    return problemas


def convert(
    rows: list[dict],
    urmind_class: str,
    blocked: set[str],
    decisoes: dict[str, dict] | None = None,
    completudes: dict[str, str] | None = None,
    scan_sha: str | None = None,
    duplicate_reviews: list[dict] | None = None,
) -> tuple[list[dict], dict]:
    import numpy as np

    decisoes = decisoes or {}
    completudes = completudes or {}
    alvo = [r for r in rows if r["folder"] == SOURCE_FOLDER]
    # A decisão de duplicata vira estado da IMAGEM aqui, no manifesto que o
    # adaptador lê. Aplicá-la só no relatório de validação fechava o par no papel
    # e deixava a imagem rejeitada treinar mesmo assim.
    estados_duplicata = resolve_duplicate_reviews(
        duplicate_reviews or [],
        scan_sha=scan_sha,
        image_paths={r["stem"]: r["image_relpath"] for r in alvo},
    )
    duplicatas_rejeitadas = duplicatas_pendentes = 0
    areas_px = [
        (b["xmax"] - b["xmin"]) * (b["ymax"] - b["ymin"]) for r in alvo for b in r["boxes"]
    ]
    tiny_floor = int(np.percentile(np.array(areas_px), 1)) if areas_px else 0

    manifest: list[dict] = []
    drops: Counter = Counter()
    quarentena: Counter = Counter()
    tipos_quarentena: Counter = Counter()
    obsoletas = 0
    portoes: Counter = Counter()
    candidatas = 0
    autorizadas = 0
    em_quarentena = 0
    por_imagem: Counter = Counter()

    for row in alvo:
        # Vem da folha de auditoria quando alguém tiver respondido. Sem resposta,
        # "não verificado" não é "completo": fica UNKNOWN e o portão reprova.
        completude = completudes.get(row["stem"], "UNKNOWN")
        estado_duplicata = estados_duplicata.get(row["stem"], DUPLICATE_NOT_IN_PAIR)
        if row["stem"] in blocked:
            drops["imagem bloqueada por duplicata (entre pastas ou cópia interna)"] += 1
            continue

        caixas = []
        for box in row["boxes"]:
            if box["class_id"] != SOURCE_CLASS_ID:
                drops[f"class_id {box['class_id']} fora da pasta {SOURCE_FOLDER}"] += 1
                continue
            problemas = geometry_ok(box, row["image_width"], row["image_height"])
            if problemas:
                drops[f"caixa inválida: {'; '.join(problemas)}"] += 1
                continue
            motivos = quality_quarantine(box, row, tiny_floor)
            if motivos:
                for motivo in motivos:
                    quarentena[motivo["reason"]] += 1
                    tipos_quarentena[motivo["kind"]] += 1
                em_quarentena += 1
            else:
                candidatas += 1

            decisao, revisao = bind_decision(
                decisoes.get(f"{row['stem']}#{box['index']}"),
                row,
                box,
                scan_sha=scan_sha,
            )
            if revisao == HUMAN_REVIEW_STALE:
                obsoletas += 1

            registro = {
                "source_label": SOURCE_LABEL,
                "source_class_id": box["class_id"],
                # Fatos medidos desta caixa, separados do veredito. A caixa
                # passou pela geometria e veio da pasta examinada — nenhuma das
                # duas coisas diz o que há dentro dela.
                "geometry_valid": True,
                "source_category_validated": True,
                "quarantine_reasons": [m["reason"] for m in motivos],
                "quarantine_kind": (min(m["kind"] for m in motivos) if motivos else None),
                "quarantine_detail": motivos,
                # Preenchido por pessoa, na folha de auditoria. Nunca aqui — e
                # só aceito depois de `bind_decision` conferir que a decisão é
                # desta caixa, nesta versão do manifesto.
                "human_decision": decisao,
                "human_review_status": revisao,
                "xmin": box["xmin"],
                "ymin": box["ymin"],
                "xmax": box["xmax"],
                "ymax": box["ymax"],
                "width": box["xmax"] - box["xmin"],
                "height": box["ymax"] - box["ymin"],
                "area_fraction": box["area_fraction"],
                "component_index": box["index"],
            }
            # O veredito sai da regra compartilhada, e sai fechado: enquanto
            # houver portão aberto, `urmind_class` é null e `training_allowed`
            # é falso. Uma caixa geometricamente perfeita continua sem classe.
            veredito = evaluate_gates(
                registro,
                image={
                    "annotation_completeness": completude,
                    "duplicate_review_status": estado_duplicata,
                },
                source_checks=CONVERSION_TIME_CHECKS,
                urmind_class=urmind_class,
            )
            for portao in veredito.blocking_gates:
                portoes[portao] += 1
            if veredito.training_allowed:
                autorizadas += 1
            caixas.append({**registro, **veredito.as_dict()})

        if not caixas:
            continue
        if estado_duplicata == DUPLICATE_REJECTED:
            duplicatas_rejeitadas += 1
        elif estado_duplicata not in DUPLICATE_RESOLVED:
            duplicatas_pendentes += 1
        por_imagem[sum(1 for c in caixas if not c["quarantine_reasons"])] += 1
        manifest.append(
            {
                "dataset_id": "urban_community",
                "derived": True,
                "manifest_version": MANIFEST_VERSION,
                "usage": USAGE,
                "training_status": TRAINING_STATUS,
                "evaluation_use": "FORBIDDEN",
                "annotation_completeness": completude,
                "human_review_status": HUMAN_REVIEW_PENDING,
                # A imagem continua no manifesto mesmo rejeitada: o histórico
                # 478 → ... → autorizadas precisa mostrar onde cada uma parou.
                "duplicate_review_status": estado_duplicata,
                "duplicate_rejected": estado_duplicata == DUPLICATE_REJECTED,
                "duplicate_rejection_reason": (
                    DUPLICATE_REJECTION_REASON
                    if estado_duplicata == DUPLICATE_REJECTED
                    else None
                ),
                "derived_from": SCAN_MANIFEST.relative_to(PROJECT_ROOT).as_posix(),
                "folder": row["folder"],
                "stem": row["stem"],
                "image_relpath": row["image_relpath"],
                "label_relpath": row["label_relpath"],
                "image_width": row["image_width"],
                "image_height": row["image_height"],
                # O único agrupamento que a fonte oferece. É fraco e está
                # registrado como fraco: não separa cena, então não sustenta
                # conjunto de avaliação (§8.4).
                "group": f"folder:{row['folder']}",
                "group_basis": "pasta de classe; a fonte não publica sessão, rota nem local",
                "boxes": caixas,
            }
        )

    return manifest, {
        "images": len(manifest),
        "semantic_candidate_boxes": candidatas,
        "training_authorized_boxes": autorizadas,
        "quarantined_boxes": em_quarentena,
        "quarantine_reasons": dict(quarentena),
        "quarantine_kinds": dict(tipos_quarentena),
        "blocking_gates": dict(sorted(portoes.items())),
        "stale_human_reviews": obsoletas,
        "duplicate_rejected_images": duplicatas_rejeitadas,
        "pending_duplicate_review_images": duplicatas_pendentes,
        "drops": dict(drops),
        "accepted_per_image": dict(sorted(por_imagem.items())),
        "tiny_floor_px": tiny_floor,
        "source_boxes": len(areas_px),
    }


def blocked_stems() -> tuple[set[str], list[dict], list[dict]]:
    """Imagens de `pothole` que saem do reforço por serem cópias.

    Dois casos distintos e ambos registrados:

    - a mesma imagem aparece em outra pasta, com outro rótulo. É contradição:
      sai das duas pontas;
    - a mesma imagem aparece repetida dentro da própria `pothole`. Não é
      contradição, é redundância: fica uma cópia e as demais saem, porque
      repetir a cena só multiplica o peso dela no treino.
    """
    import hashlib

    base = DATASETS_DIR / "raw" / "urban_community" / "Data_sets" / "Data_sets"
    if (base / "Data_sets").is_dir():
        base = base / "Data_sets"
    por_hash: dict[str, list[tuple[str, str]]] = {}
    for pasta in sorted(p for p in base.iterdir() if p.is_dir()):
        imagens = pasta / "images"
        if not imagens.is_dir():
            continue
        for arquivo in sorted(imagens.iterdir()):
            if not arquivo.is_file():
                continue
            digest = hashlib.sha256(require_local(arquivo).read_bytes()).hexdigest()
            por_hash.setdefault(digest, []).append((pasta.name, arquivo.stem))

    bloqueados: set[str] = set()
    conflitos = []
    redundantes = []
    for digest, membros in sorted(por_hash.items()):
        pastas = {p for p, _ in membros}
        if len(pastas) > 1:
            # Mesma imagem com dois rótulos: contradição, sai do reforço.
            conflitos.append({"sha256": digest, "members": membros})
            bloqueados.update(stem for pasta, stem in membros if pasta == SOURCE_FOLDER)
            continue
        dentro = sorted(stem for pasta, stem in membros if pasta == SOURCE_FOLDER)
        if len(dentro) > 1:
            # Cópias byte a byte da MESMA cena dentro da pasta. Não acrescentam
            # diversidade nenhuma; repetidas, só multiplicam o peso daquela cena
            # no treino. Fica uma — a primeira em ordem, para a escolha ser
            # reproduzível — e as outras saem.
            redundantes.append({"sha256": digest, "kept": dentro[0], "dropped": dentro[1:]})
            bloqueados.update(dentro[1:])
    return bloqueados, conflitos, redundantes


def main() -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()

    rows = load_scan()
    urmind_class = allowed_class()
    decisoes, completudes = load_human_decisions()
    # Hash do scan que a folha diz ter auditado. É a entrada imutável desta
    # etapa: a derivada não serve de âncora porque é ela que está sendo escrita.
    scan_sha = file_sha256(SCAN_MANIFEST)
    bloqueados, conflitos, redundantes = blocked_stems()
    manifest, resumo = convert(
        rows,
        urmind_class,
        bloqueados,
        decisoes,
        completudes,
        scan_sha,
        duplicate_reviews=load_duplicate_reviews(),
    )

    por_pasta = Counter(r["folder"] for r in rows)
    report = {
        "version": 1,
        "artifact_kind": "derived_dataset_version",
        "usage": USAGE,
        "scope": (
            "conversão YOLO→caixa da pasta `pothole` como reforço de treino. Não "
            "treina, não altera raw e nunca entra em avaliação."
        ),
        **provenance(
            __file__,
            source_dataset="urban_community",
            source_version="kaggle-2025",
            transform="YOLO normalizado → caixa em pixels, somente a pasta pothole",
            params={
                "source_folder": SOURCE_FOLDER,
                "source_class_id": SOURCE_CLASS_ID,
                "urmind_class": urmind_class,
                "tiny_floor_percentile": 1,
                "tiny_floor_px": resumo["tiny_floor_px"],
                "cross_folder_duplicates_blocked": sorted(bloqueados),
                "scan_manifest_sha256": file_sha256(SCAN_MANIFEST),
            },
        ),
        "inputs": por_pasta.get(SOURCE_FOLDER, 0),
        "outputs": resumo["images"],
        "dropped": sum(resumo["drops"].values()),
        "drop_reasons": resumo["drops"],
        "integrity": {
            "scan_manifest_sha256": file_sha256(SCAN_MANIFEST),
            "audit_report_sha256": file_sha256(AUDIT_REPORT) if AUDIT_REPORT.is_file() else None,
            "boxes_manifest": BOXES_MANIFEST.relative_to(PROJECT_ROOT).as_posix(),
        },
        "raw_modified": False,
        "counts": {
            # Nenhum número some do histórico: 478 candidatas originais, 22
            # caixas nas 10 imagens duplicadas, 456 restantes, 5 em quarentena
            # heurística, 451 candidatas semânticas, 0 autorizadas a treinar.
            "source_images_all_folders": sum(por_pasta.values()),
            "source_images_pothole": por_pasta.get(SOURCE_FOLDER, 0),
            "source_boxes_pothole": resumo["source_boxes"],
            "boxes_on_blocked_duplicate_images": (
                resumo["source_boxes"]
                - resumo["semantic_candidate_boxes"]
                - resumo["quarantined_boxes"]
            ),
            "manifest_images": resumo["images"],
            "geometrically_valid_boxes": (
                resumo["semantic_candidate_boxes"] + resumo["quarantined_boxes"]
            ),
            "quarantined_boxes": resumo["quarantined_boxes"],
            "semantic_candidate_boxes": resumo["semantic_candidate_boxes"],
            "human_reviewed_boxes": sum(
                1 for linha in decisoes.values() if linha.get("decision")
            ),
            "human_approved_boxes": sum(
                1
                for linha in decisoes.values()
                if linha.get("decision") == "approved_pothole"
            ),
            "stale_human_reviews": resumo["stale_human_reviews"],
            "duplicate_rejected_images": resumo["duplicate_rejected_images"],
            "pending_duplicate_review_images": resumo["pending_duplicate_review_images"],
            "d40_authorized_boxes": resumo["training_authorized_boxes"],
            "training_authorized_boxes": resumo["training_authorized_boxes"],
            "semantic_candidates_per_image": resumo["accepted_per_image"],
        },
        "gates": {
            "required": list(REQUIRED_GATES),
            "blocking_counts": resumo["blocking_gates"],
            "rule": "backend/app/datasets/authorization.py#evaluate_gates",
            "policy": (
                "conjunção: uma caixa só recebe URMIND_ROAD_D40 quando TODOS os "
                "portões passam. Portão sem resposta reprova — ausência de "
                "evidência não é evidência de ausência de problema."
            ),
            "conversion_time_checks": CONVERSION_TIME_CHECKS,
            "human_decision_binding": (
                "decisão humana só é aplicada quando o sha256 do scan "
                "auditado, a imagem, o rótulo de origem e as coordenadas da caixa "
                "conferem. `stem#index` é identificador reutilizável e sozinho não "
                "prova nada: divergência vira STALE_HUMAN_REVIEW e o portão "
                "semântico continua fechado."
            ),
            "recomputed_by_consumer": (
                "duplicata e contaminação cruzada só existem depois da "
                "validação, que roda após esta etapa. O adaptador reavalia os "
                "portões contra o relatório de validação da versão corrente, "
                "com a mesma função."
            ),
        },
        "mapping": {
            "source_label": SOURCE_LABEL,
            "urmind_class": urmind_class,
            # Duas afirmações, deliberadamente separadas. A primeira é sobre a
            # CLASSE: `pothole` corresponde à definição canônica de
            # URMIND_ROAD_D40. A segunda seria sobre cada CAIXA, e é falsa: a
            # própria fonte apresentou trinca sem cavidade, bueiro aberto e
            # anotação incompleta. Uma classe compatível não torna correta cada
            # anotação feita sob aquele nome.
            "semantic_mapping_approved": True,
            "box_level_human_validation_complete": False,
            "approved": True,
            "approved_scope": (
                "o mapeamento do rótulo para a classe — não a corretude "
                "individual das caixas"
            ),
            "basis": "definição canônica, não semelhança visual entre classes",
            "justification": [
                (
                    "taxonomy.yaml define URMIND_ROAD_D40 como 'buraco / pothole', "
                    "origem RDD2022 D40. O rótulo de origem é literalmente `pothole`: "
                    "a correspondência é entre um nome e a definição oficial da "
                    "classe, não entre duas aparências."
                ),
                (
                    "A inspeção estratificada de 48 recortes confirmou que o "
                    "conteúdo predominante da pasta é cavidade de pavimento — "
                    "buraco —, e não outra categoria de dano."
                ),
                (
                    "class_mapping.yaml já declarava esta correspondência; aqui ela "
                    "deixa de ser herdada e passa a ter evidência anexada."
                ),
            ],
            "limits": [
                (
                    "A inspeção foi assistida por modelo, não é revisão humana "
                    "registrada. Ela sustenta a categoria; não certifica caixa a caixa."
                ),
                (
                    "Foi observada contaminação na pasta: apareceram trinca sem "
                    "cavidade e um bueiro aberto entre os recortes rotulados como "
                    "buraco. Geometria não detecta isso — só revisão humana."
                ),
                (
                    "Foi observada anotação incompleta: imagem com vários buracos "
                    "visíveis e só parte deles com caixa, o que ensina falso negativo."
                ),
                (
                    "O mapa id→classe continua inferido das pastas; o pacote não "
                    "traz data.yaml e o Kaggle não publica checksum."
                ),
            ],
            "refused_labels": {
                nome: {"kind": tipo, "reason": motivo} for nome, (tipo, motivo) in REFUSED.items()
            },
            "good_road": {
                "status": "NEGATIVE_SEMANTICS_UNVERIFIED",
                "images": 100,
                "decision": "retidas; não incorporadas ao treino nesta etapa",
                "enforced_by": (
                    "app.datasets.adapters.read_urban_community marca o registro, e "
                    "app.datasets.readiness as conta em `unverified_negative_images`, "
                    "fora de `negative_images`"
                ),
                "what_would_unlock": (
                    "protocolo de anotação da fonte comprovado, ou exame humano "
                    "registrado das 100 imagens declarando que não há dano visível"
                ),
            },
        },
        "human_validation": {
            "human_review_status": HUMAN_REVIEW_PENDING,
            "acceptance_protocol": (
                "datasets/reports/urban_community_human_audit_status.json"
                "#status.acceptance_protocol — declarado ANTES da revisão, para "
                "que 'aprovada' não seja definida depois de olhar o resultado"
            ),
            "box_level_human_validation_complete": False,
            "boxes_with_individual_human_decision": 0,
            "boxes_without_individual_human_decision": resumo["semantic_candidate_boxes"],
            "sheet": "datasets/annotations/urban_community_box_audit_v1.jsonl",
            "status_report": "datasets/reports/urban_community_human_audit_status.json",
            "why": (
                "A inspeção que sustentou o mapeamento foi assistida por modelo e "
                "estratificada por área: ela responde pela categoria da pasta, não "
                "por cada caixa. Nenhuma das caixas aceitas foi conferida "
                "individualmente por humano, e a fonte já exibiu trinca sem "
                "cavidade, bueiro aberto e imagem com buracos não anotados. Até a "
                "folha de auditoria ser preenchida, estas caixas são candidatas a "
                "TRAIN_REINFORCEMENT — não ground truth humano certificado."
            ),
        },
        "quarantine": {
            "policy": (
                "Caixa em quarentena NÃO entra em treino e sai com urmind_class "
                "null. Todos os critérios de hoje são HEURÍSTICOS: são fatos "
                "geométricos e um piso de área tirado da distribuição da própria "
                "pasta. Nenhum deles observa o conteúdo da caixa."
            ),
            "heuristic_vs_semantic": {
                "heuristic_quarantine": (
                    "a caixa é atípica no conjunto. Fica retida para análise e não "
                    "entra em treino, mas NÃO foi declarada inválida."
                ),
                "semantic_rejection": (
                    "a caixa viola a definição da classe. Exige olhar o conteúdo; "
                    "nenhum critério automático deste script produz esse veredito."
                ),
                "threshold_caveat": (
                    "O piso de 236 px² é o percentil 1 da pasta: reproduzível, e "
                    "só isso. Ele separa 224 px² de 240 px² — 7% de área — e não há "
                    "evidência de que caixa abaixo dele seja inválida; um buraco "
                    "distante é pequeno sem ser errado. Por isso o percentil NÃO é "
                    "regra definitiva da classe URMIND_ROAD_D40: é gatilho de "
                    "revisão. Promovê-lo a critério de validade inventaria um "
                    "limite de tamanho que a definição da classe não tem."
                ),
                "semantic_rejections_applied": 0,
            },
            "boxes": resumo["quarantined_boxes"],
            "reasons": resumo["quarantine_reasons"],
            "kinds": resumo["quarantine_kinds"],
            "not_detectable_here": (
                "contaminação de classe (trinca ou bueiro rotulado como buraco) e "
                "anotação faltante; ambas exigem revisão humana"
            ),
        },
        "duplicate_removal": {
            "cross_folder_conflicts": conflitos,
            "cross_folder_rule": (
                "imagem idêntica em duas pastas recebe dois rótulos diferentes; é "
                "contradição e sai do reforço"
            ),
            "within_folder_redundant": redundantes,
            "within_folder_rule": (
                "cópia byte a byte da mesma cena dentro de `pothole`; fica a "
                "primeira em ordem alfabética e as demais saem. Determinístico: "
                "a mesma entrada produz sempre a mesma escolha."
            ),
            "blocked_pothole_stems": sorted(bloqueados),
            "diversity_note": (
                "Remover cópia idêntica não reduz diversidade: a imagem removida "
                "é a mesma que ficou, bit a bit."
            ),
        },
        "evaluation_policy": {
            "usage": USAGE,
            "training_status": TRAINING_STATUS,
            "training_use": (
                "BLOQUEADO: nenhuma caixa passou pelos portões. A fonte volta a "
                "ser reforço de treino quando houver caixa com decisão humana "
                "`approved_pothole` e os portões de conjunto satisfeitos — não "
                "antes, e não por amostragem."
            ),
            "test_use": "FORBIDDEN",
            "external_test_use": "FORBIDDEN",
            "reason": (
                "O único agrupamento disponível é a pasta de classe, que não separa "
                "cena. Um conjunto avaliado com esta fonte não sustentaria a "
                "garantia do §8.4, e a procedência das imagens não é declarada."
            ),
            "training_allowed_after": (
                "esta derivada é reforço declarado; nenhum treino foi executado"
            ),
        },
    }

    if args.no_report:
        print(json.dumps(report["counts"], indent=2, ensure_ascii=False))
        return 0

    write_text_safe(
        BOXES_MANIFEST, "\n".join(json.dumps(r, ensure_ascii=False) for r in manifest) + "\n"
    )
    report["integrity"]["boxes_manifest_sha256"] = file_sha256(BOXES_MANIFEST)
    destino = write_json_report(REPORT_NAME, report)

    print(f"Derivada: {BOXES_MANIFEST.relative_to(PROJECT_ROOT)}")
    print(f"Relatório: {destino.relative_to(PROJECT_ROOT)}")
    print(
        f"  {resumo['images']} imagens | "
        f"{resumo['semantic_candidate_boxes']} candidatas semânticas | "
        f"{resumo['quarantined_boxes']} em quarentena heurística | "
        f"{resumo['training_authorized_boxes']} autorizadas a treinar"
    )
    print(f"  duplicatas entre pastas bloqueadas: {sorted(bloqueados)}")
    print(f"  portões que bloqueiam: {resumo['blocking_gates']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
