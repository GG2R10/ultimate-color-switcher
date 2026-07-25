#!/usr/bin/env python3
"""
color_entry.py — Turn image pixels into the "color entry" dicts the rest of
the pipeline passes around: sample pixels (no resize, to avoid
inventing/erasing colors), cluster them in Lab space, and describe each
cluster as {lab, rgb, hex, count, coverage, L, S, label}.
"""

import os

import numpy as np
from PIL import Image, UnidentifiedImageError

from .. import color_math as cm
from ..kmeans import kmeans as _kmeans


class ImageLoadError(ValueError):
    """Raised when the given path can't be opened as an image (missing, a
    directory, or not a valid image file). User-facing: carries a clean
    Spanish message the CLI/GUI can show verbatim instead of a traceback."""


def load_image_samples(image_path: str, sample_size: int = 40000, seed: int = 42):
    """Random sample of the image's visible pixels, as an (n, 3) float64 RGB array."""
    if not os.path.exists(image_path):
        raise ImageLoadError(f"No existe la imagen: {image_path}")
    if os.path.isdir(image_path):
        raise ImageLoadError(f"La ruta es un directorio, no una imagen: {image_path}")
    try:
        img = Image.open(image_path).convert("RGBA")
    except (UnidentifiedImageError, OSError) as e:
        raise ImageLoadError(
            f"No se pudo abrir la imagen {image_path}: ¿es un archivo de imagen válido?"
        ) from e
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
