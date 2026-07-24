import os

from color_switcher.backend import color_replacer as cr


def test_replace_hex_dry_run_does_not_modify(tmp_path):
    fp = tmp_path / "style.css"
    fp.write_text(".a { color: #00ccff; }")

    result = cr.replace_color_in_file(str(fp), "00ccff", "ff0000", "hex", dry_run=True)
    assert result["count"] == 1
    assert fp.read_text() == ".a { color: #00ccff; }"


def test_replace_hex_real_preserves_prefix_and_alpha(tmp_path):
    fp = tmp_path / "style.css"
    fp.write_text("color: 00ccff80; other: #00ccff;")

    result = cr.replace_color_in_file(str(fp), "00ccff", "ff0000", "hex", dry_run=False)
    assert result["count"] == 2
    assert fp.read_text() == "color: ff000080; other: #ff0000;"


def test_replace_rgb_real(tmp_path):
    fp = tmp_path / "style.css"
    fp.write_text("rgba(0, 204, 255, 0.5)")

    result = cr.replace_color_in_file(str(fp), "00ccff", "ff0000", "hex_from_rgb", dry_run=False)
    assert result["count"] == 1
    assert fp.read_text() == "rgba(255, 0, 0, 0.5)"


def test_replace_missing_file_reports_error(tmp_path):
    result = cr.replace_color_in_file(str(tmp_path / "nope.css"), "00ccff", "ff0000", "hex")
    assert result["error"] == "File not found"


def test_backup_restore_roundtrip(tmp_path, monkeypatch):
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setattr(cr, "HOME", str(fake_home))

    target = fake_home / ".config" / "app" / "style.css"
    target.parent.mkdir(parents=True)
    target.write_text("original")

    backup_dir = tmp_path / "backup"
    written = cr.backup_files([str(target)], str(backup_dir))
    assert len(written) == 1
    assert (backup_dir / ".config" / "app" / "style.css").read_text() == "original"

    target.write_text("modified")
    results = cr.restore_files([str(target)], str(backup_dir))
    assert results == [{"file": str(target), "restored": True}]
    assert target.read_text() == "original"


def test_restore_reports_missing_backup(tmp_path, monkeypatch):
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setattr(cr, "HOME", str(fake_home))

    target = fake_home / "nope.css"
    results = cr.restore_files([str(target)], str(tmp_path / "backup"))
    assert results == [{"file": str(target), "restored": False}]


def test_backup_skips_nonexistent_files(tmp_path, monkeypatch):
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setattr(cr, "HOME", str(fake_home))

    missing = fake_home / "nope.css"
    written = cr.backup_files([str(missing)], str(tmp_path / "backup"))
    assert written == []


def test_apply_mapping_full_flow(fake_project, monkeypatch):
    monkeypatch.setattr(cr, "HOME", str(fake_project.fakehome))

    fp = fake_project.make_file(
        "app/style.css",
        ".a { color: #00ccff; }\n.b { color: rgb(0, 204, 255); }\n",
    )
    config = fake_project.load_config()

    from color_switcher.backend import detect_diff, palette_store

    detected = detect_diff.detect_with_route(config)["colors"]

    palette_path = str(fake_project.project_dir / "palettes" / "created" / "p.csv")
    palette_store.write_palette_csv(palette_path, [{"id": 1, "hex": "ff0000", "label": "red"}])
    new_palette = palette_store.read_palette_csv(palette_path)

    entries = [{"old_id": c["id"], "new_id": 1} for c in detected]

    results = cr.apply_mapping(detected, new_palette, entries, config.backup_dir, dry_run=False)
    assert sum(r["count"] for r in results) == 2

    content = open(fp).read()
    assert "#ff0000" in content
    assert "rgb(255, 0, 0)" in content
    assert "00ccff" not in content

    backup_file = os.path.join(config.backup_dir, os.path.relpath(fp, str(fake_project.fakehome)))
    assert os.path.isfile(backup_file)
    assert "#00ccff" in open(backup_file).read()


def test_apply_mapping_dry_run_creates_no_backup(fake_project, monkeypatch):
    monkeypatch.setattr(cr, "HOME", str(fake_project.fakehome))
    fp = fake_project.make_file("app/style.css", ".a { color: #00ccff; }")
    config = fake_project.load_config()

    from color_switcher.backend import detect_diff, palette_store

    detected = detect_diff.detect_with_route(config)["colors"]
    palette_path = str(fake_project.project_dir / "palettes" / "created" / "p.csv")
    palette_store.write_palette_csv(palette_path, [{"id": 1, "hex": "ff0000", "label": "red"}])
    new_palette = palette_store.read_palette_csv(palette_path)
    entries = [{"old_id": c["id"], "new_id": 1} for c in detected]

    cr.apply_mapping(detected, new_palette, entries, config.backup_dir, dry_run=True)

    assert open(fp).read() == ".a { color: #00ccff; }"
    assert not os.path.exists(os.path.join(config.backup_dir, os.path.relpath(fp, str(fake_project.fakehome))))
