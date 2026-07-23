#!/usr/bin/env python3
"""
color_replacer.py — Apply color replacements to files (backup, test-run, real apply).

- Supports both hex (#rrggbb) and rgb(r,g,b)/rgba() replacement
- Test mode (dry-run) counts matches without modifying files
- Real mode creates backups (flat mirror under BACKUP_DIR) and applies changes
"""

import os
import re
import shutil

from .color_detector import hex_to_rgb, expand_path

HOME = os.path.expanduser("~")


def _backup_relpath(filepath: str) -> str:
    """Path relative to $HOME, used to mirror a file's location under BACKUP_DIR."""
    filepath = expand_path(filepath)
    if filepath.startswith(HOME + os.sep):
        return os.path.relpath(filepath, HOME)
    return filepath.lstrip("/")


def backup_files(file_paths: list, backup_dir: str) -> list:
    """
    Copy each file into BACKUP_DIR, mirroring its path relative to $HOME.
    Flat layout, no timestamp subdir — a fresh backup overwrites the
    previous one. Returns the list of backup destination paths actually written.
    """
    backup_dir = expand_path(backup_dir)
    written = []
    for fp in file_paths:
        fp = expand_path(fp)
        if not os.path.isfile(fp):
            continue
        dst = os.path.join(backup_dir, _backup_relpath(fp))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(fp, dst)
        written.append(dst)
    return written


def restore_files(file_paths: list, backup_dir: str) -> list:
    """
    Restore each file from its mirrored location under BACKUP_DIR.
    Returns a list of {"file", "restored"} dicts.
    """
    backup_dir = expand_path(backup_dir)
    results = []
    for fp in file_paths:
        fp = expand_path(fp)
        src = os.path.join(backup_dir, _backup_relpath(fp))
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            shutil.copy2(src, fp)
            results.append({"file": fp, "restored": True})
        else:
            results.append({"file": fp, "restored": False})
    return results


def _build_hex_regex(hex_color: str) -> str:
    """Build a regex pattern for a hex color (matches with/without #)."""
    c = hex_color.lstrip("#").lower()
    return f"(#?){c}"


def _build_rgb_regex(hex_color: str) -> str:
    """Build a regex to match rgb(r,g,b)/rgba(r,g,b,a) for a color."""
    r, g, b = hex_to_rgb(hex_color)
    return f"{r}\\s*,\\s*{g}\\s*,\\s*{b}"


def _count_rgb_matches(content: str, hex_color: str) -> int:
    """Count how many times this color appears as rgb(a) in content."""
    r, g, b = hex_to_rgb(hex_color)
    pattern = re.compile(
        f"rgba?\\(\\s*{r}\\s*,\\s*{g}\\s*,\\s*{b}\\s*(?:,\\s*[\\d.]+)?\\s*\\)",
        re.IGNORECASE,
    )
    return len(pattern.findall(content))


def replace_color_in_file(
    filepath: str,
    old_hex: str,
    new_hex: str,
    color_type: str,
    dry_run: bool = False,
) -> dict:
    """
    Replace a color in a file.

    Args:
        filepath: file to modify
        old_hex: old color in hex ("rrggbb"), no #
        new_hex: new color in hex ("rrggbb"), no #
        color_type: "hex" or "hex_from_rgb"
        dry_run: if True, only count matches, don't modify

    Returns:
        dict with stats: {file, old_color, new_color, type, count, dry_run}
    """
    old_hex = old_hex.lstrip("#").lower()
    new_hex = new_hex.lstrip("#").lower()

    result = {
        "file": filepath,
        "old_color": old_hex,
        "new_color": new_hex,
        "type": color_type,
        "count": 0,
        "dry_run": dry_run,
    }

    if not os.path.isfile(filepath):
        result["error"] = "File not found"
        return result

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as e:
        result["error"] = str(e)
        return result

    if color_type == "hex":
        pattern = re.compile(
            f"(?<![0-9a-fA-F])"
            f"(#?)"
            f"({old_hex})"
            f"([0-9a-fA-F]{{2}})?"
            f"(?![0-9a-fA-F])",
            re.IGNORECASE,
        )
        count = len(pattern.findall(content))
        result["count"] = count

        if not dry_run and count > 0:
            def hex_replacer(m):
                prefix = m.group(1)  # optional #
                alpha = m.group(3) or ""  # optional alpha suffix
                return f"{prefix}{new_hex}{alpha}"

            new_content = pattern.sub(hex_replacer, content)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(new_content)

    else:  # hex_from_rgb -> replace RGB occurrences
        count = _count_rgb_matches(content, old_hex)
        result["count"] = count

        if not dry_run and count > 0:
            r_old, g_old, b_old = hex_to_rgb(old_hex)
            r_new, g_new, b_new = hex_to_rgb(new_hex)

            pattern = re.compile(
                f"(rgba?\\(\\s*)"
                f"{r_old}\\s*,\\s*{g_old}\\s*,\\s*{b_old}"
                f"(\\s*(?:,\\s*[\\d.]+)?\\s*\\))",
                re.IGNORECASE,
            )

            def rgb_replacer(m):
                prefix = m.group(1)
                suffix = m.group(2)
                return f"{prefix}{r_new}, {g_new}, {b_new}{suffix}"

            new_content = pattern.sub(rgb_replacer, content)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(new_content)

    return result


def apply_mapping(
    detected_colors: list,
    new_palette: list,
    mapping_entries: list,
    backup_dir: str,
    dry_run: bool = False,
) -> list:
    """
    Apply a full color mapping across all affected files.

    Args:
        detected_colors: list of entries from detected_palette.csv
            ({"id","type","color","count","files"})
        new_palette: list of palette entries ({"id","hex","label"})
        mapping_entries: ordered list of {"old_id","new_id"} (resolved —
            no None new_id here; see mapping_store.MappingStore.resolved_entries)
        backup_dir: where to mirror originals before modifying (skipped when dry_run)
        dry_run: if True, simulate without modifying files or backing up

    Returns:
        list of result dicts from each replacement
    """
    results = []

    color_by_id = {c["id"]: c for c in detected_colors}
    palette_by_id = {p["id"]: p for p in new_palette}

    all_files = set()
    for e in mapping_entries:
        old_color = color_by_id.get(e["old_id"])
        new_entry = palette_by_id.get(e["new_id"])
        if not old_color or not new_entry:
            continue
        for f in old_color.get("files", []):
            all_files.add(expand_path(f))

    if not dry_run and all_files:
        backup_files(list(all_files), backup_dir)

    for e in mapping_entries:
        old_color = color_by_id.get(e["old_id"])
        new_entry = palette_by_id.get(e["new_id"])
        if not old_color or not new_entry:
            continue

        for f in old_color.get("files", []):
            r = replace_color_in_file(
                expand_path(f),
                old_color["color"],
                new_entry["hex"],
                old_color["type"],
                dry_run=dry_run,
            )
            results.append(r)

    return results
