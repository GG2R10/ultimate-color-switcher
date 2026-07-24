#!/usr/bin/env python3
"""app.py — Adw.Application entrypoint for the Color Switcher GUI."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw

from .window_main import MainWindow


class ColorSwitcherApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.consequences.ColorSwitcher")
        self.window = None

    def do_activate(self):
        if self.window is None:
            self.window = MainWindow(application=self)
        self.window.present()


def main():
    return ColorSwitcherApp().run([])


if __name__ == "__main__":
    raise SystemExit(main())
