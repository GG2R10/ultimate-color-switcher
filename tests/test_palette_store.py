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


def test_list_palettes(tmp_path):
    (tmp_path / "one.csv").write_text("1,#111111,a\n")
    (tmp_path / "two.csv").write_text("1,#222222,b\n")
    (tmp_path / "ignore.txt").write_text("nope")

    result = ps.list_palettes(str(tmp_path))
    assert len(result) == 2
    assert all(p.endswith(".csv") for p in result)


def test_list_palettes_missing_dir_returns_empty(tmp_path):
    assert ps.list_palettes(str(tmp_path / "nope")) == []
