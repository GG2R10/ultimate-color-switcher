from color_switcher.backend import mapping_store as ms


def test_insertion_order_preserved_across_retargeting(tmp_path):
    path = tmp_path / "mapping.csv"
    store = ms.MappingStore(str(path), old_palette="detected.csv", new_palette="palette.csv")

    store.add_or_update(5, 1)
    store.add_or_update(2, 2)
    store.add_or_update(9, 3)
    store.add_or_update(2, 20)  # re-target an existing entry: position must not move

    assert [e["old_id"] for e in store.entries] == [5, 2, 9]
    assert store._find(2)["new_id"] == 20


def test_unresolved_entries_not_persisted_until_resolved(tmp_path):
    path = tmp_path / "mapping.csv"
    store = ms.MappingStore(str(path), old_palette="d.csv", new_palette="p.csv")
    store.add_or_update(1, None)  # selected but not yet assigned

    assert path.read_text().splitlines()[-1] == "old_id,new_id"  # no data row written
    assert store.unresolved_ids() == [1]
    assert store.resolved_entries() == []

    store.add_or_update(1, 7)
    assert path.read_text().splitlines()[-1] == "1,7"

    reloaded = ms.MappingStore(str(path)).load()
    assert reloaded.resolved_entries() == [{"old_id": 1, "new_id": 7}]


def test_link_copies_current_target_once(tmp_path):
    path = tmp_path / "mapping.csv"
    store = ms.MappingStore(str(path), old_palette="d.csv", new_palette="p.csv")
    store.add_or_update(1, 4)
    store.link(8, link_to_old_id=1)

    assert store._find(8)["new_id"] == 4

    # not a live binding: changing id 1's target later does not move id 8
    store.add_or_update(1, 99)
    assert store._find(8)["new_id"] == 4


def test_load_reads_back_same_order_and_headers(tmp_path):
    path = tmp_path / "mapping.csv"
    store = ms.MappingStore(str(path), old_palette="d.csv", new_palette="p.csv")
    for old_id, new_id in [(3, 1), (1, 2), (2, 3)]:
        store.add_or_update(old_id, new_id)

    reloaded = ms.MappingStore(str(path)).load()
    assert [e["old_id"] for e in reloaded.entries] == [3, 1, 2]
    assert reloaded.old_palette == "d.csv"
    assert reloaded.new_palette == "p.csv"


def test_remove_persists(tmp_path):
    path = tmp_path / "mapping.csv"
    store = ms.MappingStore(str(path), old_palette="d.csv", new_palette="p.csv")
    store.add_or_update(1, 1)
    store.add_or_update(2, 2)
    store.remove(1)

    assert [e["old_id"] for e in store.entries] == [2]
    reloaded = ms.MappingStore(str(path)).load()
    assert [e["old_id"] for e in reloaded.entries] == [2]


def test_write_mapping_csv_stays_legacy_2column_when_nothing_stamped(tmp_path):
    path = tmp_path / "mapping.csv"
    entries = [{"old_id": 1, "new_id": 1}]
    ms.write_mapping_csv(str(path), "d.csv", "p.csv", entries)
    lines = path.read_text().splitlines()
    assert lines[2] == "old_id,new_id"
    assert lines[3] == "1,1"


def test_write_mapping_csv_emits_stamp_columns_when_any_entry_is_stamped(tmp_path):
    path = tmp_path / "mapping.csv"
    entries = [{"old_id": 1, "new_id": 1}, {"old_id": 2, "new_id": 2, "old_type": "hex", "old_hex": "aabbcc"}]
    ms.write_mapping_csv(str(path), "d.csv", "p.csv", entries)
    lines = path.read_text().splitlines()
    assert lines[2] == "old_id,new_id,old_type,old_hex"
    assert lines[3] == "1,1,,"
    assert lines[4] == "2,2,hex,aabbcc"

    _, _, reread = ms.read_mapping_csv(str(path))
    assert "old_type" not in reread[0]
    assert reread[1]["old_type"] == "hex" and reread[1]["old_hex"] == "aabbcc"


def test_read_mapping_csv_missing_file(tmp_path):
    old_p, new_p, entries = ms.read_mapping_csv(str(tmp_path / "nope.csv"))
    assert old_p is None and new_p is None and entries == []


def test_new_mapping_path_lands_in_mappings_dir(tmp_path):
    path = ms.new_mapping_path(str(tmp_path))
    assert path.startswith(str(tmp_path))
    assert path.endswith(".csv")
    assert "mapping-" in path


def test_write_stores_paths_relative_to_project_dir_when_inside_it(tmp_path):
    project_dir = tmp_path / "project"
    (project_dir / "palettes" / "detected").mkdir(parents=True)
    (project_dir / "palettes" / "created").mkdir(parents=True)
    old_palette = project_dir / "palettes" / "detected" / "detected_palette.csv"
    new_palette = project_dir / "palettes" / "created" / "theme.csv"

    mapping_path = project_dir / "mappings" / "mapping.csv"
    store = ms.MappingStore(
        str(mapping_path), old_palette=str(old_palette), new_palette=str(new_palette),
        project_dir=str(project_dir),
    )
    store.add_or_update(1, 1)

    content = mapping_path.read_text()
    assert "#old_palette=palettes/detected/detected_palette.csv" in content
    assert "#new_palette=palettes/created/theme.csv" in content
    assert str(project_dir) not in content  # no absolute, machine-specific path leaked in


def test_read_resolves_relative_paths_against_project_dir(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    mapping_path = project_dir / "mappings" / "mapping.csv"
    mapping_path.parent.mkdir()
    mapping_path.write_text(
        "#old_palette=palettes/detected/detected_palette.csv\n"
        "#new_palette=palettes/created/theme.csv\n"
        "old_id,new_id\n1,1\n"
    )

    old_p, new_p, entries = ms.read_mapping_csv(str(mapping_path), project_dir=str(project_dir))
    assert old_p == str(project_dir / "palettes" / "detected" / "detected_palette.csv")
    assert new_p == str(project_dir / "palettes" / "created" / "theme.csv")
    assert entries == [{"old_id": 1, "new_id": 1}]


def test_paths_outside_project_dir_stay_absolute(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    external_palette = tmp_path / "elsewhere" / "detected.csv"
    external_palette.parent.mkdir()

    mapping_path = project_dir / "mapping.csv"
    store = ms.MappingStore(
        str(mapping_path), old_palette=str(external_palette), new_palette="unused.csv",
        project_dir=str(project_dir),
    )
    store.add_or_update(1, 1)

    content = mapping_path.read_text()
    assert f"#old_palette={external_palette}" in content


def _palette(n):
    return [{"id": i + 1, "hex": f"{i:06x}", "label": ""} for i in range(n)]


def _detected(*id_type_hex):
    return [{"id": i, "type": t, "color": h} for i, t, h in id_type_hex]


def test_stamp_detected_identity_sets_type_and_hex():
    entries = [{"old_id": 1, "new_id": 5}]
    detected = _detected((1, "hex", "aabbcc"))
    stamped = ms.stamp_detected_identity(entries, detected)
    assert stamped[0]["old_type"] == "hex"
    assert stamped[0]["old_hex"] == "aabbcc"
    assert entries[0].get("old_type") is None  # input untouched


def test_stamp_detected_identity_leaves_unresolvable_old_id_untouched():
    entries = [{"old_id": 99, "new_id": 5}]
    stamped = ms.stamp_detected_identity(entries, _detected((1, "hex", "aabbcc")))
    assert "old_type" not in stamped[0]


def test_stamp_applied_entries_sets_the_new_color_not_the_old_one():
    """The core regression test for the reported bug: right after a real
    apply, an entry's stamp must describe the color that's now ACTUALLY in
    the files (the palette color that was just written), not the old
    detected color (which, by design, no longer exists -- that's what
    "applying" means). Getting this backwards makes every fresh apply
    self-orphan its own mapping on the very next detection."""
    entries = [{"old_id": 1, "new_id": 1, "old_type": "hex", "old_hex": "aabbcc"}]
    applied_entries = [{"old_id": 1, "new_id": 1}]  # what was ACTUALLY applied (post tier resolution)
    new_palette = [{"id": 1, "hex": "ff00aa", "label": ""}]
    stamped = ms.stamp_applied_entries(entries, applied_entries, new_palette)
    assert stamped[0]["old_hex"] == "ff00aa"
    assert stamped[0]["old_type"] == "hex"  # representation format unchanged


def test_stamp_applied_entries_uses_the_compacted_new_id_not_the_stored_one():
    """After a tier="compacted" apply, the mapping's STORED new_id (e.g. 99)
    may differ from what was actually applied (e.g. slot 1, per
    resolve_apply_targets) -- the stamp must reflect what ACTUALLY landed in
    the files, not the stored (and now meaningless) new_id."""
    entries = [{"old_id": 1, "new_id": 99}]  # stored value, never touched by this
    applied_entries = [{"old_id": 1, "new_id": 1}]  # what tier="compacted" actually applied
    new_palette = [{"id": 1, "hex": "112233", "label": ""}]
    stamped = ms.stamp_applied_entries(entries, applied_entries, new_palette)
    assert stamped[0]["new_id"] == 99  # stored new_id untouched
    assert stamped[0]["old_hex"] == "112233"  # stamp follows what was actually applied


def test_stamp_applied_entries_infers_old_type_from_detected_colors_when_unstamped():
    entries = [{"old_id": 1, "new_id": 1}]  # never stamped before (first-ever apply)
    applied_entries = [{"old_id": 1, "new_id": 1}]
    new_palette = [{"id": 1, "hex": "ff00aa", "label": ""}]
    detected = [{"id": 1, "type": "hex_from_rgb", "color": "111111"}]
    stamped = ms.stamp_applied_entries(entries, applied_entries, new_palette, detected)
    assert stamped[0]["old_type"] == "hex_from_rgb"
    assert stamped[0]["old_hex"] == "ff00aa"


def test_stamp_applied_entries_leaves_unrelated_entries_untouched():
    entries = [{"old_id": 1, "new_id": 1, "old_type": "hex", "old_hex": "aabbcc"},
               {"old_id": 2, "new_id": 2, "old_type": "hex", "old_hex": "ddeeff"}]
    applied_entries = [{"old_id": 1, "new_id": 1}]  # only old_id 1 was actually applied this run
    new_palette = [{"id": 1, "hex": "ff00aa", "label": ""}]
    stamped = ms.stamp_applied_entries(entries, applied_entries, new_palette)
    by_old_id = {e["old_id"]: e for e in stamped}
    assert by_old_id[1]["old_hex"] == "ff00aa"
    assert by_old_id[2]["old_hex"] == "ddeeff"  # untouched


def test_detect_drift_classifies_ok_driftable_orphaned():
    entries = [
        {"old_id": 1, "new_id": 1, "old_type": "hex", "old_hex": "aabbcc"},  # still at id 1 -> ok
        {"old_id": 2, "new_id": 2, "old_type": "hex", "old_hex": "ddeeff"},  # now at id 3 -> driftable
        {"old_id": 4, "new_id": 3, "old_type": "hex", "old_hex": "112233"},  # gone entirely -> orphaned
    ]
    detected = _detected((1, "hex", "aabbcc"), (3, "hex", "ddeeff"))
    drift = ms.detect_drift(entries, detected)
    assert drift["ok"] == [1]
    assert drift["driftable"] == [{"old_id": 2, "correct_old_id": 3, "type": "hex", "hex": "ddeeff"}]
    assert drift["orphaned"] == [{"old_id": 4, "type": "hex", "hex": "112233"}]


def test_detect_drift_skips_unstamped_entries():
    entries = [{"old_id": 1, "new_id": 1}]  # never stamped
    drift = ms.detect_drift(entries, _detected((1, "hex", "aabbcc")))
    assert drift == {"ok": [], "driftable": [], "orphaned": []}


def test_refresh_identity_stamps_never_restamps_drifted_or_orphaned_entries():
    entries = [
        {"old_id": 1, "new_id": 1},  # unstamped -> gets stamped fresh
        {"old_id": 2, "new_id": 2, "old_type": "hex", "old_hex": "ddeeff"},  # driftable (now at id 3)
        {"old_id": 4, "new_id": 3, "old_type": "hex", "old_hex": "112233"},  # orphaned
    ]
    detected = _detected((1, "hex", "aabbcc"), (3, "hex", "ddeeff"))
    new_entries, drift = ms.refresh_identity_stamps(entries, detected)

    by_old_id = {e["old_id"]: e for e in new_entries}
    assert by_old_id[1]["old_type"] == "hex" and by_old_id[1]["old_hex"] == "aabbcc"
    # driftable/orphaned entries kept EXACTLY as they were -- not silently re-stamped
    assert by_old_id[2]["old_hex"] == "ddeeff"
    assert by_old_id[4]["old_hex"] == "112233"
    assert drift["driftable"][0]["old_id"] == 2
    assert drift["orphaned"][0]["old_id"] == 4


def test_apply_drift_relinks_rekeys_without_touching_new_id():
    entries = [{"old_id": 2, "new_id": 7, "old_type": "hex", "old_hex": "ddeeff"}]
    driftable = [{"old_id": 2, "correct_old_id": 3, "type": "hex", "hex": "ddeeff"}]
    new_entries = ms.apply_drift_relinks(entries, driftable)
    assert new_entries == [{"old_id": 3, "new_id": 7, "old_type": "hex", "old_hex": "ddeeff"}]
    assert entries[0]["old_id"] == 2  # input untouched


def test_apply_drift_relinks_resolves_a_two_way_rank_swap_atomically():
    """The core regression test: a naive sequential relink (process each
    driftable finding one at a time against a mutating store) corrupts a
    rank swap, because each entry is "in the way" of the other's target id
    until BOTH have moved. The atomic batch remap must get this right in a
    single pass."""
    entries = [
        {"old_id": 1, "new_id": 1, "old_type": "hex", "old_hex": "111111"},
        {"old_id": 2, "new_id": 2, "old_type": "hex", "old_hex": "222222"},
    ]
    driftable = [
        {"old_id": 1, "correct_old_id": 2, "type": "hex", "hex": "111111"},
        {"old_id": 2, "correct_old_id": 1, "type": "hex", "hex": "222222"},
    ]
    new_entries = ms.apply_drift_relinks(entries, driftable)
    by_hex = {e["old_hex"]: e["old_id"] for e in new_entries}
    assert by_hex == {"111111": 2, "222222": 1}


def test_mappingstore_apply_drift_relinks_persists_and_counts(tmp_path):
    path = tmp_path / "mapping.csv"
    store = ms.MappingStore(str(path), old_palette="d.csv", new_palette="p.csv")
    store.add_or_update(1, 1)
    store.add_or_update(2, 2)
    store.entries[0].update(old_type="hex", old_hex="111111")
    store.entries[1].update(old_type="hex", old_hex="222222")

    driftable = [
        {"old_id": 1, "correct_old_id": 2, "type": "hex", "hex": "111111"},
        {"old_id": 2, "correct_old_id": 1, "type": "hex", "hex": "222222"},
    ]
    count = store.apply_drift_relinks(driftable)
    assert count == 2

    reloaded = ms.MappingStore(str(path)).load()
    by_hex = {e["old_hex"]: e["old_id"] for e in reloaded.entries}
    assert by_hex == {"111111": 2, "222222": 1}


def test_resolve_apply_targets_exact_tier_when_palette_covers_max_new_id():
    entries = [{"old_id": 1, "new_id": 3}, {"old_id": 2, "new_id": 2}, {"old_id": 3, "new_id": 1}]
    result = ms.resolve_apply_targets(entries, _palette(5))
    assert result["tier"] == "exact"
    assert result["final_entries"] == entries
    assert result["needed"] == 3
    assert result["warning"] is None


def test_resolve_apply_targets_compacted_tier_sorts_by_value_not_insertion_order():
    """The core regression test: entries are built in an order where
    insertion order and ascending new_id value diverge -- the compacted
    tier must follow VALUE order, matching resolve_apply_targets' contract
    (this is the actual fix for "el orden del mapping se modifica")."""
    entries = [{"old_id": 1, "new_id": 99}, {"old_id": 2, "new_id": 2}, {"old_id": 3, "new_id": 4}]
    result = ms.resolve_apply_targets(entries, _palette(3))
    assert result["tier"] == "compacted"
    assert result["needed"] == 3  # 3 distinct values: {2, 4, 99}
    assert result["warning"] is not None
    by_old_id = {e["old_id"]: e["new_id"] for e in result["final_entries"]}
    assert by_old_id == {1: 3, 2: 1, 3: 2}  # 2->1, 4->2, 99->3 (ascending value order)


def test_resolve_apply_targets_blocked_tier_needed_is_distinct_count():
    entries = [{"old_id": 1, "new_id": 1}, {"old_id": 2, "new_id": 99}]
    result = ms.resolve_apply_targets(entries, _palette(1))
    assert result["tier"] == "blocked"
    assert result["final_entries"] == []
    assert result["needed"] == 2  # 2 distinct values, not max_new_id(99)
    assert result["available"] == 1
    assert result["max_new_id"] == 99


def test_resolve_apply_targets_empty_entries_is_a_trivial_exact_tier():
    result = ms.resolve_apply_targets([], _palette(3))
    assert result["tier"] == "exact"
    assert result["final_entries"] == []
    assert result["needed"] == 0


def test_mappingstore_save_load_roundtrip_with_project_dir(tmp_path):
    project_dir = tmp_path / "project"
    (project_dir / "palettes").mkdir(parents=True)
    old_palette = project_dir / "palettes" / "detected.csv"

    mapping_path = project_dir / "mapping.csv"
    store = ms.MappingStore(
        str(mapping_path), old_palette=str(old_palette), new_palette="unused.csv",
        project_dir=str(project_dir),
    )
    store.add_or_update(1, 1)

    reloaded = ms.MappingStore(str(mapping_path), project_dir=str(project_dir)).load()
    assert reloaded.old_palette == str(old_palette)  # absolute again after load, despite relative on disk


# --------------------------------------------------------------------------- #
# MappingRegistry -- one mapping section per palette + active pointer
# --------------------------------------------------------------------------- #

def test_registry_for_palette_creates_and_persists_a_section(tmp_path):
    registry_path = tmp_path / "mappings" / "mappings.json"
    reg = ms.MappingRegistry(str(registry_path), project_dir=str(tmp_path))
    store = reg.for_palette(str(tmp_path / "palettes" / "a.csv"), old_palette=str(tmp_path / "detected.csv"))
    store.add_or_update(1, 5)

    assert registry_path.is_file()
    reloaded = ms.MappingRegistry(str(registry_path), project_dir=str(tmp_path))
    again = reloaded.for_palette(str(tmp_path / "palettes" / "a.csv"), set_active=False)
    assert again.entries == [{"old_id": 1, "new_id": 5}]


def test_registry_for_palette_reuses_existing_section_instead_of_wiping(tmp_path):
    """The core regression test for the reported bug: tuning palette A's
    mapping, switching to palette B, editing B, then switching BACK to A must
    leave A's mapping exactly as it was -- not wiped/overwritten by B's."""
    registry_path = tmp_path / "mappings" / "mappings.json"
    reg = ms.MappingRegistry(str(registry_path), project_dir=str(tmp_path))

    a_path = str(tmp_path / "palettes" / "a.csv")
    b_path = str(tmp_path / "palettes" / "b.csv")

    store_a = reg.for_palette(a_path)
    store_a.add_or_update(1, 1)
    store_a.add_or_update(2, 2)

    store_b = reg.for_palette(b_path)  # switch away -- must not touch A's section
    store_b.add_or_update(1, 9)

    store_a_again = reg.for_palette(a_path, set_active=False)
    assert store_a_again.entries == [{"old_id": 1, "new_id": 1}, {"old_id": 2, "new_id": 2}]


def test_registry_active_pointer_updates_on_for_palette_unless_set_active_false(tmp_path):
    registry_path = tmp_path / "mappings" / "mappings.json"
    reg = ms.MappingRegistry(str(registry_path), project_dir=str(tmp_path))
    a_path = str(tmp_path / "a.csv")
    b_path = str(tmp_path / "b.csv")

    reg.for_palette(a_path)
    assert reg.active_palette_path() == a_path

    reg.for_palette(b_path, set_active=False)
    assert reg.active_palette_path() == a_path  # unchanged

    reg.for_palette(b_path)
    assert reg.active_palette_path() == b_path


def test_registry_for_active_returns_the_active_sections_store(tmp_path):
    registry_path = tmp_path / "mappings" / "mappings.json"
    reg = ms.MappingRegistry(str(registry_path), project_dir=str(tmp_path))
    a_path = str(tmp_path / "a.csv")
    reg.for_palette(a_path).add_or_update(1, 1)

    reloaded = ms.MappingRegistry(str(registry_path), project_dir=str(tmp_path))
    store = reloaded.for_active()
    assert store.entries == [{"old_id": 1, "new_id": 1}]
    assert store.new_palette == a_path


def test_registry_for_active_on_empty_registry_is_a_harmless_empty_store(tmp_path):
    registry_path = tmp_path / "mappings" / "mappings.json"
    reg = ms.MappingRegistry(str(registry_path), project_dir=str(tmp_path))
    store = reg.for_active()
    assert store.entries == []
    store.add_or_update(1, 1)  # must not raise, even with nothing to attach to
    assert store.resolved_entries() == [{"old_id": 1, "new_id": 1}]


def test_registry_all_sections_lists_every_palette(tmp_path):
    registry_path = tmp_path / "mappings" / "mappings.json"
    reg = ms.MappingRegistry(str(registry_path), project_dir=str(tmp_path))
    a_path = str(tmp_path / "a.csv")
    b_path = str(tmp_path / "b.csv")
    reg.for_palette(a_path).add_or_update(1, 1)
    # b is a brand-new section, seeded from a (still active at this point) --
    # see test_registry_for_palette_seeds_a_brand_new_section_from_the_active_one.
    reg.for_palette(b_path).add_or_update(2, 2)

    sections = dict(reg.all_sections())
    assert set(sections) == {a_path, b_path}
    assert sections[a_path].entries == [{"old_id": 1, "new_id": 1}]
    assert sections[b_path].entries == [{"old_id": 1, "new_id": 1}, {"old_id": 2, "new_id": 2}]


def test_registry_for_palette_seeds_a_brand_new_section_from_the_active_one(tmp_path):
    """The core fix for two related bugs: (1) `ucs automatic --from-image`
    generating/reusing a palette never associated the mapping it actually
    applied with that palette's own section, so opening it later in the GUI
    showed an empty mapping; (2) generating a genuinely NEW palette in the
    GUI silently started with an empty mapping instead of proposing the
    previous one. old_id->new_id is a reusable "recipe" (which detected
    color plays which role), not inherently tied to one literal palette
    file -- a brand-new section should propose whatever was active, as a
    starting point the user can still freely clear/edit."""
    registry_path = tmp_path / "mappings" / "mappings.json"
    reg = ms.MappingRegistry(str(registry_path), project_dir=str(tmp_path))
    a_path = str(tmp_path / "a.csv")
    b_path = str(tmp_path / "b.csv")

    store_a = reg.for_palette(a_path)
    store_a.add_or_update(1, 1)
    store_a.add_or_update(2, 2)

    store_b = reg.for_palette(b_path)  # brand new -- must be seeded from a (currently active)
    assert store_b.entries == [{"old_id": 1, "new_id": 1}, {"old_id": 2, "new_id": 2}]

    # It's a PROPOSAL, not a live link: editing b afterward must never affect a.
    store_b.add_or_update(1, 99)
    a_again = reg.for_palette(a_path, set_active=False).entries
    assert a_again == [{"old_id": 1, "new_id": 1}, {"old_id": 2, "new_id": 2}]


def test_registry_for_palette_first_ever_section_starts_empty(tmp_path):
    """No active section exists yet -- nothing to propose, so a brand-new
    registry's very first palette starts with an empty mapping (unchanged
    baseline behavior)."""
    registry_path = tmp_path / "mappings" / "mappings.json"
    reg = ms.MappingRegistry(str(registry_path), project_dir=str(tmp_path))
    store = reg.for_palette(str(tmp_path / "only.csv"))
    assert store.entries == []


def test_migrate_legacy_mapping_csv_creates_one_active_section_and_leaves_old_file_intact(tmp_path):
    legacy_path = tmp_path / "mappings" / "mapping.csv"
    old_store = ms.MappingStore(
        str(legacy_path), old_palette=str(tmp_path / "detected.csv"), new_palette=str(tmp_path / "p.csv"),
        project_dir=str(tmp_path),
    )
    old_store.add_or_update(1, 1)
    old_store.add_or_update(2, 2)
    before = legacy_path.read_text()

    registry_path = tmp_path / "mappings" / "mappings.json"
    migrated = ms.migrate_legacy_mapping_csv_if_needed(str(registry_path), str(legacy_path), str(tmp_path))
    assert migrated is True

    reg = ms.MappingRegistry(str(registry_path), project_dir=str(tmp_path))
    assert reg.active_palette_path() == str(tmp_path / "p.csv")
    assert reg.for_active().entries == [{"old_id": 1, "new_id": 1}, {"old_id": 2, "new_id": 2}]
    assert legacy_path.read_text() == before  # never touched


def test_migrate_is_idempotent_once_registry_exists(tmp_path):
    legacy_path = tmp_path / "mappings" / "mapping.csv"
    old_store = ms.MappingStore(
        str(legacy_path), old_palette=str(tmp_path / "detected.csv"), new_palette=str(tmp_path / "p.csv"),
        project_dir=str(tmp_path),
    )
    old_store.add_or_update(1, 1)

    registry_path = tmp_path / "mappings" / "mappings.json"
    assert ms.migrate_legacy_mapping_csv_if_needed(str(registry_path), str(legacy_path), str(tmp_path)) is True

    old_store.add_or_update(2, 2)  # legacy file changes AFTER migration
    assert ms.migrate_legacy_mapping_csv_if_needed(str(registry_path), str(legacy_path), str(tmp_path)) is False

    reg = ms.MappingRegistry(str(registry_path), project_dir=str(tmp_path))
    assert reg.for_active().entries == [{"old_id": 1, "new_id": 1}]  # NOT re-migrated


def test_migrate_no_op_when_legacy_mapping_is_empty(tmp_path):
    registry_path = tmp_path / "mappings" / "mappings.json"
    legacy_path = tmp_path / "mappings" / "mapping.csv"
    assert ms.migrate_legacy_mapping_csv_if_needed(str(registry_path), str(legacy_path), str(tmp_path)) is False


def test_peek_section_returns_none_without_creating_one(tmp_path):
    """Unlike for_palette, peek_section must never seed/create a section as a
    side effect -- a read-only `manage mappings show` on a palette with no
    mapping yet must not leave a fresh empty section behind."""
    registry_path = tmp_path / "mappings" / "mappings.json"
    reg = ms.MappingRegistry(str(registry_path), project_dir=str(tmp_path))
    reg.for_palette(str(tmp_path / "a.csv")).add_or_update(1, 1)  # some active section exists

    assert reg.peek_section(str(tmp_path / "never-touched.csv")) is None

    # a second, fresh registry object proves nothing was persisted for it
    reg2 = ms.MappingRegistry(str(registry_path), project_dir=str(tmp_path))
    assert str(tmp_path / "never-touched.csv") not in [
        p for p, _s in reg2.all_sections()
    ]


def test_peek_section_returns_the_existing_section(tmp_path):
    registry_path = tmp_path / "mappings" / "mappings.json"
    reg = ms.MappingRegistry(str(registry_path), project_dir=str(tmp_path))
    a_path = str(tmp_path / "a.csv")
    reg.for_palette(a_path).add_or_update(1, 1)

    peeked = reg.peek_section(a_path)
    assert peeked is not None
    assert peeked.entries == [{"old_id": 1, "new_id": 1}]


def test_remove_section_deletes_it_and_clears_active_if_it_was_active(tmp_path):
    registry_path = tmp_path / "mappings" / "mappings.json"
    reg = ms.MappingRegistry(str(registry_path), project_dir=str(tmp_path))
    a_path = str(tmp_path / "a.csv")
    b_path = str(tmp_path / "b.csv")
    reg.for_palette(a_path).add_or_update(1, 1)
    reg.for_palette(b_path).add_or_update(1, 1)  # now active

    assert reg.remove_section(b_path) is True
    assert reg.active_palette_path() is None  # was active, no reasonable fallback
    assert reg.peek_section(b_path) is None
    assert reg.peek_section(a_path) is not None  # untouched


def test_remove_section_on_a_palette_with_no_mapping_is_a_no_op(tmp_path):
    registry_path = tmp_path / "mappings" / "mappings.json"
    reg = ms.MappingRegistry(str(registry_path), project_dir=str(tmp_path))
    assert reg.remove_section(str(tmp_path / "nothing.csv")) is False


def test_remove_all_sections_wipes_everything_and_returns_the_count(tmp_path):
    registry_path = tmp_path / "mappings" / "mappings.json"
    reg = ms.MappingRegistry(str(registry_path), project_dir=str(tmp_path))
    reg.for_palette(str(tmp_path / "a.csv")).add_or_update(1, 1)
    reg.for_palette(str(tmp_path / "b.csv")).add_or_update(1, 1)

    assert reg.remove_all_sections() == 2
    assert reg.all_sections() == []
    assert reg.active_palette_path() is None
