import json
import os
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from color_switcher.backend.config import load_config  # noqa: E402


class FakeProject:
    """Builds an isolated project (fake $HOME + project dir + backup dir)
    under tmp_path, so tests never touch the user's real dotfiles."""

    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.fakehome = tmp_path / "fakehome"
        self.project_dir = tmp_path / "project"
        self.backup_dir = tmp_path / "backup"
        self.fakehome.mkdir()
        self.project_dir.mkdir()
        self.files = []

    def make_file(self, relname: str, content: str) -> str:
        fp = self.fakehome / relname
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        path_str = str(fp)
        if path_str not in self.files:
            self.files.append(path_str)
        return path_str

    def load_config(self, silent_apps=None, restart_commands=None):
        cfg = {"files_to_replace": self.files, "backup_dir": str(self.backup_dir)}
        if silent_apps is not None:
            cfg["silent_apps"] = silent_apps
        if restart_commands is not None:
            cfg["restart_commands"] = restart_commands
        (self.project_dir / "config.json").write_text(json.dumps(cfg))
        return load_config(project_dir=str(self.project_dir))


@pytest.fixture
def fake_project(tmp_path):
    return FakeProject(tmp_path)
