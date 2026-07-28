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


def _role(role, pair=None):
    return {"role": role, "pair": pair}


def test_read_color_roles_missing_file_returns_empty(tmp_path):
    assert cd.read_color_roles(str(tmp_path / "color_roles.json")) == {}


def test_write_read_color_roles_roundtrip(tmp_path):
    path = tmp_path / "color_roles.json"
    cd.write_color_roles(str(path), {"hex:aabbcc": _role("background"), "hex:112233": _role("foreground")})
    assert cd.read_color_roles(str(path)) == {
        "hex:aabbcc": _role("background"), "hex:112233": _role("foreground"),
    }


def test_write_color_roles_drops_unmarked_and_invalid_entries(tmp_path):
    path = tmp_path / "color_roles.json"
    cd.write_color_roles(str(path), {
        "hex:aabbcc": _role("background"), "hex:ffffff": None, "hex:000000": _role("bogus"),
    })
    assert cd.read_color_roles(str(path)) == {"hex:aabbcc": _role("background")}


def test_read_color_roles_tolerates_malformed_json(tmp_path):
    path = tmp_path / "color_roles.json"
    path.write_text("{not valid json")
    assert cd.read_color_roles(str(path)) == {}


def test_read_color_roles_tolerates_non_dict_json(tmp_path):
    path = tmp_path / "color_roles.json"
    path.write_text("[1, 2, 3]")
    assert cd.read_color_roles(str(path)) == {}


def test_read_color_roles_migrates_old_bare_string_shape(tmp_path):
    # Old format: {"type:hex": "foreground"|"background"}, no pairing concept
    # at all -- must upgrade transparently, never crash, never lose the role.
    path = tmp_path / "color_roles.json"
    path.write_text('{"hex:aabbcc": "background", "hex:112233": "foreground"}')
    assert cd.read_color_roles(str(path)) == {
        "hex:aabbcc": _role("background"), "hex:112233": _role("foreground"),
    }


def test_read_color_roles_nulls_dangling_or_invalid_pair(tmp_path):
    path = tmp_path / "color_roles.json"
    path.write_text(
        '{"hex:aaaaaa": {"role": "foreground", "pair": "hex:bbbbbb"},'
        ' "hex:cccccc": {"role": "background", "pair": "hex:dddddd"}}'
        # aaaaaa's pair points at nothing -> nulled. cccccc's pair is on a
        # background entry, which never carries a meaningful pair -> nulled.
    )
    roles = cd.read_color_roles(str(path))
    assert roles["hex:aaaaaa"]["pair"] is None
    assert roles["hex:cccccc"]["pair"] is None


def test_pair_of_and_role_of_helpers():
    roles = {
        "hex:aaaaaa": _role("background"),
        "hex:bbbbbb": _role("foreground", pair="hex:aaaaaa"),
    }
    assert cd.role_of(roles, "hex:aaaaaa") == "background"
    assert cd.role_of(roles, "hex:zzzzzz") is None
    assert cd.pair_of(roles, "hex:bbbbbb") == "hex:aaaaaa"
    assert cd.pair_of(roles, "hex:aaaaaa") is None  # background: never meaningful


def test_set_pair_links_and_unlinks():
    roles = {"hex:aaaaaa": _role("background"), "hex:bbbbbb": _role("foreground")}
    linked = cd.set_pair(roles, "hex:bbbbbb", "hex:aaaaaa")
    assert linked["hex:bbbbbb"]["pair"] == "hex:aaaaaa"
    assert roles["hex:bbbbbb"]["pair"] is None  # original untouched

    unlinked = cd.set_pair(linked, "hex:bbbbbb", None)
    assert unlinked["hex:bbbbbb"]["pair"] is None


def test_set_pair_is_a_no_op_on_wrong_roles():
    roles = {"hex:aaaaaa": _role("background"), "hex:bbbbbb": _role("foreground")}
    # fg_key isn't foreground
    assert cd.set_pair(roles, "hex:aaaaaa", "hex:bbbbbb")["hex:aaaaaa"]["pair"] is None
    # bg_key isn't background
    assert cd.set_pair(roles, "hex:bbbbbb", "hex:bbbbbb")["hex:bbbbbb"]["pair"] is None
    # either key missing entirely
    assert cd.set_pair(roles, "hex:zzzzzz", "hex:aaaaaa") == roles


def test_backgrounds_used_as_pair_targets():
    roles = {
        "hex:aaaaaa": _role("background"),
        "hex:bbbbbb": _role("foreground", pair="hex:aaaaaa"),
        "hex:cccccc": _role("foreground", pair="hex:aaaaaa"),
        "hex:dddddd": _role("foreground"),
    }
    assert cd.backgrounds_used_as_pair_targets(roles) == {"hex:aaaaaa"}


def test_backgrounds_used_as_pair_targets_matches_hex_and_rgb_sibling():
    # Real bug: a background detected in BOTH representations only ever
    # gets ONE of them stored as some foreground's `pair` (whichever was
    # picked as canonical, e.g. by the GUI's linking dropdown) -- the OTHER
    # representation must still count as "used", not flagged as unpaired.
    roles = {
        "hex:aaaaaa": _role("background"),
        "hex_from_rgb:aaaaaa": _role("background"),  # same real color, other representation
        "hex:bbbbbb": _role("foreground", pair="hex:aaaaaa"),  # only points at ONE representation
    }
    assert cd.backgrounds_used_as_pair_targets(roles) == {"hex:aaaaaa", "hex_from_rgb:aaaaaa"}


def test_clear_dangling_pairs_after_role_change_own_pair():
    roles = {"hex:aaaaaa": _role("background"), "hex:bbbbbb": _role("foreground", pair="hex:aaaaaa")}
    cleared = cd.clear_dangling_pairs_after_role_change(roles, "hex:bbbbbb", "background")
    assert cleared["hex:bbbbbb"]["pair"] is None


def test_clear_dangling_pairs_after_role_change_others_pointing_at_it():
    roles = {
        "hex:aaaaaa": _role("background"),
        "hex:bbbbbb": _role("foreground", pair="hex:aaaaaa"),
        "hex:cccccc": _role("foreground", pair="hex:aaaaaa"),
    }
    cleared = cd.clear_dangling_pairs_after_role_change(roles, "hex:aaaaaa", "foreground")
    assert cleared["hex:bbbbbb"]["pair"] is None
    assert cleared["hex:cccccc"]["pair"] is None


def test_detected_id_for_role_key():
    detected = [_detected(1, "hex", "aabbcc"), _detected(2, "hex", "ddeeff")]
    assert cd.detected_id_for_role_key(detected, cd.role_key("hex", "aabbcc")) == 1
    assert cd.detected_id_for_role_key(detected, cd.role_key("hex", "999999")) is None


def test_rekey_roles_after_apply_carries_role_to_new_hex(tmp_path):
    path = tmp_path / "color_roles.json"
    cd.write_color_roles(str(path), {"hex:aabbcc": _role("background")})

    detected = [{"id": 1, "type": "hex", "color": "aabbcc", "count": 1, "files": []}]
    new_palette = [{"id": 1, "hex": "112233"}]
    resolved = [{"old_id": 1, "new_id": 1}]

    role_collisions, pair_collisions = cd.rekey_roles_after_apply(str(path), detected, new_palette, resolved)
    assert role_collisions == [] and pair_collisions == []
    assert cd.read_color_roles(str(path)) == {"hex:112233": _role("background")}


def test_rekey_roles_after_apply_remaps_pair_when_bg_is_rekeyed(tmp_path):
    path = tmp_path / "color_roles.json"
    cd.write_color_roles(str(path), {
        "hex:aabbcc": _role("background"),
        "hex:ffffff": _role("foreground", pair="hex:aabbcc"),
    })

    detected = [
        {"id": 1, "type": "hex", "color": "aabbcc", "count": 1, "files": []},
        {"id": 2, "type": "hex", "color": "ffffff", "count": 1, "files": []},
    ]
    new_palette = [{"id": 1, "hex": "112233"}, {"id": 2, "hex": "eeeeee"}]
    resolved = [{"old_id": 1, "new_id": 1}, {"old_id": 2, "new_id": 2}]

    role_collisions, pair_collisions = cd.rekey_roles_after_apply(str(path), detected, new_palette, resolved)
    assert role_collisions == [] and pair_collisions == []
    roles = cd.read_color_roles(str(path))
    assert roles["hex:eeeeee"]["pair"] == "hex:112233"


def test_rekey_roles_after_apply_leaves_unrelated_roles_untouched(tmp_path):
    path = tmp_path / "color_roles.json"
    cd.write_color_roles(str(path), {"hex:aabbcc": _role("background"), "hex:ffffff": _role("foreground")})

    detected = [{"id": 1, "type": "hex", "color": "aabbcc", "count": 1, "files": []}]
    new_palette = [{"id": 1, "hex": "112233"}]
    resolved = [{"old_id": 1, "new_id": 1}]

    cd.rekey_roles_after_apply(str(path), detected, new_palette, resolved)
    assert cd.read_color_roles(str(path)) == {
        "hex:112233": _role("background"), "hex:ffffff": _role("foreground"),
    }


def test_rekey_roles_after_apply_no_op_when_nothing_tagged(tmp_path):
    path = tmp_path / "color_roles.json"
    detected = [{"id": 1, "type": "hex", "color": "aabbcc", "count": 1, "files": []}]
    new_palette = [{"id": 1, "hex": "112233"}]
    resolved = [{"old_id": 1, "new_id": 1}]

    role_collisions, pair_collisions = cd.rekey_roles_after_apply(str(path), detected, new_palette, resolved)
    assert role_collisions == [] and pair_collisions == []
    assert not path.exists()  # nothing to write, no file created


def test_rekey_roles_after_apply_same_hex_is_a_no_op(tmp_path):
    path = tmp_path / "color_roles.json"
    cd.write_color_roles(str(path), {"hex:aabbcc": _role("foreground")})
    detected = [{"id": 1, "type": "hex", "color": "aabbcc", "count": 1, "files": []}]
    new_palette = [{"id": 1, "hex": "aabbcc"}]  # mapped to itself, unchanged
    resolved = [{"old_id": 1, "new_id": 1}]

    role_collisions, pair_collisions = cd.rekey_roles_after_apply(str(path), detected, new_palette, resolved)
    assert role_collisions == [] and pair_collisions == []
    assert cd.read_color_roles(str(path)) == {"hex:aabbcc": _role("foreground")}


def test_rekey_roles_after_apply_convergence_with_different_roles_is_left_unmarked(tmp_path):
    path = tmp_path / "color_roles.json"
    cd.write_color_roles(str(path), {"hex:aabbcc": _role("background"), "hex:ddeeff": _role("foreground")})

    detected = [
        {"id": 1, "type": "hex", "color": "aabbcc", "count": 1, "files": []},
        {"id": 2, "type": "hex", "color": "ddeeff", "count": 1, "files": []},
    ]
    new_palette = [{"id": 1, "hex": "112233"}]
    resolved = [{"old_id": 1, "new_id": 1}, {"old_id": 2, "new_id": 1}]  # both converge on id 1

    role_collisions, pair_collisions = cd.rekey_roles_after_apply(str(path), detected, new_palette, resolved)
    assert len(role_collisions) == 1
    new_key, old_keys = role_collisions[0]
    assert new_key == "hex:112233"
    assert set(old_keys) == {"hex:aabbcc", "hex:ddeeff"}
    assert pair_collisions == []
    assert cd.read_color_roles(str(path)) == {}  # left unmarked, not guessed


def test_rekey_roles_after_apply_convergence_with_same_role_is_not_a_collision(tmp_path):
    path = tmp_path / "color_roles.json"
    cd.write_color_roles(str(path), {"hex:aabbcc": _role("background"), "hex:ddeeff": _role("background")})

    detected = [
        {"id": 1, "type": "hex", "color": "aabbcc", "count": 1, "files": []},
        {"id": 2, "type": "hex", "color": "ddeeff", "count": 1, "files": []},
    ]
    new_palette = [{"id": 1, "hex": "112233"}]
    resolved = [{"old_id": 1, "new_id": 1}, {"old_id": 2, "new_id": 1}]

    role_collisions, pair_collisions = cd.rekey_roles_after_apply(str(path), detected, new_palette, resolved)
    assert role_collisions == [] and pair_collisions == []
    assert cd.read_color_roles(str(path)) == {"hex:112233": _role("background")}


def test_rekey_roles_after_apply_pair_dangles_and_clears_when_bg_collides_away(tmp_path):
    path = tmp_path / "color_roles.json"
    cd.write_color_roles(str(path), {
        "hex:aabbcc": _role("background"),
        "hex:ddeeff": _role("foreground"),  # different role, will collide with aabbcc
        "hex:ffffff": _role("foreground", pair="hex:aabbcc"),
    })

    detected = [
        {"id": 1, "type": "hex", "color": "aabbcc", "count": 1, "files": []},
        {"id": 2, "type": "hex", "color": "ddeeff", "count": 1, "files": []},
        {"id": 3, "type": "hex", "color": "ffffff", "count": 1, "files": []},
    ]
    new_palette = [{"id": 1, "hex": "112233"}, {"id": 3, "hex": "eeeeee"}]
    # ids 1 and 2 both converge onto new hex 112233 -- role collision there
    resolved = [{"old_id": 1, "new_id": 1}, {"old_id": 2, "new_id": 1}, {"old_id": 3, "new_id": 3}]

    role_collisions, pair_collisions = cd.rekey_roles_after_apply(str(path), detected, new_palette, resolved)
    assert len(role_collisions) == 1
    assert pair_collisions == [("hex:eeeeee", "hex:aabbcc")]
    roles = cd.read_color_roles(str(path))
    assert roles["hex:eeeeee"]["pair"] is None


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


def test_compute_role_pairs_resolves_lab_l_from_hex():
    detected = [_detected(1, "hex", "111111"), _detected(2, "hex", "eeeeee")]
    roles = {
        cd.role_key("hex", "111111"): _role("background"),
        cd.role_key("hex", "eeeeee"): _role("foreground", pair="hex:111111"),
    }
    pairs = cd.compute_role_pairs(detected, roles)
    assert len(pairs) == 1
    p = pairs[0]
    assert p["bg_l"] < p["fg_l"]  # 111111 is dark, eeeeee is light


def test_compute_role_pairs_filters_by_mapping_when_given():
    detected = [_detected(1, "hex", "111111"), _detected(2, "hex", "eeeeee"), _detected(3, "hex", "ff0000")]
    roles = {
        cd.role_key("hex", "111111"): _role("background"),
        cd.role_key("hex", "eeeeee"): _role("foreground", pair="hex:111111"),
        cd.role_key("hex", "ff0000"): _role("foreground", pair="hex:111111"),  # not in the mapping below
    }
    mapping_entries = [{"old_id": 1, "new_id": 1}, {"old_id": 2, "new_id": 2}]
    pairs = cd.compute_role_pairs(detected, roles, mapping_entries)
    assert len(pairs) == 1  # id 3's pair doesn't count -- not mapped


def test_compute_role_pairs_falls_back_to_unfiltered_when_no_mapping():
    detected = [_detected(1, "hex", "111111"), _detected(2, "hex", "eeeeee")]
    roles = {
        cd.role_key("hex", "111111"): _role("background"),
        cd.role_key("hex", "eeeeee"): _role("foreground", pair="hex:111111"),
    }
    assert len(cd.compute_role_pairs(detected, roles, mapping_entries=None)) == 1
    assert len(cd.compute_role_pairs(detected, roles, mapping_entries=[])) == 1


def test_compute_role_pairs_ignores_tagged_but_unpaired():
    detected = [_detected(1, "hex", "111111"), _detected(2, "hex", "eeeeee")]
    roles = {
        cd.role_key("hex", "111111"): _role("background"),
        cd.role_key("hex", "eeeeee"): _role("foreground"),  # no pair set
    }
    assert cd.compute_role_pairs(detected, roles) == []


def test_compute_role_pairs_empty_when_nothing_tagged():
    detected = [_detected(1, "hex", "111111")]
    assert cd.compute_role_pairs(detected, {}) == []


def test_compute_role_pairs_dedupes_hex_and_rgb_siblings():
    # A real color detected as BOTH "hex" and "hex_from_rgb" gets tagged and
    # paired on EACH representation independently (role/pairing follow "a
    # group is one identity" in the GUI) -- must still count as ONE pair,
    # not two, or generation would demand double the palette slots.
    detected = [
        _detected(1, "hex", "111111"),
        _detected(2, "hex_from_rgb", "111111"),  # sibling of id 1
        _detected(3, "hex", "eeeeee"),
        _detected(4, "hex_from_rgb", "eeeeee"),  # sibling of id 3
    ]
    roles = {
        cd.role_key("hex", "111111"): _role("background"),
        cd.role_key("hex_from_rgb", "111111"): _role("background"),
        cd.role_key("hex", "eeeeee"): _role("foreground", pair="hex:111111"),
        cd.role_key("hex_from_rgb", "eeeeee"): _role("foreground", pair="hex_from_rgb:111111"),
    }
    pairs = cd.compute_role_pairs(detected, roles)
    assert len(pairs) == 1


def test_tagged_without_pair_flags_unpaired_fg_and_unused_bg():
    roles = {
        "hex:aaaaaa": _role("background"),  # never used as a pair target
        "hex:bbbbbb": _role("background"),  # used
        "hex:cccccc": _role("foreground", pair="hex:bbbbbb"),
        "hex:dddddd": _role("foreground"),  # no pair at all
    }
    assert set(cd.tagged_without_pair(roles)) == {"hex:aaaaaa", "hex:dddddd"}


def test_tagged_without_pair_empty_when_everything_is_paired():
    roles = {
        "hex:aaaaaa": _role("background"),
        "hex:bbbbbb": _role("foreground", pair="hex:aaaaaa"),
    }
    assert cd.tagged_without_pair(roles) == []


def test_tagged_without_pair_dedupes_hex_and_rgb_siblings():
    roles = {
        "hex:dddddd": _role("foreground"),  # unpaired
        "hex_from_rgb:dddddd": _role("foreground"),  # same real color, also unpaired
    }
    assert len(cd.tagged_without_pair(roles)) == 1


def test_tagged_without_pair_does_not_flag_paired_backgrounds_rgb_sibling():
    # Reproduces the user's real report: a bg+fg pair, BOTH sides detected
    # in hex and hex_from_rgb form, linked via the GUI (which only ever
    # stores ONE representative key as the fg's `pair`) -- neither side
    # should show up as "sin pareja", even the un-referenced sibling key.
    roles = {
        "hex:aaaaaa": _role("background"),
        "hex_from_rgb:aaaaaa": _role("background"),
        "hex:bbbbbb": _role("foreground", pair="hex:aaaaaa"),
        "hex_from_rgb:bbbbbb": _role("foreground", pair="hex:aaaaaa"),
    }
    assert cd.tagged_without_pair(roles) == []


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
