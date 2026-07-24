#!/usr/bin/env python3
"""
detect_diff.py — Startup routing logic (rutas a/b/c from the original spec).

Ruta a: no previous detected_palette.csv -> show the welcome popup, then detect.
Ruta b: previous exists and a fresh detect matches it -> go straight to mapping.
Ruta c: previous exists but a fresh detect differs -> warn the user, let them
        choose whether to build a new mapping or keep using the stale detect.

config.detected_palette_csv is a single canonical file, overwritten on every
detect — there's no built-in history. To diff "what changed", we read it
BEFORE overwriting it with a fresh scan.
"""

import os
from datetime import datetime

from . import color_detector


def load_previous(config) -> list:
    """The previously saved detection, or [] if this is the first run."""
    return color_detector.read_detected_csv(config.detected_palette_csv)


def run_detect(config) -> list:
    """A fresh scan, not yet persisted anywhere."""
    return color_detector.detect_colors(config.files_to_replace)


def diff(previous: list, current: list) -> dict:
    """Compare two detection results by (type, color) identity."""
    prev_keys = {(c["type"], c["color"]) for c in previous}
    curr_keys = {(c["type"], c["color"]) for c in current}

    added_keys = curr_keys - prev_keys
    removed_keys = prev_keys - curr_keys

    curr_by_key = {(c["type"], c["color"]): c for c in current}
    prev_by_key = {(c["type"], c["color"]): c for c in previous}

    return {
        "changed": bool(added_keys or removed_keys),
        "added": [curr_by_key[k] for k in added_keys],
        "removed": [prev_by_key[k] for k in removed_keys],
    }


def detect_with_route(config, save: bool = True) -> dict:
    """
    Runs the full startup flow and returns:
        {"route": "a"|"b"|"c", "colors": [...], "previous": [...], "diff": {...}?}
    `diff` is only present for route "c".
    If save=True (default), the fresh detection is written to config.detected_palette_csv.
    """
    previous = load_previous(config)
    current = run_detect(config)

    if not previous:
        route = "a"
        result = {"route": route, "colors": current, "previous": previous}
    else:
        d = diff(previous, current)
        route = "c" if d["changed"] else "b"
        result = {"route": route, "colors": current, "previous": previous}
        if route == "c":
            result["diff"] = d

    if save:
        color_detector.write_detected_csv(current, config.detected_palette_csv)

    return result


def save_snapshot(config, colors: list = None, name: str = None) -> str:
    """Save a named copy of a detection into palettes_detected_dir, for
    later reuse via --detected-palette."""
    colors = colors if colors is not None else run_detect(config)
    name = name or datetime.now().strftime("detected-%Y%m%d-%H%M%S.csv")
    if not name.endswith(".csv"):
        name += ".csv"
    dest = os.path.join(config.palettes_detected_dir, name)
    color_detector.write_detected_csv(colors, dest)
    return dest
