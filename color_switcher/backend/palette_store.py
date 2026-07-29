#!/usr/bin/env python3
"""
palette_store.py — Read/write/create palette CSVs.

Row format: "id,#hexvalue,label[,origin[,role[,paired_id]]]" (origin optional:
"gen"|"custom"; role optional: "foreground"|"background"; paired_id optional:
the 1-based id of the OTHER palette color this one is fg/bg-paired with --
each column only written once any entry has one, same convention throughout;
empty string means unmarked. paired_id is computer-generated only (see
palette_generator.fgbg_pairing) -- palette_shift.derive_effective resolves it
fresh from each base entry's `pair_id` every write, so it can never go stale
across an edit/delete elsewhere).
Optionally preceded by ONE metadata header line:

    #ucs-meta={...json...}

carrying the palette's provenance so `shift` can re-tweak & re-apply it without
re-typing the whole `automatic --from-image` command (see read_palette). The
header is backward-compatible: older readers (and this one's row loop) skip any
line whose first comma-field isn't a numeric id, so a palette with no header
still loads fine and is treated as a plain hand-created palette.
"""

import hashlib
import json
import os
import re

from .color_detector import expand_path
from .config import to_home_relative

_HEX_RE = re.compile(r"^[0-9a-f]{6}$")

META_PREFIX = "#ucs-meta="
META_VERSION = 1
_VALID_ORIGINS = ("gen", "custom")
_VALID_ROLES = ("foreground", "background")


def default_meta() -> dict:
    """The metadata a palette with no header is treated as: a hand-created
    palette with no image, no generation params, no active post-modifiers."""
    return {
        "v": META_VERSION,
        "generated": False,
        "image": None,
        # Cosmetic-only override for the GUI's wallpaper panel -- independent
        # of `image`/`generated`, which stay reserved for a truly generated
        # palette's actual source of truth (shift/regeneration read those,
        # never this). Lets a hand-created (or generated-but-image-missing)
        # palette still show SOME preview picture.
        "preview_image": None,
        "gen": None,   # generation params (colors, mode, scoring, ...) — only for generated palettes
        "post": {"my_eyes": False, "ying_yang": False},
        # Regeneration policy, not a post-modifier: whether a selection-modifier
        # shift preserves hand-added/edited (origin=custom) colors in their
        # original slot instead of discarding them. Defaults True so existing
        # palettes (no key yet) get the safer behavior automatically.
        "keep_custom_on_regen": True,
        # Regeneration policy: whether a monochrome source image gets a
        # synthesized saturated accent (+ a shading ramp off it) instead of a
        # genuinely greyscale palette -- see
        # palette_generator.generate_palette's hallucinate param. Defaults
        # True (matches the pre-existing, only-ever behavior).
        "hallucinate_on_monochrome": True,
        # Regeneration policy: whether case1/2 fg/bg pairs are forced to the
        # same hue (contrast purely by luminance) -- see
        # palette_generator.generate_palette's eco param. Defaults False (a
        # stylistic modifier, same category as my_eyes/ying_yang).
        "eco_contrast": False,
    }


def _parse_meta(raw: str) -> dict:
    """Parse the JSON after `#ucs-meta=`, merged over default_meta so missing
    keys fall back cleanly. Returns default_meta() on any malformed input."""
    meta = default_meta()
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return meta
    if not isinstance(parsed, dict):
        return meta
    meta.update(parsed)
    post = default_meta()["post"]
    if isinstance(parsed.get("post"), dict):
        post.update(parsed["post"])
    meta["post"] = post
    return meta


def _serialize_meta(meta: dict) -> str:
    """The full `#ucs-meta=...` header line (no trailing newline)."""
    return META_PREFIX + json.dumps(meta, separators=(",", ":"))


def _read(path: str):
    """Internal: parse a palette file into (entries, meta_or_None) where
    meta_or_None is None when the file carried NO #ucs-meta header (so callers
    can tell "header-less" apart from "header that happens to equal defaults").
    Returns ([], None) if the file doesn't exist."""
    path = expand_path(path)
    entries = []
    meta = None
    if not os.path.isfile(path):
        return entries, meta

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith(META_PREFIX):
                meta = _parse_meta(line[len(META_PREFIX):])
                continue
            parts = line.split(",")
            if len(parts) < 2:
                continue
            id_str, hex_str = parts[0].strip(), parts[1].strip()
            if not id_str.isdigit():
                continue
            hex_clean = hex_str.lstrip("#").lower()
            if not _HEX_RE.match(hex_clean):
                continue
            label = parts[2].strip() if len(parts) > 2 else ""
            origin = parts[3].strip().lower() if len(parts) > 3 else None
            role = parts[4].strip().lower() if len(parts) > 4 else None
            paired_id = parts[5].strip() if len(parts) > 5 else None
            entry = {"id": int(id_str), "hex": hex_clean, "label": label}
            if origin in _VALID_ORIGINS:  # keep the dict at {id,hex,label} unless origin is meaningful
                entry["origin"] = origin
            if role in _VALID_ROLES:
                entry["role"] = role
            if paired_id and paired_id.isdigit():
                entry["paired_id"] = int(paired_id)
            entries.append(entry)

    return entries, meta


def read_palette(path: str):
    """Load a palette CSV. Returns (entries, meta):
      entries: [{"id": int, "hex": "rrggbb", "label": str, "origin": str|None,
                "role": str|None, "paired_id": int|None}]
      meta:    the parsed #ucs-meta dict, or default_meta() if there's no header.
    Returns ([], default_meta()) if the file doesn't exist."""
    entries, meta = _read(path)
    return entries, meta if meta is not None else default_meta()


def read_palette_csv(path: str) -> list:
    """Load just the color entries of a palette CSV (metadata ignored). Kept
    for the many callers that only want the colors; use read_palette when you
    also need the provenance."""
    return _read(path)[0]


def read_palette_meta(path: str) -> dict:
    """Load just a palette's #ucs-meta (or default_meta() if it has none)."""
    return read_palette(path)[1]


def set_preview_image(path: str, image_path) -> None:
    """Set (image_path given) or clear (None) a palette's cosmetic
    `preview_image` -- see default_meta's docstring. Preserves every other
    entry/meta field untouched."""
    entries, meta = read_palette(path)
    meta["preview_image"] = to_home_relative(image_path) if image_path else None
    write_palette_csv(path, entries, meta=meta)


def write_palette_csv(path: str, entries: list, meta: dict = None) -> None:
    """Write a palette CSV. When `meta` is given, a `#ucs-meta=` header line is
    written first; without it, the file is header-less (unchanged legacy
    behavior). A per-color `origin` field is written whenever any entry carries
    one, so it stays paired with the right color."""
    path = expand_path(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_origin = any(e.get("origin") in _VALID_ORIGINS for e in entries)
    write_role = any(e.get("role") in _VALID_ROLES for e in entries)
    write_paired = any(e.get("paired_id") for e in entries)
    with open(path, "w", encoding="utf-8") as f:
        if meta is not None:
            f.write(_serialize_meta(meta) + "\n")
        for e in entries:
            hex_clean = e["hex"].lstrip("#").lower()
            label = e.get("label", "")
            if write_paired:
                # origin/role columns must stay in position even if no entry
                # actually has one, so paired_id (position 6) doesn't shift
                # into their slot.
                origin_field = e.get("origin") or ("custom" if write_origin else "")
                role_field = e.get("role") or ""
                paired_field = e.get("paired_id") or ""
                f.write(f"{e['id']},#{hex_clean},{label},{origin_field},{role_field},{paired_field}\n")
            elif write_role:
                # origin column must stay in position even if no entry actually
                # has one, so role (position 5) doesn't shift into its slot.
                origin_field = e.get("origin") or ("custom" if write_origin else "")
                f.write(f"{e['id']},#{hex_clean},{label},{origin_field},{e.get('role') or ''}\n")
            elif write_origin:
                f.write(f"{e['id']},#{hex_clean},{label},{e.get('origin') or 'custom'}\n")
            else:
                f.write(f"{e['id']},#{hex_clean},{label}\n")


def add_color(path: str, hex_value: str, label: str = "", origin: str = None, role: str = None) -> dict:
    """Append a new color to a palette CSV, assigning the next free id, and
    PRESERVING the file's #ucs-meta header. On a generated palette a manually
    added color is marked origin="custom" so a later regenerating shift can
    warn it will be discarded (see [[palette-shift-design]])."""
    entries, meta = _read(path)
    if origin is None and meta is not None and meta.get("generated"):
        origin = "custom"
    next_id = max((e["id"] for e in entries), default=0) + 1
    entry = {"id": next_id, "hex": hex_value.lstrip("#").lower(), "label": label}
    if origin in _VALID_ORIGINS:
        entry["origin"] = origin
    if role in _VALID_ROLES:
        entry["role"] = role
    entries.append(entry)
    write_palette_csv(path, entries, meta=meta)  # meta is None for header-less files -> stays header-less
    return entry


def has_custom_edits(entries: list) -> bool:
    """True if any color was hand-added/edited on top of a generated palette
    (origin="custom") — the signal for the warn-before-regenerate gate."""
    return any(e.get("origin") == "custom" for e in entries)


def find_palettes_for_image(directory: str, image_path: str) -> list:
    """Every *.csv under `directory` whose #ucs-meta says it was GENERATED
    from this exact image -- matched by PROVENANCE (meta["image"], compared
    as an absolute path), never by filename. A palette can be freely renamed
    and still be found this way; conversely a filename that merely LOOKS
    like it matches an image is never mistaken for a real match. Returns an
    empty list if the directory doesn't exist or nothing matches."""
    directory = expand_path(directory)
    if not os.path.isdir(directory):
        return []
    target = os.path.abspath(expand_path(image_path))
    matches = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".csv"):
            continue
        path = os.path.join(directory, name)
        meta = read_palette_meta(path)
        if not meta.get("generated") or not meta.get("image"):
            continue
        if os.path.abspath(expand_path(meta["image"])) == target:
            matches.append(path)
    return matches


def _slugify_image_basename(image_path: str) -> str:
    """A short, filesystem-friendly, purely COSMETIC slug of an image's
    basename -- for human-readable filenames only. Never used to decide
    whether a palette already exists for an image (find_palettes_for_image,
    by provenance, is the actual source of truth for that)."""
    base = os.path.splitext(os.path.basename(image_path))[0]
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", base).strip("-").lower()
    return slug or "wallpaper"


def default_generated_path_for_image(palettes_created_dir: str, image_path: str) -> str:
    """Deterministic default out_path for a FRESH generation against this
    image: generated-<slug-of-image-basename>-<sha1(abspath(image))[:8]>.csv
    -- the same image always proposes the same filename (so a later
    --regenerate with no explicit --out naturally lands back on this same
    file), while remaining legible at a glance in a file browser. This
    REPLACES the old "always overwrite one canonical generated.csv" default
    -- each wallpaper now gets its own persistent slot."""
    abs_image = os.path.abspath(expand_path(image_path))
    digest = hashlib.sha1(abs_image.encode("utf-8")).hexdigest()[:8]
    filename = f"generated-{_slugify_image_basename(image_path)}-{digest}.csv"
    return os.path.join(expand_path(palettes_created_dir), filename)


def list_palettes(directory: str) -> list:
    """List available palette CSVs under a directory (e.g. palettes/created)."""
    directory = expand_path(directory)
    if not os.path.isdir(directory):
        return []
    return sorted(
        os.path.join(directory, f)
        for f in os.listdir(directory)
        if f.endswith(".csv")
    )


# Aliases matching the vocabulary used in the original spec / roadmap.
import_palette = read_palette_csv
export_palette = write_palette_csv
