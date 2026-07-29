#!/usr/bin/env python3
"""
mapping_store.py — Ordered, persistent old_id -> new_id mapping.

On-disk format:

    #old_palette=<path to detected_palette.csv>
    #new_palette=<path to target palette.csv>
    old_id,new_id
    <old_id>,<new_id>
    ...

Row order in the file IS the insertion order — entries are only ever
appended, never re-sorted, so both this module and any consumer that walks
the file top to bottom (e.g. guiless.py's automatic mode) agree on order.

Unresolved entries (a detected color added to the mapping but not yet
assigned a target) are kept in memory with new_id=None and are simply not
written to disk until resolved.

Entries MAY also carry an "old_type"/"old_hex" identity stamp -- a snapshot
of the detected color (role_key-style: type + hex) that old_id pointed at
when the stamp was last refreshed. This is drift protection: detected ids
are just a rank recomputed on every scan (see color_detector.role_key's
docstring), so old_id alone can silently point at the wrong color after a
rescan. See stamp_detected_identity/detect_drift/refresh_identity_stamps.
Following palette_store.write_palette_csv's convention, the two extra
columns (old_id,new_id,old_type,old_hex) are only emitted when at least one
entry actually carries a stamp -- a plain legacy 2-column file round-trips
byte-identical.

If project_dir is given to read/write, old_palette/new_palette are stored
relative to it whenever they live inside it (so the mapping stays valid if
the whole project folder moves or is copied elsewhere) and resolved back to
absolute on read. Paths outside project_dir (e.g. an externally-picked
file) are left absolute, since there's no meaningful relative form for
those.
"""

import json
import os
from datetime import datetime

from .color_detector import expand_path

HEADER_LINE = "old_id,new_id"
HEADER_LINE_STAMPED = "old_id,new_id,old_type,old_hex"


def _to_relative(path: str, project_dir: str) -> str:
    if not path or not project_dir:
        return path or ""
    try:
        rel = os.path.relpath(os.path.abspath(expand_path(path)), project_dir)
    except ValueError:
        return path  # e.g. different drive on Windows
    return path if rel.startswith("..") else rel


def _to_absolute(path: str, project_dir: str) -> str:
    if not path or not project_dir or os.path.isabs(path):
        return path
    return os.path.join(project_dir, path)


def read_mapping_csv(path: str, project_dir: str = None):
    """Returns (old_palette_path, new_palette_path, entries) — entries is an
    ordered list of {"old_id": int, "new_id": int, "old_type": str|None,
    "old_hex": str|None}, in file order. old_type/old_hex are None when the
    file is a plain legacy 2-column mapping (no identity stamp)."""
    path = expand_path(path)
    old_palette = None
    new_palette = None
    entries = []

    if not os.path.isfile(path):
        return old_palette, new_palette, entries

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()

    data_started = False
    for line in lines:
        if not data_started:
            if line.startswith("#old_palette="):
                old_palette = line[len("#old_palette="):]
            elif line.startswith("#new_palette="):
                new_palette = line[len("#new_palette="):]
            elif line.strip() in (HEADER_LINE, HEADER_LINE_STAMPED):
                data_started = True
            continue

        if not line.strip():
            continue
        parts = line.split(",")
        if len(parts) < 2:
            continue
        old_id_str, new_id_str = parts[0].strip(), parts[1].strip()
        if not old_id_str.isdigit() or not new_id_str.isdigit():
            continue
        entry = {"old_id": int(old_id_str), "new_id": int(new_id_str)}
        old_type = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
        old_hex = parts[3].strip() if len(parts) > 3 and parts[3].strip() else None
        # Only present on the dict when actually stamped -- keeps an
        # unstamped entry's shape exactly {"old_id","new_id"}, unchanged from
        # before identity stamps existed (every existing caller/test compares
        # entries against bare 2-key dict literals).
        if old_type is not None:
            entry["old_type"] = old_type
        if old_hex is not None:
            entry["old_hex"] = old_hex
        entries.append(entry)

    return _to_absolute(old_palette, project_dir), _to_absolute(new_palette, project_dir), entries


def write_mapping_csv(path: str, old_palette: str, new_palette: str, entries: list,
                       project_dir: str = None) -> None:
    """Write header metadata + resolved rows, in the given entries order. The
    old_type/old_hex columns are only emitted if at least one entry carries a
    stamp (see module docstring) -- a plain legacy file round-trips
    byte-identical when nothing has ever stamped it."""
    path = expand_path(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_stamp = any(e.get("old_type") for e in entries)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"#old_palette={_to_relative(old_palette, project_dir)}\n")
        f.write(f"#new_palette={_to_relative(new_palette, project_dir)}\n")
        f.write(f"{HEADER_LINE_STAMPED if write_stamp else HEADER_LINE}\n")
        for e in entries:
            if e.get("new_id") is None:
                continue
            if write_stamp:
                f.write(f"{e['old_id']},{e['new_id']},{e.get('old_type') or ''},{e.get('old_hex') or ''}\n")
            else:
                f.write(f"{e['old_id']},{e['new_id']}\n")


def drop_and_shift_new_id(entries: list, deleted_new_id: int) -> list:
    """Adjust a mapping after the palette color at `deleted_new_id` (1-based,
    contiguous) was removed: entries pointing at it are unassigned (new_id
    None), and higher new_ids shift down by one to follow the palette's
    renumbering. Returns a NEW entries list (input untouched)."""
    out = []
    for e in entries:
        nid = e.get("new_id")
        if nid == deleted_new_id:
            nid = None
        elif nid is not None and nid > deleted_new_id:
            nid -= 1
        out.append({"old_id": e["old_id"], "new_id": nid})
    return out


def resolve_apply_targets(entries: list, new_palette: list) -> dict:
    """The single apply-resolution algorithm shared by every apply path (GUI,
    `apply`/`test`, `automatic`/guiless.apply_palette, `palette shift --apply`).

    entries: resolved {"old_id","new_id"} mapping entries (no None new_id).
    new_palette: palette entries carrying "id" (always 1..N contiguous, see
    palette_store.write_palette_csv/palette_shift.derive_effective).

    A new_id's own numeric VALUE is the target palette POSITION (new_id=3
    means "the 3rd color of the palette") -- this already holds for free
    whenever the palette has enough colors (tier "exact"). When it doesn't,
    we fall back to compacting the mapping's DISTINCT new_id values onto
    dense positions 1..len(distinct), in ASCENDING VALUE order (never
    insertion order -- insertion order depends on the order entries happen
    to be listed/edited in, which is not a stable, reproducible ordering to
    apply reorder-risk logic against; ascending value is deterministic and
    matches what "new_id" already means everywhere new_id fits).

    Returns {"tier": "exact"|"compacted"|"blocked",
             "final_entries": [...],  # ready for color_replacer.apply_mapping
             "needed": int, "available": int, "max_new_id": int,
             "warning": str|None}     # populated only for tier "compacted"
    """
    available = len(new_palette)
    if not entries:
        return {"tier": "exact", "final_entries": [], "needed": 0,
                "available": available, "max_new_id": 0, "warning": None}

    max_new_id = max(e["new_id"] for e in entries)
    distinct = sorted({e["new_id"] for e in entries})

    if available >= max_new_id:
        return {"tier": "exact", "final_entries": list(entries), "needed": max_new_id,
                "available": available, "max_new_id": max_new_id, "warning": None}

    if available >= len(distinct):
        remap = {v: i + 1 for i, v in enumerate(distinct)}
        final_entries = [{"old_id": e["old_id"], "new_id": remap[e["new_id"]]} for e in entries]
        warning = (
            f"La paleta tiene {available} color(es), menos que el máximo id referenciado "
            f"por el mapping ({max_new_id}). Se reasignó por orden de valor a los "
            f"{len(distinct)} distintos usados -- el orden de asignación de tu mapping "
            "CAMBIÓ y podría romper temas que ya habías afinado. Revisá el resultado antes "
            "de confiar en él."
        )
        return {"tier": "compacted", "final_entries": final_entries, "needed": len(distinct),
                "available": available, "max_new_id": max_new_id, "warning": warning}

    return {"tier": "blocked", "final_entries": [], "needed": len(distinct),
            "available": available, "max_new_id": max_new_id, "warning": None}


def stamp_detected_identity(entries: list, detected_colors: list) -> list:
    """New list: every entry whose old_id currently resolves in
    detected_colors gets old_type/old_hex set to that color's CURRENT
    (type, color) -- a raw "snapshot everything resolvable" primitive, with
    no opinion about drift (see refresh_identity_stamps for the safe,
    drift-aware version to actually call after a (re-)detection). Entries
    whose old_id doesn't currently resolve are returned unchanged."""
    by_id = {c["id"]: c for c in detected_colors}
    out = []
    for e in entries:
        c = by_id.get(e["old_id"])
        if c is None:
            out.append(dict(e))
            continue
        stamped = dict(e)
        stamped["old_type"] = c["type"]
        stamped["old_hex"] = c["color"].lstrip("#").lower()
        out.append(stamped)
    return out


def detect_drift(entries: list, detected_colors: list) -> dict:
    """Classify every STAMPED entry (old_type/old_hex present) against a
    fresh detected_colors list. Unstamped entries (never identity-tracked
    yet) are simply skipped -- there's no prior identity to compare against.

    Returns {"ok": [old_id, ...],
             "driftable": [{"old_id","correct_old_id","type","hex"}, ...],
             "orphaned": [{"old_id","type","hex"}, ...]}
      - ok: the stamped (type, hex) is still the color currently AT old_id.
      - driftable: the stamped (type, hex) still exists in detected_colors,
        but at a DIFFERENT id -- old_id should become correct_old_id.
      - orphaned: the stamped (type, hex) doesn't exist in detected_colors
        at all anymore -- flag for the user, never auto-resolved.
    """
    by_id = {c["id"]: c for c in detected_colors}
    by_key = {}
    for c in detected_colors:
        by_key.setdefault((c["type"], c["color"].lstrip("#").lower()), c["id"])

    ok, driftable, orphaned = [], [], []
    for e in entries:
        old_type, old_hex = e.get("old_type"), e.get("old_hex")
        if old_type is None or old_hex is None:
            continue
        current = by_id.get(e["old_id"])
        if (current is not None and current["type"] == old_type
                and current["color"].lstrip("#").lower() == old_hex):
            ok.append(e["old_id"])
            continue
        correct_id = by_key.get((old_type, old_hex))
        if correct_id is not None:
            driftable.append({"old_id": e["old_id"], "correct_old_id": correct_id,
                               "type": old_type, "hex": old_hex})
        else:
            orphaned.append({"old_id": e["old_id"], "type": old_type, "hex": old_hex})
    return {"ok": ok, "driftable": driftable, "orphaned": orphaned}


def refresh_identity_stamps(entries: list, detected_colors: list) -> tuple:
    """The single entry point GUI/CLI call after every (re-)detection: stamps
    every entry that's unstamped or confirmed "ok" against the fresh
    detected_colors, but NEVER re-stamps an entry currently flagged
    driftable/orphaned -- doing so would silently erase the exact signal the
    user needs to act on (a driftable entry's stamp still correctly
    describes the real color; only its old_id is wrong).

    Returns (new_entries, drift) -- caller persists new_entries and presents
    drift (see detect_drift) to the user."""
    drift = detect_drift(entries, detected_colors)
    unsafe_ids = {d["old_id"] for d in drift["driftable"]} | {d["old_id"] for d in drift["orphaned"]}
    safe_entries = [e for e in entries if e["old_id"] not in unsafe_ids]
    unsafe_entries = [e for e in entries if e["old_id"] in unsafe_ids]
    by_old_id = {e["old_id"]: e for e in stamp_detected_identity(safe_entries, detected_colors)}
    by_old_id.update({e["old_id"]: dict(e) for e in unsafe_entries})
    new_entries = [by_old_id[e["old_id"]] for e in entries]
    return new_entries, drift


def apply_drift_relinks(entries: list, driftable: list) -> list:
    """Apply every driftable finding from detect_drift in ONE ATOMIC pass --
    each entry's old_id is rewritten by looking up the ORIGINAL (pre-relink)
    old_id in a precomputed {old_id: correct_old_id} map, all from the SAME
    entries list detect_drift was run against. This must stay atomic rather
    than done as sequential single-entry relinks: the most common drift case
    is exactly two colors trading rank (e.g. an edited file shifts which of
    two colors is more frequent), and relinking one before the other would
    find the other "in the way" of its target id (or, worse, silently
    corrupt it if processed against a store already mutated by the first
    relink) -- a single-pass remap over the original list sidesteps both
    problems entirely, for a swap or any longer cycle."""
    remap = {d["old_id"]: d["correct_old_id"] for d in driftable}
    new_entries = []
    for e in entries:
        if e["old_id"] in remap:
            e = dict(e)
            e["old_id"] = remap[e["old_id"]]
        new_entries.append(e)
    return new_entries


def stamp_applied_entries(entries: list, applied_entries: list, new_palette: list,
                           detected_colors: list = None) -> list:
    """Call right after a successful (non-dry-run) apply, on the SAME
    mapping that drove it: every color that just got replaced no longer
    exists in the files BY DESIGN (that's the entire point of applying) --
    without this, the very next drift refresh (refresh_identity_stamps)
    would see that stamped color gone and flag it "orphaned", confusing the
    expected result of an apply with real, unrelated drift.

    Re-stamps each applied entry's identity to the NEW color now actually in
    the files (old_type -- the representation format, hex vs hex_from_rgb --
    never changes just because the value did, so it's kept/inferred, never
    guessed as "hex"). `applied_entries` is the ACTUALLY-applied (old_id,
    new_id) pairs (mapping_store.resolve_apply_targets' final_entries --
    these may use different new_id values than `entries` itself after a
    tier="compacted" run); `entries` is the mapping's own persisted list to
    update (its stored new_id is left untouched -- only the identity stamp
    changes). detected_colors (the PRE-apply scan) is only consulted to
    infer old_type for an entry that was never stamped before this, its
    first-ever apply."""
    palette_by_id = {p["id"]: p for p in new_palette}
    applied_new_id_by_old_id = {e["old_id"]: e["new_id"] for e in applied_entries}
    detected_by_id = {c["id"]: c for c in (detected_colors or [])}
    out = []
    for e in entries:
        applied_new_id = applied_new_id_by_old_id.get(e["old_id"])
        p = palette_by_id.get(applied_new_id) if applied_new_id is not None else None
        if p is None:
            out.append(dict(e))
            continue
        stamped = dict(e)
        old_type = e.get("old_type")
        if old_type is None:
            old = detected_by_id.get(e["old_id"])
            old_type = old["type"] if old else "hex"
        stamped["old_type"] = old_type
        stamped["old_hex"] = p["hex"].lstrip("#").lower()
        out.append(stamped)
    return out


def new_mapping_path(mappings_dir: str) -> str:
    """A timestamped mapping-<timestamp>.csv path, for callers that want a
    one-off file instead of the canonical mapping.csv (see Config.mapping_csv)."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return os.path.join(expand_path(mappings_dir), f"mapping-{ts}.csv")


class MappingStore:
    """In-memory ordered mapping, with real-time persistence to a CSV file.

    old_palette/new_palette (in memory) are always absolute. project_dir, if
    given, is only used to decide how they're written to disk (see module
    docstring) — reading them back always yields absolute paths again."""

    def __init__(self, path: str, old_palette: str = None, new_palette: str = None,
                 project_dir: str = None, _registry: "MappingRegistry" = None,
                 _registry_key: str = None):
        self.path = expand_path(path)
        self.old_palette = old_palette
        self.new_palette = new_palette
        self.project_dir = project_dir
        self.entries = []  # ordered list of {"old_id": int, "new_id": int|None,
                            #                 "old_type": str (optional), "old_hex": str (optional)}
        # Set only via MappingRegistry.for_palette/for_active -- when present,
        # load()/save() delegate to the registry's JSON section instead of
        # this standalone CSV file (see MappingRegistry). Never set this by
        # hand; construct through the registry instead.
        self._registry = _registry
        self._registry_key = _registry_key

    def load(self) -> "MappingStore":
        if self._registry is not None:
            self._registry._load_into(self)
            return self
        old_p, new_p, entries = read_mapping_csv(self.path, project_dir=self.project_dir)
        self.old_palette = old_p or self.old_palette
        self.new_palette = new_p or self.new_palette
        self.entries = entries
        return self

    def save(self) -> None:
        if self._registry is not None:
            self._registry._save_from(self)
            return
        write_mapping_csv(self.path, self.old_palette, self.new_palette, self.entries,
                           project_dir=self.project_dir)

    def _find(self, old_id: int):
        for e in self.entries:
            if e["old_id"] == old_id:
                return e
        return None

    def add_or_update(self, old_id: int, new_id: int = None, persist: bool = True) -> dict:
        """Add old_id to the mapping (at the end, preserving insertion order)
        or update its target if it's already present. Passing new_id=None
        records it as "selected but not yet assigned"."""
        existing = self._find(old_id)
        if existing is not None:
            existing["new_id"] = new_id
            entry = existing
        else:
            entry = {"old_id": old_id, "new_id": new_id}
            self.entries.append(entry)
        if persist:
            self.save()
        return entry

    def link(self, old_id: int, link_to_old_id: int, persist: bool = True) -> dict:
        """Case 2 helper: "map to the same target as <link_to_old_id>".
        This is a one-time copy of the current target, not a live binding —
        if link_to_old_id's target changes later, re-link explicitly."""
        source = self._find(link_to_old_id)
        target_new_id = source["new_id"] if source else None
        return self.add_or_update(old_id, target_new_id, persist=persist)

    def remove(self, old_id: int, persist: bool = True) -> None:
        self.entries = [e for e in self.entries if e["old_id"] != old_id]
        if persist:
            self.save()

    def drop_and_shift_new_id(self, deleted_new_id: int, persist: bool = True) -> int:
        """React to a palette color being deleted: unassign entries that
        targeted it and shift higher new_ids down (see the module-level
        function). Returns how many entries were left unassigned."""
        before = sum(1 for e in self.entries if e["new_id"] == deleted_new_id)
        self.entries = drop_and_shift_new_id(self.entries, deleted_new_id)
        if persist:
            self.save()
        return before

    def apply_drift_relinks(self, driftable: list, persist: bool = True) -> int:
        """Apply every detect_drift "driftable" finding as one ATOMIC rewrite
        of old_id fields (see the module-level apply_drift_relinks -- this
        must never be done as a sequence of one-at-a-time relinks: the most
        common drift scenario is exactly two colors trading rank, and
        resolving entry A's relink before entry B's would find B "in the way"
        of A's target id, or vice versa, corrupting the swap). Returns how
        many entries were actually rewritten."""
        rewritten = sum(1 for e in self.entries if e["old_id"] in {d["old_id"] for d in driftable})
        self.entries = apply_drift_relinks(self.entries, driftable)
        if persist:
            self.save()
        return rewritten

    def resolved_entries(self) -> list:
        """Entries ready to apply, in insertion order."""
        return [e for e in self.entries if e["new_id"] is not None]

    def unresolved_ids(self) -> list:
        return [e["old_id"] for e in self.entries if e["new_id"] is None]


def migrate_legacy_mapping_csv_if_needed(registry_path: str, legacy_csv_path: str,
                                          project_dir: str = None) -> bool:
    """One-time, non-destructive migration into the new "one mapping per
    palette" registry (see MappingRegistry): if registry_path doesn't exist
    yet AND legacy_csv_path (the old single canonical mapping.csv) has real,
    resolved entries, build a registry with exactly ONE section -- keyed by
    the legacy mapping's own #new_palette= -- copied verbatim (including any
    identity stamps it already carries), marked active. legacy_csv_path is
    NEVER deleted or rewritten -- it's left as harmless, inert leftover data
    for anyone still reading it directly. Idempotent: a no-op the moment
    registry_path exists, so this is always cheap after the first real call.
    Returns True if a migration actually happened."""
    registry_path = expand_path(registry_path)
    if os.path.isfile(registry_path):
        return False
    old_p, new_p, entries = read_mapping_csv(legacy_csv_path, project_dir=project_dir)
    if not entries or not new_p:
        return False
    key = _to_relative(new_p, project_dir)
    data = {
        "v": 1,
        "active": key,
        "mappings": {
            key: {"old_palette": _to_relative(old_p, project_dir) if old_p else None,
                  "entries": [dict(e) for e in entries]},
        },
    }
    os.makedirs(os.path.dirname(registry_path), exist_ok=True)
    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    return True


class MappingRegistry:
    """One JSON file (see Config.mapping_registry_json) holding a separate
    MappingStore "section" PER target palette, plus a top-level `active`
    pointer -- so switching which palette you're working on never clobbers a
    DIFFERENT palette's already-tuned mapping (the old single mapping.csv
    did exactly that, the core bug this registry exists to fix). JSON rather
    than a multi-section CSV: it needs O(1) lookup-by-palette (the "helper to
    find the mapping for palette X" this whole rework was requested for) and
    each section already carries a nested, evolving set of optional per-
    entry fields (old_type/old_hex) -- the same "small JSON file for keyed,
    structured, non-tabular state" pattern this project already uses for
    color_roles.json / config.json's palette_generation block.

    On-disk shape:
        {"v": 1, "active": "<palette_key>",
         "mappings": {"<palette_key>": {"old_palette": "...", "entries": [...]}, ...}}
    Palette keys are stored relative to project_dir when possible (same
    to_relative/to_absolute convention MappingStore's own old_palette/
    new_palette already use), so the whole file stays portable if the
    project folder moves.

    MappingStore instances obtained via for_palette/for_active have their
    normal method surface (add_or_update, link, remove, resolved_entries,
    ...) completely unchanged -- only load()/save() are redirected here
    instead of touching a standalone file (see MappingStore._registry)."""

    def __init__(self, registry_path: str, project_dir: str = None):
        self.path = expand_path(registry_path)
        self.project_dir = project_dir
        legacy_csv_path = os.path.join(os.path.dirname(self.path), "mapping.csv")
        migrate_legacy_mapping_csv_if_needed(self.path, legacy_csv_path, project_dir)
        self._data = self._read()

    def _read(self) -> dict:
        if not os.path.isfile(self.path):
            return {"v": 1, "active": None, "mappings": {}}
        with open(self.path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except ValueError:
                return {"v": 1, "active": None, "mappings": {}}
        if not isinstance(data, dict):
            return {"v": 1, "active": None, "mappings": {}}
        data.setdefault("v", 1)
        data.setdefault("active", None)
        data.setdefault("mappings", {})
        return data

    def _write(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)
            f.write("\n")

    def _key_for(self, palette_path: str) -> str:
        return _to_relative(palette_path, self.project_dir)

    def for_palette(self, new_palette_path: str, old_palette: str = None,
                     set_active: bool = True) -> "MappingStore":
        """Load-or-create the section for new_palette_path; returns a
        MappingStore bound to THIS registry+key. Re-reads from disk first
        (like MappingStore.load()/save() do in registry mode) so a longer-
        lived MappingRegistry object (e.g. the GUI's) never acts on data
        made stale by a separate process/CLI invocation writing to the same
        file in the meantime. set_active=True (default) also persists
        `active` = this palette -- callers that only want to LOOK, not
        switch focus (e.g. a background drift scan, or generating/shifting a
        palette that ISN'T the one currently selected), pass set_active=False.

        A genuinely brand-new section (this palette never had one) is
        SEEDED with a copy of the currently-active section's entries, rather
        than starting empty -- old_id->new_id is a reusable "recipe" (which
        detected color plays which role), not something inherently tied to
        one literal palette file: that's the whole premise `automatic`/daily
        wallpaper-switch workflows already rely on (one mapping definition,
        reapplied against a freshly generated palette each time). This is
        only ever a PROPOSAL the user can freely clear/edit afterward, never
        a binding -- it just restores the continuity the single global
        mapping.csv always had, which an empty-by-default new section would
        otherwise silently lose the moment anything (CLI or GUI) touches a
        palette for the first time."""
        self._data = self._read()
        key = self._key_for(new_palette_path)
        section = self._data["mappings"].get(key)
        if section is None:
            active_key = self._data.get("active")
            seed_entries = []
            if active_key and active_key != key:
                seed_entries = [dict(e) for e in self._data["mappings"].get(active_key, {}).get("entries", [])]
            section = {"old_palette": _to_relative(old_palette, self.project_dir) if old_palette else None,
                       "entries": seed_entries}
            self._data["mappings"][key] = section
        elif old_palette and not section.get("old_palette"):
            section["old_palette"] = _to_relative(old_palette, self.project_dir)

        store = MappingStore(
            self.path,
            old_palette=_to_absolute(section.get("old_palette"), self.project_dir),
            new_palette=_to_absolute(key, self.project_dir),
            project_dir=self.project_dir, _registry=self, _registry_key=key,
        )
        store.entries = [dict(e) for e in section["entries"]]

        if set_active:
            self._data["active"] = key
            self._write()
        return store

    def for_active(self) -> "MappingStore":
        """The MappingStore for whatever `active` currently points at -- the
        direct replacement for Config.mapping_csv as what bare apply/test/
        automatic/palette-shift fall back to with no explicit path. A fresh
        registry with no sections yet returns an empty, unattached store
        (nothing to persist into until for_palette names a real palette)."""
        self._data = self._read()
        key = self._data.get("active")
        if key is None:
            return MappingStore(self.path, project_dir=self.project_dir, _registry=self, _registry_key=None)
        return self.for_palette(_to_absolute(key, self.project_dir), set_active=False)

    def active_palette_path(self) -> str:
        self._data = self._read()
        key = self._data.get("active")
        return _to_absolute(key, self.project_dir) if key else None

    def all_sections(self) -> list:
        """[(palette_path, MappingStore), ...] -- every section currently in
        the registry (e.g. for `mapping list`). Deliberately NOT used for
        identity-stamp/drift refreshing: only one wallpaper's colors can
        physically be in the files at a time, so checking every INACTIVE
        palette's mapping against the current scan would always find them
        "gone" -- not real drift, just the routine state of anything that
        isn't the active mapping (see refresh_identity_stamps' callers,
        which are deliberately scoped to the active section only)."""
        return [
            (_to_absolute(key, self.project_dir), self.for_palette(_to_absolute(key, self.project_dir), set_active=False))
            for key in list(self._data["mappings"])
        ]

    def _load_into(self, store: "MappingStore") -> None:
        """Registry-mode MappingStore.load(): re-read this store's section
        fresh from disk (picking up anything written by another process/
        session since this registry object was created) rather than trusting
        possibly-stale in-memory state."""
        self._data = self._read()
        section = self._data["mappings"].get(store._registry_key, {"old_palette": None, "entries": []})
        if section.get("old_palette"):
            store.old_palette = _to_absolute(section["old_palette"], self.project_dir)
        store.entries = [dict(e) for e in section["entries"]]

    def _save_from(self, store: "MappingStore") -> None:
        """Registry-mode MappingStore.save(): persist store.entries back
        into this store's section. Re-reads from disk first so a DIFFERENT
        section modified elsewhere since this registry object was created
        (e.g. by another CLI invocation while the GUI is open) isn't
        clobbered by this write."""
        if store._registry_key is None:
            return  # nothing to attach to -- e.g. for_active() on an empty registry
        self._data = self._read()
        section = self._data["mappings"].setdefault(store._registry_key, {"old_palette": None, "entries": []})
        if store.old_palette:
            section["old_palette"] = _to_relative(store.old_palette, self.project_dir)
        section["entries"] = [dict(e) for e in store.entries]
        self._write()


def resolve_mapping_entries_for_palette(config, palette_path: str, mapping_path: str = None) -> list:
    """Read-only: the mapping entries relevant to `palette_path` -- used by
    palette_shift.generate_and_save_palette/_regenerate to decide which
    detected fg/bg pairs are "in use" (see color_detector.compute_role_pairs).
    An explicit mapping_path (a standalone CSV, the same escape hatch
    MappingStore's direct construction always supported) wins; otherwise
    resolves THAT PALETTE'S OWN section in the registry -- never the
    globally-active one, so generating/shifting a palette that ISN'T
    currently selected never reads (or, via apply_mapping_updates_for_palette,
    perturbs) a different palette's mapping."""
    if mapping_path:
        _old_p, _new_p, entries = read_mapping_csv(mapping_path, project_dir=config.project_dir)
        return entries
    registry = MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
    return registry.for_palette(palette_path, set_active=False).entries


def apply_mapping_updates_for_palette(config, palette_path: str, updates: list, mapping_path: str = None) -> None:
    """Persist a freshly-generated fg/bg pair's mapping rewire (see
    palette_shift._pending_mapping_updates) into the mapping relevant to
    `palette_path`. Same resolution as resolve_mapping_entries_for_palette:
    explicit mapping_path wins; otherwise that palette's OWN registry
    section, with set_active=False -- generating/shifting a palette must
    never silently make it "the active one" as a side effect."""
    if not updates:
        return
    if mapping_path:
        store = MappingStore(mapping_path, project_dir=config.project_dir).load()
    else:
        registry = MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
        store = registry.for_palette(palette_path, set_active=False)
    for old_id, new_id in updates:
        store.add_or_update(old_id, new_id, persist=False)
    store.save()
