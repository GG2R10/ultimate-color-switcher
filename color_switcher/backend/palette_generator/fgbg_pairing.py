#!/usr/bin/env python3
"""
fgbg_pairing.py — Reshape an already-generated palette's colors into
explicit foreground/background PAIRS (see [[color-roles-design]] rework),
classified by how the ORIGINAL detected pair's luminance related:
  - "case1": background is light, foreground is dark.
  - "case2": background is dark, foreground is light.
  - "case3": no considerable luminance difference -- contrast has to come
    from hue instead.

Takes and returns plain data -- no knowledge of color_roles.json/detected
colors/mapping, mirroring how compute_role_demand already fed plain ints
into generate_palette. The caller (color_detector.py/palette_shift.py) is
responsible for resolving real detected pairs into the `pairs` shape this
module expects: [{"pair_id", "bg_l", "fg_l"}, ...], bg_l/fg_l being the
ORIGINAL detected colors' CIE Lab L (not HSL) -- Lab L is the space every
other L-based transform in this package already works in (improve_contrast,
_reduce_contrast, generate_shading_series), so classification and the
target-delta nudge below stay consistent with them.
"""

import numpy as np

from .. import color_math as cm
from .color_entry import _make_color_entry
from .transforms import _complement_hue

_CASE_L_THRESHOLD = 12.0  # Lab-L points: below this, two colors read as
# "about the same lightness" to a viewer, so hue has to carry the contrast
# instead of luminance (case3). A tunable, documented constant rather than a
# derived one -- "looks about equally light" is a perceptual judgment call,
# same spirit as _MONOCHROME_SATURATION_THRESHOLD elsewhere in this package.

# Safe Lab-L band for the target-delta nudge below -- mirrors
# generate_shading_series's own min_luminance/max_luminance defaults (a Lab-L
# ramp already has to stay clear of literal 0/100), NOT transforms.py's
# _EXTREME_L_MIN/_EXTREME_L_MAX (those are HSL-scoped, used only by
# _normalize_extreme_lightness -- a different lightness space).
_PAIR_L_MIN = 8.0
_PAIR_L_MAX = 92.0


def _classify_pair(bg_l: float, fg_l: float, threshold: float = _CASE_L_THRESHOLD) -> str:
    """Which of the 3 cases a detected bg/fg pair's ORIGINAL luminance
    relationship falls into (see module docstring)."""
    delta = fg_l - bg_l
    if delta <= -threshold:
        return "case1"
    if delta >= threshold:
        return "case2"
    return "case3"


def _pair_score(a: dict, b: dict, delta_e_only: bool = False) -> float:
    """Pairwise "how good a bg/fg pair would these two make" score --
    generalizes the already-proven ΔE×score pattern from select_secondary
    (selection.py) to a symmetric pairwise form. `a`/`b` may not carry a
    ".score" (final chosen colors after improve_contrast/similar don't
    always keep the cluster's original score) -- treated as neutral (1.0)
    when absent, so ΔE alone still drives the ranking rather than crashing.

    delta_e_only=True (case3's re-scoring pass, per the user's spec) drops
    each candidate's own quality term entirely, weighing purely on how far
    apart the two are in color."""
    delta_e = cm.delta_e76(a["lab"], b["lab"])
    if delta_e_only:
        return float(delta_e)
    return float(delta_e * a.get("score", 1.0) * b.get("score", 1.0))


def _best_available_pair(pool: list, delta_e_only: bool = False):
    """argmax _pair_score over every unordered pair in `pool` (O(n²), same
    exhaustive-scan shape as select_auxiliaries' greedy loop, just pairwise
    instead of vs.-already-chosen). None if fewer than 2 candidates remain."""
    if len(pool) < 2:
        return None
    best, best_score = None, -1.0
    for i in range(len(pool)):
        for j in range(i + 1, len(pool)):
            score = _pair_score(pool[i], pool[j], delta_e_only=delta_e_only)
            if score > best_score:
                best_score, best = score, (pool[i], pool[j])
    return best


def _designate_roles(cand_a: dict, cand_b: dict, case: str) -> tuple:
    """Which of the 2 popped candidates becomes the bg-designate vs the
    fg-designate, before the case-specific transform runs. Deterministic:
    whichever candidate currently has the higher Lab L takes the role this
    case wants lighter (background in case1, foreground in case2) --
    minimizes the nudge needed to reach the target separation. case3 has no
    L-preference by definition; tie-broken by HSL S purely for determinism
    (higher S -> bg-designate -- no product meaning either way, since case3
    only changes ONE side's hue regardless of which side that is).

    Returns (bg_designate, fg_designate)."""
    if case == "case3":
        return (cand_a, cand_b) if cand_a["S"] >= cand_b["S"] else (cand_b, cand_a)
    a_l, b_l = cand_a["lab"][0], cand_b["lab"][0]
    lighter, darker = (cand_a, cand_b) if a_l >= b_l else (cand_b, cand_a)
    if case == "case1":  # background light, foreground dark
        return lighter, darker
    return darker, lighter  # case2: background dark, foreground light


def _target_ls(bg_l: float, fg_l: float, target_delta_l: float, bg_lighter: bool) -> tuple:
    """Shared midpoint/clamp math for the case1/2 transform: given the 2
    candidates' current Lab L and a target separation, returns (target_l_bg,
    target_l_fg) clamped to stay within [_PAIR_L_MIN, _PAIR_L_MAX] --
    degrading the achieved delta gracefully (never overshooting into unsafe
    territory) if `target_delta_l` doesn't fit in the room available, same
    "smaller room -> proportionally gentler" philosophy as
    generate_shading_series's fractional ramp."""
    midpoint = (bg_l + fg_l) / 2.0
    band = _PAIR_L_MAX - _PAIR_L_MIN
    clamped_delta = min(abs(target_delta_l), band)
    half = clamped_delta / 2.0
    midpoint = float(np.clip(midpoint, _PAIR_L_MIN + half, _PAIR_L_MAX - half))
    if bg_lighter:
        return midpoint + half, midpoint - half
    return midpoint - half, midpoint + half


def _nudge_to_target_delta_l(bg_lab, fg_lab, target_delta_l: float, bg_lighter: bool) -> tuple:
    """Push bg/fg's Lab L apart (or together) to approximately match
    `target_delta_l` -- the "eco OFF" behavior: each side keeps its own hue/
    chroma (a, b unchanged), only L moves. See _target_ls for the clamp/
    degrade math. Returns (new_bg_lab, new_fg_lab) as plain np.ndarray."""
    bg_lab = np.asarray(bg_lab, dtype=np.float64)
    fg_lab = np.asarray(fg_lab, dtype=np.float64)
    target_l_bg, target_l_fg = _target_ls(bg_lab[0], fg_lab[0], target_delta_l, bg_lighter)
    new_bg = np.array([target_l_bg, bg_lab[1], bg_lab[2]])
    new_fg = np.array([target_l_fg, fg_lab[1], fg_lab[2]])
    return new_bg, new_fg


def _apply_case1_or_2(bg_designate: dict, fg_designate: dict, case: str,
                      target_delta_l: float, eco: bool) -> tuple:
    """Case 1/2 transform.
    eco OFF: each designate keeps its own hue/chroma, only L moves toward
    its own role's target (see _nudge_to_target_delta_l).
    eco ON: same-hue forced -- seed = whichever designate has higher HSL S;
    BOTH outputs are rebuilt from that same hue/chroma, each at its own
    role's target L (never the other role's) -- the seed's own final L is
    NOT preserved verbatim.
    Returns (new_bg, new_fg) as fresh color-entry dicts."""
    bg_lighter = (case == "case1")
    bg_lab = np.asarray(bg_designate["lab"], dtype=np.float64)
    fg_lab = np.asarray(fg_designate["lab"], dtype=np.float64)

    if not eco:
        new_bg_lab, new_fg_lab = _nudge_to_target_delta_l(bg_lab, fg_lab, target_delta_l, bg_lighter)
    else:
        target_l_bg, target_l_fg = _target_ls(bg_lab[0], fg_lab[0], target_delta_l, bg_lighter)
        seed_lab = bg_lab if bg_designate["S"] >= fg_designate["S"] else fg_lab
        a, b = seed_lab[1], seed_lab[2]
        new_bg_lab = np.array([target_l_bg, a, b])
        new_fg_lab = np.array([target_l_fg, a, b])

    new_bg = _make_color_entry(new_bg_lab, label=bg_designate.get("label"))
    new_fg = _make_color_entry(new_fg_lab, label=fg_designate.get("label"))
    return new_bg, new_fg


def _target_l_for_other(anchor_l: float, other_l: float, target_delta_l: float) -> float:
    """For case3: the anchor's L stays fixed; compute the OTHER side's
    target L so the pair's Lab-L delta matches `target_delta_l` (the
    ORIGINAL detected pair's own delta -- small by definition, see
    _classify_pair's threshold), clamped to the safe [_PAIR_L_MIN,
    _PAIR_L_MAX] band. Direction (lighter or darker than the anchor) is
    preserved from whichever side `other` originally sat on, so matching
    the target delta never flips which of the two reads as "the lighter
    one"."""
    if other_l >= anchor_l:
        return float(np.clip(anchor_l + target_delta_l, anchor_l, _PAIR_L_MAX))
    return float(np.clip(anchor_l - target_delta_l, _PAIR_L_MIN, anchor_l))


def _apply_case3(bg_designate: dict, fg_designate: dict, target_delta_l: float) -> tuple:
    """Case 3: contrast comes from hue, not luminance -- but the new pair
    should still roughly reproduce the ORIGINAL detected pair's (small)
    Lab-L delta, not whatever incidental gap the ΔE-maximizing candidate
    search happened to produce (raw Lab ΔE conflates hue and luminance
    distance, so "maximize ΔE" can easily pick 2 candidates that differ a
    lot in L even when the original pair barely did).

    The higher-HSL-S of the two designates is the anchor, kept COMPLETELY
    untouched (same object, same hue AND lightness); the other becomes the
    anchor's HSL complement (+180°, reusing _complement_hue exactly as
    before -- no new hue-triad math) with its Lab L additionally nudged to
    sit `target_delta_l` away from the anchor's own L (see
    _target_l_for_other). Returns (new_bg, new_fg); the anchor side is
    still returned byte-identical (same object) -- the caller is
    responsible for not mutating it in place."""
    if bg_designate["S"] >= fg_designate["S"]:
        anchor, other, anchor_is_bg = bg_designate, fg_designate, True
    else:
        anchor, other, anchor_is_bg = fg_designate, bg_designate, False
    complemented = _complement_hue(other)
    target_l = _target_l_for_other(float(anchor["lab"][0]), float(other["lab"][0]), target_delta_l)
    other_lab = np.array([target_l, complemented["lab"][1], complemented["lab"][2]])
    other_new = _make_color_entry(other_lab, label=other.get("label"))
    return (anchor, other_new) if anchor_is_bg else (other_new, anchor)


def apply_fgbg_pairing(palette: list, pairs: list, eco: bool = False) -> list:
    """Reshape `palette` (already-generated color-entry dicts, each needs at
    least "lab"/"S"/"label", plus an optional ".score") into explicit bg/fg
    pairs, one per entry in `pairs` ({"pair_id", "bg_l", "fg_l"}). Iterates
    `pairs` in the order given -- iteration-priority, if any, is the
    caller's job (this module stays decoupled from coverage/count info, same
    separation of concerns compute_role_demand already had). Best-effort:
    stops (doesn't raise) once fewer than 2 candidates remain in the pool,
    same "best-effort, fewer than requested" precedent as the retired
    _assign_roles.

    Returns new_palette: same length/order as `palette` -- every matched
    pair's 2 slots REPLACED by their transformed colors, everything else
    passed through untouched (same object, same position). Each transformed
    color gains a "role" ("background"/"foreground") and a "pair_id" -- the
    SAME value (copied verbatim from the input pair's own "pair_id") on
    BOTH sides of a pair. This is a stable per-color IDENTITY rather than a
    position, so it survives whatever the caller does to the list afterward
    (regen merging, trimming, reordering) without needing separate index
    bookkeeping -- the caller resolves "who's paired with whom" by grouping
    on shared pair_id whenever it actually needs concrete ids (e.g. once
    final 1-based CSV ids are assigned)."""
    new_palette = list(palette)
    pool = list(palette)  # mutable working set, filtered by identity

    for pair in pairs:
        case = _classify_pair(pair["bg_l"], pair["fg_l"])
        best = _best_available_pair(pool, delta_e_only=(case == "case3"))
        if best is None:
            break
        cand_a, cand_b = best
        bg_designate, fg_designate = _designate_roles(cand_a, cand_b, case)

        target_delta_l = abs(pair["bg_l"] - pair["fg_l"])
        if case == "case3":
            new_bg, new_fg = _apply_case3(bg_designate, fg_designate, target_delta_l)
        else:
            new_bg, new_fg = _apply_case1_or_2(bg_designate, fg_designate, case, target_delta_l, eco)

        # Always copy before tagging "role"/"pair_id" -- case3's anchor
        # branch returns the SAME object as one of the popped candidates
        # (deliberately untouched), and mutating that in place would
        # silently corrupt the caller's own `palette` list too.
        new_bg = dict(new_bg)
        new_bg["role"] = "background"
        new_bg["pair_id"] = pair.get("pair_id")
        new_fg = dict(new_fg)
        new_fg["role"] = "foreground"
        new_fg["pair_id"] = pair.get("pair_id")

        bg_index = next(i for i, c in enumerate(new_palette) if c is bg_designate)
        fg_index = next(i for i, c in enumerate(new_palette) if c is fg_designate)
        new_palette[bg_index] = new_bg
        new_palette[fg_index] = new_fg

        pool = [c for c in pool if c is not cand_a and c is not cand_b]

    return new_palette
