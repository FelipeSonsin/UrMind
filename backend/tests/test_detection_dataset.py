import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.ml.detection_dataset import (
    CANONICAL_CLASSES,
    AuthorizedDetectionDataset,
    DetectionManifestError,
    build_yolox_dataloader,
    load_authorized_manifest,
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def valid(tmp_path: Path) -> tuple[Path, dict, Path]:
    image = tmp_path / "image.png"
    Image.new("RGB", (20, 10), color=(255, 0, 0)).save(image)
    row = {
        "dataset_id": "rdd2022",
        "schema_version": 2,
        "source_version": "2022-crddc",
        "image_path": str(image),
        "image_width": 20,
        "image_height": 10,
        "boxes": [
            {
                "bbox": [1, 2, 9, 8],
                "canonical_class": "URMIND_ROAD_D40",
                "original_class": "D40",
            }
        ],
        "split": "TRAIN",
        "group": "country:India",
        "annotation_fingerprint": "a" * 64,
        "source_fingerprint": sha(image),
        "authorization_status": "TRAIN_AUTHORIZED",
        "human_pending": False,
        "quarantine": False,
        "duplicate_rejected": False,
        "blocked_source": False,
    }
    manifest = tmp_path / "train.jsonl"
    manifest.write_text(json.dumps(row) + "\n")
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"artifacts": [{"path": "train.jsonl", "sha256": sha(manifest)}]})
    )
    return manifest, row, registry


def load(path: Path, registry: Path, split: str = "TRAIN"):
    del registry
    return load_authorized_manifest(path, intended_split=split)


@pytest.fixture(autouse=True)
def project_root(tmp_path, monkeypatch):
    monkeypatch.setattr("app.ml.detection_dataset.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("app.ml.detection_dataset.ARTIFACT_REGISTRY", tmp_path / "registry.json")


def test_valid_train_loads_and_batches(tmp_path):
    manifest, _, registry = valid(tmp_path)
    rows = load(manifest, registry)
    dataset = AuthorizedDetectionDataset(rows, input_size=(20, 20), max_labels=5)
    batches = list(build_yolox_dataloader(dataset, batch_size=1, pin_memory=False))
    assert len(batches) == 1
    images, targets, image_info, image_ids = batches[0]
    assert images.shape == (1, 3, 20, 20)
    assert targets.shape == (1, 5, 5)
    assert targets[0, 0].tolist() == [3, 5, 5, 8, 6]
    assert [part.tolist() for part in image_info] == [[10], [20]]
    assert image_ids.tolist() == [0]


def test_multiple_boxes_share_one_image_sample(tmp_path):
    manifest, row, registry = valid(tmp_path)
    row["boxes"].append(
        {"bbox": [10, 1, 19, 9], "canonical_class": "URMIND_ROAD_D00", "original_class": "D00"}
    )
    manifest.write_text(json.dumps(row) + "\n")
    registry.write_text(
        json.dumps({"artifacts": [{"path": "train.jsonl", "sha256": sha(manifest)}]})
    )
    dataset = AuthorizedDetectionDataset(load(manifest, registry), input_size=(20, 20), max_labels=5)
    assert len(dataset) == 1
    assert dataset[0][1][:2].tolist() == [[3, 5, 5, 8, 6], [0, 14.5, 5, 9, 8]]


def test_authorized_negative_is_one_sample_with_empty_labels(tmp_path):
    manifest, row, registry = valid(tmp_path)
    row["boxes"] = []
    manifest.write_text(json.dumps(row) + "\n")
    registry.write_text(json.dumps({"artifacts": [{"path": "train.jsonl", "sha256": sha(manifest)}]}))

    dataset = AuthorizedDetectionDataset(load(manifest, registry), input_size=(20, 20), max_labels=5)

    assert len(dataset) == 1
    assert dataset[0][1].shape == (5, 5)
    assert not dataset[0][1].any()


def test_duplicate_box_is_rejected(tmp_path):
    manifest, row, registry = valid(tmp_path)
    row["boxes"].append(dict(row["boxes"][0]))
    manifest.write_text(json.dumps(row) + "\n")
    registry.write_text(json.dumps({"artifacts": [{"path": "train.jsonl", "sha256": sha(manifest)}]}))
    with pytest.raises(DetectionManifestError, match="box duplicada"):
        load(manifest, registry)


def test_dataset_slice_preserves_sequence_contract(tmp_path):
    manifest, _, registry = valid(tmp_path)
    dataset = AuthorizedDetectionDataset(load(manifest, registry), input_size=(20, 20))

    sliced = dataset[:1]

    assert len(sliced) == 1
    assert sliced[0][0].shape == (3, 20, 20)


def test_official_preproc_preserves_bgr_without_normalization_and_letterboxes(tmp_path):
    manifest, _, registry = valid(tmp_path)
    image, _, _, _ = AuthorizedDetectionDataset(
        load(manifest, registry), input_size=(20, 20)
    )[0]

    assert image.dtype.name == "float32"
    assert image[:, 0, 0].tolist() == [0, 0, 255]
    assert image[:, 15, 0].tolist() == [114, 114, 114]
    assert image.max() == 255


def test_resize_scales_xyxy_then_converts_to_class_cxcywh(tmp_path):
    manifest, _, registry = valid(tmp_path)
    _, targets, _, _ = AuthorizedDetectionDataset(
        load(manifest, registry), input_size=(40, 40), max_labels=2
    )[0]

    assert targets[0].tolist() == [3, 10, 10, 16, 12]
    assert np.all(targets[0, 1::2] <= 40)
    assert np.all(targets[0, 2::2] <= 40)


def test_class_indices_match_v1_taxonomy_order():
    assert CANONICAL_CLASSES == {
        "URMIND_ROAD_D00": 0,
        "URMIND_ROAD_D10": 1,
        "URMIND_ROAD_D20": 2,
        "URMIND_ROAD_D40": 3,
    }


def test_train_and_validation_compatibility_transforms_are_deterministic(tmp_path):
    manifest, _, registry = valid(tmp_path)
    rows = load(manifest, registry)
    train = AuthorizedDetectionDataset(rows, input_size=(32, 32), mode="train")[0]
    validation_rows = [
        {**rows[0], "split": "VALIDATION", "authorization_status": "VALIDATION_AUTHORIZED"}
    ]
    validation = AuthorizedDetectionDataset(
        validation_rows, input_size=(32, 32), mode="validation"
    )[0]

    assert np.array_equal(train[0], validation[0])
    assert np.array_equal(train[1], validation[1])


def test_transform_mode_rejects_cross_split_rows(tmp_path):
    manifest, _, registry = valid(tmp_path)
    with pytest.raises(DetectionManifestError, match="VALIDATION"):
        AuthorizedDetectionDataset(load(manifest, registry), mode="validation")


def test_mixed_collate_keeps_zero_one_and_multiple_box_targets(tmp_path):
    manifest, row, registry = valid(tmp_path)
    rows = []
    box_sets = [[], row["boxes"], row["boxes"] + [{
        "bbox": [10, 1, 19, 9],
        "canonical_class": "URMIND_ROAD_D00",
        "original_class": "D00",
    }]]
    for index, boxes in enumerate(box_sets):
        image = tmp_path / f"image_{index}.png"
        Image.new("RGB", (20, 10), color=(index, 0, 0)).save(image)
        item = {**row, "image_path": str(image), "source_fingerprint": sha(image), "boxes": boxes}
        rows.append(item)
    manifest.write_text("".join(json.dumps(item) + "\n" for item in rows))
    registry.write_text(json.dumps({"artifacts": [{"path": "train.jsonl", "sha256": sha(manifest)}]}))
    dataset = AuthorizedDetectionDataset(load(manifest, registry), input_size=(32, 32), max_labels=4)

    images, targets, _, ids = next(iter(build_yolox_dataloader(
        dataset, batch_size=3, num_workers=0, pin_memory=False
    )))

    assert images.shape == (3, 3, 32, 32)
    assert targets.shape == (3, 4, 5)
    assert (targets[:, :, 3] > 0).sum(dim=1).tolist() == [0, 1, 2]
    assert ids.tolist() == [0, 1, 2]


def test_max_labels_fails_closed_instead_of_dropping_boxes(tmp_path):
    manifest, row, registry = valid(tmp_path)
    row["boxes"].append(
        {"bbox": [10, 1, 19, 9], "canonical_class": "URMIND_ROAD_D00", "original_class": "D00"}
    )
    manifest.write_text(json.dumps(row) + "\n")
    registry.write_text(json.dumps({"artifacts": [{"path": "train.jsonl", "sha256": sha(manifest)}]}))
    with pytest.raises(DetectionManifestError, match="truncamento"):
        AuthorizedDetectionDataset(load(manifest, registry), max_labels=1)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("boxes", [{"bbox": [1, 2, 9, 8], "canonical_class": None, "original_class": "D40"}]),
        ("boxes", [{"bbox": [1, 2, 21, 8], "canonical_class": "URMIND_ROAD_D40", "original_class": "D40"}]),
        ("blocked_source", True),
        ("duplicate_rejected", True),
        ("human_pending", True),
        ("quarantine", True),
        ("authorization_status", "PENDING"),
    ],
)
def test_fail_closed_rows(tmp_path, field, value):
    manifest, row, registry = valid(tmp_path)
    row[field] = value
    manifest.write_text(json.dumps(row) + "\n")
    registry.write_text(
        json.dumps({"artifacts": [{"path": "train.jsonl", "sha256": sha(manifest)}]})
    )
    with pytest.raises(DetectionManifestError):
        load(manifest, registry)


@pytest.mark.parametrize("split", ["TEST", "EXTERNAL_TEST"])
def test_protected_split_rejected_from_train(tmp_path, split):
    manifest, row, registry = valid(tmp_path)
    row["split"] = split
    manifest.write_text(json.dumps(row) + "\n")
    registry.write_text(
        json.dumps({"artifacts": [{"path": "train.jsonl", "sha256": sha(manifest)}]})
    )
    with pytest.raises(DetectionManifestError):
        load(manifest, registry)


def test_duplicate_image_record_is_rejected(tmp_path):
    manifest, row, registry = valid(tmp_path)
    manifest.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n")
    registry.write_text(json.dumps({"artifacts": [{"path": "train.jsonl", "sha256": sha(manifest)}]}))
    with pytest.raises(DetectionManifestError, match="duplicada"):
        load(manifest, registry)


def test_legacy_per_box_schema_is_rejected(tmp_path):
    manifest, row, registry = valid(tmp_path)
    box = row.pop("boxes")[0]
    row.update(box)
    manifest.write_text(json.dumps(row) + "\n")
    registry.write_text(json.dumps({"artifacts": [{"path": "train.jsonl", "sha256": sha(manifest)}]}))
    with pytest.raises(DetectionManifestError, match="legado"):
        load(manifest, registry)


def test_blocked_dataset_id_is_rejected(tmp_path):
    manifest, row, registry = valid(tmp_path)
    row["dataset_id"] = "rtk_br"
    manifest.write_text(json.dumps(row) + "\n")
    registry.write_text(json.dumps({"artifacts": [{"path": "train.jsonl", "sha256": sha(manifest)}]}))
    with pytest.raises(DetectionManifestError, match="fonte"):
        load(manifest, registry)


def test_stale_manifest_rejected(tmp_path):
    manifest, _, _registry = valid(tmp_path)
    manifest.write_text(manifest.read_text() + "\n")
    with pytest.raises(DetectionManifestError, match="stale"):
        load_authorized_manifest(manifest, intended_split="TRAIN")


def test_stale_image_is_rejected_when_sample_is_accessed(tmp_path):
    manifest, _, registry = valid(tmp_path)
    image = tmp_path / "image.png"
    Image.new("RGB", (20, 10), color="blue").save(image)
    rows = load(manifest, registry)
    with pytest.raises(DetectionManifestError, match="imagem stale"):
        AuthorizedDetectionDataset(rows)[0]


def test_missing_image_rejected(tmp_path):
    manifest, row, registry = valid(tmp_path)
    Path(row["image_path"]).unlink()
    rows = load(manifest, registry)
    with pytest.raises((DetectionManifestError, RuntimeError)):
        AuthorizedDetectionDataset(rows)[0]


def test_image_outside_project_rejected(tmp_path):
    manifest, row, registry = valid(tmp_path)
    row["image_path"] = str(tmp_path.parent / "outside.png")
    manifest.write_text(json.dumps(row) + "\n")
    registry.write_text(
        json.dumps({"artifacts": [{"path": "train.jsonl", "sha256": sha(manifest)}]})
    )
    with pytest.raises(DetectionManifestError, match="fora do projeto"):
        load(manifest, registry)


def test_cloud_only_guard_precedes_open(tmp_path, monkeypatch):
    manifest, _, registry = valid(tmp_path)
    rows = load(manifest, registry)
    opened = False

    def blocked(path):
        raise DetectionManifestError("cloud-only")

    def spy(*args, **kwargs):
        nonlocal opened
        opened = True
        raise AssertionError("open não deveria ocorrer")

    monkeypatch.setattr("app.ml.detection_dataset._require_local_artifact", blocked)
    monkeypatch.setattr(Path, "open", spy)
    with pytest.raises(DetectionManifestError, match="CLOUD_ONLY_SAMPLE"):
        AuthorizedDetectionDataset(rows)[0]
    assert not opened
