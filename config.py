"""Validated settings shared by the indexer, model runners, and plugin builder."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlsplit


CORE_FIELDS = ("summary", "key_terms", "user_intents", "use_cases", "confidence", "artifact_type")
RESERVED_FIELDS = set(CORE_FIELDS) | {
    "sha256", "id", "name", "path", "folder", "file_url", "folder_url",
    "file_format", "size", "mtime_ns", "extraction_status", "exception",
    "analyzed_at_utc", "duplicate_locations", "related_documents",
}


def _relative(value: str, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not value and not allow_empty):
        raise ValueError(f"{label} must be a relative path")
    if "\\" in value or value.startswith("/") or any(part in ("", ".", "..") for part in value.split("/") if value):
        raise ValueError(f"{label} must use safe forward-slash path segments")
    return value.strip("/")


def validate_config(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("configuration must be a JSON object")
    required = {"source_root", "state_dir", "site_url", "library_path", "source_folder",
                "index_folder", "freshness_hours", "plugin", "custom_fields"}
    if set(raw) != required:
        raise ValueError(f"configuration keys must be: {', '.join(sorted(required))}")
    cfg = dict(raw)
    for key in ("source_root", "state_dir"):
        if not isinstance(cfg[key], str) or not Path(cfg[key]).is_absolute():
            raise ValueError(f"{key} must be an absolute path")
        cfg[key] = str(Path(cfg[key]).resolve())
    root, state = Path(cfg["source_root"]), Path(cfg["state_dir"])
    if root == state or root in state.parents:
        raise ValueError("state_dir must be outside source_root")
    site = urlsplit(cfg["site_url"] if isinstance(cfg["site_url"], str) else "")
    if site.scheme != "https" or not site.netloc or site.username or site.password or site.query or site.fragment:
        raise ValueError("site_url must be an HTTPS SharePoint site URL")
    site_path = unquote(site.path).rstrip("/")
    if not (site_path.startswith("/sites/") or site_path.startswith("/teams/")):
        raise ValueError("site_url must point to a SharePoint site or team")
    cfg["site_url"] = f"https://{site.netloc}{quote(site_path, safe='/')}"
    cfg["library_path"] = _relative(cfg["library_path"], "library_path")
    cfg["source_folder"] = _relative(cfg["source_folder"], "source_folder", allow_empty=True)
    cfg["index_folder"] = _relative(cfg["index_folder"], "index_folder")
    if "/" in cfg["index_folder"]:
        raise ValueError("index_folder must be one folder name")
    if not isinstance(cfg["freshness_hours"], int) or cfg["freshness_hours"] <= 0:
        raise ValueError("freshness_hours must be a positive integer")
    plugin = cfg["plugin"]
    if not isinstance(plugin, dict) or set(plugin) != {"name", "display_name", "description", "developer_name"}:
        raise ValueError("plugin must contain name, display_name, description, and developer_name")
    if not isinstance(plugin["name"], str) or not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", plugin["name"]):
        raise ValueError("plugin.name must be kebab-case")
    if not all(isinstance(plugin[key], str) and plugin[key].strip() and "\n" not in plugin[key]
               and "\r" not in plugin[key] for key in plugin):
        raise ValueError("plugin text values cannot be empty")
    fields = cfg["custom_fields"]
    if not isinstance(fields, list):
        raise ValueError("custom_fields must be a list")
    seen = set()
    for field in fields:
        if not isinstance(field, dict) or set(field) != {"key", "label", "guidance"}:
            raise ValueError("each custom field needs key, label, and guidance")
        key = field["key"]
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", key) or key in RESERVED_FIELDS or key in seen:
            raise ValueError(f"invalid or duplicate custom field key: {key}")
        if not all(isinstance(field[name], str) and field[name].strip() and "\n" not in field[name]
                   and "\r" not in field[name] for name in ("label", "guidance")):
            raise ValueError(f"custom field {key} needs a label and guidance")
        seen.add(key)
    return cfg


def load_config(path: str | Path) -> dict:
    return validate_config(json.loads(Path(path).read_text(encoding="utf-8")))


def schema_hash(config: dict) -> str:
    payload = json.dumps(config["custom_fields"], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fields(config: dict) -> tuple[str, ...]:
    return (*CORE_FIELDS, *(field["key"] for field in config["custom_fields"]))


def drive_path(config: dict, relative: str = "") -> str:
    return "/".join(part for part in (config["source_folder"], relative.strip("/")) if part)


def catalog_path(config: dict) -> str:
    return drive_path(config, config["index_folder"])


def source_url(config: dict, relative: str, folder: bool = False) -> str:
    site = urlsplit(config["site_url"])
    site_path = unquote(site.path).rstrip("/")
    library_relative = "/".join(part for part in (config["library_path"], drive_path(config, relative)) if part)
    server_path = site_path + "/" + library_relative
    if folder:
        library_url = quote(site_path + "/" + config["library_path"], safe="/")
        return f"{site.scheme}://{site.netloc}{library_url}/Forms/AllItems.aspx?id={quote(server_path, safe='')}"
    return f"{site.scheme}://{site.netloc}{quote(server_path, safe='/')}"
