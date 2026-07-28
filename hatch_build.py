"""Hatchling build hook: compile blueprints/*.blp to *.ui before packaging.

The .ui files are gitignored (see gui/template_loader.py) and only get
(re)compiled lazily, on import, when running from a source checkout in dev
mode. A `pip`/`pipx install` never imports the package during its build
step, so without this hook the wheel would just bundle whatever .ui files
happened to already be sitting on disk -- stale relative to the .blp
sources unless a dev session happened to run first and regenerate them.
This hook makes packaging correct unconditionally: every build recompiles
every blueprint from scratch, regardless of what pre-existing .ui state
(if any) is lying around in the checkout.
"""

import glob
import os
import shutil
import subprocess

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class BlueprintBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        compiler = shutil.which("blueprint-compiler")
        if compiler is None:
            raise RuntimeError(
                "blueprint-compiler is required to build ucs (compiles gui/blueprints/*.blp "
                "to *.ui) but isn't on PATH."
            )

        blueprints_dir = os.path.join(self.root, "color_switcher", "gui", "blueprints")
        for blp_path in sorted(glob.glob(os.path.join(blueprints_dir, "*.blp"))):
            ui_path = blp_path.rsplit(".blp", 1)[0] + ".ui"
            subprocess.run([compiler, "compile", blp_path, "--output", ui_path], check=True)
