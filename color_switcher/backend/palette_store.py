#!/usr/bin/env python3
"""
palette_store.py — Read/write/create palette CSVs.

Row format: "id,#hexvalue,label[,origin]" (origin optional: "gen"|"custom").
Optionally preceded by ONE metadata header line:

    #ucs-meta={...json...}

carrying the palette's provenance so `shift` can re-tweak & re-apply it without
re-typing the whole `automatic --from-image` command (see read_palette). The
header is backward-compatible: older readers (and this one's row loop) skip any
line whose first comma-field isn't a numeric id, so a palette with no header
still loads fine and is treated as a plain hand-created palette.
"""

import json
import os
import re

from .color_detector import expand_path

_HEX_RE = re.compile(r"^[0-9a-f]{6}$")

META_PREFIX = "#ucs-meta="
META_VERSION = 1
_VALID_ORIGINS = ("gen", "custom")


def default_meta() -> dict:
    """The metadata a palette with no header is treated as: a hand-created
    palette with no image, no generation params, no active post-modifiers."""
    return {
        "v": META_VERSION,
        "generated": False,
        "image": None,
        "gen": None,   # generation params (colors, mode, scoring, ...) — only for generated palettes
        "post": {"my_eyes": False, "ying_yang": False},
    }


def _parse_meta(raw: str) -> dict:
    """Parse the JSON after `#ucs-meta=`, merged over default_meta so missing
    keys fall back cleanly. Returns default_meta() on any malformed input."""
    meta = default_meta()
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return meta
    if not isinstance(parsed, dict):
        return meta
    meta.update(parsed)
    post = default_meta()["post"]
    if isinstance(parsed.get("post"), dict):
        post.update(parsed["post"])
    meta["post"] = post
    return meta


def _serialize_meta(meta: dict) -> str:
    """The full `#ucs-meta=...` header line (no trailing newline)."""
    return META_PREFIX + json.dumps(meta, separators=(",", ":"))


def _read(path: str):
    """Internal: parse a palette file into (entries, meta_or_None) where
    meta_or_None is None when the file carried NO #ucs-meta header (so callers
    can tell "header-less" apart from "header that happens to equal defaults").
    Returns ([], None) if the file doesn't exist."""
    path = expand_path(path)
    entries = []
    meta = None
    if not os.path.isfile(path):
        return entries, meta

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith(META_PREFIX):
                meta = _parse_meta(line[len(META_PREFIX):])
                continue
            parts = line.split(",")
            if len(parts) < 2:
                continue
            id_str, hex_str = parts[0].strip(), parts[1].strip()
            if not id_str.isdigit():
                continue
            hex_clean = hex_str.lstrip("#").lower()
            if not _HEX_RE.match(hex_clean):
                continue
            label = parts[2].strip() if len(parts) > 2 else ""
            origin = parts[3].strip().lower() if len(parts) > 3 else None
            entry = {"id": int(id_str), "hex": hex_clean, "label": label}
            if origin in _VALID_ORIGINS:  # keep the dict at {id,hex,label} unless origin is meaningful
                entry["origin"] = origin
            entries.append(entry)

    return entries, meta


def read_palette(path: str):
    """Load a palette CSV. Returns (entries, meta):
      entries: [{"id": int, "hex": "rrggbb", "label": str, "origin": str|None}]
      meta:    the parsed #ucs-meta dict, or default_meta() if there's no header.
    Returns ([], default_meta()) if the file doesn't exist."""
    entries, meta = _read(path)
    return entries, meta if meta is not None else default_meta()


def read_palette_csv(path: str) -> list:
    """Load just the color entries of a palette CSV (metadata ignored). Kept
    for the many callers that only want the colors; use read_palette when you
    also need the provenance."""
    return _read(path)[0]


def read_palette_meta(path: str) -> dict:
    """Load just a palette's #ucs-meta (or default_meta() if it has none)."""
    return read_palette(path)[1]


def write_palette_csv(path: str, entries: list, meta: dict = None) -> None:
    """Write a palette CSV. When `meta` is given, a `#ucs-meta=` header line is
    written first; without it, the file is header-less (unchanged legacy
    behavior). A per-color `origin` field is written whenever any entry carries
    one, so it stays paired with the right color."""
    path = expand_path(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_origin = any(e.get("origin") in _VALID_ORIGINS for e in entries)
    with open(path, "w", encoding="utf-8") as f:
        if meta is not None:
            f.write(_serialize_meta(meta) + "\n")
        for e in entries:
            hex_clean = e["hex"].lstrip("#").lower()
            label = e.get("label", "")
            if write_origin:
                f.write(f"{e['id']},#{hex_clean},{label},{e.get('origin') or 'custom'}\n")
            else:
                f.write(f"{e['id']},#{hex_clean},{label}\n")


def add_color(path: str, hex_value: str, label: str = "", origin: str = None) -> dict:
    """Append a new color to a palette CSV, assigning the next free id, and
    PRESERVING the file's #ucs-meta header. On a generated palette a manually
    added color is marked origin="custom" so a later regenerating shift can
    warn it will be discarded (see [[palette-shift-design]])."""
    entries, meta = _read(path)
    if origin is None and meta is not None and meta.get("generated"):
        origin = "custom"
    next_id = max((e["id"] for e in entries), default=0) + 1
    entry = {"id": next_id, "hex": hex_value.lstrip("#").lower(), "label": label}
    if origin in _VALID_ORIGINS:
        entry["origin"] = origin
    entries.append(entry)
    write_palette_csv(path, entries, meta=meta)  # meta is None for header-less files -> stays header-less
    return entry


def has_custom_edits(entries: list) -> bool:
    """True if any color was hand-added/edited on top of a generated palette
    (origin="custom") — the signal for the warn-before-regenerate gate."""
    return any(e.get("origin") == "custom" for e in entries)


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
