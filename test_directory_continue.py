"""Checks for the scheduled task's checkpointed text continuation."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import directory_continue


def response(remaining, completed=0, calls=0, timeouts=0, code=0):
    return SimpleNamespace(returncode=code, stderr="model failure" if code else "",
                           stdout=json.dumps({"remaining": remaining, "completed": completed,
                                              "model_calls": calls, "timeouts": timeouts}))


class ContinuationTest(unittest.TestCase):
    def test_resumes_after_budget_rollover_and_reports_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            calls = []
            results = iter([response(2, timeouts=1), response(1, calls=1),
                            response(0, completed=1, calls=1)])

            def runner(command, **kwargs):
                calls.append(command)
                return next(results)

            with patch.object(directory_continue.settings, "load_config",
                              return_value={"state_dir": temp}):
                result = directory_continue.continue_batches(Path(temp) / "config.json",
                                                             "trusted.pem", 3, runner)
            self.assertEqual(result, {"remaining": 0, "batches": 3, "complete": True})
            self.assertEqual(len(calls), 3)
            self.assertIn("--ca-certificate", calls[0])

    def test_stops_after_three_timeout_only_batches(self):
        with tempfile.TemporaryDirectory() as temp:
            results = iter([response(1, timeouts=1) for _ in range(3)])
            with patch.object(directory_continue.settings, "load_config",
                              return_value={"state_dir": temp}), \
                 self.assertRaisesRegex(RuntimeError, "three timed-out batches"):
                directory_continue.continue_batches(Path(temp) / "config.json", max_batches=3,
                                                     runner=lambda *_args, **_kwargs: next(results))

    def test_only_deferred_extracts_remain_incomplete(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(directory_continue.settings, "load_config",
                              return_value={"state_dir": temp}):
                result = directory_continue.continue_batches(
                    Path(temp) / "config.json", runner=lambda *_args, **_kwargs: SimpleNamespace(
                        returncode=0, stderr="", stdout=json.dumps({"remaining": 1, "deferred": 1,
                            "completed": 0, "model_calls": 0, "timeouts": 0})))
            self.assertEqual(result, {"remaining": 1, "batches": 1, "complete": False})

    def test_failure_and_active_lock_stop_before_next_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(directory_continue.settings, "load_config",
                              return_value={"state_dir": temp}):
                with self.assertRaisesRegex(RuntimeError, "model failure"):
                    directory_continue.continue_batches(Path(temp) / "config.json",
                                                         runner=lambda *_args, **_kwargs: response(1, code=1))
                lock = Path(temp) / "enrichment" / ".lock"
                lock.parent.mkdir()
                lock.touch()
                with self.assertRaisesRegex(RuntimeError, "holds the lock"):
                    directory_continue.continue_batches(Path(temp) / "config.json",
                                                         runner=lambda *_args, **_kwargs: self.fail("runner called"))


if __name__ == "__main__":
    unittest.main()
