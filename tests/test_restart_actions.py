import json
import time

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
