from color_switcher.backend import color_detector as cd


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


# --- auto-scan ~/.config -------------------------------------------------------

def test_scan_finds_color_config_files_by_format(tmp_path):
    cfg = tmp_path / ".config"
    (cfg / "waybar").mkdir(parents=True)
    (cfg / "waybar" / "style.css").write_text(".a { color: #1affcc; }")   # css + color -> in
    (cfg / "hypr").mkdir()
    (cfg / "hypr" / "hyprland.conf").write_text("col.active = rgb(255,0,0)")  # conf + rgb -> in
    (cfg / "notes.txt").write_text("my #1affcc note")                    # color but wrong format -> out
    (cfg / "empty.toml").write_text("name = 'no colors here'")           # right format, no color -> out

    found = cd.scan_config_dir_for_color_files(str(cfg))
    names = {__import__("os").path.basename(p) for p in found}
    assert names == {"style.css", "hyprland.conf"}


def test_scan_matches_bare_config_filename(tmp_path):
    cfg = tmp_path / ".config"
    (cfg / "git").mkdir(parents=True)
    (cfg / "git" / "config").write_text("[color] ui = #336699")  # no extension, named 'config'
    found = cd.scan_config_dir_for_color_files(str(cfg))
    assert len(found) == 1 and found[0].endswith("git/config")


def test_scan_skips_cache_logs_git_and_node_modules_dirs(tmp_path):
    cfg = tmp_path / ".config"
    for noise in ("Cache", "GPUCache", "logs", ".git", "node_modules"):
        d = cfg / "app" / noise
        d.mkdir(parents=True)
        (d / "theme.css").write_text("color: #abcdef")
    (cfg / "app" / "real.css").write_text("color: #abcdef")  # the only one kept
    found = cd.scan_config_dir_for_color_files(str(cfg))
    assert len(found) == 1 and found[0].endswith("app/real.css")


def test_scan_skips_binary_and_oversized_files(tmp_path):
    cfg = tmp_path / ".config"
    cfg.mkdir()
    (cfg / "bin.conf").write_bytes(b"#abcdef\x00\x00 binary junk")        # NUL byte -> binary -> out
    big = "\n".join(f"#{i:06x}" for i in range(200000))                   # > 1MB of hex -> out
    (cfg / "huge.css").write_text(big)
    (cfg / "ok.conf").write_text("#abcdef")                               # kept
    found = cd.scan_config_dir_for_color_files(str(cfg))
    assert len(found) == 1 and found[0].endswith("ok.conf")


def test_scan_does_not_recurse_into_symlinked_dirs(tmp_path):
    import os
    cfg = tmp_path / ".config"
    (cfg / "real").mkdir(parents=True)
    (cfg / "real" / "a.css").write_text("#111111")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "escaped.css").write_text("#222222")
    os.symlink(str(outside), str(cfg / "link"))  # symlinked dir must not be walked into
    found = cd.scan_config_dir_for_color_files(str(cfg))
    assert any(p.endswith("real/a.css") for p in found)
    assert not any("escaped.css" in p for p in found)


def test_group_paths_by_top_level_collapses_nested_subfolders():
    base = "/c"
    paths = [
        "/c/waybar/style.css",
        "/c/claude/projects/a/x.json",   # deep nesting must collapse under "claude"
        "/c/claude/projects/b/y.json",
        "/c/claude/settings.json",
        "/c/app.conf",                    # file directly under base
    ]
    groups = cd.group_paths_by_top_level(paths, base_dir=base)
    as_dict = {top: files for top, files in groups}

    # everything under claude/** is one toggleable group, files keep their subpath
    assert as_dict["/c/claude"] == [
        ("/c/claude/projects/a/x.json", "projects/a/x.json"),
        ("/c/claude/projects/b/y.json", "projects/b/y.json"),
        ("/c/claude/settings.json", "settings.json"),
    ]
    assert as_dict["/c/waybar"] == [("/c/waybar/style.css", "style.css")]
    assert as_dict["/c"] == [("/c/app.conf", "app.conf")]  # top-level file grouped under base
