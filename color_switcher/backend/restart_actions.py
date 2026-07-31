#!/usr/bin/env python3
"""
restart_actions.py — Post-apply "restart/reload service" actions.

Each action is {"label", "command", "enabled", "run_on_cli"}, stored under a
"restart_actions" key in config.json. run_on_cli (default True when absent,
so existing configs keep today's behavior) lets an action opt out of firing
when the app was invoked from the CLI -- e.g. a wallpaper-setter action that
would otherwise double-fire when `ucs automatic --from-image` is itself
called as another tool's (waypaper, etc.) postcommand hook, which already
set the wallpaper.

Commands are launched detached (new session, stdio to /dev/null, never
waited on) so a slow, sleeping, or failing command can never block the
caller — Popen() itself returns as soon as fork+exec happens.
"""

import os
import subprocess

from . import color_detector as color_detector_module
from . import config as config_module
from . import palette_store as palette_store_module

DEFAULT_ACTIONS = [
    {
        "label": "Restart Waybar",
        "command": "killall waybar 2>/dev/null; command -v waybar >/dev/null && setsid waybar >/dev/null 2>&1 &",
        "enabled": True,
    },
    {
        "label": "Restart Swaync",
        "command": "killall swaync 2>/dev/null; command -v swaync >/dev/null && setsid swaync >/dev/null 2>&1 &",
        "enabled": True,
    },
    {
        "label": "Reload WiFi Manager",
        "command": "command -v wifi-manager >/dev/null && wifi-manager --reload",
        "enabled": True,
    },
]


def read_restart_actions(config) -> list:
    """Read the restart_actions list from config.json. Falls back to a copy
    of DEFAULT_ACTIONS (not persisted) when the key is missing — the caller
    decides if/when to persist it (see the GUI's first-run onboarding)."""
    raw = config_module.read_config_json(config)
    if "restart_actions" in raw:
        return raw["restart_actions"]
    return [dict(a) for a in DEFAULT_ACTIONS]


def write_restart_actions(config, actions: list) -> None:
    """Persist restart_actions into config.json, preserving every other key."""
    raw = config_module.read_config_json(config)
    raw["restart_actions"] = actions
    config_module.write_config_json(config, raw)


def resolve_wallpaper(palette_path: str) -> str:
    """The wallpaper image path associated with palette_path (preview_image,
    falling back to the generation source image, same priority as the
    manage-palettes thumbnail and the main window's wallpaper panel), or
    None if there isn't one or it no longer exists on disk. Shared by
    wallpaper_env() (subprocess env) and write_wallpaper_state() (the
    on-disk record)."""
    if not palette_path:
        return None
    meta = palette_store_module.read_palette_meta(palette_path)
    for candidate in (meta.get("preview_image"), meta.get("image") if meta.get("generated") else None):
        if candidate:
            expanded = color_detector_module.expand_path(candidate)
            if os.path.isfile(expanded):
                return expanded
    return None


def wallpaper_env(palette_path: str) -> dict:
    """{"UCS_WALLPAPER": <expanded path>} for the wallpaper associated with
    palette_path, so restart actions (e.g. the user's own wallpaper-setter)
    can pick it up -- {} if there's none (see resolve_wallpaper)."""
    wallpaper = resolve_wallpaper(palette_path)
    return {"UCS_WALLPAPER": wallpaper} if wallpaper else {}


def write_wallpaper_state(config, palette_path: str) -> None:
    """Persist the current wallpaper to config.wallpaper_state_file, a plain
    text file any external script can read at any later time (unlike
    $UCS_WALLPAPER, which only exists in the environment of the
    restart-action child processes launched at apply/restore time). Clears
    the file when there's no wallpaper to record, so a stale path never
    lingers past the palette/image that produced it."""
    wallpaper = resolve_wallpaper(palette_path)
    with open(config.wallpaper_state_file, "w", encoding="utf-8") as f:
        if wallpaper:
            f.write(wallpaper + "\n")


_running = []


def _reap_finished():
    global _running
    _running = [p for p in _running if p.poll() is None]


def run_enabled(actions: list, extra_env: dict = None, cli: bool = False) -> list:
    """
    Launch every enabled action's command, detached. Never blocks: Popen
    returns immediately after fork+exec, and we never call wait()/
    communicate() on the child, so a `sleep 30` or a hung process inside the
    command has zero effect on the caller.

    extra_env, if given, is merged on top of the current environment for
    every launched command (e.g. wallpaper_env()'s $UCS_WALLPAPER) -- it
    never replaces the inherited environment, so PATH and friends still
    resolve normally.

    cli=True (pass this from every CLI call site; the GUI never does) also
    skips any action with run_on_cli explicitly set to False -- see the
    module docstring for why (avoiding a double wallpaper-set when `ucs
    automatic` is itself invoked as another tool's postcommand hook).

    Returns [{"label", "command", "started": bool, "error": str|None}, ...]
    for the enabled actions only.
    """
    _reap_finished()
    env = None
    if extra_env:
        env = {**os.environ, **extra_env}
    results = []
    for action in actions:
        if not action.get("enabled"):
            continue
        if cli and not action.get("run_on_cli", True):
            continue
        entry = {"label": action["label"], "command": action["command"], "started": False, "error": None}
        try:
            proc = subprocess.Popen(
                action["command"],
                shell=True,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
            _running.append(proc)
            entry["started"] = True
        except OSError as e:
            entry["error"] = str(e)
        results.append(entry)
    return results
