#!/usr/bin/env python3
"""
palette_store.py — Read/write/create palette CSVs.

Format: plain CSV rows "id,#hexvalue,label", NO header line, no comment lines.
"""

import os
import re

from .color_detector import expand_path

_HEX_RE = re.compile(r"^[0-9a-f]{6}$")


def read_palette_csv(path: str) -> list:
    """Load a palette CSV. Returns [] if the file doesn't exist."""
    path = expand_path(path)
    entries = []
    if not os.path.isfile(path):
        return entries

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) < 2:
                continue
            id_str, hex_str = parts[0].strip(), parts[1].strip()
            label = parts[2].strip() if len(parts) > 2 else ""
            if not id_str.isdigit():
                continue
            hex_clean = hex_str.lstrip("#").lower()
            if not _HEX_RE.match(hex_clean):
                continue
            entries.append({"id": int(id_str), "hex": hex_clean, "label": label})

    return entries


def write_palette_csv(path: str, entries: list) -> None:
    """Write a palette CSV in the bash-compatible format (no header)."""
    path = expand_path(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            hex_clean = e["hex"].lstrip("#").lower()
            f.write(f"{e['id']},#{hex_clean},{e.get('label', '')}\n")


def add_color(path: str, hex_value: str, label: str = "") -> dict:
    """Append a new color to a palette CSV, assigning the next free id."""
    entries = read_palette_csv(path)
    next_id = max((e["id"] for e in entries), default=0) + 1
    entry = {"id": next_id, "hex": hex_value.lstrip("#").lower(), "label": label}
    entries.append(entry)
    write_palette_csv(path, entries)
    return entry


def list_palettes(directory: str) -> list:
    """List available palette CSVs under a directory (e.g. palettes/created)."""
    directory = expand_path(directory)
    if not os.path.isdir(directory):
        return []
    return sorted(
        os.path.join(directory, f)
        for f in os.listdir(directory)
        if f.endswith(".csv")
    )


# Aliases matching the vocabulary used in the original spec / roadmap.
import_palette = read_palette_csv
export_palette = write_palette_csv
