"""Reforço de treino do Urban Community: o que entra, o que não entra e por quê.

Esta fonte é a única que recebe classe da V1 sem ser o RDD2022, e é também a de
procedência mais fraca do acervo: sem `data.yaml`, sem checksum oficial, sem
origem declarada das imagens. O que a protege de virar ruído é um conjunto de
regras estreitas — só `pothole`, só treino, sem duplicata, com quarentena — e é
isso que estes testes prendem.

Três dessas regras viviam só em prosa, e prosa não bloqueia ninguém: a proibição
de avaliar, a retenção de `good_road` como negativa não verificada, e a distinção
entre mapeamento semântico aprovado e validação humana caixa a caixa. Cada uma
tem abaixo o teste que a torna executável.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "datasets"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import convert_urban_community as conv
import prepare_urban_community_human_audit as audit
import validate_urban_community as val

from app.datasets import adapters
from app.datasets.adapters import AdapterError, read_urban_community
from app.datasets.authorization import (
    HUMAN_DECISIONS,
    REQUIRED_GATES,
    authorize_rows,
    evaluate_gates,
    human_review_status_of,
    resolve_duplicate_pair,
)
from app.datasets.catalog import (
    EVALUATION_USAGES,
    DatasetRole,
    DatasetSource,
    DatasetUsage,
    EvaluationForbidden,
    ExpectedFile,
    assert_evaluation_allowed,
    evaluation_forbidden_ids,
    get_source,
)
from app.datasets.records import NEGATIVE_SEMANTICS_UNVERIFIED, AnnotatedImage
from app.ml.splits import SplitRatios, split_by_group


def box(xmin, ymin, xmax, ymax, *, class_id=3, index=0, area=None):
    return {
        "index": index,
        "class_id": class_id,
        "cx": 0.0,
        "cy": 0.0,
        "w": 0.0,
        "h": 0.0,
        "xmin": xmin,
        "ymin": ymin,
        "xmax": xmax,
        "ymax": ymax,
        "area_fraction": area if area is not None else 0.05,
        "defects": [],
    }


def scan_row(stem="1", folder="pothole", *, boxes=(), width=1024, height=768):
    return {
        "folder": folder,
        "stem": stem,
        "image_relpath": f"datasets/raw/urban_community/Data_sets/Data_sets/{folder}/images/{stem}.jpg",
        "label_relpath": f"datasets/raw/urban_community/Data_sets/Data_sets/{folder}/labels/{stem}.txt",
        "image_width": width,
        "image_height": height,
        "boxes": list(boxes),
    }


D40 = "URMIND_ROAD_D40"


# ------------------------------------------------------ só `pothole` entra


def test_somente_a_pasta_pothole_e_convertida():
    rows = [
        scan_row("1", "pothole", boxes=[box(10, 10, 200, 200)]),
        scan_row("2", "cracks", boxes=[box(10, 10, 200, 200, class_id=4)]),
        scan_row("3", "open_manhole", boxes=[box(10, 10, 200, 200, class_id=5)]),
        scan_row("4", "traffic_lights", boxes=[box(10, 10, 200, 200, class_id=1)]),
    ]

    manifest, resumo = conv.convert(rows, D40, set())

    assert [r["stem"] for r in manifest] == ["1"]
    assert resumo["semantic_candidate_boxes"] == 1


@pytest.mark.parametrize(
    "pasta",
    ["cracks", "open_manhole", "good_road", "animal", "traffic_lights", "waste_container"],
)
def test_cada_recusa_tem_motivo_e_natureza_registrados(pasta):
    tipo, motivo = conv.REFUSED[pasta]

    assert tipo in {"taxonomia", "escopo", "sem_anotacao"}
    assert len(motivo) > 40, "recusa sem explicação é recusa que ninguém pode contestar"


def test_trinca_nao_vira_classe_de_pavimento():
    """A pasta mistura malha, longitudinal e transversal: não há subtipo a escolher."""
    tipo, motivo = conv.REFUSED["cracks"]

    assert tipo == "taxonomia"
    assert "D00/D10/D20" in motivo


def test_bueiro_nao_vira_classe_da_v1():
    tipo, motivo = conv.REFUSED["open_manhole"]

    assert tipo == "taxonomia"
    assert "URMIND_MANHOLE" in motivo


# ------------------------------------------------------------- mapeamento


def test_classe_vem_da_declaracao_versionada_e_nao_do_script():
    """Redigitar D40 aqui criaria uma segunda verdade divergindo em silêncio."""
    assert conv.allowed_class() == D40


def test_caixa_candidata_nao_recebe_a_classe_sem_revisao():
    """A conversão não promove mais nada: `urmind_class` sai null e fica null.

    Antes toda caixa geometricamente comum saía com D40 e `ACCEPTED`, e era isso
    que fazia 451 candidatas parecerem 451 rótulos.
    """
    manifest, _ = conv.convert([scan_row(boxes=[box(10, 10, 200, 200)])], D40, set())
    caixa = manifest[0]["boxes"][0]

    assert caixa["urmind_class"] is None
    assert not caixa["training_allowed"]
    assert caixa["semantic_status"] == "SEMANTIC_CANDIDATE"
    assert caixa["human_review_status"] == "PENDING"
    assert caixa["source_label"] == "pothole"


def test_class_id_fora_da_pasta_e_descartado():
    """Um id estranho na pasta significa que a inferência id→classe falhou ali."""
    rows = [scan_row(boxes=[box(10, 10, 200, 200, class_id=4)])]

    manifest, resumo = conv.convert(rows, D40, set())

    assert manifest == []
    assert any("class_id 4" in motivo for motivo in resumo["drops"])


# -------------------------------------------------------------- quarentena


def test_caixa_em_quarentena_nao_recebe_classe_da_v1():
    """Quarentena com classe atribuída entraria em treino sem ninguém notar."""
    rows = [scan_row(boxes=[box(10, 10, 200, 200), box(0, 0, 3, 3, index=1)])]

    manifest, resumo = conv.convert(rows, D40, set())
    quarentena = [b for b in manifest[0]["boxes"] if b["quarantine_reasons"]]

    assert quarentena, "a caixa minúscula precisa ser segurada"
    assert all(b["urmind_class"] is None for b in quarentena)
    assert all(b["quarantine_reasons"] for b in quarentena)
    assert resumo["quarantined_boxes"] == len(quarentena)


def test_caixa_cobrindo_o_quadro_inteiro_vai_para_quarentena():
    rows = [scan_row(boxes=[box(0, 0, 1024, 768, area=0.99)])]

    manifest, _ = conv.convert(rows, D40, set())

    caixa = manifest[0]["boxes"][0]

    assert caixa["quarantine_reasons"]
    assert caixa["semantic_status"] == "HEURISTIC_QUARANTINE"


def test_caixa_fora_dos_limites_e_descartada_e_nao_recortada():
    rows = [scan_row(boxes=[box(10, 10, 2000, 200)])]

    manifest, resumo = conv.convert(rows, D40, set())

    assert manifest == []
    assert any("caixa inválida" in motivo for motivo in resumo["drops"])


# -------------------------------------------------------------- duplicatas


def test_imagem_bloqueada_por_duplicata_sai_inteira():
    rows = [
        scan_row("202", boxes=[box(10, 10, 200, 200)]),
        scan_row("300", boxes=[box(10, 10, 200, 200)]),
    ]

    manifest, resumo = conv.convert(rows, D40, {"202"})

    assert [r["stem"] for r in manifest] == ["300"]
    assert any("duplicata" in motivo for motivo in resumo["drops"])


# ------------------------------------------------------- uso: treino apenas


def test_manifesto_declara_reforco_candidato_e_proibe_avaliacao():
    manifest, _ = conv.convert([scan_row(boxes=[box(10, 10, 200, 200)])], D40, set())
    entrada = manifest[0]

    assert entrada["usage"] == "CANDIDATE_TRAIN_REINFORCEMENT"
    assert entrada["training_status"] == "BLOCKED_PENDING_BOX_VALIDATION"
    assert entrada["evaluation_use"] == "FORBIDDEN"


def test_grupo_declara_a_propria_fraqueza():
    """Pasta de classe não separa cena; o manifesto diz isso em vez de fingir."""
    manifest, _ = conv.convert([scan_row(boxes=[box(10, 10, 200, 200)])], D40, set())

    assert manifest[0]["group"] == "folder:pothole"
    assert "não publica sessão" in manifest[0]["group_basis"]


# ------------------------------------------- a proibição de avaliar é executável


def test_urban_community_e_declarado_proibido_de_avaliar():
    fonte = get_source("urban_community")

    assert fonte.evaluation_forbidden
    assert fonte.evaluation_forbidden_reason
    assert not EVALUATION_USAGES & set(fonte.usage)
    assert "urban_community" in evaluation_forbidden_ids()


def test_o_portao_de_avaliacao_recusa_a_fonte():
    with pytest.raises(EvaluationForbidden, match="urban_community"):
        assert_evaluation_allowed("urban_community", "conjunto de teste")


def test_catalogo_que_se_contradiz_nao_carrega():
    """Proibir de avaliar e declarar TEST ao mesmo tempo é erro, não ressalva."""
    with pytest.raises(ValueError, match="evaluation_forbidden"):
        DatasetSource(
            id="contraditoria",
            title="t",
            homepage="https://example.invalid",
            license="CC0",
            role=DatasetRole.TRAINING_V1,
            version="v1",
            adapter=None,
            expected_files=(ExpectedFile("x.zip"),),
            usage=(DatasetUsage.TRAIN, DatasetUsage.TEST),
            evaluation_forbidden=True,
            evaluation_forbidden_reason="motivo",
        )


def _registro_urban(group: str) -> AnnotatedImage:
    return AnnotatedImage(
        dataset_id="urban_community",
        image_path=f"{group}/x.jpg",
        width=100,
        height=100,
        group=group,
    )


def test_split_com_lado_avaliado_recusa_a_fonte():
    """O ponto por onde um conjunto medido nasce é onde o bloqueio precisa estar."""
    registros = [_registro_urban(f"folder:{i}") for i in range(6)]

    with pytest.raises(EvaluationForbidden, match="urban_community"):
        split_by_group(registros, lambda r: r.group)


def test_split_so_de_treino_continua_permitido():
    """A fonte é reforço de treino: proibir avaliação não a proíbe de treinar."""
    registros = [_registro_urban(f"folder:{i}") for i in range(6)]

    resultado = split_by_group(
        registros, lambda r: r.group, SplitRatios(train=1.0, validation=0.0, test=0.0)
    )

    assert len(resultado.train) == 6
    assert not resultado.validation and not resultado.test


# ----------------------------------------------- good_road não é negativa ainda


def test_good_road_fica_retida_como_semantica_nao_verificada():
    """Ausência de caixa não prova que alguém olhou e não havia nada."""
    imagem = AnnotatedImage(
        dataset_id="urban_community",
        image_path="good_road/x.jpg",
        width=100,
        height=100,
        group="good_road",
        negative_status=NEGATIVE_SEMANTICS_UNVERIFIED,
    )

    assert not imagem.usable
    assert not imagem.trusted_negative
    assert imagem.as_dict()["negative_status"] == NEGATIVE_SEMANTICS_UNVERIFIED


def test_a_recusa_de_good_road_declara_a_semantica_pendente():
    tipo, motivo = conv.REFUSED["good_road"]

    assert tipo == "sem_anotacao"
    assert "NEGATIVE_SEMANTICS_UNVERIFIED" in motivo


# ------------------------------ quarentena heurística ≠ rejeição semântica


def test_o_piso_de_area_e_heuristico_e_diz_que_e():
    """O percentil é reproduzível; reproduzível não é semanticamente fundamentado."""
    motivos = conv.quality_quarantine(box(0, 0, 10, 10, area=0.001), scan_row(boxes=[]), 236)

    assert [m["kind"] for m in motivos] == [conv.QUARANTINE_HEURISTIC]
    assert "SEM fundamento semântico" in motivos[0]["basis"]


def test_nenhum_criterio_automatico_produz_rejeicao_semantica():
    """Contaminação de classe não sai de geometria — e o código não finge que sai."""
    row = scan_row(boxes=[], width=100, height=100)
    extremos = [
        box(0, 0, 1, 1, area=0.0001),
        box(0, 0, 100, 100, area=0.99),
        box(-5, -5, 120, 120, area=0.5),
    ]

    tipos = {m["kind"] for caixa in extremos for m in conv.quality_quarantine(caixa, row, 236)}

    assert tipos == {conv.QUARANTINE_HEURISTIC}
    assert conv.QUARANTINE_SEMANTIC not in tipos


# ------------------------- mapeamento aprovado ≠ validação humana caixa a caixa


def _decisao(decision=None):
    return {"decision": decision}


def test_folha_vazia_nao_valida_nenhuma_caixa():
    estado = audit.audit_status([], total_aceitas=451)

    assert not estado["box_level_human_validation_complete"]
    assert estado["semantic_mapping_approved"]
    assert estado["boxes_without_individual_human_decision"] == 451


def test_amostra_fechada_nao_certifica_o_conjunto_inteiro():
    """50 caixas revisadas medem a taxa de erro da fonte; não certificam 451."""
    estado = audit.audit_status(
        [_decisao("approved_pothole") for _ in range(50)], total_aceitas=451
    )

    assert estado["sample_complete"]
    assert not estado["box_level_human_validation_complete"]
    assert "não certifica" in estado["why_not_complete"]
    assert estado["boxes_without_individual_human_decision"] == 401


def test_auditoria_de_caixa_tem_estado_terminal_de_sucesso():
    """Sem isto, preencher a folha inteira nunca mudaria nada — e ninguém a preenche."""
    estado = audit.audit_status([_decisao("approved_pothole") for _ in range(3)], total_aceitas=3)

    assert estado["box_level_human_validation_complete"]
    assert estado["why_not_complete"] is None
    assert estado["approved_boxes"] == 3


def test_ambiguo_nao_conta_como_caixa_validada():
    """Revisar e não concluir é resultado honesto, e não fecha a auditoria."""
    linhas = [_decisao("approved_pothole"), _decisao("ambiguous"), _decisao("wrong_class")]

    estado = audit.audit_status(linhas, total_aceitas=3)

    assert not estado["box_level_human_validation_complete"]
    assert estado["inconclusive_boxes"] == 1
    assert estado["rejected_boxes"] == 1


def test_decisao_fora_do_vocabulario_invalida_a_auditoria():
    estado = audit.audit_status([_decisao("parece_ok")], total_aceitas=1)

    assert estado["invalid_decisions"] == ["parece_ok"]
    assert not estado["box_level_human_validation_complete"]


@pytest.mark.parametrize(
    "decisao",
    [
        "approved_pothole",
        "wrong_class",
        "annotation_incomplete",
        "ambiguous",
        "bad_box",
        "duplicate",
        "other",
    ],
)
def test_vocabulario_fechado_cobre_o_que_a_fonte_ja_mostrou(decisao):
    """A fonte exibiu trinca, bueiro e anotação faltando: o vocabulário precisa nomeá-los."""
    assert decisao in audit.DECISIONS


# ------------------- portões: o que separa candidata de autorizada


TODOS_OS_CHECKS = {
    "taxonomy_mapping_validated": True,
    "duplicate_check_passed": True,
    "cross_source_check_passed": True,
}


def _caixa(**extra):
    """Uma caixa que passou por tudo que a MÁQUINA consegue verificar."""
    return {
        "geometry_valid": True,
        "source_category_validated": True,
        "quarantine_reasons": [],
        "human_decision": None,
        **extra,
    }


def _imagem(completude="COMPLETE", duplicata="NOT_IN_DUPLICATE_PAIR"):
    return {"annotation_completeness": completude, "duplicate_review_status": duplicata}


def _veredito(**extra):
    return evaluate_gates(
        _caixa(**extra),
        image=_imagem(),
        source_checks=TODOS_OS_CHECKS,
        urmind_class=D40,
    )


def test_id_bruto_nao_promove_a_d40():
    """`class_id == 3` não é evidência de nada além de como a pasta foi nomeada."""
    veredito = _veredito()

    assert veredito.urmind_class is None
    assert not veredito.training_allowed
    assert "box_semantic_validation_passed" in veredito.blocking_gates


def test_geometricamente_valida_nao_e_semanticamente_aprovada():
    """A distinção que estava colapsada em ACCEPTED e produziu 451 'aceitas'."""
    veredito = _veredito()

    assert veredito.gates["geometry_valid"]
    assert not veredito.gates["box_semantic_validation_passed"]
    assert not veredito.training_allowed


def test_revisao_pendente_bloqueia_treino():
    assert human_review_status_of(None) == "PENDING"
    assert not _veredito(human_decision=None).training_allowed


@pytest.mark.parametrize(
    "decisao",
    ["wrong_class", "annotation_incomplete", "bad_box", "ambiguous", "duplicate", "other"],
)
def test_toda_decisao_que_nao_aprova_bloqueia_treino(decisao):
    """Só `approved_pothole` aprova. Nenhuma outra palavra do vocabulário passa."""
    veredito = _veredito(human_decision=decisao)

    assert not veredito.training_allowed
    assert veredito.urmind_class is None
    assert "box_semantic_validation_passed" in veredito.blocking_gates


def test_decisao_fora_do_vocabulario_nao_aprova():
    veredito = _veredito(human_decision="parece_ok")

    assert veredito.human_review_status == "INVALID"
    assert not veredito.training_allowed


def test_imagem_com_anotacao_incompleta_bloqueia_caixa_correta():
    """Uma caixa certa numa imagem com buraco não anotado ensina falso negativo."""
    veredito = evaluate_gates(
        _caixa(human_decision="approved_pothole"),
        image=_imagem("INCOMPLETE"),
        source_checks=TODOS_OS_CHECKS,
        urmind_class=D40,
    )

    assert not veredito.training_allowed
    assert "image_annotation_complete" in veredito.blocking_gates


def test_completude_desconhecida_tambem_bloqueia():
    """Fail-closed: "ninguém verificou" não é "está completo"."""
    veredito = evaluate_gates(
        _caixa(human_decision="approved_pothole"),
        image=_imagem("UNKNOWN"),
        source_checks=TODOS_OS_CHECKS,
        urmind_class=D40,
    )

    assert not veredito.training_allowed


def test_quarentena_heuristica_nao_vira_aprovacao():
    """O percentil retém para análise; ele não aprova nem rejeita semanticamente."""
    veredito = evaluate_gates(
        _caixa(human_decision="approved_pothole", quarantine_reasons=["área abaixo do percentil"]),
        image=_imagem(),
        source_checks=TODOS_OS_CHECKS,
        urmind_class=D40,
    )

    assert not veredito.training_allowed
    assert veredito.semantic_status == "HEURISTIC_QUARANTINE"


@pytest.mark.parametrize("check", ["duplicate_check_passed", "cross_source_check_passed"])
def test_portao_de_conjunto_aberto_bloqueia(check):
    """Deduplicação sozinha não valida; mas sem ela nada é autorizado."""
    checks = {**TODOS_OS_CHECKS, check: False}
    veredito = evaluate_gates(
        _caixa(human_decision="approved_pothole"),
        image=_imagem(),
        source_checks=checks,
        urmind_class=D40,
    )

    assert not veredito.training_allowed
    assert check in veredito.blocking_gates


def test_somente_com_todos_os_portoes_a_caixa_recebe_d40():
    """O único caminho para D40, e ele passa obrigatoriamente por uma pessoa."""
    veredito = evaluate_gates(
        _caixa(human_decision="approved_pothole"),
        image=_imagem("COMPLETE"),
        source_checks=TODOS_OS_CHECKS,
        urmind_class=D40,
    )

    assert veredito.training_allowed
    assert veredito.urmind_class == D40
    assert veredito.blocking_gates == ()
    assert all(veredito.gates[nome] for nome in REQUIRED_GATES)


def test_vocabulario_do_portao_e_o_da_folha():
    """Uma palavra que a folha aceita e o portão não conhece seria aprovação muda."""
    assert audit.DECISIONS == HUMAN_DECISIONS


# ------------------- adaptador: manifesto autorizado, nunca o raw


def _derivada(tmp_path, *, boxes=None, stale=False, sem_manifesto=False):
    """Monta em disco a cadeia mínima que o adaptador exige."""
    import hashlib

    datasets = tmp_path / "datasets"
    raw = datasets / "raw" / "urban_community"
    raw.mkdir(parents=True)
    (datasets / "manifests").mkdir()
    (datasets / "reports").mkdir()

    scan = [
        {
            "folder": "pothole",
            "stem": "1",
            "image_relpath": "datasets/raw/urban_community/Data_sets/Data_sets/pothole/images/1.jpg",
            "image_width": 100,
            "image_height": 100,
            "boxes": [{"index": 0, "class_id": 3}],
        },
        {
            "folder": "good_road",
            "stem": "9",
            "image_relpath": "datasets/raw/urban_community/Data_sets/Data_sets/good_road/images/9.jpg",
            "image_width": 100,
            "image_height": 100,
            "boxes": [],
        },
    ]
    linha_boxes = {
        "dataset_id": "urban_community",
        "stem": "1",
        "folder": "pothole",
        "image_relpath": scan[0]["image_relpath"],
        "image_width": 100,
        "image_height": 100,
        "group": "folder:pothole",
        "annotation_completeness": "COMPLETE",
        "duplicate_review_status": "NOT_IN_DUPLICATE_PAIR",
        "boxes": boxes
        if boxes is not None
        else [
            {
                "source_label": "pothole",
                "geometry_valid": True,
                "source_category_validated": True,
                "quarantine_reasons": [],
                "human_decision": None,
                "xmin": 10,
                "ymin": 10,
                "xmax": 50,
                "ymax": 50,
            }
        ],
    }

    scan_path = datasets / "manifests" / "urban_community_scan.jsonl"
    boxes_path = datasets / "manifests" / "urban_community_boxes.jsonl"
    scan_path.write_text("\n".join(json.dumps(r) for r in scan) + "\n", encoding="utf-8")
    if not sem_manifesto:
        boxes_path.write_text(json.dumps(linha_boxes) + "\n", encoding="utf-8")

    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    conversao = {
        "mapping": {"semantic_mapping_approved": True},
        "integrity": {
            "scan_manifest_sha256": sha(scan_path),
            "boxes_manifest_sha256": ("0" * 64)
            if stale
            else (sha(boxes_path) if boxes_path.is_file() else None),
        },
    }
    (datasets / "reports" / "urban_community_conversion.json").write_text(
        json.dumps(conversao), encoding="utf-8"
    )
    validacao = {
        "passed": True,
        "duplicates": {"exact_sha256_groups": [], "blocking_near_duplicate_pairs": []},
        "integrity": {
            "cross_source_valid": True,
            "conversion_report_sha256": sha(
                datasets / "reports" / "urban_community_conversion.json"
            ),
            "boxes_manifest_sha256": sha(boxes_path) if boxes_path.is_file() else None,
        },
    }
    (datasets / "reports" / "urban_community_validation.json").write_text(
        json.dumps(validacao), encoding="utf-8"
    )
    return raw


def test_adaptador_nao_promove_caixa_sem_decisao_humana(tmp_path):
    """O caminho operacional inteiro, com a derivada real: zero caixas treináveis."""
    raiz = _derivada(tmp_path)

    registros = list(read_urban_community(raiz))
    pothole = next(r for r in registros if r.group == "pothole")

    assert pothole.boxes == ()
    assert not pothole.usable
    # Some de `usable`, não some da conta: o motivo fica registrado.
    assert pothole.rejected
    assert "box_semantic_validation_passed" in pothole.rejected[0].reason


def test_adaptador_autoriza_somente_a_caixa_aprovada(tmp_path):
    aprovada = {
        "source_label": "pothole",
        "geometry_valid": True,
        "source_category_validated": True,
        "quarantine_reasons": [],
        "human_decision": "approved_pothole",
        "xmin": 10,
        "ymin": 10,
        "xmax": 50,
        "ymax": 50,
    }
    negada = {**aprovada, "human_decision": "wrong_class", "xmin": 60, "xmax": 90}
    raiz = _derivada(tmp_path, boxes=[aprovada, negada])

    pothole = next(r for r in read_urban_community(raiz) if r.group == "pothole")

    assert len(pothole.boxes) == 1
    assert pothole.boxes[0].xmin == 10
    assert len(pothole.rejected) == 1


def test_good_road_sai_marcada_e_nunca_como_negativa(tmp_path):
    raiz = _derivada(tmp_path)

    good = next(r for r in read_urban_community(raiz) if r.group == "good_road")

    assert good.negative_status == NEGATIVE_SEMANTICS_UNVERIFIED
    assert not good.trusted_negative
    assert good.boxes == ()


def test_manifesto_ausente_falha_fechado(tmp_path):
    """Sem a derivada, a resposta é erro — jamais uma releitura do raw."""
    raiz = _derivada(tmp_path, sem_manifesto=True)

    with pytest.raises(AdapterError, match="ausente"):
        list(read_urban_community(raiz))


@pytest.mark.parametrize(
    "relative_path",
    [
        "manifests/urban_community_scan.jsonl",
        "manifests/urban_community_boxes.jsonl",
        "reports/urban_community_conversion.json",
        "reports/urban_community_validation.json",
    ],
)
def test_artefato_derivado_cloud_only_falha_sem_abrir(tmp_path, monkeypatch, relative_path):
    """Remover a guarda permitiria que read/open/hash hidratasse o placeholder."""
    raiz = _derivada(tmp_path)
    alvo = tmp_path / "datasets" / relative_path
    abertos = []
    original_read_text = pathlib.Path.read_text
    original_open = pathlib.Path.open

    monkeypatch.setattr(adapters, "_is_cloud_only", lambda path: path == alvo)

    def read_text_spy(self, *args, **kwargs):
        if self == alvo:
            abertos.append(("read_text", self))
        return original_read_text(self, *args, **kwargs)

    def open_spy(self, *args, **kwargs):
        if self == alvo:
            abertos.append(("open", self))
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", read_text_spy)
    monkeypatch.setattr(pathlib.Path, "open", open_spy)

    with pytest.raises(AdapterError, match="cloud-only"):
        list(read_urban_community(raiz))

    assert abertos == []


def test_manifesto_desatualizado_falha_fechado(tmp_path):
    """Hash de outra versão significa que ninguém validou ESTAS caixas."""
    raiz = _derivada(tmp_path, stale=True)

    with pytest.raises(AdapterError, match="diverge"):
        list(read_urban_community(raiz))


def test_manifesto_alterado_depois_da_conversao_falha_fechado(tmp_path):
    raiz = _derivada(tmp_path)
    manifesto = tmp_path / "datasets" / "manifests" / "urban_community_boxes.jsonl"
    manifesto.write_text(manifesto.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(AdapterError, match="desatualizada"):
        list(read_urban_community(raiz))


def test_adaptador_nao_cai_de_volta_no_raw(tmp_path):
    """Mesmo com o raw presente e íntegro, derivada quebrada = fonte bloqueada."""
    raiz = _derivada(tmp_path, sem_manifesto=True)
    pasta = raiz / "Data_sets" / "Data_sets" / "pothole"
    (pasta / "labels").mkdir(parents=True)
    (pasta / "images").mkdir(parents=True)
    (pasta / "labels" / "1.txt").write_text("3 0.5 0.5 0.2 0.2\n", encoding="utf-8")

    with pytest.raises(AdapterError):
        list(read_urban_community(raiz))


def test_caixa_sem_classe_nunca_chega_ao_conjunto_de_treino(tmp_path):
    """Teste de alcançabilidade: adaptador → registros → split de treino."""
    raiz = _derivada(tmp_path)

    registros = list(read_urban_community(raiz))
    treinaveis = [r for r in registros if r.usable]
    split = split_by_group(
        treinaveis, lambda r: r.group, SplitRatios(train=1.0, validation=0.0, test=0.0)
    )

    assert treinaveis == []
    assert len(split) == 0


def test_catalogo_nao_declara_a_fonte_treinavel_enquanto_bloqueada():
    fonte = get_source("urban_community")

    assert not fonte.feeds_training
    assert DatasetUsage.UNUSABLE in fonte.usage
    assert DatasetUsage.TRAIN in fonte.potential_usage
    assert fonte.unlock_requirement


# ---------------- a decisão humana precisa CHEGAR ao manifesto e mover o portão


def test_completude_da_folha_chega_ao_manifesto(monkeypatch, tmp_path):
    """A folha expunha `image_annotation_completeness` e ninguém a lia.

    Sem isto, mesmo uma revisão integral — toda caixa `approved_pothole`, toda
    imagem `COMPLETE` — deixaria `image_annotation_complete` fechado para sempre:
    a auditoria não teria estado terminal de sucesso, que é o mesmo defeito já
    corrigido no UNIVALI, aqui em outra roupa.
    """
    folha = tmp_path / "sheet.jsonl"
    folha.write_text(
        "\n".join(
            json.dumps(linha)
            for linha in (
                {
                    "box_id": "1#0",
                    "image_id": "1",
                    "decision": "approved_pothole",
                    "image_annotation_completeness": "COMPLETE",
                },
                {
                    "box_id": "2#0",
                    "image_id": "2",
                    "decision": "approved_pothole",
                    "image_annotation_completeness": "UNKNOWN",
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(conv, "AUDIT_SHEET", folha)
    monkeypatch.setattr(conv, "require_local", lambda p: p)

    decisoes, completudes = conv.load_human_decisions()

    # A folha devolve a LINHA inteira, não só a decisão: aplicar uma decisão
    # exige conferir a que caixa ela pertence.
    assert decisoes["1#0"]["decision"] == "approved_pothole"
    assert completudes == {"1": "COMPLETE", "2": "UNKNOWN"}


def test_anotacao_incompleta_em_uma_caixa_contamina_a_imagem(monkeypatch, tmp_path):
    """Uma caixa correta não salva a imagem: falta de caixa ensina falso negativo."""
    folha = tmp_path / "sheet.jsonl"
    folha.write_text(
        "\n".join(
            json.dumps(linha)
            for linha in (
                {
                    "box_id": "7#0",
                    "image_id": "7",
                    "decision": "approved_pothole",
                    "image_annotation_completeness": "COMPLETE",
                },
                {
                    "box_id": "7#1",
                    "image_id": "7",
                    "decision": "annotation_incomplete",
                    "image_annotation_completeness": "COMPLETE",
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(conv, "AUDIT_SHEET", folha)
    monkeypatch.setattr(conv, "require_local", lambda p: p)

    _, completudes = conv.load_human_decisions()

    assert completudes["7"] == "INCOMPLETE"


def test_revisao_completa_atravessa_a_conversao_e_abre_o_portao():
    """Fluxo real: folha preenchida -> convert() -> caixa autorizada com D40."""
    rows = [scan_row("1", boxes=[box(10, 10, 200, 200)])]

    manifest, resumo = conv.convert(
        rows,
        D40,
        set(),
        decisoes={"1#0": _folha()},
        completudes={"1": "COMPLETE"},
        scan_sha=SHA_AUDITADO,
    )
    caixa = manifest[0]["boxes"][0]

    # A conversão roda ANTES da validação, então os portões de conjunto ainda
    # estão fechados aqui — e é o adaptador que os reavalia. O que este teste
    # prova é que a decisão humana chega e move o que depende dela.
    assert caixa["human_review_status"] == "APPROVED_POTHOLE"
    assert caixa["gates"]["box_semantic_validation_passed"]
    assert caixa["gates"]["image_annotation_complete"]
    assert resumo["blocking_gates"].get("box_semantic_validation_passed") is None


def test_decisao_vazia_continua_bloqueando_depois_da_conversao():
    rows = [scan_row("1", boxes=[box(10, 10, 200, 200)])]

    manifest, _ = conv.convert(rows, D40, set(), decisoes={}, completudes={})
    caixa = manifest[0]["boxes"][0]

    assert caixa["human_review_status"] == "PENDING"
    assert not caixa["training_allowed"]
    assert caixa["urmind_class"] is None


def test_folha_de_duplicatas_nasce_pendente_e_versionada():
    """Os 4 pares dHash 0 precisam de decisão PRÓPRIA, não de aprovação de caixa."""
    assert "same_scene_keep_a" in audit.DUPLICATE_DECISIONS
    assert "different_scenes" in audit.DUPLICATE_DECISIONS
    assert audit.DUPLICATE_PENDING == "PENDING_HUMAN_DUPLICATE_REVIEW"


# ------------- decisão humana só vale para a caixa que foi realmente auditada


SHA_AUDITADO = "a" * 64
SHA_OUTRO = "b" * 64


def _folha(**extra):
    """Linha de folha vinculada à caixa de `scan_row`/`box` padrão."""
    base = {
        "box_id": "1#0",
        "image_id": "1",
        "source_scan_sha256": SHA_AUDITADO,
        "image_relpath": scan_row()["image_relpath"],
        "source_label": "pothole",
        "bbox_xyxy": [10, 10, 200, 200],
        "decision": "approved_pothole",
    }
    return {**base, **extra}


def test_decisao_com_vinculo_integro_e_aplicada():
    decisao, status = conv.bind_decision(
        _folha(), scan_row(), box(10, 10, 200, 200), scan_sha=SHA_AUDITADO
    )

    assert decisao == "approved_pothole"
    assert status == "APPROVED_POTHOLE"


def test_scan_diferente_invalida_a_decisao():
    """Outra versão do scan: as caixas auditadas não são mais estas."""
    decisao, status = conv.bind_decision(
        _folha(), scan_row(), box(10, 10, 200, 200), scan_sha=SHA_OUTRO
    )

    assert decisao is None
    assert status == "STALE_HUMAN_REVIEW"


def test_bbox_diferente_invalida_a_decisao():
    """O caso que `stem#index` não pega: a caixa foi substituída no mesmo índice."""
    decisao, status = conv.bind_decision(
        _folha(), scan_row(), box(300, 300, 400, 400), scan_sha=SHA_AUDITADO
    )

    assert decisao is None
    assert status == "STALE_HUMAN_REVIEW"


def test_imagem_diferente_invalida_a_decisao():
    decisao, status = conv.bind_decision(
        _folha(image_relpath="datasets/raw/urban_community/outra.jpg"),
        scan_row(),
        box(10, 10, 200, 200),
        scan_sha=SHA_AUDITADO,
    )

    assert decisao is None
    assert status == "STALE_HUMAN_REVIEW"


def test_mesmo_box_id_com_conteudo_diferente_nao_aproveita_a_aprovacao():
    """Reordenar as caixas de uma imagem não pode migrar a aprovação."""
    folha = _folha(bbox_xyxy=[10, 10, 200, 200])

    # O índice 0 agora contém outra geometria — a que era índice 1.
    decisao, status = conv.bind_decision(
        folha, scan_row(), box(5, 5, 40, 40, index=0), scan_sha=SHA_AUDITADO
    )

    assert decisao is None
    assert status == "STALE_HUMAN_REVIEW"


def test_decisao_sem_proveniencia_e_fail_closed():
    """Folha antiga, gravada antes do vínculo existir, não prova sobre o quê."""
    folha = _folha()
    del folha["source_scan_sha256"]

    decisao, status = conv.bind_decision(
        folha, scan_row(), box(10, 10, 200, 200), scan_sha=SHA_AUDITADO
    )

    assert decisao is None
    assert status == "STALE_HUMAN_REVIEW"


def test_ausencia_de_decisao_continua_pendente_e_nao_obsoleta():
    decisao, status = conv.bind_decision(
        _folha(decision=None), scan_row(), box(10, 10, 200, 200), scan_sha=SHA_AUDITADO
    )

    assert decisao is None
    assert status == "PENDING"


def test_vinculo_integro_sobrevive_a_execucao_idempotente():
    """Rodar duas vezes sem mudar nada não pode invalidar revisão legítima."""
    primeira = conv.convert(
        [scan_row("1", boxes=[box(10, 10, 200, 200)])],
        D40,
        set(),
        decisoes={"1#0": _folha()},
        completudes={"1": "COMPLETE"},
        scan_sha=SHA_AUDITADO,
    )
    segunda = conv.convert(
        [scan_row("1", boxes=[box(10, 10, 200, 200)])],
        D40,
        set(),
        decisoes={"1#0": _folha()},
        completudes={"1": "COMPLETE"},
        scan_sha=SHA_AUDITADO,
    )

    assert primeira[0] == segunda[0]
    assert primeira[0][0]["boxes"][0]["human_review_status"] == "APPROVED_POTHOLE"
    assert primeira[1]["stale_human_reviews"] == 0


def test_decisao_obsoleta_mantem_o_portao_fechado_e_e_contada():
    manifest, resumo = conv.convert(
        [scan_row("1", boxes=[box(10, 10, 200, 200)])],
        D40,
        set(),
        decisoes={"1#0": _folha()},
        completudes={"1": "COMPLETE"},
        scan_sha=SHA_OUTRO,
    )
    caixa = manifest[0]["boxes"][0]

    assert resumo["stale_human_reviews"] == 1
    assert caixa["human_review_status"] == "STALE_HUMAN_REVIEW"
    assert not caixa["training_allowed"]
    assert caixa["urmind_class"] is None


# ------------- decisão de duplicata precisa TIRAR a imagem rejeitada do treino


def _par(decision, a="1", b="2", sha=SHA_AUDITADO, **extra):
    return {
        "pair_id": f"{a}__{b}",
        "a": a,
        "b": b,
        "source_scan_sha256": sha,
        "a_image_relpath": scan_row(a)["image_relpath"],
        "b_image_relpath": scan_row(b)["image_relpath"],
        "decision": decision,
        **extra,
    }


def _par_convertido(decision, *, caixas_b=1, **extra):
    """Duas imagens idênticas, ambas aprovadas por pessoa, e uma decisão de par."""
    rows = [
        scan_row("1", boxes=[box(10, 10, 200, 200)]),
        scan_row("2", boxes=[box(10 + i, 10, 200 + i, 200, index=i) for i in range(caixas_b)]),
    ]
    decisoes = {"1#0": _folha()}
    for i in range(caixas_b):
        decisoes[f"2#{i}"] = _folha(
            box_id=f"2#{i}",
            image_id="2",
            image_relpath=scan_row("2")["image_relpath"],
            bbox_xyxy=[10 + i, 10, 200 + i, 200],
        )
    manifest, resumo = conv.convert(
        rows,
        D40,
        set(),
        decisoes=decisoes,
        completudes={"1": "COMPLETE", "2": "COMPLETE"},
        scan_sha=SHA_AUDITADO,
        duplicate_reviews=[_par(decision, **extra)],
    )
    autorizado = authorize_rows(manifest, source_checks=TODOS_OS_CHECKS, urmind_class=D40)
    por_stem = {linha["stem"]: linha for linha in autorizado}
    return por_stem, resumo


def test_keep_a_mantem_a_e_bloqueia_b():
    por_stem, resumo = _par_convertido("same_scene_keep_a")

    assert all(b["training_allowed"] for b in por_stem["1"]["boxes"])
    assert por_stem["2"]["duplicate_rejected"]
    assert por_stem["2"]["duplicate_rejection_reason"] == "near_duplicate_human_rejected"
    assert not any(b["training_allowed"] for b in por_stem["2"]["boxes"])
    assert "duplicate_review_resolved" in por_stem["2"]["boxes"][0]["blocking_gates"]
    assert resumo["duplicate_rejected_images"] == 1


def test_keep_b_mantem_b_e_bloqueia_a():
    por_stem, _ = _par_convertido("same_scene_keep_b")

    assert por_stem["1"]["duplicate_rejected"]
    assert not any(b["training_allowed"] for b in por_stem["1"]["boxes"])
    assert all(b["training_allowed"] for b in por_stem["2"]["boxes"])


def test_par_sem_decisao_bloqueia_os_dois_lados():
    por_stem, resumo = _par_convertido(None)

    for stem in ("1", "2"):
        assert por_stem[stem]["duplicate_review_status"] == "PENDING_HUMAN_DUPLICATE_REVIEW"
        assert not any(b["training_allowed"] for b in por_stem[stem]["boxes"])
    assert resumo["pending_duplicate_review_images"] == 2


def test_decisao_ambigua_continua_bloqueando():
    por_stem, _ = _par_convertido("ambiguous")

    assert not any(b["training_allowed"] for linha in por_stem.values() for b in linha["boxes"])


def test_decisao_de_duplicata_fora_do_vocabulario_e_fail_closed():
    por_stem, _ = _par_convertido("parecem_iguais")

    for stem in ("1", "2"):
        assert por_stem[stem]["duplicate_review_status"] == "INVALID_DUPLICATE_DECISION"
        assert not any(b["training_allowed"] for b in por_stem[stem]["boxes"])


def test_imagem_rejeitada_com_varias_caixas_nao_treina_nenhuma():
    por_stem, _ = _par_convertido("same_scene_keep_a", caixas_b=3)

    assert len(por_stem["2"]["boxes"]) == 3
    assert not any(b["training_allowed"] for b in por_stem["2"]["boxes"])


def test_decisao_de_duplicata_e_idempotente():
    primeira, _ = _par_convertido("same_scene_keep_a")
    segunda, _ = _par_convertido("same_scene_keep_a")

    assert primeira == segunda


def test_scan_diferente_invalida_decisao_de_duplicata():
    """Par com vínculo a outro scan: a decisão é de outras imagens."""
    por_stem, _ = _par_convertido("same_scene_keep_a", sha=SHA_OUTRO)

    for stem in ("1", "2"):
        assert por_stem[stem]["duplicate_review_status"] == "STALE_DUPLICATE_REVIEW"
        assert not any(b["training_allowed"] for b in por_stem[stem]["boxes"])


def test_imagem_do_par_trocada_invalida_decisao_de_duplicata():
    estados = resolve_duplicate_pair(
        _par("same_scene_keep_a", b_image_relpath="datasets/raw/outra.jpg"),
        scan_sha=SHA_AUDITADO,
        image_paths={"1": scan_row("1")["image_relpath"], "2": scan_row("2")["image_relpath"]},
    )

    assert estados == ("STALE_DUPLICATE_REVIEW", "STALE_DUPLICATE_REVIEW")


def test_manifesto_sem_estado_de_duplicata_nao_abre_o_portao():
    """Linha que não declara o estado não prova que não é o lado rejeitado."""
    veredito = evaluate_gates(
        _caixa(human_decision="approved_pothole"),
        image={"annotation_completeness": "COMPLETE"},
        source_checks=TODOS_OS_CHECKS,
        urmind_class=D40,
    )

    assert "duplicate_review_resolved" in veredito.blocking_gates


# ---------------- validação: par só deixa de bloquear quando o manifesto aplica


PAR_IDENTICO = [{"a": "1", "b": "2", "distance": 0}]


def _linhas_manifesto(estado_a, estado_b):
    return [
        {
            "stem": "1",
            "image_relpath": scan_row("1")["image_relpath"],
            "duplicate_review_status": estado_a,
        },
        {
            "stem": "2",
            "image_relpath": scan_row("2")["image_relpath"],
            "duplicate_review_status": estado_b,
        },
    ]


def test_par_sem_linha_na_folha_bloqueia():
    bloqueio = val.blocking_duplicate_pairs(
        PAR_IDENTICO,
        [],
        _linhas_manifesto("NOT_IN_DUPLICATE_PAIR", "NOT_IN_DUPLICATE_PAIR"),
        SHA_AUDITADO,
    )

    assert len(bloqueio) == 1


def test_keep_a_registrado_mas_nao_aplicado_continua_bloqueando():
    """O finding: riscar o par no relatório sem tirar `b` do manifesto."""
    bloqueio = val.blocking_duplicate_pairs(
        PAR_IDENTICO,
        [_par("same_scene_keep_a")],
        _linhas_manifesto("NOT_IN_DUPLICATE_PAIR", "NOT_IN_DUPLICATE_PAIR"),
        SHA_AUDITADO,
    )

    assert len(bloqueio) == 1
    assert "reconverta" in bloqueio[0]["reason"]


def test_keep_a_aplicado_no_manifesto_libera_o_par():
    bloqueio = val.blocking_duplicate_pairs(
        PAR_IDENTICO,
        [_par("same_scene_keep_a")],
        _linhas_manifesto("DUPLICATE_KEPT", "DUPLICATE_REJECTED"),
        SHA_AUDITADO,
    )

    assert bloqueio == []


# ------------------ UNIVALI: inventário vazio ou parcial nunca vira "0 matches"


def _univali(tmp_path, monkeypatch, *, imagens, nuvem=()):
    from PIL import Image

    manifesto = tmp_path / "univali_br_boxes.jsonl"
    linhas = []
    for nome, existe in imagens:
        rel = f"datasets/raw/univali_br/{nome}"
        if existe == "ok":
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            Image.new("L", (32, 32), color=128).save(tmp_path / rel)
        elif existe == "lixo":
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_bytes(b"isto nao e imagem")
        linhas.append(json.dumps({"image_relpath": rel}))
    manifesto.write_text("\n".join(linhas) + "\n", encoding="utf-8")

    monkeypatch.setattr(val, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(val, "is_cloud_only", lambda p: p.name in nuvem)
    monkeypatch.setattr(val, "require_local", lambda path: path)
    # `_core.file_sha256` recusa caminho fora do projeto — guarda de segurança
    # correta em produção. O teste troca só o cálculo, não relaxa a guarda.
    monkeypatch.setattr(val, "file_sha256", lambda p: hashlib.sha256(p.read_bytes()).hexdigest())
    return manifesto


URBAN_HASHES = {"u": (1 << 128) - 1}  # longe de qualquer imagem cinza lisa


def test_inventario_univali_integro_compara_normalmente(tmp_path, monkeypatch):
    manifesto = _univali(tmp_path, monkeypatch, imagens=[("a.png", "ok"), ("b.png", "ok")])

    resultado = val.univali_cross_check(manifesto, set(), URBAN_HASHES, 4, skip=False)

    assert resultado["status"] == "OK"
    assert resultado["valid"]
    assert resultado["reference_images_with_hash"] == 2


def test_nenhuma_imagem_acessivel_reprova_sem_estourar(tmp_path, monkeypatch):
    """Era `ValueError: min() arg is an empty sequence`."""
    manifesto = _univali(tmp_path, monkeypatch, imagens=[("a.png", "falta"), ("b.png", "falta")])

    resultado = val.univali_cross_check(manifesto, set(), URBAN_HASHES, 4, skip=False)

    assert resultado["status"] == "EMPTY_HASH_INVENTORY"
    assert not resultado["valid"]
    assert resultado["reference_images_missing"] == 2
    assert "não é ausência de contaminação" in resultado["reason"]


def test_manifesto_univali_ausente_reprova_controladamente(tmp_path, monkeypatch):
    monkeypatch.setattr(val, "PROJECT_ROOT", tmp_path)

    resultado = val.univali_cross_check(
        tmp_path / "nao_existe.jsonl", set(), URBAN_HASHES, 4, skip=False
    )

    assert resultado["status"] == "MANIFEST_MISSING"
    assert not resultado["valid"]


def test_imagens_cloud_only_nao_sao_hidratadas_e_reprovam(tmp_path, monkeypatch):
    manifesto = _univali(
        tmp_path, monkeypatch, imagens=[("a.png", "ok"), ("b.png", "ok")], nuvem=("a.png", "b.png")
    )
    abertos = []
    original = pathlib.Path.read_bytes

    def espiao(self, *a, **k):
        abertos.append(self.name)
        return original(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_bytes", espiao)

    resultado = val.univali_cross_check(manifesto, set(), URBAN_HASHES, 4, skip=False)

    assert resultado["status"] == "EMPTY_HASH_INVENTORY"
    assert resultado["reference_images_cloud_only"] == 2
    assert "a.png" not in abertos and "b.png" not in abertos


def _rdd_authority(
    monkeypatch,
    tmp_path,
    *,
    expected_records,
    inventory_sha256,
    dataset_id="rdd2022",
    version="2022-crddc",
    population_id="official-population",
    inventory_path="rdd2022_inventory.jsonl",
    registry_path="rdd2022_inventory.jsonl",
    source_records=None,
    source_sha256=None,
    definition_sha256=None,
):
    source_records = expected_records if source_records is None else source_records
    source = tmp_path / "File_List_CRDDC_RDD2022.txt"
    source.write_text(
        "+---Country\n|   \\---train\n"
        + "".join(f"|           image_{index}.jpg\n" for index in range(source_records)),
        encoding="utf-8",
    )
    actual_source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    definition = tmp_path / "rdd2022.json"
    definition.write_text(
        json.dumps({"name": "rdd2022", "version": "2022-crddc"}), encoding="utf-8"
    )
    actual_definition_sha = hashlib.sha256(definition.read_bytes()).hexdigest()
    contract = tmp_path / "artifact_contract.yaml"
    contract.write_text(
        "rdd2022_cross_source_reference:\n"
        f"  dataset_id: {dataset_id}\n"
        f"  version: {version}\n"
        f"  population_id: {population_id}\n"
        "  source_definition: rdd2022.json\n"
        f"  source_definition_sha256: '{definition_sha256 or actual_definition_sha}'\n"
        "  source_index: File_List_CRDDC_RDD2022.txt\n"
        f"  source_index_sha256: '{source_sha256 or actual_source_sha}'\n"
        f"  expected_records: {expected_records}\n"
        f"  inventory: {inventory_path}\n"
        f"  inventory_sha256: '{inventory_sha256}'\n",
        encoding="utf-8",
    )
    registry = tmp_path / "artifact_registry.json"
    registry.write_text(
        json.dumps(
            {
                "artifacts": [
                    {
                        "path": registry_path,
                        "sha256": inventory_sha256,
                        "size_bytes": (tmp_path / "rdd2022_inventory.jsonl").stat().st_size,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(val, "ARTIFACT_REGISTRY", registry)
    monkeypatch.setattr(val, "ARTIFACT_CONTRACT", contract)
    monkeypatch.setattr(val, "require_local", lambda path: path)


def test_rdd_inventario_parcial_nao_passa_com_zero_matches(tmp_path, monkeypatch):
    inventory = tmp_path / "rdd2022_inventory.jsonl"
    inventory.write_text(
        json.dumps({"sha256": "f" * 64, "dhash128": "0" * 32}) + "\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(inventory.read_bytes()).hexdigest()
    _rdd_authority(monkeypatch, tmp_path, expected_records=2, inventory_sha256=digest)
    monkeypatch.setattr(val, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(val, "file_sha256", lambda p: hashlib.sha256(p.read_bytes()).hexdigest())

    result = val.cross_source_check("rdd2022", inventory, set(), {}, 4, skip=False)

    assert result["valid"] is False
    assert result["expected_records"] == 2
    assert result["inventory_records"] == 1
    assert result["completeness_ratio"] == 0.5


def test_rdd_inventario_completo_sem_matches_passa(tmp_path, monkeypatch):
    inventory = tmp_path / "rdd2022_inventory.jsonl"
    inventory.write_text(
        json.dumps({"sha256": "f" * 64, "dhash128": "0" * 32}) + "\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(inventory.read_bytes()).hexdigest()
    _rdd_authority(monkeypatch, tmp_path, expected_records=1, inventory_sha256=digest)
    monkeypatch.setattr(val, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(val, "file_sha256", lambda p: hashlib.sha256(p.read_bytes()).hexdigest())

    result = val.cross_source_check("rdd2022", inventory, set(), {}, 4, skip=False)

    assert result["valid"] is True
    assert result["completeness_status"] == "COMPLETE"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"version": "outra-versao"}, "versão"),
        ({"dataset_id": "outro-dataset"}, "dataset_id"),
        ({"population_id": ""}, "incompleta"),
        ({"inventory_path": "outro/rdd2022_inventory.jsonl"}, "caminho completo"),
        ({"registry_path": "outro/rdd2022_inventory.jsonl"}, "registro exato"),
        ({"source_records": 2}, "cardinalidade declarada"),
        ({"source_sha256": "0" * 64}, "lista nominal"),
        ({"definition_sha256": "0" * 64}, "definição versionada"),
    ],
)
def test_rdd_autoridade_divergente_falha_fechado(tmp_path, monkeypatch, overrides, reason):
    inventory = tmp_path / "rdd2022_inventory.jsonl"
    inventory.write_text(
        json.dumps({"sha256": "f" * 64, "dhash128": "0" * 32}) + "\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(inventory.read_bytes()).hexdigest()
    _rdd_authority(
        monkeypatch,
        tmp_path,
        expected_records=1,
        inventory_sha256=digest,
        **overrides,
    )
    monkeypatch.setattr(val, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(val, "file_sha256", lambda p: hashlib.sha256(p.read_bytes()).hexdigest())

    result = val.cross_source_check("rdd2022", inventory, set(), {}, 4, skip=False)

    assert result["valid"] is False
    assert result["completeness_status"] != "COMPLETE"
    assert reason in result["reason"]


def test_rdd_inventario_de_um_registro_nao_passa_contra_populacao_maior(tmp_path, monkeypatch):
    inventory = tmp_path / "rdd2022_inventory.jsonl"
    inventory.write_text(
        json.dumps({"sha256": "f" * 64, "dhash128": "0" * 32}) + "\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(inventory.read_bytes()).hexdigest()
    _rdd_authority(
        monkeypatch,
        tmp_path,
        expected_records=2,
        source_records=2,
        inventory_sha256=digest,
    )
    monkeypatch.setattr(val, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(val, "file_sha256", lambda p: hashlib.sha256(p.read_bytes()).hexdigest())

    result = val.cross_source_check("rdd2022", inventory, set(), {}, 4, skip=False)

    assert result["valid"] is False
    assert result["completeness_status"] == "INCOMPLETE_CARDINALITY"


def test_rdd_inventario_stale_nao_passa(tmp_path, monkeypatch):
    inventory = tmp_path / "rdd2022_inventory.jsonl"
    inventory.write_text(
        json.dumps({"sha256": "f" * 64, "dhash128": "0" * 32}) + "\n",
        encoding="utf-8",
    )
    _rdd_authority(monkeypatch, tmp_path, expected_records=1, inventory_sha256="0" * 64)
    monkeypatch.setattr(val, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(val, "file_sha256", lambda p: hashlib.sha256(p.read_bytes()).hexdigest())

    result = val.cross_source_check("rdd2022", inventory, set(), {}, 4, skip=False)

    assert result["valid"] is False
    assert "diverge" in result["reason"]


def test_rdd_registro_sem_ambos_os_hashes_nao_passa(tmp_path, monkeypatch):
    inventory = tmp_path / "rdd2022_inventory.jsonl"
    inventory.write_text(json.dumps({"sha256": "f" * 64}) + "\n", encoding="utf-8")
    digest = hashlib.sha256(inventory.read_bytes()).hexdigest()
    _rdd_authority(monkeypatch, tmp_path, expected_records=1, inventory_sha256=digest)
    monkeypatch.setattr(val, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(val, "file_sha256", lambda p: hashlib.sha256(p.read_bytes()).hexdigest())

    result = val.cross_source_check("rdd2022", inventory, set(), {}, 4, skip=False)

    assert result["valid"] is False
    assert result["hashable_records"] == 0


def test_inventario_parcial_reprova_como_incompleto(tmp_path, monkeypatch):
    """Comportamento documentado: medir parte do holdout não aprova o todo."""
    manifesto = _univali(tmp_path, monkeypatch, imagens=[("a.png", "ok"), ("b.png", "falta")])

    resultado = val.univali_cross_check(manifesto, set(), URBAN_HASHES, 4, skip=False)

    assert resultado["status"] == "INCOMPLETE_HASH_INVENTORY"
    assert not resultado["valid"]


def test_imagem_ilegivel_e_contada_e_nunca_estoura(tmp_path, monkeypatch):
    manifesto = _univali(tmp_path, monkeypatch, imagens=[("a.png", "lixo")])

    resultado = val.univali_cross_check(manifesto, set(), URBAN_HASHES, 4, skip=False)

    assert resultado["status"] == "EMPTY_HASH_INVENTORY"
    assert resultado["reference_images_unreadable"] == 1
