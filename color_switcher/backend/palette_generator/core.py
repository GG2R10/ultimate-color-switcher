#!/usr/bin/env python3
"""
core.py — generate_palette: the full pipeline entrypoint that ties the other
modules together. Sample -> cluster -> score -> select primary, then
secondary/auxiliaries/shading ramp depending on `mode` -> contrast-adjust ->
normalize -> return [{"hex", "label"}, ...] in the shape guiless.apply_palette
already expects.
"""

import numpy as np

from .color_entry import build_clusters, load_image_samples
from .scoring import score_cluster
from .selection import (
    _ACCENT_HUE_STEP,
    _is_monochrome,
    filter_clusters,
    find_background_color,
    generate_shading_series,
    select_auxiliaries,
    select_primary,
    select_secondary,
    synthesize_accent_color,
)
from .transforms import _boost_saturation, _complement_hue, _normalize_extreme_lightness, improve_contrast

_MODES = ("balanced", "contrast", "shading")


def generate_palette(
    image_path: str,
    n_colors: int = 6,
    sample_size: int = 40000,
    seed: int = 42,
    mode: str = "contrast",
    saturate: bool = False,
    weights: dict = None,
    weighted_contrast: bool = True,
    shuffle: int = 0,
    overfetch: int = 0,
    ying_yang: bool = False,
    with_base: bool = False,
) -> list:
    """
    Full pipeline entrypoint.

    mode controls how everything past "primary" is picked:
      - "contrast" (default, original behavior): secondary is chosen and
        adjusted specifically to maximize contrast against primary.
        Auxiliaries are unaffected either way.
      - "balanced": secondary is just the best-scoring remaining cluster
        (same criteria as primary/auxiliaries), with no bias for or against
        being close to primary -- often lands on a primary-but-more-opaque
        color, which is how a lot of real themes actually use "secondary".
      - "shading": primary is picked the same way as always, then every
        other slot is a monochromatic lightness variant of primary (same
        hue/chroma) instead of a different cluster.

    saturate (--my-eyes): boost every FINAL chosen color's saturation right
    before returning, after all selection/contrast logic has already run.

    weights: the coverage/saturation/midtone/contrast fractions score_cluster
    uses to rank clusters -- see resolve_scoring_weights to build this from
    a "default"/"alternative"/"custom" choice. Defaults to
    _SCORING_PRESETS["default"] (via score_cluster) when not given.

    weighted_contrast (--no-weighted-contrast disables it): whether the
    contrast_term is a coverage-weighted average against every cluster
    (True, recommended -- see _weighted_contrast_term) or a single ΔE
    against just the highest-coverage cluster (False, the original
    behavior -- still solid for images with one clear, flat background).

    shuffle (--shuffle): skip this many top-ranked candidates when picking
    primary (see select_primary) -- already a resolved non-negative int
    here, not the CLI/GUI's "next" sentinel (see resolve_shuffle_index,
    which callers use to turn that into this). Since secondary/auxN/the
    shading ramp are all picked relative to whichever cluster ends up as
    primary, this alone shifts the whole palette, not just primary.

    overfetch (--overfetch): keep this many extra candidates around past
    n_colors when filtering clusters, so `shuffle` has more room to skip
    through -- otherwise filter_clusters only guarantees n_colors survivors,
    which leaves shuffle nothing to work with beyond a few positions.

    ying_yang (--ying-yang): flip every final color to its complement (hue
    +180°) right before returning, turning the palette into its complementary
    scheme (see _complement_hue). Applied after all selection/contrast logic,
    so it preserves the palette's internal contrast relationships.

    Returns [{"hex": "rrggbb", "label": "primary"|"secondary"|"auxN"|"shadeN"}, ...]
    — ready for guiless.apply_palette or palette_store.write_palette_csv.
    """
    if n_colors < 1:
        raise ValueError("n_colors must be at least 1")
    if mode not in _MODES:
        raise ValueError(f"mode must be one of {_MODES}, got {mode!r}")

    samples = load_image_samples(image_path, sample_size=sample_size, seed=seed)
    k = min(max(n_colors * 3, n_colors + 4), len(samples))

    clusters = build_clusters(samples, k, seed=seed)
    background = find_background_color(clusters) if clusters else None

    if weighted_contrast:
        score = lambda c: score_cluster(c, all_clusters=clusters, weights=weights)
    else:
        score = lambda c: score_cluster(c, background=background, weights=weights)

    for c in clusters:
        c["score"] = score(c)

    is_monochrome = not clusters or _is_monochrome(clusters)
    filtered = filter_clusters(clusters, min_needed=n_colors + overfetch) or clusters

    if is_monochrome:
        # No real hue anywhere in the image to build a "primary" from --
        # synthesize a saturated accent instead of settling for near-black
        # or near-white. Lean the accent's lightness away from the image's
        # own average so it still reads clearly against it.
        avg_l = float(np.mean([c["L"] for c in clusters])) if clusters else 50.0
        accent_l = 60.0 if avg_l < 50 else 40.0
        # The accent has no hue to inherit from a greyscale image, so it's
        # synthesized: the base hue comes from a seed derived from the image's
        # own pixels (different monochrome wallpapers -> different accents,
        # same wallpaper -> reproducible, which the wallpaper-switch hook
        # relies on), and --shuffle rotates it by the golden angle so it steps
        # through clearly distinct hues rather than near-identical ones.
        image_seed = (int(np.sum(samples)) * 2654435761 + seed * 40503) & 0x7FFFFFFF
        primary_raw = synthesize_accent_color(
            seed=image_seed, hue_offset=shuffle * _ACCENT_HUE_STEP, lightness=accent_l, label="primary",
        )
        primary_raw["score"] = score(primary_raw)
    else:
        primary_raw = select_primary(filtered, skip=shuffle)

    if mode == "shading":
        # Unlike the other modes, primary itself is NOT contrast-adjusted
        # here: improve_contrast() can push lightness all the way to its
        # 3/97 clamp when the natural cluster color sits close to the
        # background, and generate_shading_series then has to build the
        # entire ramp from that already-extreme base -- with few colors
        # requested, the one remaining shade has nowhere to go but the
        # opposite extreme (primary near-black, shade1 near-white). The
        # wide, evenly-spread ramp already gives some shades strong contrast
        # against most backgrounds without needing per-shade correction (see
        # generate_shading_series' call below) -- same reasoning applies to
        # primary, so it's left as the natural cluster color too.
        primary = primary_raw
    else:
        primary = improve_contrast(primary_raw, background) if background is not None else primary_raw

    chosen = [primary]

    if mode == "shading":
        if n_colors > 1:
            chosen.extend(generate_shading_series(primary, n_colors - 1))
    else:
        selection_refs = [primary_raw]

        if n_colors >= 2:
            secondary_raw = select_secondary(filtered, primary_raw, prioritize_contrast=(mode == "contrast"))
            secondary = improve_contrast(secondary_raw, primary) if mode == "contrast" else secondary_raw
            if background is not None:
                secondary = improve_contrast(secondary, background)
            selection_refs.append(secondary_raw)
            chosen.append(secondary)

        if n_colors > len(chosen):
            chosen.extend(
                select_auxiliaries(filtered, chosen, n_colors - len(chosen), exclude=selection_refs)
            )

    chosen = chosen[:n_colors]

    n = len(chosen)
    if mode == "shading":
        labels = ["primary"] + [f"shade{i}" for i in range(1, n)]
    else:
        labels = ["primary"]
        if n >= 2:
            labels.append("secondary")
        labels.extend(f"aux{i}" for i in range(1, n - len(labels) + 1))

    def _finalize(entries):
        # Final safety pass: never emit a pure black/white -- normalize the
        # near-extremes into a usable band (see _normalize_extreme_lightness).
        normed = [_normalize_extreme_lightness(c) for c in entries]
        return [{"hex": c["hex"], "label": labels[i]} for i, c in enumerate(normed)]

    effective = list(chosen)
    if saturate:
        effective = [_boost_saturation(c) for c in effective]
    if ying_yang:
        effective = [_complement_hue(c) for c in effective]
    effective_out = _finalize(effective)

    if not with_base:
        return effective_out
    # with_base: also return the palette WITHOUT the post-modifiers (my-eyes,
    # ying-yang) applied -- the "base" a stored palette keeps so `shift` can
    # toggle those on/off later without regenerating (and reversibly, since
    # they're recomputed from this base rather than mutated in place). saturate
    # and ying-yang both preserve HSL lightness and normalize only touches
    # lightness, so base and effective share the exact same _finalize outcome
    # per color except for the deliberate post-mod difference.
    return effective_out, _finalize(chosen)
