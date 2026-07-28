import numpy as np
import pytest

from color_switcher.backend import color_math as cm
from color_switcher.backend.palette_generator import color_entry as ce
from color_switcher.backend.palette_generator import fgbg_pairing as fp


def _entry(l, a, b, score=1.0, label=None):
    e = ce._make_color_entry(np.array([l, a, b]), label=label)
    e["score"] = score
    return e


# --------------------------------------------------------------------------- #
# _classify_pair
# --------------------------------------------------------------------------- #

def test_classify_pair_light_bg_dark_fg_is_case1():
    assert fp._classify_pair(bg_l=80.0, fg_l=20.0) == "case1"


def test_classify_pair_dark_bg_light_fg_is_case2():
    assert fp._classify_pair(bg_l=20.0, fg_l=80.0) == "case2"


def test_classify_pair_similar_luminance_is_case3():
    assert fp._classify_pair(bg_l=50.0, fg_l=55.0) == "case3"


def test_classify_pair_boundary_is_case3_not_beyond():
    # Exactly at the threshold: not "beyond" it yet, so still case3.
    assert fp._classify_pair(bg_l=50.0, fg_l=50.0 + fp._CASE_L_THRESHOLD) == "case2"
    assert fp._classify_pair(bg_l=50.0, fg_l=50.0 + fp._CASE_L_THRESHOLD - 0.01) == "case3"


# --------------------------------------------------------------------------- #
# _pair_score / _best_available_pair
# --------------------------------------------------------------------------- #

def test_pair_score_weighs_delta_e_and_both_scores():
    a = _entry(50, 40, 0, score=2.0)
    b = _entry(50, -40, 0, score=3.0)
    expected = cm.delta_e76(a["lab"], b["lab"]) * 2.0 * 3.0
    assert fp._pair_score(a, b) == pytest.approx(expected)


def test_pair_score_defaults_missing_score_to_neutral():
    a = _entry(50, 40, 0)
    del a["score"]
    b = _entry(50, -40, 0, score=5.0)
    expected = cm.delta_e76(a["lab"], b["lab"]) * 1.0 * 5.0
    assert fp._pair_score(a, b) == pytest.approx(expected)


def test_pair_score_delta_e_only_ignores_score():
    a = _entry(50, 40, 0, score=100.0)
    b = _entry(50, -40, 0, score=100.0)
    assert fp._pair_score(a, b, delta_e_only=True) == pytest.approx(cm.delta_e76(a["lab"], b["lab"]))


def test_best_available_pair_none_when_pool_too_small():
    assert fp._best_available_pair([]) is None
    assert fp._best_available_pair([_entry(50, 0, 0)]) is None


def test_best_available_pair_picks_highest_scoring_pair():
    close_a = _entry(50, 5, 0, score=1.0)
    close_b = _entry(50, 6, 0, score=1.0)
    far_a = _entry(50, 40, 0, score=1.0)
    far_b = _entry(50, -40, 0, score=1.0)
    pool = [close_a, close_b, far_a, far_b]
    best = fp._best_available_pair(pool)
    assert set(id(c) for c in best) == {id(far_a), id(far_b)}


# --------------------------------------------------------------------------- #
# _designate_roles
# --------------------------------------------------------------------------- #

def test_designate_roles_case1_lighter_is_background():
    light = _entry(80, 0, 0)
    dark = _entry(20, 0, 0)
    bg, fg = fp._designate_roles(dark, light, "case1")
    assert bg is light and fg is dark


def test_designate_roles_case2_darker_is_background():
    light = _entry(80, 0, 0)
    dark = _entry(20, 0, 0)
    bg, fg = fp._designate_roles(light, dark, "case2")
    assert bg is dark and fg is light


def test_designate_roles_case3_tiebreaks_by_saturation():
    hi_s = _entry(50, 40, 0)  # high HSL S
    lo_s = _entry(50, 2, 0)   # low HSL S
    bg, fg = fp._designate_roles(lo_s, hi_s, "case3")
    assert bg is hi_s and fg is lo_s


# --------------------------------------------------------------------------- #
# _target_ls / _nudge_to_target_delta_l
# --------------------------------------------------------------------------- #

def test_target_ls_reproduces_target_delta_when_room_available():
    target_l_bg, target_l_fg = fp._target_ls(bg_l=50.0, fg_l=50.0, target_delta_l=20.0, bg_lighter=True)
    assert target_l_bg - target_l_fg == pytest.approx(20.0)
    assert target_l_bg > target_l_fg


def test_target_ls_respects_bg_lighter_flag():
    bg_l, fg_l = fp._target_ls(bg_l=50.0, fg_l=50.0, target_delta_l=20.0, bg_lighter=False)
    assert fg_l > bg_l


def test_target_ls_clamps_within_safe_band():
    target_l_bg, target_l_fg = fp._target_ls(bg_l=50.0, fg_l=50.0, target_delta_l=1000.0, bg_lighter=True)
    assert fp._PAIR_L_MIN <= target_l_fg <= target_l_bg <= fp._PAIR_L_MAX


def test_target_ls_degrades_gracefully_near_extreme():
    # midpoint pinned near the ceiling: the achieved delta should shrink
    # rather than push either side out of [_PAIR_L_MIN, _PAIR_L_MAX].
    target_l_bg, target_l_fg = fp._target_ls(bg_l=90.0, fg_l=90.0, target_delta_l=40.0, bg_lighter=True)
    assert target_l_bg <= fp._PAIR_L_MAX
    assert target_l_fg >= fp._PAIR_L_MIN


def test_nudge_to_target_delta_l_preserves_hue_chroma():
    bg_lab = np.array([50.0, 30.0, -10.0])
    fg_lab = np.array([50.0, 30.0, -10.0])
    new_bg, new_fg = fp._nudge_to_target_delta_l(bg_lab, fg_lab, target_delta_l=20.0, bg_lighter=True)
    assert new_bg[1] == pytest.approx(bg_lab[1]) and new_bg[2] == pytest.approx(bg_lab[2])
    assert new_fg[1] == pytest.approx(fg_lab[1]) and new_fg[2] == pytest.approx(fg_lab[2])
    assert new_bg[0] - new_fg[0] == pytest.approx(20.0)


# --------------------------------------------------------------------------- #
# _apply_case1_or_2
# --------------------------------------------------------------------------- #

def test_apply_case_eco_off_keeps_each_own_hue():
    bg_designate = _entry(50, 40, 0)   # red-ish
    fg_designate = _entry(50, 0, 40)   # yellow-ish
    new_bg, new_fg = fp._apply_case1_or_2(bg_designate, fg_designate, "case1", target_delta_l=30.0, eco=False)
    assert new_bg["lab"][1] == pytest.approx(bg_designate["lab"][1])
    assert new_bg["lab"][2] == pytest.approx(bg_designate["lab"][2])
    assert new_fg["lab"][1] == pytest.approx(fg_designate["lab"][1])
    assert new_fg["lab"][2] == pytest.approx(fg_designate["lab"][2])
    assert new_bg["lab"][0] > new_fg["lab"][0]  # case1: bg ends up lighter


def test_apply_case_eco_on_forces_same_hue():
    bg_designate = _entry(50, 40, 0, score=1.0)   # more saturated (S higher)
    fg_designate = _entry(50, 2, 0, score=1.0)
    new_bg, new_fg = fp._apply_case1_or_2(bg_designate, fg_designate, "case2", target_delta_l=30.0, eco=True)
    # both share the seed's (bg, more saturated) hue/chroma
    assert new_bg["lab"][1] == pytest.approx(new_fg["lab"][1])
    assert new_bg["lab"][2] == pytest.approx(new_fg["lab"][2])
    assert new_fg["lab"][0] > new_bg["lab"][0]  # case2: fg ends up lighter


def test_apply_case_eco_on_each_role_gets_its_own_target_l_not_the_others():
    # Seed = foreground (higher S) -- background must still land at the
    # BACKGROUND target L, not accidentally inherit the foreground's.
    bg_designate = _entry(20, 2, 0)
    fg_designate = _entry(80, 40, 0)  # higher S, this is the seed
    new_bg, new_fg = fp._apply_case1_or_2(bg_designate, fg_designate, "case1", target_delta_l=40.0, eco=True)
    target_l_bg, target_l_fg = fp._target_ls(bg_designate["lab"][0], fg_designate["lab"][0], 40.0, bg_lighter=True)
    assert new_bg["lab"][0] == pytest.approx(target_l_bg)
    assert new_fg["lab"][0] == pytest.approx(target_l_fg)


# --------------------------------------------------------------------------- #
# _apply_case3
# --------------------------------------------------------------------------- #

def test_apply_case3_anchor_untouched_other_is_complement_and_matches_target_delta():
    anchor = _entry(50, 40, 0)   # higher S
    other = _entry(60, 2, 0)     # lower S, originally LIGHTER than anchor
    new_bg, new_fg = fp._apply_case3(anchor, other, target_delta_l=5.0)
    assert new_bg is anchor  # anchor was the bg_designate here, kept as-is, byte-identical
    # Other's L is nudged to reproduce the ORIGINAL (small) detected delta
    # relative to the anchor's OWN (untouched) L -- not left at its own
    # original L anymore, and not whatever incidental gap the candidate
    # search happened to produce.
    assert new_fg["lab"][0] == pytest.approx(anchor["lab"][0] + 5.0)
    other_hue, other_sat, other_light = cm.rgb_to_hsl(other["rgb"])
    new_hue, new_sat, new_light = cm.rgb_to_hsl(new_fg["rgb"])
    assert new_hue == pytest.approx((other_hue + 180.0) % 360.0, abs=1.0)


def test_apply_case3_anchor_can_be_the_foreground_designate():
    bg_designate = _entry(50, 2, 0)     # lower S, originally DARKER than fg
    fg_designate = _entry(60, 40, 0)    # higher S -- this is the anchor
    new_bg, new_fg = fp._apply_case3(bg_designate, fg_designate, target_delta_l=5.0)
    assert new_fg is fg_designate
    assert new_bg["lab"][0] == pytest.approx(fg_designate["lab"][0] - 5.0)


def test_apply_case3_preserves_which_side_was_originally_lighter():
    anchor = _entry(50, 40, 0)
    other_darker = _entry(40, 2, 0)  # originally DARKER than anchor
    _new_bg, new_fg = fp._apply_case3(anchor, other_darker, target_delta_l=5.0)
    assert new_fg["lab"][0] == pytest.approx(anchor["lab"][0] - 5.0)  # stays darker


def test_apply_case3_clamps_within_safe_band():
    anchor = _entry(90.0, 40, 0)  # near the ceiling
    other = _entry(91.0, 2, 0)    # originally lighter
    _new_bg, new_fg = fp._apply_case3(anchor, other, target_delta_l=50.0)  # way more room than exists
    assert new_fg["lab"][0] <= fp._PAIR_L_MAX


# --------------------------------------------------------------------------- #
# apply_fgbg_pairing (integration)
# --------------------------------------------------------------------------- #

def test_apply_fgbg_pairing_passes_through_untouched_colors():
    a = _entry(50, 40, 0, label="a")
    b = _entry(20, 0, 40, label="b")
    # Low score suppresses any pair involving it regardless of ΔE, so (a, b)
    # -- the pair the test actually wants picked -- wins unambiguously.
    untouched = _entry(70, -20, -20, score=0.001, label="untouched")
    palette = [a, b, untouched]
    pairs = [{"pair_id": "p1", "bg_l": 80.0, "fg_l": 20.0}]

    new_palette = fp.apply_fgbg_pairing(palette, pairs)

    assert len(new_palette) == 3
    assert new_palette[2] is untouched  # never touched, same object, same position
    paired = [c for c in new_palette if c.get("pair_id") == "p1"]
    assert len(paired) == 2
    assert {c["role"] for c in paired} == {"background", "foreground"}


def test_apply_fgbg_pairing_does_not_mutate_input_palette():
    a = _entry(50, 40, 0)
    b = _entry(50, 3, 0)  # low S -> becomes case3's "other", hue rewritten
    palette = [a, b]
    pairs = [{"pair_id": "p1", "bg_l": 50.0, "fg_l": 52.0}]  # small delta -> case3

    fp.apply_fgbg_pairing(palette, pairs)

    assert "role" not in a
    assert "role" not in b


def test_apply_fgbg_pairing_never_reuses_a_color_across_pairs():
    entries = [_entry(50 + i * 5, (i - 2) * 15, 0, label=f"c{i}") for i in range(4)]
    pairs = [
        {"pair_id": "p1", "bg_l": 80.0, "fg_l": 20.0},
        {"pair_id": "p2", "bg_l": 20.0, "fg_l": 80.0},
    ]
    new_palette = fp.apply_fgbg_pairing(entries, pairs)
    assert sum(1 for c in new_palette if c.get("pair_id") == "p1") == 2
    assert sum(1 for c in new_palette if c.get("pair_id") == "p2") == 2
    # every original color got consumed by exactly one pair, none shared
    assert all(c.get("pair_id") is not None for c in new_palette)


def test_apply_fgbg_pairing_stops_gracefully_when_pool_runs_out():
    entries = [_entry(50, 40, 0), _entry(20, -40, 0)]  # only 1 pair's worth
    pairs = [
        {"pair_id": "p1", "bg_l": 80.0, "fg_l": 20.0},
        {"pair_id": "p2", "bg_l": 80.0, "fg_l": 20.0},  # nothing left for this one
    ]
    new_palette = fp.apply_fgbg_pairing(entries, pairs)
    assert sum(1 for c in new_palette if c.get("pair_id") == "p1") == 2
    assert sum(1 for c in new_palette if c.get("pair_id") == "p2") == 0  # best-effort, not an error
    assert len(new_palette) == 2


def test_apply_fgbg_pairing_empty_pairs_is_a_no_op():
    entries = [_entry(50, 40, 0), _entry(20, -40, 0)]
    new_palette = fp.apply_fgbg_pairing(entries, [])
    assert new_palette == entries
