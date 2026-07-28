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

import json
import re
import os
from collections import OrderedDict

from . import color_math as cm


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


_ROLE_VALUES = ("foreground", "background")


def role_key(color_type: str, color: str) -> str:
    """The color_roles.json key for a detected color: identified by VALUE
    (type + hex), not by its `id` -- ids are just a rank in a single scan
    (recomputed from scratch every detect_colors() call, so they can shift
    even when nothing about THIS color changed), while the value is the one
    thing that's actually stable across rescans."""
    return f"{color_type}:{color.lstrip('#').lower()}"


def cycle_role(current):
    """Tri-state cycle for the role toggle button: unmarked (None) ->
    background -> foreground -> back to unmarked. Deliberately no
    binary/guessed default -- unmarked means "opt out of the contrast
    system for this color", not an assumed role nobody actually chose."""
    if current is None:
        return "background"
    if current == "background":
        return "foreground"
    return None


def read_color_roles(path: str) -> dict:
    """{"type:hex": {"role": "foreground"|"background", "pair": "type:hex"|None}, ...}
    -- a key's ABSENCE means unmarked, so this file only ever records actual
    user decisions. `pair` (the linked background a foreground contrasts
    against) is only ever meaningful on a foreground entry; background
    entries always carry `pair=None` -- the reverse lookup ("which
    foregrounds point at this background") is derived by scanning (see
    backgrounds_used_as_pair_targets) rather than stored redundantly, since
    one background can be the pair target of many foregrounds.

    Backward compatible with the OLDER shape (bare string values, no pairing
    concept at all) -- a string value transparently upgrades to
    {"role": value, "pair": None}, so an old file never crashes, never
    silently loses its role tags, and never needs a separate migration step.

    Returns {} if the file doesn't exist or is malformed (never raises)."""
    path = expand_path(path)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}

    roles = {}
    for k, v in data.items():
        if not isinstance(k, str):
            continue
        if isinstance(v, str):
            if v in _ROLE_VALUES:
                roles[k] = {"role": v, "pair": None}
        elif isinstance(v, dict):
            role = v.get("role")
            if role not in _ROLE_VALUES:
                continue
            pair = v.get("pair")
            roles[k] = {"role": role, "pair": pair if isinstance(pair, str) else None}

    # A `pair` only means something on a foreground entry, pointing at an
    # existing background entry -- null out anything else (a background
    # carrying a stray pair, a dangling pointer at a key that no longer
    # exists or isn't background anymore) rather than let bad state through.
    for entry in roles.values():
        if entry["role"] != "foreground":
            entry["pair"] = None
            continue
        target = entry["pair"]
        if target is not None and (target not in roles or roles[target]["role"] != "background"):
            entry["pair"] = None

    return roles


def write_color_roles(path: str, roles: dict) -> None:
    """Persists only well-formed {"role": ..., "pair": ...} entries --
    anything else (a malformed value, a dangling/invalid `pair`) is dropped
    or nulled rather than written, same "never write inconsistent state"
    spirit as the old bare-role filtering."""
    path = expand_path(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cleaned = {}
    for k, v in roles.items():
        if not isinstance(v, dict):
            continue
        role = v.get("role")
        if role not in _ROLE_VALUES:
            continue
        cleaned[k] = {"role": role, "pair": v.get("pair")}
    for entry in cleaned.values():
        if entry["role"] != "foreground":
            entry["pair"] = None
            continue
        target = entry["pair"]
        if target is not None and (target not in cleaned or cleaned[target]["role"] != "background"):
            entry["pair"] = None
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2, sort_keys=True)
        f.write("\n")


def role_of(roles: dict, key: str):
    """The role ("foreground"|"background") currently tagged on `key`, or
    None if unmarked. The one-line shim every caller migrating off the old
    bare-string roles-dict shape should use instead of `roles.get(key)`."""
    entry = roles.get(key)
    return entry["role"] if entry else None


def pair_of(roles: dict, key: str):
    """The background key `key` (a foreground) is linked to, or None.
    Meaningless (always None) on anything that isn't currently tagged
    foreground."""
    entry = roles.get(key)
    return entry.get("pair") if entry else None


def set_pair(roles: dict, fg_key: str, bg_key: str = None) -> dict:
    """Link (or, bg_key=None, unlink) `fg_key` (must currently be tagged
    foreground) to `bg_key` (must currently be tagged background). Returns a
    NEW roles dict -- doesn't mutate `roles` in place, so the caller always
    has something fresh to persist via write_color_roles. A no-op copy
    (nothing changed) if the preconditions aren't met -- never raises, same
    convention as the rest of this module."""
    new_roles = {k: dict(v) for k, v in roles.items()}
    fg_entry = new_roles.get(fg_key)
    if fg_entry is None or fg_entry["role"] != "foreground":
        return new_roles
    if bg_key is not None:
        bg_entry = new_roles.get(bg_key)
        if bg_entry is None or bg_entry["role"] != "background":
            return new_roles
    fg_entry["pair"] = bg_key
    return new_roles


def backgrounds_used_as_pair_targets(roles: dict) -> set:
    """Every background key that's currently the pair target of at least one
    foreground -- the reverse lookup, derived by scanning rather than a
    stored pointer (a background can be paired with many foregrounds).

    Matched by HEX VALUE, not exact key: a real background color detected
    in both a "hex" and "hex_from_rgb" representation only ever gets ONE of
    them stored as some foreground's `pair` (whichever was picked as the
    canonical option, e.g. in the GUI's linking dropdown) -- exact-key
    matching would then incorrectly flag the OTHER, equally-real
    representation as "unused" (see [[color-roles-design]]'s hex/rgb-sibling
    dedup bugfix -- this is the same bug class, just one spot it was missed
    in the first pass: this function returns background KEYS, but was
    comparing raw stored `pair` VALUES against them 1:1)."""
    paired_hexes = {
        v["pair"].split(":", 1)[1] for v in roles.values()
        if v["role"] == "foreground" and v.get("pair")
    }
    return {k for k, v in roles.items() if v["role"] == "background" and k.split(":", 1)[1] in paired_hexes}


def clear_dangling_pairs_after_role_change(roles: dict, changed_key: str, new_role) -> dict:
    """Call this right after changing `changed_key`'s role (e.g. via
    cycle_role) to `new_role` -- keeps `pair` pointers consistent instead of
    leaving them dangling:
      - `changed_key` stops being a foreground -> its own `pair` (if any) no
        longer means anything, clear it.
      - `changed_key` stops being a background -> every OTHER foreground
        that was pointing at it as `pair` now dangles, clear those too
        (same "fix up what pointed at the removed thing" precedent as
        mapping_store.drop_and_shift_new_id).
    Returns a NEW roles dict; doesn't mutate `roles` in place."""
    new_roles = {k: dict(v) for k, v in roles.items()}
    if new_role != "foreground" and changed_key in new_roles:
        new_roles[changed_key]["pair"] = None
    if new_role != "background":
        for entry in new_roles.values():
            if entry["role"] == "foreground" and entry.get("pair") == changed_key:
                entry["pair"] = None
    return new_roles


def detected_id_for_role_key(detected_colors: list, key: str):
    """Reverse of role_key: the detected color's `id` for a given role_key,
    or None if it's not currently among detected_colors (e.g. it was
    tagged, then the color stopped being detected)."""
    for c in detected_colors:
        if role_key(c["type"], c["color"]) == key:
            return c["id"]
    return None


def rekey_roles_after_apply(roles_path: str, detected_colors: list, new_palette: list,
                            resolved_entries: list) -> tuple:
    """Call this after a REAL apply (never for --test/dry-run) so a role tag
    (and its pairing) follows the color it was on, across the detect ->
    generate -> apply cycle. Without this, a role tagged on an OLD hex would
    be silently orphaned the moment that hex gets replaced in the files --
    the very next rescan won't find it anymore, and this cycle is the app's
    core recurring workflow, not an edge case.

    detected_colors: the OLD colors the apply just used (id-keyed, same
    list `color_replacer.apply_mapping` was called with).
    new_palette: the palette colors the apply just used (id-keyed, e.g.
    `guiless.apply_palette`'s already-remapped `assigned_palette`, or a
    plain `palette_store.read_palette_csv` result for the id-based path --
    either is fine, this only needs {"id", "hex"} per entry).
    resolved_entries: [{"old_id", "new_id"}, ...] the apply just used --
    already resolved against new_palette's OWN ids by the caller, regardless
    of which of the app's two apply mechanisms (id-based or positional
    compaction) produced them.

    Same-type rekey: the replaced hex keeps the format it was detected in
    (hex stays hex, hex_from_rgb stays hex_from_rgb) -- color_replacer
    substitutes in place, it doesn't change which representation a file uses.

    Returns (role_collisions, pair_collisions):
      - role_collisions: [(new_role_key, [old_role_key, ...]), ...] for every
        convergence where two OLD colors carrying DIFFERENT roles landed on
        the SAME new color -- that new key is deliberately left unmarked
        rather than guessed; the caller should warn about these, mirroring
        the project's existing warn-rather-than-guess precedent for
        ambiguous merges.
      - pair_collisions: [(fg_new_key, dangling_old_bg_key), ...] for every
        SURVIVING foreground whose paired background either collided away
        (above) or no longer resolves to a background at all -- its `pair`
        is cleared rather than left dangling, and reported separately since
        it's a distinct kind of collision the caller should word differently."""
    detected_by_id = {c["id"]: c for c in detected_colors}
    palette_by_id = {p["id"]: p for p in new_palette}

    old_to_new = {}
    for e in resolved_entries:
        old_c = detected_by_id.get(e["old_id"])
        new_c = palette_by_id.get(e["new_id"])
        if old_c is None or new_c is None:
            continue
        old_key = role_key(old_c["type"], old_c["color"])
        new_key = role_key(old_c["type"], new_c["hex"])
        if old_key != new_key:
            old_to_new[old_key] = new_key

    if not old_to_new:
        return [], []

    roles = read_color_roles(roles_path)
    original_roles = {k: dict(v) for k, v in roles.items()}
    incoming = {}
    for old_key, new_key in old_to_new.items():
        entry = roles.get(old_key)
        if entry is None:
            continue
        incoming.setdefault(new_key, []).append((old_key, entry))

    for old_key in old_to_new:
        roles.pop(old_key, None)

    role_collisions = []
    for new_key, contributions in incoming.items():
        distinct_roles = {entry["role"] for _old_key, entry in contributions}
        if len(distinct_roles) == 1:
            _old_key, entry = contributions[0]
            roles[new_key] = dict(entry)
        else:
            roles.pop(new_key, None)
            role_collisions.append((new_key, [k for k, _e in contributions]))

    # Second pass: remap every SURVIVING foreground's `pair` through
    # old_to_new (its background may itself have just been rekeyed), then
    # null out anything that no longer resolves to an existing background --
    # dangling either because that background collided away above, or
    # because it simply isn't tagged background anymore.
    pair_collisions = []
    for key, entry in roles.items():
        if entry["role"] != "foreground" or not entry.get("pair"):
            continue
        old_pair = entry["pair"]
        remapped_pair = old_to_new.get(old_pair, old_pair)
        target = roles.get(remapped_pair)
        if target is not None and target["role"] == "background":
            entry["pair"] = remapped_pair
        else:
            entry["pair"] = None
            pair_collisions.append((key, old_pair))

    if roles != original_roles:
        write_color_roles(roles_path, roles)
    return role_collisions, pair_collisions


def compute_role_pairs(detected_colors: list, roles: dict, mapping_entries: list = None) -> list:
    """Resolve color_roles.json's explicit fg/bg links into the plain
    [{"pair_id", "bg_l", "fg_l"}, ...] shape palette_generator.fgbg_pairing
    expects (bg_l/fg_l the ORIGINAL detected colors' CIE Lab L) -- see
    [[color-roles-design]]'s pairing rework. `pair_id` is a STRING, built
    from fg_hex/bg_hex (the real color VALUES, not the role_keys) -- an
    opaque identity the caller can use to trace a result back to its
    source pair.

    Same mapping-presence filter compute_role_demand used to have: if
    `mapping_entries` is given and non-empty, only pairs where BOTH the
    foreground and its linked background are currently present in the
    mapping (by old_id) count -- a pair tagged but not mapped anywhere
    doesn't need a palette slot yet. If `mapping_entries` is falsy (None or
    empty -- no mapping built yet), every valid pair in `roles` counts,
    unfiltered (same "deliberately generous" reasoning: a soft target for
    generation, over-provisioning a few extra candidates is harmless).

    Deduped by (fg_hex, bg_hex), NOT by role_key: a real color detected in
    BOTH a "hex" and "hex_from_rgb" representation gets tagged/paired on
    EACH representation independently (role/pairing follow "a group is one
    identity" -- see window_main._on_group_role_clicked/
    _on_group_pair_selected, which set the SAME role/pair on every sibling),
    so without deduping here, one real user-intended pair would be counted
    TWICE -- consuming twice the palette slots and demanding 2 generated
    pairs for what the user experiences as 1."""
    if mapping_entries:
        present_ids = {e["old_id"] for e in mapping_entries}
        detected_by_id = {c["id"]: c for c in detected_colors}
        keys_in_use = set()
        for old_id in present_ids:
            c = detected_by_id.get(old_id)
            if c is not None:
                keys_in_use.add(role_key(c["type"], c["color"]))
    else:
        keys_in_use = None  # unfiltered

    hex_by_key = {role_key(c["type"], c["color"]): c["color"] for c in detected_colors}

    pairs = []
    seen = set()
    for fg_key, entry in roles.items():
        if entry["role"] != "foreground" or not entry.get("pair"):
            continue
        bg_key = entry["pair"]
        if keys_in_use is not None and (fg_key not in keys_in_use or bg_key not in keys_in_use):
            continue
        fg_hex = hex_by_key.get(fg_key)
        bg_hex = hex_by_key.get(bg_key)
        if fg_hex is None or bg_hex is None:
            continue  # tagged, but the color isn't currently detected at all
        dedupe_key = (fg_hex, bg_hex)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        fg_l = float(cm.rgb_to_lab(cm.hex_to_rgb(fg_hex))[0])
        bg_l = float(cm.rgb_to_lab(cm.hex_to_rgb(bg_hex))[0])
        pairs.append({"pair_id": f"{fg_hex}::{bg_hex}", "bg_l": bg_l, "fg_l": fg_l})
    return pairs


def tagged_without_pair(roles: dict) -> list:
    """Every role_key tagged foreground/background that ISN'T part of a
    valid pair -- these are simply ignored by generation now (the pairing
    system fully replaces the old flat count), so callers use this to warn
    the user their tag currently does nothing.

    Deduped by hex VALUE (not role_key): a real color detected in both a
    "hex" and "hex_from_rgb" representation gets the same role/pairing
    state on each representation independently (see compute_role_pairs'
    docstring), so without deduping, one real unpaired color would be
    counted twice in a caller's warning."""
    paired_backgrounds = backgrounds_used_as_pair_targets(roles)
    unpaired_keys = [
        k for k, entry in roles.items()
        if (entry["role"] == "background" and k not in paired_backgrounds)
        or (entry["role"] == "foreground" and not entry.get("pair"))
    ]
    seen_hex = set()
    deduped = []
    for k in unpaired_keys:
        hex_val = k.split(":", 1)[1]
        if hex_val in seen_hex:
            continue
        seen_hex.add(hex_val)
        deduped.append(k)
    return deduped


def generate_regex_for_color(hex_color: str) -> str:
    """Generate a regex pattern to match this hex color (with/without #)."""
    c = hex_color.lstrip("#").lower()
    return f"(#?){c}"


def generate_rgb_regex_for_color(hex_color: str) -> str:
    """Generate a regex to match this color as rgb(r, g, b), spaces tolerant."""
    r, g, b = hex_to_rgb(hex_color)
    return f"{r}\\s*,\\s*{g}\\s*,\\s*{b}"


# --- Auto-scan ~/.config for color-bearing config files -----------------------
#
# First-run helper: propose files_to_replace candidates by walking ~/.config
# for config-format files that actually contain colors, so the user starts
# from a real base instead of an empty list. It is deliberately a *proposal* --
# HEX_PATTERN matches any 6 hex digits (so a memory address 0xDEADBE or a hash
# can read as a "color"), and a file can be config-shaped without being one you
# want rewritten. The GUI/CLI both warn about this and let the user drop the
# junk (fine-grained tree in the GUI, `config files remove` / --dry-run in the
# CLI). The format allowlist below is what keeps the noise manageable.

_SCAN_COLOR_EXTENSIONS = frozenset({
    ".conf", ".ini", ".toml", ".yaml", ".yml", ".json", ".jsonc", ".lua", ".sh",
    ".bash", ".zsh", ".fish", ".el", ".micro", ".css", ".rasi", ".theme",
    ".colors", ".qss",
})
_SCAN_MAX_BYTES = 1_000_000  # skip anything bigger -- not a hand-edited config


def _is_scannable_config_name(name: str) -> bool:
    """A file worth opening: a known config extension, or literally named
    'config' (git, some app configs have no extension)."""
    lower = name.lower()
    if lower == "config":
        return True
    return os.path.splitext(lower)[1] in _SCAN_COLOR_EXTENSIONS


def _scan_dir_should_skip(name: str) -> bool:
    """Prune heavy/noise directories by name: caches, logs, VCS internals,
    dependency trees -- case-insensitive substring for cache/logs so
    'Cache', 'GPUCache', 'Logs' all match."""
    lower = name.lower()
    return name in (".git", "node_modules") or "cache" in lower or "logs" in lower


def _file_has_colors(path: str, min_hits: int = 1) -> bool:
    """Whether `path` is a small, text (non-binary) file with at least
    `min_hits` color matches. Size/binary checks run before reading the whole
    file so the walk stays cheap."""
    try:
        if os.path.getsize(path) > _SCAN_MAX_BYTES:
            return False
        with open(path, "rb") as f:
            head = f.read(1024)
    except OSError:
        return False
    if b"\x00" in head:  # a NUL byte in the first KB -> treat as binary
        return False

    counts = detect_colors_in_file(path)
    total = sum(counts["hex"].values()) + sum(counts["rgb"].values())
    return total >= min_hits


def scan_config_dir_for_color_files(config_dir: str = None, min_hits: int = 1) -> list:
    """Walk `config_dir` (default ~/.config) for config-format files that
    contain colors. Skips heavy dirs (see _scan_dir_should_skip), does NOT
    follow directory symlinks (dotfile managers symlink real files in, which
    are still listed and included; only recursion into symlinked dirs is
    avoided, dodging loops and escapes out of the tree). Returns absolute
    paths, sorted -- callers convert to home-relative before storing."""
    base = config_dir or os.path.join(os.path.expanduser("~"), ".config")
    found = []
    if not os.path.isdir(base):
        return found
    for root, dirs, files in os.walk(base, followlinks=False):
        dirs[:] = [d for d in dirs if not _scan_dir_should_skip(d)]
        for name in files:
            if not _is_scannable_config_name(name):
                continue
            path = os.path.join(root, name)
            if _file_has_colors(path, min_hits=min_hits):
                found.append(path)
    return sorted(found)


def group_paths_by_top_level(paths: list, base_dir: str = None) -> list:
    """Group by the FIRST directory segment under base_dir (~/.config), so a
    deeply-nested tree (e.g. ~/.config/claude/projects/a/b/c.json) collapses
    into one toggleable group per top-level ~/.config child instead of a
    separate group per leaf folder -- otherwise a single app with hundreds of
    nested files floods the list with hundreds of near-identical folder rows.

    Returns [(top_folder_abs, [(abs_path, display), ...]), ...] sorted, where
    `display` is the path relative to top_folder so files deep in different
    subfolders stay distinguishable (a bare basename would collide)."""
    base = base_dir or os.path.join(os.path.expanduser("~"), ".config")
    groups = OrderedDict()
    for p in sorted(paths):
        rel = os.path.relpath(p, base)
        parts = rel.split(os.sep)
        if len(parts) == 1:  # file sitting directly in ~/.config
            top, display = base, parts[0]
        else:
            top, display = os.path.join(base, parts[0]), os.sep.join(parts[1:])
        groups.setdefault(top, []).append((p, display))
    return [(top, files) for top, files in sorted(groups.items())]
