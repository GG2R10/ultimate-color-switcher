import argparse

import pytest

from color_switcher import main as ucs_main
from color_switcher.backend import color_replacer as cr
from color_switcher.backend import detect_diff, mapping_store, palette_store


def _args(**kwargs):
    return argparse.Namespace(mapping=None, force=False, **kwargs)


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
