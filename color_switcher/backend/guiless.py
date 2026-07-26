#!/usr/bin/env python3
"""
guiless.py — Apply a color palette without going through the GUI.

Given a fresh palette (in the order the caller wants it applied) and an
existing, persistent mapping (built previously in the GUI or `mapping new`),
assigns each DISTINCT target slot the mapping actually uses (its stored
new_id — 1=primary, 2=secondary, ... by the same convention
palette_generator/palette_store use) to the next color of the palette, in
ascending slot order. What matters is how many distinct roles the mapping
needs, not the highest new_id number it happens to reference — a mapping
that only ever touched "primary" (1) and "aux1" (3), skipping "secondary",
still only needs a 2-color palette.

If the palette has fewer colors than distinct roles needed, nothing is
applied — there is no color to assign to the missing role(s), and this is
never overridden (regenerate a palette with enough colors instead). If the
palette has MORE colors than needed, the extra ones are simply unused; the
caller must pass yolo=True after confirming with the user. Real mapping
conflicts (case 1 / convergence) are a separate gate, waved through only by
force=True — so "extra colors are fine" (yolo) never silently accepts a
conflict.
"""

import json

from . import color_detector, color_replacer, conflicts, mapping_store, palette_store


def load_palette(source):
    """Accepts a path to a palette CSV (id,#hex,label — same format
    everywhere else in this app, e.g. one produced by
    palette_generator.generate_palette), a path to a JSON [{hex,label}, ...]
    file, or an already-loaded list of dicts. Public: also used by `palette
    show --apply` to display/apply CSV, JSON, or (via the caller reading
    stdin first) an ad hoc palette without caring which it is."""
    if isinstance(source, str):
        if source.lower().endswith(".csv"):
            entries = palette_store.read_palette_csv(source)
            return [{"hex": e["hex"], "label": e.get("label", "")} for e in entries]
        with open(source, "r", encoding="utf-8") as f:
            return json.load(f)
    return source


_load_palette = load_palette  # internal alias, kept for readability at the call site below


def apply_palette(
    palette_source,
    mapping_path: str,
    backup_dir: str,
    dry_run: bool = False,
    force: bool = False,
    yolo: bool = False,
    project_dir: str = None,
) -> dict:
    """
    Args:
        palette_source: path to a palette CSV or JSON file, or an
            already-loaded list of {"hex": "...", "label": "..."} dicts.
        mapping_path: path to a saved mapping CSV (has #old_palette= /
            #new_palette= headers pointing at the detected CSV it was built
            against).
        backup_dir: where color_replacer.backup_files should mirror originals.
        dry_run: simulate without touching files or creating a backup.
        force: skip the case-1/convergence conflict check only (pass after the
            caller has confirmed with the user). Does NOT bypass an
            insufficient palette (see module docstring) nor the surplus-color
            confirmation (that's `yolo`).
        yolo: skip the surplus-palette confirmation only -- i.e. apply even
            though the palette has more colors than the mapping needs. Kept
            separate from `force` so accepting "extra colors, that's fine"
            doesn't also silently wave through real mapping conflicts.
        project_dir: passed through to mapping_store.read_mapping_csv so a
            project-relative #old_palette= header resolves correctly.

    Returns a dict with "status" in
        {"empty_mapping", "insufficient_palette", "needs_confirmation",
         "conflicts", "applied"}.
    """
    old_palette_path, _new_palette_path, entries = mapping_store.read_mapping_csv(
        mapping_path, project_dir=project_dir
    )
    if not entries:
        return {"status": "empty_mapping"}

    detected_colors = color_detector.read_detected_csv(old_palette_path)
    palette = _load_palette(palette_source)

    n_palette = len(palette)
    distinct_slots = sorted({e["new_id"] for e in entries})
    n_needed = len(distinct_slots)

    if n_needed > n_palette:
        return {"status": "insufficient_palette", "needed": n_needed, "available": n_palette}

    surplus_palette_count = n_palette - n_needed
    if surplus_palette_count and not yolo:
        return {"status": "needs_confirmation", "surplus_palette_count": surplus_palette_count}

    # Compact the mapping's distinct roles (whatever numbers they happened
    # to be saved under) onto 1..n_needed, in ascending role order, then pair
    # each with the palette color at that position.
    slot_remap = {old_slot: i + 1 for i, old_slot in enumerate(distinct_slots)}
    assigned_palette = [
        {"id": i + 1, "hex": c["hex"].lstrip("#").lower(), "label": c.get("label", "")}
        for i, c in enumerate(palette)
    ]
    resolved_entries = [
        {"old_id": e["old_id"], "new_id": slot_remap[e["new_id"]]} for e in entries
    ]

    siblings = conflicts.find_case2_siblings(detected_colors)
    collisions = conflicts.find_case1_collisions(detected_colors, assigned_palette, resolved_entries)
    convergence = conflicts.find_target_convergence(
        detected_colors, assigned_palette, resolved_entries, sibling_groups=siblings
    )
    if (collisions or convergence) and not force:
        return {"status": "conflicts", "conflicts": collisions, "convergence": convergence}

    results = color_replacer.apply_mapping(
        detected_colors, assigned_palette, resolved_entries, backup_dir, dry_run=dry_run
    )
    return {
        "status": "applied",
        "results": results,
        "stale_mapping_warning": bool(collisions or convergence) and force,
    }
