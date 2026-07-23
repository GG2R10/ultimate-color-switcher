from backend import color_detector as cd


def test_detect_hex_and_rgb(fake_project):
    fp = fake_project.make_file(
        "app/style.css",
        ".a { color: #00CCFF; }\n"
        ".b { color: rgb(0, 204, 255); }\n"
        ".c { color: #ff00aa80; }\n",  # 8-digit hex with alpha suffix
    )
    colors = cd.detect_colors([fp])
    by_key = {(c["type"], c["color"]): c for c in colors}

    assert ("hex", "00ccff") in by_key
    assert ("hex_from_rgb", "00ccff") in by_key
    assert ("hex", "ff00aa") in by_key  # alpha suffix stripped, normalized lowercase
    assert by_key[("hex", "00ccff")]["count"] == 1
    assert by_key[("hex", "00ccff")]["files"] == [fp]


def test_detect_dedupes_files_and_sums_counts(fake_project):
    fp1 = fake_project.make_file("a.css", "#123456 #123456")
    fp2 = fake_project.make_file("b.css", "#123456")
    colors = cd.detect_colors([fp1, fp2])

    assert len(colors) == 1
    entry = colors[0]
    assert entry["color"] == "123456"
    assert entry["count"] == 3
    assert sorted(entry["files"]) == sorted([fp1, fp2])


def test_sorted_by_count_desc_with_sequential_ids(fake_project):
    fp = fake_project.make_file("a.css", "#111111 #222222 #222222 #222222")
    colors = cd.detect_colors([fp])

    assert colors[0]["color"] == "222222"
    assert colors[0]["id"] == 1
    assert colors[1]["color"] == "111111"
    assert colors[1]["id"] == 2


def test_missing_files_are_skipped(tmp_path):
    missing = str(tmp_path / "does-not-exist.css")
    assert cd.detect_colors([missing]) == []


def test_write_read_csv_roundtrip(tmp_path):
    colors = [
        {"id": 1, "type": "hex", "color": "00ccff", "count": 3, "files": ["/a/b.css", "/c/d.css"]},
        {"id": 2, "type": "hex_from_rgb", "color": "ff00aa", "count": 1, "files": ["/a/b.css"]},
    ]
    path = tmp_path / "detected_palette.csv"
    cd.write_detected_csv(colors, str(path))

    lines = path.read_text().splitlines()
    assert lines[0] == "id,type,color,count,files"
    assert lines[1] == "1,hex,00ccff,3,/a/b.css|/c/d.css"
    assert lines[2] == "2,hex_from_rgb,ff00aa,1,/a/b.css"

    assert cd.read_detected_csv(str(path)) == colors


def test_read_detected_csv_missing_file_returns_empty(tmp_path):
    assert cd.read_detected_csv(str(tmp_path / "nope.csv")) == []


def test_grouped_by_hex_finds_siblings():
    colors = [
        {"id": 1, "type": "hex", "color": "00ccff", "count": 1, "files": []},
        {"id": 2, "type": "hex_from_rgb", "color": "00ccff", "count": 1, "files": []},
        {"id": 3, "type": "hex", "color": "ff0000", "count": 1, "files": []},
    ]
    groups = cd.grouped_by_hex(colors)

    assert len(groups["00ccff"]) == 2
    assert {c["id"] for c in groups["00ccff"]} == {1, 2}
    assert len(groups["ff0000"]) == 1
