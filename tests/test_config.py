import json
import os

from color_switcher.backend import config as cfg


def test_load_config_resolves_dirs_and_expands(fake_project):
    fake_project.make_file("app/style.css", ".a{color:#ffffff;}")
    config = fake_project.load_config()

    assert config.files_to_replace == fake_project.files
    assert config.backup_dir == str(fake_project.backup_dir)
    assert os.path.isdir(config.mappings_dir)
    assert os.path.isdir(config.palettes_created_dir)
    assert os.path.isdir(config.palettes_detected_dir)
    assert config.detected_palette_csv == os.path.join(config.palettes_detected_dir, "detected_palette.csv")
    assert config.mapping_csv == os.path.join(config.mappings_dir, "mapping.csv")
    assert config.generated_palette_csv == os.path.join(config.palettes_created_dir, "generated.csv")


def test_load_config_defaults_optional_lists(fake_project):
    fake_project.make_file("app/style.css", "#ffffff")
    config = fake_project.load_config()
    assert config.silent_apps == []
    assert config.restart_commands == []


def test_load_config_reads_silent_apps_and_restart_commands(fake_project):
    fake_project.make_file("app/style.css", "#ffffff")
    config = fake_project.load_config(silent_apps=["waybar"], restart_commands=["pkill waybar"])
    assert config.silent_apps == ["waybar"]
    assert config.restart_commands == ["pkill waybar"]


def test_to_home_relative_converts_path_under_home(monkeypatch, tmp_path):
    fakehome = tmp_path / "fakehome"
    fakehome.mkdir()
    monkeypatch.setenv("HOME", str(fakehome))
    result = cfg.to_home_relative(str(fakehome / "app" / "style.css"))
    assert result == "$HOME/app/style.css"


def test_to_home_relative_leaves_external_path_absolute(monkeypatch, tmp_path):
    fakehome = tmp_path / "fakehome"
    fakehome.mkdir()
    monkeypatch.setenv("HOME", str(fakehome))
    external = tmp_path / "elsewhere" / "style.css"
    assert cfg.to_home_relative(str(external)) == str(external)


def test_read_files_to_replace_returns_raw_unexpanded_values(fake_project):
    fake_project.make_file("app/style.css", "#ffffff")
    config = fake_project.load_config()
    with open(config.project_dir + "/config.json") as f:
        raw = json.load(f)
    assert cfg.read_files_to_replace(config) == raw["files_to_replace"]


def test_write_files_to_replace_roundtrip_and_preserves_other_keys(fake_project):
    fake_project.make_file("app/style.css", "#ffffff")
    config = fake_project.load_config()

    cfg.write_files_to_replace(config, ["$HOME/a.css", "$HOME/b.css"])
    assert cfg.read_files_to_replace(config) == ["$HOME/a.css", "$HOME/b.css"]

    with open(config.project_dir + "/config.json") as f:
        raw = json.load(f)
    assert raw["backup_dir"] == str(fake_project.backup_dir)


def test_load_config_bootstraps_missing_config_json(tmp_path):
    project_dir = tmp_path / "fresh"  # never touched -- no config.json yet
    config = cfg.load_config(project_dir=str(project_dir))

    assert config.files_to_replace == []
    assert os.path.isfile(project_dir / "config.json")
    with open(project_dir / "config.json") as f:
        raw = json.load(f)
    assert raw == cfg._DEFAULT_CONFIG_JSON


def test_default_project_dir_respects_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cfg._default_project_dir() == str(tmp_path / "ucs")


def test_default_project_dir_falls_back_to_dot_config(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cfg._default_project_dir() == str(tmp_path / ".config" / "ucs")
