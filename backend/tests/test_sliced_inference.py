"""Inferência fatiada: grade de tiles, planos FULL/TILED/HYBRID e fusão entre vistas."""

from __future__ import annotations

import numpy as np
import pytest

from app.ml.sliced_inference import (
    SlicingConfig,
    SlicingConfigError,
    finalize_detections,
    merge_view_detections,
    offset_detections,
    plan_views,
    tile_grid,
)


def _dets(*rows: tuple[float, float, float, float, float, int]) -> np.ndarray:
    return np.asarray(rows, dtype=np.float64).reshape(-1, 6)


def test_small_image_has_no_tiles() -> None:
    assert tile_grid(512, 512, tile_size=640, overlap=0.2) == []
    assert tile_grid(640, 640, tile_size=640, overlap=0.2) == []


def test_1080p_grid_covers_frame_with_flush_edges() -> None:
    tiles = tile_grid(1920, 1080, tile_size=640, overlap=0.2)
    assert len(tiles) == 8
    assert {t[0] for t in tiles} == {0, 512, 1024, 1280}
    assert {t[1] for t in tiles} == {0, 440}
    assert all(x1 - x0 == 640 and y1 - y0 == 640 for x0, y0, x1, y1 in tiles)
    assert max(t[2] for t in tiles) == 1920 and max(t[3] for t in tiles) == 1080
    covered = np.zeros((1080, 1920), dtype=bool)
    for x0, y0, x1, y1 in tiles:
        covered[y0:y1, x0:x1] = True
    assert covered.all()


def test_4k_and_720p_grids() -> None:
    assert len(tile_grid(3840, 2160, tile_size=640, overlap=0.2)) == 32
    assert len(tile_grid(1280, 720, tile_size=640, overlap=0.2)) == 6
    assert len(tile_grid(1920, 1080, tile_size=640, overlap=0.3)) == 8


def test_short_side_below_tile_keeps_its_height() -> None:
    tiles = tile_grid(1000, 600, tile_size=640, overlap=0.2)
    assert tiles == [(0, 0, 640, 600), (360, 0, 1000, 600)]


def test_plan_views_per_mode() -> None:
    full = (0, 0, 1920, 1080)
    assert plan_views(1920, 1080, SlicingConfig(mode="full")) == [full]
    tiled = plan_views(1920, 1080, SlicingConfig(mode="tiled"))
    assert full not in tiled and len(tiled) == 8
    hybrid = plan_views(1920, 1080, SlicingConfig(mode="hybrid"))
    assert hybrid[0] == full and len(hybrid) == 9
    # Imagem pequena: nenhum tile, sempre o quadro inteiro.
    assert plan_views(512, 512, SlicingConfig(mode="tiled")) == [(0, 0, 512, 512)]
    assert plan_views(512, 512, SlicingConfig(mode="hybrid")) == [(0, 0, 512, 512)]


def test_config_validation_fails_closed() -> None:
    with pytest.raises(SlicingConfigError):
        SlicingConfig(mode="mosaic")
    with pytest.raises(SlicingConfigError):
        SlicingConfig(overlap=0.9)
    with pytest.raises(SlicingConfigError):
        SlicingConfig(merge="union_everything")
    with pytest.raises(SlicingConfigError):
        SlicingConfig(merge_threshold=0.0)
    with pytest.raises(SlicingConfigError):
        SlicingConfig(tile_size=16)


@pytest.mark.parametrize("tile_size", [640.5, 640.0, "640", True, None])
def test_tile_size_must_be_a_true_integer(tile_size: object) -> None:
    # Igual ao `z.number().int()` do espelho TS: nada de truncar float silenciosamente,
    # senão Python e navegador podem declarar a mesma grade com hashes de perfil diferentes.
    with pytest.raises(SlicingConfigError):
        SlicingConfig(tile_size=tile_size)  # type: ignore[arg-type]
    assert SlicingConfig(tile_size=512).profile_fields()["tile_size"] == 512


@pytest.mark.parametrize(
    "field,value", [("overlap", "0.2"), ("overlap", True), ("merge_threshold", float("nan"))]
)
def test_numeric_fields_refuse_text_bool_and_nan(field: str, value: object) -> None:
    with pytest.raises(SlicingConfigError):
        SlicingConfig(**{field: value})  # type: ignore[arg-type]


def test_profile_fields_identify_the_slicing() -> None:
    config = SlicingConfig(mode="hybrid", overlap=0.25, merge="nmm", merge_threshold=0.6)
    assert config.profile_fields() == {
        "mode": "hybrid",
        "tile_size": 640,
        "overlap": 0.25,
        "merge": "nmm",
        "merge_threshold": 0.6,
    }
    assert SlicingConfig().profile_fields()["mode"] == "full"


def test_offset_moves_view_detections_to_frame_coordinates() -> None:
    moved = offset_detections(_dets((10, 20, 30, 40, 0.5, 1)), (512, 440, 1152, 1080))
    assert moved.tolist() == [[522.0, 460.0, 542.0, 480.0, 0.5, 1.0]]
    assert offset_detections(np.empty((0, 6)), (5, 5, 10, 10)).shape == (0, 6)


def test_single_view_merge_is_identity() -> None:
    dets = _dets((0, 0, 10, 10, 0.9, 0), (0, 0, 10, 10, 0.8, 1))
    for method in ("nms", "nms_ios", "nmm", "wbf"):
        merged = merge_view_detections([dets], method=method, threshold=0.5)
        assert merged.tolist() == dets.tolist()


def test_merge_never_resolves_boxes_of_the_same_view_again() -> None:
    # Duas caixas que o NMS do perfil já manteve na mesma vista continuam as duas,
    # mesmo quando uma contém a outra: a fusão só decide conflitos entre vistas.
    same_view = _dets((100, 100, 300, 200, 0.8, 0), (120, 110, 180, 190, 0.5, 0))
    empty = np.empty((0, 6))
    for method in ("nms", "nms_ios", "nmm", "wbf"):
        merged = merge_view_detections([same_view, empty], method=method, threshold=0.3)
        assert merged.tolist() == same_view.tolist()


def test_nms_merge_removes_tile_duplicates_but_keeps_other_classes() -> None:
    a = _dets((100, 100, 200, 200, 0.6, 2))
    b = _dets((102, 101, 201, 199, 0.7, 2), (100, 100, 200, 200, 0.5, 3))
    merged = merge_view_detections([a, b], method="nms", threshold=0.5)
    assert sorted(merged[:, 5].tolist()) == [2.0, 3.0]
    kept = merged[merged[:, 5] == 2][0]
    assert kept[4] == pytest.approx(0.7)


def test_ios_suppresses_a_cut_box_inside_the_full_frame_box() -> None:
    full = _dets((100, 100, 300, 200, 0.8, 0))
    cut = _dets((100, 100, 190, 200, 0.5, 0))
    # IoU 0,45: NMS por IoU mantém a duplicata cortada; IOS = 1 a remove.
    assert len(merge_view_detections([full, cut], method="nms", threshold=0.5)) == 2
    merged = merge_view_detections([full, cut], method="nms_ios", threshold=0.5)
    assert merged.tolist() == full.tolist()


def test_nmm_rebuilds_an_object_split_between_tiles() -> None:
    left = _dets((100, 100, 220, 200, 0.7, 1))
    right = _dets((180, 100, 300, 200, 0.6, 1))
    full = _dets((110, 100, 290, 200, 0.4, 1))
    merged = merge_view_detections([full, left, right], method="nmm", threshold=0.5)
    assert merged.shape == (1, 6)
    assert merged[0, :4].tolist() == [100.0, 100.0, 300.0, 200.0]
    assert merged[0, 4] == pytest.approx(0.7)
    # Classes diferentes nunca se fundem.
    other = _dets((180, 100, 300, 200, 0.6, 3))
    assert len(merge_view_detections([left, other], method="nmm", threshold=0.5)) == 2


def test_wbf_averages_overlapping_boxes_weighted_by_score() -> None:
    a = _dets((100, 100, 200, 200, 0.75, 0))
    b = _dets((110, 100, 210, 200, 0.25, 0))
    merged = merge_view_detections([a, b], method="wbf", threshold=0.5)
    assert merged.shape == (1, 6)
    assert merged[0, 0] == pytest.approx(102.5)
    assert merged[0, 2] == pytest.approx(202.5)
    assert merged[0, 4] == pytest.approx(0.75)


def test_finalize_orders_by_score_and_caps() -> None:
    dets = _dets(*[(0, 0, 10 + i, 10, i / 10, 0) for i in range(1, 6)])
    final = finalize_detections(dets, max_detections=3)
    assert final[:, 4].tolist() == pytest.approx([0.5, 0.4, 0.3])
