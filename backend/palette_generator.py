#!/usr/bin/env python3
"""
palette_generator.py — Generate a color palette from an image (wallpaper),
producing exactly the shape guiless.apply_palette already expects.

Pipeline: sample pixels (no resize, to avoid inventing/erasing colors) ->
cluster in Lab space, over-clustered then filtered (kmeans.py) -> score each
surviving cluster -> select primary, then secondary/auxiliaries/shading
ramp depending on `mode` -> contrast-adjust against an optional background
-> return [{"hex", "label"}, ...]. See each function's docstring for the
"why" behind its step; generate_palette ties them together.
"""

import numpy as np
from PIL import Image

from . import color_math as cm
from . import config as config_module
from .kmeans import kmeans as _kmeans

_GENERATION_SETTINGS_KEY = "palette_generation"
DEFAULT_GENERATION_SETTINGS = {"mode": "contrast", "saturate": False}


def read_generation_settings(config) -> dict:
    """Persisted defaults for the GUI's "Generar paleta desde imagen…"
    (mode, saturate). Falls back to DEFAULT_GENERATION_SETTINGS (not
    persisted) when config.json has no palette_generation key."""
    stored = config_module.read_config_json(config).get(_GENERATION_SETTINGS_KEY)
    if stored:
        merged = dict(DEFAULT_GENERATION_SETTINGS)
        merged.update(stored)
        return merged
    return dict(DEFAULT_GENERATION_SETTINGS)


def write_generation_settings(config, settings: dict) -> None:
    """Persist palette-generation defaults into config.json, preserving every other key."""
    raw = config_module.read_config_json(config)
    raw[_GENERATION_SETTINGS_KEY] = settings
    config_module.write_config_json(config, raw)


def load_image_samples(image_path: str, sample_size: int = 40000, seed: int = 42):
    """Random sample of the image's visible pixels, as an (n, 3) float64 RGB array."""
    img = Image.open(image_path).convert("RGBA")
    pixels = np.asarray(img).reshape(-1, 4)

    visible = pixels[pixels[:, 3] > 0]
    if visible.size == 0:
        visible = pixels
    rgb = visible[:, :3].astype(np.float64)

    rng = np.random.default_rng(seed)
    n = rgb.shape[0]
    if n > sample_size:
        idx = rng.choice(n, size=sample_size, replace=False)
        rgb = rgb[idx]
    return rgb


def _make_color_entry(lab, count=0, total=1, label=None):
    lab = np.asarray(lab, dtype=np.float64)
    rgb = np.clip(cm.lab_to_rgb(lab), 0, 255)
    _hue, sat, light = cm.rgb_to_hsl(rgb)
    return {
        "lab": lab,
        "rgb": rgb,
        "hex": cm.rgb_to_hex(rgb),
        "count": int(count),
        "coverage": count / total if total else 0.0,
        "L": float(light),
        "S": float(sat),
        "label": label,  # informational only — the returned palette's label is positional
    }


def build_clusters(rgb_samples, k: int, seed: int = 42) -> list:
    """Cluster sampled RGB pixels (in Lab space); one entry per non-empty cluster."""
    lab_samples = cm.rgb_to_lab(rgb_samples)
    centers, labels = _kmeans(lab_samples, k, seed=seed)

    total = len(labels)
    clusters = []
    for i in range(k):
        count = int((labels == i).sum())
        if count == 0:
            continue
        clusters.append(_make_color_entry(centers[i], count=count, total=total))
    return clusters


_MONOCHROME_SATURATION_THRESHOLD = 8.0  # below this, treat the image as having no real hue to draw from


def find_background_color(clusters: list) -> dict:
    """The single highest-coverage cluster from the FULL (unfiltered)
    cluster set — used as a contrast reference, not necessarily itself a
    candidate for the palette."""
    return max(clusters, key=lambda c: c["coverage"])


def score_cluster(c: dict, background: dict = None) -> float:
    """
    score = 0.25*coverage + 0.35*(S/100) + 0.15*midtone_term + 0.25*contrast_term

    contrast_term rewards being visually distinct from `background` (the
    image's dominant color, or an explicit background_hex). Without it, a
    saturated, high-coverage color that IS the background wins on raw
    coverage/saturation alone — producing a "primary" that's nearly
    identical to whatever it's meant to sit on top of. If no background is
    known, contrast_term is a constant (doesn't affect relative ranking).
    """
    coverage_term = c["coverage"]
    saturation_term = c["S"] / 100
    midtone_term = 1 - abs(c["L"] - 50) / 50

    if background is not None:
        contrast_term = min(cm.delta_e76(c["lab"], background["lab"]) / 60.0, 1.0)
    else:
        contrast_term = 1.0

    return 0.25 * coverage_term + 0.35 * saturation_term + 0.15 * midtone_term + 0.25 * contrast_term


def synthesize_accent_color(seed: int = 42, saturation: float = 55.0, lightness: float = 55.0,
                             label: str = "accent") -> dict:
    """
    A saturated color for near-monochrome images, where no real cluster has
    meaningful hue to offer (see _MONOCHROME_SATURATION_THRESHOLD). The hue
    is picked from `seed` — reproducible per image/seed rather than
    different on every run, but effectively "a random saturated color" as
    far as any single generation is concerned.
    """
    rng = np.random.default_rng(seed)
    hue = float(rng.uniform(0, 360))
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
    black_l: float = 5.0,
    white_l: float = 97.0,
    min_saturation: float = 4.0,
    dup_delta_e: float = 5.0,
) -> list:
    """
    Drop extreme blacks/whites, very desaturated colors, and near-duplicates.
    If the strict thresholds leave fewer than min_needed clusters (e.g. a
    near-monochrome wallpaper), progressively relax them instead of
    returning too few colors to build a palette from.
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


def select_primary(clusters: list) -> dict:
    """Best score, preferring the 30<L<80, S>25% "usable" range when possible."""
    ranked = sorted(clusters, key=lambda c: c["score"], reverse=True)
    usable = [c for c in ranked if 30 < c["L"] < 80 and c["S"] > 25]
    return usable[0] if usable else ranked[0]


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
    prioritize_contrast=True ("contrast" mode): farthest ΔE from the primary
    (best contrast); falls back to a synthesized shade if nothing clears
    min_delta_e.

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

    best = max(candidates, key=lambda c: cm.delta_e76(c["lab"], primary["lab"]))
    if cm.delta_e76(best["lab"], primary["lab"]) >= min_delta_e:
        return best
    return synthesize_shade(primary)


def generate_shading_series(primary: dict, n_needed: int, step: float = 28.0,
                             min_l: float = 8.0, max_l: float = 92.0) -> list:
    """"shading" mode: n_needed monochromatic variants of `primary` -- same
    hue/chroma (Lab a, b), only lightness changes, like "the same color but
    more/less opaque". Walks outward from primary's lightness alternating
    lighter/darker (+step, -step, +2*step, -2*step, ...), clipped to
    [min_l, max_l] and skipping anything too close to a shade already picked
    or to primary itself."""
    lab = np.asarray(primary["lab"], dtype=np.float64)
    a, b = float(lab[1]), float(lab[2])
    base_l = float(lab[0])

    shades = []
    seen_ls = []
    direction = 1
    magnitude = step
    while len(shades) < n_needed and magnitude < 200:
        target_l = float(np.clip(base_l + direction * magnitude, min_l, max_l))
        if abs(target_l - base_l) > 3.0 and all(abs(target_l - s) > 3.0 for s in seen_ls):
            seen_ls.append(target_l)
            shades.append(_make_color_entry(np.array([target_l, a, b]), label="shade"))
        if direction == 1:
            direction = -1
        else:
            direction = 1
            magnitude += step

    return shades


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


_MODES = ("balanced", "contrast", "shading")

_MY_EYES_SATURATION_BOOST = 38.0  # HSL saturation points added, clamped to 100


def _boost_saturation(color: dict, amount: float = _MY_EYES_SATURATION_BOOST) -> dict:
    """--my-eyes: bump a final color's HSL saturation by `amount` points
    (clamped to 100), keeping hue and lightness as-is."""
    hue, sat, light = cm.rgb_to_hsl(color["rgb"])
    new_sat = min(sat + amount, 100.0)
    rgb = cm.hsl_to_rgb(hue, new_sat, light)
    return _make_color_entry(cm.rgb_to_lab(rgb), label=color.get("label"))


def generate_palette(
    image_path: str,
    n_colors: int = 6,
    sample_size: int = 40000,
    seed: int = 42,
    background_hex: str = None,
    mode: str = "contrast",
    saturate: bool = False,
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

    if background_hex:
        background = _make_color_entry(cm.rgb_to_lab(cm.hex_to_rgb(background_hex)), label="background")
    else:
        background = find_background_color(clusters) if clusters else None

    for c in clusters:
        c["score"] = score_cluster(c, background)

    is_monochrome = not clusters or max(c["S"] for c in clusters) < _MONOCHROME_SATURATION_THRESHOLD
    filtered = filter_clusters(clusters, min_needed=n_colors) or clusters

    if is_monochrome:
        # No real hue anywhere in the image to build a "primary" from --
        # synthesize a saturated accent instead of settling for near-black
        # or near-white. Lean the accent's lightness away from the image's
        # own average so it still reads clearly against it.
        avg_l = float(np.mean([c["L"] for c in clusters])) if clusters else 50.0
        accent_l = 60.0 if avg_l < 50 else 40.0
        primary_raw = synthesize_accent_color(seed=seed, lightness=accent_l, label="primary")
        primary_raw["score"] = score_cluster(primary_raw, background)
    else:
        primary_raw = select_primary(filtered)

    primary = improve_contrast(primary_raw, background) if background is not None else primary_raw

    chosen = [primary]

    if mode == "shading":
        if n_colors > 1:
            # NOT background-contrast-adjusted individually: every shade
            # shares primary's exact hue/chroma, so nudging each one toward
            # the same background independently can walk several of them to
            # the SAME lightness (whichever value first clears min_ratio),
            # collapsing the deliberate spread into duplicates. The wide,
            # evenly-spread ramp already gives some shades strong contrast
            # against most backgrounds without needing per-shade correction.
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

    if saturate:
        chosen = [_boost_saturation(c) for c in chosen]

    if mode == "shading":
        labels = ["primary"] + [f"shade{i}" for i in range(1, len(chosen))]
    else:
        labels = ["primary"]
        if len(chosen) >= 2:
            labels.append("secondary")
        labels.extend(f"aux{i}" for i in range(1, len(chosen) - len(labels) + 1))

    return [{"hex": c["hex"], "label": labels[i]} for i, c in enumerate(chosen)]
