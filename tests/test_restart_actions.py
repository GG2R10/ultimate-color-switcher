import json
import time

from color_switcher.backend import palette_store as ps
from color_switcher.backend import restart_actions as ra


def test_read_returns_defaults_when_key_missing(fake_project):
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()
    # fake_project.load_config() always writes restart_actions explicitly
    # (safety default, see conftest.py) -- simulate a config.json that
    # genuinely never had the key by removing it after the fact. This test
    # only ever calls read_restart_actions (a pure read), never run_enabled,
    # so it stays safe regardless.
    config_path = config.project_dir + "/config.json"
    with open(config_path) as f:
        raw = json.load(f)
    del raw["restart_actions"]
    with open(config_path, "w") as f:
        json.dump(raw, f)

    actions = ra.read_restart_actions(config)
    assert actions == ra.DEFAULT_ACTIONS
    assert actions is not ra.DEFAULT_ACTIONS  # must be a copy, not the shared module list


def test_write_then_read_roundtrip(fake_project):
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()
    custom = [{"label": "Custom", "command": "true", "enabled": False}]
    ra.write_restart_actions(config, custom)
    assert ra.read_restart_actions(config) == custom


def test_write_preserves_other_config_keys(fake_project):
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()
    ra.write_restart_actions(config, [{"label": "x", "command": "true", "enabled": True}])

    with open(config.project_dir + "/config.json") as f:
        raw = json.load(f)
    assert raw["files_to_replace"] == fake_project.files
    assert raw["backup_dir"] == str(fake_project.backup_dir)
    assert raw["restart_actions"] == [{"label": "x", "command": "true", "enabled": True}]


def test_write_keeps_config_json_human_readable(fake_project):
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()
    ra.write_restart_actions(config, [{"label": "x", "command": "true", "enabled": True}])

    with open(config.project_dir + "/config.json") as f:
        lines = f.read().splitlines()
    files_line = next(i for i, l in enumerate(lines) if "files_to_replace" in l)
    assert lines[files_line + 1].strip().startswith('"')  # each path on its own line


def test_run_enabled_only_launches_enabled_actions_and_does_not_block():
    actions = [
        {"label": "sleeper", "command": "sleep 5", "enabled": True},
        {"label": "disabled", "command": "sleep 5", "enabled": False},
    ]
    start = time.monotonic()
    results = ra.run_enabled(actions)
    elapsed = time.monotonic() - start

    assert elapsed < 1.0  # never waits on the child, regardless of its sleep
    assert len(results) == 1
    assert results[0]["label"] == "sleeper"
    assert results[0]["started"] is True


def test_run_enabled_reports_error_for_bad_command():
    actions = [{"label": "bad", "command": "/does/not/exist-binary-xyz", "enabled": True}]
    results = ra.run_enabled(actions)
    assert len(results) == 1
    # shell=True means the shell itself starts fine even if the target binary
    # doesn't exist (the shell reports "command not found" on its own stderr,
    # which we discard) -- Popen only fails to start for OS-level exec errors.
    assert results[0]["started"] is True


def test_wallpaper_env_returns_empty_without_a_palette_path():
    assert ra.wallpaper_env(None) == {}
    assert ra.wallpaper_env("") == {}


def test_wallpaper_env_returns_empty_when_the_image_is_missing_on_disk(tmp_path):
    palette_path = str(tmp_path / "palette.csv")
    meta = ps.default_meta()
    meta.update(generated=True, image=str(tmp_path / "gone.png"))
    ps.write_palette_csv(palette_path, [{"id": 1, "hex": "ff0000", "label": "a"}], meta=meta)

    assert ra.wallpaper_env(palette_path) == {}


def test_wallpaper_env_prefers_preview_image_and_expands_it(tmp_path):
    wallpaper = tmp_path / "wall.png"
    wallpaper.write_bytes(b"fake-image")
    palette_path = str(tmp_path / "palette.csv")
    meta = ps.default_meta()
    meta.update(generated=False, image=None, preview_image=str(wallpaper))
    ps.write_palette_csv(palette_path, [{"id": 1, "hex": "ff0000", "label": "a"}], meta=meta)

    assert ra.wallpaper_env(palette_path) == {"UCS_WALLPAPER": str(wallpaper)}


def test_run_enabled_merges_extra_env_into_the_launched_command(tmp_path):
    out_file = tmp_path / "env_out.txt"
    actions = [{"label": "dump-env", "command": f'echo "$UCS_WALLPAPER" > "{out_file}"', "enabled": True}]

    ra.run_enabled(actions, extra_env={"UCS_WALLPAPER": "/tmp/example-wallpaper.png"})

    for _ in range(50):
        if out_file.exists() and out_file.read_text().strip():
            break
        time.sleep(0.05)
    assert out_file.read_text().strip() == "/tmp/example-wallpaper.png"


def test_run_enabled_skips_run_on_cli_false_actions_only_when_cli(tmp_path):
    actions = [
        {"label": "gui-only", "command": "true", "enabled": True, "run_on_cli": False},
        {"label": "everywhere", "command": "true", "enabled": True},
    ]

    from_gui = ra.run_enabled(actions)
    assert {r["label"] for r in from_gui} == {"gui-only", "everywhere"}

    from_cli = ra.run_enabled(actions, cli=True)
    assert {r["label"] for r in from_cli} == {"everywhere"}


def test_write_wallpaper_state_records_the_resolved_wallpaper(fake_project, tmp_path):
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()
    wallpaper = tmp_path / "wall.png"
    wallpaper.write_bytes(b"fake-image")
    palette_path = str(tmp_path / "palette.csv")
    meta = ps.default_meta()
    meta.update(generated=False, image=None, preview_image=str(wallpaper))
    ps.write_palette_csv(palette_path, [{"id": 1, "hex": "ff0000", "label": "a"}], meta=meta)

    ra.write_wallpaper_state(config, palette_path)

    assert open(config.wallpaper_state_file).read().strip() == str(wallpaper)


def test_write_wallpaper_state_clears_the_file_when_theres_nothing_to_record(fake_project):
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()
    ra.write_wallpaper_state(config, "/tmp/no/such/palette.csv")
    assert open(config.wallpaper_state_file).read() == ""

    # And clears a previously-written value, rather than leaving it stale.
    with open(config.wallpaper_state_file, "w") as f:
        f.write("/tmp/stale-wallpaper.png\n")
    ra.write_wallpaper_state(config, None)
    assert open(config.wallpaper_state_file).read() == ""
