"""Continue checkpointed text analysis in bounded batches; never publish."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

import config as settings


def continue_batches(config_path: Path, ca_certificate: str | None = None,
                     max_batches: int = 2, runner=subprocess.run) -> dict:
    cfg = settings.load_config(config_path)
    state = Path(cfg["state_dir"])
    if (state / "enrichment" / ".lock").exists():
        raise RuntimeError("another text enrichment run holds the lock")

    stalled = 0
    last_remaining = None
    script = Path(__file__).with_name("directory_enrich.py")
    for batch in range(1, max_batches + 1):
        command = [sys.executable, str(script), "--config", str(config_path)]
        if ca_certificate:
            command.extend(["--ca-certificate", ca_certificate])
        result = runner(command, capture_output=True, text=True, check=False)
        if result.returncode:
            raise RuntimeError(f"text enrichment batch {batch} failed: {result.stderr.strip()}")
        try:
            status = json.loads(result.stdout.splitlines()[-1])
            remaining = status["remaining"]
            completed = status["completed"]
            calls = status["model_calls"]
            timeouts = status["timeouts"]
            if not all(type(value) is int and value >= 0 for value in
                       (remaining, completed, calls, timeouts)):
                raise ValueError("invalid batch counts")
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError(f"text enrichment batch {batch} returned invalid status") from error
        print(json.dumps({"batch": batch, **status}), flush=True)
        last_remaining = remaining
        if remaining == 0:
            return {"remaining": 0, "batches": batch, "complete": True}
        if status.get("deferred") == remaining and completed == 0:
            return {"remaining": remaining, "batches": batch, "complete": False}
        if completed or calls:
            stalled = 0  # Chunk checkpoints can advance before a whole file is complete.
        elif timeouts:
            stalled += 1
            if stalled >= 3:
                raise RuntimeError("text enrichment made no progress in three timed-out batches")
        else:
            raise RuntimeError("text enrichment made no progress; checkpoints retained")
    return {"remaining": last_remaining, "batches": max_batches, "complete": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--ca-certificate")
    parser.add_argument("--max-batches", type=int, default=2)
    args = parser.parse_args()
    if args.max_batches <= 0:
        parser.error("--max-batches must be positive")
    try:
        print(json.dumps(continue_batches(Path(args.config).resolve(), args.ca_certificate,
                                          args.max_batches)))
    except (ValueError, RuntimeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
