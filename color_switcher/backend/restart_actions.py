#!/usr/bin/env python3
"""
restart_actions.py — Post-apply "restart/reload service" actions.

Each action is {"label", "command", "enabled"}, stored under a
"restart_actions" key in config.json.

Commands are launched detached (new session, stdio to /dev/null, never
waited on) so a slow, sleeping, or failing command can never block the
caller — Popen() itself returns as soon as fork+exec happens.
"""

import subprocess

from . import config as config_module

DEFAULT_ACTIONS = [
    {
        "label": "Reiniciar Waybar",
        "command": "killall waybar 2>/dev/null; command -v waybar >/dev/null && setsid waybar >/dev/null 2>&1 &",
        "enabled": True,
    },
    {
        "label": "Reiniciar Swaync",
        "command": "killall swaync 2>/dev/null; command -v swaync >/dev/null && setsid swaync >/dev/null 2>&1 &",
        "enabled": True,
    },
    {
        "label": "Recargar WiFi Manager",
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


_running = []


def _reap_finished():
    global _running
    _running = [p for p in _running if p.poll() is None]


def run_enabled(actions: list) -> list:
    """
    Launch every enabled action's command, detached. Never blocks: Popen
    returns immediately after fork+exec, and we never call wait()/
    communicate() on the child, so a `sleep 30` or a hung process inside the
    command has zero effect on the caller.

    Returns [{"label", "command", "started": bool, "error": str|None}, ...]
    for the enabled actions only.
    """
    _reap_finished()
    results = []
    for action in actions:
        if not action.get("enabled"):
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
            )
            _running.append(proc)
            entry["started"] = True
        except OSError as e:
            entry["error"] = str(e)
        results.append(entry)
    return results
