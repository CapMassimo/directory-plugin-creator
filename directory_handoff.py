"""Seed a new maintainer's local state from an existing published catalog."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

import config as settings
import directory_index


def prepare(cfg: dict) -> dict:
    cfg = settings.validate_config(cfg)
    state = Path(cfg["state_dir"])
    target = Path(cfg["source_root"]) / cfg["index_folder"]
    if any((state / name).exists() for name in ("last_publish.json", "published_paths.json")):
        raise ValueError("this state directory already has a publication baseline")
    if not target.is_dir() or target.is_symlink():
        raise ValueError("sync the existing SharePoint index folder before takeover")
    if any(path.is_symlink() for path in target.rglob("*")):
        raise ValueError("published catalog contains a link and cannot be safely snapshotted")
    status_path, index_path = target / "catalog-status.txt", target / "search_index.yaml"
    if not status_path.is_file() or not index_path.is_file():
        raise ValueError("existing catalog status or full index is missing")
    status = dict(line.split(": ", 1) for line in status_path.read_text(encoding="utf-8").splitlines()
                  if ": " in line)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    meta = index["index_metadata"]
    generation = status.get("generation_id", "")
    if status.get("health") != "complete" or not generation or meta.get("generation_id") != generation:
        raise ValueError("published status and full index do not show one complete generation")
    if (meta.get("site_url"), meta.get("root_library_path"), meta.get("source_folder")) != (
            cfg["site_url"], cfg["library_path"], cfg["source_folder"]):
        raise ValueError("published catalog points to a different SharePoint location")
    records = [record for bundle in index["folder_bundles"] for record in bundle["assets"]]
    if not records or int(status["file_count"]) != len(records) or meta.get("asset_count") != len(records):
        raise ValueError("published catalog file count does not match its records")
    prefix = cfg["source_folder"] + "/" if cfg["source_folder"] else ""
    paths = set()
    for record in records:
        full = record["path"]
        if not isinstance(full, str) or not full.startswith(prefix):
            raise ValueError("published record falls outside the configured source folder")
        rel = full[len(prefix):]
        if not rel or "\\" in rel or any(part in ("", ".", "..") for part in rel.split("/")):
            raise ValueError("published record has an unsafe relative path")
        if rel in paths or record.get("file_url") != settings.source_url(cfg, rel):
            raise ValueError("published record has a duplicate path or wrong SharePoint URL")
        paths.add(rel)
        record_id = hashlib.sha256(rel.encode("utf-8")).hexdigest()
        if record.get("id") != record_id or not (target / "cards" / record_id[:2] / f"{record_id}.txt").is_file():
            raise ValueError("published catalog is missing a card")
    term_manifest = target / "terms" / "manifest.txt"
    if not term_manifest.is_file():
        raise ValueError("published term manifest is missing")
    lines = term_manifest.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != f"# catalog_generation_id: {generation}":
        raise ValueError("published term manifest has a different generation")
    term_paths = [line.split("\t", 1)[0] for line in lines[1:]]
    if len(term_paths) != int(status["term_shard_count"]) or any(
            len(name.split("/")) != 2 or name.split("/")[0] != "terms"
            or not name.endswith(".txt") or "\\" in name
            or any(part in ("", ".", "..") for part in name.split("/"))
            or not (target / name).is_file() for name in term_paths):
        raise ValueError("published term shards are incomplete")

    snapshot = state / "handoff_snapshots" / generation
    if snapshot.exists():
        raise ValueError("a snapshot for this catalog generation already exists")
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(target, snapshot)
    directory_index.save_json(state / "published_paths.json",
                              {"generation_id": generation, "paths": sorted(paths)})
    directory_index.save_json(state / "last_publish.json", status)
    return {"generation_id": generation, "file_count": len(paths), "snapshot": str(snapshot)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(settings.load_config(args.config))))
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
