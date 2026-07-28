#!/usr/bin/env python3
"""
palette_generator — Generate a color palette from an image (wallpaper),
producing exactly the shape guiless.apply_palette already expects.

Split into focused modules; this package re-exports every name so callers and
tests keep using ``palette_generator.<name>`` unchanged:
  - settings   : persisted generation defaults + shuffle "next" anchor
  - color_entry: pixel sampling, Lab clustering, the color-entry dict
  - scoring    : per-cluster score, weight presets, weight resolution
  - selection  : background/monochrome detection, filtering, role selection
  - transforms : contrast/saturation/lightness post-processing of chosen colors
  - core       : generate_palette, the pipeline entrypoint
"""

from .settings import (
    DEFAULT_GENERATION_SETTINGS,
    _GENERATION_SETTINGS_KEY,
    read_generation_settings,
    resolve_shuffle_index,
    write_generation_settings,
)
from .color_entry import (
    ImageLoadError,
    _make_color_entry,
    build_clusters,
    load_image_samples,
)
from .scoring import (
    _SCORING_MODES,
    _SCORING_PRESETS,
    _SCORING_WEIGHT_KEYS,
    _midtone_term,
    _saturation_term,
    _smoothstep,
    _weighted_contrast_term,
    percentages_to_weights,
    resolve_scoring_weights,
    score_cluster,
)
from .selection import (
    _ACCENT_HUE_STEP,
    _MONOCHROME_SATURATION_THRESHOLD,
    _MONOCHROME_TRUSTWORTHY_L,
    _SHADING_DIRECTIONS,
    _dedupe,
    _is_monochrome,
    filter_clusters,
    find_background_color,
    generate_shading_series,
    select_auxiliaries,
    select_primary,
    select_secondary,
    synthesize_accent_color,
    synthesize_shade,
)
from .transforms import (
    _BACKGROUND_SAFE_MAX_RATIO,
    _EXTREME_L_MAX,
    _EXTREME_L_MIN,
    _FOREGROUND_SAFE_MIN_RATIO,
    _MY_EYES_CHROMA_FACTOR,
    _MY_EYES_CHROMA_MAX,
    _assign_roles,
    _boost_saturation,
    _complement_hue,
    _normalize_extreme_lightness,
    _reduce_contrast,
    apply_post_modifiers,
    improve_contrast,
)
from .core import _MODES, generate_palette
