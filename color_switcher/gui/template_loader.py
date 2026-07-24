#!/usr/bin/env python3
"""
template_loader.py — Resolve blueprints/<name>.blp to its compiled .ui
(GtkBuilder XML) so Gtk.Template can load it.

Two scenarios:
  - Dev (running from a source checkout): blueprint-compiler is on PATH,
    so a missing/stale .ui is (re)compiled on demand, mtime-cached.
  - Packaged install: the .ui files are pre-compiled at build time (see
    PKGBUILD) and shipped alongside the .blp sources. blueprint-compiler is
    only a build-time dependency, not a runtime one, and an installed
    package shouldn't write into its own (typically read-only) location
    anyway -- so if the compiler isn't on PATH, whatever .ui already exists
    is used as-is, regardless of its mtime relative to the .blp.
"""

import os
import shutil
import subprocess

BLUEPRINTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blueprints")


def compiled_ui_path(blp_name: str) -> str:
    blp_path = os.path.join(BLUEPRINTS_DIR, blp_name)
    if not os.path.isfile(blp_path):
        raise FileNotFoundError(f"Blueprint not found: {blp_path}")

    ui_path = blp_path.rsplit(".blp", 1)[0] + ".ui"
    stale = not os.path.isfile(ui_path) or os.path.getmtime(blp_path) > os.path.getmtime(ui_path)

    if stale:
        compiler = shutil.which("blueprint-compiler")
        if compiler:
            subprocess.run([compiler, "compile", blp_path, "--output", ui_path], check=True)
        elif not os.path.isfile(ui_path):
            raise FileNotFoundError(
                f"{ui_path} is missing and blueprint-compiler isn't installed to generate it "
                "(packaged installs should ship pre-compiled .ui files -- see PKGBUILD)."
            )
        # else: no compiler, but a .ui already exists -- use it as-is (the packaged-install case).

    return ui_path
