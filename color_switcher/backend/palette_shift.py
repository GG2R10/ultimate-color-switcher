#!/usr/bin/env python3
"""
palette_shift.py — Re-tweak an existing palette (its modifiers, or individual
colors) and hand back the new colors, WITHOUT the caller re-passing the image
or the whole flag set that first produced it. Powers `ucs automatic shift` and
`ucs palette edit|remove`, plus the GUI's Modificadores / edit / delete.

Model (see [[palette-shift-design]]): a palette's #ucs-meta stores an ordered
`base` list — every color, each tagged origin gen|custom — plus the active
`post` modifiers. The CSV rows are the EFFECTIVE colors, always
`apply_post_modifiers(base, post)` (so the applied file is unchanged in shape).

Two kinds of modifier, handled very differently:

  * Post modifiers (my-eyes, ying-yang) are image-independent and apply to
    EVERY base color, gen and custom alike. Recomputed from base (never
    mutated), so they're instant and reversible, and hand-added colors survive
    (transformed, not dropped).

  * Selection modifiers (mode, scoring, weighted-contrast, shuffle, overfetch,
    colors) need the image's cluster pool, so they REGENERATE the gen colors
    from the stored image + params. A regeneration discards hand-added/edited
    colors (v1: warn, don't preserve). Only valid on a generated palette.

Per-color ops (add/edit/delete) keep the base coherent: an added or edited
color becomes origin custom (a literal, user-chosen color — still subject to
post-mods, immune only to regeneration); delete renumbers, and the caller
adjusts the mapping (see mapping_store.drop_and_shift_new_id).
"""

import os

from . import palette_generator
from . import palette_store
from .color_detector import expand_path
from .config import to_home_relative


def generate_and_save_palette(config, image, n_colors, sample_size, mode, saturate,
                                out_path=None, scoring="default", custom_scoring_values=None,
                                weighted_contrast=True, shuffle=None, overfetch=0, ying_yang=False,
                                my_eyes_factor=palette_generator._MY_EYES_CHROMA_FACTOR,
                                my_eyes_max_chroma=palette_generator._MY_EYES_CHROMA_MAX,
                                shading_direction="dark", shading_min_luminance=8.0,
                                shading_max_luminance=92.0):
    """Shared by the CLI (`palette generate` / `automatic --from-image`) and
    the GUI ("Generar paleta desde imagen…") -- the one place that turns an
    image + generation params into a saved palette CSV with full #ucs-meta.
    Returns (entries, saved_path) — entries is the id/hex/label list ready to
    hand to guiless.apply_palette directly (no need to re-read the file back).

    shuffle=None means "no shuffle override was requested" -- distinct from
    an explicit 0, since resolving it (and persisting it as the new
    "last_shuffle" anchor for a future --shuffle next) only happens when
    shuffling was actually requested.

    my_eyes_factor/my_eyes_max_chroma: only used when saturate=True -- see
    palette_generator._boost_saturation.

    shading_direction/shading_min_luminance/shading_max_luminance: only used
    when mode="shading" -- see palette_generator.generate_shading_series."""
    weights = palette_generator.resolve_scoring_weights(
        scoring, custom_percentages=custom_scoring_values, config=config,
    )
    resolved_shuffle = (
        palette_generator.resolve_shuffle_index(shuffle, n_colors, overfetch=overfetch, config=config)
        if shuffle is not None else 0
    )
    colors, base_colors = palette_generator.generate_palette(
        image, n_colors=n_colors, sample_size=sample_size, mode=mode,
        saturate=saturate, weights=weights, weighted_contrast=weighted_contrast,
        shuffle=resolved_shuffle, overfetch=overfetch, ying_yang=ying_yang,
        saturate_factor=my_eyes_factor, saturate_max_chroma=my_eyes_max_chroma,
        shading_direction=shading_direction, shading_min_l=shading_min_luminance,
        shading_max_l=shading_max_luminance, with_base=True,
    )

    if not out_path:
        out_path = config.generated_palette_csv
    if not out_path.endswith(".csv"):
        out_path += ".csv"
    if not os.path.isabs(out_path):
        out_path = os.path.join(config.palettes_created_dir, out_path)

    # Rows are the EFFECTIVE (applied) colors; the pre-post-mod base + the
    # generation params ride along in #ucs-meta so `shift` can re-tweak & re-apply
    # this palette without re-passing the image or the whole flag set.
    entries = [{"id": i + 1, "hex": c["hex"], "label": c["label"], "origin": "gen"}
               for i, c in enumerate(colors)]
    meta = palette_store.default_meta()
    meta.update({
        "generated": True,
        "image": to_home_relative(image),
        "gen": {
            "colors": n_colors,
            "sample_size": sample_size,
            "mode": mode,
            "scoring": scoring,
            "custom_percentages": custom_scoring_values,
            "weighted_contrast": weighted_contrast,
            "shuffle": resolved_shuffle,
            "overfetch": overfetch,
            "shading_direction": shading_direction,
            "shading_min_luminance": shading_min_luminance,
            "shading_max_luminance": shading_max_luminance,
        },
        "post": {"my_eyes": bool(saturate), "ying_yang": bool(ying_yang),
                 "my_eyes_factor": my_eyes_factor, "my_eyes_max_chroma": my_eyes_max_chroma},
        "base": [{"hex": c["hex"], "label": c["label"], "origin": "gen"} for c in base_colors],
    })
    palette_store.write_palette_csv(out_path, entries, meta=meta)
    return entries, out_path


class ShiftError(Exception):
    """A clean, user-facing reason a shift can't proceed (created palette asked
    for a selection modifier, missing provenance, ...). main() prints it and
    exits 1, same as ImageLoadError -- never a traceback."""


class PaletteEditError(Exception):
    """User-facing reason a per-color edit can't proceed (duplicate color,
    target not found, empty palette). Printed cleanly, no traceback."""


def _resolve_bool(current, override) -> bool:
    """Apply an on|off|toggle|None override to a stored boolean. None keeps it."""
    if override is None:
        return bool(current)
    if override == "on":
        return True
    if override == "off":
        return False
    if override == "toggle":
        return not bool(current)
    raise ShiftError(f"valor inválido para un modificador booleano: {override!r} (esperado on|off|toggle)")


def _resolve_shading_direction(current, override) -> str:
    """Apply a dark|light|toggle|None override to the stored shading
    direction (defaults to "dark" if nothing was stored yet -- e.g. a
    palette generated before this option existed). None keeps it."""
    current = current or "dark"
    if override is None:
        return current
    if override in ("dark", "light"):
        return override
    if override == "toggle":
        return "light" if current == "dark" else "dark"
    raise ShiftError(f"valor inválido para la dirección de shading: {override!r} (esperado dark|light|toggle)")


# --------------------------------------------------------------------------- #
# base <-> effective
# --------------------------------------------------------------------------- #

def _norm_hex(h):
    return h.lstrip("#").lower()


def reconstruct_base(entries, meta):
    """The full ordered base (one entry per color, with origin), robust to the
    formats a palette file can be in:
      - new: meta['base'] covers every row (len matches) -> used directly.
      - older: meta['base'] held only gen colors, customs were separate literal
        rows -> gen bases come from meta['base'], custom bases from the row hex.
      - legacy: no meta['base'] at all -> every row is its own base.
    Always aligned 1:1 with `entries` by position."""
    stored = list(meta.get("base") or [])
    if stored and len(stored) == len(entries):
        return [
            {"hex": _norm_hex(b["hex"]), "label": b.get("label", ""),
             "origin": b.get("origin") or (entries[i].get("origin") or "gen")}
            for i, b in enumerate(stored)
        ]
    base = []
    gi = 0
    for e in entries:
        if e.get("origin") == "custom":
            base.append({"hex": _norm_hex(e["hex"]), "label": e.get("label", ""), "origin": "custom"})
        elif gi < len(stored):
            base.append({"hex": _norm_hex(stored[gi]["hex"]),
                         "label": stored[gi].get("label", e.get("label", "")), "origin": "gen"})
            gi += 1
        else:
            base.append({"hex": _norm_hex(e["hex"]), "label": e.get("label", ""), "origin": "gen"})
    return base


def derive_effective(base, post):
    """The effective (to-apply) rows for a base under the active post-mods:
    apply_post_modifiers to EVERY color (gen and custom), order preserved, ids
    renumbered 1..N, origin carried through."""
    modded = palette_generator.apply_post_modifiers(
        [{"hex": b["hex"], "label": b.get("label", "")} for b in base],
        my_eyes=bool(post.get("my_eyes")), ying_yang=bool(post.get("ying_yang")),
        my_eyes_factor=post.get("my_eyes_factor", palette_generator._MY_EYES_CHROMA_FACTOR),
        my_eyes_max_chroma=post.get("my_eyes_max_chroma", palette_generator._MY_EYES_CHROMA_MAX),
    )
    rows = []
    for i, (b, m) in enumerate(zip(base, modded)):
        row = {"id": i + 1, "hex": m["hex"], "label": b.get("label", "")}
        if b.get("origin") in ("gen", "custom"):
            row["origin"] = b["origin"]
        rows.append(row)
    return rows


def _post_of(meta):
    post = dict({"my_eyes": False, "ying_yang": False,
                 "my_eyes_factor": palette_generator._MY_EYES_CHROMA_FACTOR,
                 "my_eyes_max_chroma": palette_generator._MY_EYES_CHROMA_MAX})
    post.update(meta.get("post") or {})
    return post


def _write_derived(path, base, meta):
    """Persist a mutated base: rows = derive_effective(base, post), meta.base
    updated to the full base. Returns the new rows."""
    post = _post_of(meta)
    rows = derive_effective(base, post)
    new_meta = dict(meta)
    new_meta["base"] = base
    new_meta["post"] = post
    palette_store.write_palette_csv(path, rows, meta=new_meta)
    return rows


# --------------------------------------------------------------------------- #
# per-color ops (add / edit / delete)
# --------------------------------------------------------------------------- #

def _load(path):
    entries, meta = palette_store.read_palette(path)
    return entries, meta, reconstruct_base(entries, meta)


def _has_dup(base, new_hex, exclude_index=None):
    h = _norm_hex(new_hex)
    return any(i != exclude_index and b["hex"] == h for i, b in enumerate(base))


def _find_index(base, entries, target):
    """Locate a color by id (a digit) or by hex (effective row hex or base
    hex). Returns its position, or None."""
    t = _norm_hex(str(target).strip())
    if t.isdigit():
        tid = int(t)
        for i, e in enumerate(entries):
            if e["id"] == tid:
                return i
        return None
    for i, e in enumerate(entries):
        if _norm_hex(e["hex"]) == t:
            return i
    for i, b in enumerate(base):
        if b["hex"] == t:
            return i
    return None


def add_color(path, hex_value, label=""):
    """Append a user-chosen color (origin custom) to a palette, rejecting a
    duplicate. Returns the new effective row entry."""
    entries, meta, base = _load(path)
    if _has_dup(base, hex_value):
        raise PaletteEditError(f"El color #{_norm_hex(hex_value)} ya existe en la paleta.")
    base.append({"hex": _norm_hex(hex_value), "label": label, "origin": "custom"})
    rows = _write_derived(path, base, meta)
    return rows[-1]


def edit_color(path, target, new_hex):
    """Change a color (by id or hex) to new_hex, marking it custom. Rejects a
    duplicate. Returns the 1-based id of the edited slot (unchanged: an edit
    keeps its position, so any mapping to it stays valid)."""
    entries, meta, base = _load(path)
    if not base:
        raise PaletteEditError(f"Paleta vacía o no encontrada: {path}")
    idx = _find_index(base, entries, target)
    if idx is None:
        raise PaletteEditError(f"No se encontró el color {target!r} en la paleta.")
    if _has_dup(base, new_hex, exclude_index=idx):
        raise PaletteEditError(f"El color #{_norm_hex(new_hex)} ya existe en la paleta.")
    base[idx] = {"hex": _norm_hex(new_hex), "label": base[idx].get("label", ""), "origin": "custom"}
    _write_derived(path, base, meta)
    return idx + 1


def delete_color(path, target):
    """Remove a color (by id or hex). Returns the 1-based id it had, so the
    caller can adjust a mapping (unassign that new_id, shift higher ones down --
    see mapping_store.drop_and_shift_new_id). Renumbers the palette to stay
    contiguous, which guiless's positional matching relies on."""
    entries, meta, base = _load(path)
    if not base:
        raise PaletteEditError(f"Paleta vacía o no encontrada: {path}")
    idx = _find_index(base, entries, target)
    if idx is None:
        raise PaletteEditError(f"No se encontró el color {target!r} en la paleta.")
    del base[idx]
    _write_derived(path, base, meta)
    return idx + 1


# --------------------------------------------------------------------------- #
# shift (modifiers)
# --------------------------------------------------------------------------- #

_SELECTION_KEYS = ("mode", "scoring", "custom_scoring_values", "weighted_contrast",
                   "shuffle", "overfetch", "colors", "shading_direction",
                   "shading_min_luminance", "shading_max_luminance")


def shift_palette(palette_path, config, *, my_eyes=None, ying_yang=None,
                  my_eyes_factor=None, my_eyes_max_chroma=None,
                  mode=None, scoring=None, custom_scoring_values=None,
                  weighted_contrast=None, shuffle=None, overfetch=None, colors=None,
                  shading_direction=None, shading_min_luminance=None, shading_max_luminance=None,
                  write=True) -> dict:
    """Compute (and, unless write=False, persist) the shifted palette.

    Boolean modifiers (my_eyes, ying_yang) take on|off|toggle|None.
    shading_direction takes dark|light|toggle|None (like a boolean modifier,
    but a 2-way choice instead -- see _resolve_shading_direction).
    my_eyes_factor/my_eyes_max_chroma are POST modifiers too (no
    regeneration needed, just a different multiplier/cap applied to the same
    stored base) -- a concrete value or None ("keep stored"). Everything else
    (selection modifiers, including the luminance bounds) also takes a
    concrete value or None ("keep stored"), but DOES trigger a regenerate.

    Returns {"entries", "meta", "regenerated": bool, "warnings": [str]}.
    Raises ShiftError for the clean user-facing failures."""
    entries, meta = palette_store.read_palette(palette_path)
    if not entries:
        raise ShiftError(f"Paleta vacía o no encontrada: {palette_path}")

    post = _post_of(meta)
    new_post = {
        "my_eyes": _resolve_bool(post.get("my_eyes"), my_eyes),
        "ying_yang": _resolve_bool(post.get("ying_yang"), ying_yang),
        "my_eyes_factor": my_eyes_factor if my_eyes_factor is not None else post.get("my_eyes_factor"),
        "my_eyes_max_chroma": (my_eyes_max_chroma if my_eyes_max_chroma is not None
                               else post.get("my_eyes_max_chroma")),
    }
    selection = {"mode": mode, "scoring": scoring, "custom_scoring_values": custom_scoring_values,
                 "weighted_contrast": weighted_contrast, "shuffle": shuffle,
                 "overfetch": overfetch, "colors": colors,
                 "shading_direction": shading_direction,
                 "shading_min_luminance": shading_min_luminance,
                 "shading_max_luminance": shading_max_luminance}
    wants_regen = any(selection[k] is not None for k in _SELECTION_KEYS)
    warnings = []

    if wants_regen:
        new_entries, new_meta = _regenerate(meta, entries, new_post, selection, config, warnings)
    else:
        base = reconstruct_base(entries, meta)
        new_entries = derive_effective(base, new_post)
        new_meta = dict(meta)
        new_meta["post"] = new_post
        new_meta["base"] = base

    if write:
        palette_store.write_palette_csv(palette_path, new_entries, meta=new_meta)
    return {"entries": new_entries, "meta": new_meta, "regenerated": wants_regen, "warnings": warnings}


def _regenerate(meta, entries, new_post, selection, config, warnings):
    if not meta.get("generated"):
        raise ShiftError(
            "Esta paleta es creada (no tiene imagen): no admite modificadores de selección "
            "(--mode/--scoring/--shuffle/--overfetch/--colors). Solo --my-eyes/--ying-yang."
        )
    image = meta.get("image")
    if not image:
        raise ShiftError(
            "Esta paleta no tiene información de generación guardada. Regenerá una vez con "
            "'automatic --from-image <img>' para grabarla, y después usá shift."
        )
    gen = meta.get("gen") or {}
    n_colors = selection["colors"] if selection["colors"] is not None else gen.get("colors", 6)
    resolved = {
        "sample_size": gen.get("sample_size", 40000),
        "mode": selection["mode"] if selection["mode"] is not None else gen.get("mode", "contrast"),
        "scoring": selection["scoring"] if selection["scoring"] is not None else gen.get("scoring", "default"),
        "custom_percentages": (selection["custom_scoring_values"] if selection["custom_scoring_values"] is not None
                               else gen.get("custom_percentages")),
        "weighted_contrast": (selection["weighted_contrast"] if selection["weighted_contrast"] is not None
                              else gen.get("weighted_contrast", True)),
        "overfetch": selection["overfetch"] if selection["overfetch"] is not None else gen.get("overfetch", 0),
        "shading_min_luminance": (selection["shading_min_luminance"]
                                  if selection["shading_min_luminance"] is not None
                                  else gen.get("shading_min_luminance", 8.0)),
        "shading_max_luminance": (selection["shading_max_luminance"]
                                  if selection["shading_max_luminance"] is not None
                                  else gen.get("shading_max_luminance", 92.0)),
    }
    resolved["shading_direction"] = _resolve_shading_direction(
        gen.get("shading_direction"), selection["shading_direction"],
    )
    weights = palette_generator.resolve_scoring_weights(
        resolved["scoring"], custom_percentages=resolved["custom_percentages"], config=config,
    )
    # Only resolve/persist a new shuffle anchor when the user actually passed
    # --shuffle; otherwise reuse the stored resolved index verbatim (no "next"
    # bookkeeping side effects on an unrelated shift).
    if selection["shuffle"] is not None:
        resolved_shuffle = palette_generator.resolve_shuffle_index(
            selection["shuffle"], n_colors, overfetch=resolved["overfetch"], config=config,
        )
    else:
        resolved_shuffle = int(gen.get("shuffle", 0))

    n_custom = sum(1 for e in entries if e.get("origin") == "custom")
    if n_custom:
        warnings.append(
            f"Se descartan {n_custom} color(es) agregados/editados a mano: una regeneración "
            "reemplaza los colores. Los modificadores simples (--my-eyes/--ying-yang) no los borran."
        )

    effective, base = palette_generator.generate_palette(
        expand_path(image), n_colors=n_colors, sample_size=resolved["sample_size"],
        mode=resolved["mode"], saturate=new_post["my_eyes"], weights=weights,
        weighted_contrast=resolved["weighted_contrast"], shuffle=resolved_shuffle,
        overfetch=resolved["overfetch"], ying_yang=new_post["ying_yang"],
        saturate_factor=new_post["my_eyes_factor"], saturate_max_chroma=new_post["my_eyes_max_chroma"],
        shading_direction=resolved["shading_direction"], shading_min_l=resolved["shading_min_luminance"],
        shading_max_l=resolved["shading_max_luminance"], with_base=True,
    )
    new_entries = [{"id": i + 1, "hex": c["hex"], "label": c["label"], "origin": "gen"}
                   for i, c in enumerate(effective)]
    new_meta = dict(meta)
    new_meta["generated"] = True
    new_meta["gen"] = {
        "colors": n_colors, "sample_size": resolved["sample_size"], "mode": resolved["mode"],
        "scoring": resolved["scoring"], "custom_percentages": resolved["custom_percentages"],
        "weighted_contrast": resolved["weighted_contrast"], "shuffle": resolved_shuffle,
        "overfetch": resolved["overfetch"], "shading_direction": resolved["shading_direction"],
        "shading_min_luminance": resolved["shading_min_luminance"],
        "shading_max_luminance": resolved["shading_max_luminance"],
    }
    new_meta["post"] = new_post
    new_meta["base"] = [{"hex": c["hex"], "label": c["label"], "origin": "gen"} for c in base]
    return new_entries, new_meta
