import argparse
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

import directory_index
import directory_vision


def vision_note():
    return {"visible_text": "Onboarding review", "visual_description": "A two-step review diagram.",
            "summary": "This image shows a two-step Onboarding review process for compliance staff.",
            "key_terms": ["Onboarding", "review"], "user_intents": ["Find Onboarding review diagram"],
            "use_cases": ["Explain review steps"], "confidence": "high",
            "artifact_type": "diagram"}


class VisionTest(unittest.TestCase):
    def test_transparent_icon_is_composited_for_visual_analysis(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "icon.png"
            icon = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
            icon.putpixel((8, 8), (0, 0, 0, 255))
            icon.save(source)
            import hashlib
            prepared = directory_vision.prepared_image(source, Path(d), hashlib.sha256(source.read_bytes()).hexdigest())
            with Image.open(prepared) as visible:
                self.assertEqual(visible.getpixel((0, 0)), (192, 192, 192))
                self.assertEqual(visible.getpixel((8, 8)), (0, 0, 0))

    def test_incomplete_visual_analysis_retries_once(self):
        with tempfile.TemporaryDirectory() as d:
            root, state = Path(d) / "library", Path(d) / "state"
            root.mkdir()
            Image.new("RGB", (50, 50), "white").save(root / "diagram.png")
            directory_index.scan(argparse.Namespace(source_root=str(root), state_dir=str(state),
                                           site_url="https://example.sharepoint.com/sites/ExampleTeam",
                                           min_retention=0.8, rehash_all=False))
            calls = 0

            def model(*args, **kwargs):
                nonlocal calls
                calls += 1
                note = vision_note()
                if calls == 1:
                    note["key_terms"] = []
                return note

            result = directory_vision.run(argparse.Namespace(state_dir=str(state), ca_certificate=None,
                                                       max_files=10, max_seconds=60, timeout_seconds=30), model)
            self.assertEqual(calls, 2)
            self.assertEqual(result["completed_image_hashes"], 1)

    def test_identical_images_are_analyzed_once_and_both_locations_indexed(self):
        with tempfile.TemporaryDirectory() as d:
            root, state = Path(d) / "library", Path(d) / "state"
            (root / "General").mkdir(parents=True)
            (root / "Archive").mkdir()
            first = root / "General" / "diagram.png"
            Image.new("RGB", (50, 50), "white").save(first)
            (root / "Archive" / "diagram.png").write_bytes(first.read_bytes())
            directory_index.scan(argparse.Namespace(source_root=str(root), state_dir=str(state),
                                           site_url="https://example.sharepoint.com/sites/ExampleTeam",
                                           min_retention=0.8, rehash_all=False))
            calls = []

            def model(payload, schema, output, budget, image_path=None, rules=None):
                calls.append(payload["sha256"])
                self.assertTrue(image_path.is_file())
                return vision_note()

            result = directory_vision.run(argparse.Namespace(state_dir=str(state), ca_certificate=None,
                                                       max_files=10, max_seconds=60, timeout_seconds=30), model)
            self.assertEqual(result["completed_image_hashes"], 1)
            self.assertEqual(len(calls), 1)
            manifest = json.loads((state / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual({entry["extraction_status"] for entry in manifest["files"].values()}, {"complete"})
            self.assertEqual(directory_index.validate(state)["files"], 2)

    def test_changed_image_is_not_analyzed_from_stale_scan(self):
        with tempfile.TemporaryDirectory() as d:
            root, state = Path(d) / "library", Path(d) / "state"
            root.mkdir()
            image = root / "diagram.png"
            Image.new("RGB", (50, 50), "white").save(image)
            directory_index.scan(argparse.Namespace(source_root=str(root), state_dir=str(state),
                                           site_url="https://example.sharepoint.com/sites/ExampleTeam",
                                           min_retention=0.8, rehash_all=False))
            Image.new("RGB", (60, 60), "black").save(image)
            with self.assertRaisesRegex(OSError, "changed since scan"):
                directory_vision.run(argparse.Namespace(state_dir=str(state), ca_certificate=None,
                                                   max_files=10, max_seconds=60, timeout_seconds=30),
                                lambda *args, **kwargs: self.fail("model must not run"))
            self.assertFalse((state / ".vision.lock").exists())


if __name__ == "__main__":
    unittest.main()
