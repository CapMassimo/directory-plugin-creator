"""Cache a verified local copy when a synced OneDrive placeholder cannot be read."""

import argparse
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

import config as settings
from directory_index import file_hash, load_json, local_path, save_json


def recover(cfg: dict, relative_path: str, copy_from: str, remote_modified: str) -> Path:
    root = Path(cfg["source_root"])
    relative = Path(relative_path)
    if relative.is_absolute() or any(part in (".", "..") for part in relative.parts):
        raise ValueError("relative_path must stay inside the configured source folder")
    source = root / relative
    if not source.resolve().is_relative_to(root.resolve()) or not source.is_file():
        raise ValueError("source path is outside the directory or missing")
    stat = local_path(source).stat()
    remote_time = datetime.fromisoformat(remote_modified.replace("Z", "+00:00"))
    if remote_time.tzinfo is None or abs(stat.st_mtime - remote_time.astimezone(timezone.utc).timestamp()) > 1:
        raise ValueError("OneDrive and SharePoint modification times disagree")
    supplied = Path(copy_from).resolve()
    if not supplied.is_file() or supplied.stat().st_size != stat.st_size:
        raise ValueError("supplied copy is missing or has a different size")
    state = Path(cfg["state_dir"])
    recovery = state / "recovery"
    recovery.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(relative.as_posix().encode("utf-8")).hexdigest() + source.suffix.lower()
    with tempfile.NamedTemporaryFile("wb", dir=recovery, prefix=".copy-", delete=False) as stream:
        staged = Path(stream.name)
    try:
        shutil.copyfile(supplied, staged)
        if staged.stat().st_size != stat.st_size:
            raise ValueError("supplied copy changed during copying")
        if source.suffix.lower() in {".docx", ".pptx", ".potx", ".xlsx", ".xlsm"}:
            with zipfile.ZipFile(staged) as archive:
                if archive.testzip() is not None:
                    raise ValueError("supplied Office file is damaged")
        sha = file_hash(staged)
        current = local_path(source).stat()
        if (current.st_size, current.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
            raise ValueError("source changed during recovery")
        os.replace(staged, recovery / name)
        manifest_path = state / "recovery_manifest.json"
        manifest = load_json(manifest_path, {})
        manifest[relative.as_posix()] = {"filename": name, "sha256": sha,
                                         "fingerprint": f"{stat.st_size}:{stat.st_mtime_ns}"}
        save_json(manifest_path, manifest)
        return recovery / name
    finally:
        staged.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--relative-path", required=True)
    parser.add_argument("--copy-from", required=True, help="locally saved copy fetched from SharePoint")
    parser.add_argument("--remote-modified", required=True, help="SharePoint modified time in ISO 8601")
    args = parser.parse_args()
    print(recover(settings.load_config(args.config), args.relative_path, args.copy_from, args.remote_modified))


if __name__ == "__main__":
    main()
