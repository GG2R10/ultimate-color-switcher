#!/usr/bin/env python3
"""
color_math.py — Color space conversions and perceptual distance, implemented
directly with numpy (no scikit-learn / colormath dependency).

RGB <-> XYZ <-> Lab (D65 reference white, standard sRGB companding), RGB <->
HSL, CIE76 Delta E, and WCAG relative luminance / contrast ratio.

All functions are vectorized: they accept arrays of shape (..., 3) and
operate elementwise over the leading dimensions, so the same functions work
on a single color or a whole array of sampled pixels.
"""

import numpy as np

_D65 = (0.95047, 1.0, 1.08883)

_RGB_TO_XYZ_MATRIX = np.array([
    [0.4124564, 0.3575761, 0.1804375],
    [0.2126729, 0.7151522, 0.0721750],
    [0.0193339, 0.1191920, 0.9503041],
])

_XYZ_TO_RGB_MATRIX = np.array([
    [3.2404542, -1.5371385, -0.4985314],
    [-0.9692660, 1.8760108, 0.0415560],
    [0.0556434, -0.2040259, 1.0572252],
])


def _srgb_to_linear(c):
    c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * (c ** (1 / 2.4)) - 0.055)


def rgb_to_xyz(rgb):
    """rgb: array(..., 3), values 0-255. Returns array(..., 3) in [0,1]-ish XYZ."""
    rgb = np.asarray(rgb, dtype=np.float64)
    linear = _srgb_to_linear(rgb)
    return linear @ _RGB_TO_XYZ_MATRIX.T


def xyz_to_rgb(xyz):
    """Returns array(..., 3) clipped to 0-255."""
    xyz = np.asarray(xyz, dtype=np.float64)
    linear = xyz @ _XYZ_TO_RGB_MATRIX.T
    srgb = _linear_to_srgb(linear)
    return np.clip(srgb * 255.0, 0, 255)


def _f(t):
    delta = 6 / 29
    return np.where(t > delta ** 3, np.cbrt(t), t / (3 * delta ** 2) + 4 / 29)


def _f_inv(t):
    delta = 6 / 29
    return np.where(t > delta, t ** 3, 3 * delta ** 2 * (t - 4 / 29))


def xyz_to_lab(xyz):
    xn, yn, zn = _D65
    xyz = np.asarray(xyz, dtype=np.float64)
    fx = _f(xyz[..., 0] / xn)
    fy = _f(xyz[..., 1] / yn)
    fz = _f(xyz[..., 2] / zn)
    L = 116 * fy - 16
    a = 500 * (fx - fy)
    b = 200 * (fy - fz)
    return np.stack([L, a, b], axis=-1)


def lab_to_xyz(lab):
    xn, yn, zn = _D65
    lab = np.asarray(lab, dtype=np.float64)
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    fy = (L + 16) / 116
    fx = fy + a / 500
    fz = fy - b / 200
    x = xn * _f_inv(fx)
    y = yn * _f_inv(fy)
    z = zn * _f_inv(fz)
    return np.stack([x, y, z], axis=-1)


def rgb_to_lab(rgb):
    """rgb: array(..., 3), values 0-255. Returns Lab (L in 0-100, a/b roughly -128..127)."""
    return xyz_to_lab(rgb_to_xyz(rgb))


def lab_to_rgb(lab):
    """Returns array(..., 3), values 0-255 (clipped — not every Lab point is displayable)."""
    return xyz_to_rgb(lab_to_xyz(lab))


def delta_e76(lab1, lab2):
    """CIE76 Delta E: plain Euclidean distance in Lab space. Good enough for
    dedup/diversity purposes (not print-industry color matching)."""
    lab1 = np.asarray(lab1, dtype=np.float64)
    lab2 = np.asarray(lab2, dtype=np.float64)
    return np.linalg.norm(lab1 - lab2, axis=-1)


def rgb_to_hsl(rgb):
    """rgb: array(..., 3), values 0-255. Returns (hue_deg, saturation_pct, lightness_pct)."""
    rgb = np.asarray(rgb, dtype=np.float64) / 255.0
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    maxc = np.maximum(np.maximum(r, g), b)
    minc = np.minimum(np.minimum(r, g), b)
    light = (maxc + minc) / 2
    delta = maxc - minc

    sat = np.where(delta < 1e-9, 0.0, delta / (1 - np.abs(2 * light - 1) + 1e-12))

    with np.errstate(divide="ignore", invalid="ignore"):
        hue = np.select(
            [delta < 1e-9, maxc == r, maxc == g, maxc == b],
            [
                np.zeros_like(delta),
                (60 * (((g - b) / delta) % 6)),
                (60 * (((b - r) / delta) + 2)),
                (60 * (((r - g) / delta) + 4)),
            ],
        )
    return hue, sat * 100, light * 100


def hsl_to_rgb(hue: float, saturation: float, lightness: float):
    """hue: degrees (0-360), saturation/lightness: percent (0-100). Returns
    an array(3,) RGB, 0-255. Scalar helper (used to synthesize a single
    accent color) — not vectorized like the rest of this module."""
    h = hue % 360
    s = saturation / 100
    l = lightness / 100
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = l - c / 2

    if h < 60:
        r, g, b = c, x, 0.0
    elif h < 120:
        r, g, b = x, c, 0.0
    elif h < 180:
        r, g, b = 0.0, c, x
    elif h < 240:
        r, g, b = 0.0, x, c
    elif h < 300:
        r, g, b = x, 0.0, c
    else:
        r, g, b = c, 0.0, x

    return np.array([(r + m) * 255, (g + m) * 255, (b + m) * 255])


def hex_to_rgb(hex_value: str):
    hex_value = hex_value.lstrip("#").lower()
    return np.array([int(hex_value[0:2], 16), int(hex_value[2:4], 16), int(hex_value[4:6], 16)], dtype=np.float64)


def rgb_to_hex(rgb) -> str:
    r, g, b = (int(round(float(c))) for c in np.clip(rgb, 0, 255))
    return f"{r:02x}{g:02x}{b:02x}"


def relative_luminance(rgb) -> float:
    """WCAG relative luminance from sRGB (0-255)."""
    rgb = np.asarray(rgb, dtype=np.float64) / 255.0

    def channel(c):
        return np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)

    c = channel(rgb)
    return float(0.2126 * c[..., 0] + 0.7152 * c[..., 1] + 0.0722 * c[..., 2])


def contrast_ratio(rgb1, rgb2) -> float:
    """WCAG contrast ratio, always >= 1 (1 = identical luminance)."""
    l1, l2 = relative_luminance(rgb1), relative_luminance(rgb2)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)
