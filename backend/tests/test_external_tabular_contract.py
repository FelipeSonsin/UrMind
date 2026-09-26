"""External visual severity contracts with small, explicitly synthetic rows."""

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from app.ml import tabular as t
from app.services.features import (
    SEVERITY_DETECTION_FEATURE_ORDER,
    SEVERITY_DETECTION_SCHEMA_VERSION,
    severity_detection_schema_sha256,
)


def synthetic_document() -> dict:
    rows = []
    for index in range(200):
        source_type = "alligator crack" if index < 100 else "pothole"
        high = index % 100 >= 50
        subset = "ws_v1" if index % 2 == 0 else "ws_v2"
        image_id = f"Attain_SMP_WS_{'v1' if subset == 'ws_v1' else 'v2'}_{index:06d}.jpg"
        features = {
            "detected_class_D20": float(source_type == "alligator crack"),
            "detected_class_D40": float(source_type == "pothole"),
            "bbox_width_ratio": 0.2,
            "bbox_height_ratio": 0.3,
            "bbox_area_ratio": 0.06,
            "bbox_center_y_ratio": 0.5,
            "crop_brightness_mean": 0.4,
            "crop_brightness_std": 0.1,
            "crop_dark_fraction": 0.2,
            "crop_edge_mean": 0.1,
            "crop_color_range_mean": 0.1,
        }
        assert tuple(features) == SEVERITY_DETECTION_FEATURE_ORDER
        rows.append(
            {
                "row_id": f"{image_id}#0",
                "source_sample_id": image_id,
                "image_id": image_id,
                "image_file_id": f"image-file-{index}",
                "annotation_file_id": f"annotation-file-{index}",
                "annotation_index": 0,
                "image_sha256": f"{index + 1:064x}",
                "annotation_sha256": f"{index + 1000:064x}",
                "source_subset": subset,
                "source_type": source_type,
                "label_original": f"{source_type} - {'High' if high else 'Low'}",
                "target": int(high),
                "group_id": f"{index + 1:064x}",
                "scene_group_status": "UNKNOWN",
                "box_origin": "human_annotation_proxy",
                "bbox": {"x": 0.3, "y": 0.35, "width": 0.2, "height": 0.3},
                "image_width": 640,
                "image_height": 640,
                "vision_model_version": None,
                "vision_checkpoint_hash": None,
                "vision_feature_version": SEVERITY_DETECTION_SCHEMA_VERSION,
                "yolox_exposure": "YOLOX_EXPOSURE_UNKNOWN",
                "captured_at": None,
                "label_created_at": None,
                "feature_computed_at": "2026-09-25T12:00:00+00:00",
                "missingness": {
                    "capture_time_unknown": True,
                    "label_time_unknown": True,
                    "context_unavailable": True,
                    "detector_confidence_unavailable": True,
                },
                "features": features,
            }
        )
    document = {
        "source_kind": "EXTERNAL_ANNOTATION",
        "source_dataset": "Attain",
        "source_version": t.EXTERNAL_SEVERITY_SOURCE_VERSION,
        "source_url": "https://data.mendeley.com/datasets/nykrzdm74f/1",
        "license_reference": "CC BY 4.0; https://data.mendeley.com/datasets/nykrzdm74f/1",
        "target": t.EXTERNAL_SEVERITY_TARGET,
        "target_version": t.EXTERNAL_SEVERITY_TARGET_VERSION,
        "feature_schema_version": SEVERITY_DETECTION_SCHEMA_VERSION,
        "feature_schema_sha256": severity_detection_schema_sha256(),
        "label_schema_sha256": t.external_severity_label_schema_sha256(),
        "feature_order": list(SEVERITY_DETECTION_FEATURE_ORDER),
        "annotation_manifest_sha256": "a" * 64,
        "image_verification_sha256": "b" * 64,
        "rows": rows,
    }
    digest = t.external_annotation_content_sha256(document)
    document["dataset"] = {
        "name": f"attain-pavement-visual-severity-{digest[:16]}",
        "content_sha256": digest,
        "status": "EXPERIMENTAL_DRAFT",
    }
    return document


def synthetic_similarity_audit(document: dict) -> dict:
    rows = {row["image_id"]: row for row in document["rows"]}
    images = [
        {
            "image_id": image_id,
            "source_subset": row["source_subset"],
            "image_sha256": row["image_sha256"],
            "dhash64": hashlib.sha256(image_id.encode()).hexdigest()[:16],
        }
        for image_id, row in sorted(rows.items())
    ]
    first = [row for row in images if row["source_subset"] == "ws_v1"]
    second = [row for row in images if row["source_subset"] == "ws_v2"]
    first[0]["dhash64"] = "0" * 16
    second[0]["dhash64"] = "0" * 16
    pairs = [
        {
            "ws_v1_image_id": left["image_id"],
            "ws_v2_image_id": right["image_id"],
            "distance": (int(left["dhash64"], 16) ^ int(right["dhash64"], 16)).bit_count(),
        }
        for left in first
        for right in second
        if (int(left["dhash64"], 16) ^ int(right["dhash64"], 16)).bit_count()
        <= t.EXTERNAL_SIMILARITY_MAX_DISTANCE
    ]
    audit = {
        "dataset_sha256": document["dataset"]["content_sha256"],
        "dataset_version": document["dataset"]["name"],
        "annotation_manifest_sha256": document["annotation_manifest_sha256"],
        "image_verification_sha256": document["image_verification_sha256"],
        "method": t.EXTERNAL_SIMILARITY_METHOD,
        "pillow_version": "synthetic-test",
        "max_distance": t.EXTERNAL_SIMILARITY_MAX_DISTANCE,
        "images": images,
        "pairs": pairs,
    }
    audit["content_sha256"] = t.external_similarity_audit_content_sha256(audit)
    return audit


class ExternalTabularContractTests(unittest.TestCase):
    def test_domain_shift_report_uses_features_and_image_shapes_only(self) -> None:
        document = synthetic_document()
        for row in document["rows"]:
            if row["source_subset"] == "ws_v2":
                row["image_width"], row["image_height"] = 1479, 508
        digest = t.external_annotation_content_sha256(document)
        document["dataset"]["content_sha256"] = digest
        document["dataset"]["name"] = f"attain-pavement-visual-severity-{digest[:16]}"
        split = t.generate_external_split_plan(document, allow_historical_split=True)
        t.validate_external_split_plan(split, document)
        report = t._external_domain_shift_summary(document, split)
        self.assertEqual(report["image_dimension_rows"]["TRAIN"], {"1479x508": 100})
        self.assertEqual(report["image_dimension_rows"]["VALIDATION"], {"640x640": 100})
        self.assertEqual(report["image_dimension_total_variation"], 1.0)
        self.assertEqual(
            report["source_annotation_protocol_rows"]["TRAIN"],
            {"PASCAL_VOC_XML_PIXEL_BOX": 100},
        )
        self.assertEqual(
            report["source_annotation_protocol_rows"]["VALIDATION"],
            {"YOLO_TXT_NORMALIZED_BOX_OR_POLYGON": 100},
        )
        self.assertEqual(report["annotation_protocol_total_variation"], 1.0)
        self.assertAlmostEqual(report["feature_means"]["TRAIN"]["crop_edge_mean"], 0.1)
        self.assertFalse(report["labels_accessed"])
        self.assertFalse(report["causal_explanation"])
        without_labels = copy.deepcopy(document)
        for row in without_labels["rows"]:
            row.pop("target")
            row.pop("label_original")
        self.assertEqual(report, t._external_domain_shift_summary(without_labels, split))

    def test_annotation_protocol_difference_is_visible_even_at_equal_image_size(self) -> None:
        document = synthetic_document()
        split = t.generate_external_split_plan(document, allow_historical_split=True)
        report = t._external_domain_shift_summary(document, split)
        self.assertEqual(report["image_dimension_total_variation"], 0.0)
        self.assertEqual(report["annotation_protocol_total_variation"], 1.0)

    def test_similarity_boundary_reports_candidates_beyond_quarantine(self) -> None:
        split = {
            "rows": [
                {"row_id": "ws_v1_a.jpg#0", "role": "VALIDATION"},
                {"row_id": "ws_v2_b.jpg#0", "role": "TRAIN"},
                {"row_id": "ws_v2_c.jpg#0", "role": "EXCLUDED"},
            ]
        }
        audit = {
            "max_distance": 12,
            "images": [
                {"image_id": "ws_v1_a.jpg", "dhash64": "0000000000000000"},
                {"image_id": "ws_v2_b.jpg", "dhash64": f"{(1 << 13) - 1:016x}"},
                {"image_id": "ws_v2_c.jpg", "dhash64": "0000000000000000"},
            ],
        }
        report = t._external_similarity_boundary_summary(split, audit)
        self.assertEqual(report["nearest_cross_role_distance"], 13)
        self.assertEqual(report["pairs_at_cutoff_plus_one"], 1)
        self.assertFalse(report["scene_independence_verified"])

    def test_direct_fit_rejects_historical_split_before_model_import(self) -> None:
        document = synthetic_document()
        historical = t.generate_external_split_plan(document, allow_historical_split=True)
        root = Path(t.__file__).resolve().parents[3]
        config = json.loads(
            (root / "datasets/metadata/attain_severity_xgboost_draft_config.json").read_text(
                encoding="utf-8"
            )
        )
        before = {name: name in sys.modules for name in ("psutil", "xgboost")}
        bundle = t.VerifiedTrainingBundle(document, historical, config, {}, "synthetic")
        with self.assertRaisesRegex(t.TabularExportError, "similarity audit required before fit"):
            t.train_xgboost(bundle, output_root=root / "datasets/processed/tabular")
        audited_bundle = t.VerifiedTrainingBundle(
            document, historical, config, {}, "synthetic", synthetic_similarity_audit(document)
        )
        with self.assertRaisesRegex(t.TabularExportError, "matching similarity audit"):
            t.train_xgboost(audited_bundle, output_root=root / "datasets/processed/tabular")
        valid_split = t.generate_external_split_plan(
            document, similarity_audit=audited_bundle.similarity_audit
        )
        grouped_bundle = t.VerifiedTrainingBundle(
            document, valid_split, config, {}, "synthetic", audited_bundle.similarity_audit
        )
        with self.assertRaisesRegex(
            t.TabularExportError, "BLOCKED_GROUP_EVIDENCE.*LABEL_RUBRIC_UNVERIFIED"
        ):
            t.train_xgboost(grouped_bundle, output_root=root / "datasets/processed/tabular")
        self.assertEqual(before, {name: name in sys.modules for name in before})

    def test_label_rubric_blocks_fit_independently_of_scene_groups(self) -> None:
        document = synthetic_document()
        audit = synthetic_similarity_audit(document)
        split = t.generate_external_split_plan(document, similarity_audit=audit)
        root = Path(t.__file__).resolve().parents[3]
        config = json.loads(
            (root / "datasets/metadata/attain_severity_xgboost_draft_config.json").read_text(
                encoding="utf-8"
            )
        )
        bundle = t.VerifiedTrainingBundle(document, split, config, {}, "synthetic", audit)
        with (
            patch.object(t, "validate_external_split_plan", return_value={"scene_group_overlap": 0}),
            patch.dict(sys.modules, {"psutil": None}),
            self.assertRaisesRegex(t.TabularExportError, "LABEL_RUBRIC_UNVERIFIED"),
        ):
            t.train_xgboost(bundle, output_root=root / "datasets/processed/tabular")

    def test_cross_subset_near_matches_are_quarantined(self) -> None:
        document = synthetic_document()
        audit = synthetic_similarity_audit(document)
        checked = t.validate_external_similarity_audit(audit, document)
        self.assertGreater(checked["pair_count"], 0)
        with self.assertRaisesRegex(t.TabularExportError, "required for new split"):
            t.generate_external_split_plan(document)
        old_split = t.generate_external_split_plan(document, allow_historical_split=True)
        with self.assertRaisesRegex(t.TabularExportError, "matching similarity audit"):
            t.validate_external_split_plan(old_split, document, similarity_audit=audit)
        split = t.generate_external_split_plan(document, similarity_audit=audit)
        roles = {row["row_id"]: row["role"] for row in split["rows"]}
        candidates = checked["candidate_image_ids"]
        self.assertTrue(candidates)
        self.assertTrue(all(
            roles[row["row_id"]] == "EXCLUDED"
            for row in document["rows"]
            if row["image_id"] in candidates
        ))
        self.assertEqual(
            t.validate_external_split_plan(split, document, similarity_audit=audit)[
                "near_duplicate_cross_role_candidates"
            ],
            0,
        )
        root = Path(t.__file__).resolve().parents[3]
        config = json.loads(
            (root / "datasets/metadata/attain_severity_xgboost_draft_config.json").read_text(
                encoding="utf-8"
            )
        )
        preflight = t.preflight_external_xgboost(
            document, split, config, similarity_audit=audit
        )
        self.assertEqual(preflight["status"], "BLOCKED_GROUP_EVIDENCE")
        self.assertEqual(
            preflight["label_mapping_status"], "SYNTACTIC_ONLY_RUBRIC_UNVERIFIED"
        )
        self.assertEqual(preflight["similarity_boundary"]["status"], "DESCRIPTIVE_ONLY")
        self.assertFalse(preflight["similarity_boundary"]["scene_independence_verified"])
        forged = copy.deepcopy(split)
        excluded = next(row for row in forged["rows"] if row["role"] == "EXCLUDED")
        excluded["role"] = "VALIDATION"
        forged["content_sha256"] = t.external_split_content_sha256(forged)
        with self.assertRaisesRegex(t.TabularExportError, "role or row fingerprint"):
            t.validate_external_split_plan(forged, document, similarity_audit=audit)
        missing_pair = copy.deepcopy(audit)
        missing_pair["pairs"] = []
        missing_pair["content_sha256"] = t.external_similarity_audit_content_sha256(missing_pair)
        with self.assertRaisesRegex(t.TabularExportError, "candidate pairs incomplete"):
            t.validate_external_similarity_audit(missing_pair, document)
        wrong_source = copy.deepcopy(audit)
        wrong_source["images"][0]["image_sha256"] = "f" * 64
        wrong_source["content_sha256"] = t.external_similarity_audit_content_sha256(wrong_source)
        with self.assertRaisesRegex(t.TabularExportError, "image fingerprint mismatch"):
            t.validate_external_similarity_audit(wrong_source, document)

    def test_class_prior_baseline_learns_only_from_train(self) -> None:
        document = synthetic_document()
        split = t.generate_external_split_plan(document, allow_historical_split=True)
        roles = {row["row_id"]: row["role"] for row in split["rows"]}
        train = [row for row in document["rows"] if roles[row["row_id"]] == "TRAIN"]
        validation = [row for row in document["rows"] if roles[row["row_id"]] == "VALIDATION"]
        baseline = t.external_severity_class_prior_baseline(train, validation)
        self.assertEqual(baseline["train_class_rates"], {"alligator crack": 0.5, "pothole": 0.5})
        forged_validation = copy.deepcopy(validation)
        forged_validation[0]["target"] = 1 - forged_validation[0]["target"]
        changed = t.external_severity_class_prior_baseline(train, forged_validation)
        self.assertEqual(changed["train_class_rates"], baseline["train_class_rates"])
        with self.assertRaisesRegex(t.TabularExportError, "both labels"):
            t.external_severity_class_prior_baseline(
                [row for row in train if row["target"] == 0], validation
            )

    def test_external_config_cannot_train_review_or_use_gpu(self) -> None:
        path = (
            Path(__file__).resolve().parents[2]
            / "datasets/metadata/attain_severity_xgboost_draft_config.json"
        )
        config = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(t.validate_external_training_config(config)["fit_called"], False)
        for key, value in (("target", "review_confirmed"), ("device", "cuda"), ("n_jobs", 2)):
            with self.subTest(key=key):
                forged = {**config, key: value}
                with self.assertRaises(t.TabularExportError):
                    t.validate_external_training_config(forged)
        weighted = {
            **config,
            "class_weight_mode": "balanced_from_train",
            "eval_metric": "aucpr",
            "metric_direction": "maximize",
        }
        self.assertEqual(t.validate_external_training_config(weighted)["fit_called"], False)
        with self.assertRaisesRegex(t.TabularExportError, "metric/direction"):
            t.validate_external_training_config({**weighted, "metric_direction": "minimize"})
        with self.assertRaisesRegex(t.TabularExportError, "class weighting"):
            t.validate_external_training_config({**weighted, "class_weight_mode": "forged"})

    def test_detector_box_needs_lineage_before_model_import(self) -> None:
        with self.assertRaisesRegex(t.TabularExportError, "visual lineage"):
            t.predict_visual_severity_experimental(
                Path("no-model.json"),
                Path("no-metadata.json"),
                None,
                "URMIND_ROAD_D40",
                {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
                box_origin="persisted_detector",
            )

    def test_valid_synthetic_shape_keeps_targets_separate(self) -> None:
        checked = t.validate_external_annotation_export(synthetic_document())
        self.assertEqual(checked["rows"], 200)
        self.assertEqual(checked["class_counts"]["D20:HIGH"], 50)
        self.assertEqual(checked["class_counts"]["D40:LOW"], 50)
        self.assertFalse(checked["scientific_validation"])

    def test_rejects_target_leakage_and_forged_lineage(self) -> None:
        cases = (
            ("event_id", "invented-event"),
            ("yolox_exposure", "YOLOX_NOT_EXPOSED_VERIFIED"),
            ("target", 1),
            ("captured_at", "2020-01-01T00:00:00+00:00"),
        )
        for key, value in cases:
            with self.subTest(key=key):
                document = synthetic_document()
                document["rows"][0][key] = value
                with self.assertRaises(t.TabularExportError):
                    t.validate_external_annotation_export(document)
        document = copy.deepcopy(synthetic_document())
        document["rows"][0]["features"] = dict(
            reversed(list(document["rows"][0]["features"].items()))
        )
        with self.assertRaisesRegex(t.TabularExportError, "feature order"):
            t.validate_external_annotation_export(document)

    def test_split_binds_rows_and_image_hashes(self) -> None:
        document = synthetic_document()
        split = t.generate_external_split_plan(document, allow_historical_split=True)
        checked = t.validate_external_split_plan(split, document)
        self.assertEqual(checked["counts"], {"TRAIN": 100, "VALIDATION": 100})
        self.assertEqual(checked["exact_image_overlap"], 0)
        self.assertEqual(checked["scene_group_overlap"], "NOT_VERIFIED")
        forged = copy.deepcopy(split)
        forged["rows"][0]["role"] = "TRAIN"
        forged["content_sha256"] = t.external_split_content_sha256(forged)
        with self.assertRaisesRegex(t.TabularExportError, "role or row fingerprint"):
            t.validate_external_split_plan(forged, document)
        document["rows"][1]["image_sha256"] = document["rows"][0]["image_sha256"]
        document["rows"][1]["group_id"] = document["rows"][0]["image_sha256"]
        digest = t.external_annotation_content_sha256(document)
        document["dataset"]["name"] = f"attain-pavement-visual-severity-{digest[:16]}"
        document["dataset"]["content_sha256"] = digest
        with self.assertRaisesRegex(t.TabularExportError, "crosses TRAIN/VALIDATION"):
            t.generate_external_split_plan(document, allow_historical_split=True)

    def test_split_rejects_missing_high_for_one_damage_type_in_train(self) -> None:
        document = synthetic_document()
        for index, original in enumerate(copy.deepcopy(document["rows"]), start=200):
            image_id = original["image_id"].replace(
                original["image_id"][-10:-4], f"{index:06d}"
            )
            original.update(
                row_id=f"{image_id}#0",
                source_sample_id=image_id,
                image_id=image_id,
                image_file_id=f"image-file-{index}",
                annotation_file_id=f"annotation-file-{index}",
                image_sha256=f"{index + 1:064x}",
                annotation_sha256=f"{index + 1000:064x}",
                group_id=f"{index + 1:064x}",
            )
            document["rows"].append(original)
        for row in document["rows"]:
            if row["source_subset"] == "ws_v2" and row["source_type"] == "pothole" and row["target"] == 1:
                row["target"] = 0
                row["label_original"] = "pothole - Low"
        digest = t.external_annotation_content_sha256(document)
        document["dataset"]["name"] = f"attain-pavement-visual-severity-{digest[:16]}"
        document["dataset"]["content_sha256"] = digest
        with self.assertRaisesRegex(t.TabularExportError, "per-damage class support"):
            t.generate_external_split_plan(document, allow_historical_split=True)

    def test_external_validation_uses_bound_membership_and_fingerprints(self) -> None:
        root = Path(__file__).resolve().parents[2]
        temporary_root = root / "datasets/processed/tabular"
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.assertTrue(temporary_root.resolve().is_relative_to(root.resolve()))
        with tempfile.TemporaryDirectory(dir=temporary_root) as directory:
            folder = Path(directory)

            def write(name: str, value: dict) -> Path:
                path = folder / name
                path.write_text(json.dumps(value) + "\n", encoding="utf-8")
                return path

            def sha(path: Path) -> str:
                return hashlib.sha256(path.read_bytes()).hexdigest()

            document = synthetic_document()
            source_rows = [
                {
                    "image_id": row["image_id"],
                    "subset": row["source_subset"],
                    "image_file_id": row["image_file_id"],
                    "annotation_file_id": row["annotation_file_id"],
                    "image_sha256_official": row["image_sha256"],
                    "annotation_sha256": row["annotation_sha256"],
                }
                for row in document["rows"]
            ]
            source_rows.extend(
                {"image_id": f"unused-{index}.jpg"} for index in range(1456)
            )
            manifest_path = write(
                "source.json",
                {
                    "source_version": t.EXTERNAL_SEVERITY_SOURCE_VERSION,
                    "license": "CC BY 4.0",
                    "rows": source_rows,
                },
            )
            image_path = write(
                "images.json",
                {
                    "source_version": t.EXTERNAL_SEVERITY_SOURCE_VERSION,
                    "verified_images": 1656,
                    "annotation_manifest_sha256": sha(manifest_path),
                },
            )
            document["annotation_manifest_sha256"] = sha(manifest_path)
            document["image_verification_sha256"] = sha(image_path)
            digest = t.external_annotation_content_sha256(document)
            document["dataset"] = {
                "name": f"attain-pavement-visual-severity-{digest[:16]}",
                "content_sha256": digest,
                "status": "EXPERIMENTAL_DRAFT",
            }
            audit = synthetic_similarity_audit(document)
            split = t.generate_external_split_plan(document, similarity_audit=audit)
            dataset_path = write("dataset.json", document)
            split_path = write("split.json", split)
            audit_path = write("similarity.json", audit)
            config_path = root / "datasets/metadata/attain_severity_xgboost_draft_config.json"
            hashes = {
                "dataset": sha(dataset_path),
                "split": sha(split_path),
                "config": sha(config_path),
                "source_manifest": sha(manifest_path),
                "image_verification": sha(image_path),
                "similarity_audit": sha(audit_path),
            }
            approval_path = write(
                "approval.json",
                {
                    "status": "APPROVED_EXPERIMENTAL_TRAINING",
                    "approved_by": "synthetic test",
                    "authorization_source": "synthetic test fixture",
                    "approved_at": datetime.now(UTC).isoformat(),
                    "dataset_artifact_sha256": hashes["dataset"],
                    "split_artifact_sha256": hashes["split"],
                    "config_artifact_sha256": hashes["config"],
                    "source_manifest_sha256": hashes["source_manifest"],
                    "image_verification_sha256": hashes["image_verification"],
                    "similarity_audit_sha256": hashes["similarity_audit"],
                    "dataset_sha256": digest,
                    "dataset_version": document["dataset"]["name"],
                    "split_sha256": split["content_sha256"],
                    "feature_schema_sha256": t.severity_detection_schema_sha256(),
                    "label_schema_sha256": t.external_severity_label_schema_sha256(),
                    "target": t.EXTERNAL_SEVERITY_TARGET,
                    "target_version": t.EXTERNAL_SEVERITY_TARGET_VERSION,
                    "scope": "experimental_visual_severity_only",
                    "scientific_validation_approved": False,
                    "model_promotion_approved": False,
                },
            )
            model_path = folder / "synthetic-model.json"
            model_path.write_text("{}\n", encoding="utf-8")
            role = {row["row_id"]: row["role"] for row in split["rows"]}
            validation = [row for row in document["rows"] if role[row["row_id"]] == "VALIDATION"]
            predictions = {
                "role": "VALIDATION",
                "target": t.EXTERNAL_SEVERITY_TARGET,
                "target_version": t.EXTERNAL_SEVERITY_TARGET_VERSION,
                "dataset_version": document["dataset"]["name"],
                "dataset_sha256": digest,
                "split_sha256": hashes["split"],
                "model_version": "synthetic-run",
                "model_sha256": sha(model_path),
                "feature_schema_sha256": t.severity_detection_schema_sha256(),
                "rows": [
                    {
                        "row_id": row["row_id"],
                        "image_sha256": row["image_sha256"],
                        "annotation_sha256": row["annotation_sha256"],
                        "probability": 0.1,
                    }
                    for row in validation
                ],
            }
            prediction_path = write("predictions.json", predictions)
            metadata = {
                "status": "EXPERIMENTAL_CANDIDATE",
                "run_id": "synthetic-run",
                "model_version": "synthetic-run",
                "model_promoted": False,
                "model_sha256": sha(model_path),
                "dataset_sha256": digest,
                "dataset_version": document["dataset"]["name"],
                "split_sha256": hashes["split"],
                "feature_schema_sha256": t.severity_detection_schema_sha256(),
                "label_schema_sha256": t.external_severity_label_schema_sha256(),
                "target": t.EXTERNAL_SEVERITY_TARGET,
                "target_version": t.EXTERNAL_SEVERITY_TARGET_VERSION,
                "source_kind": "EXTERNAL_ANNOTATION",
                "scientific_validation": False,
                "scene_group_status": "UNKNOWN",
                "combined_model_evaluation": "BLOCKED_EXPOSURE",
                "validation_prediction_sha256": sha(prediction_path),
                "artifact_sha256": {**hashes, "approval": sha(approval_path)},
                "feature_order": list(SEVERITY_DETECTION_FEATURE_ORDER),
                "validation_metrics": t.validation_metrics(
                    [row["target"] for row in validation], [0.1] * len(validation)
                ),
            }
            metadata_path = write("run.json", metadata)
            paths = (
                prediction_path,
                dataset_path,
                split_path,
                config_path,
                approval_path,
                manifest_path,
                image_path,
                model_path,
                metadata_path,
            )
            with self.assertRaisesRegex(t.TabularExportError, "similarity audit required"):
                t.evaluate_external_validation_artifacts(*paths)
            def evaluate() -> dict:
                return t.evaluate_external_validation_artifacts(
                    *paths, similarity_audit_path=audit_path
                )

            result = evaluate()
            self.assertEqual(result["status"], "INTERNAL_VALIDATION_ONLY")
            self.assertEqual(
                result["label_mapping_status"], "SYNTACTIC_ONLY_RUBRIC_UNVERIFIED"
            )
            self.assertEqual(result["rows"], len(validation))
            self.assertFalse(result["prediction_generation_verified"])
            self.assertEqual(result["selection_gate"], "FAILS_CLASS_PRIOR_BASELINE")
            self.assertEqual(result["similarity_boundary"]["status"], "DESCRIPTIVE_ONLY")
            metadata["model_version"] = "forged-run"
            write("run.json", metadata)
            with self.assertRaisesRegex(t.TabularExportError, "run/version"):
                evaluate()
            metadata["model_version"] = "synthetic-run"
            write("run.json", metadata)
            metadata["model_promoted"] = True
            write("run.json", metadata)
            with self.assertRaisesRegex(t.TabularExportError, "candidate status"):
                evaluate()
            metadata["model_promoted"] = False
            write("run.json", metadata)
            original_approval = approval_path.read_text(encoding="utf-8")
            stale_approval = json.loads(original_approval)
            stale_approval["dataset_artifact_sha256"] = "0" * 64
            write("approval.json", stale_approval)
            with self.assertRaisesRegex(t.TabularExportError, "authorization does not bind"):
                evaluate()
            approval_path.write_text(original_approval, encoding="utf-8")
            predictions["rows"][0]["row_id"] = document["rows"][1]["row_id"]
            write("predictions.json", predictions)
            metadata["validation_prediction_sha256"] = sha(prediction_path)
            write("run.json", metadata)
            with self.assertRaisesRegex(t.TabularExportError, "outside proven VALIDATION"):
                evaluate()


if __name__ == "__main__":
    unittest.main()
