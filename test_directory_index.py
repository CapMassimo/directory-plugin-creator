import argparse
import json
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import directory_index as index


def analysis(sha):
    return {"sha256": sha, "summary": "This document explains compliance and risk controls for audit work.",
            "key_terms": ["AI", "compliance controls"], "user_intents": ["find control guidance"],
            "use_cases": ["audit planning"], "confidence": "high",
            "artifact_type": "guidance"}


class IndexFlowTest(unittest.TestCase):
    def test_office_xml_labels_without_content_are_exceptions(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root, state = base / "library", base / "state"
            root.mkdir()
            deck = root / "empty.pptx"
            with ZipFile(deck, "w") as archive:
                archive.writestr("ppt/slides/slide1.xml", "<root />")
            options = argparse.Namespace(source_root=str(root), state_dir=str(state),
                                         site_url="https://example.sharepoint.com/sites/ExampleTeam",
                                         min_retention=0.8, rehash_all=False)
            index.scan(options)
            manifest = json.loads((state / "manifest.json").read_text())
            entry = manifest["files"]["empty.pptx"]
            self.assertEqual(entry["extraction_status"], "exception")
            self.assertEqual(index.pending_items(state), [])
            # An older marker-only cache must not restore a false complete record.
            (state / "extracts" / f"{entry['sha256']}.txt").write_text("[ppt/slides/slide1.xml]\n")
            entry["extraction_status"] = "complete"
            entry.pop("exception")
            (state / "manifest.json").write_text(json.dumps(manifest))
            index.scan(options)
            manifest = json.loads((state / "manifest.json").read_text())
            self.assertEqual(manifest["files"]["empty.pptx"]["extraction_status"], "exception")

    def test_verified_recovery_resumes_unreadable_onedrive_placeholder(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root, state = base / "library", base / "state"
            root.mkdir()
            source = root / "deck.txt"
            source.write_text("Risk and compliance controls described in this document.")
            recovered = state / "recovery" / (hashlib.sha256(b"deck.txt").hexdigest() + ".txt")
            recovered.parent.mkdir(parents=True)
            shutil.copyfile(source, recovered)
            stat = source.stat()
            state.joinpath("recovery_manifest.json").write_text(json.dumps({"deck.txt": {
                "filename": recovered.name, "sha256": index.file_hash(recovered),
                "fingerprint": f"{stat.st_size}:{stat.st_mtime_ns}"}}))
            original_hash, original_extract = index.file_hash, index.extract

            def unavailable_hash(path):
                if path == index.local_path(source):
                    raise OSError(22, "placeholder unavailable")
                return original_hash(path)

            def unavailable_extract(path):
                if path == index.local_path(source):
                    raise OSError(22, "placeholder unavailable")
                return original_extract(path)

            with patch.object(index, "file_hash", side_effect=unavailable_hash), patch.object(
                    index, "extract", side_effect=unavailable_extract):
                index.scan(argparse.Namespace(source_root=str(root), state_dir=str(state),
                           site_url="https://example.sharepoint.com/sites/ExampleTeam",
                           min_retention=0.8, rehash_all=False))
            manifest = json.loads((state / "manifest.json").read_text())
            self.assertEqual(manifest["files"]["deck.txt"]["extraction_status"], "complete")
            self.assertEqual((state / "extracts" / f"{original_hash(source)}.txt").read_text(), source.read_text())

    def test_hash_retries_when_onedrive_finishes_hydrating(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root, state = base / "library", base / "state"
            root.mkdir()
            source = root / "guide.txt"
            source.write_text("Initial local placeholder content")
            original_hash = index.file_hash
            changed = False

            def hydrated(path):
                nonlocal changed
                if not changed:
                    source.write_text("Hydrated full compliance guidance for reviewers.")
                    changed = True
                return original_hash(path)

            with patch.object(index, "file_hash", side_effect=hydrated):
                index.scan(argparse.Namespace(source_root=str(root), state_dir=str(state),
                                           site_url="https://example.sharepoint.com/sites/ExampleTeam",
                                           min_retention=0.8, rehash_all=False))
            manifest = json.loads((state / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["files"]["guide.txt"]["sha256"], original_hash(source))

    def test_interrupted_extract_write_resumes_without_partial_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root, state = base / "library", base / "state"
            root.mkdir()
            source = root / "guide.txt"
            source.write_text("Complete guidance for a compliance review.")
            sha = index.file_hash(source)
            options = argparse.Namespace(source_root=str(root), state_dir=str(state),
                                         site_url="https://example.sharepoint.com/sites/ExampleTeam",
                                         min_retention=0.8, rehash_all=False)
            replace = os.replace

            def interrupted(staged, destination):
                if str(destination).endswith(f"{sha}.txt"):
                    raise OSError("synthetic interruption")
                return replace(staged, destination)

            with patch.object(index.os, "replace", side_effect=interrupted):
                with self.assertRaisesRegex(ValueError, "scan incomplete"):
                    index.scan(options)
            self.assertFalse((state / "extracts" / f"{sha}.txt").exists())
            index.scan(options)
            self.assertEqual((state / "extracts" / f"{sha}.txt").read_text(),
                             "Complete guidance for a compliance review.")

    def test_broad_term_postings_cover_multiple_folders(self):
        records = []
        for folder in ("Archive/02. Operations", "General/06. Risk"):
            for number in range(20):
                path = f"{folder}/Project {number}.pdf"
                records.append({"id": f"{len(records):064x}", "name": f"Project {number}.pdf",
                                "path": path, "folder": folder, "file_format": "pdf",
                                "key_terms": [], "user_intents": [], "use_cases": [], "summary": None})
        with patch.object(index, "MAX_TERM_POSTINGS", 6):
            found = index.term_postings(records, {"custom_fields": []})["project"]
        self.assertEqual(len(found), 6)
        self.assertEqual({item[2].split("/")[0] for item in found}, {"Archive", "General"})

    def test_publish_rejects_a_source_removed_after_scan(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root, state = base / "Shared Documents", base / "state"
            root.mkdir()
            source = root / "guide.txt"
            source.write_text("Current compliance guidance for reviewers.")
            index.scan(argparse.Namespace(source_root=str(root), state_dir=str(state),
                       site_url="https://example.sharepoint.com/sites/ExampleTeam",
                       min_retention=0.8, rehash_all=False))
            task = index.pending_items(state)[0]
            payload = base / "analysis.json"
            payload.write_text(json.dumps(analysis(task["sha256"])))
            index.apply(argparse.Namespace(state_dir=str(state), input=str(payload)))
            source.unlink()
            with self.assertRaisesRegex(ValueError, "source missing or unreadable since scan"):
                index.publish(argparse.Namespace(state_dir=str(state), output_root=str(root)))
            self.assertFalse((root / "Directory Search Index").exists())

    def test_sparse_xlsx_skips_blank_formatted_rows(self):
        from openpyxl import Workbook

        with tempfile.TemporaryDirectory() as temporary:
            workbook = Workbook()
            sheet = workbook.active
            sheet["A1"] = "Control"
            sheet["A1000"] = "Evidence"
            for suffix in (".xlsx", ".xlsm"):
                path = Path(temporary) / ("sparse" + suffix)
                workbook.save(path)
                self.assertEqual(index.extract(path).splitlines(), ["[Sheet]", "Control", "Evidence"])

    def test_powerpoint_extract_includes_chart_and_smartart_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            for suffix in (".pptx", ".potx"):
                path = Path(temporary) / ("deck" + suffix)
                with ZipFile(path, "w") as archive:
                    archive.writestr("ppt/slides/slide1.xml", "<root><t>Risk overview</t></root>")
                    archive.writestr("ppt/charts/chart1.xml", "<root><t>Control trend</t><v>2026</v></root>")
                    archive.writestr("ppt/diagrams/data1.xml", "<root><t>Review approval</t></root>")
                content = index.extract(path)
                self.assertIn("Risk overview", content)
                self.assertIn("Control trend", content)
                self.assertIn("Review approval", content)

    @unittest.skipUnless(os.name == "nt", "Windows extended paths only")
    def test_scan_includes_path_longer_than_260_characters(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root, state = base / "Shared Documents", base / "state"
            deep = root / ("a" * 90) / ("b" * 90) / ("c" * 90)
            index.local_path(deep).mkdir(parents=True)
            index.local_path(deep / "deep.txt").write_text("Deeply nested control guidance is readable.")
            self.assertGreater(len(str(deep / "deep.txt")), 260)
            options = argparse.Namespace(source_root=str(root), state_dir=str(state),
                                         site_url="https://example.sharepoint.com/sites/ExampleTeam",
                                         min_retention=0.8, rehash_all=False)
            try:
                index.scan(options)
                self.assertEqual(len(json.loads((state / "manifest.json").read_text())["files"]), 1)
            finally:
                shutil.rmtree(index.local_path(root))

    def test_path_metadata_is_cautious_and_separate_from_archive_location(self):
        found = index.path_metadata("Archive/Safety Training Final v2.0 2025-03-10.pptx")
        self.assertEqual(found["location_class"], "archive")
        self.assertEqual(found["revision_status"], "final")
        self.assertEqual(found["version_label"], {"value": "v2.0", "source": "filename"})
        self.assertEqual(found["date_label"], {"value": "2025-03-10", "source": "filename"})
        self.assertEqual(found["document_family"], "safety training")
        self.assertEqual(index.path_metadata("General/Safety Training v1.1.pptx")["document_family"], "safety training")
        self.assertEqual(index.path_metadata("General/Safety Training.pptx")["revision_status"], "unknown")

    def test_removed_path_requires_exact_current_scan_confirmation(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root, state = base / "Shared Documents", base / "state"
            root.mkdir()
            (root / "old.txt").write_text("The old guidance explains control evidence for this process.")
            (root / "keep.txt").write_text("The current guidance explains operating model controls.")
            (root / ".849C9593-D756-4E56-8D6E-42412F2A707B").write_bytes(b"OneDrive metadata")
            options = argparse.Namespace(source_root=str(root), state_dir=str(state),
                                         site_url="https://example.sharepoint.com/sites/ExampleTeam",
                                         min_retention=0.8, rehash_all=False)
            index.scan(options)
            self.assertEqual(len(json.loads((state / "manifest.json").read_text())["files"]), 2)
            tasks = index.pending_items(state)
            payload = [analysis(item["sha256"]) for item in tasks]
            input_path = base / "analysis.json"
            input_path.write_text(json.dumps(payload))
            index.apply(argparse.Namespace(state_dir=str(state), input=str(input_path)))
            index.publish(argparse.Namespace(state_dir=str(state), output_root=str(root)))
            (root / "old.txt").unlink()
            index.scan(options)
            with self.assertRaisesRegex(ValueError, "need SharePoint verification"):
                index.publish(argparse.Namespace(state_dir=str(state), output_root=str(root)))
            # A second successful scan cannot erase an unconfirmed removal.
            index.scan(options)
            scan_id = json.loads((state / "manifest.json").read_text())["scan_id"]
            confirmation = base / "confirmation.json"
            confirmation.write_text(json.dumps({"scan_id": "stale", "removed_paths": ["old.txt"]}))
            with self.assertRaisesRegex(ValueError, "exact removed path set"):
                index.publish(argparse.Namespace(state_dir=str(state), output_root=str(root), confirm_removals=str(confirmation)))
            confirmation.write_text(json.dumps({"scan_id": scan_id, "removed_paths": ["wrong.txt"]}))
            with self.assertRaisesRegex(ValueError, "exact removed path set"):
                index.publish(argparse.Namespace(state_dir=str(state), output_root=str(root), confirm_removals=str(confirmation)))
            confirmation.write_text(json.dumps({"scan_id": scan_id, "removed_paths": ["old.txt"]}))
            index.publish(argparse.Namespace(state_dir=str(state), output_root=str(root), confirm_removals=str(confirmation)))
            self.assertEqual(json.loads((state / "published_paths.json").read_text())["paths"], ["keep.txt"])

    def test_failed_hydration_keeps_checkpoint_and_reuses_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root, state = base / "Shared Documents", base / "state"
            root.mkdir()
            (root / "guide.txt").write_text("A source document with enough content to analyze.")
            options = argparse.Namespace(source_root=str(root), state_dir=str(state),
                                         site_url="https://example.sharepoint.com/sites/ExampleTeam",
                                         min_retention=0.8, rehash_all=False)
            with patch.object(index, "extract", side_effect=OSError("cloud hydration unavailable")):
                with self.assertRaisesRegex(ValueError, "scan incomplete"):
                    index.scan(options)
            self.assertTrue((state / "scan_checkpoint.sqlite").exists())
            self.assertFalse((state / "manifest.json").exists())
            with patch.object(index, "file_hash", side_effect=AssertionError("must reuse checkpoint hash")):
                index.scan(options)
            self.assertFalse((state / "scan_checkpoint.sqlite").exists())
            self.assertEqual(len(index.pending_items(state)), 1)

    def test_named_exception_is_indexed_without_fake_analysis(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root, state = base / "Shared Documents", base / "state"
            root.mkdir()
            (root / "legacy.msg").write_bytes(b"binary mail container")
            options = argparse.Namespace(source_root=str(root), state_dir=str(state),
                                         site_url="https://example.sharepoint.com/sites/ExampleTeam",
                                         min_retention=0.8, rehash_all=False)
            index.scan(options)
            self.assertEqual(index.pending_items(state), [])
            self.assertEqual(index.validate(state)["exceptions"], 1)
            index.publish(argparse.Namespace(state_dir=str(state), output_root=str(root)))
            catalog = json.loads((root / "Directory Search Index" / "search_index.yaml").read_text())
            item = catalog["folder_bundles"][0]["assets"][0]
            self.assertEqual(item["extraction_status"], "exception")
            self.assertIsNone(item["summary"])
            self.assertIn("unsupported file type", item["exception"])

    def test_scan_enrich_publish_and_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root, state = base / "Shared Documents", base / "state"
            root.mkdir()
            (root / "Archive").mkdir()
            (root / "Archive" / "policy.txt").write_text("The compliance policy records required controls and testing responsibilities.")
            docx = root / "Guide.docx"
            with ZipFile(docx, "w") as archive:
                archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Risk assessment instructions for auditors</w:t></w:r></w:p></w:document>')
            options = argparse.Namespace(source_root=str(root), state_dir=str(state),
                                         site_url="https://example.sharepoint.com/sites/ExampleTeam",
                                         min_retention=0.8, rehash_all=False)
            index.scan(options)
            tasks = index.pending_items(state)
            self.assertEqual(len(tasks), 2)
            with self.assertRaisesRegex(ValueError, "lack model analysis"):
                index.validate(state)
            enrichment = [analysis(task["sha256"]) for task in tasks]
            input_path = base / "enrichment.json"
            input_path.write_text(json.dumps(enrichment))
            index.apply(argparse.Namespace(state_dir=str(state), input=str(input_path)))
            self.assertEqual(index.validate(state)["files"], 2)
            index.publish(argparse.Namespace(state_dir=str(state), output_root=str(root)))
            published = root / "Directory Search Index"
            self.assertTrue((published / "catalog-status.txt").exists())
            catalog = json.loads((published / "search_index.yaml").read_text())
            self.assertEqual(catalog["index_metadata"]["asset_count"], 2)
            assets = [asset for bundle in catalog["folder_bundles"] for asset in bundle["assets"]]
            self.assertTrue(all("summary" in asset and "related_documents" in asset for asset in assets))
            self.assertEqual(next(asset for asset in assets if asset["name"] == "Guide.docx")["file_format"], "docx")
            self.assertIn("excel", index.term_postings([dict(assets[0], file_format="xlsx")], {"custom_fields": []}))
            self.assertIn("ExampleTeam", assets[0]["file_url"])
            self.assertIn("%2Fsites%2FExampleTeam", assets[0]["folder_url"])
            manifest_text = (published / "terms" / "manifest.txt").read_text()
            self.assertIn("terms/gui.txt\t", manifest_text)
            self.assertIn("terms/ai_.txt\t", manifest_text)
            mini_text = (published / "terms" / "manifests" / "gu.txt").read_text()
            self.assertIn("terms/gui.txt\t", mini_text)
            self.assertIn("# catalog_generation_id: ", mini_text)
            guide_shard = published / "terms" / "gui.txt"
            self.assertIn("guide\t", guide_shard.read_text())
            self.assertTrue(guide_shard.read_text().startswith("# generation_id: "))
            first_shard_mtime = guide_shard.stat().st_mtime_ns
            card_file = next((published / "cards").glob("*/*.txt"))
            first_mtime = card_file.stat().st_mtime_ns
            index.scan(options)
            index.publish(argparse.Namespace(state_dir=str(state), output_root=str(root)))
            self.assertEqual(card_file.stat().st_mtime_ns, first_mtime)
            self.assertEqual(guide_shard.stat().st_mtime_ns, first_shard_mtime)
            # Duplicate and rename reuse model analysis by content hash.
            (root / "duplicate.txt").write_bytes((root / "Archive" / "policy.txt").read_bytes())
            old_card_id = next(asset["id"] for asset in assets if asset["name"] == "Guide.docx")
            docx.rename(root / "Archive" / "Renamed.docx")
            index.scan(options)
            self.assertEqual(index.pending_items(state), [])
            self.assertEqual(index.validate(state)["files"], 3)
            changes = json.loads((state / "manifest.json").read_text())["last_scan_changes"]
            self.assertEqual(changes["moved"], [{"from": "Guide.docx", "to": "Archive/Renamed.docx"}])
            with self.assertRaisesRegex(ValueError, "need SharePoint verification"):
                index.publish(argparse.Namespace(state_dir=str(state), output_root=str(root)))
            scan_id = json.loads((state / "manifest.json").read_text())["scan_id"]
            confirmation = base / "rename_confirmation.json"
            confirmation.write_text(json.dumps({"scan_id": scan_id, "removed_paths": ["Guide.docx"]}))
            index.publish(argparse.Namespace(state_dir=str(state), output_root=str(root), confirm_removals=str(confirmation)))
            catalog = json.loads((published / "search_index.yaml").read_text())
            assets = {asset["path"]: asset for bundle in catalog["folder_bundles"] for asset in bundle["assets"]}
            self.assertNotIn("Guide.docx", assets)
            self.assertEqual(assets["Archive/Renamed.docx"]["folder"], "Archive")
            self.assertIn("/Archive/Renamed.docx", assets["Archive/Renamed.docx"]["file_url"])
            self.assertFalse((published / "cards" / old_card_id[:2] / f"{old_card_id}.txt").exists())
            old_term_shard = published / "terms" / "gui.txt"
            if old_term_shard.exists():
                self.assertNotIn("guide\t", old_term_shard.read_text())
            self.assertEqual([other["path"] for other in assets["duplicate.txt"]["duplicate_locations"]],
                             ["Archive/policy.txt"])
            self.assertNotIn("named_entities", assets["duplicate.txt"])
            self.assertIn("Artifact type: guidance", (published / "cards" / assets["duplicate.txt"]["id"][:2] /
                                                    f"{assets['duplicate.txt']['id']}.txt").read_text())
            # Edited content needs new analysis and cannot publish until applied.
            (root / "duplicate.txt").write_text("Changed testing procedures with more detailed controls.")
            index.scan(options)
            self.assertEqual(len(index.pending_items(state)), 1)
            with self.assertRaisesRegex(ValueError, "lack model analysis"):
                index.validate(state)
            # An anomalous drop retains the previous manifest.
            (root / "Archive" / "policy.txt").unlink()
            (root / "Archive" / "Renamed.docx").unlink()
            with self.assertRaisesRegex(ValueError, "anomalous drop"):
                index.scan(options)
            self.assertEqual(len(json.loads((state / "manifest.json").read_text())["files"]), 3)

    def test_large_term_bucket_splits_but_keeps_exact_prefix(self):
        records = [{"id": f"{i:064x}", "name": name + ".txt", "path": name + ".txt",
                    "folder": "", "file_format": "", "key_terms": [], "user_intents": [], "use_cases": [], "summary": None}
                   for i, name in enumerate(("ris", "risk", "rising"), 1)]
        with patch.object(index, "MAX_SHARD_BYTES", 200):
            shards = index.term_shards(records, {"custom_fields": []})
        self.assertIn("terms/ris.txt", shards)
        self.assertIn("ris\t", shards["terms/ris.txt"])
        self.assertTrue(any(name != "terms/ris.txt" for name in shards))


if __name__ == "__main__":
    unittest.main()
