"""Travas do pipeline UNIVALI: validação obrigatória e taxonomia canônica.

Duas garantias que existiam só no caminho feliz:

1. o split se declarava livre de vazamento mesmo sem a validação de duplicatas
   ter rodado — ausência de evidência virava "zero duplicatas";
2. `--assert-v1-mapping` aceitava qualquer string, então uma evidência escrita
   para "POTHOLE é buraco" podia promover as 1.490 caixas para `URMIND_ROAD_D00`;
3. a auditoria visual quebrava com `ValueError` desde que as máscaras vazias
   passaram a ser preservadas — 1.671 linhas sem caixa chegavam a `min()`/`max()`;
4. a auditoria humana não tinha estado terminal de sucesso: a validação era um
   `False` fixo, então preencher a folha inteira não mudava nada e o bloqueador
   do catálogo nunca poderia ser removido;
5. o validador de proveniência achatava o documento antes de procurar os campos,
   então o `version` do cabeçalho passava por `source_version` e qualquer `note`
   aninhado passava por `drop_reasons`.

Todas eram invisíveis pelo mesmo motivo: o artefato saía com aparência de
validado, ou o pipeline com aparência de completo.
"""

from __future__ import annotations

import json
import pathlib
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "datasets"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import convert_univali_masks as conv
import make_univali_splits as splits
import prepare_univali_human_audit as human
import render_univali_audit as render
import validate_manifests as manifests

BOXES_SHA = "a" * 64
OUTRO_SHA = "b" * 64


@pytest.fixture(autouse=True)
def _manifest_fixture_paths_are_local(monkeypatch):
    """Os testes de schema usam tmp_path; cloud-only tem um teste dedicado."""
    monkeypatch.setattr(manifests, "require_local", lambda path: path)


def test_validate_manifests_recusa_cloud_only_sem_abrir(tmp_path, monkeypatch):
    artifact = tmp_path / "urban_community_validation.json"
    opened = []

    def refuse(_path):
        raise RuntimeError("cloud-only: leitura recusada")

    def spy(self, *args, **kwargs):
        opened.append(self)
        raise AssertionError("placeholder não pode ser aberto")

    monkeypatch.setattr(manifests, "require_local", refuse)
    monkeypatch.setattr(pathlib.Path, "read_text", spy)

    with pytest.raises(RuntimeError, match="cloud-only"):
        manifests._check(artifact)

    assert opened == []


def test_paineis_humanos_cobrem_toda_imagem_da_folha_univali():
    rows = [{"directory": "b"}, {"directory": "a"}]
    audit_rows = [{"image_id": "b"}, {"image_id": "a"}, {"image_id": "b"}]

    selected = render.audit_panel_rows(rows, audit_rows)

    assert [row["directory"] for row in selected] == ["a", "b"]


def test_paineis_humanos_univali_falham_se_folha_referencia_imagem_ausente():
    with pytest.raises(RuntimeError, match="ausentes do manifesto"):
        render.audit_panel_rows([{"directory": "a"}], [{"image_id": "b"}])


# --------------------------------------------------- 1. validação obrigatória


def _relatorio_valido(**overrides) -> dict:
    base = {
        "passed": True,
        "generated_at": "2026-09-10T16:00:00-03:00",
        "integrity": {
            "boxes_manifest_sha256": BOXES_SHA,
            "rdd_inventory_sha256": "inv-sha",
        },
        "cross_source_contamination": {
            "skipped": False,
            "valid": True,
            "inventory_sha256": "inv-sha",
        },
        "duplicates": {
            "exact_sha256_groups": [{"sha256": "x", "directories": ["d1", "d2"]}],
            "near_duplicate_pairs": [{"a": "d3", "b": "d4", "distance": 3}],
        },
    }
    base.update(overrides)
    return base


@pytest.fixture
def relatorio(tmp_path, monkeypatch):
    """Aponta o script para um relatório em tmp_path, com raiz coerente.

    `PROJECT_ROOT` acompanha porque o script cita o caminho relativo à raiz nas
    mensagens; sem isso o teste falharia por `relative_to`, não pela regra.
    """
    caminho = tmp_path / "univali_box_validation.json"
    monkeypatch.setattr(splits, "VALIDATION_REPORT", caminho)
    monkeypatch.setattr(splits, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(splits, "require_local", lambda p: p)
    monkeypatch.setattr(splits, "file_sha256", lambda _p: "sha-do-relatorio")

    def escrever(payload):
        caminho.write_text(json.dumps(payload), encoding="utf-8")
        return caminho

    return escrever


def test_validacao_presente_e_ligada_ao_manifesto_libera(relatorio):
    relatorio(_relatorio_valido())

    clusters, evidencia = splits.require_validation(BOXES_SHA)

    assert clusters == [["d1", "d2"], ["d3", "d4"]]
    assert evidencia["passed"] is True
    assert evidencia["boxes_manifest_sha256"] == BOXES_SHA
    assert evidencia["exact_duplicate_groups"] == 1
    assert evidencia["near_duplicate_pairs"] == 1


def test_relatorio_ausente_bloqueia(relatorio):
    """Ausência de evidência NÃO é zero duplicatas."""
    with pytest.raises(SystemExit) as erro:
        splits.require_validation(BOXES_SHA)

    assert "VALIDATION_REQUIRED" in str(erro.value)
    assert "não existe" in str(erro.value)


def test_relatorio_ilegivel_bloqueia(relatorio):
    relatorio_bruto = relatorio(_relatorio_valido())
    relatorio_bruto.write_text("{ isto não é json", encoding="utf-8")

    with pytest.raises(SystemExit) as erro:
        splits.require_validation(BOXES_SHA)

    assert "VALIDATION_REQUIRED" in str(erro.value)


@pytest.mark.parametrize(
    "payload",
    [
        {"passed": True, "integrity": {"boxes_manifest_sha256": BOXES_SHA}},
        _relatorio_valido(duplicates={"exact_sha256_groups": []}),
        {"duplicates": {"exact_sha256_groups": [], "near_duplicate_pairs": []}},
    ],
)
def test_relatorio_incompleto_bloqueia(relatorio, payload):
    relatorio(payload)

    with pytest.raises(SystemExit) as erro:
        splits.require_validation(BOXES_SHA)

    assert "VALIDATION_REQUIRED" in str(erro.value)


def test_relatorio_de_outra_versao_das_caixas_bloqueia(relatorio):
    """Nome de arquivo continua igual depois de reconverter; o hash não."""
    relatorio(_relatorio_valido(integrity={"boxes_manifest_sha256": OUTRO_SHA}))

    with pytest.raises(SystemExit) as erro:
        splits.require_validation(BOXES_SHA)

    assert "outra versão das caixas" in str(erro.value)


def test_relatorio_sem_vinculo_de_integridade_bloqueia(relatorio):
    relatorio(_relatorio_valido(integrity={}))

    with pytest.raises(SystemExit) as erro:
        splits.require_validation(BOXES_SHA)

    assert "boxes_manifest_sha256" in str(erro.value)


def test_validacao_reprovada_bloqueia(relatorio):
    relatorio(_relatorio_valido(passed=False, failures=["12 caixas fora do limite"]))

    with pytest.raises(SystemExit) as erro:
        splits.require_validation(BOXES_SHA)

    assert "reprovou" in str(erro.value)


# ------------------------------------------------ 2. taxonomia canônica real


def _evidencia(**overrides) -> dict:
    base = {
        "source_dataset": "univali_br",
        "source_version": "mendeley-v4",
        "source_label": "POTHOLE",
        "urmind_class": "URMIND_ROAD_D40",
        "approved": True,
        "evidence_id": "univali-pothole-2026-09-10",
        "verified_by": "equipe",
        "verified_at": "2026-09-10",
        "method": "inspeção humana de amostra estratificada",
        "sample_size": 60,
    }
    base.update(overrides)
    return base


@pytest.fixture
def evidencia_em_arquivo(tmp_path, monkeypatch):
    monkeypatch.setattr(conv, "require_local", lambda p: p)

    def escrever(payload):
        caminho = tmp_path / "evidencia.json"
        caminho.write_text(json.dumps(payload), encoding="utf-8")
        return caminho

    return escrever


def test_classe_declarada_para_pothole_e_aceita(evidencia_em_arquivo):
    """A classe permitida vem de class_mapping.yaml, não de lista no script."""
    caminho = evidencia_em_arquivo(_evidencia())

    dados = conv.load_mapping_evidence(caminho)

    assert dados["urmind_class"] == "URMIND_ROAD_D40"
    assert conv._allowed_class_for("POTHOLE") == "URMIND_ROAD_D40"


@pytest.mark.parametrize(
    "classe",
    [
        "URMIND_ROAD_D400",  # typo
        "urmind_road_d40",  # caixa errada
        "POTHOLE",  # rótulo de origem, não classe
        "URMIND_BURACO",  # classe inventada
    ],
)
def test_classe_inexistente_ou_typo_e_recusada(evidencia_em_arquivo, classe):
    caminho = evidencia_em_arquivo(_evidencia(urmind_class=classe))

    with pytest.raises(SystemExit) as erro:
        conv.load_mapping_evidence(caminho)

    assert "URMIND_ROAD_D40" in str(erro.value)


@pytest.mark.parametrize("classe", ["URMIND_ROAD_D00", "URMIND_ROAD_D10", "URMIND_ROAD_D20"])
def test_classe_canonica_porem_semanticamente_errada_e_recusada(evidencia_em_arquivo, classe):
    """Evidência de buraco não autoriza rotular trinca, mesmo sendo classe da V1."""
    caminho = evidencia_em_arquivo(_evidencia(urmind_class=classe))

    with pytest.raises(SystemExit) as erro:
        conv.load_mapping_evidence(caminho)

    assert "não autoriza rotular trinca" in str(erro.value)


def test_evidencia_de_outro_dataset_e_recusada(evidencia_em_arquivo):
    caminho = evidencia_em_arquivo(_evidencia(source_dataset="urban_community"))

    with pytest.raises(SystemExit) as erro:
        conv.load_mapping_evidence(caminho)

    assert "urban_community" in str(erro.value)


def test_evidencia_de_outra_versao_e_recusada(evidencia_em_arquivo):
    caminho = evidencia_em_arquivo(_evidencia(source_version="mendeley-v3"))

    with pytest.raises(SystemExit) as erro:
        conv.load_mapping_evidence(caminho)

    assert "versão" in str(erro.value)


def test_evidencia_de_outro_rotulo_de_origem_e_recusada(evidencia_em_arquivo):
    caminho = evidencia_em_arquivo(_evidencia(source_label="CRACK"))

    with pytest.raises(SystemExit) as erro:
        conv.load_mapping_evidence(caminho)

    assert "CRACK" in str(erro.value)


def test_evidencia_nao_aprovada_e_recusada(evidencia_em_arquivo):
    caminho = evidencia_em_arquivo(_evidencia(approved=False))

    with pytest.raises(SystemExit) as erro:
        conv.load_mapping_evidence(caminho)

    assert "aprovada" in str(erro.value)


def test_evidencia_incompleta_e_recusada(evidencia_em_arquivo):
    incompleta = _evidencia()
    del incompleta["evidence_id"]
    caminho = evidencia_em_arquivo(incompleta)

    with pytest.raises(SystemExit) as erro:
        conv.load_mapping_evidence(caminho)

    assert "incompleta" in str(erro.value)


def test_arquivo_de_evidencia_invalido_e_recusado(tmp_path, monkeypatch):
    monkeypatch.setattr(conv, "require_local", lambda p: p)
    caminho = tmp_path / "evidencia.json"
    caminho.write_text("[]", encoding="utf-8")

    with pytest.raises(SystemExit) as erro:
        conv.load_mapping_evidence(caminho)

    assert "objeto JSON" in str(erro.value)


def test_sem_evidencia_nenhuma_classe_e_aplicada():
    """O padrão continua conservador: nada de evidência, nada de promoção."""
    assert conv.load_mapping_evidence(None) is None


# ------------------------------------- máscara vazia não derruba a auditoria


def _linha_com_caixa(directory: str, area: int) -> dict:
    return {
        "directory": directory,
        "mask_status": "POSITIVE_MASK",
        "boxes": [
            {
                "mask_area_px": area,
                "fill_ratio": 0.5,
                "size_tier": "medium_small",
                "touches_border": False,
            }
        ],
    }


def _linha_vazia(directory: str) -> dict:
    return {
        "directory": directory,
        "mask_status": "EMPTY_MASK_SEMANTICS_UNRESOLVED",
        "boxes": [],
    }


def test_linhas_sem_caixa_nao_derrubam_a_selecao():
    """Era `ValueError: min() arg is an empty sequence` — o pipeline não rodava."""
    rows = [_linha_com_caixa("A", 900), _linha_vazia("B"), _linha_com_caixa("C", 50)]
    scan = {
        "B": {"masks": {"POTHOLE": {"present": True, "foreground_px": 0}}},
        "A": {"masks": {"POTHOLE": {"present": True, "foreground_px": 900}}},
    }

    casos = render.choose_samples(rows, scan, per_case=2)

    assert [r["directory"] for r in casos["smallest"]] == ["C", "A"]
    assert all(r["boxes"] for r in casos["largest"])


def test_a_mascara_vazia_continua_tendo_caso_proprio():
    """Pular no ranking não é sumir: a máscara vazia é o achado, não um estorvo."""
    rows = [_linha_com_caixa("A", 900), _linha_vazia("B")]
    scan = {"B": {"masks": {"POTHOLE": {"present": True, "foreground_px": 0}}}}

    casos = render.choose_samples(rows, scan, per_case=2)

    assert [r["directory"] for r in casos["empty_mask"]] == ["B"]


# ------------------------- a auditoria humana precisa poder terminar em sucesso


def test_folha_toda_decidida_valida_a_semantica_de_instancia():
    linhas = [{"decision": "independent_object"}, {"decision": "fragment_of_same_object"}]

    estado = human.audit_status(linhas)

    assert estado["complete"]
    assert estado["instance_semantics_validated"]
    assert estado["why_not_validated"] is None


def test_pendencia_mantem_a_hipotese_sem_verificacao():
    estado = human.audit_status([{"decision": "independent_object"}, {"decision": None}])

    assert not estado["instance_semantics_validated"]
    assert "sem decisão humana" in estado["why_not_validated"]


def test_folha_toda_ambigua_esta_completa_e_nao_valida_nada():
    """Revisar e não concluir é resposta honesta — e não é validação."""
    estado = human.audit_status([{"decision": "ambiguous"}, {"decision": "other_problem"}])

    assert estado["complete"]
    assert not estado["instance_semantics_validated"]
    assert estado["inconclusive_decisions"] == 2


def test_folha_vazia_nao_valida():
    estado = human.audit_status([])

    assert not estado["complete"]
    assert not estado["instance_semantics_validated"]


# ------------------------------ proveniência é conferida onde o contrato manda


def _escreve(tmp_path: Path, payload: dict) -> Path:
    caminho = tmp_path / "artefato.json"
    caminho.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return caminho


def test_version_de_cabecalho_nao_passa_por_source_version(tmp_path, monkeypatch):
    """Todo relatório tem `version: 1`; nenhum deles declarava a versão da FONTE."""
    monkeypatch.setattr(manifests, "PROJECT_ROOT", tmp_path)
    caminho = _escreve(tmp_path, {"version": 1, "source_dataset": "x"})

    resultado = manifests._check(caminho)

    assert "source_version" in resultado["missing"]
    assert not resultado["compliant"]


def test_note_aninhado_nao_passa_por_drop_reasons(tmp_path, monkeypatch):
    """Um comentário enterrado em outro bloco não é a prestação de contas do descarte."""
    monkeypatch.setattr(manifests, "PROJECT_ROOT", tmp_path)
    caminho = _escreve(tmp_path, {"dropped": 3, "duplicates": {"note": "para revisão"}})

    resultado = manifests._check(caminho)

    assert "drop_reasons" in resultado["missing"]


def test_caminho_declarado_e_aceito(tmp_path, monkeypatch):
    """`source.version` é posição prevista; `qualquer.coisa.version` não é."""
    monkeypatch.setattr(manifests, "PROJECT_ROOT", tmp_path)
    caminho = _escreve(tmp_path, {"source": {"version": "2022-crddc"}})

    resultado = manifests._check(caminho)

    assert resultado["fields"]["source_version"] == "source.version"


def test_campo_declarado_vazio_conta_como_declarado(tmp_path, monkeypatch):
    """`drop_reasons: {}` quando nada caiu é declaração; ausência da chave não é."""
    monkeypatch.setattr(manifests, "PROJECT_ROOT", tmp_path)
    caminho = _escreve(tmp_path, {"drop_reasons": {}, "dropped": 0})

    resultado = manifests._check(caminho)

    assert "drop_reasons" not in resultado["missing"]
