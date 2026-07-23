#!/usr/bin/env python3
"""
template_loader.py — Compile .blp (Blueprint) files into .ui (GtkBuilder XML)
on demand, with simple mtime-based caching, so Gtk.Template can load them.
"""

import os
import subprocess

BLUEPRINTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blueprints")


def compiled_ui_path(blp_name: str) -> str:
    """Compile blueprints/<blp_name> to a .ui file next to it (if stale or
    missing) and return the .ui path."""
    blp_path = os.path.join(BLUEPRINTS_DIR, blp_name)
    if not os.path.isfile(blp_path):
        raise FileNotFoundError(f"Blueprint not found: {blp_path}")

    ui_path = blp_path.rsplit(".blp", 1)[0] + ".ui"
    needs_compile = (
        not os.path.isfile(ui_path)
        or os.path.getmtime(blp_path) > os.path.getmtime(ui_path)
    )
    if needs_compile:
        subprocess.run(
            ["blueprint-compiler", "compile", blp_path, "--output", ui_path],
            check=True,
        )
    return ui_path
