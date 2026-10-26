import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import directory_enrich
import directory_index
import directory_profile
import config as settings


def note(label):
    return {"summary": f"This source section describes {label} and its contents.",
            "key_terms": ["Directory", label], "user_intents": [f"Find {label}"],
            "use_cases": [f"Review {label}"], "confidence": "high",
            "artifact_type": "unknown"}


def test_config(state):
    return settings.validate_config({
        "source_root": str(state.parent / "library"), "state_dir": str(state),
        "site_url": "https://example.sharepoint.com/sites/ExampleTeam",
        "library_path": "Shared Documents", "source_folder": "", "index_folder": "Directory Search Index",
        "freshness_hours": 48, "plugin": {"name": "example-search", "display_name": "Example Search",
        "description": "Find example files", "developer_name": "Example team"}, "custom_fields": []})


def save_manifest(state, manifest):
    cfg = test_config(state)
    directory_index.save_json(state / "manifest.json", {
        **manifest, "settings": cfg, "schema_hash": settings.schema_hash(cfg),
        "source_identity": (cfg["source_root"], cfg["site_url"], cfg["library_path"],
                            cfg["source_folder"], cfg["index_folder"])})


def enrichment(state):
    return state / "enrichment" / settings.schema_hash(test_config(state))


class EnrichmentTest(unittest.TestCase):
    def test_sparse_pdf_is_deferred_and_stays_unpublished(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            sha = hashlib.sha256(b"image-only PDF").hexdigest()
            (state / "extracts").mkdir()
            source = state / "extracts" / f"{sha}.txt"
            source.write_text("October 23", encoding="utf-8")
            save_manifest(state, {"files": {"General/image-only.pdf": {
                "sha256": sha, "extraction_status": "complete"}}})
            cfg = test_config(state)
            args = argparse.Namespace(config_data=cfg, state_dir=str(state), ca_certificate=None,
                                      max_files=1, max_seconds=60, timeout_seconds=10,
                                      chunk_chars=30000, merge_chars=30000,
                                      batch_chars=50000, batch_docs=12)
            result = directory_enrich.run(args, lambda *_: self.fail("sparse text sent to model"))
            self.assertEqual((result["remaining"], result["deferred"]), (1, 1))
            self.assertFalse((state / "analyses.json").exists())
            source.write_text("This recovered document explains a detailed review process " * 2,
                              encoding="utf-8")
            self.assertFalse(directory_enrich.is_deferred(state, directory_index.pending_items(state, cfg)[0]))

    def test_cli_timeout_retries_once_with_longer_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "response.json"
            limits = []

            def fake_process(command, prompt, limit):
                limits.append(limit)
                if len(limits) == 1:
                    raise subprocess.TimeoutExpired(command, limit)
                output.write_text(json.dumps(note("recovered")), encoding="utf-8")
                return 0, ""

            budget = directory_enrich.Budget(100, 10)
            with patch.object(directory_enrich.shutil, "which", return_value="codex"), \
                 patch.object(directory_enrich, "check_login"), \
                 patch.object(directory_enrich, "_cli_process", side_effect=fake_process), \
                 patch.object(directory_enrich.time, "sleep"):
                result = directory_enrich.invoke_codex({"kind": "source chunk", "text": "sample"},
                                                  Path(temp) / "schema.json", output, budget)
            self.assertEqual(result["summary"], note("recovered")["summary"])
            self.assertEqual(limits, [10, 20])
            self.assertEqual((budget.timeouts, budget.calls), (1, 1))

    def test_large_identical_workbook_extracts_share_one_profile_analysis(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            (state / "extracts").mkdir()
            content = "[Orders]\nOrder ID | Status | Amount\n" + "1 | 0 | 25\n" * 50
            files = {}
            for number in range(2):
                sha = hashlib.sha256(f"binary workbook {number}".encode()).hexdigest()
                (state / "extracts" / f"{sha}.txt").write_text(content, encoding="utf-8")
                files[f"General/workbook{number}.xlsx"] = {"sha256": sha, "extraction_status": "complete"}
            save_manifest(state, {"files": files})
            args = argparse.Namespace(state_dir=str(state), max_files=2, max_seconds=100,
                                      timeout_seconds=10, chunk_chars=10000, merge_chars=10000,
                                      batch_chars=10000, batch_docs=12)
            calls = []

            def model(payload, *args):
                calls.append(payload)
                return note("orders workbook")

            with patch.object(directory_profile, "MIN_CHARS", 100):
                first = directory_enrich.run(args, model)
                second = directory_enrich.run(args, lambda *a: self.fail("cached analysis should be reused"))
            self.assertEqual((first["completed"], first["remaining"]), (1, 1))
            self.assertEqual((second["completed"], second["remaining"]), (1, 0))
            self.assertEqual(len(calls), 1)
            profile = json.loads(next((state / "profiles").glob("*.txt")).read_text(encoding="utf-8"))
            self.assertEqual(profile["sheets"][0]["nonempty_rows"], 51)
            self.assertEqual(profile["sheets"][0]["early_labels"], ["Order ID | Status | Amount"])

    def test_bad_batch_retries_then_analyzes_documents_individually(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            (state / "extracts").mkdir()
            files = {}
            for number in range(2):
                content = f"Risk control document {number} with distinct content."
                sha = hashlib.sha256(content.encode()).hexdigest()
                (state / "extracts" / f"{sha}.txt").write_text(content)
                files[f"doc{number}.txt"] = {"sha256": sha, "extraction_status": "complete"}
            save_manifest(state, {"files": files})
            args = argparse.Namespace(state_dir=str(state), max_files=2, max_seconds=100,
                                      timeout_seconds=10, chunk_chars=1000, merge_chars=1000,
                                      batch_chars=1000, batch_docs=12)
            batch_calls = []
            single_calls = []

            def bad_batch(payload, *args):
                batch_calls.append(payload)
                return {"analyses": [{"sha256": item["sha256"], **(note("risk") | {"key_terms": []})}
                                     for item in payload["documents"]]}

            def good_single(payload, *args):
                single_calls.append(payload)
                return note("risk")

            with contextlib.redirect_stdout(io.StringIO()):
                result = directory_enrich.run(args, good_single, bad_batch)
            self.assertEqual((len(batch_calls), len(single_calls)), (2, 2))
            self.assertEqual((result["completed"], result["remaining"]), (2, 0))

    def test_completed_final_checkpoint_is_applied_without_another_model_call(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            content = "Onboarding control review process."
            sha = hashlib.sha256(content.encode()).hexdigest()
            (state / "extracts").mkdir()
            (state / "extracts" / f"{sha}.txt").write_text(content)
            save_manifest(state, {"files": {
                "General/review.txt": {"sha256": sha, "extraction_status": "complete"}}})
            directory_index.save_json(enrichment(state) / sha / "final.json", {"sha256": sha, **note("Onboarding")})
            args = argparse.Namespace(state_dir=str(state), max_files=1, max_seconds=60,
                                      timeout_seconds=30, chunk_chars=30000, merge_chars=30000,
                                      batch_chars=50000, batch_docs=12)
            with contextlib.redirect_stdout(io.StringIO()):
                result = directory_enrich.run(args, lambda *a, **k: self.fail("model called"),
                                         lambda *a, **k: self.fail("batch model called"))
            self.assertEqual((result["completed"], result["remaining"], result["model_calls"]), (1, 0, 0))

    def test_all_chunks_are_processed_and_checkpoint_resumes(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            content = "A" * 37 + "B" * 37 + "C" * 37
            sha = hashlib.sha256(content.encode()).hexdigest()
            extracts = state / "extracts"
            extracts.mkdir()
            (extracts / f"{sha}.txt").write_text(content, encoding="utf-8")
            save_manifest(state, {"files": {
                "General/sample.txt": {"sha256": sha, "extraction_status": "complete"}}})
            args = argparse.Namespace(state_dir=str(state), max_files=1, max_seconds=100,
                                      timeout_seconds=10, chunk_chars=40, merge_chars=400,
                                      batch_chars=100, batch_docs=12)
            seen = []

            def interrupted(payload, schema, output, budget):
                if len(seen) == 1:
                    raise RuntimeError("synthetic interruption")
                seen.append(payload)
                return note("first chunk")

            with self.assertRaisesRegex(RuntimeError, "synthetic interruption"):
                directory_enrich.run(args, interrupted)
            self.assertEqual(len(seen), 1)
            self.assertFalse((state / "enrichment" / ".lock").exists())

            def resumed(payload, schema, output, budget):
                seen.append(payload)
                return note("merged" if payload["kind"] == "merge evidence notes" else "next chunk")

            with contextlib.redirect_stdout(io.StringIO()):
                result = directory_enrich.run(args, resumed)
            self.assertEqual(result["completed"], 1)
            self.assertEqual(result["remaining"], 0)
            source_payloads = [p for p in seen if p["kind"] == "source chunk"]
            self.assertEqual("".join(p["text"] for p in source_payloads), content)
            self.assertEqual(len(source_payloads), 3)
            self.assertIn(sha, json.loads((state / "analyses.json").read_text(encoding="utf-8")))
            self.assertTrue((enrichment(state) / sha / "final.json").exists())
            old = json.loads((state / "analyses.json").read_text(encoding="utf-8"))
            old[sha].pop("artifact_type")
            directory_index.save_json(state / "analyses.json", old)
            self.assertEqual(len(directory_index.pending_items(state)), 1)
            with self.assertRaisesRegex(ValueError, "lack model analysis"):
                directory_index.validate(state)
            final_path = enrichment(state) / sha / "final.json"
            stale = json.loads(final_path.read_text(encoding="utf-8"))
            stale.pop("artifact_type")
            directory_index.save_json(final_path, stale)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(directory_enrich.run(args, resumed)["remaining"], 0)

    def test_batch_budget_exhaustion_keeps_partial_document_notes(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            content = "A" * 41 + "B" * 41
            sha = hashlib.sha256(content.encode()).hexdigest()
            (state / "extracts").mkdir()
            (state / "extracts" / f"{sha}.txt").write_text(content, encoding="utf-8")
            save_manifest(state, {"files": {
                "General/long.txt": {"sha256": sha, "extraction_status": "complete"}}})
            args = argparse.Namespace(state_dir=str(state), max_files=1, max_seconds=100,
                                      timeout_seconds=10, chunk_chars=41, merge_chars=1000,
                                      batch_chars=50, batch_docs=12)
            calls = []

            def first_run(payload, *args):
                calls.append(payload)
                if len(calls) == 2:
                    raise directory_enrich.RunBudgetExhausted("batch ended")
                return note("first section")

            result = directory_enrich.run(args, first_run)
            self.assertEqual((result["completed"], result["remaining"]), (0, 1))
            self.assertTrue((enrichment(state) / sha / "level-0-00000.json").exists())
            self.assertFalse((state / "enrichment" / ".lock").exists())

            resumed = []
            result = directory_enrich.run(args, lambda payload, *args: resumed.append(payload) or note("rest"))
            self.assertEqual((result["completed"], result["remaining"]), (1, 0))
            self.assertEqual(len([p for p in resumed if p["kind"] == "source chunk"]), 1)

    def test_rejects_incomplete_model_response(self):
        with self.assertRaisesRegex(ValueError, "unexpected fields"):
            directory_enrich.check_note({"summary": "a"})
        with self.assertRaisesRegex(ValueError, "omitted or duplicated"):
            directory_enrich.check_batch({"analyses": []}, {"abc"})
        cfg = {"custom_fields": [{"key": "region", "label": "Region", "guidance": "Explicit region"}]}
        bad = note("bad") | {"region": ["", "North"]}
        with self.assertRaisesRegex(ValueError, "region"):
            directory_enrich.check_note(bad, cfg)
        schema = directory_enrich.note_schema(cfg)
        self.assertEqual(set(schema["required"]), set(schema["properties"]))

    def test_ca_certificate_is_scoped_to_run(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ca.pem"
            path.write_text("public cert", encoding="ascii")
            previous = os.environ.get("CODEX_CA_CERTIFICATE")
            with directory_enrich.ca_environment(str(path)):
                self.assertEqual(os.environ["CODEX_CA_CERTIFICATE"], str(path))
            self.assertEqual(os.environ.get("CODEX_CA_CERTIFICATE"), previous)

    def test_multiple_small_files_share_one_call(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            extracts = state / "extracts"
            extracts.mkdir()
            files = {}
            for number in range(3):
                content = f"Document {number} describes project quality controls."
                sha = hashlib.sha256(content.encode()).hexdigest()
                (extracts / f"{sha}.txt").write_text(content, encoding="utf-8")
                files[f"General/doc{number}.txt"] = {"sha256": sha, "extraction_status": "complete"}
            save_manifest(state, {"files": files})
            args = argparse.Namespace(state_dir=str(state), max_files=3, max_seconds=100,
                                      timeout_seconds=10, chunk_chars=1000, merge_chars=400,
                                      batch_chars=1000, batch_docs=12)
            calls = []

            def fake_batch(payload, schema, output, budget):
                calls.append(payload)
                return {"analyses": [{"sha256": item["sha256"], **note(f"document {i}")}
                                     for i, item in enumerate(payload["documents"])]}

            with contextlib.redirect_stdout(io.StringIO()):
                result = directory_enrich.run(args, batch_model_call=fake_batch)
            self.assertEqual(result["completed"], 3)
            self.assertEqual(len(calls), 1)
            self.assertEqual(result["remaining"], 0)


if __name__ == "__main__":
    unittest.main()
