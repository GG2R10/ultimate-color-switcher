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


def test_role_key_normalizes_hash_and_case():
    assert cd.role_key("hex", "#AABBCC") == "hex:aabbcc"
    assert cd.role_key("hex_from_rgb", "aabbcc") == "hex_from_rgb:aabbcc"


def test_cycle_role_is_unmarked_background_foreground_unmarked():
    assert cd.cycle_role(None) == "background"
    assert cd.cycle_role("background") == "foreground"
    assert cd.cycle_role("foreground") is None


def test_read_color_roles_missing_file_returns_empty(tmp_path):
    assert cd.read_color_roles(str(tmp_path / "color_roles.json")) == {}


def test_write_read_color_roles_roundtrip(tmp_path):
    path = tmp_path / "color_roles.json"
    cd.write_color_roles(str(path), {"hex:aabbcc": "background", "hex:112233": "foreground"})
    assert cd.read_color_roles(str(path)) == {"hex:aabbcc": "background", "hex:112233": "foreground"}


def test_write_color_roles_drops_unmarked_and_invalid_entries(tmp_path):
    path = tmp_path / "color_roles.json"
    cd.write_color_roles(str(path), {"hex:aabbcc": "background", "hex:ffffff": None, "hex:000000": "bogus"})
    assert cd.read_color_roles(str(path)) == {"hex:aabbcc": "background"}


def test_read_color_roles_tolerates_malformed_json(tmp_path):
    path = tmp_path / "color_roles.json"
    path.write_text("{not valid json")
    assert cd.read_color_roles(str(path)) == {}


def test_read_color_roles_tolerates_non_dict_json(tmp_path):
    path = tmp_path / "color_roles.json"
    path.write_text("[1, 2, 3]")
    assert cd.read_color_roles(str(path)) == {}


def test_rekey_roles_after_apply_carries_role_to_new_hex(tmp_path):
    path = tmp_path / "color_roles.json"
    cd.write_color_roles(str(path), {"hex:aabbcc": "background"})

    detected = [{"id": 1, "type": "hex", "color": "aabbcc", "count": 1, "files": []}]
    new_palette = [{"id": 1, "hex": "112233"}]
    resolved = [{"old_id": 1, "new_id": 1}]

    collisions = cd.rekey_roles_after_apply(str(path), detected, new_palette, resolved)
    assert collisions == []
    assert cd.read_color_roles(str(path)) == {"hex:112233": "background"}


def test_rekey_roles_after_apply_leaves_unrelated_roles_untouched(tmp_path):
    path = tmp_path / "color_roles.json"
    cd.write_color_roles(str(path), {"hex:aabbcc": "background", "hex:ffffff": "foreground"})

    detected = [{"id": 1, "type": "hex", "color": "aabbcc", "count": 1, "files": []}]
    new_palette = [{"id": 1, "hex": "112233"}]
    resolved = [{"old_id": 1, "new_id": 1}]

    cd.rekey_roles_after_apply(str(path), detected, new_palette, resolved)
    assert cd.read_color_roles(str(path)) == {"hex:112233": "background", "hex:ffffff": "foreground"}


def test_rekey_roles_after_apply_no_op_when_nothing_tagged(tmp_path):
    path = tmp_path / "color_roles.json"
    detected = [{"id": 1, "type": "hex", "color": "aabbcc", "count": 1, "files": []}]
    new_palette = [{"id": 1, "hex": "112233"}]
    resolved = [{"old_id": 1, "new_id": 1}]

    collisions = cd.rekey_roles_after_apply(str(path), detected, new_palette, resolved)
    assert collisions == []
    assert not path.exists()  # nothing to write, no file created


def test_rekey_roles_after_apply_same_hex_is_a_no_op(tmp_path):
    path = tmp_path / "color_roles.json"
    cd.write_color_roles(str(path), {"hex:aabbcc": "foreground"})
    detected = [{"id": 1, "type": "hex", "color": "aabbcc", "count": 1, "files": []}]
    new_palette = [{"id": 1, "hex": "aabbcc"}]  # mapped to itself, unchanged
    resolved = [{"old_id": 1, "new_id": 1}]

    collisions = cd.rekey_roles_after_apply(str(path), detected, new_palette, resolved)
    assert collisions == []
    assert cd.read_color_roles(str(path)) == {"hex:aabbcc": "foreground"}


def test_rekey_roles_after_apply_convergence_with_different_roles_is_left_unmarked(tmp_path):
    path = tmp_path / "color_roles.json"
    cd.write_color_roles(str(path), {"hex:aabbcc": "background", "hex:ddeeff": "foreground"})

    detected = [
        {"id": 1, "type": "hex", "color": "aabbcc", "count": 1, "files": []},
        {"id": 2, "type": "hex", "color": "ddeeff", "count": 1, "files": []},
    ]
    new_palette = [{"id": 1, "hex": "112233"}]
    resolved = [{"old_id": 1, "new_id": 1}, {"old_id": 2, "new_id": 1}]  # both converge on id 1

    collisions = cd.rekey_roles_after_apply(str(path), detected, new_palette, resolved)
    assert len(collisions) == 1
    new_key, old_keys = collisions[0]
    assert new_key == "hex:112233"
    assert set(old_keys) == {"hex:aabbcc", "hex:ddeeff"}
    assert cd.read_color_roles(str(path)) == {}  # left unmarked, not guessed


def test_rekey_roles_after_apply_convergence_with_same_role_is_not_a_collision(tmp_path):
    path = tmp_path / "color_roles.json"
    cd.write_color_roles(str(path), {"hex:aabbcc": "background", "hex:ddeeff": "background"})

    detected = [
        {"id": 1, "type": "hex", "color": "aabbcc", "count": 1, "files": []},
        {"id": 2, "type": "hex", "color": "ddeeff", "count": 1, "files": []},
    ]
    new_palette = [{"id": 1, "hex": "112233"}]
    resolved = [{"old_id": 1, "new_id": 1}, {"old_id": 2, "new_id": 1}]

    collisions = cd.rekey_roles_after_apply(str(path), detected, new_palette, resolved)
    assert collisions == []
    assert cd.read_color_roles(str(path)) == {"hex:112233": "background"}


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


def _detected(id_, type_, color):
    return {"id": id_, "type": type_, "color": color}


def test_compute_role_demand_filters_by_mapping_when_given():
    detected = [_detected(1, "hex", "111111"), _detected(2, "hex", "222222"), _detected(3, "hex", "333333")]
    roles = {
        cd.role_key("hex", "111111"): "background",
        cd.role_key("hex", "222222"): "foreground",
        cd.role_key("hex", "333333"): "foreground",  # tagged but NOT in the mapping below
    }
    mapping_entries = [{"old_id": 1, "new_id": 1}, {"old_id": 2, "new_id": 2}]
    n_bg, n_fg = cd.compute_role_demand(detected, roles, mapping_entries)
    assert (n_bg, n_fg) == (1, 1)  # id 3's tag doesn't count -- not mapped


def test_compute_role_demand_falls_back_to_unfiltered_roles_when_no_mapping():
    detected = [_detected(1, "hex", "111111"), _detected(2, "hex", "222222")]
    roles = {
        cd.role_key("hex", "111111"): "background",
        cd.role_key("hex", "222222"): "foreground",
    }
    assert cd.compute_role_demand(detected, roles, mapping_entries=None) == (1, 1)
    assert cd.compute_role_demand(detected, roles, mapping_entries=[]) == (1, 1)  # empty == no mapping


def test_compute_role_demand_zero_when_nothing_tagged():
    detected = [_detected(1, "hex", "111111")]
    assert cd.compute_role_demand(detected, {}, mapping_entries=[{"old_id": 1, "new_id": 1}]) == (0, 0)
    assert cd.compute_role_demand(detected, {}, mapping_entries=None) == (0, 0)


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
