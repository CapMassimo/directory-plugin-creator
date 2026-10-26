"""Offline checks for a configured directory and generated plugin."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

import config as settings
import directory_enrich
import directory_handoff
import directory_index
import directory_recover
import directory_vision
import setup


def example(root: Path, state: Path) -> dict:
    return settings.validate_config({
        "source_root": str(root), "state_dir": str(state),
        "site_url": "https://example.sharepoint.com/sites/Team%20Docs",
        "library_path": "Shared Documents", "source_folder": "Projects/Current",
        "index_folder": "Search Catalog", "freshness_hours": 72,
        "plugin": {"name": "project-search", "display_name": "Project Search",
                   "description": "Find project files", "developer_name": "Example team"},
        "custom_fields": [{"key": "project_code", "label": "Project code",
                           "guidance": "Use a code explicitly present in the file."}],
    })


def note(code: str) -> dict:
    return {"summary": "This file explains the launch checklist and review steps for the project.",
            "key_terms": ["launch", "checklist"], "user_intents": ["Find the launch checklist"],
            "use_cases": ["Prepare a project launch"], "confidence": "high",
            "artifact_type": "checklist", "project_code": [code]}


def scan_args(cfg):
    return argparse.Namespace(config_data=cfg, min_retention=0.8, rehash_all=False)


def enrich_args(cfg):
    return argparse.Namespace(config_data=cfg, state_dir=cfg["state_dir"], ca_certificate=None,
                              max_files=10, max_seconds=60, timeout_seconds=30,
                              chunk_chars=30000, merge_chars=30000, batch_chars=50000, batch_docs=12)


class TemplateTest(unittest.TestCase):
    def test_new_maintainer_imports_existing_catalog_before_republishing(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            root = base / "source"
            root.mkdir()
            (root / "Guide.txt").write_text("Project code PX-42. Guide for launch reviews.", encoding="utf-8")
            (root / "Old.txt").write_text("Project code PX-42. Older process notes.", encoding="utf-8")
            original = example(root, base / "old-state")
            directory_index.scan(scan_args(original))
            original_enrich = enrich_args(original)
            original_enrich.batch_docs = 1
            directory_enrich.run(original_enrich, lambda *_: note("PX-42"))
            directory_index.publish(argparse.Namespace(config_data=original, state_dir=original["state_dir"],
                                                       output_root=str(root)))

            successor = example(root, base / "new-state")
            (root / "Old.txt").unlink()
            directory_index.scan(scan_args(successor))
            directory_enrich.run(enrich_args(successor), lambda *_: note("PX-42"))
            publish = argparse.Namespace(config_data=successor, state_dir=successor["state_dir"],
                                         output_root=str(root))
            with self.assertRaisesRegex(ValueError, "directory_handoff.py"):
                directory_index.publish(publish)
            catalog_status = root / "Search Catalog" / "catalog-status.txt"
            healthy_status = catalog_status.read_text(encoding="utf-8")
            catalog_status.write_text(healthy_status.replace("health: complete", "health: partial"),
                                      encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "one complete generation"):
                directory_handoff.prepare(successor)
            catalog_status.write_text(healthy_status, encoding="utf-8")
            prepared = directory_handoff.prepare(successor)
            self.assertEqual(prepared["file_count"], 2)
            self.assertTrue(Path(prepared["snapshot"]).is_dir())
            with self.assertRaisesRegex(ValueError, "removed paths need SharePoint verification"):
                directory_index.publish(publish)
            self.assertEqual(set(directory_index.load_json(base / "new-state" / "published_paths.json", {})["paths"]),
                             {"Guide.txt", "Old.txt"})

    def test_config_rejects_unsafe_paths_and_field_keys(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            cfg = example(base / "source", base / "state")
            for changed in ({"source_folder": "../Other"}, {"state_dir": str(base / "source" / "state")},
                            {"custom_fields": [{"key": "summary", "label": "Bad", "guidance": "Bad"}]}):
                with self.subTest(changed=changed), self.assertRaises(ValueError):
                    settings.validate_config(cfg | changed)

    def test_generation_uses_subfolder_and_custom_field(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            cfg = example(base / "source", base / "state")
            files = setup.generate(cfg, base)
            self.assertEqual(len(files), 5)
            manifest = json.loads((base / "plugin" / "plugin.json").read_text(encoding="utf-8"))
            skill = (base / "plugin" / "skills" / "find-directory-files" / "SKILL.md").read_text(encoding="utf-8")
            marketplace = json.loads((base / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["name"], "project-search")
            self.assertEqual(marketplace["plugins"][0]["source"]["path"], "./plugin")
            self.assertIn("Projects/Current/Search Catalog/cards", skill)
            self.assertIn("Project code (`project_code`)", skill)
            self.assertIn("/sites/Team Docs/Shared Documents/Projects/Current", skill)

    def test_synthetic_scan_enrich_publish_and_field_change(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            root, state = base / "source", base / "state"
            root.mkdir()
            (root / "Launch.txt").write_text("Project code PX-42. This is the launch checklist.", encoding="utf-8")
            cfg = example(root, state)
            directory_index.scan(scan_args(cfg))
            self.assertEqual(len(directory_index.pending_items(state, cfg)), 1)
            result = directory_enrich.run(enrich_args(cfg), lambda *_: note("PX-42"))
            self.assertEqual(result["remaining"], 0)
            directory_index.validate(state, cfg)
            directory_index.publish(argparse.Namespace(config_data=cfg, state_dir=str(state), output_root=str(root)))
            catalog = root / "Search Catalog"
            index = json.loads((catalog / "search_index.yaml").read_text(encoding="utf-8"))
            record = index["folder_bundles"][0]["assets"][0]
            self.assertEqual(record["project_code"], ["PX-42"])
            self.assertEqual(record["path"], "Projects/Current/Launch.txt")
            self.assertIn("/Projects/Current/Launch.txt", record["file_url"])
            card = (catalog / "cards" / record["id"][:2] / f"{record['id']}.txt").read_text(encoding="utf-8")
            self.assertIn("Project code: PX-42", card)
            self.assertTrue(any("px\t" in path.read_text(encoding="utf-8")
                                for path in (catalog / "terms").glob("*.txt")))
            directory_index.scan(scan_args(cfg))
            self.assertEqual(len(directory_index.load_json(state / "manifest.json", {})["files"]), 1)
            changed = settings.validate_config({**cfg, "custom_fields": [{**cfg["custom_fields"][0],
                "guidance": "Use only a project code stated in the file body."}]})
            with self.assertRaisesRegex(ValueError, "custom field definitions changed"):
                directory_index.validate(state, changed)
            moved = settings.validate_config({**cfg, "source_folder": "Projects/Other"})
            with self.assertRaisesRegex(ValueError, "source location changed"):
                directory_index.validate(state, moved)
            directory_index.scan(scan_args(changed))
            self.assertEqual(len(directory_index.pending_items(state, changed)), 1)
            with self.assertRaisesRegex(ValueError, "lack model analysis"):
                directory_index.publish(argparse.Namespace(config_data=changed, state_dir=str(state), output_root=str(root)))
            self.assertEqual(directory_enrich.run(enrich_args(changed), lambda *_: note("PX-42"))["remaining"], 0)

    def test_image_custom_field_and_local_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            root, state = base / "source", base / "state"
            root.mkdir()
            Image.new("RGB", (30, 30), "white").save(root / "Plan.png")
            cfg = example(root, state)
            directory_index.scan(scan_args(cfg))
            args = argparse.Namespace(config_data=cfg, state_dir=str(state), ca_certificate=None,
                                      max_files=10, max_seconds=60, timeout_seconds=30)
            def image_note(*_args, **_kwargs):
                return {"visible_text": "PX-42", "visual_description": "A project plan diagram.",
                        **note("PX-42")}
            self.assertEqual(directory_vision.run(args, image_note)["remaining_image_hashes"], 0)
            self.assertEqual(directory_index.validate(state, cfg)["files"], 1)
            changed = settings.validate_config({**cfg, "custom_fields": [{**cfg["custom_fields"][0],
                "guidance": "Copy only visible project codes."}]})
            directory_index.scan(scan_args(changed))
            with self.assertRaisesRegex(ValueError, "run directory_vision.py"):
                directory_enrich.run(enrich_args(changed), lambda *_: self.fail("text model must not run"))
            args.config_data = changed
            self.assertEqual(directory_vision.run(args, image_note)["remaining_image_hashes"], 0)
            self.assertEqual(directory_index.validate(state, changed)["files"], 1)
            source = root / "Copy.txt"
            source.write_text("Saved SharePoint text copy.", encoding="utf-8")
            supplied = base / "copy.txt"
            supplied.write_bytes(source.read_bytes())
            modified = datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).isoformat()
            cached = directory_recover.recover(cfg, "Copy.txt", str(supplied), modified)
            self.assertEqual(cached.read_bytes(), supplied.read_bytes())
            with self.assertRaises(ValueError):
                directory_recover.recover(cfg, "../Copy.txt", str(supplied), modified)


if __name__ == "__main__":
    unittest.main()
