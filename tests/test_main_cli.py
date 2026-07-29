import argparse
import os

import numpy as np
import pytest
from PIL import Image

from color_switcher import main as ucs_main
from color_switcher.backend import color_detector as cd
from color_switcher.backend import color_replacer as cr
from color_switcher.backend import detect_diff, mapping_store, palette_store


def _make_image(path):
    arr = np.zeros((64, 64, 3), dtype=np.uint8)
    for i, c in enumerate([(220, 40, 40), (40, 200, 60), (50, 60, 230), (240, 230, 40)]):
        arr[:, i * 16:(i + 1) * 16] = c
    Image.fromarray(arr, "RGB").save(path)


def _args(**kwargs):
    kwargs.setdefault("mapping", None)
    kwargs.setdefault("force", False)
    kwargs.setdefault("link", None)
    return argparse.Namespace(**kwargs)


def _setup(fake_project, monkeypatch):
    monkeypatch.setattr(cr, "HOME", str(fake_project.fakehome))
    fake_project.make_file("app/style.css", "#cbff29")
    config = fake_project.load_config()
    detected = detect_diff.detect_with_route(config)["colors"]
    palette_store.write_palette_csv(config.generated_palette_csv, [{"id": 1, "hex": "ff00aa", "label": "primary"}])
    store = mapping_store.MappingStore(
        config.mapping_csv, old_palette=config.detected_palette_csv, new_palette=config.generated_palette_csv,
        project_dir=config.project_dir,
    )
    store.load()
    return config, detected, store


def test_apply_or_test_errors_cleanly_when_even_the_distinct_count_does_not_fit(fake_project, monkeypatch, capsys):
    """2 distinct new_id values needed (1 and 99), but the palette only has 1
    color -- not even the tier="compacted" fallback can help here (that needs
    `available >= len(distinct)`), so this is a genuine tier="blocked"."""
    monkeypatch.setattr(cr, "HOME", str(fake_project.fakehome))
    fake_project.make_file("app/style.css", "#cbff29 #112233")
    config = fake_project.load_config()
    detected = detect_diff.detect_with_route(config)["colors"]
    assert len(detected) == 2
    palette_store.write_palette_csv(config.generated_palette_csv, [{"id": 1, "hex": "ff00aa", "label": "primary"}])
    store = mapping_store.MappingStore(
        config.mapping_csv, old_palette=config.detected_palette_csv, new_palette=config.generated_palette_csv,
        project_dir=config.project_dir,
    )
    store.load()
    store.add_or_update(detected[0]["id"], 1)
    store.add_or_update(detected[1]["id"], 99)

    with pytest.raises(SystemExit) as exc:
        ucs_main.cmd_test(_args(), config)
    assert exc.value.code == 1
    assert "necesita al menos" in capsys.readouterr().out
    # cmd_apply shares the exact same _apply_or_test body -- must fail identically
    with pytest.raises(SystemExit) as exc:
        ucs_main.cmd_apply(_args(), config)
    assert exc.value.code == 1


def test_apply_or_test_compacted_tier_warns_and_applies_anyway(fake_project, monkeypatch, capsys):
    """A single distinct new_id (99) against a 1-color palette IS enough for
    tier="compacted" (available(1) >= distinct-count(1)) -- unlike the old
    strict/hard-error behavior, the CLI now warns loudly and applies (a
    wallpaper-switch hook must not stop dead on this)."""
    config, detected, store = _setup(fake_project, monkeypatch)
    store.add_or_update(detected[0]["id"], 99)  # the palette only has id 1

    ucs_main.cmd_test(_args(), config)  # must NOT raise
    out = capsys.readouterr().out
    assert "REASIGNACIÓN DE MAPPING" in out
    assert "1 reemplazos" in out


def test_apply_or_test_errors_cleanly_when_new_palette_file_is_missing(fake_project, monkeypatch, capsys):
    config, detected, store = _setup(fake_project, monkeypatch)
    store.new_palette = str(fake_project.tmp_path / "nope.csv")
    store.add_or_update(detected[0]["id"], 1)
    store.save()

    with pytest.raises(SystemExit) as exc:
        ucs_main.cmd_test(_args(), config)
    assert exc.value.code == 1
    assert "necesita al menos" in capsys.readouterr().out


def test_apply_or_test_still_works_on_a_healthy_mapping(fake_project, monkeypatch, capsys):
    config, detected, store = _setup(fake_project, monkeypatch)
    store.add_or_update(detected[0]["id"], 1)

    ucs_main.cmd_test(_args(), config)  # must NOT raise
    out = capsys.readouterr().out
    assert "1 reemplazos" in out


def test_mapping_relink_reports_and_applies_with_yes(fake_project, monkeypatch, capsys):
    monkeypatch.setattr(cr, "HOME", str(fake_project.fakehome))
    fake_project.make_file("app/style.css", "#111111 #111111 #111111 #222222")
    config = fake_project.load_config()
    detected = detect_diff.detect_with_route(config)["colors"]
    by_hex = {c["color"]: c["id"] for c in detected}
    assert by_hex["111111"] == 1  # higher count -> rank 1
    assert by_hex["222222"] == 2

    store = mapping_store.MappingStore(
        config.mapping_csv, old_palette=config.detected_palette_csv, new_palette="unused.csv",
        project_dir=config.project_dir,
    )
    store.add_or_update(by_hex["111111"], 1)
    store.add_or_update(by_hex["222222"], 2)
    stamped, _drift = mapping_store.refresh_identity_stamps(store.entries, detected)
    store.entries = stamped
    store.save()

    # Flip the counts so ranks (ids) swap -- same real colors, different ids.
    fake_project.make_file("app/style.css", "#111111 #222222 #222222 #222222")
    new_detected = detect_diff.detect_with_route(config)["colors"]
    new_by_hex = {c["color"]: c["id"] for c in new_detected}
    assert new_by_hex["222222"] == 1
    assert new_by_hex["111111"] == 2

    capsys.readouterr()  # discard detect's own output
    ucs_main.cmd_mapping_relink(_args(yes=True, mapping=config.mapping_csv), config)
    out = capsys.readouterr().out
    assert "re-vinculada" in out

    _, _, reloaded = mapping_store.read_mapping_csv(config.mapping_csv, project_dir=config.project_dir)
    by_old_id = {e["old_id"]: e["new_id"] for e in reloaded}
    assert by_old_id[new_by_hex["111111"]] == 1  # entry followed 111111 to its new id
    assert by_old_id[new_by_hex["222222"]] == 2


def test_apply_falls_back_to_active_mapping_with_no_explicit_flag(fake_project, monkeypatch, capsys):
    monkeypatch.setattr(cr, "HOME", str(fake_project.fakehome))
    fake_project.make_file("app/style.css", "#cbff29")
    config = fake_project.load_config()
    detected = detect_diff.detect_with_route(config)["colors"]
    palette_path = os.path.join(config.palettes_created_dir, "target.csv")
    palette_store.write_palette_csv(palette_path, [{"id": 1, "hex": "ff00aa", "label": "primary"}])

    registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
    store = registry.for_palette(palette_path, old_palette=config.detected_palette_csv)
    store.add_or_update(detected[0]["id"], 1)

    ucs_main.cmd_test(_args(), config)  # no --mapping at all -- must resolve via the active registry section
    out = capsys.readouterr().out
    assert "1 reemplazos" in out


def test_mapping_new_for_a_second_palette_does_not_touch_the_first_palettes_mapping(fake_project, monkeypatch, capsys):
    monkeypatch.setattr(cr, "HOME", str(fake_project.fakehome))
    fake_project.make_file("app/style.css", "#111111")
    config = fake_project.load_config()
    detected = detect_diff.detect_with_route(config)["colors"]
    old_id = detected[0]["id"]

    palette_a = os.path.join(config.palettes_created_dir, "a.csv")
    palette_b = os.path.join(config.palettes_created_dir, "b.csv")
    palette_store.write_palette_csv(palette_a, [{"id": 1, "hex": "aaaaaa", "label": ""}])
    palette_store.write_palette_csv(palette_b, [{"id": 1, "hex": "bbbbbb", "label": ""}])

    lines_a = iter([f"{old_id} 1", ""])
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(lines_a))
    ucs_main.cmd_mapping_new(argparse.Namespace(target_palette=palette_a, detected_palette=None, out=None), config)

    lines_b = iter([f"{old_id} 1", ""])
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(lines_b))
    ucs_main.cmd_mapping_new(argparse.Namespace(target_palette=palette_b, detected_palette=None, out=None), config)

    registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
    a_entries = registry.for_palette(palette_a, set_active=False).entries
    assert a_entries == [{"old_id": old_id, "new_id": 1}]  # untouched by building B's mapping afterward


def test_mapping_show_with_no_args_shows_the_active_mapping(fake_project, monkeypatch, capsys):
    monkeypatch.setattr(cr, "HOME", str(fake_project.fakehome))
    fake_project.make_file("app/style.css", "#cbff29")
    config = fake_project.load_config()
    detected = detect_diff.detect_with_route(config)["colors"]
    palette_path = os.path.join(config.palettes_created_dir, "target.csv")
    palette_store.write_palette_csv(palette_path, [{"id": 1, "hex": "ff00aa", "label": "primary"}])

    registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
    store = registry.for_palette(palette_path, old_palette=config.detected_palette_csv)
    store.add_or_update(detected[0]["id"], 1)

    ucs_main.cmd_mapping_show(argparse.Namespace(path=None, palette=None), config)
    out = capsys.readouterr().out
    assert f"new_palette: {palette_path}" in out


def test_mapping_list_marks_the_active_palette(fake_project, monkeypatch, capsys):
    monkeypatch.setattr(cr, "HOME", str(fake_project.fakehome))
    config = fake_project.load_config()
    registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
    registry.for_palette(os.path.join(config.palettes_created_dir, "a.csv")).add_or_update(1, 1)
    registry.for_palette(os.path.join(config.palettes_created_dir, "b.csv")).add_or_update(1, 1)

    ucs_main.cmd_mapping_list(argparse.Namespace(), config)
    lines = {l.strip().split()[0]: l for l in capsys.readouterr().out.splitlines() if l.strip()}
    a_line = next(l for l in lines.values() if "a.csv" in l)
    b_line = next(l for l in lines.values() if "b.csv" in l)
    assert "(activo)" not in a_line
    assert "(activo)" in b_line  # b was the last one set active


def test_mapping_relink_without_yes_prompts_and_cancels_on_no(fake_project, monkeypatch, capsys):
    monkeypatch.setattr(cr, "HOME", str(fake_project.fakehome))
    fake_project.make_file("app/style.css", "#111111 #111111 #111111 #222222")
    config = fake_project.load_config()
    detected = detect_diff.detect_with_route(config)["colors"]
    by_hex = {c["color"]: c["id"] for c in detected}

    store = mapping_store.MappingStore(
        config.mapping_csv, old_palette=config.detected_palette_csv, new_palette="unused.csv",
        project_dir=config.project_dir,
    )
    store.add_or_update(by_hex["111111"], 1)
    stamped, _ = mapping_store.refresh_identity_stamps(store.entries, detected)
    store.entries = stamped
    store.save()

    fake_project.make_file("app/style.css", "#111111 #222222 #222222 #222222")
    detect_diff.detect_with_route(config)

    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "n")
    capsys.readouterr()
    ucs_main.cmd_mapping_relink(_args(yes=False), config)
    out = capsys.readouterr().out
    assert "Cancelado" in out

    _, _, reloaded = mapping_store.read_mapping_csv(config.mapping_csv, project_dir=config.project_dir)
    assert reloaded[0]["old_id"] == by_hex["111111"]  # untouched -- user declined


def test_detect_prints_drift_summary_without_mutating(fake_project, monkeypatch, capsys):
    monkeypatch.setattr(cr, "HOME", str(fake_project.fakehome))
    fake_project.make_file("app/style.css", "#111111 #111111 #111111 #222222")
    config = fake_project.load_config()
    detected = detect_diff.detect_with_route(config)["colors"]
    by_hex = {c["color"]: c["id"] for c in detected}

    store = mapping_store.MappingStore(
        config.mapping_csv, old_palette=config.detected_palette_csv, new_palette="unused.csv",
        project_dir=config.project_dir,
    )
    store.add_or_update(by_hex["111111"], 1)
    stamped, _ = mapping_store.refresh_identity_stamps(store.entries, detected)
    store.entries = stamped
    store.save()
    before = open(config.mapping_csv).read()

    fake_project.make_file("app/style.css", "#111111 #222222 #222222 #222222")
    capsys.readouterr()
    ucs_main.cmd_detect(argparse.Namespace(dry_run=False), config)
    out = capsys.readouterr().out
    assert "cambiaron de id" in out

    # a bare `detect` never mutates the mapping's stamps, only reports.
    assert open(config.mapping_csv).read() == before


def test_apply_does_not_self_report_its_own_mapping_as_orphaned(fake_project, monkeypatch, capsys):
    """Real bug report: applying a mapping replaces the old detected color
    with the new palette color -- by design, that old color then no longer
    exists in the files. A naive post-apply drift check would see the
    stamped color gone and cry "orphaned" about the very mapping that just
    ran successfully. Two applies in a row (matching the user's real
    scenario: apply once, then run `automatic`/`apply` again against the
    same, already-applied state) must never print that false warning."""
    config, detected, store = _setup(fake_project, monkeypatch)
    store.add_or_update(detected[0]["id"], 1)
    # Simulate a prior detection cycle that already stamped this entry with
    # the ORIGINAL (pre-apply) detected color -- exactly what happens when a
    # mapping is built/detected before ever being applied (e.g. via the GUI,
    # which stamps identities on every detection, well before any apply).
    stamped, _drift = mapping_store.refresh_identity_stamps(store.entries, detected)
    store.entries = stamped
    store.save()

    ucs_main.cmd_apply(_args(), config)
    out = capsys.readouterr().out
    assert "ya no aparecen" not in out

    # Re-running against the now-already-applied state (a real no-op re-apply,
    # exactly what the user's `automatic --from-image` re-run does when the
    # palette already existed) must also stay silent about orphaned colors.
    ucs_main.cmd_apply(_args(), config)
    out = capsys.readouterr().out
    assert "ya no aparecen" not in out


def test_apply_does_not_warn_about_other_inactive_palettes_going_stale(fake_project, monkeypatch, capsys):
    """Real bug report: with several saved per-palette mappings (one per
    wallpaper tried), applying ONE of them printed a false "N colores
    huérfanos" about every OTHER, currently-inactive palette -- but only one
    wallpaper's colors can physically be in the files at a time, so every
    OTHER mapping is ALWAYS "orphaned" relative to whatever's active; that's
    not real, actionable drift, just noise. Drift/orphan checking must be
    scoped to the ACTIVE mapping only."""
    monkeypatch.setattr(cr, "HOME", str(fake_project.fakehome))
    fake_project.make_file("app/style.css", "#cbff29")
    config = fake_project.load_config()
    detected = detect_diff.detect_with_route(config)["colors"]

    # Two OTHER palettes, each stamped from some earlier, unrelated wallpaper
    # -- their stamped colors don't exist anywhere in the current files at
    # all (a totally different wallpaper was applied since).
    for name in ("other1.csv", "other2.csv"):
        other_palette = os.path.join(config.palettes_created_dir, name)
        palette_store.write_palette_csv(other_palette, [{"id": 1, "hex": "abcabc", "label": ""}])
        registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
        store = registry.for_palette(other_palette, old_palette=config.detected_palette_csv, set_active=False)
        store.add_or_update(999, 1, persist=False)
        store.entries[0]["old_type"] = "hex"
        store.entries[0]["old_hex"] = "deaddd"  # matches nothing currently detected -- permanently stale
        store.save()

    # The palette we're ACTUALLY going to apply.
    target = os.path.join(config.palettes_created_dir, "target.csv")
    palette_store.write_palette_csv(target, [{"id": 1, "hex": "ff00aa", "label": ""}])
    registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
    registry.for_palette(target, old_palette=config.detected_palette_csv).add_or_update(detected[0]["id"], 1)

    ucs_main.cmd_apply(_args(), config)
    out = capsys.readouterr().out
    assert "ya no aparecen" not in out
    assert "cambiaron de id" not in out


def test_apply_rekeys_color_role_to_the_new_hex(fake_project, monkeypatch, capsys):
    config, detected, store = _setup(fake_project, monkeypatch)
    old_key = cd.role_key(detected[0]["type"], detected[0]["color"])
    roles_path = os.path.join(os.path.dirname(config.detected_palette_csv), "color_roles.json")
    cd.write_color_roles(roles_path, {old_key: {"role": "background", "pair": None}})
    store.add_or_update(detected[0]["id"], 1)

    ucs_main.cmd_apply(_args(), config)  # real apply, not --test

    roles = cd.read_color_roles(roles_path)
    assert old_key not in roles
    assert cd.role_of(roles, cd.role_key(detected[0]["type"], "ff00aa")) == "background"


def test_test_mode_never_rekeys_color_roles(fake_project, monkeypatch, capsys):
    config, detected, store = _setup(fake_project, monkeypatch)
    old_key = cd.role_key(detected[0]["type"], detected[0]["color"])
    roles_path = os.path.join(os.path.dirname(config.detected_palette_csv), "color_roles.json")
    cd.write_color_roles(roles_path, {old_key: {"role": "foreground", "pair": None}})
    store.add_or_update(detected[0]["id"], 1)

    ucs_main.cmd_test(_args(), config)  # --test / dry-run, must NOT rekey

    assert cd.read_color_roles(roles_path) == {old_key: {"role": "foreground", "pair": None}}


# --------------------------------------------------------------------------- #
# palette-side roles (Phase 4)
# --------------------------------------------------------------------------- #

def test_palette_create_parser_accepts_add_with_optional_role():
    parser = ucs_main.build_parser()
    args = parser.parse_args([
        "palette", "create", "p.csv",
        "--add", "aabbcc", "primary", "foreground",
        "--add", "112233", "secondary",
    ])
    assert args.add == [["aabbcc", "primary", "foreground"], ["112233", "secondary"]]


def test_palette_create_parser_rejects_invalid_role_choice_on_add_color():
    parser = ucs_main.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["palette", "add-color", "p.csv", "aabbcc", "--role", "sideways"])


def test_cmd_palette_create_writes_role_from_add_flag(tmp_path, fake_project):
    config = fake_project.load_config()
    path = tmp_path / "p.csv"
    args = _args(path=str(path), add=[["aabbcc", "primary", "foreground"], ["112233", "secondary"]])
    ucs_main.cmd_palette_create(args, config)

    entries = palette_store.read_palette_csv(str(path))
    assert entries[0]["role"] == "foreground"
    assert "role" not in entries[1]  # no third token -> unmarked, key omitted


def test_cmd_palette_create_rejects_bad_role_in_add(tmp_path, fake_project, capsys):
    config = fake_project.load_config()
    path = tmp_path / "p.csv"
    args = _args(path=str(path), add=[["aabbcc", "primary", "sideways"]])
    with pytest.raises(SystemExit):
        ucs_main.cmd_palette_create(args, config)
    assert "inválido" in capsys.readouterr().out


def test_cmd_palette_add_color_sets_role(tmp_path, fake_project):
    config = fake_project.load_config()
    path = tmp_path / "p.csv"
    palette_store.write_palette_csv(str(path), [{"id": 1, "hex": "111111", "label": "a"}])
    args = _args(path=str(path), hex="aabbcc", label="new", role="background")

    ucs_main.cmd_palette_add_color(args, config)

    entries = palette_store.read_palette_csv(str(path))
    assert entries[-1]["role"] == "background"


def test_cmd_palette_edit_sets_role_without_touching_other_colors(tmp_path, fake_project):
    config = fake_project.load_config()
    path = tmp_path / "p.csv"
    palette_store.write_palette_csv(str(path), [
        {"id": 1, "hex": "111111", "label": "a"},
        {"id": 2, "hex": "222222", "label": "b"},
    ])
    args = _args(palette=str(path), target="1", new_hex="333333", role="foreground")

    ucs_main.cmd_palette_edit(args, config)

    entries = palette_store.read_palette_csv(str(path))
    assert entries[0]["hex"] == "333333"
    assert entries[0]["role"] == "foreground"
    assert "role" not in entries[1]


def test_cmd_palette_edit_without_role_flag_preserves_existing_role(tmp_path, fake_project):
    config = fake_project.load_config()
    path = tmp_path / "p.csv"
    palette_store.write_palette_csv(str(path), [{"id": 1, "hex": "111111", "label": "a", "role": "background"}])
    args = _args(palette=str(path), target="1", new_hex="333333", role=None)

    ucs_main.cmd_palette_edit(args, config)

    entries = palette_store.read_palette_csv(str(path))
    assert entries[0]["hex"] == "333333"
    assert entries[0]["role"] == "background"  # untouched, since --role wasn't passed


def test_palette_shift_parser_accepts_keep_custom_on_off_toggle():
    parser = ucs_main.build_parser()
    for value in ("on", "off", "toggle"):
        args = parser.parse_args(["palette", "shift", "p.csv", "--keep-custom", value])
        assert args.keep_custom == value


def test_palette_shift_parser_rejects_invalid_keep_custom_value():
    parser = ucs_main.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["palette", "shift", "p.csv", "--keep-custom", "sideways"])


def test_palette_shift_parser_keep_custom_defaults_to_none():
    parser = ucs_main.build_parser()
    args = parser.parse_args(["palette", "shift", "p.csv"])
    assert args.keep_custom is None


def test_cmd_palette_edit_role_none_clears_it(tmp_path, fake_project):
    config = fake_project.load_config()
    path = tmp_path / "p.csv"
    palette_store.write_palette_csv(str(path), [{"id": 1, "hex": "111111", "label": "a", "role": "background"}])
    args = _args(palette=str(path), target="1", new_hex="111111", role="none")

    ucs_main.cmd_palette_edit(args, config)

    entries = palette_store.read_palette_csv(str(path))
    assert "role" not in entries[0]


def test_cmd_palette_add_color_with_link(tmp_path, fake_project):
    config = fake_project.load_config()
    path = tmp_path / "p.csv"
    palette_store.write_palette_csv(str(path), [{"id": 1, "hex": "111111", "label": "bg", "role": "background"}])
    args = _args(path=str(path), hex="eeeeee", label="fg", role="foreground", link="1")

    ucs_main.cmd_palette_add_color(args, config)

    entries = palette_store.read_palette_csv(str(path))
    assert entries[0]["paired_id"] == 2
    assert entries[1]["paired_id"] == 1


def test_cmd_palette_edit_with_link(tmp_path, fake_project):
    config = fake_project.load_config()
    path = tmp_path / "p.csv"
    palette_store.write_palette_csv(str(path), [
        {"id": 1, "hex": "111111", "label": "bg", "role": "background"},
        {"id": 2, "hex": "222222", "label": "fg", "role": "foreground"},
    ])
    args = _args(palette=str(path), target="2", new_hex="eeeeee", role=None, link="1")

    ucs_main.cmd_palette_edit(args, config)

    entries = palette_store.read_palette_csv(str(path))
    assert entries[0]["paired_id"] == 2
    assert entries[1]["paired_id"] == 1


def test_cmd_palette_edit_link_none_unlinks(tmp_path, fake_project):
    config = fake_project.load_config()
    path = tmp_path / "p.csv"
    palette_store.write_palette_csv(str(path), [
        {"id": 1, "hex": "111111", "label": "bg", "role": "background", "paired_id": 2},
        {"id": 2, "hex": "222222", "label": "fg", "role": "foreground", "paired_id": 1},
    ])
    args = _args(palette=str(path), target="2", new_hex="222222", role=None, link="none")

    ucs_main.cmd_palette_edit(args, config)

    entries = palette_store.read_palette_csv(str(path))
    assert "paired_id" not in entries[0]
    assert "paired_id" not in entries[1]


def test_palette_generate_reuses_existing_palette_for_same_image(tmp_path, fake_project, monkeypatch, capsys):
    monkeypatch.setattr(cr, "HOME", str(fake_project.fakehome))
    config = fake_project.load_config()
    img = tmp_path / "wall.png"
    _make_image(img)
    parser = ucs_main.build_parser()

    args = parser.parse_args(["palette", "generate", str(img), "--colors", "4"])
    ucs_main.cmd_palette_generate(args, config)
    first_out = capsys.readouterr().out
    saved_line = [l for l in first_out.splitlines() if l.startswith("Guardada en:")][0]
    saved_path = saved_line.split("Guardada en: ")[1]
    before = open(saved_path).read()

    args2 = parser.parse_args(["palette", "generate", str(img), "--colors", "4"])
    ucs_main.cmd_palette_generate(args2, config)
    out2 = capsys.readouterr().out
    assert "Ya existe una paleta generada para esta imagen" in out2
    assert saved_path in out2
    assert open(saved_path).read() == before  # not rewritten


def test_palette_generate_regenerate_flag_forces_fresh_generation_at_same_path(
    tmp_path, fake_project, monkeypatch, capsys
):
    monkeypatch.setattr(cr, "HOME", str(fake_project.fakehome))
    config = fake_project.load_config()
    img = tmp_path / "wall.png"
    _make_image(img)
    parser = ucs_main.build_parser()

    args = parser.parse_args(["palette", "generate", str(img), "--colors", "4"])
    ucs_main.cmd_palette_generate(args, config)
    saved_path = [l for l in capsys.readouterr().out.splitlines()
                  if l.startswith("Guardada en:")][0].split("Guardada en: ")[1]

    args2 = parser.parse_args(["palette", "generate", str(img), "--colors", "4", "--regenerate"])
    ucs_main.cmd_palette_generate(args2, config)
    out2 = capsys.readouterr().out
    assert "Ya existe una paleta generada" not in out2
    assert f"Guardada en: {saved_path}" in out2  # regenerated IN PLACE, same slot


def test_palette_generate_regenerate_without_colors_falls_back_to_existing_count(
    tmp_path, fake_project, monkeypatch, capsys
):
    monkeypatch.setattr(cr, "HOME", str(fake_project.fakehome))
    config = fake_project.load_config()
    img = tmp_path / "wall.png"
    _make_image(img)
    parser = ucs_main.build_parser()

    args = parser.parse_args(["palette", "generate", str(img), "--colors", "3"])
    ucs_main.cmd_palette_generate(args, config)
    capsys.readouterr()

    args2 = parser.parse_args(["palette", "generate", str(img), "--regenerate"])  # no --colors
    ucs_main.cmd_palette_generate(args2, config)
    out2 = capsys.readouterr().out
    assert "(3 color(es))" in out2  # fell back to the existing palette's own count


def test_palette_edit_apply_binds_mapping_to_the_edited_palette(fake_project, monkeypatch, tmp_path):
    """Same class of bug as `automatic --from-image` (see the test right
    below): `ucs palette edit <path> ... --apply` with no explicit --mapping
    must bind to <path>'s OWN registry section (creating/seeding it if
    needed), not silently keep using/saving into whatever was active before."""
    monkeypatch.setattr(cr, "HOME", str(fake_project.fakehome))
    fake_project.make_file("app/style.css", "#cbff29")
    config = fake_project.load_config()
    detected = detect_diff.detect_with_route(config)["colors"]

    other_palette = os.path.join(config.palettes_created_dir, "other.csv")
    palette_store.write_palette_csv(other_palette, [{"id": 1, "hex": "000000", "label": ""}])
    registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
    registry.for_palette(other_palette, old_palette=config.detected_palette_csv).add_or_update(detected[0]["id"], 1)

    path = tmp_path / "target.csv"
    palette_store.write_palette_csv(str(path), [{"id": 1, "hex": "111111", "label": ""}])

    args = _args(palette=str(path), target="1", new_hex="ff00aa", role=None,
                 apply=True, test=False, yolo=False)
    ucs_main.cmd_palette_edit(args, config)

    registry_after = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
    target_store = registry_after.for_palette(str(path), set_active=False)
    assert target_store.resolved_entries() != []
    assert registry_after.active_palette_path() == str(path)


def test_automatic_from_image_binds_the_applied_mapping_to_the_generated_palette(
    tmp_path, fake_project, monkeypatch, capsys
):
    """Real bug report: `ucs automatic --from-image ...` applied correctly,
    but never associated the mapping it actually used with the generated/
    reused palette's OWN registry section -- it stayed on whatever was
    "active" before the command ran. Opening that exact palette afterward
    (e.g. via the GUI's "Importar paleta") then found an empty mapping.
    After this fix, the palette that was actually generated/applied must
    have its own section populated with what was used, and become active."""
    monkeypatch.setattr(cr, "HOME", str(fake_project.fakehome))
    fake_project.make_file("app/style.css", "#cbff29")
    config = fake_project.load_config()
    detected = detect_diff.detect_with_route(config)["colors"]

    # An "active" mapping already exists (built earlier, e.g. via the GUI),
    # targeting some unrelated prior palette -- this is the "recipe" that
    # `automatic --from-image` is meant to reuse.
    old_target = os.path.join(config.palettes_created_dir, "yesterday.csv")
    palette_store.write_palette_csv(old_target, [{"id": 1, "hex": "111111", "label": ""}])
    registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
    registry.for_palette(old_target, old_palette=config.detected_palette_csv).add_or_update(detected[0]["id"], 1)

    img = tmp_path / "wall.png"
    _make_image(img)
    parser = ucs_main.build_parser()
    args = parser.parse_args(["automatic", "apply", "--from-image", str(img), "--colors", "3", "--yolo"])
    ucs_main.cmd_automatic(args, config)

    target_path = palette_store.find_palettes_for_image(config.palettes_created_dir, str(img))[0]
    registry_after = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
    target_store = registry_after.for_palette(target_path, set_active=False)
    assert target_store.resolved_entries() != []  # not left empty -- the actual bug report
    assert registry_after.active_palette_path() == target_path  # now the active one, not "yesterday"


def test_palette_generate_parser_accepts_keep_custom():
    parser = ucs_main.build_parser()
    args = parser.parse_args(["palette", "generate", "wall.png", "--keep-custom", "off"])
    assert args.keep_custom == "off"


def test_automatic_from_image_parser_accepts_keep_custom():
    parser = ucs_main.build_parser()
    args = parser.parse_args(["automatic", "apply", "--from-image", "wall.png", "--keep-custom", "on"])
    assert args.keep_custom == "on"


def test_palette_generate_parser_accepts_eco():
    parser = ucs_main.build_parser()
    args = parser.parse_args(["palette", "generate", "wall.png", "--eco", "off"])
    assert args.eco == "off"


def test_automatic_from_image_parser_accepts_eco():
    parser = ucs_main.build_parser()
    args = parser.parse_args(["automatic", "apply", "--from-image", "wall.png", "--eco", "toggle"])
    assert args.eco == "toggle"


def test_palette_shift_parser_accepts_eco():
    parser = ucs_main.build_parser()
    args = parser.parse_args(["palette", "shift", "p.csv", "--eco", "on"])
    assert args.eco == "on"


def test_palette_shift_parser_accepts_hallucinate_on_off_toggle():
    parser = ucs_main.build_parser()
    for value in ("on", "off", "toggle"):
        args = parser.parse_args(["palette", "shift", "p.csv", "--hallucinate", value])
        assert args.hallucinate == value


def test_palette_shift_parser_rejects_invalid_hallucinate_value():
    parser = ucs_main.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["palette", "shift", "p.csv", "--hallucinate", "sideways"])


def test_palette_shift_parser_hallucinate_defaults_to_none():
    parser = ucs_main.build_parser()
    args = parser.parse_args(["palette", "shift", "p.csv"])
    assert args.hallucinate is None


def test_palette_generate_parser_accepts_hallucinate():
    parser = ucs_main.build_parser()
    args = parser.parse_args(["palette", "generate", "wall.png", "--hallucinate", "off"])
    assert args.hallucinate == "off"


def test_automatic_from_image_parser_accepts_hallucinate():
    parser = ucs_main.build_parser()
    args = parser.parse_args(["automatic", "apply", "--from-image", "wall.png", "--hallucinate", "toggle"])
    assert args.hallucinate == "toggle"
