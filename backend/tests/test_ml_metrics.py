"""Métricas de detecção (§8.5).

Os casos são pequenos e calculados à mão de propósito: uma métrica que ninguém
consegue conferir no papel não serve para decidir promoção de modelo (§9).
"""

from __future__ import annotations

import pytest

from app.ml.metrics import (
    IOU_THRESHOLDS_COCO,
    Box,
    GroundTruth,
    Prediction,
    evaluate,
    iou,
)

D40 = "URMIND_ROAD_D40"
D00 = "URMIND_ROAD_D00"


def gt(image: str, label: str, box: tuple[float, float, float, float]) -> GroundTruth:
    return GroundTruth(image_id=image, label=label, box=Box(*box))


def pred(image: str, label: str, box: tuple[float, float, float, float], score: float):
    return Prediction(image_id=image, label=label, box=Box(*box), score=score)


# ------------------------------------------------------------------------ IoU


def test_iou_de_caixas_identicas_e_um():
    assert iou(Box(0, 0, 10, 10), Box(0, 0, 10, 10)) == 1.0


def test_iou_de_caixas_disjuntas_e_zero():
    assert iou(Box(0, 0, 10, 10), Box(20, 20, 30, 30)) == 0.0


def test_iou_de_sobreposicao_conhecida():
    # Interseção 5x10 = 50; união = 100 + 100 - 50 = 150.
    assert iou(Box(0, 0, 10, 10), Box(5, 0, 15, 10)) == pytest.approx(50 / 150)


def test_caixa_degenerada_e_recusada():
    with pytest.raises(ValueError, match="degenerada"):
        Box(10, 0, 10, 10)


def test_caixas_que_so_se_tocam_nao_tem_interseccao():
    assert iou(Box(0, 0, 10, 10), Box(10, 0, 20, 10)) == 0.0


# --------------------------------------------------------- deteccao perfeita


def test_deteccao_perfeita_zera_os_erros():
    truths = [gt("img1", D40, (0, 0, 10, 10)), gt("img2", D40, (5, 5, 15, 15))]
    predictions = [
        pred("img1", D40, (0, 0, 10, 10), 0.9),
        pred("img2", D40, (5, 5, 15, 15), 0.8),
    ]

    result = evaluate(predictions, truths, labels=[D40])
    metrics = result.per_class[D40]

    assert metrics.true_positives == 2
    assert metrics.false_positives == 0
    assert metrics.false_negatives == 0
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.f1 == 1.0
    assert metrics.ap50 == 1.0
    assert result.map50 == 1.0


def test_deteccao_duplicada_conta_como_falso_positivo():
    """A segunda caixa no mesmo defeito não é um acerto extra."""
    truths = [gt("img1", D40, (0, 0, 10, 10))]
    predictions = [
        pred("img1", D40, (0, 0, 10, 10), 0.9),
        pred("img1", D40, (0, 0, 10, 10), 0.8),
    ]

    metrics = evaluate(predictions, truths, labels=[D40]).per_class[D40]

    assert metrics.true_positives == 1
    assert metrics.false_positives == 1
    assert metrics.precision == 0.5
    assert metrics.recall == 1.0


def test_defeito_nao_detectado_vira_falso_negativo():
    truths = [gt("img1", D40, (0, 0, 10, 10)), gt("img1", D40, (50, 50, 60, 60))]
    predictions = [pred("img1", D40, (0, 0, 10, 10), 0.9)]

    metrics = evaluate(predictions, truths, labels=[D40]).per_class[D40]

    assert metrics.true_positives == 1
    assert metrics.false_negatives == 1
    assert metrics.recall == 0.5


def test_caixa_deslocada_abaixo_do_iou_nao_conta_como_acerto():
    truths = [gt("img1", D40, (0, 0, 10, 10))]
    # IoU ≈ 0,18: sobreposição existe, mas não caracteriza o mesmo defeito.
    predictions = [pred("img1", D40, (7, 0, 17, 10), 0.9)]

    metrics = evaluate(predictions, truths, labels=[D40], iou_threshold=0.5).per_class[D40]

    assert metrics.true_positives == 0
    assert metrics.false_positives == 1
    assert metrics.false_negatives == 1


# ------------------------------------------------------- limiar de confiança


def test_limiar_de_confianca_afeta_precision_mas_nao_ap():
    truths = [gt("img1", D40, (0, 0, 10, 10)), gt("img2", D40, (0, 0, 10, 10))]
    predictions = [
        pred("img1", D40, (0, 0, 10, 10), 0.9),
        pred("img2", D40, (0, 0, 10, 10), 0.3),
    ]

    alto = evaluate(predictions, truths, labels=[D40], score_threshold=0.5)
    baixo = evaluate(predictions, truths, labels=[D40], score_threshold=0.1)

    # O ponto de operação muda: com limiar alto, metade dos defeitos escapa.
    assert alto.per_class[D40].recall == 0.5
    assert baixo.per_class[D40].recall == 1.0
    # O ranqueamento é o mesmo nos dois casos, então o AP não se move.
    assert alto.per_class[D40].ap50 == baixo.per_class[D40].ap50


def test_limiar_usado_fica_registrado_no_resultado():
    result = evaluate([], [gt("img1", D40, (0, 0, 10, 10))], labels=[D40], score_threshold=0.42)

    persistido = result.as_persisted()
    assert persistido["score_threshold"] == 0.42
    assert persistido["iou_threshold"] == 0.5


# --------------------------------------------------- ausência de ground truth


def test_classe_sem_ground_truth_devolve_none_e_nao_zero():
    """Indefinido não é fracasso. Zero seria uma afirmação falsa."""
    truths = [gt("img1", D40, (0, 0, 10, 10))]
    predictions = [pred("img1", D40, (0, 0, 10, 10), 0.9)]

    result = evaluate(predictions, truths, labels=[D40, D00])

    vazia = result.per_class[D00]
    assert vazia.support == 0
    assert vazia.ap50 is None
    assert vazia.ap50_95 is None
    assert vazia.recall is None
    assert vazia.is_measurable is False


def test_map_ignora_classes_sem_ground_truth():
    """Senão o número global dependeria de quantas classes vazias há no recorte."""
    truths = [gt("img1", D40, (0, 0, 10, 10))]
    predictions = [pred("img1", D40, (0, 0, 10, 10), 0.9)]

    result = evaluate(predictions, truths, labels=[D40, D00])

    assert result.map50 == 1.0  # e não 0,5
    assert result.measured_classes == [D40]
    assert result.skipped_classes == [D00]


def test_avaliacao_sem_nenhum_ground_truth_nao_produz_map():
    result = evaluate([pred("img1", D40, (0, 0, 10, 10), 0.9)], [], labels=[D40])

    assert result.map50 is None
    assert result.map50_95 is None
    assert result.per_class[D40].false_positives == 1


def test_modelo_que_nao_detecta_nada_tem_recall_zero():
    truths = [gt("img1", D40, (0, 0, 10, 10))]

    metrics = evaluate([], truths, labels=[D40]).per_class[D40]

    assert metrics.recall == 0.0
    assert metrics.ap50 == 0.0
    assert metrics.false_negatives == 1


# ------------------------------------------------------------------- AP@50:95


def test_ap50_95_e_a_media_sobre_dez_limiares():
    assert len(IOU_THRESHOLDS_COCO) == 10
    assert IOU_THRESHOLDS_COCO[0] == 0.50
    assert IOU_THRESHOLDS_COCO[-1] == 0.95


def test_ap50_95_penaliza_caixa_folgada():
    """Caixa que acerta o objeto mas erra o contorno cai no IoU alto."""
    truths = [gt("img1", D40, (0, 0, 100, 100))]
    justa = [pred("img1", D40, (0, 0, 100, 100), 0.9)]
    folgada = [pred("img1", D40, (0, 0, 130, 130), 0.9)]

    metrics_justa = evaluate(justa, truths, labels=[D40]).per_class[D40]
    metrics_folgada = evaluate(folgada, truths, labels=[D40]).per_class[D40]

    assert metrics_justa.ap50 == metrics_folgada.ap50 == 1.0
    assert metrics_justa.ap50_95 > metrics_folgada.ap50_95


# ----------------------------------------------------------- matriz de confusão


def test_confusao_registra_troca_entre_classes():
    """Trinca longitudinal detectada como transversal é confusão, não FP + FN soltos."""
    truths = [gt("img1", D00, (0, 0, 10, 10))]
    predictions = [pred("img1", D40, (0, 0, 10, 10), 0.9)]

    confusion = evaluate(predictions, truths, labels=[D00, D40]).confusion

    assert confusion[D00][D40] == 1
    assert confusion[D00][D00] == 0


def test_confusao_separa_alarme_falso_de_deteccao_perdida():
    truths = [gt("img1", D40, (0, 0, 10, 10))]
    predictions = [pred("img1", D40, (500, 500, 510, 510), 0.9)]

    confusion = evaluate(predictions, truths, labels=[D40]).confusion

    assert confusion["__background__"][D40] == 1  # detectou onde não havia nada
    assert confusion[D40]["__background__"] == 1  # deixou passar o que havia


def test_confusao_respeita_o_limiar_de_confianca():
    truths = [gt("img1", D40, (0, 0, 10, 10))]
    predictions = [pred("img1", D40, (0, 0, 10, 10), 0.2)]

    acima = evaluate(predictions, truths, labels=[D40], score_threshold=0.1).confusion
    abaixo = evaluate(predictions, truths, labels=[D40], score_threshold=0.5).confusion

    assert acima[D40][D40] == 1
    assert abaixo[D40]["__background__"] == 1


# ------------------------------------------------------------------ persistência


def test_resultado_serializa_para_model_versions():
    truths = [gt("img1", D40, (0, 0, 10, 10))]
    predictions = [pred("img1", D40, (0, 0, 10, 10), 0.9)]

    persistido = evaluate(predictions, truths, labels=[D40]).as_persisted()

    assert persistido["map50"] == 1.0
    assert persistido["per_class"][D40]["support"] == 1
    assert "confusion" in persistido
    assert persistido["skipped_classes_without_ground_truth"] == []


def test_ap_confere_com_calculo_manual():
    """Caso pequeno o suficiente para conferir no papel.

    Três defeitos reais; três detecções em ordem de confiança: acerto, alarme
    falso, acerto. A curva precision-recall passa por (1/3, 1), (1/3, 1/2) e
    (2/3, 2/3); depois da envoltória, a área vale 1/3 · 1 + 1/3 · 2/3 = 0,5556.
    """
    truths = [
        gt("img1", D40, (0, 0, 10, 10)),
        gt("img2", D40, (0, 0, 10, 10)),
        gt("img3", D40, (0, 0, 10, 10)),
    ]
    predictions = [
        pred("img1", D40, (0, 0, 10, 10), 0.9),   # acerto
        pred("img1", D40, (90, 90, 99, 99), 0.8),  # alarme falso
        pred("img2", D40, (0, 0, 10, 10), 0.7),   # acerto
    ]

    metrics = evaluate(predictions, truths, labels=[D40], score_threshold=0.0).per_class[D40]

    assert metrics.ap50 == pytest.approx(0.5556, abs=1e-4)
    assert metrics.precision == pytest.approx(2 / 3)
    assert metrics.recall == pytest.approx(2 / 3)
    assert metrics.f1 == pytest.approx(2 / 3)


def test_ordem_de_confianca_muda_o_ap_com_os_mesmos_acertos():
    """AP mede ranqueamento: acertar primeiro vale mais que acertar depois."""
    truths = [gt("img1", D40, (0, 0, 10, 10)), gt("img2", D40, (0, 0, 10, 10))]

    bom = [
        pred("img1", D40, (0, 0, 10, 10), 0.9),
        pred("img2", D40, (0, 0, 10, 10), 0.8),
        pred("img3", D40, (0, 0, 10, 10), 0.1),
    ]
    ruim = [
        pred("img3", D40, (0, 0, 10, 10), 0.9),
        pred("img1", D40, (0, 0, 10, 10), 0.8),
        pred("img2", D40, (0, 0, 10, 10), 0.7),
    ]

    ap_bom = evaluate(bom, truths, labels=[D40]).per_class[D40].ap50
    ap_ruim = evaluate(ruim, truths, labels=[D40]).per_class[D40].ap50

    assert ap_bom == 1.0
    assert ap_ruim < ap_bom


def test_metricas_globais_sao_micro_precision_e_recall() -> None:
    truths = [gt("img1", D40, (0, 0, 10, 10)), gt("img2", D00, (0, 0, 10, 10))]
    predictions = [
        pred("img1", D40, (0, 0, 10, 10), 0.9),
        pred("negativa", D40, (0, 0, 10, 10), 0.8),
    ]

    result = evaluate(predictions, truths, labels=[D40, D00])

    assert result.precision == 0.5
    assert result.recall == 0.5
    assert result.as_persisted()["precision"] == 0.5
    assert result.as_persisted()["recall"] == 0.5


def test_metricas_globais_sem_prediction_com_gt_tem_precision_e_recall_zero() -> None:
    result = evaluate([], [gt("img1", D40, (0, 0, 10, 10))], labels=[D40])

    assert result.precision == 0.0
    assert result.recall == 0.0
