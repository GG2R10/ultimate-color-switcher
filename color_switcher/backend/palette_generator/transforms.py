#!/usr/bin/env python3
"""
transforms.py — Per-color post-processing applied to already-chosen palette
entries: WCAG contrast adjustment, the --my-eyes saturation boost, and the
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


_MY_EYES_SATURATION_BOOST = 38.0  # HSL saturation points added, clamped to 100


def _boost_saturation(color: dict, amount: float = _MY_EYES_SATURATION_BOOST) -> dict:
    """--my-eyes: bump a final color's HSL saturation by `amount` points
    (clamped to 100), keeping hue and lightness as-is."""
    hue, sat, light = cm.rgb_to_hsl(color["rgb"])
    new_sat = min(sat + amount, 100.0)
    rgb = cm.hsl_to_rgb(hue, new_sat, light)
    return _make_color_entry(cm.rgb_to_lab(rgb), label=color.get("label"))


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


def apply_post_modifiers(entries: list, my_eyes: bool = False, ying_yang: bool = False) -> list:
    """Apply the image-INDEPENDENT post modifiers (my-eyes, ying-yang) to
    already-final palette entries ([{"hex", "label"}, ...]), returning the
    same shape.

    This is the primitive `shift` uses to toggle my-eyes/ying-yang on a
    HAND-CREATED palette (which has no image to regenerate from). It is
    reversible by construction: `shift` always re-derives the effective colors
    from the stored *base* rather than mutating in place, so turning a modifier
    back off recomputes the original exactly (short-circuit below) -- important
    because _boost_saturation clamps at 100 and so isn't individually
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
            color = _boost_saturation(color)
        if ying_yang:
            color = _complement_hue(color)
        out.append({"hex": color["hex"], "label": e.get("label", "")})
    return out
