import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts/datasets"
sys.path.insert(0, str(SCRIPTS))


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


builder = _module("build_detection_manifests")
rtk = _module("audit_rtk")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def core_project_root(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules["_core"], "PROJECT_ROOT", tmp_path)


def rdd_fixture(tmp_path: Path, monkeypatch, *, status="AUTHORIZED_FOR_MODEL_V1"):
    monkeypatch.setattr(builder, "PROJECT_ROOT", tmp_path)
    split_dir = tmp_path / "datasets/splits"
    manifest_dir = tmp_path / "datasets/manifests"
    annotation_dir = tmp_path / "ann"
    split_dir.mkdir(parents=True)
    manifest_dir.mkdir(parents=True)
    annotation_dir.mkdir()
    source = manifest_dir / "rdd2022.json"
    source.write_text(json.dumps({"version": "2022-crddc"}))
    selection = manifest_dir / "selection.jsonl"
    rows = []
    splits = {}
    for role in ("train", "validation", "test"):
        image_rel = f"images/{role}.jpg"
        annotation = annotation_dir / f"{role}.xml"
        annotation.write_text(
            "<annotation><size><width>20</width><height>10</height></size>"
            "<object><name>D40</name><bndbox><xmin>1</xmin><ymin>2</ymin>"
            "<xmax>9</xmax><ymax>8</ymax></bndbox></object></annotation>"
        )
        rows.append(
            {
                "rel_path": image_rel,
                "annotation_path": annotation.relative_to(tmp_path).as_posix(),
                "group": f"country:{role}",
                "sha256": {"train": "a", "validation": "b", "test": "c"}[role] * 64,
                "annotation_sha256": sha(annotation),
            }
        )
        image_paths = [image_rel]
        if role == "train":
            negative_rel = "images/train_negative.jpg"
            negative_annotation = annotation_dir / "train_negative.xml"
            negative_annotation.write_text(
                "<annotation><size><width>20</width><height>10</height></size></annotation>"
            )
            rows.append(
                {
                    "rel_path": negative_rel,
                    "annotation_path": negative_annotation.relative_to(tmp_path).as_posix(),
                    "group": f"country:{role}",
                    "sha256": "d" * 64,
                    "annotation_sha256": sha(negative_annotation),
                }
            )
            image_paths.append(negative_rel)
        list_path = split_dir / f"rdd2022_subset_{role}.txt"
        list_path.write_text("\n".join(image_paths) + "\n")
        splits[role] = {
            "images": len(image_paths),
            "objects": 1,
            "groups": [f"country:{role}"],
            "manifest_sha256": sha(list_path),
        }
    selection.write_text("".join(json.dumps(row) + "\n" for row in rows))
    split = split_dir / "split.json"
    split.write_text(
        json.dumps({"source_manifest_sha256": sha(selection), "splits": splits})
    )
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "status": status,
                "dataset_id": "rdd2022",
                "dataset_version": "2022-crddc",
                "population_id": "crddc2022-official-all-images-train-test",
                "split_manifest_sha256": sha(split),
                "selection_manifest_sha256": sha(selection),
            }
        )
    )
    return split, selection, source, status_path


@pytest.mark.parametrize("status", ["BLOCKED", "proposal_requires_human_review"])
def test_blocked_or_proposal_source_emits_zero(tmp_path, monkeypatch, status):
    args = rdd_fixture(tmp_path, monkeypatch, status=status)
    rows, evidence = builder.materialize_rdd2022(
        split_path=args[0], selection_path=args[1], source_path=args[2], status_path=args[3]
    )
    assert not evidence["authorized"]
    assert all(not values for values in rows.values())


def test_authorized_source_materializes_only_its_roles(tmp_path, monkeypatch):
    args = rdd_fixture(tmp_path, monkeypatch)
    rows, evidence = builder.materialize_rdd2022(
        split_path=args[0], selection_path=args[1], source_path=args[2], status_path=args[3]
    )
    assert evidence["authorized"]
    assert len(rows["TRAIN"]) == 2
    assert len(rows["VALIDATION"]) == len(rows["TEST"]) == 1
    assert rows["EXTERNAL_TEST"] == []
    for role in ("TRAIN", "VALIDATION", "TEST"):
        assert {row["split"] for row in rows[role]} == {role}
        assert all(row["schema_version"] == 2 for row in rows[role])
        assert all(isinstance(row["boxes"], list) for row in rows[role])
        assert rows[role][0]["boxes"][0]["canonical_class"] == "URMIND_ROAD_D40"
    assert rows["TRAIN"][1]["boxes"] == []
    assert evidence["counts"]["TRAIN"] == {
        "total_images": 2,
        "positive_images": 1,
        "negative_images": 1,
        "boxes": 1,
    }


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("annotation", "annotation stale"),
        ("selection", "selection fingerprint"),
        ("split_list", "arquivo de split diverge"),
        ("count", "boxes"),
    ],
)
def test_authorized_source_rejects_stale_or_inconsistent_inputs(
    tmp_path, monkeypatch, mutation, match
):
    split, selection, source, status = rdd_fixture(tmp_path, monkeypatch)
    if mutation == "annotation":
        (tmp_path / "ann/train.xml").write_text("changed")
    elif mutation == "selection":
        doc = json.loads(split.read_text())
        doc["source_manifest_sha256"] = "0" * 64
        split.write_text(json.dumps(doc))
        status_doc = json.loads(status.read_text())
        status_doc["split_manifest_sha256"] = sha(split)
        status.write_text(json.dumps(status_doc))
    elif mutation == "split_list":
        (split.parent / "rdd2022_subset_train.txt").write_text("images/train.jpg\n\n")
    else:
        doc = json.loads(split.read_text())
        doc["splits"]["train"]["objects"] = 2
        split.write_text(json.dumps(doc))
        status_doc = json.loads(status.read_text())
        status_doc["split_manifest_sha256"] = sha(split)
        status.write_text(json.dumps(status_doc))
    with pytest.raises(builder.ManifestBuildError, match=match):
        builder.materialize_rdd2022(
            split_path=split, selection_path=selection, source_path=source, status_path=status
        )


def test_old_authorization_is_not_reused(tmp_path, monkeypatch):
    split, selection, source, status = rdd_fixture(tmp_path, monkeypatch)
    doc = json.loads(status.read_text())
    doc["population_id"] = "other-population"
    status.write_text(json.dumps(doc))
    rows, evidence = builder.materialize_rdd2022(
        split_path=split, selection_path=selection, source_path=source, status_path=status
    )
    assert not evidence["authorized"]
    assert all(not value for value in rows.values())


def test_checked_in_detection_manifests_match_authorized_split():
    root = Path(__file__).resolve().parents[2]
    split = json.loads((root / "datasets/splits/rdd2022_subset_splits.json").read_text(encoding="utf-8"))
    records_by_role = {}
    for role, key in (("TRAIN", "train"), ("VALIDATION", "validation"), ("TEST", "test")):
        path = root / "datasets/manifests" / builder.NAMES[role]
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        records_by_role[role] = records
        expected = split["splits"][key]
        assert len(records) == expected["images"]
        assert len({row["image_path"] for row in records}) == len(records)
        assert sum(len(row["boxes"]) for row in records) == expected["objects"]
        assert sum(bool(row["boxes"]) for row in records) + sum(not row["boxes"] for row in records) == len(records)
    for left, right in (("TRAIN", "VALIDATION"), ("TRAIN", "TEST"), ("VALIDATION", "TEST")):
        assert not ({row["image_path"] for row in records_by_role[left]} & {row["image_path"] for row in records_by_role[right]})
        assert not ({row["source_fingerprint"] for row in records_by_role[left]} & {row["source_fingerprint"] for row in records_by_role[right]})
    external = root / "datasets/manifests" / builder.NAMES["EXTERNAL_TEST"]
    assert external.read_text(encoding="utf-8") == ""


def test_rtk_codes_require_exact_contract(tmp_path):
    path = tmp_path / "codes.txt"
    path.write_text("id,class\n" + "".join(f"{key},{value}\n" for key, value in rtk.CLASSES.items()))
    assert rtk.parse_codes(path) == rtk.CLASSES
    for bad in (
        path.read_text().replace("11,pothole", "11,changed"),
        path.read_text().replace("11,pothole\n", ""),
        path.read_text() + "13,extra\n",
    ):
        path.write_text(bad)
        with pytest.raises(RuntimeError):
            rtk.parse_codes(path)


def test_rtk_metadata_hashes_and_near_pairs(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    names = ("codes.txt", "trainpaths.txt", "valpaths.txt")
    for name in names:
        (raw / name).write_text(name)
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"metadata_sha256": {name: sha(raw / name) for name in names}}))
    assert rtk.validate_metadata_identity(raw, source)
    (raw / "trainpaths.txt").write_text("stale")
    with pytest.raises(RuntimeError):
        rtk.validate_metadata_identity(raw, source)
    pairs = rtk.near_duplicate_pairs(
        [
            {"dhash128": "0", "published_split": "TRAIN", "image_path": "a", "image_sha256": "a"},
            {"dhash128": "3", "published_split": "VALIDATION", "image_path": "b", "image_sha256": "b"},
            {"dhash128": "1f", "published_split": "TRAIN", "image_path": "c", "image_sha256": "c"},
        ]
    )
    assert [(pair["hamming_distance"], pair["cross_split"]) for pair in pairs] == [(2, True), (3, True)]
