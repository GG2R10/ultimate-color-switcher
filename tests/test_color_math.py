import numpy as np
import pytest

from backend import color_math as cm


def test_rgb_lab_roundtrip_known_colors():
    for rgb in ([0, 0, 0], [255, 255, 255], [255, 0, 0], [0, 255, 0], [0, 0, 255], [128, 64, 200]):
        rgb = np.array(rgb, dtype=np.float64)
        lab = cm.rgb_to_lab(rgb)
        back = cm.lab_to_rgb(lab)
        assert np.allclose(back, rgb, atol=1.0)


def test_lab_known_reference_values():
    white_lab = cm.rgb_to_lab(np.array([255.0, 255.0, 255.0]))
    black_lab = cm.rgb_to_lab(np.array([0.0, 0.0, 0.0]))
    assert white_lab[0] == pytest.approx(100.0, abs=0.5)
    assert black_lab[0] == pytest.approx(0.0, abs=0.5)


def test_delta_e76_zero_for_identical_colors():
    lab = cm.rgb_to_lab(np.array([100.0, 150.0, 200.0]))
    assert cm.delta_e76(lab, lab) == 0.0


def test_delta_e76_larger_for_more_different_colors():
    black = cm.rgb_to_lab(np.array([0.0, 0.0, 0.0]))
    white = cm.rgb_to_lab(np.array([255.0, 255.0, 255.0]))
    gray = cm.rgb_to_lab(np.array([128.0, 128.0, 128.0]))
    assert cm.delta_e76(black, white) > cm.delta_e76(black, gray)


def test_rgb_to_hsl_known_values():
    hue, sat, light = cm.rgb_to_hsl(np.array([255.0, 0.0, 0.0]))  # pure red
    assert sat == pytest.approx(100.0, abs=1.0)
    assert light == pytest.approx(50.0, abs=1.0)
    assert hue == pytest.approx(0.0, abs=1.0)

    _, sat_gray, light_gray = cm.rgb_to_hsl(np.array([128.0, 128.0, 128.0]))
    assert sat_gray == pytest.approx(0.0, abs=0.5)
    assert light_gray == pytest.approx(50.0, abs=1.0)


def test_hex_rgb_roundtrip():
    rgb = cm.hex_to_rgb("#1a2b3c")
    assert cm.rgb_to_hex(rgb) == "1a2b3c"


def test_contrast_ratio_black_white_is_maximal():
    ratio = cm.contrast_ratio(np.array([0.0, 0.0, 0.0]), np.array([255.0, 255.0, 255.0]))
    assert ratio == pytest.approx(21.0, abs=0.5)


def test_contrast_ratio_identical_colors_is_one():
    ratio = cm.contrast_ratio(np.array([100.0, 100.0, 100.0]), np.array([100.0, 100.0, 100.0]))
    assert ratio == pytest.approx(1.0, abs=0.01)


def test_contrast_ratio_symmetric():
    a = np.array([10.0, 20.0, 30.0])
    b = np.array([200.0, 210.0, 220.0])
    assert cm.contrast_ratio(a, b) == pytest.approx(cm.contrast_ratio(b, a), abs=0.001)


def test_hsl_to_rgb_known_hues():
    # pure red, green, blue at full saturation / 50% lightness
    assert cm.rgb_to_hex(cm.hsl_to_rgb(0, 100, 50)) == "ff0000"
    assert cm.rgb_to_hex(cm.hsl_to_rgb(120, 100, 50)) == "00ff00"
    assert cm.rgb_to_hex(cm.hsl_to_rgb(240, 100, 50)) == "0000ff"


def test_hsl_to_rgb_roundtrips_through_rgb_to_hsl():
    for hue in (0, 45, 90, 180, 270, 359):
        rgb = cm.hsl_to_rgb(hue, 60, 55)
        h2, s2, l2 = cm.rgb_to_hsl(rgb)
        assert h2 == pytest.approx(hue, abs=1.5)
        assert s2 == pytest.approx(60, abs=1.5)
        assert l2 == pytest.approx(55, abs=1.5)


def test_hsl_to_rgb_gray_at_zero_saturation():
    rgb = cm.hsl_to_rgb(200, 0, 50)
    assert np.allclose(rgb, [127.5, 127.5, 127.5], atol=0.5)
