import argparse
import os

import pytest

from color_switcher import main as ucs_main
from color_switcher.backend import color_detector as cd
from color_switcher.backend import color_replacer as cr
from color_switcher.backend import detect_diff, mapping_store, palette_store


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


def test_apply_or_test_errors_cleanly_on_out_of_range_new_id(fake_project, monkeypatch, capsys):
    config, detected, store = _setup(fake_project, monkeypatch)
    store.add_or_update(detected[0]["id"], 99)  # the palette only has id 1

    with pytest.raises(SystemExit) as exc:
        ucs_main.cmd_test(_args(), config)
    assert exc.value.code == 1
    assert "no existen" in capsys.readouterr().out
    # cmd_apply shares the exact same _apply_or_test body -- must fail identically
    with pytest.raises(SystemExit) as exc:
        ucs_main.cmd_apply(_args(), config)
    assert exc.value.code == 1


def test_apply_or_test_errors_cleanly_when_new_palette_file_is_missing(fake_project, monkeypatch, capsys):
    config, detected, store = _setup(fake_project, monkeypatch)
    store.new_palette = str(fake_project.tmp_path / "nope.csv")
    store.add_or_update(detected[0]["id"], 1)
    store.save()

    with pytest.raises(SystemExit) as exc:
        ucs_main.cmd_test(_args(), config)
    assert exc.value.code == 1
    assert "no existen" in capsys.readouterr().out


def test_apply_or_test_still_works_on_a_healthy_mapping(fake_project, monkeypatch, capsys):
    config, detected, store = _setup(fake_project, monkeypatch)
    store.add_or_update(detected[0]["id"], 1)

    ucs_main.cmd_test(_args(), config)  # must NOT raise
    out = capsys.readouterr().out
    assert "1 reemplazos" in out


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
