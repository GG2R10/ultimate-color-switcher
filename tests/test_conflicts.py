from color_switcher.backend import conflicts


def _detected():
    return [
        {"id": 1, "type": "hex", "color": "00ccff", "count": 1, "files": []},
        {"id": 2, "type": "hex", "color": "ff00aa", "count": 1, "files": []},
        {"id": 3, "type": "hex_from_rgb", "color": "00ccff", "count": 1, "files": []},
    ]


def test_case1_detects_collision_with_another_detected_color():
    detected = _detected()
    palette = [
        {"id": 1, "hex": "ff00aa", "label": "collide"},  # equals detected id 2's hex
        {"id": 2, "hex": "112233", "label": "safe"},
    ]
    mapping = [{"old_id": 1, "new_id": 1}]  # map id1 (00ccff) -> ff00aa

    collisions = conflicts.find_case1_collisions(detected, palette, mapping)
    assert len(collisions) == 1
    assert collisions[0]["old_id"] == 1
    assert collisions[0]["new_hex"] == "ff00aa"
    assert collisions[0]["conflict_with_ids"] == [2]


def test_case1_no_collision_for_unique_target():
    detected = _detected()
    palette = [{"id": 1, "hex": "112233", "label": "safe"}]
    mapping = [{"old_id": 1, "new_id": 1}]
    assert conflicts.find_case1_collisions(detected, palette, mapping) == []


def test_case1_self_mapping_is_not_a_collision():
    # No case-2 twin here on purpose: id1 is the only detected entry with this hex.
    detected = [{"id": 1, "type": "hex", "color": "00ccff", "count": 1, "files": []}]
    palette = [{"id": 1, "hex": "00ccff", "label": "same"}]
    mapping = [{"old_id": 1, "new_id": 1}]  # id1 mapped back to its own current color
    assert conflicts.find_case1_collisions(detected, palette, mapping) == []


def test_case1_exempts_mapping_a_sibling_to_the_shared_current_color():
    # id1 (hex) and id3 (hex_from_rgb) are siblings sharing hex "00ccff".
    # Mapping id3 to that same shared value is a no-op for the pair, not a collision.
    detected = _detected()
    palette = [{"id": 1, "hex": "00ccff", "label": "same"}]
    mapping = [{"old_id": 3, "new_id": 1}]
    assert conflicts.find_case1_collisions(detected, palette, mapping) == []


def test_case1_ignores_unresolved_entries():
    detected = _detected()
    palette = [{"id": 1, "hex": "ff00aa", "label": "collide"}]
    mapping = [{"old_id": 1, "new_id": None}]
    assert conflicts.find_case1_collisions(detected, palette, mapping) == []


def test_case1_flags_multiple_conflicting_ids():
    detected = [
        {"id": 1, "type": "hex", "color": "aaaaaa", "count": 1, "files": []},
        {"id": 2, "type": "hex", "color": "bbbbbb", "count": 1, "files": []},
        {"id": 3, "type": "hex_from_rgb", "color": "bbbbbb", "count": 1, "files": []},
    ]
    palette = [{"id": 1, "hex": "bbbbbb", "label": "x"}]
    mapping = [{"old_id": 1, "new_id": 1}]
    collisions = conflicts.find_case1_collisions(detected, palette, mapping)
    assert collisions[0]["conflict_with_ids"] == [2, 3]


def test_case2_groups_only_multi_member_hexes():
    detected = _detected()
    groups = conflicts.find_case2_siblings(detected)

    assert set(groups.keys()) == {"00ccff"}
    assert {e["id"] for e in groups["00ccff"]} == {1, 3}


def test_case2_empty_when_no_shared_hex():
    detected = [{"id": 1, "type": "hex", "color": "aaaaaa", "count": 1, "files": []}]
    assert conflicts.find_case2_siblings(detected) == {}


def test_convergence_flags_two_different_reals_targeting_the_same_hex():
    detected = [
        {"id": 1, "type": "hex", "color": "111111", "count": 1, "files": []},
        {"id": 2, "type": "hex", "color": "222222", "count": 1, "files": []},
    ]
    palette = [{"id": 1, "hex": "999999", "label": "shared"}]
    mapping = [{"old_id": 1, "new_id": 1}, {"old_id": 2, "new_id": 1}]

    result = conflicts.find_target_convergence(detected, palette, mapping)
    assert len(result) == 1
    assert result[0]["target_hex"] == "999999"
    assert result[0]["old_ids"] == [1, 2]


def test_convergence_exempts_siblings_of_the_same_real_color():
    detected = _detected()  # id1 (hex) and id3 (hex_from_rgb) are the same real color
    palette = [{"id": 1, "hex": "999999", "label": "shared"}]
    mapping = [{"old_id": 1, "new_id": 1}, {"old_id": 3, "new_id": 1}]
    siblings = conflicts.find_case2_siblings(detected)

    assert conflicts.find_target_convergence(detected, palette, mapping, sibling_groups=siblings) == []


def test_convergence_ignores_unresolved_and_single_targets():
    detected = _detected()
    palette = [{"id": 1, "hex": "999999", "label": "shared"}]
    mapping = [{"old_id": 1, "new_id": 1}, {"old_id": 2, "new_id": None}]
    assert conflicts.find_target_convergence(detected, palette, mapping) == []


def test_role_mismatch_flags_foreground_mapped_to_background():
    detected = _detected()
    palette = [{"id": 1, "hex": "999999", "label": "bg", "role": "background"}]
    mapping = [{"old_id": 2, "new_id": 1}]  # detected id 2 is tagged foreground below
    roles = {conflicts.role_key("hex", "ff00aa"): "foreground"}

    result = conflicts.find_role_mismatches(detected, palette, mapping, roles)
    assert len(result) == 1
    assert result[0] == {"old_id": 2, "new_id": 1, "detected_role": "foreground", "palette_role": "background"}


def test_role_mismatch_ignores_agreeing_roles():
    detected = _detected()
    palette = [{"id": 1, "hex": "999999", "label": "bg", "role": "foreground"}]
    mapping = [{"old_id": 2, "new_id": 1}]
    roles = {conflicts.role_key("hex", "ff00aa"): "foreground"}
    assert conflicts.find_role_mismatches(detected, palette, mapping, roles) == []


def test_role_mismatch_ignores_when_either_side_unmarked():
    detected = _detected()
    palette_untagged = [{"id": 1, "hex": "999999", "label": "bg"}]
    mapping = [{"old_id": 2, "new_id": 1}]
    roles = {conflicts.role_key("hex", "ff00aa"): "foreground"}
    assert conflicts.find_role_mismatches(detected, palette_untagged, mapping, roles) == []

    palette_tagged = [{"id": 1, "hex": "999999", "label": "bg", "role": "background"}]
    assert conflicts.find_role_mismatches(detected, palette_tagged, mapping, {}) == []
