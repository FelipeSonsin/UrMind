"""Small tests for the shared Attain/inference visual feature extractor."""

import unittest

from PIL import Image

from app.services.features import (
    SEVERITY_DETECTION_FEATURE_ORDER,
    build_severity_detection_features,
    severity_detection_schema_sha256,
)


class SeverityDetectionFeatureTests(unittest.TestCase):
    def test_feature_order_and_image_evidence(self) -> None:
        image = Image.new("RGB", (64, 32), "white")
        image.paste("black", (0, 0, 32, 32))
        left = build_severity_detection_features(
            "URMIND_ROAD_D20", {"x": 0.0, "y": 0.0, "width": 0.5, "height": 1.0}, image
        )
        right = build_severity_detection_features(
            "URMIND_ROAD_D40", {"x": 0.5, "y": 0.0, "width": 0.5, "height": 1.0}, image
        )
        self.assertEqual(tuple(left), SEVERITY_DETECTION_FEATURE_ORDER)
        self.assertEqual(tuple(right), SEVERITY_DETECTION_FEATURE_ORDER)
        self.assertEqual(left["detected_class_D20"], 1.0)
        self.assertEqual(right["detected_class_D40"], 1.0)
        self.assertEqual(left["bbox_area_ratio"], 0.5)
        self.assertLess(left["crop_brightness_mean"], right["crop_brightness_mean"])
        self.assertEqual(left["crop_dark_fraction"], 1.0)
        self.assertEqual(right["crop_dark_fraction"], 0.0)
        self.assertEqual(len(severity_detection_schema_sha256()), 64)

    def test_unsupported_class_and_invalid_box_fail_closed(self) -> None:
        image = Image.new("RGB", (16, 16), "gray")
        box = {"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.5}
        with self.assertRaisesRegex(ValueError, "unsupported"):
            build_severity_detection_features("URMIND_ROAD_D00", box, image)
        with self.assertRaisesRegex(ValueError, "outside frame"):
            build_severity_detection_features(
                "URMIND_ROAD_D40", {**box, "width": 1.0}, image
            )
        with self.assertRaisesRegex(ValueError, "finite numeric"):
            build_severity_detection_features(
                "URMIND_ROAD_D40", {**box, "width": True}, image
            )


if __name__ == "__main__":
    unittest.main()
