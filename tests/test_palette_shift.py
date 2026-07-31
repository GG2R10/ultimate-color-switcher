import numpy as np
import pytest
from PIL import Image

from color_switcher.backend import color_detector as cd
from color_switcher.backend import color_math as cm
from color_switcher.backend import mapping_store as ms
from color_switcher.backend import palette_generator as pg
from color_switcher.backend import palette_shift, palette_store


def _make_image(path):
    arr = np.zeros((64, 64, 3), dtype=np.uint8)
    for i, c in enumerate([(220, 40, 40), (40, 200, 60), (50, 60, 230), (240, 230, 40)]):
        arr[:, i * 16:(i + 1) * 16] = c
    Image.fromarray(arr, "RGB").save(path)


def _write_generated_palette(path, image_path, n_colors=4, my_eyes=False, ying_yang=False):
    """Write a generated palette CSV (rows=effective, #ucs-meta with base+params),
    exactly as palette_shift.generate_and_save_palette would."""
    eff, base = pg.generate_palette(str(image_path), n_colors=n_colors, sample_size=3000,
                                    mode="contrast", saturate=my_eyes, ying_yang=ying_yang, with_base=True)
    meta = palette_store.default_meta()
    meta.update({
        "generated": True, "image": str(image_path),
        "gen": {"colors": n_colors, "sample_size": 3000, "mode": "contrast", "scoring": "default",
                "custom_percentages": None, "weighted_contrast": True, "shuffle": 0, "overfetch": 0},
        "post": {"my_eyes": my_eyes, "ying_yang": ying_yang},
        "base": [{"hex": c["hex"], "label": c["label"]} for c in base],
    })
    entries = [{"id": i + 1, "hex": c["hex"], "label": c["label"], "origin": "gen"} for i, c in enumerate(eff)]
    palette_store.write_palette_csv(str(path), entries, meta=meta)
    return base


def _write_created_palette(path, colors):
    entries = [{"id": i + 1, "hex": h, "label": lbl} for i, (h, lbl) in enumerate(colors)]
    meta = palette_store.default_meta()
    meta["base"] = [{"hex": h, "label": lbl} for h, lbl in colors]
    palette_store.write_palette_csv(str(path), entries, meta=meta)


@pytest.fixture
def gen_palette(tmp_path, fake_project):
    img = tmp_path / "wall.png"
    _make_image(img)
    path = tmp_path / "gen.csv"
    base = _write_generated_palette(path, img)
    return path, base, fake_project.load_config()


def test_post_only_shift_does_not_regenerate(gen_palette):
    path, base, config = gen_palette
    result = palette_shift.shift_palette(str(path), config, my_eyes="on")
    assert result["regenerated"] is False
    assert palette_store.read_palette_meta(str(path))["post"]["my_eyes"] is True


def test_post_mod_toggle_is_reversible(gen_palette):
    path, base, config = gen_palette
    palette_shift.shift_palette(str(path), config, my_eyes="on")
    result = palette_shift.shift_palette(str(path), config, my_eyes="toggle")  # back off
    assert [e["hex"] for e in result["entries"]] == [b["hex"] for b in base]


def test_my_eyes_factor_and_max_chroma_are_post_mods_no_regen(gen_palette):
    """Changing the chroma-boost factor/cap is a POST modifier (recomputed
    from the stored base), NOT a selection modifier -- must not regenerate."""
    path, base, config = gen_palette
    result = palette_shift.shift_palette(str(path), config, my_eyes="on", my_eyes_factor=3.0)
    assert result["regenerated"] is False
    assert result["meta"]["post"]["my_eyes_factor"] == 3.0


def test_my_eyes_factor_change_alone_recomputes_effective_colors(gen_palette):
    path, base, config = gen_palette
    palette_shift.shift_palette(str(path), config, my_eyes="on", my_eyes_factor=1.2)
    weak = palette_store.read_palette_csv(str(path))
    result = palette_shift.shift_palette(str(path), config, my_eyes_factor=4.0)  # my_eyes stays on
    strong = result["entries"]
    # a bigger factor pushes chroma further -- at least one color must differ
    assert [e["hex"] for e in strong] != [e["hex"] for e in weak]


def test_my_eyes_max_chroma_shift_caps_the_result(gen_palette):
    from color_switcher.backend import color_math as cm

    path, base, config = gen_palette
    result = palette_shift.shift_palette(str(path), config, my_eyes="on", my_eyes_factor=10.0,
                                         my_eyes_max_chroma=15.0)
    for e in result["entries"]:
        lab = cm.rgb_to_lab(np.array(cm.hex_to_rgb(e["hex"])))
        chroma = float(np.hypot(lab[1], lab[2]))
        assert chroma <= 15.0 + 0.5  # capped (small tolerance for rounding/gamut clipping)


def test_post_only_shift_transforms_and_keeps_hand_added_color(gen_palette):
    path, base, config = gen_palette
    palette_shift.add_color(str(path), "abcdef", "mine")   # origin=custom on a generated palette
    result = palette_shift.shift_palette(str(path), config, ying_yang="on")
    hexes = [e["hex"] for e in result["entries"]]

    # the custom color survives as a slot AND gets the post-mod (not kept literal)
    expected = pg.apply_post_modifiers([{"hex": "abcdef"}], ying_yang=True)[0]["hex"]
    assert expected in hexes
    assert "abcdef" not in hexes
    assert result["entries"][-1]["origin"] == "custom"

    # reversible: the stored base never changed, so toggling ying-yang back off
    # restores the exact literal custom color
    back = palette_shift.shift_palette(str(path), config, ying_yang="toggle")
    assert "abcdef" in [e["hex"] for e in back["entries"]]


def test_selection_shift_keeps_custom_colors_by_default(gen_palette):
    """keep_custom_on_regen defaults True -- a hand-added color survives a
    regeneration instead of being wiped, with no discard warning."""
    path, base, config = gen_palette
    palette_shift.add_color(str(path), "abcdef", "mine")
    result = palette_shift.shift_palette(str(path), config, shuffle=1)
    assert result["regenerated"] is True
    assert not any("hand" in w for w in result["warnings"])
    assert "abcdef" in [e["hex"] for e in result["entries"]]
    assert result["meta"]["keep_custom_on_regen"] is True


def test_selection_shift_keep_custom_off_wipes_and_warns(gen_palette):
    path, base, config = gen_palette
    palette_shift.add_color(str(path), "abcdef", "mine")
    result = palette_shift.shift_palette(str(path), config, shuffle=1, keep_custom="off")
    assert result["regenerated"] is True
    assert any("hand" in w for w in result["warnings"])
    assert "abcdef" not in [e["hex"] for e in result["entries"]]   # wiped by regeneration
    assert result["meta"]["keep_custom_on_regen"] is False


def test_keep_custom_preference_persists_across_shifts(gen_palette):
    path, base, config = gen_palette
    palette_shift.shift_palette(str(path), config, keep_custom="off")  # post-only shift, no regen
    assert palette_store.read_palette_meta(str(path))["keep_custom_on_regen"] is False

    palette_shift.add_color(str(path), "abcdef", "mine")
    result = palette_shift.shift_palette(str(path), config, shuffle=1)  # keep_custom not passed -> stays off
    assert "abcdef" not in [e["hex"] for e in result["entries"]]


def test_edited_in_place_custom_keeps_its_original_id_on_regen(gen_palette):
    """The key correctness property: an edit_color'd slot (same id it always
    had, not appended) survives regeneration in that SAME position -- so an
    existing mapping pointing at that id keeps pointing at the right color."""
    path, base, config = gen_palette
    palette_shift.edit_color(str(path), 2, "abcdef")  # slot 2 becomes custom, same id
    result = palette_shift.shift_palette(str(path), config, shuffle=1)
    assert result["regenerated"] is True
    entries = result["entries"]
    assert entries[1]["id"] == 2
    assert entries[1]["hex"] == "abcdef"          # still there, still at id 2
    assert entries[1]["origin"] == "custom"
    other_hexes = [e["hex"] for i, e in enumerate(entries) if i != 1]
    assert all(h != base[1]["hex"] for h in other_hexes)  # the rest actually regenerated


def test_keep_custom_raises_when_in_range_customs_fill_the_whole_budget(gen_palette):
    path, base, config = gen_palette  # 4 colors
    for i in range(1, 5):
        palette_shift.edit_color(str(path), i, f"{i:02x}{i:02x}{i:02x}")  # all 4 slots now custom
    with pytest.raises(palette_shift.ShiftError):
        palette_shift.shift_palette(str(path), config, shuffle=1)


def test_keep_custom_off_bypasses_the_in_range_budget_error(gen_palette):
    path, base, config = gen_palette
    for i in range(1, 5):
        palette_shift.edit_color(str(path), i, f"{i:02x}{i:02x}{i:02x}")
    result = palette_shift.shift_palette(str(path), config, shuffle=1, keep_custom="off")
    assert result["regenerated"] is True  # doesn't raise -- keep_custom off has no budget conflict


def test_keep_custom_trailing_customs_dont_count_against_the_budget(gen_palette):
    """Customs ADDED beyond the original n_colors (never occupied a gen slot)
    must not be mistaken for occupying part of the budget -- regenerating
    must still request a FULL n_colors fresh gen colors, not fewer."""
    path, base, config = gen_palette  # 4 colors
    palette_shift.add_color(str(path), "abcdef", "extra1")  # position 4 (trailing, beyond n_colors=4)
    palette_shift.add_color(str(path), "123456", "extra2")  # position 5 (also trailing)
    result = palette_shift.shift_palette(str(path), config, shuffle=1)  # must not raise/IndexError
    assert result["regenerated"] is True
    hexes = [e["hex"] for e in result["entries"]]
    assert "abcdef" in hexes and "123456" in hexes
    assert len(result["entries"]) == 6  # 4 regenerated + 2 preserved trailing customs


def test_selection_shift_on_created_palette_raises(tmp_path, fake_project):
    path = tmp_path / "created.csv"
    _write_created_palette(path, [("ff00aa", "primary"), ("00ccff", "secondary")])
    with pytest.raises(palette_shift.ShiftError):
        palette_shift.shift_palette(str(path), fake_project.load_config(), mode="shading")


def test_post_mod_shift_works_on_created_palette(tmp_path, fake_project):
    path = tmp_path / "created.csv"
    _write_created_palette(path, [("2ec16b", "primary"), ("00ccff", "secondary")])
    result = palette_shift.shift_palette(str(path), fake_project.load_config(), my_eyes="on")
    assert result["regenerated"] is False
    # a not-yet-maxed color got more saturated (my-eyes applied without an image)
    assert result["entries"][0]["hex"] != "2ec16b"


def test_shift_without_provenance_and_selection_raises(tmp_path, fake_project):
    # A generated-looking palette but with no image recorded -> can't regenerate.
    path = tmp_path / "noimg.csv"
    meta = palette_store.default_meta()
    meta.update({"generated": True, "image": None, "base": [{"hex": "cbff29", "label": "primary"}]})
    palette_store.write_palette_csv(str(path), [{"id": 1, "hex": "cbff29", "label": "primary", "origin": "gen"}], meta=meta)
    with pytest.raises(palette_shift.ShiftError):
        palette_shift.shift_palette(str(path), fake_project.load_config(), shuffle=1)


def test_shift_test_mode_does_not_write(gen_palette):
    path, base, config = gen_palette
    before = path.read_text()
    result = palette_shift.shift_palette(str(path), config, my_eyes="on", write=False)
    assert result["entries"]                       # computed
    assert path.read_text() == before              # but nothing persisted


def test_add_color_rejects_duplicate(gen_palette):
    path, base, config = gen_palette
    existing = base[0]["hex"]
    with pytest.raises(palette_shift.PaletteEditError):
        palette_shift.add_color(str(path), existing, "dup")


def test_edit_color_marks_custom_and_keeps_position(gen_palette):
    path, base, config = gen_palette
    new_id = palette_shift.edit_color(str(path), 2, "abcdef")   # edit slot 2 by id
    assert new_id == 2                                          # position/id unchanged
    entries, meta = palette_store.read_palette(str(path))
    assert entries[1]["hex"] == "abcdef"
    assert meta["base"][1]["origin"] == "custom"
    # every other slot keeps its id (mapping to them stays valid)
    assert [e["id"] for e in entries] == [1, 2, 3, 4]


def _assert_hex_close(a, b, tol=1):
    ra, ga, ba = int(a[0:2], 16), int(a[2:4], 16), int(a[4:6], 16)
    rb, gb, bb = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)
    assert max(abs(ra - rb), abs(ga - gb), abs(ba - bb)) <= tol, f"{a} vs {b}"


def test_edit_color_shows_up_as_picked_even_with_ying_yang_already_active(gen_palette):
    """The reported bug: picking yellow via the editor while ying-yang is
    already on used to show up as yellow's COMPLEMENT (blue) -- the picked
    hex was stored literally as base, then transformed forward on top."""
    path, base, config = gen_palette
    palette_shift.shift_palette(str(path), config, ying_yang="on")
    palette_shift.edit_color(str(path), 1, "ffcc00")  # yellow
    entries = palette_store.read_palette_csv(str(path))
    _assert_hex_close(entries[0]["hex"], "ffcc00")


def test_add_color_shows_up_as_picked_even_with_ying_yang_already_active(gen_palette):
    path, base, config = gen_palette
    palette_shift.shift_palette(str(path), config, ying_yang="on")
    entry = palette_shift.add_color(str(path), "ffcc00", "mine")
    _assert_hex_close(entry["hex"], "ffcc00")


def test_edit_color_shows_up_as_picked_even_with_my_eyes_already_active(gen_palette):
    path, base, config = gen_palette
    palette_shift.shift_palette(str(path), config, my_eyes="on")
    palette_shift.edit_color(str(path), 1, "8899aa")
    entries = palette_store.read_palette_csv(str(path))
    _assert_hex_close(entries[0]["hex"], "8899aa")


def test_edit_color_then_toggling_ying_yang_off_flips_the_custom_color_too(gen_palette):
    """Consistent with how a GENERATED color already behaves: toggling a
    post-mod off/on transforms EVERY base color the same way, hand-picked or
    not -- a custom color picked while ying-yang was active WILL look
    different (its complement) once ying-yang is turned back off. Accepted,
    documented tradeoff (see [[color-roles-design]]), not a bug: the base
    model has no other way to keep post-mods uniformly reversible."""
    path, base, config = gen_palette
    palette_shift.shift_palette(str(path), config, ying_yang="on")
    palette_shift.edit_color(str(path), 1, "ffcc00")  # shows up as yellow while ying-yang is on

    result = palette_shift.shift_palette(str(path), config, ying_yang="off")
    # ying-yang off now -> the STORED base (yellow's complement) shows plainly
    assert result["entries"][0]["hex"] != "ffcc00"


def test_edit_color_rejects_duplicate(gen_palette):
    path, base, config = gen_palette
    with pytest.raises(palette_shift.PaletteEditError):
        palette_shift.edit_color(str(path), 1, base[2]["hex"])   # color 2's value already exists


def test_edit_then_post_mod_still_applies_to_edited_color(gen_palette):
    path, base, config = gen_palette
    palette_shift.edit_color(str(path), 1, "abcdef")            # now custom
    result = palette_shift.shift_palette(str(path), config, ying_yang="on")
    expected = pg.apply_post_modifiers([{"hex": "abcdef"}], ying_yang=True)[0]["hex"]
    assert result["entries"][0]["hex"] == expected             # custom color got the post-mod


def test_delete_color_renumbers_contiguously(gen_palette):
    path, base, config = gen_palette
    deleted_id = palette_shift.delete_color(str(path), 2)
    assert deleted_id == 2
    entries, meta = palette_store.read_palette(str(path))
    assert [e["id"] for e in entries] == [1, 2, 3]              # 4 -> 3, contiguous
    assert len(meta["base"]) == 3


def test_delete_mapping_surgery():
    from color_switcher.backend import mapping_store as ms
    entries = [{"old_id": 10, "new_id": 1}, {"old_id": 11, "new_id": 2},
               {"old_id": 12, "new_id": 3}, {"old_id": 13, "new_id": 2}]
    out = ms.drop_and_shift_new_id(entries, 2)                  # delete palette color 2
    assert [e["new_id"] for e in out] == [1, None, 2, None]     # ==2 unassigned, 3 shifts to 2


def test_resolve_bool_semantics():
    assert palette_shift._resolve_bool(False, None) is False   # keep
    assert palette_shift._resolve_bool(True, None) is True     # keep
    assert palette_shift._resolve_bool(False, "on") is True
    assert palette_shift._resolve_bool(True, "off") is False
    assert palette_shift._resolve_bool(True, "toggle") is False


def test_resolve_shading_direction_semantics():
    assert palette_shift._resolve_shading_direction("dark", None) == "dark"     # keep
    assert palette_shift._resolve_shading_direction(None, None) == "dark"       # no stored value -> defaults dark
    assert palette_shift._resolve_shading_direction("dark", "light") == "light"
    assert palette_shift._resolve_shading_direction("light", "dark") == "dark"
    assert palette_shift._resolve_shading_direction("dark", "toggle") == "light"
    assert palette_shift._resolve_shading_direction("light", "toggle") == "dark"
    assert palette_shift._resolve_shading_direction(None, "toggle") == "light"   # no stored -> dark -> toggled=light
    with pytest.raises(palette_shift.ShiftError):
        palette_shift._resolve_shading_direction("dark", "sideways")


@pytest.fixture
def shading_palette(tmp_path, fake_project):
    img = tmp_path / "wall.png"
    _make_image(img)
    path = tmp_path / "shading.csv"
    eff, base = pg.generate_palette(str(img), n_colors=2, sample_size=3000, mode="shading", with_base=True)
    meta = palette_store.default_meta()
    meta.update({
        "generated": True, "image": str(img),
        "gen": {"colors": 2, "sample_size": 3000, "mode": "shading", "scoring": "default",
                "custom_percentages": None, "weighted_contrast": True, "shuffle": 0, "overfetch": 0},
        # deliberately NO shading_direction/min/max keys -- a palette generated
        # before this option existed, must default to dark/8/92.
        "post": {"my_eyes": False, "ying_yang": False},
        "base": [{"hex": c["hex"], "label": c["label"]} for c in base],
    })
    entries = [{"id": i + 1, "hex": c["hex"], "label": c["label"], "origin": "gen"} for i, c in enumerate(eff)]
    palette_store.write_palette_csv(str(path), entries, meta=meta)
    return path, fake_project.load_config()


def test_shift_shading_direction_toggle_regenerates_lighter(shading_palette):
    path, config = shading_palette
    before = palette_store.read_palette_csv(str(path))
    result = palette_shift.shift_palette(str(path), config, shading_direction="toggle")
    assert result["regenerated"] is True
    after = palette_store.read_palette_csv(str(path))
    assert after[0]["hex"] == before[0]["hex"]  # primary unaffected, only the shade ramp direction changes
    assert after[1]["hex"] != before[1]["hex"]
    _, meta = palette_store.read_palette(str(path))
    assert meta["gen"]["shading_direction"] == "light"  # legacy (no stored direction) -> defaulted dark -> toggled


def test_shift_shading_direction_toggle_twice_is_reversible(shading_palette):
    path, config = shading_palette
    before = palette_store.read_palette_csv(str(path))
    palette_shift.shift_palette(str(path), config, shading_direction="toggle")
    palette_shift.shift_palette(str(path), config, shading_direction="toggle")
    after = palette_store.read_palette_csv(str(path))
    assert after == before


def test_shift_shading_min_luminance_alone_triggers_regeneration(shading_palette):
    path, config = shading_palette
    result = palette_shift.shift_palette(str(path), config, shading_min_luminance=30.0)
    assert result["regenerated"] is True
    assert result["meta"]["gen"]["shading_min_luminance"] == 30.0
    assert result["meta"]["gen"]["shading_direction"] == "dark"  # untouched override still defaults
    assert palette_shift._resolve_bool(False, "toggle") is True


# --------------------------------------------------------------------------- #
# palette-side roles (Phase 4)
# --------------------------------------------------------------------------- #

def test_add_color_with_role(gen_palette):
    path, base, config = gen_palette
    entry = palette_shift.add_color(str(path), "abcdef", "mine", role="foreground")
    assert entry["role"] == "foreground"
    assert palette_store.read_palette_csv(str(path))[-1]["role"] == "foreground"


def test_edit_color_with_role_sets_it(gen_palette):
    path, base, config = gen_palette
    palette_shift.edit_color(str(path), 1, "abcdef", role="background")
    entries = palette_store.read_palette_csv(str(path))
    assert entries[0]["hex"] == "abcdef"
    assert entries[0]["role"] == "background"


def test_edit_color_without_role_arg_preserves_existing_role(gen_palette):
    path, base, config = gen_palette
    palette_shift.set_role(str(path), 1, "foreground")
    palette_shift.edit_color(str(path), 1, "abcdef")  # role not passed -> _UNSET default
    entries = palette_store.read_palette_csv(str(path))
    assert entries[0]["hex"] == "abcdef"
    assert entries[0]["role"] == "foreground"


def test_edit_color_role_none_clears_it(gen_palette):
    path, base, config = gen_palette
    palette_shift.set_role(str(path), 1, "foreground")
    palette_shift.edit_color(str(path), 1, "abcdef", role=None)
    entries = palette_store.read_palette_csv(str(path))
    assert "role" not in entries[0]


def test_set_role_does_not_mark_color_custom(gen_palette):
    path, base, config = gen_palette
    palette_shift.set_role(str(path), 1, "background")
    entries, meta = palette_store.read_palette(str(path))
    assert entries[0]["role"] == "background"
    assert entries[0]["origin"] == "gen"  # untouched -- set_role never mutates origin/hex


def test_set_role_none_clears_it(gen_palette):
    path, base, config = gen_palette
    palette_shift.set_role(str(path), 1, "background")
    palette_shift.set_role(str(path), 1, None)
    entries = palette_store.read_palette_csv(str(path))
    assert "role" not in entries[0]


def test_set_pair_links_both_sides(gen_palette):
    path, base, config = gen_palette
    palette_shift.set_role(str(path), 1, "background")
    palette_shift.set_role(str(path), 2, "foreground")
    palette_shift.set_pair(str(path), 2, 1)
    entries = palette_store.read_palette_csv(str(path))
    assert entries[0]["paired_id"] == 2
    assert entries[1]["paired_id"] == 1


def test_set_pair_none_unlinks(gen_palette):
    path, base, config = gen_palette
    palette_shift.set_role(str(path), 1, "background")
    palette_shift.set_role(str(path), 2, "foreground")
    palette_shift.set_pair(str(path), 2, 1)
    palette_shift.set_pair(str(path), 2, None)
    entries = palette_store.read_palette_csv(str(path))
    assert "paired_id" not in entries[0]
    assert "paired_id" not in entries[1]


def test_set_pair_rejects_pairing_a_color_with_itself(gen_palette):
    path, base, config = gen_palette
    with pytest.raises(palette_shift.PaletteEditError):
        palette_shift.set_pair(str(path), 1, 1)


def test_set_pair_relinking_drops_the_old_partners_pairing(gen_palette):
    path, base, config = gen_palette
    palette_shift.set_role(str(path), 1, "background")
    palette_shift.set_role(str(path), 2, "foreground")
    palette_shift.set_role(str(path), 3, "background")
    palette_shift.set_pair(str(path), 2, 1)
    palette_shift.set_pair(str(path), 2, 3)  # re-link 2 to a different background
    entries = palette_store.read_palette_csv(str(path))
    assert "paired_id" not in entries[0]  # old partner (1) no longer paired
    assert entries[1]["paired_id"] == 3
    assert entries[2]["paired_id"] == 2


def test_role_change_clears_own_and_partners_pairing(gen_palette):
    path, base, config = gen_palette
    palette_shift.set_role(str(path), 1, "background")
    palette_shift.set_role(str(path), 2, "foreground")
    palette_shift.set_pair(str(path), 2, 1)
    palette_shift.set_role(str(path), 1, "foreground")  # 1 stops being background
    entries = palette_store.read_palette_csv(str(path))
    assert "paired_id" not in entries[0]
    assert "paired_id" not in entries[1]  # partner's side cleared too


def test_edit_color_hex_change_alone_preserves_pairing(gen_palette):
    path, base, config = gen_palette
    palette_shift.set_role(str(path), 1, "background")
    palette_shift.set_role(str(path), 2, "foreground")
    palette_shift.set_pair(str(path), 2, 1)
    palette_shift.edit_color(str(path), 2, "abcdef")  # role not passed, hex changes
    entries = palette_store.read_palette_csv(str(path))
    assert entries[1]["hex"] == "abcdef"
    assert entries[0]["paired_id"] == 2
    assert entries[1]["paired_id"] == 1


def test_edit_color_role_change_clears_pairing(gen_palette):
    path, base, config = gen_palette
    palette_shift.set_role(str(path), 1, "background")
    palette_shift.set_role(str(path), 2, "foreground")
    palette_shift.set_pair(str(path), 2, 1)
    palette_shift.edit_color(str(path), 2, "abcdef", role="background")
    entries = palette_store.read_palette_csv(str(path))
    assert "paired_id" not in entries[0]
    assert "paired_id" not in entries[1]


def test_delete_color_clears_partners_pairing_and_renumbers(gen_palette):
    path, base, config = gen_palette
    palette_shift.set_role(str(path), 1, "background")
    palette_shift.set_role(str(path), 2, "foreground")
    palette_shift.set_pair(str(path), 2, 1)
    palette_shift.delete_color(str(path), 2)
    entries = palette_store.read_palette_csv(str(path))
    assert "paired_id" not in entries[0]
    assert len(entries) == len(base) - 1


def test_pairing_survives_a_reload_from_disk(gen_palette):
    """paired_id read back from a fresh CSV load must still resolve correctly
    through a subsequent per-color op (reconstruct_base's fallback via
    _pair_ids_from_entries when meta.base doesn't carry pair_id yet)."""
    path, base, config = gen_palette
    palette_shift.set_role(str(path), 1, "background")
    palette_shift.set_role(str(path), 2, "foreground")
    palette_shift.set_pair(str(path), 2, 1)
    # touch an unrelated color, forcing a fresh _load()/_write_derived() cycle
    palette_shift.edit_color(str(path), 3, "abcdef")
    entries = palette_store.read_palette_csv(str(path))
    assert entries[0]["paired_id"] == 2
    assert entries[1]["paired_id"] == 1


def test_role_survives_post_mod_shift(gen_palette):
    path, base, config = gen_palette
    palette_shift.set_role(str(path), 1, "foreground")
    result = palette_shift.shift_palette(str(path), config, ying_yang="on")
    assert result["entries"][0]["role"] == "foreground"
    # hex changed (post-mod applied) but role followed the same logical slot
    assert result["entries"][0]["hex"] != base[0]["hex"]


def test_role_tag_alone_without_pair_does_nothing_on_regeneration(gen_palette):
    """A plain set_role with NO explicit pairing has no generation-time
    effect anymore (the old flat count mechanism was fully retired) -- it's
    just inert metadata unless it's also linked via set_pair. Unlike a
    paired role (which the pairing mechanism carries forward -- see
    test_pairing_carries_forward_by_default_on_regeneration), this one has
    no fallback at all, so losing it on a regen IS a genuine, unrecoverable
    loss worth warning about."""
    path, base, config = gen_palette
    palette_shift.set_role(str(path), 1, "background")
    result = palette_shift.shift_palette(str(path), config, shuffle=1)
    assert result["regenerated"] is True
    assert not any(e.get("role") for e in result["entries"])
    assert any("rol" in w for w in result["warnings"])


def test_pending_mapping_updates_maps_old_ids_to_where_the_pair_landed():
    role_pairs = [{"pair_id": "a::b", "bg_l": 10.0, "fg_l": 80.0,
                  "fg_old_ids": [5], "bg_old_ids": [6]}]
    base = [
        {"hex": "111111", "role": "background", "pair_id": "a::b"},
        {"hex": "eeeeee", "role": None, "pair_id": None},
        {"hex": "ffffff", "role": "foreground", "pair_id": "a::b"},
    ]
    updates = palette_shift._pending_mapping_updates(role_pairs, base)
    assert set(updates) == {(5, 3), (6, 1)}


def test_pending_mapping_updates_ignores_pairs_without_detected_old_ids():
    # tier-1-sourced pairs (_role_pairs_from_entries) never carry
    # fg_old_ids/bg_old_ids -- nothing detected-side to rewire.
    role_pairs = [{"pair_id": "1::2", "bg_l": 10.0, "fg_l": 80.0}]
    base = [{"hex": "111111", "role": "background", "pair_id": "1::2"},
            {"hex": "ffffff", "role": "foreground", "pair_id": "1::2"}]
    assert palette_shift._pending_mapping_updates(role_pairs, base) == []


def test_pending_mapping_updates_skips_a_pair_the_pool_never_realized():
    role_pairs = [{"pair_id": "a::b", "bg_l": 10.0, "fg_l": 80.0,
                  "fg_old_ids": [5], "bg_old_ids": [6]}]
    base = [{"hex": "111111", "role": None, "pair_id": None}]  # pool ran out, pair never applied
    assert palette_shift._pending_mapping_updates(role_pairs, base) == []


def test_pairing_carries_forward_by_default_on_regeneration(gen_palette):
    """The OLD paired colors' specific hexes change, but the pairing
    RELATIONSHIP itself is read from the palette's own current tags (tier 1)
    and re-satisfied by 2 NEW colors elsewhere -- this is the actual point
    of the whole pairing feature, and genuinely nothing is lost from the
    user's perspective, so no warning should fire."""
    path, base, config = gen_palette
    palette_shift.set_role(str(path), 1, "background")
    palette_shift.set_role(str(path), 2, "foreground")
    palette_shift.set_pair(str(path), 2, 1)
    result = palette_shift.shift_palette(str(path), config, shuffle=1)
    assert result["regenerated"] is True
    assert sum(1 for e in result["entries"] if e.get("role") == "background") == 1
    assert sum(1 for e in result["entries"] if e.get("role") == "foreground") == 1
    assert result["warnings"] == []


def _setup_detected_pair_mapping(config, path):
    """detected_palette.csv (id1=bg/id2=fg, paired) + a mapping already
    pointing both at `path`'s pre-existing slots 1/2 -- the exact real-world
    setup the user reported: a mapping built BEFORE regeneration, whose
    old_ids should end up following wherever the freshly generated pair
    actually lands, not wherever it used to be."""
    detected = [
        {"id": 1, "type": "hex", "color": "111111", "count": 1, "files": []},
        {"id": 2, "type": "hex", "color": "eeeeee", "count": 1, "files": []},
    ]
    cd.write_detected_csv(detected, config.detected_palette_csv)
    cd.write_color_roles(config.color_roles_json, {
        cd.role_key("hex", "111111"): {"role": "background", "pair": None},
        cd.role_key("hex", "eeeeee"): {"role": "foreground", "pair": "hex:111111"},
    })
    store = ms.MappingStore(config.mapping_csv, old_palette=config.detected_palette_csv,
                            new_palette=str(path), project_dir=config.project_dir)
    store.add_or_update(1, 1)
    store.add_or_update(2, 2)


def test_shift_regen_rewires_mapping_to_follow_the_generated_pair(gen_palette):
    path, base, config = gen_palette
    _setup_detected_pair_mapping(config, path)

    result = palette_shift.shift_palette(str(path), config, shuffle=1)
    fg_id = next(e["id"] for e in result["entries"] if e.get("role") == "foreground")
    bg_id = next(e["id"] for e in result["entries"] if e.get("role") == "background")

    _o, _n, after = ms.read_mapping_csv(config.mapping_csv, project_dir=config.project_dir)
    by_old = {e["old_id"]: e["new_id"] for e in after}
    assert by_old[2] == fg_id  # detected foreground (old_id 2) follows the pair
    assert by_old[1] == bg_id  # detected background (old_id 1) follows the pair


def test_shift_dry_run_does_not_rewire_mapping(gen_palette):
    path, base, config = gen_palette
    _setup_detected_pair_mapping(config, path)

    palette_shift.shift_palette(str(path), config, shuffle=1, write=False)

    _o, _n, after = ms.read_mapping_csv(config.mapping_csv, project_dir=config.project_dir)
    by_old = {e["old_id"]: e["new_id"] for e in after}
    assert by_old == {1: 1, 2: 2}  # untouched -- nothing was actually written


# --------------------------------------------------------------------------- #
# generate_and_save_palette + keep_custom (palette generate / automatic --from-image)
# --------------------------------------------------------------------------- #

def test_generate_no_out_path_defaults_to_a_per_image_slot_not_canonical_generated_csv(tmp_path, fake_project):
    """Regression guard for the "one palette per wallpaper" rework: omitting
    out_path must NOT land on the old single canonical config.generated_palette_csv
    -- it lands on a deterministic, per-image path under palettes_created_dir
    (see palette_store.default_generated_path_for_image), and running it again
    for the SAME image lands on that SAME file (so a later --regenerate finds it)."""
    img = tmp_path / "wall.png"
    _make_image(img)
    config = fake_project.load_config()

    entries, saved_path, _warnings = palette_shift.generate_and_save_palette(
        config, str(img), 4, 3000, "contrast", False,
    )
    assert saved_path != config.generated_palette_csv
    assert saved_path.startswith(config.palettes_created_dir)
    assert saved_path == palette_store.default_generated_path_for_image(config.palettes_created_dir, str(img))

    matches = palette_store.find_palettes_for_image(config.palettes_created_dir, str(img))
    assert matches == [saved_path]


def test_generate_fresh_out_path_defaults_keep_custom_true(tmp_path, fake_project):
    img = tmp_path / "wall.png"
    _make_image(img)
    config = fake_project.load_config()
    out = tmp_path / "fresh.csv"

    entries, saved_path, warnings = palette_shift.generate_and_save_palette(
        config, str(img), 4, 3000, "contrast", False, str(out),
    )
    assert saved_path == str(out)
    assert warnings == []
    assert palette_store.read_palette_meta(str(out))["keep_custom_on_regen"] is True


def test_generate_falls_back_to_project_setting_when_out_path_is_fresh(tmp_path, fake_project):
    img = tmp_path / "wall.png"
    _make_image(img)
    config = fake_project.load_config()
    pg.write_generation_settings(config, {**pg.DEFAULT_GENERATION_SETTINGS, "keep_custom_on_regen": False})
    out = tmp_path / "fresh.csv"

    palette_shift.generate_and_save_palette(config, str(img), 4, 3000, "contrast", False, str(out))

    assert palette_store.read_palette_meta(str(out))["keep_custom_on_regen"] is False


def test_generate_into_existing_file_preserves_its_own_customs_by_default(tmp_path, fake_project):
    img = tmp_path / "wall.png"
    _make_image(img)
    config = fake_project.load_config()
    out = tmp_path / "generated.csv"
    palette_shift.generate_and_save_palette(config, str(img), 4, 3000, "contrast", False, str(out))
    palette_shift.edit_color(str(out), 2, "abcdef")  # slot 2 becomes a hand edit, same id

    entries, saved_path, warnings = palette_shift.generate_and_save_palette(
        config, str(img), 4, 3000, "contrast", False, str(out),
    )
    assert warnings == []
    assert entries[1]["id"] == 2
    assert entries[1]["hex"] == "abcdef"
    assert entries[1]["origin"] == "custom"


def test_generate_existing_files_own_stored_preference_wins_over_project_setting(tmp_path, fake_project):
    img = tmp_path / "wall.png"
    _make_image(img)
    config = fake_project.load_config()
    out = tmp_path / "generated.csv"
    palette_shift.generate_and_save_palette(config, str(img), 4, 3000, "contrast", False, str(out))
    palette_shift.edit_color(str(out), 1, "abcdef")
    palette_shift.shift_palette(str(out), config, keep_custom="off")  # this file's OWN preference -> off
    pg.write_generation_settings(config, {**pg.DEFAULT_GENERATION_SETTINGS, "keep_custom_on_regen": True})

    entries, _saved_path, warnings = palette_shift.generate_and_save_palette(
        config, str(img), 4, 3000, "contrast", False, str(out),
    )
    # the file's own "off" wins over the project's "on" -- wiped, with a warning
    assert "abcdef" not in [e["hex"] for e in entries]
    assert any("hand" in w for w in warnings)


def test_generate_keep_custom_override_beats_everything_stored(tmp_path, fake_project):
    img = tmp_path / "wall.png"
    _make_image(img)
    config = fake_project.load_config()
    out = tmp_path / "generated.csv"
    palette_shift.generate_and_save_palette(config, str(img), 4, 3000, "contrast", False, str(out))
    palette_shift.edit_color(str(out), 1, "abcdef")

    entries, _saved_path, warnings = palette_shift.generate_and_save_palette(
        config, str(img), 4, 3000, "contrast", False, str(out), keep_custom="off",
    )
    assert "abcdef" not in [e["hex"] for e in entries]
    assert any("hand" in w for w in warnings)


def test_generate_resolved_keep_custom_persists_back_to_project_settings(tmp_path, fake_project):
    img = tmp_path / "wall.png"
    _make_image(img)
    config = fake_project.load_config()
    out = tmp_path / "fresh.csv"

    palette_shift.generate_and_save_palette(
        config, str(img), 4, 3000, "contrast", False, str(out), keep_custom="off",
    )
    assert pg.read_generation_settings(config)["keep_custom_on_regen"] is False


def test_generate_raises_when_in_range_customs_fill_the_whole_budget(tmp_path, fake_project):
    img = tmp_path / "wall.png"
    _make_image(img)
    config = fake_project.load_config()
    out = tmp_path / "generated.csv"
    palette_shift.generate_and_save_palette(config, str(img), 4, 3000, "contrast", False, str(out))
    for i in range(1, 5):
        palette_shift.edit_color(str(out), i, f"{i:02x}{i:02x}{i:02x}")

    with pytest.raises(palette_shift.ShiftError):
        palette_shift.generate_and_save_palette(config, str(img), 4, 3000, "contrast", False, str(out))


# --------------------------------------------------------------------------- #
# generate_and_save_palette + fg/bg pairing rework
# --------------------------------------------------------------------------- #

def test_generate_role_pairs_tier1_reads_existing_out_paths_own_pairing(tmp_path, fake_project):
    img = tmp_path / "wall.png"
    _make_image(img)
    config = fake_project.load_config()
    out = tmp_path / "generated.csv"
    palette_shift.generate_and_save_palette(config, str(img), 4, 3000, "contrast", False, str(out))
    palette_shift.set_role(str(out), 1, "background")
    palette_shift.set_role(str(out), 2, "foreground")
    palette_shift.set_pair(str(out), 2, 1)

    entries, _saved_path, warnings = palette_shift.generate_and_save_palette(
        config, str(img), 4, 3000, "contrast", False, str(out), shuffle=1,
    )
    assert sum(1 for e in entries if e.get("role") == "background") == 1
    assert sum(1 for e in entries if e.get("role") == "foreground") == 1
    assert warnings == []  # pairing carried forward -- nothing actually lost, no false alarm


def test_generate_role_pairs_tier2_filters_by_mapping(tmp_path, fake_project):
    img = tmp_path / "wall.png"
    _make_image(img)
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()

    detected = [
        {"id": 1, "type": "hex", "color": "111111", "count": 1, "files": []},
        {"id": 2, "type": "hex", "color": "eeeeee", "count": 1, "files": []},
        {"id": 3, "type": "hex", "color": "ff0000", "count": 1, "files": []},
    ]
    cd.write_detected_csv(detected, config.detected_palette_csv)
    cd.write_color_roles(config.color_roles_json, {
        cd.role_key("hex", "111111"): {"role": "background", "pair": None},
        cd.role_key("hex", "eeeeee"): {"role": "foreground", "pair": "hex:111111"},
        cd.role_key("hex", "ff0000"): {"role": "foreground", "pair": "hex:111111"},  # NOT in the mapping below
    })
    out = tmp_path / "fresh.csv"
    # The mapping consulted for "which detected colors are actually in use" is
    # THIS target palette's own registry section (see
    # mapping_store.resolve_mapping_entries_for_palette) -- not a global one.
    registry = ms.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
    store = registry.for_palette(str(out), old_palette=config.detected_palette_csv)
    store.add_or_update(1, 1)
    store.add_or_update(2, 2)

    entries, _saved_path, _warnings = palette_shift.generate_and_save_palette(
        config, str(img), 4, 3000, "contrast", False, str(out),
    )
    assert sum(1 for e in entries if e.get("role") == "background") == 1
    assert sum(1 for e in entries if e.get("role") == "foreground") == 1  # id 3's pair didn't count


def test_generate_rewires_mapping_to_follow_the_generated_pair(tmp_path, fake_project):
    """Real bug report: a mapping built BEFORE generation (e.g. detected
    ids 1..6 pointed at an existing palette's positions 1..6, with detected
    ids 5/6 tagged fg/bg and paired) kept pointing at those SAME positions
    after a fresh generation, even though the newly generated fg/bg pair can
    land anywhere in the pool -- so "the detected foreground maps to the
    generated foreground" silently broke. Generation must rewire those
    specific mapping entries to follow wherever the pair actually landed."""
    img = tmp_path / "wall.png"
    _make_image(img)
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()

    detected = [
        {"id": 1, "type": "hex", "color": "111111", "count": 1, "files": []},
        {"id": 2, "type": "hex", "color": "eeeeee", "count": 1, "files": []},
    ]
    cd.write_detected_csv(detected, config.detected_palette_csv)
    cd.write_color_roles(config.color_roles_json, {
        cd.role_key("hex", "111111"): {"role": "background", "pair": None},
        cd.role_key("hex", "eeeeee"): {"role": "foreground", "pair": "hex:111111"},
    })
    out = tmp_path / "fresh.csv"
    registry = ms.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
    store = registry.for_palette(str(out), old_palette=config.detected_palette_csv)
    store.add_or_update(1, 1)
    store.add_or_update(2, 2)

    entries, _saved_path, _warnings = palette_shift.generate_and_save_palette(
        config, str(img), 4, 3000, "contrast", False, str(out),
    )
    fg_id = next(e["id"] for e in entries if e.get("role") == "foreground")
    bg_id = next(e["id"] for e in entries if e.get("role") == "background")

    after = registry.for_palette(str(out), set_active=False).entries
    by_old = {e["old_id"]: e["new_id"] for e in after}
    assert by_old[2] == fg_id
    assert by_old[1] == bg_id


def test_generate_role_pairs_tier3_falls_back_to_unfiltered_without_mapping(tmp_path, fake_project):
    img = tmp_path / "wall.png"
    _make_image(img)
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()

    detected = [
        {"id": 1, "type": "hex", "color": "111111", "count": 1, "files": []},
        {"id": 2, "type": "hex", "color": "eeeeee", "count": 1, "files": []},
    ]
    cd.write_detected_csv(detected, config.detected_palette_csv)
    cd.write_color_roles(config.color_roles_json, {
        cd.role_key("hex", "111111"): {"role": "background", "pair": None},
        cd.role_key("hex", "eeeeee"): {"role": "foreground", "pair": "hex:111111"},
    })
    # deliberately no mapping.csv at all

    out = tmp_path / "fresh.csv"
    entries, _saved_path, _warnings = palette_shift.generate_and_save_palette(
        config, str(img), 4, 3000, "contrast", False, str(out),
    )
    assert sum(1 for e in entries if e.get("role") == "background") == 1
    assert sum(1 for e in entries if e.get("role") == "foreground") == 1


def test_generate_tagged_without_pair_is_ignored_and_warns(tmp_path, fake_project):
    img = tmp_path / "wall.png"
    _make_image(img)
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()

    detected = [{"id": 1, "type": "hex", "color": "111111", "count": 1, "files": []}]
    cd.write_detected_csv(detected, config.detected_palette_csv)
    cd.write_color_roles(config.color_roles_json, {
        cd.role_key("hex", "111111"): {"role": "background", "pair": None},  # tagged, never paired
    })

    out = tmp_path / "fresh.csv"
    entries, _saved_path, warnings = palette_shift.generate_and_save_palette(
        config, str(img), 4, 3000, "contrast", False, str(out),
    )
    assert not any(e.get("role") for e in entries)
    assert any("no linked pair" in w for w in warnings)


def test_generate_raises_when_role_pairs_exceed_colors_requested(tmp_path, fake_project):
    img = tmp_path / "wall.png"
    _make_image(img)
    config = fake_project.load_config()
    out = tmp_path / "generated.csv"
    palette_shift.generate_and_save_palette(config, str(img), 2, 3000, "contrast", False, str(out))
    palette_shift.set_role(str(out), 1, "background")
    palette_shift.set_role(str(out), 2, "foreground")
    palette_shift.set_pair(str(out), 2, 1)

    with pytest.raises(palette_shift.ShiftError):
        # demanding a full pair (2 colors) but asking for only 1 color total
        palette_shift.generate_and_save_palette(config, str(img), 1, 3000, "contrast", False, str(out))


def test_generate_eco_resolved_value_persists_to_project_settings(tmp_path, fake_project):
    img = tmp_path / "wall.png"
    _make_image(img)
    config = fake_project.load_config()
    out = tmp_path / "fresh.csv"

    palette_shift.generate_and_save_palette(
        config, str(img), 4, 3000, "contrast", False, str(out), eco="on",
    )
    assert pg.read_generation_settings(config)["eco_contrast"] is True


def _make_grayscale_image(path, dark_fraction=0.5):
    img = Image.new("RGB", (60, 60))
    pixels = img.load()
    split = int(60 * dark_fraction)
    for x in range(60):
        for y in range(60):
            v = 5 if x < split else 250
            pixels[x, y] = (v, v, v)
    img.save(path)


def _saturations(entries):
    sats = []
    for e in entries:
        _hue, sat, _light = cm.rgb_to_hsl(cm.hex_to_rgb(e["hex"]))
        sats.append(sat)
    return sats


def test_generate_fresh_out_path_defaults_hallucinate_true(tmp_path, fake_project):
    img = tmp_path / "wall.png"
    _make_image(img)
    config = fake_project.load_config()
    out = tmp_path / "fresh.csv"

    palette_shift.generate_and_save_palette(config, str(img), 4, 3000, "contrast", False, str(out))
    assert palette_store.read_palette_meta(str(out))["hallucinate_on_monochrome"] is True


def test_generate_hallucinate_true_colors_a_monochrome_wallpapers_whole_palette(tmp_path, fake_project):
    img = tmp_path / "bw.png"
    _make_grayscale_image(img)
    config = fake_project.load_config()
    out = tmp_path / "generated.csv"

    entries, _saved_path, _warnings = palette_shift.generate_and_save_palette(
        config, str(img), 4, 3000, "contrast", False, str(out),
    )
    assert all(s > 15 for s in _saturations(entries))


def test_generate_hallucinate_off_gives_a_genuinely_greyscale_palette(tmp_path, fake_project):
    img = tmp_path / "bw.png"
    _make_grayscale_image(img)
    config = fake_project.load_config()
    out = tmp_path / "generated.csv"

    entries, _saved_path, _warnings = palette_shift.generate_and_save_palette(
        config, str(img), 2, 3000, "contrast", False, str(out), hallucinate="off",
    )
    assert all(s < 10 for s in _saturations(entries))


def test_generate_existing_files_own_stored_hallucinate_preference_wins_over_project_setting(
    tmp_path, fake_project,
):
    img = tmp_path / "bw.png"
    _make_grayscale_image(img)
    config = fake_project.load_config()
    out = tmp_path / "generated.csv"
    palette_shift.generate_and_save_palette(config, str(img), 2, 3000, "contrast", False, str(out))
    palette_shift.shift_palette(str(out), config, hallucinate="off")  # this file's OWN preference -> off
    pg.write_generation_settings(config, {**pg.DEFAULT_GENERATION_SETTINGS, "hallucinate_on_monochrome": True})

    entries, _saved_path, _warnings = palette_shift.generate_and_save_palette(
        config, str(img), 2, 3000, "contrast", False, str(out),
    )
    # the file's own "off" wins over the project's "on" -- stays genuinely grey
    assert all(s < 10 for s in _saturations(entries))


def test_generate_hallucinate_override_beats_everything_stored(tmp_path, fake_project):
    img = tmp_path / "bw.png"
    _make_grayscale_image(img)
    config = fake_project.load_config()
    out = tmp_path / "generated.csv"
    palette_shift.generate_and_save_palette(config, str(img), 2, 3000, "contrast", False, str(out))

    entries, _saved_path, _warnings = palette_shift.generate_and_save_palette(
        config, str(img), 2, 3000, "contrast", False, str(out), hallucinate="off",
    )
    assert all(s < 10 for s in _saturations(entries))


def test_generate_resolved_hallucinate_persists_back_to_project_settings(tmp_path, fake_project):
    img = tmp_path / "wall.png"
    _make_image(img)
    config = fake_project.load_config()
    out = tmp_path / "fresh.csv"

    palette_shift.generate_and_save_palette(
        config, str(img), 4, 3000, "contrast", False, str(out), hallucinate="off",
    )
    assert pg.read_generation_settings(config)["hallucinate_on_monochrome"] is False


def test_shift_hallucinate_off_forces_greyscale_on_regenerate(tmp_path, fake_project):
    img = tmp_path / "bw.png"
    _make_grayscale_image(img)
    config = fake_project.load_config()
    out = tmp_path / "generated.csv"
    palette_shift.generate_and_save_palette(config, str(img), 2, 3000, "contrast", False, str(out))

    result = palette_shift.shift_palette(str(out), config, colors=2, hallucinate="off")
    assert result["regenerated"] is True
    assert all(s < 10 for s in _saturations(result["entries"]))
    assert result["meta"]["hallucinate_on_monochrome"] is False


def _make_two_tone_image(path):
    # A strict 2-value black/white image: only 2 distinct real clusters exist
    # no matter how many colors are requested -- the scarce-real-clusters case.
    img = Image.new("RGB", (60, 60))
    pixels = img.load()
    for x in range(60):
        for y in range(60):
            v = 5 if x < 30 else 250
            pixels[x, y] = (v, v, v)
    img.save(path)


def test_generate_raises_clean_error_instead_of_indexerror_when_image_lacks_diversity(
    tmp_path, fake_project,
):
    # Real bug: hallucinate=off against a scarce-real-cluster image used to
    # let generate_palette return fewer colors than requested, which then
    # IndexError'd inside _merge_regen_base (keep_custom defaults True) instead
    # of failing cleanly.
    img = tmp_path / "bw.png"
    _make_two_tone_image(img)
    config = fake_project.load_config()
    out = tmp_path / "generated.csv"

    with pytest.raises(palette_shift.ShiftError):
        palette_shift.generate_and_save_palette(
            config, str(img), 6, 3000, "contrast", False, str(out), hallucinate="off",
        )


def test_generate_raises_clean_error_even_without_keep_custom(tmp_path, fake_project):
    # Same shortfall, but with keep_custom off (n_gen_needed == n_colors, no
    # positional merge involved) -- must still be flagged instead of silently
    # handing back fewer colors than asked for.
    img = tmp_path / "bw.png"
    _make_two_tone_image(img)
    config = fake_project.load_config()
    out = tmp_path / "generated.csv"

    with pytest.raises(palette_shift.ShiftError):
        palette_shift.generate_and_save_palette(
            config, str(img), 6, 3000, "contrast", False, str(out),
            hallucinate="off", keep_custom="off",
        )


def test_shift_raises_clean_error_instead_of_indexerror_when_image_lacks_diversity(
    tmp_path, fake_project,
):
    img = tmp_path / "bw.png"
    _make_two_tone_image(img)
    config = fake_project.load_config()
    out = tmp_path / "generated.csv"
    palette_shift.generate_and_save_palette(
        config, str(img), 2, 3000, "contrast", False, str(out), hallucinate="off",
    )

    with pytest.raises(palette_shift.ShiftError):
        palette_shift.shift_palette(str(out), config, colors=6, hallucinate="off")


def test_generate_shortfall_error_hints_at_hallucinate_when_off(tmp_path, fake_project):
    img = tmp_path / "bw.png"
    _make_two_tone_image(img)
    config = fake_project.load_config()
    out = tmp_path / "generated.csv"

    with pytest.raises(palette_shift.ShiftError, match="hallucinate"):
        palette_shift.generate_and_save_palette(
            config, str(img), 6, 3000, "contrast", False, str(out), hallucinate="off",
        )
