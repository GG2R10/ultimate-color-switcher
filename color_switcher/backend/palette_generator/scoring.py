#!/usr/bin/env python3
"""
scoring.py — Rank a cluster by how good a palette color it is: steep
saturation/midtone terms + a coverage term + a contrast term, combined with
per-term weights. Also the weight presets and the "default"/"alternative"/
"custom" -> weights resolution.
"""

from .. import color_math as cm
from .settings import read_generation_settings

_SCORING_WEIGHT_KEYS = ("coverage", "saturation", "midtone", "contrast")

_SCORING_PRESETS = {
    "default": {"coverage": 0.20, "saturation": 0.30, "midtone": 0.25, "contrast": 0.25},
    "alternative": {"coverage": 0.30, "saturation": 0.30, "midtone": 0.30, "contrast": 0.10},
}
_SCORING_MODES = ("default", "alternative", "custom")


def percentages_to_weights(percentages: dict) -> dict:
    """Validate a {coverage, saturation, midtone, contrast} percentages dict
    (0-100, human-friendly units used in config.json/CLI/GUI) and convert it
    to the 0-1 fractions score_cluster's weights expect. Raises ValueError
    (missing/unknown keys, non-numeric, negative, or not adding up to 100)
    -- callers decide whether that should surface as a CLI error, a GUI
    toast, or a fallback (see resolve_scoring_weights)."""
    keys = set(percentages)
    missing = [k for k in _SCORING_WEIGHT_KEYS if k not in keys]
    if missing:
        raise ValueError(f"missing percentages: {', '.join(missing)}")
    unknown = keys - set(_SCORING_WEIGHT_KEYS)
    if unknown:
        raise ValueError(f"unknown keys: {', '.join(sorted(unknown))}")

    for k in _SCORING_WEIGHT_KEYS:
        try:
            percentages[k] = float(percentages[k])
        except (TypeError, ValueError):
            raise ValueError(f"{k!r} is not a number: {percentages[k]!r}")
        if percentages[k] < 0:
            raise ValueError(f"{k!r} can't be negative: {percentages[k]!r}")

    total = sum(percentages[k] for k in _SCORING_WEIGHT_KEYS)
    if abs(total - 100.0) > 0.5:
        raise ValueError(f"percentages must add up to 100 (they add up to {total:g})")

    return {k: percentages[k] / 100.0 for k in _SCORING_WEIGHT_KEYS}


def resolve_scoring_weights(scoring: str, custom_percentages: dict = None, config=None) -> dict:
    """Turn a `scoring` choice ("default"/"alternative"/"custom") into the
    0-1 weights dict score_cluster needs.

    "custom" needs percentages from somewhere: `custom_percentages` if given
    (e.g. --custom-scoring-values on the CLI, taking priority since the
    caller passed them explicitly), otherwise `config`'s persisted
    palette_generation.custom_percentages. If custom was requested but
    ended up with nothing configured anywhere, falls back to "default"
    silently -- this can run unattended (the wallpaper-switch hook calls
    `ucs automatic` in the background), so a missing value shouldn't hard
    crash it. Percentages that WERE found but are invalid (bad keys, don't
    add up to 100) still raise -- that's a real mistake, not an absent
    setting.
    """
    if scoring not in _SCORING_MODES:
        raise ValueError(f"scoring must be one of {_SCORING_MODES}, got {scoring!r}")
    if scoring != "custom":
        return _SCORING_PRESETS[scoring]

    if custom_percentages is None and config is not None:
        custom_percentages = read_generation_settings(config).get("custom_percentages")
    if not custom_percentages:
        return _SCORING_PRESETS["default"]

    return percentages_to_weights(dict(custom_percentages))


def _smoothstep(edge0: float, edge1: float, x: float) -> float:
    """Classic smoothstep: 0 below edge0, 1 above edge1, an S-curve between.
    edge0 > edge1 is allowed (a falling ramp), so the same helper builds both
    sides of the midtone window."""
    t = (x - edge0) / (edge1 - edge0)
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def _saturation_term(s: float) -> float:
    """Reward chroma steeply: ~0 below S≈15 (a near-grey is worthless as a
    palette color), rising fast to full credit by S≈60. Replaces the old
    linear S/100, which gave washed-out colors a misleadingly large share of
    the score."""
    return _smoothstep(15.0, 60.0, s)


def _midtone_term(light: float) -> float:
    """Reward an acceptable lightness with a plateau, not a single sweet spot:
    full credit across the comfortable L∈[38,68] band, falling steeply to 0 at
    the near-black (L≈15) and near-white (L≈90) ends. The old linear
    1-|L-50|/50 still handed extreme lights/darks ~0.16, enough to sneak them
    in on a high midtone weight."""
    return min(_smoothstep(15.0, 38.0, light), _smoothstep(90.0, 68.0, light))


def score_cluster(c: dict, background: dict = None, all_clusters: list = None, weights: dict = None) -> float:
    """
    score = weights["coverage"]*coverage + weights["saturation"]*saturation_term
          + weights["midtone"]*midtone_term + weights["contrast"]*contrast_term

    saturation_term / midtone_term are steep (see _saturation_term /
    _midtone_term): low-chroma and near-black/near-white colors contribute
    almost nothing regardless of their weight, so a color has to earn its
    place with real chroma at a usable lightness.

    weights defaults to _SCORING_PRESETS["default"] -- see resolve_scoring_weights
    for how a "default"/"alternative"/"custom" choice becomes this dict.

    contrast_term rewards being visually distinct from the image's overall
    composition. Without it, a saturated, high-coverage color that IS the
    background wins on raw coverage/saturation alone -- producing a
    "primary" that's nearly identical to whatever it's meant to sit on top
    of.

    Two ways to compute it, depending on what's known:
      - `all_clusters` given (default, `weighted_contrast=True` in
        generate_palette): a coverage-weighted average of ΔE against every
        cluster in the image (see _weighted_contrast_term) -- a cluster
        that dominates the image pulls this average toward "must contrast
        with IT specifically", while an image with no single dominant
        region spreads that pull across several clusters instead of
        anchoring to an arbitrary one.
      - `background` given instead (`--no-weighted-contrast`): a single ΔE
        against the single highest-coverage cluster only -- the old
        behavior, still useful for images with one clear, solid-color
        background where the extra averaging isn't needed.
      - neither given: contrast_term is a constant (doesn't affect ranking).
    """
    w = weights or _SCORING_PRESETS["default"]
    coverage_term = c["coverage"]
    saturation_term = _saturation_term(c["S"])
    midtone_term = _midtone_term(c["L"])

    if all_clusters:
        contrast_term = _weighted_contrast_term(c, all_clusters)
    elif background is not None:
        contrast_term = min(cm.delta_e76(c["lab"], background["lab"]) / 60.0, 1.0)
    else:
        contrast_term = 1.0

    return (w["coverage"] * coverage_term + w["saturation"] * saturation_term
            + w["midtone"] * midtone_term + w["contrast"] * contrast_term)


def _weighted_contrast_term(c: dict, all_clusters: list) -> float:
    """Coverage-weighted mean ΔE of `c` against every cluster in
    `all_clusters` (including `c` itself, if present -- its own ΔE=0 term
    just dilutes the average by its own weight, which is fine: a cluster
    that IS most of the image being "not very distinct from the image
    overall" is a sensible signal, not a bug).

    Each cluster's `coverage` is count/total from the SAME build_clusters()
    call, so Σcoverage == 1 across the full set -- this is a genuine
    weighted average (a convex combination), bounded to the same [0, max
    individual ΔE] range as a single-background comparison. It can't blow
    up numerically, so the existing /60.0 normalization still applies
    as-is.
    """
    total = sum(cl["coverage"] * cm.delta_e76(c["lab"], cl["lab"]) for cl in all_clusters)
    return min(total / 60.0, 1.0)
