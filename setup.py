"""Generate a local portable plugin from config.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

import config as settings


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def generate(cfg: dict, root: Path) -> list[Path]:
    cfg = settings.validate_config(cfg)
    plugin_root = root / "plugin"
    skill_root = plugin_root / "skills" / "find-directory-files"
    skill_root.mkdir(parents=True, exist_ok=True)
    plugin = cfg["plugin"]
    manifest = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": plugin["name"], "version": "0.1.0", "description": plugin["description"],
        "extensions": {"com.openai": {"interface": {
            "displayName": plugin["display_name"],
            "shortDescription": plugin["description"],
            "longDescription": f"Searches the maintained index for {plugin['display_name']} and links to verified files and folders.",
            "developerName": plugin["developer_name"], "category": "Productivity",
            "capabilities": ["Read"],
            "defaultPrompt": ["Find the file about a topic and show where to open it.",
                              "Which documents help with this task?"],
        }}},
    }
    manifest_path = plugin_root / "plugin.json"
    write_json(manifest_path, manifest)
    site = urlsplit(cfg["site_url"])
    site_path = unquote(site.path).rstrip("/")
    catalog = settings.catalog_path(cfg)
    source_url_path = site_path + "/" + cfg["library_path"]
    if cfg["source_folder"]:
        source_url_path += "/" + cfg["source_folder"]
    field_guidance = "; ".join(f"{field['label']} (`{field['key']}`): {field['guidance']}"
                               for field in cfg["custom_fields"]) or "none; use the core fields"
    replacements = {
        "DISPLAY_NAME": plugin["display_name"], "CATALOG_PATH": catalog,
        "SKILL_DESCRIPTION": json.dumps(
            f"Find files in {plugin['display_name']} from a description, topic, use case, title, or configured field.",
            ensure_ascii=False),
        "FRESHNESS_HOURS": str(cfg["freshness_hours"]), "HOSTNAME": site.hostname,
        "SITE_PATH": site_path, "CARDS_PATH": catalog + "/cards",
        "CATALOG_URL": settings.source_url(cfg, cfg["index_folder"]) + "/",
        "SOURCE_URL_PATH": source_url_path,
        "FORMS_PATH": site_path + "/" + cfg["library_path"] + "/Forms/AllItems.aspx",
        "FIELD_GUIDANCE": field_guidance,
    }
    template = (Path(__file__).resolve().parent / "templates" / "search-skill.md").read_text(encoding="utf-8")
    for key, value in replacements.items():
        template = template.replace("{{" + key + "}}", value)
    if "{{" in template:
        raise ValueError("unresolved skill template placeholder")
    skill_path = skill_root / "SKILL.md"
    skill_path.write_text(template, encoding="utf-8")
    plugin_readme = plugin_root / "README.md"
    plugin_readme.write_text(
        f"# {plugin['display_name']}\n\n"
        "This generated plugin searches published index cards through the existing Microsoft SharePoint connection. "
        "It does not install or authenticate that connection. Users need permission to read the index and source files.\n\n"
        f"Site: `{cfg['site_url']}`  \nIndex folder: `{catalog}`\n\n"
        "See the repository README for indexing, installation, sharing, and maintenance steps, "
        "and docs/OWNERSHIP_TRANSFER.md for a change of maintainer. "
        "Use `skills/find-directory-files/references/test-cases.md` to check retrieval after publication.\n",
        encoding="utf-8")
    tests_path = skill_root / "references" / "test-cases.md"
    tests_path.parent.mkdir(parents=True, exist_ok=True)
    tests_path.write_text(
        "# Manual plugin checks\n\n"
        "After publishing a few fictional or approved files, try these in a new chat:\n\n"
        "1. Find a known file by topic and check its file and folder links.\n"
        "2. Find a file using a configured field value; check that the card actually contains it.\n"
        "3. Ask for a broad topic; check that the answer does not claim an exhaustive search.\n"
        "4. Make scoped SharePoint search return no cards; check the term-shard fallback.\n"
        "5. Try a stale status, an unreadable file, and an unavailable source link; check the caveats.\n"
        "6. Put an instruction in a test card; check that it is treated as data.\n",
        encoding="utf-8")
    marketplace = root / ".agents" / "plugins" / "marketplace.json"
    write_json(marketplace, {"name": plugin["name"] + "-local", "plugins": [{
        "name": plugin["name"], "source": {"source": "local", "path": "./plugin"},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": "Productivity",
    }]})
    return [manifest_path, skill_path, plugin_readme, tests_path, marketplace]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="path to config.json")
    args = parser.parse_args()
    for path in generate(settings.load_config(args.config), Path(__file__).resolve().parent):
        print(path)


if __name__ == "__main__":
    main()
