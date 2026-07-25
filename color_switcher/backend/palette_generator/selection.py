#!/usr/bin/env python3
"""
selection.py — Pick the palette's roles from scored clusters: the background
reference, monochrome detection, cluster filtering, and the
primary/secondary/auxiliary/shading choices (plus the synthesized fallbacks
for images that don't offer enough real color).
"""

import numpy as np

from .. import color_math as cm
from .color_entry import _make_color_entry

_MONOCHROME_SATURATION_THRESHOLD = 8.0  # below this, treat the image as having no real hue to draw from
_MONOCHROME_TRUSTWORTHY_L = (8.0, 92.0)  # HSL saturation is numerically unreliable outside this band


def _is_monochrome(clusters: list) -> bool:
    """Whether the image has essentially no real hue to build a primary from.

    Only clusters within a trustworthy lightness band count: HSL saturation
    is numerically unstable near pure black/white (a near-black JPEG-noise
    cluster can report S≈78 off a few stray RGB units), so folding those fake
    highs into the max made a genuinely greyscale image look colorful -- and,
    worse, made the verdict depend on how many clusters (k) were requested,
    since a larger k surfaces more extreme-L noise. Restricting to mid-L
    clusters makes the check k-independent. If nothing sits in the band at all
    (a pure black-and-white image), that's monochrome too."""
    trustworthy = [
        c for c in clusters
        if _MONOCHROME_TRUSTWORTHY_L[0] <= c["L"] <= _MONOCHROME_TRUSTWORTHY_L[1]
    ]
    if not trustworthy:
        return True
    return max(c["S"] for c in trustworthy) < _MONOCHROME_SATURATION_THRESHOLD


def find_background_color(clusters: list) -> dict:
    """The single highest-coverage cluster from the FULL (unfiltered)
    cluster set — used as a contrast reference, not necessarily itself a
    candidate for the palette."""
    return max(clusters, key=lambda c: c["coverage"])


_ACCENT_HUE_STEP = 137.508  # golden angle: consecutive shuffles land far apart on the color wheel


def synthesize_accent_color(seed: int = 42, hue_offset: float = 0.0, saturation: float = 55.0,
                             lightness: float = 55.0, label: str = "accent") -> dict:
    """
    A saturated color for near-monochrome images, where no real cluster has
    meaningful hue to offer (see _MONOCHROME_SATURATION_THRESHOLD). The base
    hue is picked from `seed`; generate_palette derives that seed from the
    image's own pixels so different greyscale wallpapers get different accents
    while a given wallpaper stays reproducible.

    `hue_offset` (degrees) rotates the base hue -- generate_palette feeds it
    shuffle × the golden angle so `--shuffle` steps through *clearly* distinct
    accents instead of the near-identical hues a shuffled RNG seed produced
    (consecutive seeds could land a few degrees apart, all reading as "the
    same purple").
    """
    rng = np.random.default_rng(seed)
    hue = (float(rng.uniform(0, 360)) + hue_offset) % 360
    rgb = cm.hsl_to_rgb(hue, saturation, lightness)
    return _make_color_entry(cm.rgb_to_lab(rgb), label=label)


def _dedupe(clusters: list, dup_delta_e: float) -> list:
    """Keep the highest-coverage member of each near-duplicate (ΔE < dup_delta_e) group."""
    ordered = sorted(clusters, key=lambda c: c["coverage"], reverse=True)
    kept = []
    for c in ordered:
        if all(cm.delta_e76(c["lab"], k["lab"]) >= dup_delta_e for k in kept):
            kept.append(c)
    return kept


def filter_clusters(
    clusters: list,
    min_needed: int,
    black_l: float = 12.0,
    white_l: float = 93.0,
    min_saturation: float = 18.0,
    dup_delta_e: float = 5.0,
) -> list:
    """
    Drop extreme blacks/whites, very desaturated colors, and near-duplicates.
    If the strict thresholds leave fewer than min_needed clusters (e.g. a
    near-monochrome wallpaper), progressively relax them instead of
    returning too few colors to build a palette from.

    The strict thresholds are deliberately unforgiving on saturation
    (min_saturation) -- a low-chroma grey has no business being a palette
    color -- while only cutting L at true near-black/near-white extremes, so
    a genuinely saturated pastel (high S, high L, e.g. a light pink) still
    survives. Images that can't meet the strict bar relax down through the
    steps below rather than returning nothing.
    """

    def apply(bl, wl, ms):
        survivors = [c for c in clusters if bl <= c["L"] <= wl and c["S"] >= ms]
        return _dedupe(survivors, dup_delta_e)

    relax_steps = [
        (black_l, white_l, min_saturation),
        (black_l * 0.5, white_l + (100 - white_l) * 0.5, min_saturation * 0.5),
        (0.0, 100.0, 0.0),
    ]

    result = []
    for bl, wl, ms in relax_steps:
        result = apply(bl, wl, ms)
        if len(result) >= min_needed:
            break

    return result if result else _dedupe(clusters, dup_delta_e)


def select_primary(clusters: list, skip: int = 0) -> dict:
    """Best score, preferring the 30<L<80, S>25% "usable" range when possible.

    `skip` (--shuffle): instead of the top-ranked candidate, take the one
    `skip` positions below it in that same ranked pool -- clamped to the
    last available candidate rather than raising, since the exact pool size
    depends on the image and isn't known by the caller ahead of time (see
    resolve_shuffle_index). Because everything downstream (secondary/auxN/
    the shading ramp) is picked relative to whichever cluster ends up as
    primary, this alone is enough to generalize shading's "discard the
    first N picks" idea to every mode -- no per-mode special-casing needed.
    """
    ranked = sorted(clusters, key=lambda c: c["score"], reverse=True)
    usable = [c for c in ranked if 30 < c["L"] < 80 and c["S"] > 25]
    pool = usable if usable else ranked
    return pool[min(skip, len(pool) - 1)]


def synthesize_shade(color: dict, delta_l: float = 25.0) -> dict:
    """Fallback secondary: a lighter/darker shade of `color`, for when no
    other cluster is far enough in ΔE (near-monochrome images)."""
    lab = np.array(color["lab"], dtype=np.float64)
    new_l = lab[0] - delta_l if lab[0] > 50 else lab[0] + delta_l
    new_l = float(np.clip(new_l, 5, 95))
    return _make_color_entry(np.array([new_l, lab[1], lab[2]]), label="shade-of-primary")


def select_secondary(clusters: list, primary: dict, min_delta_e: float = 25.0,
                      prioritize_contrast: bool = True) -> dict:
    """
    prioritize_contrast=True ("contrast" mode): the cluster that best trades
    off contrast against the primary AND its own quality -- max(ΔE × score),
    the same coverage/saturation/midtone/contrast score used everywhere else,
    NOT raw ΔE. Pure ΔE rewarded the farthest color regardless of what it
    was, so a bright primary got paired with whatever near-black or near-white
    sat farthest away -- a technically-distant but useless secondary. Weighting
    by score keeps the contrast pull while refusing low-chroma extremes when a
    genuinely colorful option is nearly as far. Falls back to a synthesized
    shade if even the chosen best doesn't clear min_delta_e in raw ΔE.

    prioritize_contrast=False ("balanced" mode): just the best-scoring
    remaining cluster, by the same coverage/saturation/midtone/background
    criteria used everywhere else — no bias toward or away from primary. In
    practice this often picks something close to primary (e.g. a near-shade
    that scored well), which matches how a lot of real themes actually use
    "secondary" as primary-but-more-opaque rather than a contrasting accent.
    """
    candidates = [c for c in clusters if c is not primary]
    if not candidates:
        return synthesize_shade(primary)

    if not prioritize_contrast:
        return max(candidates, key=lambda c: c["score"])

    best = max(candidates, key=lambda c: cm.delta_e76(c["lab"], primary["lab"]) * c["score"])
    if cm.delta_e76(best["lab"], primary["lab"]) >= min_delta_e:
        return best
    return synthesize_shade(primary)


def generate_shading_series(primary: dict, n_needed: int, min_l: float = 8.0, max_l: float = 92.0) -> list:
    """"shading" mode: n_needed monochromatic variants of `primary` -- same
    hue/chroma (Lab a, b), only lightness changes -- as a single, monotonic
    ramp toward one end (NOT alternating lighter/darker: that reads as a
    jarring zigzag, and lighter shades of a saturated color look
    washed-out/pastel regardless of chroma, since the sRGB gamut narrows
    sharply near white -- an inherent property of RGB, not a bug). Defaults
    to going darker (stays visually "rich"), unless primary is already dark
    enough that there's meaningfully more room going lighter instead."""
    lab = np.asarray(primary["lab"], dtype=np.float64)
    a, b = float(lab[1]), float(lab[2])
    base_l = float(lab[0])

    room_dark = base_l - min_l
    room_light = max_l - base_l
    target_end = max_l if room_light > room_dark * 1.5 else min_l

    ls = np.linspace(base_l, target_end, n_needed + 1)[1:]
    return [_make_color_entry(np.array([float(l), a, b]), label="shade") for l in ls]


def select_auxiliaries(clusters: list, chosen: list, n_needed: int, exclude: list = None) -> list:
    """
    Greedy farthest-point sampling weighted by each candidate's own score —
    favors colors that are both far from everything already chosen AND
    individually "good", instead of chasing a distant-but-low-quality
    outlier just for being far away.

    `chosen` supplies the reference points to maximize distance from (the
    FINAL, contrast-adjusted colors). `exclude` (defaults to `chosen`) is
    what actually gets removed from the candidate pool — pass the original
    pre-adjustment cluster objects here when `chosen` holds copies made by
    improve_contrast(), so the untouched original doesn't sneak back in as
    an "auxiliary" nearly identical to the primary/secondary it came from.
    """
    exclude = chosen if exclude is None else exclude
    excluded_ids = {id(c) for c in exclude}
    remaining = [c for c in clusters if id(c) not in excluded_ids]
    pool = list(chosen)
    result = []

    while remaining and len(result) < n_needed:
        best, best_value = None, -1.0
        for c in remaining:
            min_dist = min(cm.delta_e76(c["lab"], p["lab"]) for p in pool)
            value = min_dist * c["score"]
            if value > best_value:
                best_value, best = value, c
        result.append(best)
        pool.append(best)
        remaining = [c for c in remaining if c is not best]  # identity-based: dicts hold numpy arrays

    return result
