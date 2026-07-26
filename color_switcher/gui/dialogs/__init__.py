#!/usr/bin/env python3
"""
dialogs/ — Dialog helpers used by window_main.py, split by concern:

- common.py: generic building blocks (toast, confirm/text/color prompts,
  file pickers, the group-title-restyling and dialog-shell helpers).
- onboarding.py: the first-run flow (welcome, "which files", the threaded
  ~/.config auto-scan + review tree, stale-detect, restart-actions intro).
- restart_actions.py / scanned_files.py: the two small config.json-backed
  settings managers (restart hooks / scanned dotfiles).
- generation_settings.py: "Configurar generación de paleta…" and "Otros…"
  (mode/scoring/shading/my-eyes knobs).
- modifiers.py: the "Modificadores…" live-shift-preview dialog.

Everything public each submodule defines is re-exported here so callers keep
using `dialogs.whatever(...)` exactly as before this split -- window_main.py
never needed to change."""

from .common import (
    ask_confirm,
    pick_image_file,
    pick_import_palette_file,
    prompt_add_color,
    prompt_pick_color,
    prompt_text,
    toast,
)
from .generation_settings import (
    build_advanced_generation_group,
    build_contrast_comparison_group,
    build_other_settings_group,
    build_palette_generation_group,
    show_other_settings,
    show_palette_generation_settings,
)
from .modifiers import show_palette_modifiers
from .onboarding import (
    run_config_scan_flow,
    show_config_scan_results,
    show_configure_files_onboarding,
    show_restart_actions_onboarding,
    show_stale_detect,
    show_welcome,
)
from .restart_actions import (
    build_restart_actions_group,
    prompt_restart_action,
    show_restart_actions_settings,
)
from .scanned_files import (
    build_scanned_files_group,
    pick_scan_file,
    show_scanned_files_settings,
)

__all__ = [
    "ask_confirm",
    "pick_image_file",
    "pick_import_palette_file",
    "prompt_add_color",
    "prompt_pick_color",
    "prompt_text",
    "toast",
    "build_advanced_generation_group",
    "build_contrast_comparison_group",
    "build_other_settings_group",
    "build_palette_generation_group",
    "show_other_settings",
    "show_palette_generation_settings",
    "show_palette_modifiers",
    "run_config_scan_flow",
    "show_config_scan_results",
    "show_configure_files_onboarding",
    "show_restart_actions_onboarding",
    "show_stale_detect",
    "show_welcome",
    "build_restart_actions_group",
    "prompt_restart_action",
    "show_restart_actions_settings",
    "build_scanned_files_group",
    "pick_scan_file",
    "show_scanned_files_settings",
]
