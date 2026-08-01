#!/usr/bin/env python3
"""
dialogs/common.py — Generic building blocks shared across the other dialogs
submodules: a toast shortcut, the confirm/text/color prompts, the
import-palette/image file pickers, and the two small style-restyling and
dialog-shell helpers every hand-built dialog leans on.
"""

import tempfile
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk
from PIL import Image


def toast(toast_overlay: Adw.ToastOverlay, message: str, timeout: int = 3):
    toast_overlay.add_toast(Adw.Toast.new(message))


_GIF_PREVIEW_CACHE = os.path.join(tempfile.gettempdir(), "ucs-gif-preview.png")


def resolve_gif_safe_image_source(image_path: str) -> str:
    """Gtk.Picture/Gtk.Image load files through the container's gdk-pixbuf
    loaders, which may not include a GIF one at all -- unlike a missing/
    corrupt static image, that fails to render anything rather than
    degrading gracefully. Pillow ships its own GIF decoder as an app
    dependency already (see palette_generator/color_entry.py), so for .gif
    we bake just the first frame to a cached PNG and point the widget at
    that instead. Any other format is returned untouched. Shared by the
    wallpaper preview panel and the "Manage palettes" thumbnails."""
    if not image_path.lower().endswith(".gif"):
        return image_path
    try:
        with Image.open(image_path) as im:
            im.seek(0)
            im.convert("RGB").save(_GIF_PREVIEW_CACHE, "PNG")
        return _GIF_PREVIEW_CACHE
    except Exception:
        return image_path


def _find_by_css_class(widget: Gtk.Widget, css_class: str):
    child = widget.get_first_child()
    while child is not None:
        if child.has_css_class(css_class):
            return child
        found = _find_by_css_class(child, css_class)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def _center_group_title(group: Adw.PreferencesGroup) -> None:
    """AdwPreferencesGroup hardcodes its title/description labels left-aligned
    with no public API to restyle them, so we reach into its (stable, but
    private) template tree for the '.heading'/'.dimmed' labels and
    recenter+enlarge the title / shrink the description a notch (own
    .ucs-section-title/.ucs-section-description classes in color_chip._BASE_CSS,
    since libadwaita's own title-* scale turned out too subtle a bump here).
    Purely cosmetic and best-effort: if a future libadwaita ever restructures
    this, the lookups just return None and this becomes a silent no-op."""
    title_label = _find_by_css_class(group, "heading")
    if title_label is not None:
        title_label.set_xalign(0.5)
        title_label.set_halign(Gtk.Align.CENTER)
        title_label.add_css_class("ucs-section-title")
    description_label = _find_by_css_class(group, "dimmed")
    if description_label is not None:
        description_label.add_css_class("ucs-section-description")


def _build_dialog_shell(title: str, content: Gtk.Widget, footer_buttons: list,
                        width: int, height: int, hide_title_buttons: bool = False) -> Adw.Dialog:
    """Common shell for the hand-built (non-Adw.PreferencesDialog) dialogs:
    an Adw.ToolbarView with a HeaderBar on top, `content` in a scrollable
    area, and a right-aligned footer button row, wrapped in an Adw.Dialog of
    the given size. `footer_buttons` are Gtk.Buttons the caller already built
    and wired up -- this only lays them out. Returns the (not yet presented)
    dialog so the caller can add its own extra wiring (e.g. a "closed" signal)
    before calling dialog.present(parent)."""
    toolbar_view = Adw.ToolbarView()
    header = Adw.HeaderBar(show_start_title_buttons=not hide_title_buttons,
                           show_end_title_buttons=not hide_title_buttons)
    header.set_title_widget(Adw.WindowTitle(title=title))
    toolbar_view.add_top_bar(header)
    toolbar_view.set_content(Gtk.ScrolledWindow(child=content, vexpand=True))

    footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, halign=Gtk.Align.END, spacing=8,
                     margin_top=8, margin_bottom=12, margin_end=12)
    for button in footer_buttons:
        footer.append(button)
    toolbar_view.add_bottom_bar(footer)

    dialog = Adw.Dialog.new()
    dialog.set_title(title)
    dialog.set_content_width(width)
    dialog.set_content_height(height)
    dialog.set_child(toolbar_view)
    return dialog


def ask_confirm(parent: Gtk.Widget, heading: str, body: str, ok_label: str, on_confirm,
                 destructive: bool = False):
    dialog = Adw.AlertDialog.new(heading, body)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("ok", ok_label)
    dialog.set_response_appearance(
        "ok", Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.SUGGESTED
    )
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")

    def _on_response(_d, response):
        if response == "ok":
            on_confirm()

    dialog.connect("response", _on_response)
    dialog.present(parent)


def ask_choice(parent: Gtk.Widget, heading: str, body: str, choices: list, on_choice,
               extra_child: Gtk.Widget = None):
    """Generic N-way Adw.AlertDialog (ask_confirm generalized past a single
    ok/cancel) -- used for the "use existing palette / regenerate" reuse
    prompt, and any future 3+-way confirm. choices: [(response_id, label,
    appearance_or_None), ...]; appearance is an Adw.ResponseAppearance.* or
    None for the default look. A "cancel" response is always added and never
    calls on_choice. extra_child, if given, is shown inside the dialog body
    (e.g. a small image preview)."""
    dialog = Adw.AlertDialog.new(heading, body)
    if extra_child is not None:
        dialog.set_extra_child(extra_child)
    dialog.add_response("cancel", "Cancel")
    for response_id, label, appearance in choices:
        dialog.add_response(response_id, label)
        if appearance is not None:
            dialog.set_response_appearance(response_id, appearance)
    dialog.set_default_response(choices[0][0] if choices else "cancel")
    dialog.set_close_response("cancel")

    def _on_response(_d, response):
        if response != "cancel":
            on_choice(response)

    dialog.connect("response", _on_response)
    dialog.present(parent)


def prompt_text(parent: Gtk.Widget, heading: str, body: str, placeholder: str, ok_label: str, on_submit):
    """Generic single-line text prompt (used for palette filenames / labels).
    placeholder is shown as a greyed hint only -- an EXAMPLE (e.g.
    "my-palette", or a computed suggested count), never submitted verbatim
    if the entry is left untouched (entry.get_text() is "" in that case, not
    the placeholder) -- callers whose placeholder is a real fallback value
    should treat "" the same as that value themselves (see
    MainWindow._on_generate_palette_count)."""
    dialog = Adw.AlertDialog.new(heading, body)
    entry = Gtk.Entry(placeholder_text=placeholder)
    entry.set_activates_default(True)
    dialog.set_extra_child(entry)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("ok", ok_label)
    dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("ok")
    dialog.set_close_response("cancel")

    def _on_response(_d, response):
        if response == "ok":
            on_submit(entry.get_text().strip())

    dialog.connect("response", _on_response)
    dialog.present(parent)


def _rgba_to_hex(rgba: Gdk.RGBA) -> str:
    r = round(rgba.red * 255)
    g = round(rgba.green * 255)
    b = round(rgba.blue * 255)
    return f"{r:02x}{g:02x}{b:02x}"


def prompt_add_color(parent: Gtk.Widget, on_add):
    """Native color picker, then an inline prompt for the label. Calls
    on_add(hex_no_hash, label)."""
    color_dialog = Gtk.ColorDialog(with_alpha=False)

    def _on_color_chosen(dialog, result):
        try:
            rgba = dialog.choose_rgba_finish(result)
        except Exception:
            return  # user cancelled
        hex_value = _rgba_to_hex(rgba)

        def _on_label(label):
            on_add(hex_value, label)

        prompt_text(
            parent,
            f"Color #{hex_value}",
            "Label for this color in the palette (optional):",
            "e.g. primary",
            "Add",
            _on_label,
        )

    color_dialog.choose_rgba(parent, None, None, _on_color_chosen)


def prompt_pick_color(parent: Gtk.Widget, on_pick, initial_hex=None):
    """Native color picker (no label step), pre-set to initial_hex if given.
    Calls on_pick(hex_no_hash). For editing an existing palette color."""
    color_dialog = Gtk.ColorDialog(with_alpha=False)
    initial = None
    if initial_hex:
        rgba = Gdk.RGBA()
        if rgba.parse("#" + initial_hex.lstrip("#")):
            initial = rgba

    def _on_chosen(dialog, result):
        try:
            rgba = dialog.choose_rgba_finish(result)
        except Exception:
            return  # user cancelled
        on_pick(_rgba_to_hex(rgba))

    color_dialog.choose_rgba(parent, initial, None, _on_chosen)


def pick_import_palette_file(parent: Gtk.Widget, on_selected):
    file_dialog = Gtk.FileDialog(title="Import palette CSV")
    csv_filter = Gtk.FileFilter(name="Palette CSV")
    csv_filter.add_pattern("*.csv")
    filters = Gio.ListStore.new(Gtk.FileFilter)
    filters.append(csv_filter)
    file_dialog.set_filters(filters)

    def _on_selected(dialog, result):
        try:
            gfile = dialog.open_finish(result)
        except Exception:
            return  # user cancelled
        on_selected(gfile.get_path())

    file_dialog.open(parent, None, _on_selected)


def pick_image_file(parent: Gtk.Widget, on_selected):
    file_dialog = Gtk.FileDialog(title="Choose image (wallpaper)")
    image_filter = Gtk.FileFilter(name="Images")
    for pattern in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp", "*.gif"):
        image_filter.add_pattern(pattern)
    filters = Gio.ListStore.new(Gtk.FileFilter)
    filters.append(image_filter)
    file_dialog.set_filters(filters)

    def _on_selected(dialog, result):
        try:
            gfile = dialog.open_finish(result)
        except Exception:
            return  # user cancelled
        on_selected(gfile.get_path())

    file_dialog.open(parent, None, _on_selected)
