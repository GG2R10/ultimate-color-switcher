from color_switcher.backend import color_replacer as cr
from color_switcher.backend import detect_diff, guiless, mapping_store, palette_store


def _setup(fake_project, monkeypatch):
    """Detected colors 111111/222222/333333 (equal counts -> stable id order
    1,2,3 matching first-seen order), plus a saved, fully-resolved mapping
    assigning each detected color to its own distinct slot (1,2,3 -- the
    same convention palette_generator/palette_store use, e.g. 1=primary,
    2=secondary). guiless.py re-colors slot N using palette[N-1], keyed off
    this stored new_id rather than the row's position in the file."""
    monkeypatch.setattr(cr, "HOME", str(fake_project.fakehome))
    fp = fake_project.make_file("app/style.css", "#111111 #222222 #333333")
    config = fake_project.load_config()
    detected = detect_diff.detect_with_route(config)["colors"]

    mapping_path = mapping_store.new_mapping_path(config.mappings_dir)
    store = mapping_store.MappingStore(
        mapping_path, old_palette=config.detected_palette_csv, new_palette="unused.csv"
    )
    for i, c in enumerate(detected):
        store.add_or_update(c["id"], i + 1)

    return config, detected, store, fp


def test_exact_size_applies_in_insertion_order(fake_project, monkeypatch):
    config, detected, store, fp = _setup(fake_project, monkeypatch)
    palette_json = [{"hex": "#aaaaaa"}, {"hex": "#bbbbbb"}, {"hex": "#cccccc"}]

    result = guiless.apply_palette(palette_json, store.path, config.backup_dir, dry_run=False)
    assert result["status"] == "applied"

    content = open(fp).read().lower()
    assert "111111" not in content
    assert "222222" not in content
    assert "333333" not in content
    assert "aaaaaa" in content
    assert "bbbbbb" in content
    assert "cccccc" in content


def test_surplus_palette_needs_confirmation(fake_project, monkeypatch):
    """3 distinct roles needed, 4 palette colors given -- the 4th goes
    unused. Soft warning, skippable with yolo (NOT force: force is only for
    real conflicts, so accepting "extra colors" mustn't also wave conflicts
    through)."""
    config, detected, store, fp = _setup(fake_project, monkeypatch)
    palette_json = [{"hex": "#aaaaaa"}, {"hex": "#bbbbbb"}, {"hex": "#cccccc"}, {"hex": "#dddddd"}]

    result = guiless.apply_palette(palette_json, store.path, config.backup_dir, dry_run=True)
    assert result["status"] == "needs_confirmation"
    assert result["surplus_palette_count"] == 1

    # force alone does NOT bypass a surplus anymore -- that's yolo's job.
    still_blocked = guiless.apply_palette(
        palette_json, store.path, config.backup_dir, dry_run=True, force=True
    )
    assert still_blocked["status"] == "needs_confirmation"

    yolo = guiless.apply_palette(
        palette_json, store.path, config.backup_dir, dry_run=True, yolo=True
    )
    assert yolo["status"] == "applied"
    assert len(yolo["results"]) == 3


def test_yolo_does_not_bypass_conflicts(fake_project, monkeypatch):
    """yolo waves through surplus colors but must NOT wave through a real
    case-1/convergence conflict -- that still requires force. Guards the whole
    point of splitting the two flags."""
    config, detected, store, fp = _setup(fake_project, monkeypatch)

    order = [e["old_id"] for e in store.entries]
    detected_by_id = {c["id"]: c for c in detected}
    hexes_in_order = [detected_by_id[oid]["color"] for oid in order]
    rotated = hexes_in_order[1:] + hexes_in_order[:1]  # each target equals a DIFFERENT entry's current color
    palette_json = [{"hex": f"#{h}"} for h in rotated]

    with_yolo = guiless.apply_palette(
        palette_json, store.path, config.backup_dir, dry_run=True, yolo=True
    )
    assert with_yolo["status"] == "conflicts"  # yolo didn't help -- needs force

    with_force = guiless.apply_palette(
        palette_json, store.path, config.backup_dir, dry_run=True, force=True
    )
    assert with_force["status"] == "applied"


def test_insufficient_palette_blocks_even_with_force(fake_project, monkeypatch):
    """3 distinct roles needed (_setup maps 3 detected colors to 3 distinct
    slots), only 1 palette color given -- there's nothing to assign to the
    other 2 roles. This is a hard block, unlike the surplus case: --force
    does NOT bypass it."""
    config, detected, store, fp = _setup(fake_project, monkeypatch)
    palette_json = [{"hex": "#aaaaaa"}]

    result = guiless.apply_palette(palette_json, store.path, config.backup_dir, dry_run=True)
    assert result["status"] == "insufficient_palette"
    assert result["needed"] == 3
    assert result["available"] == 1

    forced = guiless.apply_palette(
        palette_json, store.path, config.backup_dir, dry_run=True, force=True
    )
    assert forced["status"] == "insufficient_palette"


def test_distinct_roles_counted_not_max_slot_number(fake_project, monkeypatch):
    """A mapping that only ever used roles 1 and 3 (skipping 2 -- e.g.
    primary and aux1, never secondary) needs a 2-color palette, not a
    3-color one, and the 2 colors given fill roles 1 and 3 in ascending
    order without any surplus/deficit confirmation. Two DISTINCT detected
    colors, each its own role -- no duplicates/convergence involved, to
    isolate the slot-compaction behavior on its own."""
    monkeypatch.setattr(cr, "HOME", str(fake_project.fakehome))
    fp = fake_project.make_file("app/style.css", "#111111 #222222")
    config = fake_project.load_config()
    detected = detect_diff.detect_with_route(config)["colors"]
    assert len(detected) == 2
    ids = [c["id"] for c in detected]

    mapping_path = mapping_store.new_mapping_path(config.mappings_dir)
    store = mapping_store.MappingStore(
        mapping_path, old_palette=config.detected_palette_csv, new_palette="unused.csv"
    )
    store.add_or_update(ids[0], 1)
    store.add_or_update(ids[1], 3)  # skips role 2 entirely

    result = guiless.apply_palette(
        [{"hex": "#aaaaaa"}, {"hex": "#bbbbbb"}], store.path, config.backup_dir, dry_run=False
    )
    assert result["status"] == "applied"

    content = open(fp).read().lower()
    assert "111111" not in content and "222222" not in content
    assert "aaaaaa" in content  # role 1
    assert "bbbbbb" in content  # role 3, compacted to the 2nd (last) palette color


def test_conflicts_block_unless_forced(fake_project, monkeypatch):
    config, detected, store, fp = _setup(fake_project, monkeypatch)

    order = [e["old_id"] for e in store.entries]
    detected_by_id = {c["id"]: c for c in detected}
    hexes_in_order = [detected_by_id[oid]["color"] for oid in order]
    rotated = hexes_in_order[1:] + hexes_in_order[:1]  # each target now equals a DIFFERENT entry's current color
    palette_json = [{"hex": f"#{h}"} for h in rotated]

    result = guiless.apply_palette(palette_json, store.path, config.backup_dir, dry_run=True)
    assert result["status"] == "conflicts"
    assert len(result["conflicts"]) > 0

    forced = guiless.apply_palette(
        palette_json, store.path, config.backup_dir, dry_run=True, force=True
    )
    assert forced["status"] == "applied"


def test_convergence_blocks_unless_forced(fake_project, monkeypatch):
    """Two DIFFERENT (non-sibling) detected colors mapped to the same brand
    new target hex must be blocked just like a case-1 collision, even though
    neither old_id's target collides with any OTHER, untouched detected
    color -- forcing it loses the ability to tell them apart on a future
    re-detect."""
    monkeypatch.setattr(cr, "HOME", str(fake_project.fakehome))
    fp = fake_project.make_file("app/style.css", "#111111 #222222")
    config = fake_project.load_config()
    detected = detect_diff.detect_with_route(config)["colors"]
    assert len(detected) == 2

    mapping_path = mapping_store.new_mapping_path(config.mappings_dir)
    store = mapping_store.MappingStore(
        mapping_path, old_palette=config.detected_palette_csv, new_palette="unused.csv"
    )
    for c in detected:
        store.add_or_update(c["id"], 1)  # both target slot 1 -- neither is a hex/rgb sibling of the other

    result = guiless.apply_palette([{"hex": "#aaaaaa"}], store.path, config.backup_dir, dry_run=True)
    assert result["status"] == "conflicts"
    assert result["convergence"]

    forced = guiless.apply_palette(
        [{"hex": "#aaaaaa"}], store.path, config.backup_dir, dry_run=True, force=True
    )
    assert forced["status"] == "applied"
    assert forced["stale_mapping_warning"] is True


def test_dry_run_does_not_modify_files(fake_project, monkeypatch):
    config, detected, store, fp = _setup(fake_project, monkeypatch)
    palette_json = [{"hex": "#aaaaaa"}, {"hex": "#bbbbbb"}, {"hex": "#cccccc"}]
    before = open(fp).read()

    guiless.apply_palette(palette_json, store.path, config.backup_dir, dry_run=True)
    assert open(fp).read() == before


def test_empty_mapping_reports_status(fake_project, monkeypatch, tmp_path):
    config, detected, store, fp = _setup(fake_project, monkeypatch)
    empty_mapping = tmp_path / "empty.csv"
    empty_mapping.write_text("#old_palette=x\n#new_palette=y\nold_id,new_id\n")

    result = guiless.apply_palette([{"hex": "#aaaaaa"}], str(empty_mapping), config.backup_dir)
    assert result["status"] == "empty_mapping"


def test_multiple_old_ids_sharing_a_slot_all_get_that_slot_color(fake_project, monkeypatch):
    """Case-1/case-2 duplicates: e.g. a color's hex occurrence and its rgb()
    occurrence are two different detected ids but must end up the same final
    color. Both point at the same new_id (slot) in the mapping; automatic
    must resolve them to the SAME fresh palette color, not two different
    ones grabbed by row position. Since these two ids are NOT hex/rgb
    siblings of each other, this also trips the convergence gate -- force is
    required, same as test_convergence_blocks_unless_forced covers."""
    monkeypatch.setattr(cr, "HOME", str(fake_project.fakehome))
    fp = fake_project.make_file("app/style.css", "#111111 #222222")
    config = fake_project.load_config()
    detected = detect_diff.detect_with_route(config)["colors"]
    assert len(detected) == 2

    mapping_path = mapping_store.new_mapping_path(config.mappings_dir)
    store = mapping_store.MappingStore(
        mapping_path, old_palette=config.detected_palette_csv, new_palette="unused.csv"
    )
    for c in detected:
        store.add_or_update(c["id"], 1)  # both target slot 1

    result = guiless.apply_palette(
        [{"hex": "#aaaaaa"}], store.path, config.backup_dir, dry_run=False, force=True
    )
    assert result["status"] == "applied"

    content = open(fp).read().lower()
    assert "111111" not in content
    assert "222222" not in content
    assert content.count("aaaaaa") == 2


def test_slot_assignment_uses_stored_new_id_not_row_position(fake_project, monkeypatch):
    """Each entry's OWN stored new_id decides its role -- not the row's
    position in the file. Rank-compaction order is a separate concern (see
    test_slot_compaction_uses_insertion_order_not_numeric_new_id_order); here
    both entries' new_ids already happen to be in ascending insertion order
    (2 then 1 is NOT ascending though -- so this also incidentally exercises
    that first_id, inserted first while targeting new_id=2, gets rank 1 (the
    first color), since 2 is the first NEW_ID VALUE ever seen -- see the other
    test for why that's insertion order, not sorted order)."""
    monkeypatch.setattr(cr, "HOME", str(fake_project.fakehome))
    fp = fake_project.make_file("app/style.css", "#111111 #222222")
    config = fake_project.load_config()
    detected = detect_diff.detect_with_route(config)["colors"]
    assert len(detected) == 2
    first_id, second_id = detected[0]["id"], detected[1]["id"]

    mapping_path = mapping_store.new_mapping_path(config.mappings_dir)
    store = mapping_store.MappingStore(
        mapping_path, old_palette=config.detected_palette_csv, new_palette="unused.csv"
    )
    store.add_or_update(first_id, 2)  # inserted first (targets new_id=2)
    store.add_or_update(second_id, 1)  # inserted second (targets new_id=1)

    palette_json = [{"hex": "#aaaaaa"}, {"hex": "#bbbbbb"}]
    result = guiless.apply_palette(palette_json, store.path, config.backup_dir, dry_run=False)
    assert result["status"] == "applied"

    # positional: "#111111 #222222" -> first_id's color is at position 0
    positions = open(fp).read().lower().split()
    assert positions == ["#aaaaaa", "#bbbbbb"]  # first_id (inserted 1st) -> rank 1 -> aaaaaa


def test_slot_compaction_uses_insertion_order_not_numeric_new_id_order(fake_project, monkeypatch):
    """Distinct roles are compacted onto the fresh palette in the order they
    were FIRST ADDED to the mapping -- NOT sorted by the numeric value of
    new_id. This is the ORIGINAL documented spec (ROADMAP.md's "GUIless mode"
    decision #4: pairing happens in insertion order of the mapping), which an
    earlier implementation of this function had drifted from by sorting
    numerically instead.

    A mapping built with new_id=99, then 2, then 4 (in that literal insertion
    order) must apply the fresh palette's 1st/2nd/3rd colors in that SAME
    insertion order, even though 99 > 4 > 2 numerically -- e.g. a corrupted or
    hand-edited new_id value must not silently swap which detected color gets
    which target color just because of its numeric size."""
    monkeypatch.setattr(cr, "HOME", str(fake_project.fakehome))
    fp = fake_project.make_file("app/style.css", "#111111 #222222 #333333")
    config = fake_project.load_config()
    detected = detect_diff.detect_with_route(config)["colors"]
    assert len(detected) == 3
    id_a, id_b, id_c = (c["id"] for c in detected)

    mapping_path = mapping_store.new_mapping_path(config.mappings_dir)
    store = mapping_store.MappingStore(
        mapping_path, old_palette=config.detected_palette_csv, new_palette="unused.csv"
    )
    store.add_or_update(id_a, 99)  # inserted 1st -> must get the palette's 1st color
    store.add_or_update(id_b, 2)   # inserted 2nd -> must get the palette's 2nd color
    store.add_or_update(id_c, 4)   # inserted 3rd -> must get the palette's 3rd color

    result = guiless.apply_palette(
        [{"hex": "#aaaaaa"}, {"hex": "#bbbbbb"}, {"hex": "#cccccc"}],
        store.path, config.backup_dir, dry_run=False,
    )
    assert result["status"] == "applied"

    # positional: "#111111 #222222 #333333" -> id_a/id_b/id_c's colors are at 0/1/2
    positions = open(fp).read().lower().split()
    assert positions == ["#aaaaaa", "#bbbbbb", "#cccccc"]


def test_accepts_a_palette_csv_path_instead_of_json(fake_project, monkeypatch, tmp_path):
    config, detected, store, fp = _setup(fake_project, monkeypatch)
    palette_csv = tmp_path / "generated.csv"
    palette_store.write_palette_csv(str(palette_csv), [
        {"id": 1, "hex": "aaaaaa", "label": "primary"},
        {"id": 2, "hex": "bbbbbb", "label": "secondary"},
        {"id": 3, "hex": "cccccc", "label": "aux1"},
    ])

    result = guiless.apply_palette(str(palette_csv), store.path, config.backup_dir, dry_run=False)
    assert result["status"] == "applied"

    content = open(fp).read().lower()
    assert "aaaaaa" in content and "bbbbbb" in content and "cccccc" in content
