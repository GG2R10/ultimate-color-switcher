#!/usr/bin/env python3
"""
guiless.py — Apply a color palette without going through the GUI.

Given a fresh palette (in the order the caller wants it applied) and an
existing, persistent mapping (built previously in the GUI or `mapping new`),
resolves each mapping entry's new_id against the palette via
mapping_store.resolve_apply_targets — the SAME resolver every apply path in
this app uses (GUI, `apply`/`test`, `palette shift --apply`). A new_id's own
numeric VALUE is the target position (new_id=3 means "the 3rd color of the
palette"); when the palette is too small for the highest new_id referenced,
the mapping's DISTINCT new_id values are compacted onto dense positions in
ASCENDING VALUE order (never insertion order — see resolve_apply_targets'
docstring for why this superseded the previous insertion-order design,
formerly ROADMAP.md's "GUIless mode" decision #4).

If the palette has fewer colors than distinct new_id values needed, nothing
is applied — there is no color to assign to the missing slot(s), and this is
never overridden (regenerate a palette with enough colors instead). If the
palette has MORE colors than needed, the extra ones are simply unused; the
caller must pass yolo=True after confirming with the user. Real mapping
conflicts (case 1 / convergence) are a separate gate, waved through only by
force=True — so "extra colors are fine" (yolo) never silently accepts a
conflict.
"""

import json
import os

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
            against) -- OR an already-loaded mapping_store.MappingStore
            instance (mirrors palette_source's own path-or-already-loaded
            flexibility), e.g. one resolved via mapping_store.MappingRegistry
            .for_palette/.for_active. This module stays deliberately agnostic
            of the registry itself -- callers resolve however they need
            (standalone file or registry section) and hand the result here.
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
         "conflicts", "applied"}. Every status except "empty_mapping" also
        carries "pre_apply_drift" -- mapping_store.check_and_relink_drift's
        result for the mapping BEFORE this call did anything else with it
        (see that function), so a caller can report a relink/merge/orphan
        that happened even if the run didn't get as far as "applied". On
        "applied", "reorder_warning" is a string
        (mapping_store.resolve_apply_targets' tier="compacted" warning) when
        the palette was too small for the mapping's highest new_id and had to
        be compacted, else None; "collisions"/"convergence" (only ever
        non-empty when this was force-applied despite them, since otherwise
        "conflicts" would have been returned instead) are the same lists
        conflicts.find_case1_collisions/find_target_convergence produce, for
        a caller that wants to auto-relink the mapping's now-certainly-stale
        ids -- both cases leave the replaced color coinciding with another
        real detected color, so a rescan collapses two old_ids into one --
        see mapping_store.apply_drift_relinks.
    """
    if isinstance(mapping_path, mapping_store.MappingStore):
        store = mapping_path
    else:
        store = mapping_store.MappingStore(mapping_path, project_dir=project_dir).load()
    if not store.resolved_entries():
        return {"status": "empty_mapping"}
    old_palette_path = store.old_palette

    detected_colors = color_detector.read_detected_csv(old_palette_path)
    # store may have sat inactive since it was last touched -- its old_ids
    # could be silently WRONG against detected_colors (coincidentally reused
    # by a different real color since then, since ids are just a rank
    # recomputed every scan). Resolve that now, BEFORE the collision/
    # convergence checks below (which are computed from old_id too, and
    # would just be checking garbage otherwise) and before it's used to
    # apply anything. persist=False for a dry-run/simulate -- store.entries
    # is corrected in memory either way, so the simulated result is
    # accurate, but nothing is written to disk for a run that's not real.
    pre_apply_drift = mapping_store.check_and_relink_drift(store, detected_colors, persist=not dry_run)
    entries = store.resolved_entries()

    palette = _load_palette(palette_source)

    assigned_palette = [
        {"id": i + 1, "hex": c["hex"].lstrip("#").lower(), "label": c.get("label", "")}
        for i, c in enumerate(palette)
    ]

    resolution = mapping_store.resolve_apply_targets(entries, assigned_palette)
    if resolution["tier"] == "blocked":
        return {"status": "insufficient_palette", "pre_apply_drift": pre_apply_drift,
                "needed": resolution["needed"], "available": resolution["available"]}

    surplus_palette_count = resolution["available"] - resolution["needed"]
    if surplus_palette_count and not yolo:
        return {"status": "needs_confirmation", "pre_apply_drift": pre_apply_drift,
                "surplus_palette_count": surplus_palette_count}

    resolved_entries = resolution["final_entries"]

    siblings = conflicts.find_case2_siblings(detected_colors)
    collisions = conflicts.find_case1_collisions(detected_colors, assigned_palette, resolved_entries)
    convergence = conflicts.find_target_convergence(
        detected_colors, assigned_palette, resolved_entries, sibling_groups=siblings
    )
    if (collisions or convergence) and not force:
        return {"status": "conflicts", "pre_apply_drift": pre_apply_drift,
                "conflicts": collisions, "convergence": convergence}

    results = color_replacer.apply_mapping(
        detected_colors, assigned_palette, resolved_entries, backup_dir, dry_run=dry_run
    )
    role_collisions, pair_collisions = [], []
    if not dry_run:
        roles_path = os.path.join(os.path.dirname(old_palette_path), "color_roles.json")
        role_collisions, pair_collisions = color_detector.rekey_roles_after_apply(
            roles_path, detected_colors, assigned_palette, resolved_entries
        )
        # The colors just replaced no longer exist in the files BY DESIGN --
        # re-stamp this mapping's identity to the NEW colors now actually
        # there, so the post-apply drift refresh doesn't mistake "I just
        # replaced this" for real drift and flag it orphaned. Reuses the
        # SAME store the drift-check above already relinked in memory --
        # a fresh reload here would just be wasted I/O (its relink was
        # already persisted above, this store already reflects it).
        store.entries = mapping_store.stamp_applied_entries(
            store.entries, resolved_entries, assigned_palette, detected_colors,
        )
        store.save()
    return {
        "status": "applied",
        "results": results,
        "stale_mapping_warning": bool(collisions or convergence) and force,
        "pre_apply_drift": pre_apply_drift,
        "collisions": collisions,
        "convergence": convergence,
        "reorder_warning": resolution["warning"],
        "role_collisions": role_collisions,
        "pair_collisions": pair_collisions,
    }
