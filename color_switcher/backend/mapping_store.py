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

If project_dir is given to read/write, old_palette/new_palette are stored
relative to it whenever they live inside it (so the mapping stays valid if
the whole project folder moves or is copied elsewhere) and resolved back to
absolute on read. Paths outside project_dir (e.g. an externally-picked
file) are left absolute, since there's no meaningful relative form for
those.
"""

import os
from datetime import datetime

from .color_detector import expand_path

HEADER_LINE = "old_id,new_id"


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
    ordered list of {"old_id": int, "new_id": int}, in file order."""
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
            elif line.strip() == HEADER_LINE:
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
        entries.append({"old_id": int(old_id_str), "new_id": int(new_id_str)})

    return _to_absolute(old_palette, project_dir), _to_absolute(new_palette, project_dir), entries


def write_mapping_csv(path: str, old_palette: str, new_palette: str, entries: list,
                       project_dir: str = None) -> None:
    """Write header metadata + resolved rows, in the given entries order."""
    path = expand_path(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"#old_palette={_to_relative(old_palette, project_dir)}\n")
        f.write(f"#new_palette={_to_relative(new_palette, project_dir)}\n")
        f.write(f"{HEADER_LINE}\n")
        for e in entries:
            if e.get("new_id") is None:
                continue
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
                 project_dir: str = None):
        self.path = expand_path(path)
        self.old_palette = old_palette
        self.new_palette = new_palette
        self.project_dir = project_dir
        self.entries = []  # ordered list of {"old_id": int, "new_id": int|None}

    def load(self) -> "MappingStore":
        old_p, new_p, entries = read_mapping_csv(self.path, project_dir=self.project_dir)
        self.old_palette = old_p or self.old_palette
        self.new_palette = new_p or self.new_palette
        self.entries = entries
        return self

    def save(self) -> None:
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

    def resolved_entries(self) -> list:
        """Entries ready to apply, in insertion order."""
        return [e for e in self.entries if e["new_id"] is not None]

    def unresolved_ids(self) -> list:
        return [e["old_id"] for e in self.entries if e["new_id"] is None]
