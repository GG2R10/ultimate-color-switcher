#!/usr/bin/env python3
"""
palette_shift.py — Re-tweak an existing palette (its modifiers, or individual
colors) and hand back the new colors, WITHOUT the caller re-passing the image
or the whole flag set that first produced it. Powers `ucs automatic shift` and
`ucs palette edit|remove`, plus the GUI's Modificadores / edit / delete.

Model (see [[palette-shift-design]]): a palette's #ucs-meta stores an ordered
`base` list — every color, each tagged origin gen|custom, plus an optional
foreground/background role (user metadata, like label — never touched by a
post-mod, carried through verbatim) — plus the active `post` modifiers. The
CSV rows are the EFFECTIVE colors, always `apply_post_modifiers(base, post)`
(so the applied file is unchanged in shape).

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

from . import color_detector
from . import color_math as cm
from . import mapping_store
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
                                shading_max_luminance=92.0, keep_custom=None, eco=None,
                                hallucinate=None, mapping_path=None):
    """Shared by the CLI (`palette generate` / `automatic --from-image`) and
    the GUI ("Generar paleta desde imagen…") -- the one place that turns an
    image + generation params into a saved palette CSV with full #ucs-meta.
    Returns (entries, saved_path, warnings) — entries is the id/hex/label
    list ready to hand to guiless.apply_palette directly (no need to re-read
    the file back).

    shuffle=None means "no shuffle override was requested" -- distinct from
    an explicit 0, since resolving it (and persisting it as the new
    "last_shuffle" anchor for a future --shuffle next) only happens when
    shuffling was actually requested.

    my_eyes_factor/my_eyes_max_chroma: only used when saturate=True -- see
    palette_generator._boost_saturation.

    shading_direction/shading_min_luminance/shading_max_luminance: only used
    when mode="shading" -- see palette_generator.generate_shading_series.

    keep_custom (on|off|toggle|None): if out_path ALREADY exists as a palette
    (e.g. a repeated `automatic --from-image` wallpaper-switch hook always
    targeting the same default generated.csv), this decides whether hand-
    added/edited colors already there survive this fresh generation instead
    of being silently overwritten -- same on/off/toggle/keep-current
    semantics and same positional-overlay mechanism as palette_shift's own
    regeneration (see _plan_regen_merge/_merge_regen_base). The "current"
    value resolved against is out_path's OWN stored keep_custom_on_regen if
    it already exists, else the project's remembered palette_generation
    default (config.json) -- so a brand new out_path still has a sensible
    fallback instead of always hardcoding True. Whatever gets resolved is
    persisted both into the new file's #ucs-meta and back into the project's
    palette_generation settings (same "last used becomes the new remembered
    default" pattern resolve_shuffle_index already uses for last_shuffle).

    Foreground/background PAIRING (see [[color-roles-design]]'s pairing
    rework) is ALWAYS resolved and considered -- there's no on/off flag for
    it anymore (the old count-based "consider fg/bg roles at all" mechanism,
    `consider_plane`, was fully retired: a color tagged fg/bg with no
    explicit pair simply does nothing now, which is warned about in
    `warnings` rather than gated behind a flag). The actual pairs are
    resolved in priority order: (1) detected colors explicitly linked in
    color_roles.json AND present in `mapping_path`'s mapping (default:
    config.mapping_csv), or every valid pair in color_roles.json unfiltered
    if there's no mapping at all -- see color_detector.compute_role_pairs;
    (2) out_path's own current pairing, ONLY as a fallback when (1) comes up
    empty (e.g. a pairing set purely by hand via palette_shift.set_pair,
    with no color_roles.json backing at all for tier 1 to ever see) -- see
    _role_pairs_from_entries. Tier 1 used to be preferred UNCONDITIONALLY
    over the detected side whenever out_path already existed, but that self-
    reinforces: it recomputes a floating-point Lab-L target from out_path's
    OWN current hex values, so a deterministic regeneration (e.g. `mode=
    "shading"` against the same wallpaper) just reads back whatever delta
    the PREVIOUS run happened to produce instead of the real originally-
    detected one, drifting away from it forever once anything nudges it off.
    Re-deriving from color_roles.json first avoids that drift.

    Since a fresh pair can land ANYWHERE in the generated pool (not
    necessarily the same slot a previous generation used), any of
    `mapping_path`'s existing entries whose old_id is one of these detected
    fg/bg colors get REWRITTEN to point at wherever the pair actually
    landed this time -- see _pending_mapping_updates/_apply_mapping_updates.
    Otherwise "the detected foreground/background maps to the generated
    foreground/background" would silently break on every regeneration.

    eco (on|off|toggle|None): whether case1/2 pairs are forced to the same
    hue (contrast purely by luminance) -- same resolution shape as
    keep_custom (out_path's own stored eco_contrast if it exists, else the
    project's remembered default). See
    palette_generator.generate_palette's eco param.

    hallucinate (on|off|toggle|None): whether a monochrome source image gets
    a synthesized accent (+ a shading ramp off it) instead of a genuinely
    greyscale palette -- see palette_generator.generate_palette's
    hallucinate param. Same resolution shape as keep_custom/eco (out_path's
    own stored hallucinate_on_monochrome if it exists, else the project's
    remembered default).

    Raises ShiftError if the image doesn't have enough real color diversity to
    produce as many colors as generation needs (see
    _check_generated_enough_colors) -- most likely with hallucinate=off
    against a flat/near-monochrome image."""
    if not out_path:
        out_path = config.generated_palette_csv
    if not out_path.endswith(".csv"):
        out_path += ".csv"
    if not os.path.isabs(out_path):
        out_path = os.path.join(config.palettes_created_dir, out_path)

    existing_entries, existing_meta = palette_store.read_palette(out_path)
    gen_settings = palette_generator.read_generation_settings(config)
    stored_keep_custom = (
        existing_meta.get("keep_custom_on_regen", True) if existing_entries
        else gen_settings.get("keep_custom_on_regen", True)
    )
    resolved_keep_custom = _resolve_bool(stored_keep_custom, keep_custom)
    stored_hallucinate = (
        existing_meta.get("hallucinate_on_monochrome", True) if existing_entries
        else gen_settings.get("hallucinate_on_monochrome", True)
    )
    resolved_hallucinate = _resolve_bool(stored_hallucinate, hallucinate)
    stored_eco = (
        existing_meta.get("eco_contrast", False) if existing_entries
        else gen_settings.get("eco_contrast", False)
    )
    resolved_eco = _resolve_bool(stored_eco, eco)
    gen_settings["keep_custom_on_regen"] = resolved_keep_custom
    gen_settings["hallucinate_on_monochrome"] = resolved_hallucinate
    gen_settings["eco_contrast"] = resolved_eco
    palette_generator.write_generation_settings(config, gen_settings)

    old_base = reconstruct_base(existing_entries, existing_meta) if existing_entries else []
    warnings = []
    n_gen_needed, custom_in_range, custom_trailing = _plan_regen_merge(
        old_base, n_colors, resolved_keep_custom, warnings,
    )

    # Prefer re-deriving from the detected side (color_roles.json) --
    # the TRUE, non-drifting reference for the luminance target -- and only
    # fall back to out_path's own current pairing (tier 1) when nothing is
    # derivable from the detected side at all (see docstring above).
    detected_colors = color_detector.read_detected_csv(config.detected_palette_csv)
    roles = color_detector.read_color_roles(config.color_roles_json)
    _old_p, _new_p, mapping_entries = mapping_store.read_mapping_csv(
        mapping_path or config.mapping_csv, project_dir=config.project_dir,
    )
    role_pairs = color_detector.compute_role_pairs(detected_colors, roles, mapping_entries)
    unpaired = color_detector.tagged_without_pair(roles)
    if unpaired:
        warnings.append(
            f"{len(unpaired)} color(es) marcado(s) foreground/background sin pareja vinculada "
            "no se toman en cuenta para la generación."
        )
    if existing_entries and not role_pairs:
        role_pairs = _role_pairs_from_entries(existing_entries)
    _check_role_pairs_fit(n_colors, len(role_pairs))

    weights = palette_generator.resolve_scoring_weights(
        scoring, custom_percentages=custom_scoring_values, config=config,
    )
    resolved_shuffle = (
        palette_generator.resolve_shuffle_index(shuffle, n_colors, overfetch=overfetch, config=config)
        if shuffle is not None else 0
    )
    _unused_effective, base_colors = palette_generator.generate_palette(
        image, n_colors=n_gen_needed, sample_size=sample_size, mode=mode,
        saturate=False, weights=weights, weighted_contrast=weighted_contrast,
        shuffle=resolved_shuffle, overfetch=overfetch, ying_yang=False,
        shading_direction=shading_direction, shading_min_l=shading_min_luminance,
        shading_max_l=shading_max_luminance,
        role_pairs=role_pairs, eco=resolved_eco,
        hallucinate=resolved_hallucinate, with_base=True,
    )
    _check_generated_enough_colors(base_colors, n_gen_needed, resolved_hallucinate)
    fresh_gen = [{"hex": c["hex"], "label": c["label"], "origin": "gen",
                 "role": c.get("role"), "pair_id": c.get("pair_id")}
                for c in base_colors]
    merged_base = _merge_regen_base(fresh_gen, n_colors, custom_in_range, custom_trailing, resolved_keep_custom)

    post = {"my_eyes": bool(saturate), "ying_yang": bool(ying_yang),
            "my_eyes_factor": my_eyes_factor, "my_eyes_max_chroma": my_eyes_max_chroma}
    # Rows are the EFFECTIVE (applied) colors; the pre-post-mod base + the
    # generation params ride along in #ucs-meta so `shift` can re-tweak & re-apply
    # this palette without re-passing the image or the whole flag set.
    entries = derive_effective(merged_base, post)
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
        "post": post,
        "keep_custom_on_regen": resolved_keep_custom,
        "hallucinate_on_monochrome": resolved_hallucinate,
        "eco_contrast": resolved_eco,
        "base": merged_base,
    })
    palette_store.write_palette_csv(out_path, entries, meta=meta)
    _apply_mapping_updates(mapping_path or config.mapping_csv, config,
                            _pending_mapping_updates(role_pairs, merged_base))
    return entries, out_path, warnings


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


def _pair_ids_from_entries(entries):
    """Per-entry-index stable-for-THIS-load `pair_id`, derived from the
    CSV's own numeric `paired_id` cross-reference: `min(id,partner)::
    max(id,partner)`. This is what lets derive_effective's group-by-pair_id
    step find the same 2 entries again later even if some OTHER entry gets
    removed/reordered in between -- a raw stored numeric cross-reference
    would go stale the moment ids shift, which is exactly the "new_id
    treated too loosely" bug class this project has hit before."""
    by_id = {e["id"] for e in entries}
    pair_ids = [None] * len(entries)
    for i, e in enumerate(entries):
        partner_id = e.get("paired_id")
        if not partner_id or partner_id not in by_id:
            continue
        a, b = e["id"], partner_id
        pair_ids[i] = f"{min(a, b)}::{max(a, b)}"
    return pair_ids


def reconstruct_base(entries, meta):
    """The full ordered base (one entry per color, with origin/role/pair_id),
    robust to the formats a palette file can be in:
      - new: meta['base'] covers every row (len matches) -> used directly.
      - older: meta['base'] held only gen colors, customs were separate literal
        rows -> gen bases come from meta['base'], custom bases from the row hex.
      - legacy: no meta['base'] at all -> every row is its own base.
    Always aligned 1:1 with `entries` by position. Role/pair_id, like label,
    are pure user metadata a post-mod transform never touches -- carried
    through verbatim rather than defaulted (unlike origin, which always has
    a value). pair_id prefers the stored base's own value (the authoritative
    pre-post-mod source), falling back to reconstructing one from the row's
    numeric paired_id (see _pair_ids_from_entries) for an older/legacy file
    that never stored it on base."""
    stored = list(meta.get("base") or [])
    entry_pair_ids = _pair_ids_from_entries(entries)
    if stored and len(stored) == len(entries):
        return [
            {"hex": _norm_hex(b["hex"]), "label": b.get("label", ""),
             "origin": b.get("origin") or (entries[i].get("origin") or "gen"),
             "role": b.get("role") or entries[i].get("role"),
             "pair_id": b.get("pair_id") or entry_pair_ids[i]}
            for i, b in enumerate(stored)
        ]
    base = []
    gi = 0
    for i, e in enumerate(entries):
        if e.get("origin") == "custom":
            base.append({"hex": _norm_hex(e["hex"]), "label": e.get("label", ""),
                        "origin": "custom", "role": e.get("role"), "pair_id": entry_pair_ids[i]})
        elif gi < len(stored):
            base.append({"hex": _norm_hex(stored[gi]["hex"]),
                         "label": stored[gi].get("label", e.get("label", "")), "origin": "gen",
                         "role": stored[gi].get("role") or e.get("role"),
                         "pair_id": stored[gi].get("pair_id") or entry_pair_ids[i]})
            gi += 1
        else:
            base.append({"hex": _norm_hex(e["hex"]), "label": e.get("label", ""),
                        "origin": "gen", "role": e.get("role"), "pair_id": entry_pair_ids[i]})
    return base


def derive_effective(base, post):
    """The effective (to-apply) rows for a base under the active post-mods:
    apply_post_modifiers to EVERY color (gen and custom), order preserved,
    ids renumbered 1..N, origin/role carried through.

    pair_id (a stable per-color identity shared by both sides of a pair --
    see palette_generator.fgbg_pairing) is resolved into a concrete
    `paired_id` (the OTHER row's final 1-based id) fresh on EVERY call, by
    grouping base entries that share the same pair_id -- never trusting a
    previously-written numeric cross-reference, so a delete/edit elsewhere
    can never leave a stale paired_id behind. A pair_id surviving on only
    ONE row (its partner got discarded) simply doesn't get a paired_id."""
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
        if b.get("role"):
            row["role"] = b["role"]
        rows.append(row)

    by_pair_id = {}
    for i, b in enumerate(base):
        pid = b.get("pair_id")
        if pid is not None:
            by_pair_id.setdefault(pid, []).append(i)
    for indices in by_pair_id.values():
        if len(indices) == 2:
            i, j = indices
            rows[i]["paired_id"] = rows[j]["id"]
            rows[j]["paired_id"] = rows[i]["id"]

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


# Sentinel for edit_color's role param: "not passed, leave whatever role this
# slot already had untouched" -- distinct from None, which means "explicitly
# clear it back to unmarked" (role=None is itself a meaningful value here).
_UNSET = object()


def _debased_hex(hex_value, meta):
    """A hand-picked hex, converted to what BASE must store so the active
    post-modifiers reproduce it exactly as picked once derive_effective runs
    forward again -- see palette_generator.debase_for_post. Without this, a
    custom color would visibly show up transformed on top of what was
    picked (e.g. picking yellow while ying-yang is active would render as
    blue), and inconsistently with how a generated color's base already
    works (always pre-transform)."""
    post = _post_of(meta)
    return palette_generator.debase_for_post(
        hex_value, my_eyes=bool(post.get("my_eyes")), ying_yang=bool(post.get("ying_yang")),
        my_eyes_factor=post.get("my_eyes_factor", palette_generator._MY_EYES_CHROMA_FACTOR),
        my_eyes_max_chroma=post.get("my_eyes_max_chroma", palette_generator._MY_EYES_CHROMA_MAX),
    )


def _clear_pairing(base, idx):
    """Clear base[idx]'s own pair_id AND null out whichever OTHER entry was
    sharing it (its partner) -- call this right before an operation
    invalidates base[idx]'s side of a pairing (role changed away from
    fg/bg, or the color is about to be removed entirely). A pair_id
    surviving on only one side is harmless (derive_effective just won't
    emit a paired_id for it) but this keeps state tidy instead of leaving
    stale, never-again-matching metadata around -- same "fix up what
    pointed at the thing being changed" spirit as
    mapping_store.drop_and_shift_new_id / color_detector.
    clear_dangling_pairs_after_role_change."""
    pair_id = base[idx].get("pair_id")
    if pair_id is None:
        return
    for i, b in enumerate(base):
        if i != idx and b.get("pair_id") == pair_id:
            base[i] = dict(b, pair_id=None)
    base[idx] = dict(base[idx], pair_id=None)


def add_color(path, hex_value, label="", role=None):
    """Append a user-chosen color (origin custom) to a palette, rejecting a
    duplicate. Returns the new effective row entry."""
    entries, meta, base = _load(path)
    if _has_dup(base, hex_value):
        raise PaletteEditError(f"El color #{_norm_hex(hex_value)} ya existe en la paleta.")
    base.append({"hex": _debased_hex(hex_value, meta), "label": label, "origin": "custom",
                "role": role, "pair_id": None})
    rows = _write_derived(path, base, meta)
    return rows[-1]


def edit_color(path, target, new_hex, role=_UNSET):
    """Change a color (by id or hex) to new_hex, marking it custom. Rejects a
    duplicate. `role` left at its default keeps the slot's existing role
    (None explicitly clears it back to unmarked). Returns the 1-based id of
    the edited slot (unchanged: an edit keeps its position, so any mapping to
    it stays valid). Changing the role away from what it was clears this
    slot's pairing (and its partner's) -- see _clear_pairing; the hex itself
    changing doesn't, by itself, invalidate an existing pairing."""
    entries, meta, base = _load(path)
    if not base:
        raise PaletteEditError(f"Paleta vacía o no encontrada: {path}")
    idx = _find_index(base, entries, target)
    if idx is None:
        raise PaletteEditError(f"No se encontró el color {target!r} en la paleta.")
    if _has_dup(base, new_hex, exclude_index=idx):
        raise PaletteEditError(f"El color #{_norm_hex(new_hex)} ya existe en la paleta.")
    old_role = base[idx].get("role")
    new_role = old_role if role is _UNSET else role
    if new_role != old_role:
        _clear_pairing(base, idx)
    base[idx] = {"hex": _debased_hex(new_hex, meta), "label": base[idx].get("label", ""),
                "origin": "custom", "role": new_role, "pair_id": base[idx].get("pair_id")}
    _write_derived(path, base, meta)
    return idx + 1


def set_role(path, target, role):
    """Set (or clear, role=None) a color's role WITHOUT touching its
    hex/label/origin -- unlike edit_color, this never marks a gen color
    custom (used by the GUI's role toggle button, which only ever changes
    the role, never the color value itself). Changing the role away from
    what it was clears this slot's pairing (and its partner's) -- see
    _clear_pairing. Returns the 1-based id of the affected slot."""
    entries, meta, base = _load(path)
    if not base:
        raise PaletteEditError(f"Paleta vacía o no encontrada: {path}")
    idx = _find_index(base, entries, target)
    if idx is None:
        raise PaletteEditError(f"No se encontró el color {target!r} en la paleta.")
    if role != base[idx].get("role"):
        _clear_pairing(base, idx)
    base[idx] = dict(base[idx], role=role)
    _write_derived(path, base, meta)
    return idx + 1


def set_pair(path, target_a, target_b=None):
    """Manually link (or, target_b=None, unlink) two palette colors as an
    explicit fg/bg pair -- symmetric to set_role: touches ONLY pair_id on
    both sides, never hex/label/origin/role. Doesn't validate that the two
    colors' roles are actually foreground/background (that's
    conflicts.find_pair_mismatches's job -- a warning, not a block), same
    "simplicity over cleverness" spirit as this module's other manual
    editing tools. Whatever pairing `target_a` (and, if linking, `target_b`)
    already had is dropped first (see _clear_pairing) before the new link is
    made. Returns the 1-based id of `target_a`'s slot."""
    entries, meta, base = _load(path)
    if not base:
        raise PaletteEditError(f"Paleta vacía o no encontrada: {path}")
    idx_a = _find_index(base, entries, target_a)
    if idx_a is None:
        raise PaletteEditError(f"No se encontró el color {target_a!r} en la paleta.")

    _clear_pairing(base, idx_a)

    if target_b is not None:
        idx_b = _find_index(base, entries, target_b)
        if idx_b is None:
            raise PaletteEditError(f"No se encontró el color {target_b!r} en la paleta.")
        if idx_b == idx_a:
            raise PaletteEditError("Un color no puede ser su propia pareja.")
        _clear_pairing(base, idx_b)
        id_a, id_b = entries[idx_a]["id"], entries[idx_b]["id"]
        pair_id = f"{min(id_a, id_b)}::{max(id_a, id_b)}"
        base[idx_a] = dict(base[idx_a], pair_id=pair_id)
        base[idx_b] = dict(base[idx_b], pair_id=pair_id)

    _write_derived(path, base, meta)
    return idx_a + 1


def delete_color(path, target):
    """Remove a color (by id or hex). Returns the 1-based id it had, so the
    caller can adjust a mapping (unassign that new_id, shift higher ones down --
    see mapping_store.drop_and_shift_new_id). Renumbers the palette to stay
    contiguous, which guiless's positional matching relies on. If this color
    was paired, its partner's pairing is cleared too (see _clear_pairing)."""
    entries, meta, base = _load(path)
    if not base:
        raise PaletteEditError(f"Paleta vacía o no encontrada: {path}")
    idx = _find_index(base, entries, target)
    if idx is None:
        raise PaletteEditError(f"No se encontró el color {target!r} en la paleta.")
    _clear_pairing(base, idx)
    del base[idx]
    _write_derived(path, base, meta)
    return idx + 1


# --------------------------------------------------------------------------- #
# shift (modifiers)
# --------------------------------------------------------------------------- #

_SELECTION_KEYS = ("mode", "scoring", "custom_scoring_values", "weighted_contrast",
                   "shuffle", "overfetch", "colors", "shading_direction",
                   "shading_min_luminance", "shading_max_luminance")


def _plan_regen_merge(old_base, n_colors, keep_custom, warnings):
    """Shared by _regenerate (palette shift) and generate_and_save_palette
    (palette generate/automatic --from-image, when out_path already has
    content): given an existing base (possibly empty -- a brand new out_path
    has nothing to preserve) and a freshly requested n_colors, decide the
    regen plan and append the right discard/role-loss warnings (a list,
    mutated in place). Raises ShiftError if keep_custom is on and there's no
    room. Returns (n_gen_needed, custom_in_range, custom_trailing) -- the
    last two keyed by ORIGINAL position, for _merge_regen_base."""
    custom_by_pos = {i: b for i, b in enumerate(old_base) if b.get("origin") == "custom"}
    n_custom = len(custom_by_pos)
    # Only customs that landed WITHIN the requested n_colors (edited in
    # place, same slot they always had) compete for the gen budget -- ones
    # added past the original block never occupied a numbered gen slot.
    custom_in_range = {i: b for i, b in custom_by_pos.items() if i < n_colors}
    custom_trailing = {i: b for i, b in custom_by_pos.items() if i >= n_colors}
    n_custom_in_range = len(custom_in_range)

    if keep_custom and n_custom_in_range >= n_colors:
        raise ShiftError(
            f"Esta paleta tiene {n_custom_in_range} color(es) editado(s) a mano dentro de los "
            f"primeros {n_colors} que pediste -- no queda espacio para generar ninguno nuevo. "
            f"Pedí al menos {n_custom_in_range + 1} colores, desactivá --keep-custom para esta "
            "regeneración, o creá una paleta nueva desde cero."
        )

    if not keep_custom and n_custom:
        warnings.append(
            f"Se descartan {n_custom} color(es) agregados/editados a mano: una regeneración "
            "reemplaza los colores. Los modificadores simples (--my-eyes/--ying-yang) no los borran."
        )
    # A PAIRED color's role/pairing is NOT actually lost on a regeneration:
    # tier 1 (_role_pairs_from_entries/_role_demand_from_entries-equivalent)
    # always reads the CURRENT pairing from old_base BEFORE regenerating and
    # feeds it back in as role_pairs, so the same relationship gets
    # reproduced on fresh colors elsewhere in the new palette (only the
    # specific hex values change -- expected of any regeneration). Warning
    # about this would be a false alarm. A role with NO pairing, though,
    # genuinely has no fallback -- it's plain metadata nothing re-derives --
    # so losing it (when its color isn't preserved by keep_custom) really
    # is a total, unrecoverable loss worth flagging.
    n_roled_lost = sum(
        1 for i, b in enumerate(old_base)
        if b.get("role") and not b.get("pair_id") and not (keep_custom and i in custom_by_pos)
    )
    if n_roled_lost:
        warnings.append(
            f"Se pierde el rol foreground/background (sin pareja vinculada) asignado a "
            f"{n_roled_lost} color(es) de esta paleta: una regeneración reemplaza los colores, "
            "así que quedan sin marcar de nuevo."
        )

    n_gen_needed = (n_colors - n_custom_in_range) if keep_custom else n_colors
    return n_gen_needed, custom_in_range, custom_trailing


def _merge_regen_base(fresh_gen, n_colors, custom_in_range, custom_trailing, keep_custom):
    """Overlay fresh_gen (n_gen_needed colors, freshly generated) with the
    preserved custom slots BY POSITION -- an edited-in-place custom must stay
    at the same id it always had, or an existing mapping entry pointing at it
    would silently start resolving to a different, freshly-regenerated color."""
    if not keep_custom:
        return fresh_gen
    merged = []
    gi = 0
    for i in range(n_colors):
        if i in custom_in_range:
            merged.append(custom_in_range[i])
        else:
            merged.append(fresh_gen[gi])
            gi += 1
    merged.extend(b for i, b in sorted(custom_trailing.items()))
    return merged


def _role_pairs_from_entries(entries: list) -> list:
    """Tier 1 of the pairing resolution (see [[color-roles-design]]'s
    pairing rework): an EXISTING palette's own already-paired colors are the
    most accurate source -- no need to touch color_roles.json/the mapping
    at all when regenerating something that already carries its own
    pairing. Computes each entry's own Lab L directly from its hex (mirrors
    compute_role_pairs on the detected side). `pair_id` here is just
    `"{fg_id}::{bg_id}"` -- ephemeral, only used to seed this one
    generate_palette call; a fresh pairing gets its OWN pair_id from
    fgbg_pairing regardless."""
    by_id = {e["id"]: e for e in entries}
    pairs = []
    seen = set()
    for e in entries:
        if e.get("role") != "foreground" or not e.get("paired_id"):
            continue
        bg = by_id.get(e["paired_id"])
        if bg is None or bg.get("role") != "background":
            continue
        pair_key = (e["id"], bg["id"])
        if pair_key in seen:
            continue
        seen.add(pair_key)
        fg_l = float(cm.rgb_to_lab(cm.hex_to_rgb(e["hex"]))[0])
        bg_l = float(cm.rgb_to_lab(cm.hex_to_rgb(bg["hex"]))[0])
        pairs.append({"pair_id": f"{e['id']}::{bg['id']}", "bg_l": bg_l, "fg_l": fg_l})
    return pairs


def _pending_mapping_updates(role_pairs: list, base: list) -> list:
    """A fresh generation places each fg/bg pair from `role_pairs` SOMEWHERE
    in `base` (fgbg_pairing picks whichever 2 pool candidates score best --
    not necessarily the same positions a previous generation used), but a
    mapping built before generation still points its detected-side old_ids
    at wherever the pair USED to land. Without fixing that up, "the detected
    foreground/background maps to the generated foreground/background"
    silently breaks on every regeneration -- see [[color-roles-design]].

    Only role_pairs sourced from color_detector.compute_role_pairs carry
    "fg_old_ids"/"bg_old_ids" (tier 1's _role_pairs_from_entries never does,
    since it has no detected-side old_ids to offer) -- those are the only
    ones this can act on. Returns [(old_id, new_id), ...] ready to feed
    into a MappingStore, empty if nothing needs rewiring."""
    id_by_pair_role = {}
    for i, b in enumerate(base):
        pid, role = b.get("pair_id"), b.get("role")
        if pid is not None and role:
            id_by_pair_role.setdefault(pid, {})[role] = i + 1

    updates = []
    for pair in role_pairs:
        fg_ids = pair.get("fg_old_ids") or []
        bg_ids = pair.get("bg_old_ids") or []
        if not fg_ids and not bg_ids:
            continue
        landed = id_by_pair_role.get(pair.get("pair_id"))
        if not landed or "foreground" not in landed or "background" not in landed:
            continue  # pool ran out before this pair got realized -- nothing to rewire
        updates.extend((old_id, landed["foreground"]) for old_id in fg_ids)
        updates.extend((old_id, landed["background"]) for old_id in bg_ids)
    return updates


def _apply_mapping_updates(mapping_path: str, config, updates: list) -> None:
    """Persist `_pending_mapping_updates`' output to the mapping file, if
    any. Loads fresh from disk (never reuses a caller's possibly-stale
    in-memory MappingStore) so this is safe to call right after a write
    that a long-lived GUI object hasn't seen yet."""
    if not updates:
        return
    store = mapping_store.MappingStore(mapping_path, project_dir=config.project_dir).load()
    for old_id, new_id in updates:
        store.add_or_update(old_id, new_id, persist=False)
    store.save()


def _check_role_pairs_fit(n_colors: int, n_pairs: int) -> None:
    """Each pair needs 2 DISTINCT colors (one background, one foreground) --
    same shape of hard constraint as keep_custom's budget check: refuse
    outright rather than silently under-cover."""
    total_needed = n_pairs * 2
    if total_needed > n_colors:
        raise ShiftError(
            f"Hay {n_pairs} pareja(s) foreground/background vinculada(s) (hacen falta al menos "
            f"{total_needed} color(es)), pero pediste {n_colors} en total. Pedí al menos "
            f"{total_needed} colores (--colors), desvinculá alguna pareja, o creá una paleta "
            "nueva desde cero."
        )


def _check_generated_enough_colors(base_colors: list, n_gen_needed: int, hallucinate: bool) -> None:
    """generate_palette can return FEWER colors than requested when the image's
    real clusters run out (select_auxiliaries/select_secondary just have
    nothing left to pick from) -- most likely with hallucinate=off against a
    flat/near-monochrome image, but possible any time real color diversity is
    scarce. Without this check, `_merge_regen_base` would index past the end
    of a too-short `fresh_gen` (an opaque IndexError) when keep_custom is on,
    or silently hand back fewer colors than asked for when it's off. Same
    treatment as the other "not enough room" checks here: refuse outright with
    a clean, actionable message instead of either failure mode."""
    if len(base_colors) < n_gen_needed:
        hint = (
            "activá --hallucinate para que sintetice un acento en vez de depender de los "
            "colores reales de la imagen, "
        ) if not hallucinate else ""
        raise ShiftError(
            f"La imagen no tiene suficiente diversidad de color real para generar {n_gen_needed} "
            f"color(es) -- sólo se pudieron obtener {len(base_colors)}. Pedí menos colores "
            f"(--colors), {hint}o probá con otra imagen."
        )


def shift_palette(palette_path, config, *, my_eyes=None, ying_yang=None,
                  my_eyes_factor=None, my_eyes_max_chroma=None,
                  mode=None, scoring=None, custom_scoring_values=None,
                  weighted_contrast=None, shuffle=None, overfetch=None, colors=None,
                  shading_direction=None, shading_min_luminance=None, shading_max_luminance=None,
                  keep_custom=None, eco=None, hallucinate=None, mapping_path=None, write=True) -> dict:
    """Compute (and, unless write=False, persist) the shifted palette.

    Boolean modifiers (my_eyes, ying_yang) take on|off|toggle|None.
    shading_direction takes dark|light|toggle|None (like a boolean modifier,
    but a 2-way choice instead -- see _resolve_shading_direction).
    my_eyes_factor/my_eyes_max_chroma are POST modifiers too (no
    regeneration needed, just a different multiplier/cap applied to the same
    stored base) -- a concrete value or None ("keep stored"). Everything else
    (selection modifiers, including the luminance bounds) also takes a
    concrete value or None ("keep stored"), but DOES trigger a regenerate.
    keep_custom/eco/hallucinate (on|off|toggle|None) are stored per-palette
    REGEN POLICIES, not post-modifiers -- none of them triggers a regenerate
    by itself, but they control what a regenerate does with hand-added/
    edited colors, fg/bg pair hue (--eco), and a monochrome source image
    respectively (see _regenerate). fg/bg PAIRING itself is always resolved
    on a regenerate (no flag) -- see _regenerate's own docstring.
    mapping_path (default: config.mapping_csv) is only used for that
    pairing resolution (which detected colors currently have real demand).

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
    resolved_keep_custom = _resolve_bool(meta.get("keep_custom_on_regen", True), keep_custom)
    resolved_eco = _resolve_bool(meta.get("eco_contrast", False), eco)
    resolved_hallucinate = _resolve_bool(meta.get("hallucinate_on_monochrome", True), hallucinate)
    selection = {"mode": mode, "scoring": scoring, "custom_scoring_values": custom_scoring_values,
                 "weighted_contrast": weighted_contrast, "shuffle": shuffle,
                 "overfetch": overfetch, "colors": colors,
                 "shading_direction": shading_direction,
                 "shading_min_luminance": shading_min_luminance,
                 "shading_max_luminance": shading_max_luminance}
    wants_regen = any(selection[k] is not None for k in _SELECTION_KEYS)
    warnings = []

    mapping_updates = []
    if wants_regen:
        new_entries, new_meta, mapping_updates = _regenerate(
            meta, entries, new_post, selection, config, warnings,
            resolved_keep_custom, resolved_eco, resolved_hallucinate, mapping_path,
        )
    else:
        base = reconstruct_base(entries, meta)
        new_entries = derive_effective(base, new_post)
        new_meta = dict(meta)
        new_meta["post"] = new_post
        new_meta["keep_custom_on_regen"] = resolved_keep_custom
        new_meta["eco_contrast"] = resolved_eco
        new_meta["hallucinate_on_monochrome"] = resolved_hallucinate
        new_meta["base"] = base

    if write:
        palette_store.write_palette_csv(palette_path, new_entries, meta=new_meta)
        # Only rewire the mapping once the regenerated pair is actually
        # persisted -- never on a dry-run preview (write=False, e.g. the
        # Modificadores dialog's live preview before the user confirms).
        _apply_mapping_updates(mapping_path or config.mapping_csv, config, mapping_updates)
    return {"entries": new_entries, "meta": new_meta, "regenerated": wants_regen, "warnings": warnings}


def _regenerate(meta, entries, new_post, selection, config, warnings, keep_custom, eco,
                hallucinate, mapping_path=None):
    """fg/bg pairing (see [[color-roles-design]]'s pairing rework) is ALWAYS
    resolved here, preferring color_roles.json (the TRUE, non-drifting
    reference) whenever it has something to say, falling back to `entries`'
    own current pairing (tier 1) only when nothing is derivable from the
    detected side at all (e.g. a pairing set purely by hand via set_pair,
    with no color_roles.json backing to ever see) -- see
    generate_and_save_palette's docstring for why tier 1 can't be preferred
    unconditionally anymore (it self-reinforces a drifted luminance target
    across repeated deterministic regenerations).

    Returns (new_entries, new_meta, mapping_updates) -- mapping_updates is
    _pending_mapping_updates' output, for the caller (shift_palette) to
    persist ONLY if it actually writes (never on a dry-run preview)."""
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

    old_base = reconstruct_base(entries, meta)
    n_gen_needed, custom_in_range, custom_trailing = _plan_regen_merge(
        old_base, n_colors, keep_custom, warnings,
    )

    detected_colors = color_detector.read_detected_csv(config.detected_palette_csv)
    roles = color_detector.read_color_roles(config.color_roles_json)
    _old_p, _new_p, mapping_entries = mapping_store.read_mapping_csv(
        mapping_path or config.mapping_csv, project_dir=config.project_dir,
    )
    role_pairs = color_detector.compute_role_pairs(detected_colors, roles, mapping_entries)
    unpaired = color_detector.tagged_without_pair(roles)
    if unpaired:
        warnings.append(
            f"{len(unpaired)} color(es) marcado(s) foreground/background sin pareja vinculada "
            "no se toman en cuenta para la generación."
        )
    if not role_pairs:
        role_pairs = _role_pairs_from_entries(entries)
    _check_role_pairs_fit(n_colors, len(role_pairs))

    _unused_effective, gen_base = palette_generator.generate_palette(
        expand_path(image), n_colors=n_gen_needed, sample_size=resolved["sample_size"],
        mode=resolved["mode"], saturate=False, weights=weights,
        weighted_contrast=resolved["weighted_contrast"], shuffle=resolved_shuffle,
        overfetch=resolved["overfetch"], ying_yang=False,
        shading_direction=resolved["shading_direction"], shading_min_l=resolved["shading_min_luminance"],
        shading_max_l=resolved["shading_max_luminance"],
        role_pairs=role_pairs, eco=eco,
        hallucinate=hallucinate, with_base=True,
    )
    _check_generated_enough_colors(gen_base, n_gen_needed, hallucinate)
    fresh_gen = [{"hex": c["hex"], "label": c["label"], "origin": "gen",
                 "role": c.get("role"), "pair_id": c.get("pair_id")} for c in gen_base]
    merged_base = _merge_regen_base(fresh_gen, n_colors, custom_in_range, custom_trailing, keep_custom)

    new_entries = derive_effective(merged_base, new_post)
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
    new_meta["keep_custom_on_regen"] = keep_custom
    new_meta["eco_contrast"] = eco
    new_meta["hallucinate_on_monochrome"] = hallucinate
    new_meta["base"] = merged_base
    mapping_updates = _pending_mapping_updates(role_pairs, merged_base)
    return new_entries, new_meta, mapping_updates
