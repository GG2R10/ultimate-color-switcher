import numpy as np
import pytest
from PIL import Image

from color_switcher.backend import color_math as cm
from color_switcher.backend import palette_generator as pg


def _entry(l, a, b, count=10, total=100, label=None):
    return pg._make_color_entry(np.array([l, a, b]), count=count, total=total, label=label)


def test_score_prefers_coverage_saturation_and_midtone():
    high = _entry(50, 40, 0, count=50, total=100)  # midtone, saturated, high coverage
    low = _entry(2, 0, 0, count=1, total=100)  # near-black, desaturated, tiny coverage
    assert pg.score_cluster(high) > pg.score_cluster(low)


def test_score_midtone_beats_extreme_lightness_all_else_equal():
    # Lab coordinates derived from real (in-gamut) RGB colors, same hue family,
    # so only lightness differs -- hand-picked Lab tuples can be unachievable
    # in sRGB and get distorted by gamut clipping when converted back.
    mid = pg._make_color_entry(cm.rgb_to_lab(np.array([180.0, 60.0, 60.0])), count=10, total=100)
    dark = pg._make_color_entry(cm.rgb_to_lab(np.array([40.0, 10.0, 10.0])), count=10, total=100)
    assert pg.score_cluster(mid) > pg.score_cluster(dark)


def test_filter_drops_extremes_and_dedupes():
    clusters = [
        _entry(2, 0, 0),  # too dark
        _entry(99, 0, 0),  # too light
        _entry(50, 40, 0),  # fine
        _entry(50, 41, 1, count=5),  # near-duplicate of the previous (low coverage)
    ]
    result = pg.filter_clusters(clusters, min_needed=1)
    assert len(result) == 1
    assert result[0]["count"] == 10  # kept the higher-coverage member of the duplicate pair


def test_filter_relaxes_when_image_is_near_monochrome():
    # every cluster is desaturated -- strict thresholds would drop them all.
    # Spread across L so they're distinct colors, not near-duplicates of each other.
    clusters = [_entry(l, 1, 0, count=10) for l in (20, 35, 50, 65, 80)]
    result = pg.filter_clusters(clusters, min_needed=3)
    assert len(result) >= 3


def test_select_primary_prefers_usable_range():
    unusable_but_higher_score = _entry(95, 0, 0, count=100, total=100)  # very light, desaturated
    unusable_but_higher_score["score"] = 0.9
    usable = _entry(50, 40, 0, count=10, total=100)
    usable["score"] = 0.5
    result = pg.select_primary([unusable_but_higher_score, usable])
    assert result is usable


def test_select_secondary_prefers_farthest_delta_e():
    primary = _entry(50, 40, 0)
    close = _entry(52, 41, 1)
    far = _entry(20, -40, 30)
    for c in (primary, close, far):
        c["score"] = pg.score_cluster(c)
    result = pg.select_secondary([primary, close, far], primary)
    assert result is far


def test_select_secondary_falls_back_to_synthesized_shade_when_all_too_close():
    primary = _entry(50, 40, 0)
    close = _entry(51, 41, 1)
    for c in (primary, close):
        c["score"] = pg.score_cluster(c)
    result = pg.select_secondary([primary, close], primary, min_delta_e=25.0)
    assert result["label"] == "shade-of-primary"
    assert result["hex"] != primary["hex"]


def test_select_secondary_balanced_ignores_distance_from_primary():
    """balanced mode (prioritize_contrast=False): pick by score alone, even
    if that means something CLOSE to primary beats something farther away."""
    primary = _entry(50, 40, 0)
    close_but_high_score = _entry(52, 41, 1, count=90, total=100)  # near-duplicate hue, high coverage
    far_but_low_score = _entry(20, -40, 30, count=1, total=100)  # very different, but tiny coverage
    for c in (primary, close_but_high_score, far_but_low_score):
        c["score"] = pg.score_cluster(c)
    result = pg.select_secondary([primary, close_but_high_score, far_but_low_score], primary,
                                  prioritize_contrast=False)
    assert result is close_but_high_score


def test_generate_shading_series_keeps_same_hue_varies_lightness():
    primary = _entry(50, 40, -10)
    shades = pg.generate_shading_series(primary, 3)
    assert len(shades) == 3
    for s in shades:
        assert s["lab"][1] == pytest.approx(40, abs=0.01)
        assert s["lab"][2] == pytest.approx(-10, abs=0.01)
        assert abs(s["lab"][0] - 50) > 3.0
    # all distinct lightness values
    ls = [round(s["lab"][0], 1) for s in shades]
    assert len(set(ls)) == len(ls)


def test_generate_shading_series_is_monotonic_not_alternating():
    """Regression: shades used to alternate lighter/darker every step,
    which read as a jarring zigzag (and the lighter half looked
    washed-out). The ramp must move in one direction only."""
    primary = _entry(50, 40, -10)
    shades = pg.generate_shading_series(primary, 5)
    ls = [s["lab"][0] for s in shades]
    assert ls == sorted(ls) or ls == sorted(ls, reverse=True)


def test_generate_shading_series_prefers_darker_when_room_is_similar():
    primary = _entry(50, 40, -10)  # roughly centered -- similar room either way
    shades = pg.generate_shading_series(primary, 2)
    assert shades[0]["lab"][0] < 50  # goes darker, not lighter


def test_generate_shading_series_prefers_lighter_when_primary_is_already_dark():
    dark_primary = _entry(12, 30, -5)  # little room left to go darker
    shades = pg.generate_shading_series(dark_primary, 2)
    assert shades[0]["lab"][0] > 12  # goes lighter instead


def test_select_auxiliaries_avoids_reusing_already_chosen():
    primary = _entry(50, 0, 0, label="p")
    secondary = _entry(20, 0, 0, label="s")
    candidate = _entry(80, 30, 30, label="c")
    for c in (primary, secondary, candidate):
        c["score"] = pg.score_cluster(c)
    result = pg.select_auxiliaries([primary, secondary, candidate], [primary, secondary], 1)
    assert len(result) == 1
    assert result[0] is candidate


def test_select_auxiliaries_weighs_distance_by_score():
    primary = _entry(50, 0, 0)
    far_but_bad = _entry(5, 0, 0, count=1, total=1000)  # far, but near-black/desaturated -> low score
    closer_but_good = _entry(55, 40, 20, count=500, total=1000)  # closer, but high coverage+saturation
    for c in (primary, far_but_bad, closer_but_good):
        c["score"] = pg.score_cluster(c)
    result = pg.select_auxiliaries([primary, far_but_bad, closer_but_good], [primary], 1)
    assert result[0] is closer_but_good


def test_improve_contrast_increases_ratio_against_background():
    background = _entry(50, 0, 0)  # midtone gray-ish background
    low_contrast = _entry(55, 10, 10)  # close in lightness -> poor contrast against background
    before = cm.contrast_ratio(low_contrast["rgb"], background["rgb"])
    improved = pg.improve_contrast(low_contrast, background, min_ratio=3.0)
    after = cm.contrast_ratio(improved["rgb"], background["rgb"])
    assert after >= before
    assert after >= 3.0 or after > before  # either it clears the bar, or it at least improved


def _make_test_image(path):
    img = Image.new("RGB", (80, 80), (10, 10, 10))
    pixels = img.load()
    # four distinctly-hued saturated patches on a dark background, so a
    # 4-color palette has enough real diversity to draw from.
    patches = [(220, 30, 30), (30, 60, 220), (30, 200, 60), (230, 210, 30)]
    for i, color in enumerate(patches):
        x0 = i * 20
        for x in range(x0, x0 + 20):
            for y in range(0, 80):
                pixels[x, y] = color
    img.save(path)


def test_generate_palette_end_to_end(tmp_path):
    image_path = tmp_path / "wallpaper.png"
    _make_test_image(image_path)

    palette = pg.generate_palette(str(image_path), n_colors=4, sample_size=5000)

    assert len(palette) == 4
    labels = [c["label"] for c in palette]
    assert labels[0] == "primary"
    assert labels[1] == "secondary"
    for entry in palette:
        assert len(entry["hex"]) == 6
        int(entry["hex"], 16)  # valid hex


def test_generate_palette_respects_n_colors_of_one(tmp_path):
    image_path = tmp_path / "wallpaper.png"
    _make_test_image(image_path)
    palette = pg.generate_palette(str(image_path), n_colors=1, sample_size=2000)
    assert len(palette) == 1
    assert palette[0]["label"] == "primary"


def test_generate_palette_rejects_invalid_n_colors(tmp_path):
    image_path = tmp_path / "wallpaper.png"
    _make_test_image(image_path)
    with pytest.raises(ValueError):
        pg.generate_palette(str(image_path), n_colors=0)


def test_generate_palette_rejects_invalid_mode(tmp_path):
    image_path = tmp_path / "wallpaper.png"
    _make_test_image(image_path)
    with pytest.raises(ValueError):
        pg.generate_palette(str(image_path), n_colors=2, mode="nonsense")


def test_boost_saturation_raises_saturation_keeps_hue_and_lightness():
    from color_switcher.backend import color_math as cm

    color = pg._make_color_entry(cm.rgb_to_lab(np.array([120.0, 100.0, 90.0])))  # mildly saturated
    hue_before, sat_before, light_before = cm.rgb_to_hsl(color["rgb"])

    boosted = pg._boost_saturation(color, amount=30.0)
    hue_after, sat_after, light_after = cm.rgb_to_hsl(boosted["rgb"])

    assert sat_after == pytest.approx(min(sat_before + 30.0, 100.0), abs=1.0)
    assert hue_after == pytest.approx(hue_before, abs=1.0)
    assert light_after == pytest.approx(light_before, abs=1.0)


def test_boost_saturation_clamps_at_100():
    from color_switcher.backend import color_math as cm

    color = pg._make_color_entry(cm.rgb_to_lab(np.array([255.0, 0.0, 0.0])))  # already fully saturated
    boosted = pg._boost_saturation(color, amount=30.0)
    _hue, sat, _light = cm.rgb_to_hsl(boosted["rgb"])
    assert sat <= 100.0 + 1e-6


def test_generate_palette_my_eyes_boosts_every_chosen_color(tmp_path):
    image_path = tmp_path / "wallpaper.png"
    _make_test_image(image_path)

    from color_switcher.backend import color_math as cm

    normal = pg.generate_palette(str(image_path), n_colors=4, sample_size=5000, saturate=False)
    boosted = pg.generate_palette(str(image_path), n_colors=4, sample_size=5000, saturate=True)

    assert len(normal) == len(boosted) == 4
    for n, b in zip(normal, boosted):
        _h1, s1, _l1 = cm.rgb_to_hsl(cm.hex_to_rgb(n["hex"]))
        _h2, s2, _l2 = cm.rgb_to_hsl(cm.hex_to_rgb(b["hex"]))
        assert s2 >= s1 - 1e-6  # never LESS saturated


def _make_moderate_saturation_image(path):
    # A moderately saturated (not gamut-extreme) steel-blue patch as the
    # main subject, over a distinct dark neutral background -- unlike
    # _make_test_image's fully-saturated primaries, this hue/chroma survives
    # a wide lightness excursion without severe sRGB gamut clipping, so hue
    # preservation can be checked precisely after the hex round-trip. Two
    # distinct regions (not a flat fill) so background != primary --
    # otherwise improve_contrast tries to push primary away from its own
    # color, walking it to an extreme where gamut clipping distorts hue.
    img = Image.new("RGB", (60, 60), (20, 20, 20))
    pixels = img.load()
    for x in range(15, 45):
        for y in range(15, 45):
            pixels[x, y] = (70, 110, 150)
    img.save(path)


def test_generate_palette_shading_mode_produces_monochromatic_ramp(tmp_path):
    image_path = tmp_path / "wallpaper.png"
    _make_moderate_saturation_image(image_path)

    palette = pg.generate_palette(str(image_path), n_colors=4, sample_size=5000, mode="shading")

    assert len(palette) == 4
    labels = [c["label"] for c in palette]
    assert labels == ["primary", "shade1", "shade2", "shade3"]

    from color_switcher.backend import color_math as cm
    primary_hue, _s, primary_light = cm.rgb_to_hsl(cm.hex_to_rgb(palette[0]["hex"]))
    lightnesses = [primary_light]
    for entry in palette[1:]:
        hue, _s, light = cm.rgb_to_hsl(cm.hex_to_rgb(entry["hex"]))
        assert hue == pytest.approx(primary_hue, abs=15.0)  # same hue family as primary (the wider ramp pushes closer to gamut extremes, so more drift is expected)
        assert abs(light - primary_light) > 2.0  # but a genuinely different lightness
        lightnesses.append(light)
    assert len(set(round(l, 1) for l in lightnesses)) == len(lightnesses)  # all distinct


def test_generate_palette_balanced_mode_can_pick_secondary_close_to_primary(tmp_path):
    """balanced mode shouldn't force secondary away from primary -- build an
    image where the best-scoring non-primary cluster is deliberately close
    in hue to the dominant patch, and confirm balanced mode is willing to
    pick it (unlike contrast mode, which actively avoids it)."""
    from PIL import Image

    img = Image.new("RGB", (80, 80), (10, 10, 10))
    pixels = img.load()
    # a big, saturated, high-coverage red patch (will win as primary), plus a
    # SLIGHTLY different (still saturated, still high-coverage) red patch
    # right next to it -- close in hue/lightness to primary.
    for x in range(80):
        for y in range(40):
            pixels[x, y] = (220, 30, 30)
    for x in range(80):
        for y in range(40, 80):
            pixels[x, y] = (235, 55, 45)
    path = image_path = tmp_path / "close_hue.png"
    img.save(path)

    balanced = pg.generate_palette(str(path), n_colors=2, sample_size=8000, mode="balanced")
    contrast = pg.generate_palette(str(path), n_colors=2, sample_size=8000, mode="contrast")

    from color_switcher.backend import color_math as cm
    def delta_e(hex_a, hex_b):
        return cm.delta_e76(cm.rgb_to_lab(cm.hex_to_rgb(hex_a)), cm.rgb_to_lab(cm.hex_to_rgb(hex_b)))

    balanced_gap = delta_e(balanced[0]["hex"], balanced[1]["hex"])
    contrast_gap = delta_e(contrast[0]["hex"], contrast[1]["hex"])
    assert balanced_gap < contrast_gap


def test_find_background_color_picks_highest_coverage():
    small = _entry(50, 20, 20, count=5, total=100)
    big = _entry(30, -10, -10, count=90, total=100)
    assert pg.find_background_color([small, big]) is big


def test_score_with_background_penalizes_color_close_to_background():
    background = _entry(45, 50, 20, count=80, total=100)  # a big, saturated red-ish background
    same_as_background = _entry(46, 51, 21, count=80, total=100)  # basically the same color
    distinct = _entry(45, -50, -20, count=10, total=100)  # equally saturated, but far away

    score_same = pg.score_cluster(same_as_background, background)
    score_distinct = pg.score_cluster(distinct, background)
    # without a background reference, "same_as_background" would win on raw
    # coverage alone -- with it, the low-contrast candidate should lose out
    # to the smaller-but-distinct one.
    assert score_distinct > score_same


def test_score_without_background_is_unaffected_by_the_new_term():
    # background=None must reproduce the original (pre-contrast-term) ranking
    high = _entry(50, 40, 0, count=50, total=100)
    low = _entry(2, 0, 0, count=1, total=100)
    assert pg.score_cluster(high) > pg.score_cluster(low)


def test_synthesize_accent_color_is_saturated():
    accent = pg.synthesize_accent_color(seed=1)
    assert accent["S"] > 30
    assert accent["label"] == "accent"


def test_synthesize_accent_color_deterministic_per_seed():
    a = pg.synthesize_accent_color(seed=7)
    b = pg.synthesize_accent_color(seed=7)
    c = pg.synthesize_accent_color(seed=8)
    assert a["hex"] == b["hex"]
    assert a["hex"] != c["hex"]


def _make_grayscale_image(path, dark_fraction=0.5):
    img = Image.new("RGB", (60, 60))
    pixels = img.load()
    split = int(60 * dark_fraction)
    for x in range(60):
        for y in range(60):
            v = 5 if x < split else 250
            pixels[x, y] = (v, v, v)
    img.save(path)


def test_generate_palette_synthesizes_accent_for_monochrome_image(tmp_path):
    image_path = tmp_path / "bw.png"
    _make_grayscale_image(image_path)

    palette = pg.generate_palette(str(image_path), n_colors=2, sample_size=3000)

    assert len(palette) == 2
    for entry in palette[:1]:  # primary must be a real, saturated accent, not near-black/near-white
        _hue, sat, _light = cm.rgb_to_hsl(cm.hex_to_rgb(entry["hex"]))
        assert sat > 30


def _make_dominant_background_image(path):
    # A large saturated red background with one small, distinctly different
    # saturated patch -- the naive (no background-awareness) scorer would
    # pick the background itself as "primary" since it wins on coverage.
    img = Image.new("RGB", (60, 60), (210, 30, 30))
    pixels = img.load()
    for x in range(0, 10):
        for y in range(0, 10):
            pixels[x, y] = (30, 200, 210)
    img.save(path)


def test_generate_palette_primary_is_not_the_dominant_background(tmp_path):
    image_path = tmp_path / "dominant_bg.png"
    _make_dominant_background_image(image_path)

    palette = pg.generate_palette(str(image_path), n_colors=2, sample_size=8000)
    primary_rgb = cm.hex_to_rgb(palette[0]["hex"])
    background_rgb = np.array([210.0, 30.0, 30.0])

    assert cm.delta_e76(cm.rgb_to_lab(primary_rgb), cm.rgb_to_lab(background_rgb)) > 20


def test_read_generation_settings_returns_defaults_when_key_missing(fake_project):
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()
    settings = pg.read_generation_settings(config)
    assert settings == pg.DEFAULT_GENERATION_SETTINGS
    assert settings is not pg.DEFAULT_GENERATION_SETTINGS  # copy, not the shared module dict


def test_write_then_read_generation_settings_roundtrip(fake_project):
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()
    pg.write_generation_settings(config, {"mode": "shading", "saturate": True})
    assert pg.read_generation_settings(config) == {"mode": "shading", "saturate": True}


def test_write_generation_settings_preserves_other_config_keys(fake_project):
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()
    pg.write_generation_settings(config, {"mode": "balanced", "saturate": False})

    import json
    with open(config.project_dir + "/config.json") as f:
        raw = json.load(f)
    assert raw["files_to_replace"] == fake_project.files
