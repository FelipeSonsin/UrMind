"""Small source-contract tests; no network, images, model, or database."""

import unittest

import acquire_attain_annotations as attain


class AttainAnnotationTests(unittest.TestCase):
    def test_severity_is_separate_from_damage_type(self) -> None:
        names = ["Alligator crack - High", "Alligator crack - Low", "Alligator crack - low"]
        parsed = attain._parse_yolo(
            b"0 0.5 0.5 0.2 0.3\n1 0.5 0.5 0.2 0.3\n2 0.5 0.5 0.2 0.3\n", names
        )
        self.assertEqual(
            parsed,
            [
                ("alligator crack", "HIGH"),
                ("alligator crack", "LOW"),
                ("alligator crack", "LOW"),
            ],
        )
        self.assertNotIn("linear crack", attain.SUPPORTED_TYPES)
        with self.assertRaisesRegex(ValueError, "explicit severity"):
            attain._label_parts("Pothole")

    def test_binary_target_never_collapses_medium_into_low(self) -> None:
        self.assertEqual(attain._binary_severity_target("LOW"), 0)
        self.assertEqual(attain._binary_severity_target("HIGH"), 1)
        with self.assertRaisesRegex(ValueError, "unsupported binary severity"):
            attain._binary_severity_target("MEDIUM")

    def test_yolo_rejects_invalid_geometry_and_class(self) -> None:
        with self.assertRaisesRegex(ValueError, "box invalid"):
            attain._parse_yolo(b"0 0.9 0.5 0.4 0.2\n", ["Pothole - High"])
        with self.assertRaisesRegex(ValueError, "class ID"):
            attain._parse_yolo(b"1 0.5 0.5 0.2 0.2\n", ["Pothole - High"])

    def test_xml_accepts_published_one_unit_boundary_only(self) -> None:
        template = (
            "<annotation><filename>image.jpg</filename>"
            "<size><width>640</width><height>640</height></size>"
            "<object><name>Pothole - High</name><bndbox>"
            "<xmin>620</xmin><xmax>{xmax}</xmax><ymin>300</ymin><ymax>641</ymax>"
            "</bndbox></object></annotation>"
        )
        self.assertEqual(
            attain._parse_xml(template.format(xmax=641).encode(), "image.jpg"),
            [("pothole", "HIGH")],
        )
        with self.assertRaisesRegex(ValueError, "outside image"):
            attain._parse_xml(template.format(xmax=642).encode(), "image.jpg")
        with self.assertRaisesRegex(ValueError, "linkage mismatch"):
            attain._parse_xml(template.format(xmax=641).encode(), "other.jpg")
        issues: list[str] = []
        self.assertEqual(
            attain._parse_xml(template.format(xmax=620).encode(), "image.jpg", issues=issues),
            [],
        )
        self.assertEqual(issues, ["object_0:box_outside_or_degenerate"])

    def test_yolo_polygon_and_xml_box_share_normalized_feature_frame(self) -> None:
        yolo = attain._yolo_objects(
            b"0 0.25 0.25 0.75 0.25 0.75 0.75 0.25 0.75\n",
            ["Pothole - High"],
        )
        xml = attain._xml_objects(
            b"<annotation><filename>image.jpg</filename>"
            b"<size><width>100</width><height>100</height></size>"
            b"<object><name>Pothole - High</name><bndbox>"
            b"<xmin>25</xmin><xmax>75</xmax><ymin>25</ymin><ymax>75</ymax>"
            b"</bndbox></object></annotation>",
            "image.jpg",
        )
        self.assertEqual(yolo[0][1:3], xml[0][1:3])
        self.assertEqual(yolo[0][4], xml[0][4])
        self.assertEqual(
            yolo[0][4], {"x": 0.25, "y": 0.25, "width": 0.5, "height": 0.5}
        )

    def test_xml_polygon_must_agree_with_its_box(self) -> None:
        template = (
            "<annotation><filename>image.jpg</filename>"
            "<size><width>100</width><height>100</height></size>"
            "<object><name>Pothole - High</name><bndbox>"
            "<xmin>25</xmin><xmax>75</xmax><ymin>25</ymin><ymax>75</ymax>"
            "</bndbox><polygon>"
            "<x1>{left}</x1><y1>{top}</y1><x2>75</x2><y2>{top}</y2>"
            "<x3>75</x3><y3>75</y3><x4>{left}</x4><y4>75</y4>"
            "</polygon></object></annotation>"
        )
        self.assertEqual(
            len(
                attain._xml_objects(
                    template.format(left=24, top=24).encode(), "image.jpg"
                )
            ),
            1,
        )
        with self.assertRaisesRegex(ValueError, "polygon.*box"):
            attain._xml_objects(template.format(left=40, top=40).encode(), "image.jpg")
        issues: list[str] = []
        self.assertEqual(
            attain._xml_objects(
                template.format(left=40, top=40).encode(), "image.jpg", issues=issues
            ),
            [],
        )
        self.assertEqual(issues, ["object_0:polygon_box_mismatch"])
        malformed = template.format(left=25, top=25).replace("<y4>75</y4>", "")
        with self.assertRaisesRegex(ValueError, "polygon.*box"):
            attain._xml_objects(malformed.encode(), "image.jpg")


if __name__ == "__main__":
    unittest.main()
