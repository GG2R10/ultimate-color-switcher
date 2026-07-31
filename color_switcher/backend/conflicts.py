#!/usr/bin/env python3
"""
conflicts.py — Live conflict checks for the mapping screen.

Case 1 (from the original spec): mapping a detected color to a new-palette
color whose hex already exists among the OTHER detected colors. Once
applied, a future re-detect can no longer tell those occurrences apart —
the user could unknowingly overwrite a color they wanted to keep distinct.

Case 2: the same real color appears in the files in more than one literal
format (e.g. as "#00ccff" and as "rgb(0, 204, 255)"), which detect_colors()
already tracks as separate detected ids. The GUI should offer to map them
together ("same as hex format") instead of asking the user twice.
"""

from .color_detector import pair_of, role_key, role_of


def find_case1_collisions(detected_colors: list, new_palette: list, mapping_entries: list) -> list:
    """
    Returns a list of collisions:
        {"old_id", "new_id", "new_hex", "conflict_with_ids": [...]}
    A collision is reported when the target hex for old_id already belongs
    to a *different real color* than old_id itself — self-mapping to your
    own current color is not a conflict, and neither is mapping to your own
    hex/rgb sibling's current color (they're the same real color already).
    """
    color_by_id = {c["id"]: c for c in detected_colors}
    palette_by_id = {p["id"]: p for p in new_palette}

    detected_by_hex = {}
    for c in detected_colors:
        detected_by_hex.setdefault(c["color"].lower(), []).append(c["id"])

    collisions = []
    for e in mapping_entries:
        old_id, new_id = e["old_id"], e.get("new_id")
        if new_id is None:
            continue
        new_entry = palette_by_id.get(new_id)
        old_entry = color_by_id.get(old_id)
        if not new_entry or not old_entry:
            continue

        own_hex = old_entry["color"].lower()
        new_hex = new_entry["hex"].lower()
        if new_hex == own_hex:
            continue  # mapping to your own (or your sibling's) current color is a no-op, not a conflict
        conflicting_ids = [i for i in detected_by_hex.get(new_hex, []) if i != old_id]
        if conflicting_ids:
            collisions.append({
                "old_id": old_id,
                "new_id": new_id,
                "new_hex": new_hex,
                "conflict_with_ids": conflicting_ids,
            })

    return collisions


def find_target_convergence(detected_colors: list, new_palette: list, mapping_entries: list,
                             sibling_groups: dict = None) -> list:
    """
    Flags mapping entries whose target hex collapses two or more *different*
    real colors into one. Unlike find_case1_collisions (which checks against
    untouched, pre-existing detected colors), this compares mapping targets
    against EACH OTHER: if id A (color X) and id B (color Y, X != Y) both get
    mapped to the same new hex, that distinction is lost on a future
    re-detect. Hex/rgb siblings of the same real color are exempt — mapping
    them to the same target is expected, not a conflict.

    Returns [{"target_hex", "old_ids": [...]}, ...] grouped per colliding target.
    """
    sibling_groups = sibling_groups or {}
    color_by_id = {c["id"]: c for c in detected_colors}
    palette_by_id = {p["id"]: p for p in new_palette}

    real_color_of = {}
    for group in sibling_groups.values():
        ids = frozenset(g["id"] for g in group)
        for i in ids:
            real_color_of[i] = ids

    by_target_hex = {}
    for e in mapping_entries:
        old_id, new_id = e["old_id"], e.get("new_id")
        if new_id is None:
            continue
        entry = palette_by_id.get(new_id)
        color = color_by_id.get(old_id)
        if not entry or not color:
            continue
        by_target_hex.setdefault(entry["hex"].lower(), []).append(old_id)

    collisions = []
    for target_hex, old_ids in by_target_hex.items():
        if len(old_ids) < 2:
            continue
        distinct_reals = {real_color_of.get(oid, frozenset({oid})) for oid in old_ids}
        if len(distinct_reals) > 1:
            collisions.append({"target_hex": target_hex, "old_ids": sorted(old_ids)})

    return collisions


def find_role_mismatches(detected_colors: list, new_palette: list, mapping_entries: list,
                          detected_roles: dict) -> list:
    """
    Flags mapping entries where a detected color explicitly tagged
    foreground/background (color_roles.json, see [[color-roles-design]])
    gets mapped to a palette color explicitly tagged the OPPOSITE role
    (Phase 4's palette-side role column) -- e.g. a color you use as text
    (foreground) about to be replaced by a color someone tagged as a
    background swatch. Neither side being tagged, or both agreeing, is not
    a mismatch. Warn, don't block -- same precedent as
    find_case1_collisions/find_target_convergence.

    Returns [{"old_id", "new_id", "detected_role", "palette_role"}, ...].
    """
    color_by_id = {c["id"]: c for c in detected_colors}
    palette_by_id = {p["id"]: p for p in new_palette}
    opposite = {"foreground": "background", "background": "foreground"}

    mismatches = []
    for e in mapping_entries:
        old_id, new_id = e["old_id"], e.get("new_id")
        if new_id is None:
            continue
        color = color_by_id.get(old_id)
        entry = palette_by_id.get(new_id)
        if not color or not entry:
            continue
        detected_role = role_of(detected_roles, role_key(color["type"], color["color"]))
        palette_role = entry.get("role")
        if detected_role and palette_role and palette_role == opposite[detected_role]:
            mismatches.append({
                "old_id": old_id, "new_id": new_id,
                "detected_role": detected_role, "palette_role": palette_role,
            })
    return mismatches


def find_pair_mismatches(detected_colors: list, new_palette: list, mapping_entries: list,
                         detected_roles: dict) -> list:
    """
    Flags mapping entries where a detected foreground's explicit pairing
    (color_roles.json, see [[color-roles-design]]'s pairing rework) isn't
    reflected in how the palette colors it maps to actually got paired
    (Phase 4's palette-side `paired_id` column) -- e.g. a detected fg/bg
    pair whose mapped palette colors ended up NOT paired with each other.
    Warn, don't block -- same precedent as find_role_mismatches.

    Returns [{"fg_old_id", "bg_old_id", "fg_new_id", "bg_new_id"}, ...] for
    every detected pair where both sides ARE mapped but the palette doesn't
    actually pair them (a pair whose background isn't mapped at all has
    nothing to compare against and is silently skipped, not flagged).

    Deduped by (fg_new_id, bg_new_id): a real color detected in both a
    "hex" and "hex_from_rgb" representation carries the SAME role/pairing
    on each (see compute_role_pairs' docstring) and, in the common case
    where both representations are mapped to the same palette colors,
    would otherwise surface the exact same mismatch twice.
    """
    palette_by_id = {p["id"]: p for p in new_palette}
    new_id_by_old_id = {e["old_id"]: e.get("new_id") for e in mapping_entries}
    # Built once (O(detected_colors)) instead of calling detected_id_for_role_key
    # (an O(detected_colors) linear scan) per foreground-tagged color below --
    # that used to make this whole function O(foreground-tagged x detected_colors).
    old_id_by_role_key = {role_key(c["type"], c["color"]): c["id"] for c in detected_colors}

    mismatches = []
    seen = set()
    for c in detected_colors:
        fg_key = role_key(c["type"], c["color"])
        if role_of(detected_roles, fg_key) != "foreground":
            continue
        bg_key = pair_of(detected_roles, fg_key)
        if not bg_key:
            continue
        bg_old_id = old_id_by_role_key.get(bg_key)
        if bg_old_id is None:
            continue
        fg_new_id = new_id_by_old_id.get(c["id"])
        bg_new_id = new_id_by_old_id.get(bg_old_id)
        if fg_new_id is None or bg_new_id is None:
            continue
        fg_entry = palette_by_id.get(fg_new_id)
        bg_entry = palette_by_id.get(bg_new_id)
        if fg_entry is None or bg_entry is None:
            continue
        if fg_entry.get("paired_id") != bg_new_id or bg_entry.get("paired_id") != fg_new_id:
            dedupe_key = (fg_new_id, bg_new_id)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            mismatches.append({
                "fg_old_id": c["id"], "bg_old_id": bg_old_id,
                "fg_new_id": fg_new_id, "bg_new_id": bg_new_id,
            })
    return mismatches


def find_case2_siblings(detected_colors: list) -> dict:
    """Group detected colors that share the same hex value across formats.
    Returns {hex: [entry, entry, ...]} only for groups with more than one
    member (the ones worth offering a "map together" shortcut for)."""
    groups = {}
    for c in detected_colors:
        groups.setdefault(c["color"].lower(), []).append(c)
    return {hex_val: entries for hex_val, entries in groups.items() if len(entries) > 1}
