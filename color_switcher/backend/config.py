#!/usr/bin/env python3
"""
config.py — Load config.json and resolve the standard project directories.
"""

import json
import os
from dataclasses import dataclass, field

from .color_detector import expand_path


@dataclass
class Config:
    project_dir: str
    files_to_replace: list = field(default_factory=list)
    backup_dir: str = ""
    silent_apps: list = field(default_factory=list)
    restart_commands: list = field(default_factory=list)
    mappings_dir: str = ""
    palettes_created_dir: str = ""
    palettes_detected_dir: str = ""

    @property
    def detected_palette_csv(self) -> str:
        """Canonical detected-palette file. Lives under palettes_detected_dir,
        alongside named snapshots (detect_diff.save_snapshot) -- NOT under
        backup_dir, unlike the deprecated bash tool this replaced."""
        return os.path.join(self.palettes_detected_dir, "detected_palette.csv")

    @property
    def mapping_csv(self) -> str:
        """LEGACY, single mapping file -- superseded by mapping_registry_json
        (one mapping PER palette + an active pointer, see
        mapping_store.MappingRegistry). Kept only as the one-time migration
        source (mapping_store.migrate_legacy_mapping_csv_if_needed) and for
        any external script/escape hatch that still wants a single standalone
        file (`mapping new --out <file>`, `apply --mapping <file>`) -- no
        current flow reads/writes this path as its own default anymore."""
        return os.path.join(self.mappings_dir, "mapping.csv")

    @property
    def mapping_registry_json(self) -> str:
        """The current canonical mapping storage: one JSON file holding a
        MappingStore section per target palette, plus an `active` pointer --
        see mapping_store.MappingRegistry. Replaces the single mapping_csv;
        switching which palette you're working on no longer clobbers a
        different palette's already-tuned mapping."""
        return os.path.join(self.mappings_dir, "mappings.json")

    @property
    def generated_palette_csv(self) -> str:
        """Canonical, single "last generated from image" palette file --
        overwritten in place unless the caller names an explicit --out
        (same convention as detected_palette_csv/mapping_csv, instead of a
        fresh generated-<image>-<timestamp>.csv per run)."""
        return os.path.join(self.palettes_created_dir, "generated.csv")

    @property
    def color_roles_json(self) -> str:
        """Cosmetic foreground/background role tags for detected colors,
        keyed by "type:hex" -- deliberately its own file, separate from
        detected_palette_csv (which must stay a pure, reproducible function
        of the scanned files -- roles are user state, not file-derived) and
        from mapping.csv (deleting/recreating a mapping shouldn't wipe role
        tags). See the color-roles design notes."""
        return os.path.join(self.palettes_detected_dir, "color_roles.json")


_DEFAULT_CONFIG_JSON = {"files_to_replace": [], "backup_dir": "$HOME/.config-colors-backup"}


def _default_project_dir() -> str:
    """Per-user data dir (XDG_CONFIG_HOME/ucs, usually ~/.config/ucs) --
    independent of wherever the installed package's code actually lives."""
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(xdg_config_home, "ucs")


def load_config(project_dir: str = None) -> Config:
    project_dir = project_dir or _default_project_dir()
    os.makedirs(project_dir, exist_ok=True)
    cfg_path = os.path.join(project_dir, "config.json")

    if not os.path.isfile(cfg_path):
        # first run: bootstrap an empty config instead of crashing --
        # the user adds files via `ucs config files add` or the GUI menu.
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(_DEFAULT_CONFIG_JSON, f, indent=2)
            f.write("\n")

    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    files_to_replace = [expand_path(p) for p in raw.get("files_to_replace", [])]
    backup_dir = expand_path(raw.get("backup_dir", "~/.config-colors-backup"))
    silent_apps = raw.get("silent_apps", [])
    restart_commands = raw.get("restart_commands", [])

    mappings_dir = os.path.join(project_dir, "mappings")
    palettes_created_dir = os.path.join(project_dir, "palettes", "created")
    palettes_detected_dir = os.path.join(project_dir, "palettes", "detected")

    for d in (mappings_dir, palettes_created_dir, palettes_detected_dir, backup_dir):
        os.makedirs(d, exist_ok=True)

    return Config(
        project_dir=project_dir,
        files_to_replace=files_to_replace,
        backup_dir=backup_dir,
        silent_apps=silent_apps,
        restart_commands=restart_commands,
        mappings_dir=mappings_dir,
        palettes_created_dir=palettes_created_dir,
        palettes_detected_dir=palettes_detected_dir,
    )


def _config_json_path(config: Config) -> str:
    return os.path.join(config.project_dir, "config.json")


def read_config_json(config: Config) -> dict:
    """The raw, un-expanded config.json contents."""
    with open(_config_json_path(config), "r", encoding="utf-8") as f:
        return json.load(f)


def write_config_json(config: Config, raw: dict) -> None:
    with open(_config_json_path(config), "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2)
        f.write("\n")


def to_home_relative(path: str) -> str:
    """Store an absolute path under $HOME as "$HOME/..." (same convention
    backup_dir already uses), so config.json stays portable across machines
    that share these dotfiles. Paths outside $HOME are left as absolute."""
    home = os.path.expanduser("~")
    absolute = os.path.abspath(expand_path(path))
    if absolute == home or absolute.startswith(home + os.sep):
        return "$HOME" + absolute[len(home):]
    return absolute


def read_files_to_replace(config: Config) -> list:
    """Raw (un-expanded, e.g. still "$HOME/...") files_to_replace strings,
    straight from config.json -- for listing/editing. Use
    config.files_to_replace (already expanded) to actually scan files."""
    return list(read_config_json(config).get("files_to_replace", []))


def write_files_to_replace(config: Config, files: list) -> None:
    """Persist files_to_replace into config.json, preserving every other key."""
    raw = read_config_json(config)
    raw["files_to_replace"] = files
    write_config_json(config, raw)
