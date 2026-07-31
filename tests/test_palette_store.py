from color_switcher.backend import palette_store as ps


def test_write_read_roundtrip(tmp_path):
    path = tmp_path / "created" / "my.csv"
    entries = [
        {"id": 1, "hex": "ff00aa", "label": "primary"},
        {"id": 2, "hex": "00ccff", "label": "secondary"},
    ]
    ps.write_palette_csv(str(path), entries)

    lines = path.read_text().splitlines()
    assert lines[0] == "1,#ff00aa,primary"  # no header row
    assert lines[1] == "2,#00ccff,secondary"

    assert ps.read_palette_csv(str(path)) == entries


def test_read_accepts_uppercase_and_missing_hash(tmp_path):
    path = tmp_path / "p.csv"
    path.write_text("1,FF00AA,label\n2,00ccff,\n")
    entries = ps.read_palette_csv(str(path))

    assert entries[0] == {"id": 1, "hex": "ff00aa", "label": "label"}
    assert entries[1] == {"id": 2, "hex": "00ccff", "label": ""}


def test_read_skips_invalid_rows(tmp_path):
    path = tmp_path / "p.csv"
    path.write_text("notanumber,#111111,bad\n1,#zzzzzz,badhex\n2,#00ccff,ok\n")
    entries = ps.read_palette_csv(str(path))
    assert entries == [{"id": 2, "hex": "00ccff", "label": "ok"}]


def test_read_missing_file_returns_empty(tmp_path):
    assert ps.read_palette_csv(str(tmp_path / "nope.csv")) == []


def test_add_color_assigns_next_id(tmp_path):
    path = tmp_path / "p.csv"
    ps.write_palette_csv(str(path), [{"id": 1, "hex": "111111", "label": "a"}])
    entry = ps.add_color(str(path), "#222222", "b")

    assert entry == {"id": 2, "hex": "222222", "label": "b"}
    assert ps.read_palette_csv(str(path))[-1] == entry


def test_add_color_to_empty_or_missing_palette_starts_at_one(tmp_path):
    path = tmp_path / "p.csv"
    entry = ps.add_color(str(path), "abcdef", "first")
    assert entry["id"] == 1


def test_meta_roundtrip(tmp_path):
    path = tmp_path / "gen.csv"
    meta = ps.default_meta()
    meta.update({"generated": True, "image": "$HOME/w.jpg",
                 "gen": {"colors": 4, "mode": "shading"}, "post": {"my_eyes": True, "ying_yang": False}})
    entries = [{"id": 1, "hex": "cbff29", "label": "primary", "origin": "gen"}]
    ps.write_palette_csv(str(path), entries, meta=meta)

    lines = path.read_text().splitlines()
    assert lines[0].startswith(ps.META_PREFIX)
    assert lines[1] == "1,#cbff29,primary,gen"  # origin field written

    got_entries, got_meta = ps.read_palette(str(path))
    assert got_meta == meta
    assert got_entries == entries


def test_read_palette_meta_defaults_when_no_header(tmp_path):
    path = tmp_path / "plain.csv"
    path.write_text("1,#111111,a\n")
    assert ps.read_palette_meta(str(path)) == ps.default_meta()
    # and a header-less palette's entries carry NO origin key (backward-compat shape)
    assert ps.read_palette_csv(str(path)) == [{"id": 1, "hex": "111111", "label": "a"}]


def test_read_palette_tolerates_malformed_meta(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text(ps.META_PREFIX + "{not valid json\n1,#111111,a\n")
    entries, meta = ps.read_palette(str(path))
    assert meta == ps.default_meta()
    assert entries == [{"id": 1, "hex": "111111", "label": "a"}]


def test_add_color_preserves_header_and_marks_custom_on_generated(tmp_path):
    path = tmp_path / "gen.csv"
    meta = ps.default_meta()
    meta.update({"generated": True, "image": "$HOME/w.jpg", "gen": {"colors": 1}})
    ps.write_palette_csv(str(path), [{"id": 1, "hex": "cbff29", "label": "primary", "origin": "gen"}], meta=meta)

    entry = ps.add_color(str(path), "#123456", "mine")
    assert entry["origin"] == "custom"

    entries, got_meta = ps.read_palette(str(path))
    assert got_meta == meta                       # header preserved verbatim
    assert ps.has_custom_edits(entries)           # the warn-before-regenerate signal
    assert entries[-1] == {"id": 2, "hex": "123456", "label": "mine", "origin": "custom"}


def test_add_color_keeps_headerless_palette_headerless(tmp_path):
    path = tmp_path / "plain.csv"
    ps.write_palette_csv(str(path), [{"id": 1, "hex": "111111", "label": "a"}])
    ps.add_color(str(path), "222222", "b")

    text = path.read_text()
    assert ps.META_PREFIX not in text             # stays header-less
    assert not text.splitlines()[0].endswith(",custom")  # no origin field either


def test_list_palettes(tmp_path):
    (tmp_path / "one.csv").write_text("1,#111111,a\n")
    (tmp_path / "two.csv").write_text("1,#222222,b\n")
    (tmp_path / "ignore.txt").write_text("nope")

    result = ps.list_palettes(str(tmp_path))
    assert len(result) == 2
    assert all(p.endswith(".csv") for p in result)


def test_list_palettes_missing_dir_returns_empty(tmp_path):
    assert ps.list_palettes(str(tmp_path / "nope")) == []


def test_set_preview_image_persists_and_preserves_everything_else(tmp_path):
    path = tmp_path / "mine.csv"
    entries = [{"id": 1, "hex": "ff0000", "label": "primary"}]
    ps.write_palette_csv(str(path), entries, meta=ps.default_meta())

    ps.set_preview_image(str(path), "/home/someone/wallpapers/w.jpg")

    got_entries, got_meta = ps.read_palette(str(path))
    assert got_meta["preview_image"] == "/home/someone/wallpapers/w.jpg"
    assert got_meta["generated"] is False  # untouched
    assert got_meta["image"] is None       # untouched -- preview_image is separate
    assert got_entries == entries


def test_set_preview_image_none_clears_it(tmp_path):
    path = tmp_path / "mine.csv"
    ps.write_palette_csv(str(path), [{"id": 1, "hex": "ff0000", "label": "primary"}], meta=ps.default_meta())
    ps.set_preview_image(str(path), "/some/image.png")

    ps.set_preview_image(str(path), None)

    assert ps.read_palette_meta(str(path))["preview_image"] is None


def test_default_meta_includes_preview_image_key():
    assert ps.default_meta()["preview_image"] is None


def test_write_read_roundtrip_with_role(tmp_path):
    path = tmp_path / "p.csv"
    entries = [
        {"id": 1, "hex": "ff00aa", "label": "primary", "role": "foreground"},
        {"id": 2, "hex": "00ccff", "label": "secondary"},  # no role -> stays unmarked
    ]
    ps.write_palette_csv(str(path), entries)

    lines = path.read_text().splitlines()
    assert lines[0] == "1,#ff00aa,primary,,foreground"  # origin field empty, role present
    assert lines[1] == "2,#00ccff,secondary,,"

    got = ps.read_palette_csv(str(path))
    assert got[0] == entries[0]
    assert "role" not in got[1]  # absence round-trips back to "no key", not "" or None


def test_write_read_roundtrip_with_role_and_origin(tmp_path):
    path = tmp_path / "p.csv"
    entries = [{"id": 1, "hex": "cbff29", "label": "primary", "origin": "gen", "role": "background"}]
    ps.write_palette_csv(str(path), entries)

    assert path.read_text().splitlines()[0] == "1,#cbff29,primary,gen,background"
    assert ps.read_palette_csv(str(path)) == entries


def test_role_column_omitted_when_no_entry_has_one(tmp_path):
    path = tmp_path / "p.csv"
    ps.write_palette_csv(str(path), [{"id": 1, "hex": "111111", "label": "a"}])
    assert path.read_text().splitlines()[0] == "1,#111111,a"  # unchanged shape, no role column at all


def test_read_ignores_invalid_role_value(tmp_path):
    path = tmp_path / "p.csv"
    path.write_text("1,#111111,a,,sideways\n")
    entries = ps.read_palette_csv(str(path))
    assert "role" not in entries[0]


def test_find_palettes_for_image_matches_by_provenance_not_filename(tmp_path):
    directory = tmp_path / "created"
    meta = ps.default_meta()
    meta.update({"generated": True, "image": str(tmp_path / "wall.png")})
    ps.write_palette_csv(str(directory / "totally-unrelated-name.csv"),
                         [{"id": 1, "hex": "111111", "label": ""}], meta=meta)

    matches = ps.find_palettes_for_image(str(directory), str(tmp_path / "wall.png"))
    assert matches == [str(directory / "totally-unrelated-name.csv")]

    # a different image -> no match, regardless of any filename resemblance
    assert ps.find_palettes_for_image(str(directory), str(tmp_path / "other.png")) == []


def test_find_palettes_for_image_ignores_non_generated_palettes(tmp_path):
    directory = tmp_path / "created"
    ps.write_palette_csv(str(directory / "handmade.csv"), [{"id": 1, "hex": "111111", "label": ""}])  # no meta

    meta = ps.default_meta()  # generated=False, but image happens to be set
    meta["image"] = str(tmp_path / "wall.png")
    ps.write_palette_csv(str(directory / "half.csv"), [{"id": 1, "hex": "222222", "label": ""}], meta=meta)

    assert ps.find_palettes_for_image(str(directory), str(tmp_path / "wall.png")) == []


def test_find_palettes_for_image_missing_dir_returns_empty(tmp_path):
    assert ps.find_palettes_for_image(str(tmp_path / "nope"), str(tmp_path / "wall.png")) == []


def test_default_generated_path_for_image_is_deterministic_per_image(tmp_path):
    created_dir = str(tmp_path / "created")
    image = str(tmp_path / "wallpapers" / "sunset.jpg")

    first = ps.default_generated_path_for_image(created_dir, image)
    second = ps.default_generated_path_for_image(created_dir, image)
    assert first == second
    assert first.startswith(str(tmp_path / "created"))
    assert first.endswith(".csv")
    assert "sunset" in first

    other_image = str(tmp_path / "wallpapers" / "sunrise.jpg")
    assert ps.default_generated_path_for_image(created_dir, other_image) != first


def test_add_color_with_role(tmp_path):
    path = tmp_path / "p.csv"
    ps.write_palette_csv(str(path), [{"id": 1, "hex": "111111", "label": "a"}])
    entry = ps.add_color(str(path), "222222", "b", role="foreground")
    assert entry["role"] == "foreground"
    assert ps.read_palette_csv(str(path))[-1]["role"] == "foreground"


def test_delete_palette_removes_the_file_and_reports_it_existed(tmp_path):
    path = tmp_path / "created" / "p.csv"
    ps.write_palette_csv(str(path), [{"id": 1, "hex": "111111", "label": "a"}])

    assert ps.delete_palette(str(path)) is True
    assert not path.exists()


def test_delete_palette_on_a_missing_file_is_a_no_op(tmp_path):
    assert ps.delete_palette(str(tmp_path / "nope.csv")) is False
