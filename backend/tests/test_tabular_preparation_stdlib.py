"""Small synthetic contract tests without pytest, database, or model imports."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.ml import tabular as t

NOW = datetime(2026, 1, 1, tzinfo=UTC)
CONFIG = {
    "target": "review_confirmed",
    "device": "cpu",
    "tree_method": "hist",
    "n_jobs": 1,
    "nthread": 1,
    "parallel_search": False,
    "include_holdouts": False,
    "max_wall_time_seconds": 36000,
    "min_start_available_mb": 384,
    "critical_available_mb": 256,
    "monitor_every_iterations": 10,
    "max_depth": 4,
    "max_bin": 64,
    "seed": 17,
    "n_estimators": 40,
    "early_stopping_rounds": 5,
    "eval_metric": "logloss",
    "metric_direction": "minimize",
    "target_version": "urmind-review-v1",
    "feature_schema_sha256": t.feature_schema_sha256(),
}


def snapshot(*, history: object = (), context_available: datetime | None = None) -> dict:
    return {
        "feature_schema_version": t.PINNED_FEATURE_SCHEMA,
        "event": {"event_id": "event-1", "capture_id": "capture-1", "occurred_at": NOW.isoformat()},
        "visual": {
            "detection_count": 1,
            "detection_classes": ["URMIND_ROAD_D40"],
            "detection_confidences": [0.89],
            "bounding_boxes": [{"x": 0.1, "y": 0.2, "width": 0.25, "height": 0.4}],
            "model_version_ids": ["model-old"],
            "preprocessing_version": "model-contract-sha256:" + "a" * 64,
            "postprocessing_version": "model-contract-sha256:" + "a" * 64,
            "checkpoint_sha256": "b" * 64,
            "class_order": ["URMIND_ROAD_D40"],
            "feature_version": t.VISUAL_LINEAGE_VERSION,
        },
        "location": {"road_segment_id": "segment-1", "accuracy_m": None, "snap_distance_m": None},
        "context": {"rain_mm_24h": 4.0},
        "history": {
            "previous_events_same_segment": 0,
            "recent_events_same_segment_30d": 0,
            "order_status": "serialized_commit_order",
            "coverage_status": "VERIFIED",
            "window_end_at": NOW.isoformat(),
            "available_at": NOW.isoformat(),
            "last_event_at": None,
        },
        "missingness": {"history_unavailable": history is None, "road_segment_missing": False},
        "provenance": {
            "visual_lineage": {
                "source": "model_versions.metrics.serving@assessment",
                "available_at": NOW.isoformat(),
                "latest_detection_at": NOW.isoformat(),
                "model_version_id": "model-old",
                "checkpoint_sha256": "b" * 64,
                "class_order": ["URMIND_ROAD_D40"],
                "feature_version": t.VISUAL_LINEAGE_VERSION,
                "model_contract_sha256": "a" * 64,
            },
            "context": {
                "open_meteo_rain": {
                    "temporal_status": "historical_source",
                    "fetched_at": context_available.isoformat() if context_available else None,
                    "ingested_at": context_available.isoformat() if context_available else None,
                }
            },
        },
    }


def export_row() -> dict:
    source = snapshot()
    review = SimpleNamespace(
        id="review-1",
        reviewer="synthetic-reviewer",
        event_id="event-1",
        created_at=NOW + timedelta(days=1),
        decision="confirm",
    )
    example = t.build_example(
        source,
        snapshot_collected_at=NOW + timedelta(minutes=5),
        label_at=review.created_at,
        review_confirmed=True,
        review=review,
        snapshot_id="assessment-1",
        knowledge_cutoff=NOW + timedelta(minutes=5),
        review_status="consensus",
        detector_origin="persisted_detection",
    )
    return json.loads(json.dumps(asdict(example), default=str))


def label_row() -> dict:
    return {
        "event_id": "event-1",
        "snapshot_id": "assessment-1",
        "snapshot_sha256": "a" * 64,
        "feature_schema_version": t.PINNED_FEATURE_SCHEMA,
        "capture_ids": ["capture-1"],
        "scene_group_id": "scene-1",
        "duplicate_group_id": "duplicate-1",
        "sequence_group_id": "sequence-1",
        "detector_version": "model-old",
        "detector_origin": "persisted_detector",
        "review_id": "review-1",
        "label_source": "persisted_review",
        "reviewers": ["reviewer-one", "reviewer-two"],
        "review_status": "consensus",
        "observed_at": NOW.isoformat(),
        "available_at": (NOW + timedelta(minutes=5)).isoformat(),
        "knowledge_cutoff": (NOW + timedelta(minutes=5)).isoformat(),
        "label_at": (NOW + timedelta(days=1)).isoformat(),
        "label": 1,
        "use_authorized": True,
    }


def split_row(index: int, role: str) -> dict:
    observed = NOW + timedelta(days=index)
    return {
        "event_id": f"event-{index}",
        "capture_ids": [f"capture-{index}"],
        "road_segment_id": f"segment-{index}",
        "scene_group_id": f"scene-{index}",
        "duplicate_group_id": f"duplicate-{index}",
        "sequence_group_id": f"sequence-{index}",
        "observed_at": observed.isoformat(),
        "label_at": (observed + timedelta(hours=1)).isoformat(),
        "role": role,
        "yolox_exposure": "YOLOX_EXPOSURE_UNKNOWN",
    }


class TabularPreparationTests(unittest.TestCase):
    def test_canonical_label_form_matches_runtime_protocol(self):
        protocol = (
            Path(__file__).resolve().parents[2]
            / "datasets/annotations/tabular_labeling_protocol.json"
        )
        self.assertEqual(json.loads(protocol.read_text(encoding="utf8")), t.labeling_protocol())

    def test_label_schema_and_target_are_distinct(self):
        document = {
            "target": "review_confirmed",
            "target_version": "urmind-review-v1",
            "rows": [label_row()],
        }
        self.assertEqual(t.validate_label_import(document)["rows"], 1)
        invalid = label_row() | {"label": "high"}
        with self.assertRaisesRegex(t.TabularExportError, "must be 0 or 1"):
            t.validate_label_import(document | {"rows": [invalid]})
        with self.assertRaisesRegex(t.TabularExportError, "cannot label another target"):
            t.validate_label_import(document | {"target": "risk"})
        self.assertEqual(
            t.labeling_protocol()["targets"]["risk"]["approval_status"], "TARGET_APPROVAL_PENDING"
        )
        for target, label in (("severity", "high"), ("priority", "expedited")):
            with self.subTest(target=target):
                candidate = label_row() | {
                    "label_source": "independent_inspection",
                    "evidence_ids": ["synthetic-inspection-1"],
                    "rubric_version": "synthetic-draft-v1",
                    "label": label,
                }
                checked = t.validate_label_import(
                    document | {"target": target, "rows": [candidate]}
                )
                self.assertEqual(checked["status"], "TARGET_APPROVAL_PENDING")
                self.assertEqual(checked["candidate_counts"], {label: 1})
                self.assertEqual(checked["training_eligible_rows"], 0)
        outcome = label_row() | {
            "label_source": "observed_outcome",
            "evidence_ids": ["synthetic-outcome-1"],
            "label": "observed",
            "horizon": "30d-synthetic",
            "follow_up_start": NOW.isoformat(),
            "follow_up_end": (NOW + timedelta(days=30)).isoformat(),
            "label_at": (NOW + timedelta(days=31)).isoformat(),
            "outcome_evidence": "synthetic-record",
            "exposure": "synthetic-exposure",
            "censoring": "synthetic-none",
        }
        risk = t.validate_label_import(document | {"target": "risk", "rows": [outcome]})
        self.assertEqual(risk["status"], "TARGET_APPROVAL_PENDING")
        self.assertEqual(risk["training_eligible_rows"], 0)
        with self.assertRaisesRegex(t.TabularExportError, "horizon/evidence"):
            t.validate_label_import(
                document | {"target": "risk", "rows": [outcome | {"horizon": None}]}
            )

    def test_missing_conflicting_duplicate_and_incomplete_labels(self):
        base = {"target": "review_confirmed", "target_version": "v1"}
        for rows, message in [
            ([label_row() | {"label": None}], "must be 0 or 1"),
            ([label_row(), label_row() | {"label": 0}], "conflicting labels"),
            ([label_row(), label_row()], "duplicate label"),
            ([label_row() | {"review_status": "pending"}], "review incomplete"),
            ([label_row() | {"use_authorized": False}], "use authorization"),
            (
                [label_row() | {"available_at": (NOW + timedelta(hours=1)).isoformat()}],
                "temporal order",
            ),
        ]:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(t.TabularExportError, message),
            ):
                t.validate_label_import(base | {"rows": rows})

    def test_feature_order_zero_missing_and_unknown_history(self):
        source = snapshot()
        row = t.flatten_snapshot(source)
        self.assertEqual(tuple(row), tuple(t.FEATURE_COLUMNS))
        self.assertEqual(row["previous_events_same_segment"], 0)
        self.assertEqual(row["detected_class_D40"], 1.0)
        self.assertEqual(row["detected_class_D00"], 0.0)
        self.assertAlmostEqual(row["bbox_area_ratio_max"], 0.1)
        source["visual"].update(
            detection_count=0, detection_classes=[], detection_confidences=[], bounding_boxes=[]
        )
        empty = t.flatten_snapshot(source)
        self.assertEqual(empty["detected_class_D40"], 0.0)
        self.assertIsNone(empty["bbox_area_ratio_max"])
        source["visual"].update(
            detection_count=1,
            detection_classes=["URMIND_ROAD_D40"],
            detection_confidences=[0.89],
            bounding_boxes=[{"x": 0.1, "y": 0.2, "width": 0.25, "height": 0.4}],
        )
        source["missingness"]["history_unavailable"] = True
        self.assertIsNone(t.flatten_snapshot(source)["previous_events_same_segment"])
        source["missingness"]["history_unavailable"] = False
        source["history"]["order_status"] = "legacy_order_uncertain"
        self.assertIsNone(t.flatten_snapshot(source)["recent_events_same_segment_30d"])
        source["history"]["order_status"] = "serialized_commit_order"
        source["history"]["coverage_status"] = "UNKNOWN"
        self.assertIsNone(t.flatten_snapshot(source)["previous_events_same_segment"])
        example = export_row()
        example["missingness"]["history_coverage_unknown"] = True
        example["features"]["previous_events_same_segment"] = None
        example["features"]["recent_events_same_segment_30d"] = None
        checked = t.validate_ground_truth_export({"training_authorized": False, "rows": [example]})
        self.assertEqual(checked["history_coverage_unknown"], 1)
        self.assertFalse(
            any(
                "cobertura histórica desconhecida" in reason
                for reason in checked["readiness"]["blockers"]
            )
        )

    def test_future_context_and_cutoff_are_rejected(self):
        source = snapshot(context_available=NOW + timedelta(hours=1))
        self.assertIsNone(t.flatten_snapshot(source, knowledge_cutoff=NOW)["rain_mm_24h"])
        source = snapshot(context_available=NOW - timedelta(hours=1))
        self.assertEqual(t.flatten_snapshot(source)["rain_mm_24h"], 4.0)
        with self.assertRaisesRegex(t.TabularExportError, "cutoff"):
            t.build_example(
                snapshot(),
                snapshot_collected_at=NOW + timedelta(minutes=5),
                label_at=NOW + timedelta(days=1),
                review_confirmed=True,
                knowledge_cutoff=NOW,
            )
        source = snapshot()
        source["provenance"]["visual_lineage"]["available_at"] = (
            NOW + timedelta(hours=1)
        ).isoformat()
        with self.assertRaisesRegex(t.TabularExportError, "FEATURE_BLOCKED_FUTURE"):
            t.flatten_snapshot(source, knowledge_cutoff=NOW)

    def test_visual_feature_values_reject_unsupported_class_and_bad_bbox(self):
        source = snapshot()
        source["visual"]["detection_classes"] = ["URMIND_ROAD_D99"]
        with self.assertRaisesRegex(t.TabularExportError, "outside pinned detector"):
            t.flatten_snapshot(source, knowledge_cutoff=NOW)
        source = snapshot()
        source["visual"]["bounding_boxes"][0]["width"] = 0.95
        with self.assertRaisesRegex(t.TabularExportError, "bbox outside frame"):
            t.flatten_snapshot(source, knowledge_cutoff=NOW)
        row = export_row()
        row["features"]["detected_class_D40"] = 2.0
        with self.assertRaisesRegex(t.TabularExportError, "outside 0/1"):
            t.validate_ground_truth_export({"training_authorized": False, "rows": [row]})

    def test_future_review_event_and_adjudication_cannot_enter_features(self):
        row = export_row()
        for forbidden in ("future_review", "future_event", "future_adjudication"):
            with (
                self.subTest(forbidden=forbidden),
                self.assertRaisesRegex(t.TabularExportError, "feature allowlist"),
            ):
                t.validate_ground_truth_export(
                    {
                        "training_authorized": False,
                        "rows": [row | {"features": row["features"] | {forbidden: 1}}],
                    }
                )
        future_history = snapshot()
        future_history["history"].update(
            previous_events_same_segment=1,
            last_event_at=(NOW + timedelta(minutes=1)).isoformat(),
        )
        with self.assertRaisesRegex(t.TabularExportError, "FEATURE_BLOCKED_FUTURE"):
            t.flatten_snapshot(future_history, knowledge_cutoff=NOW)
        later_availability = snapshot()
        later_availability["history"]["available_at"] = (NOW + timedelta(minutes=1)).isoformat()
        with self.assertRaisesRegex(t.TabularExportError, "FEATURE_BLOCKED_FUTURE"):
            t.flatten_snapshot(later_availability, knowledge_cutoff=NOW)
        forged_export = export_row()
        forged_export["feature_provenance"]["history_timing"]["available_at"] = (
            NOW + timedelta(days=2)
        ).isoformat()
        with self.assertRaisesRegex(t.TabularExportError, "FEATURE_BLOCKED_FUTURE"):
            t.validate_ground_truth_export({"training_authorized": False, "rows": [forged_export]})

    def test_feature_types_do_not_coerce_strings(self):
        source = snapshot()
        source["context"]["near_school"] = "false"
        source["provenance"]["context"]["overpass_pois"] = {
            "temporal_status": "historical_source",
            "fetched_at": NOW.isoformat(),
            "ingested_at": NOW.isoformat(),
        }
        with self.assertRaisesRegex(t.TabularExportError, "boolean feature"):
            t.flatten_snapshot(source)
        source = snapshot()
        source["visual"]["detection_count"] = "2"
        with self.assertRaisesRegex(t.TabularExportError, "count feature"):
            t.flatten_snapshot(source)

    def test_export_contract_hash_lineage_and_duplicate(self):
        row = export_row()
        document = {"training_authorized": False, "rows": [row]}
        result = t.validate_ground_truth_export(document)
        self.assertEqual(result["rows"], 1)
        self.assertEqual(result["readiness"]["status"], "BLOCKED_DATA")
        with self.assertRaisesRegex(t.TabularExportError, "duplicate Event"):
            t.validate_ground_truth_export(document | {"rows": [row, row]})
        with self.assertRaisesRegex(t.TabularExportError, "feature allowlist"):
            t.validate_ground_truth_export(
                document | {"rows": [row | {"features": {"review_result": 1, **row["features"]}}]}
            )
        with self.assertRaisesRegex(t.TabularExportError, "hash mismatch"):
            t.validate_ground_truth_export(
                document
                | {
                    "dataset": {
                        "target": t.TARGET_NAME,
                        "schema_version": t.TABULAR_SCHEMA_VERSION,
                        "feature_schema_version": t.PINNED_FEATURE_SCHEMA,
                        "feature_schema_sha256": t.feature_schema_sha256(),
                        "label_schema_sha256": t.label_schema_sha256(),
                        "target_version": row["target_version"],
                        "feature_columns": list(t.FEATURE_COLUMNS),
                        "content_sha256": "0" * 64,
                    }
                }
            )
        future = json.loads(json.dumps(row))
        future["features"]["rain_mm_24h"] = 4.0
        future["feature_provenance"]["context"]["open_meteo_rain"].update(
            temporal_status="historical_source",
            fetched_at=(NOW + timedelta(hours=1)).isoformat(),
            ingested_at=(NOW + timedelta(hours=1)).isoformat(),
        )
        with self.assertRaisesRegex(t.TabularExportError, "future context"):
            t.validate_ground_truth_export(document | {"rows": [future]})
        unknown = json.loads(json.dumps(row))
        unknown["missingness"]["history_coverage_unknown"] = True
        with self.assertRaisesRegex(t.TabularExportError, "history feature"):
            t.validate_ground_truth_export(document | {"rows": [unknown]})

    def test_split_unknown_overlap_temporal_and_cross_model(self):
        rows = [
            split_row(i, role)
            for i, role in enumerate(("TRAIN", "VALIDATION", "CALIBRATION", "TEST"))
        ]
        self.assertEqual(
            t.validate_split_plan({"rows": rows, "temporal": True})["status"],
            "BLOCKED_DATA",
        )
        bad = [*rows[:-1], rows[-1] | {"scene_group_id": "UNCONFIRMED"}]
        with self.assertRaisesRegex(t.TabularExportError, "unknown scene"):
            t.validate_split_plan({"rows": bad})
        bad = [*rows[:-1], rows[-1] | {"duplicate_group_id": rows[0]["duplicate_group_id"]}]
        with self.assertRaisesRegex(t.TabularExportError, "overlaps"):
            t.validate_split_plan({"rows": bad})
        bad = [*rows[:-1], rows[-1] | {"yolox_exposure": "NONE_VERIFIED"}]
        with self.assertRaisesRegex(t.TabularExportError, "invalid YOLOX exposure"):
            t.validate_split_plan({"rows": bad})
        bad = [*rows[:-1], rows[-1] | {"yolox_exposure": "YOLOX_NOT_EXPOSED_VERIFIED"}]
        with self.assertRaisesRegex(t.TabularExportError, "lacks manifest proof"):
            t.validate_split_plan({"rows": bad})
        self.assertEqual(
            t.validate_split_plan({"rows": rows})["combined_model_evaluation"],
            "BLOCKED_EXPOSURE",
        )
        bad = [*rows[:-1], rows[-1] | {"observed_at": NOW.isoformat()}]
        with self.assertRaisesRegex(t.TabularExportError, "temporal split"):
            t.validate_split_plan({"rows": bad, "temporal": True})
        skipped_middle = [
            split_row(0, "TRAIN"),
            split_row(1, "TRAIN"),
            split_row(2, "VALIDATION"),
            split_row(3, "VALIDATION"),
            split_row(4, "TEST"),
            split_row(5, "TEST"),
        ]
        skipped_middle[-1]["observed_at"] = (NOW + timedelta(days=1)).isoformat()
        with self.assertRaisesRegex(t.TabularExportError, "temporal split"):
            t.validate_split_plan({"rows": skipped_middle, "temporal": True})

    def test_generate_split_plan_requires_verified_groups(self):
        rows = []
        for index in range(10):
            row = export_row()
            observed = NOW + timedelta(days=index * 2)
            row.update(
                event_id=f"event-{index}",
                capture_ids=[f"capture-{index}"],
                road_segment_id=f"segment-{index}",
                snapshot_id=f"assessment-{index}",
                scene_group_id=f"scene-{index}",
                duplicate_group_id=f"duplicate-{index}",
                sequence_group_id=f"sequence-{index}",
                occurred_at=observed.isoformat(),
                snapshot_collected_at=(observed + timedelta(minutes=5)).isoformat(),
                knowledge_cutoff=(observed + timedelta(minutes=5)).isoformat(),
                label_at=(observed + timedelta(hours=1)).isoformat(),
                yolox_exposure="YOLOX_EXPOSURE_UNKNOWN",
            )
            rows.append(row)
        export = {"training_authorized": False, "rows": rows}
        plan = t.generate_split_plan(export, seed=17)
        self.assertEqual(plan["status"], "DRAFT")
        self.assertEqual(plan["include_holdouts"], False)
        self.assertEqual(
            t.validate_split_plan(plan)["counts"],
            {"TRAIN": 7, "VALIDATION": 3},
        )
        preflight = t.preflight_xgboost(export, plan, CONFIG)
        self.assertIn("TRAIN lacks a target class", preflight["blockers"])
        self.assertIn("VALIDATION lacks a target class", preflight["blockers"])
        self.assertEqual(
            t.generate_split_plan(export, seed=17, include_holdouts=True)["status"],
            "BLOCKED_DATA",
        )
        rows[0]["scene_group_id"] = "UNCONFIRMED"
        with self.assertRaisesRegex(t.TabularExportError, "unknown scene"):
            t.generate_split_plan(export, seed=17)

    def test_yolox_exposure_requires_registered_manifest_bytes(self):
        rows = [split_row(i, role) for i, role in enumerate(("TRAIN", "VALIDATION"))]
        rows[0]["visual_source_fingerprints"] = ["source-a"]
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            metadata = root / "datasets" / "metadata"
            manifests = root / "datasets" / "manifests"
            metadata.mkdir(parents=True)
            manifests.mkdir(parents=True)
            paths = {}
            artifacts = []
            for role, fingerprint in (("TRAIN", "source-a"), ("VALIDATION", "source-b")):
                path = manifests / f"{role.lower()}.jsonl"
                path.write_text(
                    json.dumps({"split": role, "source_fingerprint": fingerprint}) + "\n",
                    encoding="utf8",
                )
                paths[role] = path
                artifacts.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "size_bytes": path.stat().st_size,
                    }
                )
            registry = metadata / "artifact_registry.json"
            registry.write_text(json.dumps({"artifacts": artifacts}), encoding="utf8")
            result = t.validate_split_plan(
                {"rows": rows}, yolox_registry=registry, yolox_manifests=paths
            )
            self.assertEqual(result["yolox_exposure_by_event"]["event-0"], "YOLOX_TRAIN_EXPOSED")
            self.assertEqual(result["yolox_exposure_by_event"]["event-1"], "YOLOX_EXPOSURE_UNKNOWN")
            self.assertEqual(result["combined_model_evaluation"], "BLOCKED_EXPOSURE")
            seen_validation = [rows[0], rows[1] | {"visual_source_fingerprints": ["source-b"]}]
            self.assertEqual(
                t.validate_split_plan(
                    {"rows": seen_validation}, yolox_registry=registry, yolox_manifests=paths
                )["yolox_exposure_by_event"]["event-1"],
                "YOLOX_VALIDATION_EXPOSED",
            )
            forged = [rows[0], rows[1] | {"yolox_exposure": "YOLOX_NOT_EXPOSED_VERIFIED"}]
            with self.assertRaisesRegex(t.TabularExportError, "lacks manifest proof"):
                t.validate_split_plan(
                    {"rows": forged}, yolox_registry=registry, yolox_manifests=paths
                )
            paths["TRAIN"].write_text("{}\n", encoding="utf8")
            with self.assertRaisesRegex(t.TabularExportError, "artifact.*mismatch"):
                t.validate_split_plan(
                    {"rows": rows}, yolox_registry=registry, yolox_manifests=paths
                )

    def test_validation_evaluation_recomputes_roles_and_artifact_hashes(self):
        rows = []
        for index in range(10):
            row = export_row()
            observed = NOW + timedelta(days=index * 2)
            row.update(
                event_id=f"event-{index}",
                capture_ids=[f"capture-{index}"],
                road_segment_id=f"segment-{index}",
                snapshot_id=f"assessment-{index}",
                scene_group_id=f"scene-{index}",
                duplicate_group_id=f"duplicate-{index}",
                sequence_group_id=f"sequence-{index}",
                occurred_at=observed.isoformat(),
                snapshot_collected_at=(observed + timedelta(minutes=5)).isoformat(),
                knowledge_cutoff=(observed + timedelta(minutes=5)).isoformat(),
                available_at=(observed + timedelta(minutes=5)).isoformat(),
                label_at=(observed + timedelta(hours=1)).isoformat(),
                target=index % 2,
                yolox_exposure="YOLOX_EXPOSURE_UNKNOWN",
            )
            rows.append(row)
        dataset = {"training_authorized": False, "rows": rows}
        checked = t.validate_ground_truth_export(dataset)
        dataset["dataset"] = {
            "name": "synthetic-v1",
            "target": t.TARGET_NAME,
            "schema_version": t.TABULAR_SCHEMA_VERSION,
            "feature_schema_version": t.PINNED_FEATURE_SCHEMA,
            "feature_schema_sha256": t.feature_schema_sha256(),
            "label_schema_sha256": t.label_schema_sha256(),
            "target_version": rows[0]["target_version"],
            "feature_columns": list(t.FEATURE_COLUMNS),
            "content_sha256": checked["content_sha256"],
        }
        split = t.generate_split_plan(dataset, seed=17)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            metadata_dir = root / "datasets" / "metadata"
            output_dir = root / "datasets" / "tabular"
            metadata_dir.mkdir(parents=True)
            output_dir.mkdir(parents=True)
            paths = {
                name: output_dir / f"{name}.json"
                for name in ("dataset", "split", "predictions", "model_metadata")
            }
            paths["model"] = output_dir / "model.ubj"
            paths["model"].write_bytes(b"synthetic model artifact; no fit")
            model_hash = hashlib.sha256(paths["model"].read_bytes()).hexdigest()
            lineage = {
                "model_sha256": model_hash,
                "dataset_sha256": checked["content_sha256"],
                "split_sha256": "",
                "feature_schema_sha256": t.feature_schema_sha256(),
                "target": t.TARGET_NAME,
                "target_version": checked["target_version"],
                "dataset_version": "synthetic-v1",
                "model_version": "synthetic-model-v1",
            }
            paths["dataset"].write_text(json.dumps(dataset), encoding="utf8")
            paths["split"].write_text(json.dumps(split), encoding="utf8")
            lineage["split_sha256"] = hashlib.sha256(paths["split"].read_bytes()).hexdigest()
            paths["model_metadata"].write_text(json.dumps(lineage), encoding="utf8")
            validation = [row for row in split["rows"] if row["role"] == "VALIDATION"]
            predictions = {
                **lineage,
                "role": "VALIDATION",
                "rows": [
                    {
                        "event_id": row["event_id"],
                        "snapshot_id": row["snapshot_id"],
                        "snapshot_sha256": row["snapshot_sha256"],
                        "probability": 0.5,
                    }
                    for row in validation
                ],
            }
            paths["predictions"].write_text(json.dumps(predictions), encoding="utf8")
            registry = metadata_dir / "artifact_registry.json"

            def register() -> None:
                registry.write_text(
                    json.dumps(
                        {
                            "artifacts": [
                                {
                                    "path": path.relative_to(root).as_posix(),
                                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                    "size_bytes": path.stat().st_size,
                                }
                                for path in paths.values()
                            ]
                        }
                    ),
                    encoding="utf8",
                )

            def evaluate() -> dict:
                return t.evaluate_validation_artifacts(
                    paths["predictions"],
                    paths["dataset"],
                    paths["split"],
                    paths["model"],
                    paths["model_metadata"],
                    registry,
                )

            register()
            self.assertEqual(evaluate()["role"], "VALIDATION")
            forged = dict(split)
            forged["rows"] = [
                row | {"role": "VALIDATION"} if row["role"] == "TRAIN" else row
                for row in split["rows"]
            ]
            paths["split"].write_text(json.dumps(forged), encoding="utf8")
            register()
            with self.assertRaisesRegex(t.TabularExportError, "split manifest differs"):
                evaluate()
            paths["split"].write_text(json.dumps(split), encoding="utf8")
            register()
            predictions["rows"] = [predictions["rows"][0] | {"event_id": "event-3"}]
            paths["predictions"].write_text(json.dumps(predictions), encoding="utf8")
            register()
            with self.assertRaisesRegex(t.TabularExportError, "not unique VALIDATION"):
                evaluate()
            paths["model"].write_bytes(b"modified after registration")
            with self.assertRaisesRegex(t.TabularExportError, "registered artifact.*mismatch"):
                evaluate()

    def test_training_bundle_binds_approval_and_never_fits_unregistered_data(self):
        template = export_row()
        rows = []
        for index in range(200):
            row = json.loads(json.dumps(template))
            observed = NOW + timedelta(days=index * 2)
            row.update(
                event_id=f"event-{index}",
                capture_ids=[f"capture-{index}"],
                road_segment_id=f"segment-{index}",
                snapshot_id=f"assessment-{index}",
                review_id=f"review-{index}",
                scene_group_id=f"scene-{index}",
                duplicate_group_id=f"duplicate-{index}",
                sequence_group_id=f"sequence-{index}",
                occurred_at=observed.isoformat(),
                snapshot_collected_at=(observed + timedelta(minutes=5)).isoformat(),
                knowledge_cutoff=(observed + timedelta(minutes=5)).isoformat(),
                available_at=(observed + timedelta(minutes=5)).isoformat(),
                label_at=(observed + timedelta(hours=1)).isoformat(),
                target=index % 2,
                use_authorized=True,
                visual_preprocessing_version="model-contract-sha256:" + "a" * 64,
                visual_postprocessing_version="model-contract-sha256:" + "a" * 64,
            )
            rows.append(row)
        export = {"training_authorized": False, "rows": rows}
        checked = t.validate_ground_truth_export(export)
        mixed = json.loads(json.dumps(export))
        mixed["rows"][0]["vision_checkpoint_sha256"] = "c" * 64
        mixed["rows"][0]["feature_provenance"]["visual_lineage"]["checkpoint_sha256"] = "c" * 64
        self.assertTrue(
            any(
                "versões visuais misturadas" in reason
                for reason in t.validate_ground_truth_export(mixed)["readiness"]["blockers"]
            )
        )
        export["dataset"] = {
            "name": "synthetic-training-v1",
            "target": t.TARGET_NAME,
            "schema_version": t.TABULAR_SCHEMA_VERSION,
            "feature_schema_version": t.PINNED_FEATURE_SCHEMA,
            "feature_schema_sha256": t.feature_schema_sha256(),
            "label_schema_sha256": t.label_schema_sha256(),
            "target_version": rows[0]["target_version"],
            "feature_columns": list(t.FEATURE_COLUMNS),
            "content_sha256": checked["content_sha256"],
        }
        split = t.generate_split_plan(export, seed=CONFIG["seed"])
        self.assertEqual(split["status"], "DRAFT")
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            metadata = root / "datasets" / "metadata"
            inputs = root / "datasets" / "processed" / "tabular"
            metadata.mkdir(parents=True)
            inputs.mkdir(parents=True)
            paths = {
                "dataset": inputs / "dataset.json",
                "split": inputs / "split.json",
                "config": inputs / "config.json",
                "approval": inputs / "approval.json",
            }
            for name, value in (("dataset", export), ("split", split), ("config", CONFIG)):
                paths[name].write_text(json.dumps(value), encoding="utf8")
            digests = {
                name: hashlib.sha256(paths[name].read_bytes()).hexdigest()
                for name in ("dataset", "split", "config")
            }
            approval = {
                "status": "APPROVED_EXPERIMENTAL_TRAINING",
                "dataset_artifact_sha256": digests["dataset"],
                "split_artifact_sha256": digests["split"],
                "config_artifact_sha256": digests["config"],
                "dataset_sha256": checked["content_sha256"],
                "dataset_version": "synthetic-training-v1",
                "feature_schema_sha256": t.feature_schema_sha256(),
                "label_schema_sha256": t.label_schema_sha256(),
                "target": t.TARGET_NAME,
                "target_version": CONFIG["target_version"],
                "approved_by": "synthetic-reviewer",
                "authorization_source": "synthetic-fixture-only",
                "approved_at": NOW.isoformat(),
            }
            paths["approval"].write_text(json.dumps(approval), encoding="utf8")
            registry = metadata / "artifact_registry.json"

            def register() -> None:
                registry.write_text(
                    json.dumps(
                        {
                            "artifacts": [
                                {
                                    "path": path.relative_to(root).as_posix(),
                                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                    "size_bytes": path.stat().st_size,
                                }
                                for path in paths.values()
                            ]
                        }
                    ),
                    encoding="utf8",
                )

            register()
            paths["approval"].write_text(
                json.dumps(approval | {"dataset_sha256": "0" * 64}), encoding="utf8"
            )
            register()
            with (
                patch.object(t.importlib.util, "find_spec", return_value=object()),
                self.assertRaisesRegex(t.TabularExportError, "approval does not bind"),
            ):
                t.load_verified_training_bundle(
                    paths["dataset"],
                    paths["split"],
                    paths["config"],
                    paths["approval"],
                    registry,
                )
            paths["approval"].write_text(json.dumps(approval), encoding="utf8")
            register()
            with patch.object(t.importlib.util, "find_spec", return_value=object()):
                bundle = t.load_verified_training_bundle(
                    paths["dataset"],
                    paths["split"],
                    paths["config"],
                    paths["approval"],
                    registry,
                )
            self.assertIsInstance(bundle, t.VerifiedTrainingBundle)
            # Blocking the imports proves the refusal happens before any heavy import:
            # an attempted import would raise ImportError instead of this refusal.
            with (
                patch.dict(sys.modules, {"xgboost": None, "psutil": None}),
                self.assertRaisesRegex(t.TabularExportError, "isolated worktree"),
            ):
                t.train_xgboost(bundle, output_root=Path(__file__).resolve().parents[3])

    def test_visual_provenance_absence_and_unsupported_class(self):
        base = {
            "model_version": "model-old",
            "preprocessing_version": "prep-1",
            "postprocessing_version": "post-1",
            "checkpoint_sha256": "b" * 64,
            "class_order": ["URMIND_ROAD_D40"],
            "feature_version": "vision-synthetic-v1",
            "origin": "synthetic_fixture",
            "image_width": 640,
            "image_height": 480,
            "detections": [],
        }
        self.assertEqual(t.validate_visual_output(base)["observation"], "NO_DETECTION_OBSERVED")
        with self.assertRaisesRegex(t.TabularExportError, "checkpoint SHA-256"):
            t.validate_visual_output(base | {"checkpoint_sha256": "unknown"})
        with self.assertRaisesRegex(t.TabularExportError, "class order"):
            t.validate_visual_output(base | {"class_order": ["URMIND_ROAD_D40"] * 2})
        bad_detection = {
            "class": "URMIND_UNKNOWN",
            "confidence": 0.8,
            "bbox": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
        }
        may_emit = lambda code: code == "URMIND_ROAD_D40"
        with self.assertRaisesRegex(t.TabularExportError, "unsupported visual class"):
            t.validate_visual_output(base | {"detections": [bad_detection]}, may_emit=may_emit)
        good = bad_detection | {"class": "URMIND_ROAD_D40"}
        self.assertEqual(
            t.validate_visual_output(base | {"detections": [good]}, may_emit=may_emit)[
                "detections"
            ],
            1,
        )

    def test_dry_run_dependency_and_no_fit_or_auto_promotion(self):
        with self.assertRaisesRegex(t.TabularExportError, "at most 10 hours"):
            t.preflight_xgboost(None, None, CONFIG | {"max_wall_time_seconds": 36001})
        with self.assertRaisesRegex(t.TabularExportError, "one thread"):
            t.preflight_xgboost(None, None, CONFIG | {"nthread": 2})
        with patch.object(t.importlib.util, "find_spec", return_value=None):
            result = t.dry_run_xgboost(None, None, CONFIG)
        self.assertEqual(result["label_count"], "NOT_VERIFIED")
        self.assertFalse(result["fit_called"])
        self.assertFalse(result["artifact_written"])
        self.assertIn("xgboost is not installed", " ".join(result["blockers"]))
        self.assertFalse(t.promotion_gate({})["promotable"])
        # A fresh interpreter: other tests in this process may already have imported these.
        probe = (
            "import json, sys\n"
            "from unittest.mock import patch\n"
            "from app.ml import tabular as t\n"
            f"config = json.loads({json.dumps(json.dumps(CONFIG))})\n"
            "with patch.object(t.importlib.util, 'find_spec', return_value=None):\n"
            "    t.dry_run_xgboost(None, None, config)\n"
            "t.promotion_gate({})\n"
            "heavy = ('torch', 'xgboost', 'shap', 'app.main', 'app.services.core')\n"
            "print(json.dumps([m for m in heavy if m in sys.modules]))\n"
        )
        loaded = subprocess.run(
            [sys.executable, "-B", "-c", probe],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(json.loads(loaded.stdout.strip().splitlines()[-1]), [])

    def test_training_supervisor_enforces_ten_hour_process_limit_without_fit(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            config_path = root / "config.json"
            config_path.write_text(json.dumps(CONFIG), encoding="utf8")
            with patch.dict(sys.modules, {"xgboost": None}), patch.object(
                t.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(
                    "synthetic-worker", CONFIG["max_wall_time_seconds"]
                ),
            ) as run:
                # The supervisor never imports xgboost itself; the fit runs in a child.
                result = t.supervise_xgboost_train(
                    root / "dataset.json",
                    root / "split.json",
                    config_path,
                    root / "approval.json",
                    root / "artifact_registry.json",
                    root,
                )
            self.assertEqual(result["status"], "TRAINING_TIME_LIMIT")
            self.assertEqual(run.call_args.kwargs["timeout"], 36000)
            self.assertIn("--train-worker", run.call_args.args[0])

    def test_export_cli_requires_content_bound_dataset_version(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            source = Path(directory) / "source.json"
            output = Path(directory) / "dataset.json"
            source.write_text(
                json.dumps({"training_authorized": False, "rows": [export_row()]}),
                encoding="utf8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "app.ml.tabular",
                    "--export-dataset",
                    str(source),
                    "--output",
                    str(output),
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("DatasetVersion name and content hash required", result.stderr)
            self.assertFalse(output.exists())

    def test_validation_metrics_missing_class(self):
        result = t.validation_metrics([0, 0], [0.2, 0.7])
        self.assertEqual(result["confusion_matrix"], {"tn": 1, "fp": 1, "fn": 0, "tp": 0})
        self.assertTrue(result["class_missing"])
        self.assertIsNone(result["average_precision"])
        tied = t.validation_metrics([0, 1], [0.5, 0.5])
        self.assertEqual(tied["average_precision"], 0.5)


if __name__ == "__main__":
    unittest.main()
