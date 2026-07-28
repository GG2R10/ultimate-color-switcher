#!/usr/bin/env python3
"""
transforms.py — Per-color post-processing applied to already-chosen palette
entries: WCAG contrast adjustment, the --my-eyes chroma boost, and the
near-black/white lightness normalization. Each returns a NEW entry and never
mutates its input.
"""

import numpy as np

from .. import color_math as cm
from .color_entry import _make_color_entry


def improve_contrast(color: dict, background: dict, min_ratio: float = 3.0,
                      step: float = 5.0, max_iter: int = 12) -> dict:
    """Push `color`'s lightness away from `background`'s until the WCAG
    contrast ratio clears min_ratio (or max_iter runs out). Returns a new
    entry — never mutates the input."""
    lab = list(color["lab"])
    background_l = background["lab"][0]  # CIE Lab L -- NOT background["L"], which is HSL
    for _ in range(max_iter):
        rgb = np.clip(cm.lab_to_rgb(np.array(lab)), 0, 255)
        if cm.contrast_ratio(rgb, background["rgb"]) >= min_ratio:
            break
        if lab[0] >= background_l:
            lab[0] = min(lab[0] + step, 97)
        else:
            lab[0] = max(lab[0] - step, 3)
    return _make_color_entry(np.array(lab), label=color.get("label"))


def _reduce_contrast(color: dict, background: dict, max_ratio: float = 3.0,
                     step: float = 5.0, max_iter: int = 12) -> dict:
    """The inverse of improve_contrast: push `color`'s lightness TOWARD
    `background`'s until the WCAG contrast ratio drops to max_ratio or below
    (or max_iter runs out), clamped so it never overshoots past background's
    own lightness. Used to synthesize a background-safe candidate (see
    _assign_roles) when generation doesn't naturally have enough."""
    lab = list(color["lab"])
    background_l = background["lab"][0]
    for _ in range(max_iter):
        rgb = np.clip(cm.lab_to_rgb(np.array(lab)), 0, 255)
        if cm.contrast_ratio(rgb, background["rgb"]) <= max_ratio:
            break
        if lab[0] >= background_l:
            lab[0] = max(lab[0] - step, background_l)
        else:
            lab[0] = min(lab[0] + step, background_l)
    return _make_color_entry(np.array(lab), label=color.get("label"))


_BACKGROUND_SAFE_MAX_RATIO = 3.0   # WCAG: below this, a color reads as blending into the background
_FOREGROUND_SAFE_MIN_RATIO = 4.5   # WCAG AA, normal text


def _assign_roles(chosen: list, background_ref: dict, n_background_needed: int,
                  n_foreground_needed: int) -> tuple:
    """Auto-label each chosen color background-safe/foreground-safe by its
    OWN WCAG contrast against the image's own background reference (NOT a
    user-tagged dotfile color -- see [[color-roles-design]]), then top up any
    shortfall via improve_contrast/_reduce_contrast (push lightness, keep
    hue/chroma) so at least n_background_needed/n_foreground_needed
    candidates satisfy each threshold, best-effort (fewer than requested if
    the whole chosen set is exhausted).

    Returns (roles, chosen): `roles` is a list of None/"background"/
    "foreground" parallel to `chosen`; `chosen` itself may come back with
    some entries REPLACED (the topped-up ones) -- same length/order,
    otherwise untouched."""
    chosen = list(chosen)
    ratios = [
        cm.contrast_ratio(np.clip(cm.lab_to_rgb(np.asarray(c["lab"])), 0, 255), background_ref["rgb"])
        for c in chosen
    ]
    roles = [None] * len(chosen)

    # Best-first order: for background, lowest ratio (safest blend) first;
    # for foreground, highest ratio (clearest read) first. Reused for BOTH
    # the natural-candidate pass and, if that falls short, the top-up pass --
    # in the latter case whatever's left closest to its own threshold needs
    # the smallest nudge.
    bg_order = sorted(range(len(chosen)), key=lambda i: ratios[i])
    fg_order = sorted(range(len(chosen)), key=lambda i: -ratios[i])

    bg_assigned = 0
    for i in bg_order:
        if bg_assigned >= n_background_needed:
            break
        if ratios[i] < _BACKGROUND_SAFE_MAX_RATIO:
            roles[i] = "background"
            bg_assigned += 1

    fg_assigned = 0
    for i in fg_order:
        if fg_assigned >= n_foreground_needed:
            break
        if ratios[i] >= _FOREGROUND_SAFE_MIN_RATIO and roles[i] is None:
            roles[i] = "foreground"
            fg_assigned += 1

    if bg_assigned < n_background_needed:
        for i in (i for i in bg_order if roles[i] is None):
            if bg_assigned >= n_background_needed:
                break
            chosen[i] = _reduce_contrast(chosen[i], background_ref, max_ratio=_BACKGROUND_SAFE_MAX_RATIO)
            roles[i] = "background"
            bg_assigned += 1

    if fg_assigned < n_foreground_needed:
        for i in (i for i in fg_order if roles[i] is None):
            if fg_assigned >= n_foreground_needed:
                break
            chosen[i] = improve_contrast(chosen[i], background_ref, min_ratio=_FOREGROUND_SAFE_MIN_RATIO)
            roles[i] = "foreground"
            fg_assigned += 1

    return roles, chosen


_MY_EYES_CHROMA_FACTOR = 1.5   # multiplicative CIELAB chroma (C*) boost
_MY_EYES_CHROMA_MAX = 132.0    # cap on the resulting chroma


def _boost_saturation(color: dict, factor: float = _MY_EYES_CHROMA_FACTOR,
                      max_chroma: float = _MY_EYES_CHROMA_MAX) -> dict:
    """--my-eyes: multiply a final color's CIELAB chroma (C* = sqrt(a*²+b*²))
    by `factor`, capped at `max_chroma`, keeping hue and lightness as-is.

    Works in Lab/LCh, NOT HSL: HSL saturation is a poor proxy for perceived
    vividness, since it's driven as much by lightness as by real
    colorfulness -- a pale, near-white/near-black color can already read as
    HSL saturation=100% while its actual CIELAB chroma is quite low (e.g.
    #ffc5c2, a washed-out pink: HSL-S=100 but C*≈22), leaving it "already
    maxed" and completely untouched by an HSL-based boost even though it
    doesn't look vivid at all. Multiplying CHROMA instead means a washed-out
    color (low C*) gets genuinely more colorful, proportional to how little
    color it had; an already-vivid one (high C*) gets a proportionally
    bigger absolute boost too -- capped so a very high starting chroma can't
    run away past what's usually still representable in sRGB (an
    out-of-gamut request just gets clipped to the nearest valid RGB when
    converted back, which can drift the hue slightly at extreme values)."""
    lab = np.asarray(color["lab"], dtype=np.float64)
    light, a, b = float(lab[0]), float(lab[1]), float(lab[2])
    chroma = float(np.hypot(a, b))
    hue = float(np.arctan2(b, a))
    new_chroma = min(chroma * factor, max_chroma)
    new_a, new_b = new_chroma * np.cos(hue), new_chroma * np.sin(hue)
    return _make_color_entry(np.array([light, new_a, new_b]), label=color.get("label"))


def _complement_hue(color: dict) -> dict:
    """Ying-Yang: rotate a final color's HSL hue by 180° (its complement),
    keeping saturation and lightness. Applied to every chosen color, it flips
    the whole palette to its complementary scheme -- an orange-dominant
    wallpaper becomes blue-dominant, etc. Relative hue differences and all
    lightnesses are preserved, so primary/secondary contrast survives the
    flip untouched."""
    hue, sat, light = cm.rgb_to_hsl(color["rgb"])
    rgb = cm.hsl_to_rgb((hue + 180.0) % 360.0, sat, light)
    return _make_color_entry(cm.rgb_to_lab(rgb), label=color.get("label"))


_EXTREME_L_MIN = 10.0  # HSL lightness floor/ceiling for final palette colors
_EXTREME_L_MAX = 90.0


def _normalize_extreme_lightness(color: dict, min_l: float = _EXTREME_L_MIN,
                                 max_l: float = _EXTREME_L_MAX) -> dict:
    """Pull a near-pure black or white into a usable lightness band, keeping
    hue/saturation. Dotfiles lean on #000/#fff for text and widget
    backgrounds, so dropping a raw pure extreme into that role tends to read
    worse than a near-black/near-white -- #ffffff becomes ~#e6e6e6, #010101
    becomes ~#1a1a1a. Only touches colors already outside [min_l, max_l] in
    HSL lightness; everything in-band is returned untouched."""
    hue, sat, light = cm.rgb_to_hsl(color["rgb"])
    if min_l <= light <= max_l:
        return color
    rgb = cm.hsl_to_rgb(hue, sat, min(max(light, min_l), max_l))
    return _make_color_entry(cm.rgb_to_lab(rgb), label=color.get("label"))


def apply_post_modifiers(entries: list, my_eyes: bool = False, ying_yang: bool = False,
                         my_eyes_factor: float = _MY_EYES_CHROMA_FACTOR,
                         my_eyes_max_chroma: float = _MY_EYES_CHROMA_MAX) -> list:
    """Apply the image-INDEPENDENT post modifiers (my-eyes, ying-yang) to
    already-final palette entries ([{"hex", "label"}, ...]), returning the
    same shape.

    This is the primitive `shift` uses to toggle my-eyes/ying-yang on a
    HAND-CREATED palette (which has no image to regenerate from). It is
    reversible by construction: `shift` always re-derives the effective colors
    from the stored *base* rather than mutating in place, so turning a modifier
    back off recomputes the original exactly (short-circuit below) -- important
    because _boost_saturation clamps at max_chroma and so isn't individually
    invertible.

    Deliberately does NOT run _normalize_extreme_lightness: that is a safety
    pass for GENERATED output, not a user modifier -- a created palette's
    hand-picked colors (including a deliberate #ffffff/#000000) must be left
    exactly as chosen. Generated palettes don't go through here at all; `shift`
    regenerates them via generate_palette (image + params on hand), which keeps
    full float precision and applies its own normalize internally."""
    if not my_eyes and not ying_yang:
        return [{"hex": e["hex"].lstrip("#").lower(), "label": e.get("label", "")} for e in entries]
    out = []
    for e in entries:
        color = _make_color_entry(cm.rgb_to_lab(cm.hex_to_rgb(e["hex"])), label=e.get("label"))
        if my_eyes:
            color = _boost_saturation(color, factor=my_eyes_factor, max_chroma=my_eyes_max_chroma)
        if ying_yang:
            color = _complement_hue(color)
        out.append({"hex": color["hex"], "label": e.get("label", "")})
    return out
