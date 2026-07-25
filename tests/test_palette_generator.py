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


def test_filter_drops_low_saturation_greys_even_at_midtone():
    # A high-coverage grey at a perfectly fine lightness is still no good as a
    # palette color -- the strict saturation floor should cut it (Fix B), even
    # though it dominates the image, keeping only the saturated one.
    saturated = _entry(50, 40, 0, count=10)
    grey = _entry(50, 2, 0, count=60)
    result = pg.filter_clusters([saturated, grey], min_needed=1)
    assert len(result) == 1
    assert result[0]["S"] > 18


def test_select_primary_prefers_usable_range():
    unusable_but_higher_score = _entry(95, 0, 0, count=100, total=100)  # very light, desaturated
    unusable_but_higher_score["score"] = 0.9
    usable = _entry(50, 40, 0, count=10, total=100)
    usable["score"] = 0.5
    result = pg.select_primary([unusable_but_higher_score, usable])
    assert result is usable


def test_select_primary_skip_takes_next_ranked_candidate():
    a = _entry(50, 40, 0, count=10, total=100)
    a["score"] = 0.9
    b = _entry(55, 35, 5, count=10, total=100)
    b["score"] = 0.7
    c = _entry(45, 45, -5, count=10, total=100)
    c["score"] = 0.5
    assert pg.select_primary([a, b, c]) is a
    assert pg.select_primary([a, b, c], skip=1) is b
    assert pg.select_primary([a, b, c], skip=2) is c


def test_select_primary_skip_clamps_to_last_candidate():
    a = _entry(50, 40, 0, count=10, total=100)
    a["score"] = 0.9
    b = _entry(55, 35, 5, count=10, total=100)
    b["score"] = 0.7
    assert pg.select_primary([a, b], skip=5) is b


def test_select_secondary_prefers_farthest_delta_e():
    primary = _entry(50, 40, 0)
    close = _entry(52, 41, 1)
    far = _entry(20, -40, 30)
    for c in (primary, close, far):
        c["score"] = pg.score_cluster(c)
    result = pg.select_secondary([primary, close, far], primary)
    assert result is far


def test_select_secondary_contrast_weights_by_score_not_raw_delta_e():
    # A near-black is the farthest cluster in raw ΔE from a light primary, but
    # it's a low-quality color; a saturated mid-tone sits a bit closer yet is
    # far better. Fix A weights ΔE by score, so the saturated one must win --
    # under the old pure-ΔE rule the near-black would have been picked.
    primary = _entry(80, 0, 0)
    near_black_far = _entry(5, 0, 0)
    saturated_closer = _entry(60, 50, 30)
    near_black_far["score"] = 0.30
    saturated_closer["score"] = 0.70
    assert (cm.delta_e76(near_black_far["lab"], primary["lab"])
            > cm.delta_e76(saturated_closer["lab"], primary["lab"]))  # near-black really is farther
    result = pg.select_secondary([primary, near_black_far, saturated_closer], primary)
    assert result is saturated_closer


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


def test_generate_palette_shuffle_changes_primary_pick(tmp_path):
    # _make_test_image has 4 distinctly-hued saturated patches, several of
    # which qualify as "usable" -- overfetch keeps enough of them around for
    # shuffle to have real candidates to skip to.
    image_path = tmp_path / "wallpaper.png"
    _make_test_image(image_path)

    baseline = pg.generate_palette(str(image_path), n_colors=1, sample_size=5000, overfetch=3)
    shuffled = pg.generate_palette(str(image_path), n_colors=1, sample_size=5000, overfetch=3, shuffle=1)

    assert baseline[0]["hex"] != shuffled[0]["hex"]


def test_generate_palette_shuffle_cascades_to_shading_ramp(tmp_path):
    # Same mechanism (select_primary's skip), no shading-specific code path
    # -- shuffle=1 should pick a different primary hue, so the whole ramp
    # (built from that primary's own hue/chroma) comes out different too.
    image_path = tmp_path / "wallpaper.png"
    _make_test_image(image_path)

    baseline = pg.generate_palette(str(image_path), n_colors=3, sample_size=5000, mode="shading", overfetch=3)
    shuffled = pg.generate_palette(
        str(image_path), n_colors=3, sample_size=5000, mode="shading", overfetch=3, shuffle=1,
    )

    assert baseline[0]["hex"] != shuffled[0]["hex"]


def test_generate_palette_shuffle_beyond_pool_clamps_instead_of_crashing(tmp_path):
    image_path = tmp_path / "wallpaper.png"
    _make_test_image(image_path)
    palette = pg.generate_palette(str(image_path), n_colors=1, sample_size=5000, overfetch=1, shuffle=50)
    assert len(palette) == 1
    int(palette[0]["hex"], 16)  # still a valid color, not a crash


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


def test_complement_hue_rotates_180_keeps_sat_and_lightness():
    color = pg._make_color_entry(cm.rgb_to_lab(np.array([200.0, 90.0, 60.0])))  # warm orange
    hue_before, sat_before, light_before = cm.rgb_to_hsl(color["rgb"])

    flipped = pg._complement_hue(color)
    hue_after, sat_after, light_after = cm.rgb_to_hsl(flipped["rgb"])

    expected_hue = (hue_before + 180.0) % 360.0
    diff = abs(hue_after - expected_hue)
    assert min(diff, 360.0 - diff) < 2.0            # ~180° around the wheel
    assert sat_after == pytest.approx(sat_before, abs=2.0)
    assert light_after == pytest.approx(light_before, abs=2.0)


def test_generate_palette_ying_yang_flips_every_color_to_its_complement(tmp_path):
    image_path = tmp_path / "wallpaper.png"
    _make_test_image(image_path)

    normal = pg.generate_palette(str(image_path), n_colors=4, sample_size=5000, ying_yang=False)
    flipped = pg.generate_palette(str(image_path), n_colors=4, sample_size=5000, ying_yang=True)

    assert len(normal) == len(flipped) == 4
    for n, f in zip(normal, flipped):
        h_n, _s, _l = cm.rgb_to_hsl(cm.hex_to_rgb(n["hex"]))
        h_f, _s, _l = cm.rgb_to_hsl(cm.hex_to_rgb(f["hex"]))
        diff = abs(h_f - (h_n + 180.0) % 360.0)
        assert min(diff, 360.0 - diff) < 12.0  # each role flipped to its complement (loose: gamut round-trip drift)


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


def _make_similar_lightness_image(path):
    # Background and subject are close in Lab lightness (~7pt apart) but
    # different in hue/saturation -- like a real photo's midtones, unlike
    # _make_moderate_saturation_image's dark-vs-medium (already high
    # contrast) pairing. Reproduces the bug where shading mode's primary
    # got contrast-adjusted toward an extreme (since reaching contrast
    # ratio 3.0 against a similarly-light background takes a large L push),
    # and with few colors requested the remaining shade had nowhere to go
    # but the opposite extreme -- see generate_palette's mode=="shading"
    # branch.
    img = Image.new("RGB", (60, 60), (90, 90, 90))
    pixels = img.load()
    for x in range(15, 45):
        for y in range(15, 45):
            pixels[x, y] = (200, 40, 90)
    img.save(path)


def test_generate_palette_shading_mode_two_colors_not_pushed_to_extremes(tmp_path):
    image_path = tmp_path / "wallpaper.png"
    _make_similar_lightness_image(image_path)

    palette = pg.generate_palette(str(image_path), n_colors=2, sample_size=5000, mode="shading")

    assert len(palette) == 2
    from color_switcher.backend import color_math as cm

    lab_ls = [cm.rgb_to_lab(cm.hex_to_rgb(c["hex"]))[0] for c in palette]
    for l in lab_ls:
        assert 8.0 < l < 92.0  # neither color slammed into the ramp's floor/ceiling
    assert abs(lab_ls[0] - lab_ls[1]) < 50.0  # no jarring primary-vs-shade jump


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


def test_weighted_contrast_matches_single_background_when_one_cluster_dominates():
    # A dominant cluster's coverage should pull the weighted average close
    # to "just compare against it", reproducing the old single-background
    # behavior when there really is one clear background. Uses contrast-heavy
    # weights so this exercises the weighted-contrast MECHANISM specifically,
    # not whatever balance the current presets happen to strike between
    # contrast and coverage.
    contrast_heavy = {"coverage": 0.1, "saturation": 0.1, "midtone": 0.1, "contrast": 0.7}
    background = _entry(45, 50, 20, count=90, total=100)
    tiny_other = _entry(60, -30, 10, count=10, total=100)
    same_as_background = _entry(46, 51, 21, count=80, total=100)
    distinct = _entry(45, -50, -20, count=10, total=100)

    all_clusters = [background, tiny_other, same_as_background, distinct]
    score_same = pg.score_cluster(same_as_background, all_clusters=all_clusters, weights=contrast_heavy)
    score_distinct = pg.score_cluster(distinct, all_clusters=all_clusters, weights=contrast_heavy)
    assert score_distinct > score_same


def test_weighted_contrast_spreads_across_clusters_when_no_background_dominates():
    # Three similarly-sized clusters (no single dominant "background") --
    # a candidate close to just ONE of them should still score reasonably,
    # since the pull toward "must contrast" is now spread across all three
    # instead of anchored to one arbitrary highest-coverage pick.
    a = _entry(45, 50, 20, count=34, total=100)
    b = _entry(45, -50, 20, count=33, total=100)
    close_to_a = _entry(46, 51, 21, count=10, total=100)

    all_clusters = [a, b, close_to_a]
    weighted = pg._weighted_contrast_term(close_to_a, all_clusters)
    single_vs_a = min(cm.delta_e76(close_to_a["lab"], a["lab"]) / 60.0, 1.0)
    # averaging in cluster b's distance should push the term higher than
    # comparing against a (its closest neighbor) alone would
    assert weighted > single_vs_a


def test_weighted_contrast_term_sums_to_convex_combination_bounded_by_60():
    # Sanity check on the normalization claim: since Σcoverage == 1 across
    # the full cluster set, the weighted term can never exceed what the
    # /60.0 clip already handles, no matter how many clusters are summed.
    many_clusters = [_entry(l, 40, 0, count=1, total=20) for l in range(5, 95, 5)]
    for c in many_clusters:
        term = pg._weighted_contrast_term(c, many_clusters)
        assert 0.0 <= term <= 1.0


def test_saturation_term_is_steep_low_chroma_gets_almost_nothing():
    # A near-grey (low S) should score ~0 on saturation, a vivid color ~1 --
    # the steep curve (Fix C), unlike the old linear S/100 that gave S=20 a
    # full 0.20.
    assert pg._saturation_term(10) < 0.05
    assert pg._saturation_term(20) < 0.2
    assert pg._saturation_term(70) > 0.95
    assert pg._saturation_term(45) > pg._saturation_term(25)  # monotonic increasing


def test_midtone_term_plateaus_in_band_and_drops_at_extremes():
    assert pg._midtone_term(50) == pytest.approx(1.0)      # comfortable middle
    assert pg._midtone_term(45) == pytest.approx(1.0)      # still in the plateau
    assert pg._midtone_term(8) < 0.05                      # near-black
    assert pg._midtone_term(95) < 0.05                     # near-white
    assert pg._midtone_term(25) < pg._midtone_term(40)     # falls off toward dark


def test_normalize_extreme_lightness_pulls_pure_black_and_white_inward():
    white = pg._make_color_entry(cm.rgb_to_lab(np.array([255.0, 255.0, 255.0])), label="x")
    black = pg._make_color_entry(cm.rgb_to_lab(np.array([1.0, 1.0, 1.0])), label="x")
    nw = pg._normalize_extreme_lightness(white)
    nb = pg._normalize_extreme_lightness(black)
    assert nw["hex"] != "ffffff" and nw["L"] <= 90.5
    assert nb["hex"] != "010101" and nb["L"] >= 9.5


def test_normalize_extreme_lightness_leaves_in_band_colors_untouched():
    mid = pg._make_color_entry(cm.rgb_to_lab(np.array([120.0, 90.0, 60.0])), label="x")
    result = pg._normalize_extreme_lightness(mid)
    assert result is mid  # in-band -> returned as-is, not a rebuilt copy


def test_generate_palette_never_emits_pure_black_or_white(tmp_path):
    # A grayscale image asking for many colors used to surface #ffffff/#010101
    # as farthest-point auxiliaries; the normalization pass must keep every
    # final color inside the usable lightness band.
    image_path = tmp_path / "grey.png"
    _make_grayscale_image(image_path)
    palette = pg.generate_palette(str(image_path), n_colors=10, sample_size=5000)
    for entry in palette:
        h = entry["hex"]
        assert h not in ("ffffff", "000000", "010101")
        _hue, _s, light = cm.rgb_to_hsl(cm.hex_to_rgb(h))
        assert 9.0 <= light <= 91.0


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


def test_is_monochrome_ignores_extreme_lightness_saturation_noise():
    # Greyscale midtones plus a near-black cluster whose HSL saturation is
    # spuriously high (the JPEG-noise artifact) must still read as monochrome:
    # the noise sits outside the trustworthy lightness band and is ignored.
    greys = [_entry(l, 1, 0) for l in (30, 50, 70)]
    noise = pg._make_color_entry(cm.rgb_to_lab(np.array([12.0, 0.0, 2.0])))
    assert noise["S"] > 8 and noise["L"] < 8  # sanity: high fake S at an extreme-dark L
    assert pg._is_monochrome(greys + [noise])


def test_is_monochrome_false_when_real_midtone_hue_present():
    greys = [_entry(l, 1, 0) for l in (30, 70)]
    real_color = _entry(50, 40, 10)  # genuinely saturated at a trustworthy lightness
    assert real_color["S"] > 8
    assert not pg._is_monochrome(greys + [real_color])


def test_monochrome_accent_differs_between_images(tmp_path):
    # Two different greyscale wallpapers must NOT get the same synthesized
    # accent -- the old fixed seed handed every greyscale image one identical
    # color regardless of content.
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    _make_grayscale_image(a, dark_fraction=0.30)
    _make_grayscale_image(b, dark_fraction=0.45)  # same accent_l side, different pixels
    pa = pg.generate_palette(str(a), n_colors=2, sample_size=3000)
    pb = pg.generate_palette(str(b), n_colors=2, sample_size=3000)
    assert pa[0]["hex"] != pb[0]["hex"]


def test_monochrome_accent_is_reproducible_for_same_image(tmp_path):
    img = tmp_path / "a.png"
    _make_grayscale_image(img, dark_fraction=0.30)
    p1 = pg.generate_palette(str(img), n_colors=2, sample_size=3000)
    p2 = pg.generate_palette(str(img), n_colors=2, sample_size=3000)
    assert p1[0]["hex"] == p2[0]["hex"]  # same wallpaper -> same accent (hook relies on this)


def test_monochrome_accent_cycles_with_shuffle(tmp_path):
    img = tmp_path / "a.png"
    _make_grayscale_image(img, dark_fraction=0.30)
    p0 = pg.generate_palette(str(img), n_colors=2, sample_size=3000, shuffle=0)
    p1 = pg.generate_palette(str(img), n_colors=2, sample_size=3000, shuffle=1)
    assert p0[0]["hex"] != p1[0]["hex"]  # shuffle now varies the monochrome accent too


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
    # read_generation_settings merges onto DEFAULT_GENERATION_SETTINGS, so
    # keys not passed to write_generation_settings (scoring, custom_percentages)
    # come back filled in with their defaults.
    assert pg.read_generation_settings(config) == {
        "mode": "shading",
        "saturate": True,
        "scoring": "default",
        "custom_percentages": None,
        "weighted_contrast": True,
        "ying_yang": False,
        "shuffle_enabled": False,
        "shuffle_mode": "manual",
        "shuffle_value": 0,
        "overfetch": 0,
        "last_shuffle": -1,
    }


def test_write_generation_settings_preserves_other_config_keys(fake_project):
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()
    pg.write_generation_settings(config, {"mode": "balanced", "saturate": False})

    import json
    with open(config.project_dir + "/config.json") as f:
        raw = json.load(f)
    assert raw["files_to_replace"] == fake_project.files


def test_scoring_presets_add_up_to_one():
    for name, weights in pg._SCORING_PRESETS.items():
        assert sum(weights.values()) == pytest.approx(1.0), name


def test_percentages_to_weights_converts_and_normalizes():
    weights = pg.percentages_to_weights(
        {"coverage": 20, "saturation": 40, "midtone": 30, "contrast": 10}
    )
    assert weights == {"coverage": 0.20, "saturation": 0.40, "midtone": 0.30, "contrast": 0.10}


def test_percentages_to_weights_rejects_wrong_sum():
    with pytest.raises(ValueError):
        pg.percentages_to_weights({"coverage": 25, "saturation": 25, "midtone": 25, "contrast": 30})


def test_percentages_to_weights_rejects_missing_key():
    with pytest.raises(ValueError):
        pg.percentages_to_weights({"coverage": 40, "saturation": 40, "midtone": 20})


def test_percentages_to_weights_rejects_unknown_key():
    with pytest.raises(ValueError):
        pg.percentages_to_weights(
            {"coverage": 20, "saturation": 30, "midtone": 30, "contrast": 10, "extra": 10}
        )


def test_percentages_to_weights_rejects_negative():
    with pytest.raises(ValueError):
        pg.percentages_to_weights({"coverage": -10, "saturation": 50, "midtone": 30, "contrast": 30})


def test_resolve_scoring_weights_default_and_alternative_presets():
    assert pg.resolve_scoring_weights("default") == pg._SCORING_PRESETS["default"]
    assert pg.resolve_scoring_weights("alternative") == pg._SCORING_PRESETS["alternative"]


def test_resolve_scoring_weights_rejects_unknown_scoring_name():
    with pytest.raises(ValueError):
        pg.resolve_scoring_weights("nonsense")


def test_resolve_scoring_weights_custom_prefers_explicit_over_config(fake_project):
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()
    pg.write_generation_settings(config, {
        "mode": "contrast", "saturate": False, "scoring": "custom",
        "custom_percentages": {"coverage": 10, "saturation": 10, "midtone": 10, "contrast": 70},
    })

    explicit = {"coverage": 20, "saturation": 40, "midtone": 30, "contrast": 10}
    weights = pg.resolve_scoring_weights("custom", custom_percentages=explicit, config=config)
    assert weights == {"coverage": 0.20, "saturation": 0.40, "midtone": 0.30, "contrast": 0.10}


def test_resolve_scoring_weights_custom_falls_back_to_config_when_not_given(fake_project):
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()
    pg.write_generation_settings(config, {
        "mode": "contrast", "saturate": False, "scoring": "custom",
        "custom_percentages": {"coverage": 10, "saturation": 10, "midtone": 10, "contrast": 70},
    })

    weights = pg.resolve_scoring_weights("custom", config=config)
    assert weights == {"coverage": 0.10, "saturation": 0.10, "midtone": 0.10, "contrast": 0.70}


def test_resolve_scoring_weights_custom_falls_back_to_default_when_nothing_configured(fake_project):
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()
    assert pg.resolve_scoring_weights("custom", config=config) == pg._SCORING_PRESETS["default"]
    assert pg.resolve_scoring_weights("custom") == pg._SCORING_PRESETS["default"]


def test_resolve_scoring_weights_custom_still_raises_on_invalid_stored_values(fake_project):
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()
    pg.write_generation_settings(config, {
        "mode": "contrast", "saturate": False, "scoring": "custom",
        "custom_percentages": {"coverage": 10, "saturation": 10, "midtone": 10, "contrast": 10},  # sums to 40
    })
    with pytest.raises(ValueError):
        pg.resolve_scoring_weights("custom", config=config)


def test_resolve_shuffle_index_manual_value_used_as_is_and_persisted(fake_project):
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()

    resolved = pg.resolve_shuffle_index(3, n_colors=4, overfetch=2, config=config)

    assert resolved == 3
    assert pg.read_generation_settings(config)["last_shuffle"] == 3


def test_resolve_shuffle_index_next_starts_at_zero_with_no_prior_anchor(fake_project):
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()

    assert pg.resolve_shuffle_index("next", n_colors=4, overfetch=2, config=config) == 0


def test_resolve_shuffle_index_next_continues_from_last_anchor(fake_project):
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()
    pg.resolve_shuffle_index(2, n_colors=4, overfetch=2, config=config)  # anchor=2, pool_bound=6

    assert pg.resolve_shuffle_index("next", n_colors=4, overfetch=2, config=config) == 3


def test_resolve_shuffle_index_next_wraps_around_pool_bound(fake_project):
    # pool_bound = n_colors + overfetch = 6 -- an anchor at the last index
    # (5) should wrap back to 0 instead of getting stuck at a clamp.
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()
    pg.resolve_shuffle_index(5, n_colors=4, overfetch=2, config=config)

    assert pg.resolve_shuffle_index("next", n_colors=4, overfetch=2, config=config) == 0


def test_resolve_shuffle_index_without_config_does_not_persist():
    # no config to read/write an anchor from -- "next" always starts at 0.
    assert pg.resolve_shuffle_index("next", n_colors=4, overfetch=2, config=None) == 0
    assert pg.resolve_shuffle_index("next", n_colors=4, overfetch=2, config=None) == 0


def test_resolve_shuffle_index_rejects_negative():
    with pytest.raises(ValueError):
        pg.resolve_shuffle_index(-1, n_colors=4, overfetch=0, config=None)


def test_score_cluster_accepts_custom_weights():
    # "default" leans harder into contrast and less into coverage than
    # "alternative" does -- a candidate that's far from the background but
    # low-coverage should be favored more under "default" than "alternative".
    background = _entry(50, 0, 0, count=50, total=100)
    high_contrast_low_coverage = _entry(50, 80, 0, count=5, total=100)
    low_contrast_high_coverage = _entry(50, 5, 0, count=40, total=100)

    default_weights = pg._SCORING_PRESETS["default"]
    alt_weights = pg._SCORING_PRESETS["alternative"]

    default_gap = (pg.score_cluster(high_contrast_low_coverage, background=background, weights=default_weights)
                   - pg.score_cluster(low_contrast_high_coverage, background=background, weights=default_weights))
    alt_gap = (pg.score_cluster(high_contrast_low_coverage, background=background, weights=alt_weights)
               - pg.score_cluster(low_contrast_high_coverage, background=background, weights=alt_weights))
    assert default_gap > alt_gap


def _spy_on_score_cluster(monkeypatch):
    """Records the (background, all_clusters) kwargs generate_palette's
    internal `score` closure passes to score_cluster on every call, without
    changing its behavior."""
    calls = []
    original = pg.score_cluster

    def spy(c, background=None, all_clusters=None, weights=None):
        calls.append((background, all_clusters))
        return original(c, background=background, all_clusters=all_clusters, weights=weights)

    # generate_palette lives in pg.core and calls the score_cluster imported
    # into THAT module's namespace, so patch the name there -- patching the
    # package-level re-export (pg.score_cluster) wouldn't be seen by core.
    monkeypatch.setattr(pg.core, "score_cluster", spy)
    return calls


def test_generate_palette_weighted_contrast_true_scores_against_all_clusters(tmp_path, monkeypatch):
    image_path = tmp_path / "wallpaper.png"
    _make_moderate_saturation_image(image_path)
    calls = _spy_on_score_cluster(monkeypatch)

    pg.generate_palette(str(image_path), n_colors=2, sample_size=3000, weighted_contrast=True)

    assert calls  # sanity: the spy actually intercepted calls
    assert all(all_clusters is not None for _background, all_clusters in calls)
    assert all(background is None for background, _all_clusters in calls)


def test_generate_palette_weighted_contrast_false_scores_against_single_background(tmp_path, monkeypatch):
    image_path = tmp_path / "wallpaper.png"
    _make_moderate_saturation_image(image_path)
    calls = _spy_on_score_cluster(monkeypatch)

    pg.generate_palette(str(image_path), n_colors=2, sample_size=3000, weighted_contrast=False)

    assert calls
    assert all(background is not None for background, _all_clusters in calls)
    assert all(all_clusters is None for _background, all_clusters in calls)
