#!/usr/bin/env python3
"""
color_detector.py — Detect colors (hex, rgb, rgba) from configuration files.

- Finds #rrggbb and #rrggbbaa hex colors
- Finds rgb(r,g,b) / rgba(r,g,b,a) function calls
- Normalizes everything to 6-digit lowercase hex
- Tracks count per color and which files contain each color
- Sorts by occurrence count descending, assigns sequential IDs

detected_palette.csv format: header id,type,color,count,files — color has
no leading '#', files are '|'-joined.
"""

import re
import os
from collections import OrderedDict


def expand_path(path_str: str) -> str:
    """Expand ~ and $HOME in a path string."""
    return os.path.expandvars(os.path.expanduser(path_str))


def rgb_string_to_hex(r, g, b):
    """Convert RGB integers to 6-digit hex string."""
    r = max(0, min(255, int(r)))
    g = max(0, min(255, int(g)))
    b = max(0, min(255, int(b)))
    return f"{r:02x}{g:02x}{b:02x}"


def hex_to_rgb(hex_color):
    """Convert 6-digit hex (no #) to (r, g, b) tuple."""
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


HEX_PATTERN = re.compile(
    r'(?<![0-9a-fA-F])#?([0-9a-fA-F]{6})(?:[0-9a-fA-F]{2})?(?![0-9a-fA-F])'
)

RGB_PATTERN = re.compile(
    r'rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*[\d.]+)?\s*\)',
    re.IGNORECASE
)


def detect_colors_in_file(filepath: str) -> dict:
    """
    Scan a single file for colors.
    Returns: dict with keys 'hex' and 'rgb' mapping color_hex -> count
    """
    result = {"hex": {}, "rgb": {}}

    if not os.path.isfile(filepath):
        return result

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return result

    for match in HEX_PATTERN.finditer(content):
        clean = match.group(0).lstrip("#").lower()[:6]
        result["hex"][clean] = result["hex"].get(clean, 0) + 1

    for match in RGB_PATTERN.finditer(content):
        r, g, b = int(match.group(1)), int(match.group(2)), int(match.group(3))
        hex_color = rgb_string_to_hex(r, g, b)
        result["rgb"][hex_color] = result["rgb"].get(hex_color, 0) + 1

    return result


def detect_colors(files_to_replace: list) -> list:
    """
    Scan all files and produce a list of detected colors sorted by count desc.

    Each entry: {
        "id": int,
        "type": "hex" | "hex_from_rgb",
        "color": "rrggbb" (6-char lowercase hex, no #),
        "count": int,
        "files": ["file1", "file2", ...]
    }
    """
    color_data = OrderedDict()

    for raw_path in files_to_replace:
        filepath = expand_path(raw_path)
        if not os.path.isfile(filepath):
            continue

        detected = detect_colors_in_file(filepath)

        for color_type in ("hex", "rgb"):
            for color, count_in_file in detected[color_type].items():
                key = ("hex_from_rgb" if color_type == "rgb" else "hex", color)
                if key not in color_data:
                    color_data[key] = {"count": 0, "files": []}
                color_data[key]["count"] += count_in_file
                if filepath not in color_data[key]["files"]:
                    color_data[key]["files"].append(filepath)

    sorted_items = sorted(color_data.items(), key=lambda x: x[1]["count"], reverse=True)

    result = []
    for idx, ((col_type, color), data) in enumerate(sorted_items, start=1):
        result.append({
            "id": idx,
            "type": col_type,
            "color": color,
            "count": data["count"],
            "files": data["files"],
        })

    return result


def grouped_by_hex(colors: list) -> dict:
    """
    Group detected color entries by their underlying hex value, regardless of
    the format (hex / hex_from_rgb) they were found in. Entries whose group
    has more than one member are the "case 2" siblings from the original
    spec: same color, different literal representation in the files.
    """
    groups = OrderedDict()
    for c in colors:
        groups.setdefault(c["color"].lower(), []).append(c)
    return groups


def write_detected_csv(colors: list, path: str) -> None:
    path = expand_path(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("id,type,color,count,files\n")
        for c in colors:
            files = "|".join(c["files"])
            f.write(f"{c['id']},{c['type']},{c['color']},{c['count']},{files}\n")


def read_detected_csv(path: str) -> list:
    """Read a detected_palette.csv back into the same list-of-dict shape
    produced by detect_colors(). Tolerates file paths that themselves
    contain commas (only the first 4 fields are split strictly)."""
    path = expand_path(path)
    colors = []
    if not os.path.isfile(path):
        return colors

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()

    for line in lines[1:]:  # skip header
        if not line.strip():
            continue
        parts = line.split(",", 4)
        if len(parts) < 5:
            continue
        id_str, type_str, color, count_str, files_str = parts
        if not id_str.strip().isdigit():
            continue
        colors.append({
            "id": int(id_str),
            "type": type_str,
            "color": color.lstrip("#").lower(),
            "count": int(count_str) if count_str.strip().isdigit() else 0,
            "files": [f for f in files_str.split("|") if f],
        })

    return colors


def generate_regex_for_color(hex_color: str) -> str:
    """Generate a regex pattern to match this hex color (with/without #)."""
    c = hex_color.lstrip("#").lower()
    return f"(#?){c}"


def generate_rgb_regex_for_color(hex_color: str) -> str:
    """Generate a regex to match this color as rgb(r, g, b), spaces tolerant."""
    r, g, b = hex_to_rgb(hex_color)
    return f"{r}\\s*,\\s*{g}\\s*,\\s*{b}"
