import numpy as np
import pytest
from PIL import Image

from color_switcher.backend import palette_generator as pg
from color_switcher.backend import palette_shift, palette_store


def _make_image(path):
    arr = np.zeros((64, 64, 3), dtype=np.uint8)
    for i, c in enumerate([(220, 40, 40), (40, 200, 60), (50, 60, 230), (240, 230, 40)]):
        arr[:, i * 16:(i + 1) * 16] = c
    Image.fromarray(arr, "RGB").save(path)


def _write_generated_palette(path, image_path, n_colors=4, my_eyes=False, ying_yang=False):
    """Write a generated palette CSV (rows=effective, #ucs-meta with base+params),
    exactly as main._generate_and_save_palette would."""
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


def test_selection_shift_regenerates_and_warns_about_custom_wipe(gen_palette):
    path, base, config = gen_palette
    palette_shift.add_color(str(path), "abcdef", "mine")
    result = palette_shift.shift_palette(str(path), config, shuffle=1)
    assert result["regenerated"] is True
    assert any("mano" in w for w in result["warnings"])
    assert "abcdef" not in [e["hex"] for e in result["entries"]]   # wiped by regeneration


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
    assert palette_shift._resolve_bool(False, "toggle") is True
