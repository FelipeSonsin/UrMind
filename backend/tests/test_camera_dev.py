"""Harness da sprint visual: rótulos, divisão, taxonomia de erros e recusas."""

from __future__ import annotations

import json

import numpy as np
import pytest

from app.ml.camera_dev import (
    BASELINE,
    CacheEntry,
    DevImage,
    DevSetError,
    Profile,
    best_thresholds,
    categorize_mask_detection,
    classify_image_errors,
    data_use,
    frame_quality,
    ird_subset,
    latency_section,
    parse_yolo_labels,
    persist_build_timings,
    profile_detections,
    quality_flags,
    select_system,
)
from app.ml.metrics import Box
from app.ml.sliced_inference import SlicingConfig


def test_yolo_labels_become_normalized_xyxy_and_drop_speed_bumps() -> None:
    truths = parse_yolo_labels("3 0.5 0.5 0.2 0.1\n4 0.1 0.1 0.1 0.1\n\n0 0.05 0.5 0.2 0.2\n")
    assert truths[0] == pytest.approx((3, 0.4, 0.45, 0.6, 0.55))
    # Caixa encostada na borda é recortada a 0..1; SpeedBump (4) não entra.
    assert truths[1] == pytest.approx((0, 0.0, 0.4, 0.15, 0.6))
    assert len(truths) == 2


def test_malformed_labels_are_refused() -> None:
    with pytest.raises(DevSetError):
        parse_yolo_labels("0 0.5 0.5 0.2\n")
    with pytest.raises(DevSetError):
        parse_yolo_labels("0 0.5 0.5 0 0.2\n")


def test_ird_split_keeps_known_near_duplicate_groups_on_one_side() -> None:
    assert ird_subset("Dash_0008") == ird_subset("Dash_0028") == "tune"
    assert ird_subset("Dash_0560") == "tune"
    assert ird_subset("Dash_0561") == "check"
    for group in (("Dash_0604", "Dash_0836", "Dash_0884"), ("Dash_0612", "Dash_0896")):
        assert {ird_subset(name) for name in group} == {"check"}


def _dets(*rows: tuple[float, float, float, float, float, int]) -> np.ndarray:
    return np.asarray(rows, dtype=np.float64).reshape(-1, 6)


def test_error_taxonomy() -> None:
    truths = [
        (0, Box(0, 0, 100, 100)),  # acerto
        (1, Box(200, 0, 300, 100)),  # detectado como outra classe
        (2, Box(400, 0, 500, 100)),  # caixa ruim
        (3, Box(600, 0, 610, 10)),  # nada perto e pequeno
    ]
    detections = _dets(
        (0, 0, 100, 100, 0.9, 0),
        (200, 0, 300, 100, 0.8, 2),
        (400, 0, 440, 100, 0.7, 2),
        (900, 900, 950, 950, 0.6, 1),
    )
    result = classify_image_errors(detections, truths, frame_size=(1920, 1080))
    assert result["tp"] == 1
    assert sorted(item["cause"] for item in result["fn"]) == ["BAD_BOX", "PURE_MISS", "WRONG_CLASS"]
    miss = next(item for item in result["fn"] if item["cause"] == "PURE_MISS")
    assert miss["tags"] == ["SMALL_OBJECT"] and miss["class"] == "URMIND_ROAD_D40"
    assert sorted(item["cause"] for item in result["fp"]) == [
        "BACKGROUND",
        "BAD_BOX",
        "WRONG_CLASS",
    ]
    empty = classify_image_errors(detections[:1], [], frame_size=(1920, 1080))
    assert [item["cause"] for item in empty["fp"]] == ["NEGATIVE_IMAGE"]


def test_mask_categories() -> None:
    mask = np.ones((100, 100), dtype=np.uint8)  # asfalto
    mask[0:20, 0:20] = 4  # faixa
    mask[50:60, 50:90] = 12  # trinca
    mask[80:100, 0:20] = 7  # bueiro
    assert categorize_mask_detection(mask, (0, 0, 20, 20)) == "MARKING_CONFUSION"
    assert categorize_mask_detection(mask, (45, 45, 95, 65)) == "DAMAGE_OVERLAP"
    assert categorize_mask_detection(mask, (0, 80, 20, 100)) == "MANHOLE_CONFUSION"
    assert categorize_mask_detection(mask, (20, 20, 45, 45)) == "ASPHALT_TEXTURE"
    mask[20:45, 20:45] = 2  # paralelepípedo
    assert categorize_mask_detection(mask, (20, 20, 45, 45)) == "PAVED_TEXTURE"
    mask[20:45, 20:45] = 3  # terra
    assert categorize_mask_detection(mask, (20, 20, 45, 45)) == "UNPAVED_TEXTURE"
    mask[:, :] = 0
    assert categorize_mask_detection(mask, (0, 0, 50, 50)) == "OFF_ROAD"


def test_quality_matches_browser_limits() -> None:
    dark = np.full((720, 1280, 3), 20, dtype=np.uint8)
    assert quality_flags(frame_quality(dark)) == ["LOW_LIGHT"]
    flat = np.full((720, 1280, 3), 128, dtype=np.uint8)
    assert quality_flags(frame_quality(flat)) == ["BLUR"]
    rng = np.random.default_rng(1)
    textured = rng.integers(0, 256, size=(720, 1280, 3), dtype=np.uint8)
    assert quality_flags(frame_quality(textured)) == []


def _entry() -> CacheEntry:
    image = DevImage("ird", "Dash_0001", path=None, sha256="x", subset="tune", truths=())  # type: ignore[arg-type]
    boxes = np.asarray([[10, 10, 50, 50]], dtype=np.float32)
    scores = np.asarray([[0.5, 0.0, 0.0, 0.0]], dtype=np.float32)
    meta = {"resolutions": {"720p": {"size": [640, 480], "views": [[0, 0, 640, 480]]}}}
    return CacheEntry(image, meta, {"720p/0/boxes": boxes, "720p/0/scores": scores})


def test_profile_below_cache_floor_or_other_grid_is_refused() -> None:
    entry = _entry()
    assert profile_detections(entry, "720p", BASELINE).shape == (1, 6)
    with pytest.raises(DevSetError):
        profile_detections(entry, "720p", Profile("low", (0.005, 0.2, 0.07, 0.2)))
    with pytest.raises(DevSetError):
        profile_detections(
            entry,
            "720p",
            Profile("grid", BASELINE.thresholds, slicing=SlicingConfig(mode="hybrid", overlap=0.3)),
        )


def _scored(recall: float, f1: float, per_class_f1: float = 0.2) -> dict[str, object]:
    names = ("URMIND_ROAD_D00", "URMIND_ROAD_D10", "URMIND_ROAD_D20", "URMIND_ROAD_D40")
    return {
        "micro_recall": recall,
        "macro_f1": f1,
        "per_class": {name: {"f1": per_class_f1} for name in names},
    }


def _report(candidate: dict[str, object], validation_f1: float, profile: dict) -> dict:
    return {
        "baseline": {"validation": {"macro_f1": 0.28}},
        "thresholds": {"1080p": {"passed_preregistered_f1_rule": True}},
        "evaluated_candidates": {
            "1080p": {
                "evaluated_profile": profile,
                "baseline_check": _scored(0.248, 0.235),
                "candidate_check": candidate,
                "candidate_validation": {"macro_f1": validation_f1},
            }
        },
    }


def test_f1_only_threshold_candidate_is_never_selected() -> None:
    changed = Profile("x", (0.07, 0.2, 0.13, 0.07)).fields()
    selection = select_system(_report(_scored(0.236, 0.250), 0.267, changed))
    assert selection["BEST_SYSTEM"] == "A"
    assert selection["THRESHOLD_CANDIDATE_REJECTED"] is True
    assert selection["runtime_change"] == "NONE" and selection["publishable_candidate"] is None
    assert selection["SELECTED_MODEL"].startswith("d429bde8")
    assert selection["SELECTED_PROFILE"].startswith("2702eb15")
    reasons = " ".join(selection["per_resolution"]["1080p"]["reasons"])
    assert "recall" in reasons and "VALIDATION" in reasons


def test_candidate_must_raise_recall_and_f1_without_hurting_validation() -> None:
    changed = Profile("x", (0.05, 0.2, 0.07, 0.2)).fields()
    selection = select_system(_report(_scored(0.30, 0.26), 0.281, changed))
    assert selection["BEST_SYSTEM"] == "B"
    assert selection["SELECTED_PROFILE"] is None  # perfil novo exige gerador + autorização
    assert selection["runtime_change"] == "PENDING_OWNER_AUTHORIZATION"
    # Mesmo ganho, mas uma classe despenca: não seleciona.
    regressed = _scored(0.30, 0.26)
    regressed["per_class"]["URMIND_ROAD_D20"] = {"f1": 0.1}  # type: ignore[index]
    assert select_system(_report(regressed, 0.281, changed))["BEST_SYSTEM"] == "A"
    # Perfil igual ao baseline nunca é "melhoria".
    assert select_system(_report(_scored(0.3, 0.3), 0.3, BASELINE.fields()))["BEST_SYSTEM"] == "A"


def test_no_threshold_candidate_is_not_reported_as_accepted() -> None:
    # Sem nenhum limiar passando a regra F1 não há candidato: o campo fica nulo, nunca
    # `false` (que se leria como "candidato de limiar aceito").
    report = _report(_scored(0.2, 0.2), 0.28, BASELINE.fields())
    report["thresholds"]["1080p"]["passed_preregistered_f1_rule"] = False
    assert select_system(report)["THRESHOLD_CANDIDATE_REJECTED"] is None


def test_latency_section_summarizes_and_estimates_per_frame() -> None:
    section = latency_section(
        [50.0, 60.0, 70.0], [40.0, 40.0], {"full": (1, 0), "tiled": (0, 8), "hybrid": (1, 8)}
    )
    assert section["session_run_ms"]["full_view"]["p50"] == 60.0
    # TILED não tem vista global: 8 tiles, nunca "global + 7 tiles".
    assert section["per_frame_estimate_ms_p50"] == {
        "full": 60.0,
        "tiled": 8 * 40.0,
        "hybrid": 60.0 + 8 * 40.0,
    }
    with pytest.raises(ValueError):
        latency_section([], [40.0], {"full": (1, 0)})


def test_build_timings_are_persisted_per_process(tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert persist_build_timings(tmp_path, []) is None
    summary = persist_build_timings(tmp_path, [10.0, 20.0, 30.0])
    assert summary is not None and summary["p50"] == 20.0
    [written] = tmp_path.glob("_build_timings.*.json")
    assert json.loads(written.read_text(encoding="utf-8"))["source"] == (
        "MEASURED_DURING_CACHE_BUILD"
    )


def test_data_use_marks_development_only_with_real_review_state() -> None:
    use = data_use()
    assert use["role"] == "DEVELOPMENT_ONLY"
    assert use["official_evaluation"] is False and use["production_evidence"] is False
    assert use["sets"]["ird"]["human_validated_images"] == 0
    assert use["sets"]["ird"]["future_holdout"] is False
    assert use["sets"]["rtk"]["future_independent_brazilian_holdout"] is False
    assert use["sets"]["rtk"]["human_review_recorded"] is False


def test_best_threshold_prefers_f1_then_fewer_alarms() -> None:
    names = ("URMIND_ROAD_D00", "URMIND_ROAD_D10", "URMIND_ROAD_D20", "URMIND_ROAD_D40")
    curve = {
        name: [
            {"threshold": 0.05, "f1": 0.3},
            {"threshold": 0.1, "f1": 0.4},
            {"threshold": 0.2, "f1": 0.4},
            {"threshold": 0.3, "f1": None},
        ]
        for name in names
    }
    assert best_thresholds(curve) == (0.2, 0.2, 0.2, 0.2)
