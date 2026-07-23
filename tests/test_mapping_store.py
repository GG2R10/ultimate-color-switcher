from backend import mapping_store as ms


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
