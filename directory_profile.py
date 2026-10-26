"""Build compact, reproducible search profiles for very large spreadsheet extracts.

Every extracted line contributes to the text hash and structural counts. Only
early label rows enter the model prompt; bulk transaction values are not needed
to describe which workbook a user should open.
"""

from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

import directory_index


VERSION = 2
MIN_CHARS = 250_000
SPREADSHEETS = {".xlsx", ".xlsm", ".xlsb"}
SHEET = re.compile(r"^\[([^\[\]\r\n]{1,31})\]$")
NUMBER = re.compile(r"^[+-]?(?:\d[\d,]*(?:\.\d*)?|\.\d+)%?$")


def applicable(item: dict) -> bool:
    return (item["char_count"] > MIN_CHARS
            and PurePosixPath(item["paths"][0]).suffix.lower() in SPREADSHEETS)


def build(item: dict, state: Path) -> dict:
    """Return a pending item whose extract is a whole-workbook structural profile."""
    if not applicable(item):
        return item
    source = Path(item["extract_path"])
    destination = state / "profiles" / f"{item['sha256']}.txt"
    sidecar = state / "profiles" / f"{item['sha256']}.json"
    fingerprint = f"{source.stat().st_size}:{source.stat().st_mtime_ns}"
    saved = directory_index.load_json(sidecar, {})
    if (saved.get("version") == VERSION and saved.get("fingerprint") == fingerprint
            and saved.get("extract_sha256") and destination.is_file()):
        return item | {"extract_path": str(destination), "char_count": destination.stat().st_size,
                       "profile_hash": f"v{VERSION}-{saved['extract_sha256']}"}

    digest = hashlib.sha256()
    sheets = []
    current = None

    def new_sheet(name: str) -> dict:
        sheet = {"name": name, "nonempty_rows": 0, "row_widths": Counter(),
                 "formula_rows": 0, "early_labels": [], "_first_table_seen": False}
        sheets.append(sheet)
        return sheet

    with source.open("rb") as stream:
        for raw in stream:
            digest.update(raw)
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            heading = SHEET.fullmatch(line)
            if heading:
                current = new_sheet(heading.group(1))
                continue
            if current is None:
                current = new_sheet("Unlabelled sheet")
            current["nonempty_rows"] += 1
            width = line.count(" | ") + 1
            current["row_widths"][width] += 1
            if "=" in line:
                current["formula_rows"] += 1
            if current["nonempty_rows"] <= 30 and not current["_first_table_seen"]:
                if width == 1 and not current["early_labels"] and len(line) <= 120:
                    current["early_labels"].append(line)
                elif width > 1:
                    current["_first_table_seen"] = True
                    cells = line.split(" | ")
                    numeric = sum(bool(NUMBER.fullmatch(c.strip())) for c in cells)
                    if numeric * 3 < width:
                        current["early_labels"].append(line[:1200])

    for sheet in sheets:
        sheet["row_widths"] = dict(sheet["row_widths"].most_common(8))
        sheet.pop("_first_table_seen")
    profile = {"profile_kind": "whole-workbook structural search profile",
               "coverage": "All extracted lines counted and hashed; only a first title and header row retained. "
                           "Do not infer the values or contents of omitted data rows.",
               "source_extract_characters": item["char_count"],
               "sheet_count": len(sheets), "sheets": sheets}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    directory_index.save_json(sidecar, {"version": VERSION, "fingerprint": fingerprint,
                                   "extract_sha256": digest.hexdigest()})
    return item | {"extract_path": str(destination), "char_count": destination.stat().st_size,
                   "profile_hash": f"v{VERSION}-{digest.hexdigest()}"}
