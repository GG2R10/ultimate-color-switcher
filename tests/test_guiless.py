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
    VALUE order (1 then 3) without any surplus/deficit confirmation -- this
    also gets a "compacted" tier + reorder_warning since 2 < max_new_id(3).
    Two DISTINCT detected colors, each its own role -- no duplicates/
    convergence involved, to isolate the slot-compaction behavior on its own."""
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
    assert result["reorder_warning"] is not None

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
    """Each entry's OWN stored new_id decides its target position -- not the
    row's position in the mapping file, nor insertion order. first_id targets
    new_id=2 and second_id targets new_id=1; since the palette has exactly
    2 colors (== max_new_id), this resolves at tier "exact" -- new_id IS the
    palette position, directly: first_id -> the 2nd palette color, second_id
    -> the 1st, regardless of which was inserted into the mapping first."""
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
    assert result["reorder_warning"] is None  # tier "exact" -- no reorder happened

    # positional: "#111111 #222222" -> first_id's color is at position 0
    positions = open(fp).read().lower().split()
    assert positions == ["#bbbbbb", "#aaaaaa"]  # first_id -> new_id=2 -> 2nd palette color


def test_slot_compaction_uses_ascending_value_order_not_insertion_order(fake_project, monkeypatch):
    """Distinct new_id values are compacted onto the fresh palette in
    ASCENDING NUMERIC VALUE order -- NOT the order they were first added to
    the mapping. This supersedes a previous decision (formerly ROADMAP.md's
    "GUIless mode" decision #4, which mandated insertion order) -- confirmed
    explicitly this session: new_id must mean the same thing (a literal
    target position) on every apply path, not an opaque "role label" only on
    `automatic`.

    A mapping built with new_id=99, then 2, then 4 (in that literal insertion
    order) must apply the fresh palette's colors by ASCENDING VALUE: 2 (the
    smallest) -> the palette's 1st color, 4 -> the 2nd, 99 (the largest) ->
    the 3rd -- regardless of which entry was inserted into the mapping first."""
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
    store.add_or_update(id_a, 99)  # inserted 1st, but largest value -> gets the 3rd color
    store.add_or_update(id_b, 2)   # inserted 2nd, but smallest value -> gets the 1st color
    store.add_or_update(id_c, 4)   # inserted 3rd, middle value -> gets the 2nd color

    result = guiless.apply_palette(
        [{"hex": "#aaaaaa"}, {"hex": "#bbbbbb"}, {"hex": "#cccccc"}],
        store.path, config.backup_dir, dry_run=False,
    )
    assert result["status"] == "applied"
    assert result["reorder_warning"] is not None  # 3 colors < max_new_id(99) -> tier "compacted"

    # positional: "#111111 #222222 #333333" -> id_a/id_b/id_c's colors are at 0/1/2
    positions = open(fp).read().lower().split()
    assert positions == ["#cccccc", "#aaaaaa", "#bbbbbb"]


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


def test_apply_does_not_leave_its_own_mapping_looking_orphaned(fake_project, monkeypatch, tmp_path):
    """Real bug report (via `ucs automatic --from-image ...`): a mapping
    already identity-stamped from a PRIOR detection (e.g. built/applied once
    via the GUI) must not be mistaken for drift/orphaned right after THIS
    apply replaces those very colors -- that's the expected, intended result
    of applying, not an external change. See mapping_store.stamp_applied_entries."""
    config, detected, store, fp = _setup(fake_project, monkeypatch)
    # Stamp identities as they were BEFORE this apply (mirrors a real prior
    # detection cycle, e.g. the mapping's own creation via the GUI).
    stamped, _drift = mapping_store.refresh_identity_stamps(store.entries, detected)
    store.entries = stamped
    store.save()

    palette_csv = tmp_path / "generated.csv"
    palette_store.write_palette_csv(str(palette_csv), [
        {"id": 1, "hex": "aaaaaa", "label": "primary"},
        {"id": 2, "hex": "bbbbbb", "label": "secondary"},
        {"id": 3, "hex": "cccccc", "label": "aux1"},
    ])

    result = guiless.apply_palette(str(palette_csv), store.path, config.backup_dir, dry_run=False)
    assert result["status"] == "applied"

    reloaded = mapping_store.MappingStore(store.path).load()
    fresh = detect_diff.detect_with_route(config)["colors"]
    drift = mapping_store.detect_drift(reloaded.entries, fresh)
    assert drift["orphaned"] == []
    assert drift["driftable"] == []
