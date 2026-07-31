#!/usr/bin/env python3
"""
main.py — Color Switcher CLI: detect -> mapping -> conflicts -> apply/test
-> restore -> automatic, plus `gui` (the default with no subcommand) to
launch the GTK4 + libadwaita window.

Examples:
  ucs                   # same as: ucs gui
  ucs detect
  ucs palette create palettes/created/my-theme.csv \\
      --add ff00aa primary --add 00ccff secondary
  ucs mapping new palettes/created/my-theme.csv
  ucs mapping show mappings/mapping.csv
  ucs test  --mapping mappings/mapping.csv
  ucs apply --mapping mappings/mapping.csv
  ucs restore
  ucs automatic palette.csv --mapping mappings/mapping.csv
"""

import argparse
import json
import os
import sys

from .backend import (
    color_detector,
    color_replacer,
    conflicts,
    detect_diff,
    guiless,
    mapping_store,
    palette_generator,
    palette_shift,
    palette_store,
    restart_actions,
)
from .backend.config import load_config, read_files_to_replace, to_home_relative, write_files_to_replace


def _report_mapping_drift(config, detected_colors, persist=False):
    """Compare the ACTIVE palette's identity-stamped mapping entries (see
    mapping_store.refresh_identity_stamps) against a fresh detected_colors
    scan and print a summary. Deliberately scoped to the ACTIVE mapping
    ONLY -- every OTHER (inactive) palette's mapping is, by definition,
    "orphaned" against whatever palette IS currently applied (only one
    wallpaper's colors can physically be in the files at any given time),
    which isn't real, actionable drift -- it's the routine, permanent state
    of every mapping that isn't the one currently in use. Checking/warning
    about ALL of them on every detection was pure noise (real bug report:
    switching wallpapers printed a false "N colores huérfanos" for every
    OTHER previously-used palette). Inactive mappings get correctly
    re-stamped automatically the next time THEY become active again (see
    stamp_applied_entries) -- not checked continuously in the background.
    persist=True (only right after a REAL apply, where files on disk
    actually changed under us) also writes the refreshed stamp back;
    persist=False (e.g. a bare `ucs detect`) never touches the file -- it
    just reports."""
    registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
    store = registry.for_active()
    if not store.entries:
        return
    new_entries, drift = mapping_store.refresh_identity_stamps(store.entries, detected_colors)
    if persist:
        store.entries = new_entries
        store.save()
    if drift["driftable"]:
        print(f"⚠ {len(drift['driftable'])} mapped color(s) changed id on the last scan "
              "(the real color still exists, it just moved position). Run "
              "'ucs mapping relink' to fix them.")
    if drift["orphaned"]:
        print(f"⚠ {len(drift['orphaned'])} mapped color(s) no longer appear in your scanned files. "
              "Check them by hand ('ucs mapping show').")


def cmd_detect(args, config):
    result = detect_diff.detect_with_route(config, save=not args.dry_run)
    route = result["route"]

    if route == "a":
        print("Route a: first detection (no previous detected_palette.csv).")
    elif route == "b":
        print("Route b: detection matches what's saved. No changes.")
    else:
        d = result["diff"]
        print("Route c: the detected colors changed since last time.")
        if d["added"]:
            print(f"  New ({len(d['added'])}):")
            for c in d["added"]:
                print(f"    + #{c['color']} ({c['type']}) x{c['count']}")
        if d["removed"]:
            print(f"  Gone ({len(d['removed'])}):")
            for c in d["removed"]:
                print(f"    - #{c['color']} ({c['type']}) x{c['count']}")
        print("  A new mapping is recommended.")

    print(f"\nDetected colors: {len(result['colors'])}")
    for c in result["colors"]:
        print(f"  ID {c['id']:>3} | {c['type']:<12} | #{c['color']} | x{c['count']:<4} | {len(c['files'])} file(s)")

    if not args.dry_run:
        print(f"\nSaved to: {config.detected_palette_csv}")

    _report_mapping_drift(config, result["colors"], persist=False)


def cmd_config_files_list(args, config):
    files = read_files_to_replace(config)
    if not files:
        print("No files configured to scan.")
        return
    for f in files:
        marker = "" if os.path.isfile(color_detector.expand_path(f)) else "  (not found)"
        print(f"  {f}{marker}")


def cmd_config_files_add(args, config):
    files = read_files_to_replace(config)
    entry = to_home_relative(args.path)
    if entry in files:
        print(f"Already in the list: {entry}")
        return
    files.append(entry)
    write_files_to_replace(config, files)
    print(f"Added: {entry}")
    print("Run 'ucs detect' to refresh the detected colors.")


def cmd_config_files_remove(args, config):
    files = read_files_to_replace(config)
    target_expanded = color_detector.expand_path(args.path)
    remaining = [f for f in files if f != args.path and color_detector.expand_path(f) != target_expanded]
    if len(remaining) == len(files):
        print(f"Wasn't in the list: {args.path}")
        return
    write_files_to_replace(config, remaining)
    print(f"Removed: {args.path}")
    print("Run 'ucs detect' to refresh the detected colors.")


def cmd_config_files_scan(args, config):
    print("Looking for files with colors under ~/.config …")
    found = color_detector.scan_config_dir_for_color_files()
    if not found:
        print("No file with colors was found.")
        return

    existing = set(read_files_to_replace(config))
    new_paths = [to_home_relative(p) for p in found]
    new_paths = [hr for hr in new_paths if hr not in existing]

    for folder, files in color_detector.group_paths_by_top_level(found):
        print(f"\n{to_home_relative(folder)}/")
        for p, display in files:
            hr = to_home_relative(p)
            marker = "  (already in the list)" if hr in existing else ""
            print(f"    {display}{marker}")

    print(f"\nTotal: {len(found)} file(s) with colors, {len(new_paths)} new.")
    print("⚠ May include hex-looking values that aren't colors (e.g. 0xADDR addresses) or files you "
          "don't want to modify. Remove whatever doesn't apply with: ucs config files remove <path>")

    if args.dry_run:
        print("\n(--dry-run: nothing was added)")
        return
    if not new_paths:
        print("\nEverything was already in the list, nothing to add.")
        return
    if not args.yes:
        try:
            answer = input(f"\nAdd the {len(new_paths)} new file(s)? (y/N): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in ("y", "yes", "s", "si", "sí"):
            print("Cancelled.")
            return

    merged = read_files_to_replace(config)
    merged.extend(new_paths)
    write_files_to_replace(config, merged)
    print(f"Added {len(new_paths)} file(s). Run 'ucs detect' to refresh the detected colors.")


def _resolve_path(path, default_dir, project_dir=None):
    """Absolute paths are used as-is; paths that exist relative to cwd or to
    project_dir are used as-is; a bare name is resolved under default_dir."""
    if os.path.isabs(path):
        return path
    candidates = [path]
    if project_dir:
        candidates.append(os.path.join(project_dir, path))
    for c in candidates:
        if os.path.exists(c):
            return os.path.abspath(c)
    return os.path.join(default_dir, path)


def _use_color() -> bool:
    """Whether to emit ANSI color. Honors the NO_COLOR / FORCE_COLOR
    conventions, otherwise only colors when stdout is a real terminal (so
    swatches don't leak escape codes into pipes/files)."""
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


def _color_swatch(hex_str: str, width: int = 3) -> str:
    """A `width`-cell block painted with `hex_str` as its background, via an
    ANSI truecolor escape -- or "" when color output is suppressed."""
    if not _use_color():
        return ""
    h = hex_str.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"\x1b[48;2;{r};{g};{b}m{' ' * width}\x1b[0m"


_ROLE_TAGS = {"foreground": "[F]", "background": "[B]"}


def _print_palette(entries, show_id: bool = True) -> None:
    """Print a palette one color per line, each led by its swatch (falls back
    to just hex + label when color is off), with a trailing [F]/[B] tag when
    the color has a foreground/background role."""
    for e in entries:
        swatch = _color_swatch(e["hex"])
        prefix = f"  {e['id']}) " if show_id else "  "
        cells = [swatch, f"#{e['hex']}", e.get("label", ""), _ROLE_TAGS.get(e.get("role"))]
        print((prefix + "  ".join(c for c in cells if c)).rstrip())


def _role_arg_to_value(raw):
    """--role CLI value ('foreground'|'background'|'none'|None) -> internal
    role, mapping both 'none' and omission (None) to unmarked (None)."""
    return None if raw in (None, "none") else raw


def cmd_palette_create(args, config):
    path = args.path
    if not os.path.isabs(path):
        path = os.path.join(config.palettes_created_dir, path)
    entries = []
    for parts in (args.add or []):
        if len(parts) not in (2, 3):
            print(f"--add expects HEX LABEL [ROLE], got {len(parts)} value(s): {parts}")
            sys.exit(1)
        hexval, label = parts[0], parts[1]
        role = None
        if len(parts) == 3:
            role_raw = parts[2]
            if role_raw not in ("foreground", "background", "none"):
                print(f"--add: invalid role {role_raw!r} (use foreground, background, or none)")
                sys.exit(1)
            role = _role_arg_to_value(role_raw)
        next_id = max((e["id"] for e in entries), default=0) + 1
        entry = {"id": next_id, "hex": hexval.lstrip("#").lower(), "label": label}
        if role:
            entry["role"] = role
        entries.append(entry)
    palette_store.write_palette_csv(path, entries)
    print(f"Palette created: {path} ({len(entries)} color(s))")
    _print_palette(entries)
    _maybe_apply_after_edit(args, config, path, target_palette=path)


def cmd_palette_list(args, config):
    for p in palette_store.list_palettes(config.palettes_created_dir):
        entries = palette_store.read_palette_csv(p)
        strip = "".join(_color_swatch(e["hex"], width=2) for e in entries)
        sep = "  " if strip else ""
        print(f"{p} ({len(entries)} color(s)){sep}{strip}")


def cmd_palette_show(args, config):
    """Also doubles as `automatic apply <ruta-existente>`'s replacement: reads
    CSV, JSON, or (via main()'s stdin swap on '-') an already-loaded list --
    same tolerant guiless.load_palette every apply path uses -- and can
    --apply it against the mapping right after showing it."""
    resolved = _resolve_target_palette(args.path, config, mapping_path=args.mapping)
    entries = guiless.load_palette(resolved)
    label = resolved if isinstance(resolved, str) else "(stdin)"
    if not entries:
        print(f"Empty or not found palette: {label}")
        sys.exit(1)
    display = [{"id": i + 1, **e} for i, e in enumerate(entries)]
    print(f"{label} ({len(entries)} color(s)):")
    _print_palette(display)
    _maybe_apply_after_edit(args, config, resolved, mapping_path=args.mapping,
                            target_palette=resolved if isinstance(resolved, str) else None)


def cmd_palette_add_color(args, config):
    path = _resolve_target_palette(args.path, config, mapping_path=args.mapping)
    entry = palette_shift.add_color(path, args.hex, args.label or "", role=_role_arg_to_value(args.role))
    if args.link is not None:
        palette_shift.set_pair(path, entry["id"], args.link)
    swatch = _color_swatch(entry["hex"])
    cells = ["Added:", swatch, f"#{entry['hex']}", entry.get("label", "")]
    print(" ".join(c for c in cells if c))
    print("Current palette:")
    _print_palette(palette_store.read_palette_csv(path))
    _maybe_apply_after_edit(args, config, path, mapping_path=args.mapping, target_palette=path)


def _resolve_target_palette(palette_arg, config, mapping_path=None):
    """The palette every optional-palette command operates on: the given path
    if provided, otherwise whatever the mapping currently applies (its
    #new_palette=). Shared by `palette shift/edit/remove/show/add-color` --
    "which palette" is answered the same way everywhere: explicit arg wins,
    otherwise deduce it from the mapping (default: the canonical one, or an
    explicit --mapping when the caller has that flag).

    A non-string palette_arg (an already-loaded list, e.g. from main()'s
    stdin-JSON swap for `palette show -`) is returned as-is -- there is
    nothing to resolve, it's already the palette."""
    if palette_arg is not None and not isinstance(palette_arg, str):
        return palette_arg
    if palette_arg:
        return _resolve_path(palette_arg, config.palettes_created_dir, config.project_dir)
    if mapping_path:
        _old, new_p, _entries = mapping_store.read_mapping_csv(mapping_path, project_dir=config.project_dir)
    else:
        registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
        new_p = registry.active_palette_path()
    if not new_p:
        raise palette_shift.PaletteEditError(
            "No palette was given and the mapping doesn't reference any (#new_palette=). "
            "Pass the palette path explicitly."
        )
    return new_p


def _adjust_mapping_after_palette_delete(config, mapping_path, palette_path, deleted_id):
    """If the palette a color was just deleted from is the one this mapping
    applies to, unassign entries that pointed at it and shift the rest, so
    the mapping stays aligned (see mapping_store.drop_and_shift_new_id).
    mapping_path=None resolves the currently-active mapping via the registry
    (see mapping_store.MappingRegistry) -- an explicit --mapping (a
    standalone file) always wins."""
    if mapping_path:
        store = mapping_store.MappingStore(mapping_path, project_dir=config.project_dir).load()
    else:
        registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
        store = registry.for_active()
    if not store.entries or not store.new_palette:
        return
    same = (os.path.abspath(color_detector.expand_path(store.new_palette))
            == os.path.abspath(color_detector.expand_path(palette_path)))
    if not same:
        return
    adjusted = mapping_store.drop_and_shift_new_id(store.entries, deleted_id)
    dropped = sum(1 for e, a in zip(store.entries, adjusted)
                  if e["new_id"] is not None and a["new_id"] is None)
    store.entries = adjusted
    store.save()
    if dropped:
        print(f"⚠ {dropped} mapping assignment(s) pointed at that color and were left unassigned.")


def cmd_palette_edit(args, config):
    path = _resolve_target_palette(args.palette, config, mapping_path=args.mapping)
    if args.role is not None:
        new_id = palette_shift.edit_color(path, args.target, args.new_hex, role=_role_arg_to_value(args.role))
    else:
        new_id = palette_shift.edit_color(path, args.target, args.new_hex)
    if args.link is not None:
        link_target = None if args.link == "none" else args.link
        palette_shift.set_pair(path, new_id, link_target)
    print(f"Edited in {path}:")
    _print_palette(palette_store.read_palette_csv(path))
    _maybe_apply_after_edit(args, config, path, mapping_path=args.mapping, target_palette=path)


def cmd_palette_remove(args, config):
    path = _resolve_target_palette(args.palette, config, mapping_path=args.mapping)
    deleted_id = palette_shift.delete_color(path, args.target)
    _adjust_mapping_after_palette_delete(config, args.mapping, path, deleted_id)
    print(f"Removed color {deleted_id} from {path}:")
    _print_palette(palette_store.read_palette_csv(path))
    _maybe_apply_after_edit(args, config, path, mapping_path=args.mapping, target_palette=path)


def _parse_on_off(raw: str) -> bool:
    """--ying-yang on|off -- argparse type= callable (ArgumentTypeError for a
    clean CLI error instead of a traceback)."""
    v = raw.strip().lower()
    if v in ("on", "true", "1", "yes", "si", "sí"):
        return True
    if v in ("off", "false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError(f"value must be 'on' or 'off', got {raw!r}")


def _mapping_fallback_colors(config, mapping_path):
    """How many colors a fresh generation should ask for when the caller
    didn't pass --colors: the number of distinct roles the mapping actually
    needs (same convenience both `palette generate` and `automatic
    --from-image` already had), or 6 if there's no mapping to consult.
    mapping_path=None consults the currently-active mapping (see
    mapping_store.MappingRegistry) -- an explicit --mapping always wins."""
    if mapping_path:
        _old_p, _new_p, entries = mapping_store.read_mapping_csv(mapping_path, project_dir=config.project_dir)
    else:
        registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
        entries = registry.for_active().entries
    return len({e["new_id"] for e in entries}) if entries else 6


def _resolve_generate_target(config, image, explicit_out, regenerate, requested_colors, fallback_colors):
    """Decide whether to reuse an already-generated palette for this image or
    actually (re)generate one -- the "one persistent palette per wallpaper"
    model (see palette_store.find_palettes_for_image/
    default_generated_path_for_image). An explicit --out always means
    "regenerate at this exact path" (matches the old, still-supported
    escape hatch for callers that want a one-off file, never auto-reused).

    Returns (out_path, n_colors, reused_path_or_None):
      - reused_path is not None => don't call generate_and_save_palette at
        all, just read/use that existing file as-is.
      - otherwise, out_path/n_colors are ready to pass straight through."""
    existing = [] if explicit_out else palette_store.find_palettes_for_image(config.palettes_created_dir, image)

    if existing and not regenerate:
        return existing[0], None, existing[0]

    if existing and regenerate:
        out_path = explicit_out or existing[0]
        n_colors = requested_colors
        if n_colors is None:
            n_colors = len(palette_store.read_palette_csv(existing[0])) or fallback_colors
        return out_path, n_colors, None

    n_colors = requested_colors if requested_colors is not None else fallback_colors
    return explicit_out, n_colors, None


def cmd_palette_generate(args, config):
    fallback_colors = _mapping_fallback_colors(config, args.mapping)
    out_path, n_colors, reused_path = _resolve_generate_target(
        config, args.image, args.out, args.regenerate, args.colors, fallback_colors,
    )

    if reused_path:
        entries = palette_store.read_palette_csv(reused_path)
        print(f"A palette already exists for this image: {reused_path} ({len(entries)} color(s)) "
              "-- reusing it. Pass --regenerate to force a fresh generation.")
        print(f"\nSaved to: {reused_path}")
        if not args.apply:
            print(f"To apply it: ucs palette show {reused_path} --apply")
        _maybe_apply_after_edit(args, config, entries, mapping_path=args.mapping, target_palette=reused_path)
        return

    entries, saved_path, warnings = palette_shift.generate_and_save_palette(
        config, args.image, n_colors, args.sample_size, args.mode, args.my_eyes, out_path,
        scoring=args.scoring, custom_scoring_values=args.custom_scoring_values,
        weighted_contrast=args.weighted_contrast, shuffle=args.shuffle, overfetch=args.overfetch,
        ying_yang=args.ying_yang, my_eyes_factor=args.my_eyes_factor, my_eyes_max_chroma=args.my_eyes_max_chroma,
        shading_direction=args.shading_direction,
        shading_min_luminance=args.shading_min_luminance, shading_max_luminance=args.shading_max_luminance,
        keep_custom=args.keep_custom, eco=args.eco, hallucinate=args.hallucinate,
        mapping_path=args.mapping,
    )
    for w in warnings:
        print(f"⚠ {w}")

    print(f"Palette generated from {args.image} ({n_colors} color(s)):")
    _print_palette(entries)
    print(f"\nSaved to: {saved_path}")
    if not args.apply:
        print(f"To apply it: ucs palette show {saved_path} --apply")
    # entries is already the in-memory, just-computed list -- guiless accepts
    # it directly, no need to round-trip it back through the file we just wrote.
    _maybe_apply_after_edit(args, config, entries, mapping_path=args.mapping, target_palette=saved_path)


def _resolve_detected_csv(args, config):
    return args.detected_palette or config.detected_palette_csv


def cmd_mapping_new(args, config):
    detected_path = _resolve_detected_csv(args, config)
    detected_colors = color_detector.read_detected_csv(detected_path)
    if not detected_colors:
        print(f"No detected colors in {detected_path}. Run 'detect' first.")
        sys.exit(1)

    target_palette = _resolve_path(args.target_palette, config.palettes_created_dir, config.project_dir)
    new_palette = palette_store.read_palette_csv(target_palette)
    if not new_palette:
        print(f"Empty or not found target palette: {target_palette}")
        sys.exit(1)

    if args.out:
        # Explicit --out: a standalone export file, the original escape hatch
        # -- never auto-discovered/reused later, unlike the registry-backed
        # default below.
        out_path = args.out
        if not out_path.endswith(".csv"):
            out_path += ".csv"
        if not os.path.isabs(out_path):
            out_path = os.path.join(config.mappings_dir, out_path)
        store = mapping_store.MappingStore(
            out_path, old_palette=detected_path, new_palette=target_palette, project_dir=config.project_dir
        )
        location = out_path
    else:
        registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
        store = registry.for_palette(target_palette, old_palette=detected_path)
        location = f"{target_palette}  (registry: {config.mapping_registry_json})"

    siblings = conflicts.find_case2_siblings(detected_colors)
    color_by_id = {c["id"]: c for c in detected_colors}

    print(f"Target palette: {target_palette} ({len(new_palette)} color(s))")
    print("Detected colors:")
    for c in detected_colors:
        twin_note = ""
        group = siblings.get(c["color"].lower())
        if group and len(group) > 1:
            other_ids = [g["id"] for g in group if g["id"] != c["id"]]
            twin_note = f"  (same color also as id {other_ids})"
        print(f"  ID {c['id']:>3} | {c['type']:<12} | #{c['color']} | x{c['count']:<4}{twin_note}")

    print("\nTarget palette:")
    for p in new_palette:
        print(f"  ID {p['id']:>3} | #{p['hex']} | {p['label']}")

    print(f"\nEnter 'old_id new_id' pairs (empty ENTER to finish). Mapping: {location}")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            break
        parts = line.split()
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            print("Invalid format. Use: <old_id> <new_id>")
            continue
        old_id, new_id = int(parts[0]), int(parts[1])
        store.add_or_update(old_id, new_id)  # persists immediately

        collisions = conflicts.find_case1_collisions(detected_colors, new_palette, store.resolved_entries())
        relevant = [c for c in collisions if c["old_id"] == old_id]
        for c in relevant:
            print(f"  ⚠ #{c['new_hex']} already exists in the detected palette (id {c['conflict_with_ids']}).")

        old_color_entry = color_by_id.get(old_id)
        group = siblings.get(old_color_entry["color"].lower()) if old_color_entry else None
        if group and len(group) > 1:
            twins = [g["id"] for g in group if g["id"] != old_id]
            for twin_id in twins:
                if store._find(twin_id) is None:
                    print(f"  ⚠ id {old_id} also appears as id {twin_id} in another format. "
                          f"Run: {twin_id} {new_id}  (or leave it to map it differently)")

    if not store.resolved_entries():
        print("No mapping was saved (empty).")
        return

    print(f"\nMapping saved: {location} ({len(store.resolved_entries())} entries)")


def _resolve_mapping_store_for_show(args, config):
    """`mapping show`/`mapping relink` share the same 3-way resolution: an
    explicit standalone `path`/`--mapping` always wins; else `--palette`
    resolves that specific palette's own registry section (read-only, never
    changes what's active); else the currently-active mapping."""
    explicit = getattr(args, "path", None) or getattr(args, "mapping", None)
    if explicit:
        return mapping_store.MappingStore(explicit, project_dir=config.project_dir).load()
    registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
    if getattr(args, "palette", None):
        target = _resolve_path(args.palette, config.palettes_created_dir, config.project_dir)
        return registry.for_palette(target, set_active=False)
    return registry.for_active()


def _print_mapping_details(store, config) -> None:
    """The body of `mapping show`, factored out so `manage mappings show`
    can print the same detail (possibly for several sections in a row)."""
    old_p, new_p, entries = store.old_palette, store.new_palette, store.entries
    print(f"old_palette: {old_p}")
    print(f"new_palette: {new_p}")
    detected_colors = color_detector.read_detected_csv(old_p) if old_p else []
    new_palette = palette_store.read_palette_csv(new_p) if new_p else []
    color_by_id = {c["id"]: c for c in detected_colors}
    palette_by_id = {p["id"]: p for p in new_palette}
    for e in entries:
        old_c = color_by_id.get(e["old_id"])
        new_c = palette_by_id.get(e["new_id"])
        old_desc = f"#{old_c['color']}" if old_c else "?"
        new_desc = f"#{new_c['hex']} ({new_c['label']})" if new_c else "?"
        print(f"  {e['old_id']} ({old_desc}) -> {e['new_id']} ({new_desc})")

    collisions = conflicts.find_case1_collisions(detected_colors, new_palette, entries)
    if collisions:
        print("\nConflicts (case 1):")
        for c in collisions:
            print(f"  old_id {c['old_id']} -> #{c['new_hex']} collides with id(s) {c['conflict_with_ids']}")


def cmd_mapping_show(args, config):
    store = _resolve_mapping_store_for_show(args, config)
    _print_mapping_details(store, config)


def cmd_mapping_relink(args, config):
    store = _resolve_mapping_store_for_show(args, config)
    entries = store.entries
    if not entries:
        print("Empty or not found mapping.")
        return

    detected_path = store.old_palette or config.detected_palette_csv
    detected_colors = color_detector.read_detected_csv(detected_path)
    drift = mapping_store.detect_drift(entries, detected_colors)

    if not drift["driftable"] and not drift["orphaned"]:
        print("Nothing to relink: the mapping is up to date with the detected colors.")
        return

    if drift["driftable"]:
        print(f"{len(drift['driftable'])} color(s) to relink (the real color still exists, "
              "it just moved id):")
        for d in drift["driftable"]:
            print(f"  old_id {d['old_id']} -> {d['correct_old_id']}  (#{d['hex']}, {d['type']})")
    if drift["orphaned"]:
        print(f"\n{len(drift['orphaned'])} orphaned color(s) -- no longer exist, not auto-resolved:")
        for d in drift["orphaned"]:
            print(f"  old_id {d['old_id']}  (#{d['hex']}, {d['type']}) -- fix it by hand.")

    if not drift["driftable"]:
        return

    if not args.yes:
        n_driftable = len(drift["driftable"])
        noun = "entry" if n_driftable == 1 else "entries"
        try:
            answer = input(f"\nRelink {n_driftable} {noun}? (y/N): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in ("y", "yes", "s", "si", "sí"):
            print("Cancelled.")
            return

    relinked = store.apply_drift_relinks(drift["driftable"])
    print(f"\n{relinked} {'entry' if relinked == 1 else 'entries'} relinked.")


def cmd_mapping_list(args, config):
    registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
    sections = registry.all_sections()
    if not sections:
        print("No mapping yet.")
        return
    active = registry.active_palette_path()
    for palette_path, store in sections:
        marker = "  (active)" if palette_path == active else ""
        n = len(store.resolved_entries())
        print(f"  {palette_path}{marker} -- {n} {'entry' if n == 1 else 'entries'}")


def _resolve_explicit_palette_path(target: str, config) -> list:
    """A `manage mappings/palette` target that's neither None nor "all": a
    .csv path is treated as a palette path directly; anything else is
    treated as a wallpaper image and resolved to whatever palette(s) were
    generated from it, by provenance (see palette_store.find_palettes_for_image
    -- never by filename, so a renamed palette or a same-looking-but-
    unrelated filename never gets mismatched)."""
    expanded = color_detector.expand_path(target)
    if expanded.lower().endswith(".csv"):
        return [_resolve_path(target, config.palettes_created_dir, config.project_dir)]
    return palette_store.find_palettes_for_image(config.palettes_created_dir, expanded)


def _resolve_manage_palette_targets(target: str, config, registry) -> list:
    """`manage palette show/delete`'s target resolution: None -> the active
    palette (if any); "all" -> every palette CSV that actually exists under
    palettes_created_dir; else -> _resolve_explicit_palette_path."""
    if target is None:
        active = registry.active_palette_path()
        return [active] if active else []
    if target == "all":
        return palette_store.list_palettes(config.palettes_created_dir)
    return _resolve_explicit_palette_path(target, config)


def _resolve_manage_mapping_targets(target: str, config, registry) -> list:
    """`manage mappings show/delete`'s target resolution -- same idea as
    _resolve_manage_palette_targets, but "all" means every palette that HAS
    a mapping SECTION in the registry, not every palette file that exists
    (those are two different universes -- a palette can exist with no
    mapping yet, and (rarely) a mapping section can outlive its palette
    file, see palette_store.delete_palette)."""
    if target is None:
        active = registry.active_palette_path()
        return [active] if active else []
    if target == "all":
        return [palette_path for palette_path, _store in registry.all_sections()]
    return _resolve_explicit_palette_path(target, config)


def _manage_empty_message(target: str, kind: str) -> str:
    """kind: "mapping" or "palette"."""
    if target is None:
        return f"No active {kind}."
    if target == "all":
        return f"No {kind} saved yet."
    return f"No palette found matching: {target}"


def _confirm_manage_delete(target: str, count: int, kind: str, idc: bool) -> bool:
    """Shared confirm-unless---idc prompt for `manage mappings/palette
    delete`. kind: "mapping" or "palette". target == "all" additionally
    gets the loud ATTENTION banner -- an irreversible bulk wipe deserves
    more than an easy-to-miss "(y/N)"."""
    if idc:
        return True
    plural = "mapping(s)" if kind == "mapping" else "palette(s)"
    if target == "all":
        wipeout = (f"ALL saved mappings ({count})" if kind == "mapping"
                   else f"ALL saved palettes ({count})")
        _print_warning_banner(
            "ATTENTION! BULK DELETE",
            f"This is going to delete {wipeout}. This action cannot be undone.",
        )
    try:
        answer = input(f"Delete {count} {plural}? (y/N): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        answer = ""
    return answer in ("y", "yes", "s", "si", "sí")


def cmd_manage_mappings_show(args, config):
    registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
    palettes = _resolve_manage_mapping_targets(args.target, config, registry)
    if not palettes:
        print(_manage_empty_message(args.target, "mapping"))
        return
    active = registry.active_palette_path()
    shown = False
    for palette_path in palettes:
        store = registry.peek_section(palette_path)
        if store is None:
            continue
        shown = True
        marker = "  (active)" if palette_path == active else ""
        print(f"\n=== {palette_path}{marker} ===")
        _print_mapping_details(store, config)
    if not shown:
        print(_manage_empty_message(args.target, "mapping"))


def cmd_manage_mappings_delete(args, config):
    registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
    palettes = _resolve_manage_mapping_targets(args.target, config, registry)
    to_delete = [p for p in palettes if registry.peek_section(p) is not None]
    if not to_delete:
        print(_manage_empty_message(args.target, "mapping"))
        return
    if not _confirm_manage_delete(args.target, len(to_delete), "mapping", args.idc):
        print("Cancelled.")
        return
    if args.target == "all":
        registry.remove_all_sections()
    else:
        for p in to_delete:
            registry.remove_section(p)
    print(f"{len(to_delete)} mapping(s) deleted.")


def cmd_manage_palette_show(args, config):
    registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
    palettes = _resolve_manage_palette_targets(args.target, config, registry)
    if not palettes:
        print(_manage_empty_message(args.target, "palette"))
        return
    for palette_path in palettes:
        entries = palette_store.read_palette_csv(palette_path)
        print(f"\n=== {palette_path} ({len(entries)} color(s)) ===")
        _print_palette(entries)


def cmd_manage_palette_delete(args, config):
    registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
    palettes = _resolve_manage_palette_targets(args.target, config, registry)
    to_delete = [p for p in palettes if os.path.isfile(color_detector.expand_path(p))]
    if not to_delete:
        print(_manage_empty_message(args.target, "palette"))
        return
    if not _confirm_manage_delete(args.target, len(to_delete), "palette", args.idc):
        print("Cancelled.")
        return
    for p in to_delete:
        palette_store.delete_palette(p)
    print(f"{len(to_delete)} palette(s) deleted.")


def _print_warning_banner(title: str, message: str) -> None:
    """An unmissable, visually distinct banner -- deliberately louder than an
    ordinary "⚠ ..." line, for warnings a user could otherwise skim past
    (a mapping reassignment, or an irreversible bulk delete)."""
    bar = "=" * 70
    print(f"\n{bar}\n{title}\n{bar}")
    print(message)
    print(f"{bar}\n")


def _print_reorder_banner(warning: str) -> None:
    """tier="compacted" applies: the mapping's assignment order changed and
    may break a previously-tuned theme (see resolve_apply_targets)."""
    _print_warning_banner("ATTENTION! MAPPING REASSIGNMENT", warning)


def _apply_or_test(args, config, mode):
    if args.mapping:
        store = mapping_store.MappingStore(args.mapping, project_dir=config.project_dir).load()
    else:
        registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
        store = registry.for_active()
    new_p, entries = store.new_palette, store.entries
    if not entries:
        print(f"Empty or not found mapping: {args.mapping or '(no active mapping)'}")
        sys.exit(1)

    detected_path = store.old_palette or config.detected_palette_csv
    detected_colors = color_detector.read_detected_csv(detected_path)
    new_palette = palette_store.read_palette_csv(new_p) if new_p else []

    # Resolve entries against the palette via the one shared resolver every
    # apply path in this app uses (see mapping_store.resolve_apply_targets).
    # tier "blocked": not even enough colors for the mapping's distinct new_id
    # values -- a hard error, same as before. tier "compacted": the palette is
    # smaller than the mapping's highest new_id but big enough for its
    # distinct values -- unlike the GUI, a CLI apply/automatic run must not
    # block on this (a wallpaper-switch hook can't stop to ask), so it warns
    # loudly and proceeds with the resolved (possibly reordered) entries.
    resolution = mapping_store.resolve_apply_targets(entries, new_palette)
    if resolution["tier"] == "blocked":
        print(f"⚠ Palette {new_p or '(not set)'} has {resolution['available']} color(s), "
              f"but the mapping needs at least {resolution['needed']} (number of distinct ids "
              "used). Check the mapping ('ucs mapping show <mapping>') or "
              "regenerate/reimport the palette.")
        sys.exit(1)
    if resolution["tier"] == "compacted":
        _print_reorder_banner(resolution["warning"])
    entries = resolution["final_entries"]

    siblings = conflicts.find_case2_siblings(detected_colors)
    collisions = conflicts.find_case1_collisions(detected_colors, new_palette, entries)
    convergence = conflicts.find_target_convergence(detected_colors, new_palette, entries, sibling_groups=siblings)
    if (collisions or convergence) and not args.force:
        print("⚠ Conflicts detected. Use --force to continue anyway:")
        for c in collisions:
            print(f"  [case 1] old_id {c['old_id']} -> #{c['new_hex']} collides with id(s) {c['conflict_with_ids']}")
        for c in convergence:
            print(f"  [convergence] ids {c['old_ids']} -> #{c['target_hex']} (loses the distinction between them)")
        sys.exit(1)

    dry_run = mode == "test"
    results = color_replacer.apply_mapping(
        detected_colors, new_palette, entries, config.backup_dir, dry_run=dry_run
    )
    total = sum(r.get("count", 0) for r in results)
    errors = [r for r in results if "error" in r]
    label = "Simulation (test)" if dry_run else "Applied"
    print(f"{label}: {total} replacement(s) across {len(results)} file operation(s).")
    for r in results:
        if r.get("count", 0) > 0 or "error" in r:
            status = r.get("error", f"x{r['count']}")
            print(f"  {r['file']}: {r['old_color']} -> {r['new_color']} [{r['type']}] {status}")
    if errors:
        print(f"Errors: {len(errors)}")
    if (collisions or convergence) and args.force and not dry_run:
        print("⚠ A conflict/convergence was forced: the mapping used is probably out of date now "
              "(some ids no longer represent the same real color). It's worth redoing it before reusing it.")
    if not dry_run:
        roles_path = os.path.join(os.path.dirname(detected_path), "color_roles.json")
        role_collisions, pair_collisions = color_detector.rekey_roles_after_apply(
            roles_path, detected_colors, new_palette, entries
        )
        for new_key, old_keys in role_collisions:
            print(f"⚠ {new_key} was left without a role: colors {old_keys} had different roles "
                  "and converged into it. Reassign the role by hand if needed.")
        for fg_key, dangling_bg_key in pair_collisions:
            print(f"⚠ {fg_key} lost its link to {dangling_bg_key}: that background no longer exists "
                  "as such after the apply. Relink it by hand if needed.")
        # The colors just replaced no longer exist in the files BY DESIGN --
        # re-stamp this mapping's identity to the NEW colors now actually
        # there, so the drift refresh below doesn't mistake "I just replaced
        # this" for real drift and flag it orphaned.
        store.entries = mapping_store.stamp_applied_entries(store.entries, entries, new_palette, detected_colors)
        store.save()
        print(f"Backup at: {config.backup_dir}")
        print("To undo: ucs restore")
        _refresh_detected_after_change(config)
        restart_actions.write_wallpaper_state(config, new_p)
        started = restart_actions.run_enabled(
            restart_actions.read_restart_actions(config), extra_env=restart_actions.wallpaper_env(new_p), cli=True
        )
        for a in started:
            print(f"  Running: {a['label']}" + ("" if a["started"] else f" (error: {a['error']})"))


def _refresh_detected_after_change(config):
    """Re-scan and persist detected_palette.csv after files on disk changed
    (a real apply/automatic or a restore) — otherwise the old mapping keeps
    pointing at colors that no longer exist in the files."""
    colors = detect_diff.run_detect(config)
    color_detector.write_detected_csv(colors, config.detected_palette_csv)
    print(f"Colors re-detected and saved to: {config.detected_palette_csv}")
    _report_mapping_drift(config, colors, persist=True)


def cmd_test(args, config):
    _apply_or_test(args, config, "test")


def cmd_apply(args, config):
    _apply_or_test(args, config, "apply")


def cmd_gui(args, config):
    from .gui.app import main as gui_main
    sys.exit(gui_main())


def cmd_restore(args, config):
    if not os.path.isdir(config.backup_dir) or not os.listdir(config.backup_dir):
        print(f"⚠ No backup was found at {config.backup_dir}. "
              "You need to have done at least one real 'ucs apply' before you can restore.")
        sys.exit(1)

    if not args.yolo:
        _print_warning_banner(
            "ATTENTION! RESTORE FROM BACKUP",
            "This overwrites your COMPLETE files with the backup copy -- it's not just "
            "undoing the color replacement. Any change or new code you added to those "
            "files AFTER the last apply is lost. Use --yolo to skip this "
            "confirmation.",
        )
        try:
            answer = input("Restore anyway? (y/N): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            answer = ""
        if answer not in ("y", "yes", "s", "si", "sí"):
            print("Cancelled.")
            return

    results = color_replacer.restore_files(config.files_to_replace, config.backup_dir)
    for r in results:
        status = "restored" if r["restored"] else "no backup"
        print(f"  {r['file']}: {status}")

    _refresh_detected_after_change(config)

    registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
    active_palette = registry.for_active().new_palette
    restart_actions.write_wallpaper_state(config, active_palette)

    if args.postcommands is not None:
        do_postcommands = args.postcommands
    else:
        try:
            answer = input("Run the configured post-apply scripts? (y/N): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        do_postcommands = answer in ("y", "yes", "s", "si", "sí")

    if do_postcommands:
        started = restart_actions.run_enabled(
            restart_actions.read_restart_actions(config),
            extra_env=restart_actions.wallpaper_env(active_palette),
            cli=True,
        )
        for a in started:
            print(f"  Running: {a['label']}" + ("" if a["started"] else f" (error: {a['error']})"))


def cmd_automatic(args, config):
    if bool(args.palette) == bool(args.from_image):
        print("Pass exactly one: the palette (positional) or --from-image <wallpaper>.")
        sys.exit(1)

    registry = None
    if args.mapping:
        store = mapping_store.MappingStore(args.mapping, project_dir=config.project_dir).load()
        mapping_desc = args.mapping
    else:
        registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
        store = registry.for_active()
        mapping_desc = "the active mapping"

    palette_source = args.palette
    target_path = None
    if args.from_image:
        if not store.entries:
            print(f"Empty or not found mapping: {mapping_desc}")
            sys.exit(1)
        fallback_colors = len({e["new_id"] for e in store.entries})
        out_path, n_colors, reused_path = _resolve_generate_target(
            config, args.from_image, None, args.regenerate, args.colors, fallback_colors,
        )
        if reused_path:
            palette_source = palette_store.read_palette_csv(reused_path)
            target_path = reused_path
            print(f"A palette already exists for this image: {reused_path} "
                  f"({len(palette_source)} color(s)) -- reusing it. Pass --regenerate to force "
                  "a fresh generation.")
        else:
            palette_source, saved_path, gen_warnings = palette_shift.generate_and_save_palette(
                config, args.from_image, n_colors, args.sample_size, args.mode, args.my_eyes, out_path,
                scoring=args.scoring, custom_scoring_values=args.custom_scoring_values,
                weighted_contrast=args.weighted_contrast, shuffle=args.shuffle, overfetch=args.overfetch,
                ying_yang=args.ying_yang, my_eyes_factor=args.my_eyes_factor, my_eyes_max_chroma=args.my_eyes_max_chroma,
                shading_direction=args.shading_direction,
                shading_min_luminance=args.shading_min_luminance, shading_max_luminance=args.shading_max_luminance,
                keep_custom=args.keep_custom, eco=args.eco, hallucinate=args.hallucinate,
                mapping_path=args.mapping,
            )
            target_path = saved_path
            for w in gen_warnings:
                print(f"⚠ {w}")
            print(f"Palette generated from {args.from_image} ({n_colors} color(s)) — saved to: {saved_path}")
        _print_palette(palette_source)
    elif isinstance(args.palette, str) and args.palette != "-" and args.palette.lower().endswith(".csv"):
        target_path = _resolve_path(args.palette, config.palettes_created_dir, config.project_dir)

    # Bind (creating/seeding if needed -- see MappingRegistry.for_palette) the
    # mapping to the palette actually being applied against, so what's used
    # here is persisted/discoverable under ITS OWN section afterward -- e.g.
    # opening this exact palette later in the GUI ("Importar paleta") must
    # show what was actually applied, not whatever happened to be active
    # before this command ran (the real bug report this fixes: `automatic
    # --from-image` kept using/saving into the PREVIOUS active section,
    # leaving the just-generated/-reused palette's own section empty).
    if registry is not None and target_path:
        store = registry.for_palette(target_path, old_palette=store.old_palette or config.detected_palette_csv)
        mapping_desc = target_path

    result = guiless.apply_palette(
        palette_source,
        store,
        config.backup_dir,
        dry_run=args.test,
        force=args.force,
        yolo=args.yolo,
        project_dir=config.project_dir,
    )
    _report_apply_result(result, args, config, mapping_desc, palette_path=store.new_palette)


def _report_apply_result(result, args, config, mapping_desc, palette_path=None):
    """Shared by `automatic` and `automatic shift`: turn a guiless.apply_palette
    result into CLI output (and the post-apply detect refresh / restarts on a
    real apply). Exits 1 on any non-applied status. mapping_desc is a plain
    display string (a path, or "the active mapping") -- only used in messages.
    palette_path, if given, is the applied palette's own path, used to look
    up $UCS_WALLPAPER for the restart actions (see restart_actions.wallpaper_env)."""
    status = result["status"]
    if status == "insufficient_palette":
        print(f"The new palette has {result['available']} color(s), but the mapping needs "
              f"{result['needed']} (distinct roles used). Generate a palette with at least "
              f"{result['needed']} colors (--colors {result['needed']}).")
        sys.exit(1)
    elif status == "needs_confirmation":
        print(f"The new palette has {result['surplus_palette_count']} extra color(s) that won't be used.")
        print("Run again with --yolo to apply anyway.")
        sys.exit(1)
    elif status == "conflicts":
        print("⚠ Conflicts detected. Use --force to continue anyway:")
        for c in result["conflicts"]:
            print(f"  [case 1] old_id {c['old_id']} -> #{c['new_hex']} collides with id(s) {c['conflict_with_ids']}")
        for c in result.get("convergence", []):
            print(f"  [convergence] ids {c['old_ids']} -> #{c['target_hex']} (loses the distinction between them)")
        sys.exit(1)
    elif status == "empty_mapping":
        print(f"Empty or not found mapping: {mapping_desc}")
        sys.exit(1)
    else:
        results = result["results"]
        total = sum(r.get("count", 0) for r in results)
        label = "Simulation (test)" if args.test else "Applied"
        print(f"{label}: {total} replacement(s) across {len(results)} file operation(s).")
        if result.get("stale_mapping_warning"):
            print("⚠ A conflict/convergence was forced: the mapping used is probably out of date now "
                  "(some ids no longer represent the same real color). It's worth redoing it before reusing it.")
        for new_key, old_keys in result.get("role_collisions", []):
            print(f"⚠ {new_key} was left without a role: colors {old_keys} had different roles "
                  "and converged into it. Reassign the role by hand if needed.")
        if not args.test:
            print(f"Backup at: {config.backup_dir}")
            _refresh_detected_after_change(config)
            restart_actions.write_wallpaper_state(config, palette_path)
            started = restart_actions.run_enabled(
                restart_actions.read_restart_actions(config),
                extra_env=restart_actions.wallpaper_env(palette_path),
                cli=True,
            )
            for a in started:
                print(f"  Running: {a['label']}" + ("" if a["started"] else f" (error: {a['error']})"))


def _maybe_apply_after_edit(args, config, palette_source, mapping_path=None, target_palette=None):
    """Shared tail for every palette-mutating command's `--apply`: reuses the
    exact same guiless.apply_palette + _report_apply_result pipeline
    `automatic`/`automatic shift` already use, so "mutate a palette, then
    apply it" never needs its own bespoke apply logic. No-op if --apply
    wasn't passed. `palette_source` is a path OR an already-loaded list
    (guiless.load_palette accepts either) -- `palette generate --apply`
    passes the just-computed in-memory entries directly, no file round-trip.

    target_palette (a real palette CSV path, when the caller has one -- None
    for an anonymous/in-memory palette_source with no stable identity, e.g.
    `palette show -` from stdin): without an explicit --mapping, binds
    (creating/seeding if needed -- see MappingRegistry.for_palette) to THAT
    palette's own registry section instead of blindly whatever was active
    before this command ran, so what actually gets applied here is
    persisted/discoverable under its own palette afterward -- the same class
    of bug fixed in `cmd_automatic` (a mapping edited/applied against palette
    X must live under X's own section, not some unrelated previously-active
    one)."""
    if not getattr(args, "apply", False):
        return
    mapping_path = mapping_path or args.mapping
    if mapping_path:
        store = mapping_store.MappingStore(mapping_path, project_dir=config.project_dir).load()
        mapping_desc = mapping_path
    else:
        registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
        if target_palette:
            store = registry.for_palette(target_palette, old_palette=config.detected_palette_csv)
            mapping_desc = target_palette
        else:
            store = registry.for_active()
            mapping_desc = "the active mapping"
    result = guiless.apply_palette(
        palette_source, store, config.backup_dir,
        dry_run=args.test, force=args.force, yolo=args.yolo, project_dir=config.project_dir,
    )
    _report_apply_result(result, args, config, mapping_desc, palette_path=store.new_palette)


def cmd_palette_shift(args, config):
    """`palette shift` (--apply defaults False) and the back-compat `automatic
    shift` (which forces apply=True via set_defaults, no --apply flag shown)
    both point here -- one function, no duplicated shift-then-apply logic."""
    palette_path = _resolve_target_palette(args.palette, config, mapping_path=args.mapping)

    weighted_contrast = None if args.weighted_contrast is None else (args.weighted_contrast == "on")
    result = palette_shift.shift_palette(
        palette_path, config,
        my_eyes=args.my_eyes, ying_yang=args.ying_yang,
        my_eyes_factor=args.my_eyes_factor, my_eyes_max_chroma=args.my_eyes_max_chroma,
        mode=args.mode, scoring=args.scoring, custom_scoring_values=args.custom_scoring_values,
        weighted_contrast=weighted_contrast, shuffle=args.shuffle, overfetch=args.overfetch,
        colors=args.colors, shading_direction=args.shading_direction,
        shading_min_luminance=args.shading_min_luminance, shading_max_luminance=args.shading_max_luminance,
        keep_custom=args.keep_custom, eco=args.eco, hallucinate=args.hallucinate,
        mapping_path=args.mapping, write=not args.test,
    )
    for w in result["warnings"]:
        print(f"⚠ {w}")
    print(f"Palette {'regenerated' if result['regenerated'] else 'adjusted'}"
          f"{' (simulation, not saved)' if args.test else ''}: {palette_path}")
    _print_palette(result["entries"])

    palette_source = [{"hex": e["hex"], "label": e.get("label", "")} for e in result["entries"]]
    _maybe_apply_after_edit(args, config, palette_source, mapping_path=args.mapping, target_palette=palette_path)


def _parse_custom_scoring_values(raw: str) -> dict:
    """--custom-scoring-values coverage=20,saturation=40,midtone=30,contrast=10
    -- argparse type= callable, so failures must raise ArgumentTypeError to
    get a clean CLI error instead of a traceback."""
    percentages = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise argparse.ArgumentTypeError(f"invalid format: {part!r} (expected key=value)")
        key, _, value = part.partition("=")
        key = key.strip()
        try:
            percentages[key] = float(value.strip())
        except ValueError:
            raise argparse.ArgumentTypeError(f"non-numeric value for {key!r}: {value.strip()!r}")

    try:
        palette_generator.percentages_to_weights(dict(percentages))
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))
    return percentages


def _parse_shuffle(raw: str):
    """--shuffle N or --shuffle next -- argparse type= callable, so failures
    must raise ArgumentTypeError to get a clean CLI error."""
    if raw == "next":
        return "next"
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError("--shuffle must be an integer >= 0 or 'next'")
    if value < 0:
        raise argparse.ArgumentTypeError("--shuffle must be an integer >= 0 or 'next'")
    return value


def _parse_overfetch(raw: str):
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError("--overfetch must be an integer >= 0")
    if value < 0:
        raise argparse.ArgumentTypeError("--overfetch must be an integer >= 0")
    return value


def _add_shuffle_args(parser, from_image_note=""):
    parser.add_argument(
        "--shuffle", type=_parse_shuffle, default=None,
        help=f"Skip the first N primary candidates{from_image_note} (everything else is chosen "
             "relative to that new primary). Also accepts 'next': resumes from the last value "
             "used + 1, cyclic based on the pool size (--colors + --overfetch) -- meant for "
             "scripts that call this repeatedly to try out variants.",
    )
    parser.add_argument(
        "--overfetch", type=_parse_overfetch, default=0,
        help=f"Extra candidates to consider beyond --colors{from_image_note} (default: 0). "
             "auxN: chosen among n_needed+overfetch candidates, the best-scoring ones survive. "
             "shading: the ramp is generated as if n_needed+overfetch shades were needed and the "
             "ones closest to primary (denser) are kept. Also gives --shuffle more "
             "room to skip without running out of candidates.",
    )


def _add_scoring_args(parser, from_image_note=""):
    parser.add_argument(
        "--scoring", choices=["default", "alternative", "custom"], default="default",
        help=f"How to weigh coverage/saturation/midtone/contrast when choosing colors{from_image_note} "
             "(default: default). 'custom' uses --custom-scoring-values, or if not passed, the "
             "values saved in config.json (and if there are none either, falls back to 'default').",
    )
    parser.add_argument(
        "--custom-scoring-values", type=_parse_custom_scoring_values,
        help="Only with --scoring custom: 'coverage=20,saturation=40,midtone=30,contrast=10' "
             "(must add up to 100). Takes priority over what's saved in config.json.",
    )


def _add_apply_args(parser, apply_help=None):
    """Shared by every palette-mutating `palette` subcommand: after the
    mutation, --apply re-applies the (canonical, or --mapping) mapping
    against the result -- reusing the exact same guiless.apply_palette
    pipeline `automatic` already uses (see _maybe_apply_after_edit). Without
    --apply, these flags are simply unused."""
    parser.add_argument(
        "--apply", action="store_true",
        help=apply_help or "Also apply the mapping against the resulting palette (like 'automatic apply').",
    )
    parser.add_argument("--mapping", help="default: mappings/mapping.csv, the canonical mapping")
    parser.add_argument("--test", action="store_true", help="With --apply: simulate, don't modify files")
    parser.add_argument("--force", action="store_true",
                         help="With --apply: apply even if there are case 1/convergence conflicts")
    parser.add_argument("--yolo", action="store_true",
                         help="With --apply: apply even if the palette has leftover colors")


def _add_shift_args(parser):
    """The modifier flags for a shift (my-eyes/ying-yang always valid; the
    rest only regenerate a GENERATED palette -- see palette_shift.shift_palette).
    Shared verbatim by `palette shift` and the back-compat `automatic shift`,
    so the two entry points can never drift apart."""
    onofftoggle = ["on", "off", "toggle"]
    parser.add_argument("--my-eyes", choices=onofftoggle, default=None, metavar="on|off|toggle",
                         help="Extra saturation. 'on'/'off' sets the value, 'toggle' flips it. "
                              "Applied without regenerating, even on created palettes (default: keep).")
    parser.add_argument("--my-eyes-factor", type=float, default=None,
                         help="With --my-eyes: CIELAB chroma multiplier (default: keep). "
                              "Applied without regenerating, even on created palettes.")
    parser.add_argument("--my-eyes-max-chroma", type=float, default=None,
                         help="With --my-eyes: cap on the resulting CIELAB chroma (default: keep). "
                              "Applied without regenerating, even on created palettes.")
    parser.add_argument("--ying-yang", choices=onofftoggle, default=None, metavar="on|off|toggle",
                         help="Complementary palette (hues +180°). 'on'/'off'/'toggle' (default: keep). "
                              "Applied without regenerating, even on created palettes.")
    parser.add_argument("--mode", choices=["balanced", "contrast", "shading"], default=None,
                         help="Generated palettes only: regenerate with this mode (default: keep).")
    parser.add_argument("--scoring", choices=["default", "alternative", "custom"], default=None,
                         help="Generated palettes only: regenerate with this weighting (default: keep).")
    parser.add_argument("--custom-scoring-values", type=_parse_custom_scoring_values, default=None,
                         help="With --scoring custom: 'coverage=20,saturation=40,midtone=30,contrast=10' (must add up to 100).")
    parser.add_argument("--weighted-contrast", choices=["on", "off"], default=None, metavar="on|off",
                         help="Generated palettes only: weighted contrast against all clusters "
                              "(default: keep).")
    parser.add_argument("--shuffle", type=_parse_shuffle, default=None,
                         help="Generated palettes only: skip N primary candidates, or 'next' (cyclic). Regenerates.")
    parser.add_argument("--overfetch", type=_parse_overfetch, default=None,
                         help="Generated palettes only: extra candidates beyond --colors. Regenerates.")
    parser.add_argument("--colors", type=int, default=None,
                         help="Generated palettes only: regenerate with this many colors (default: keep).")
    parser.add_argument("--shading-direction", choices=["dark", "light", "toggle"], default=None,
                         help="Generated palettes only, shading mode: 'dark'/'light' sets the ramp's "
                              "direction, 'toggle' flips it (default: keep). Regenerates.")
    parser.add_argument("--shading-min-luminance", type=float, default=None,
                         help="Only with --shading-direction dark: the ramp's minimum luminance "
                              "(default: keep). Regenerates.")
    parser.add_argument("--shading-max-luminance", type=float, default=None,
                         help="Only with --shading-direction light: the ramp's maximum luminance "
                              "(default: keep). Regenerates.")
    parser.add_argument("--keep-custom", choices=onofftoggle, default=None, metavar="on|off|toggle",
                         help="When regenerating (generated palettes), preserve hand-added/edited colors "
                              "in their same spot instead of discarding them (default: keep the "
                              "preference saved on the palette, which starts at 'on'). Doesn't trigger a "
                              "regeneration by itself.")
    parser.add_argument("--eco", choices=onofftoggle, default=None, metavar="on|off|toggle",
                         help="When regenerating (generated palettes), force the same hue on "
                              "foreground/background pairs that contrast by luminance (contrast purely by "
                              "luminance, no hue variety) (default: keep the saved preference, "
                              "which starts at 'off'). Doesn't trigger a regeneration by itself. "
                              "Colors tagged fg/bg with no linked pair are ignored for "
                              "generation (warned about, not blocked).")
    parser.add_argument("--hallucinate", choices=onofftoggle, default=None, metavar="on|off|toggle",
                         help="When regenerating (generated palettes) against a monochrome image, "
                              "synthesize a saturated accent + a shading ramp off it instead "
                              "of a genuinely grey palette (default: keep the saved preference, "
                              "which starts at 'on'). Doesn't trigger a regeneration by itself.")


def _add_my_eyes_generation_args(parser):
    """--my-eyes' chroma-boost knobs, for a FRESH generation (palette
    generate / automatic --from-image) -- fixed defaults matching
    palette_generator._MY_EYES_CHROMA_FACTOR/_MY_EYES_CHROMA_MAX. Shared so
    the two entry points can't drift apart; see
    palette_generator._boost_saturation for what these mean."""
    parser.add_argument("--my-eyes-factor", type=float, default=palette_generator._MY_EYES_CHROMA_FACTOR,
                         help=f"With --my-eyes: CIELAB chroma multiplier "
                              f"(default: {palette_generator._MY_EYES_CHROMA_FACTOR}).")
    parser.add_argument("--my-eyes-max-chroma", type=float, default=palette_generator._MY_EYES_CHROMA_MAX,
                         help=f"With --my-eyes: cap on the resulting CIELAB chroma "
                              f"(default: {palette_generator._MY_EYES_CHROMA_MAX}).")


def _add_shading_generation_args(parser):
    """--mode shading's direction/luminance-bounds knobs, for a FRESH
    generation (palette generate / automatic --from-image) -- fixed
    defaults (dark, 8, 92), no "toggle" (there's no stored prior state to
    invert yet). Shared so the two entry points can't drift apart; see
    palette_generator.generate_shading_series for what these mean."""
    parser.add_argument("--shading-direction", choices=["dark", "light"], default="dark",
                         help="Only with --mode shading: which way to generate the shades. 'dark' "
                              "(default): darkens toward black (a genuine 'shade'). 'light': "
                              "lightens toward white (technically a 'tint').")
    parser.add_argument("--shading-min-luminance", type=float, default=8.0,
                         help="Only with --mode shading --shading-direction dark: the ramp's "
                              "minimum luminance (default: 8).")
    parser.add_argument("--shading-max-luminance", type=float, default=92.0,
                         help="Only with --mode shading --shading-direction light: the ramp's "
                              "maximum luminance (default: 92).")


def _add_keep_custom_generation_arg(parser):
    """Whether a FRESH generation (palette generate / automatic --from-image)
    preserves hand-added/edited colors already at the output path, when it
    already exists as a palette (e.g. a wallpaper-switch hook regenerating
    the same generated.csv every time) -- see
    palette_shift.generate_and_save_palette. Shared so the two entry points
    can't drift apart."""
    parser.add_argument("--keep-custom", choices=["on", "off", "toggle"], default=None, metavar="on|off|toggle",
                         help="If the output path already exists as a palette: preserve its "
                              "hand-added/edited colors instead of discarding them (default: keep the "
                              "saved preference -- from that palette if it already exists, else the "
                              "project's, which starts at 'on').")


def _add_eco_generation_arg(parser):
    """Whether a FRESH generation (palette generate / automatic --from-image)
    forces same-hue fg/bg pairs (contrast purely by luminance) for the
    luminance-contrasting cases -- see
    palette_generator.fgbg_pairing.apply_fgbg_pairing /
    palette_shift.generate_and_save_palette. Shared so the two entry points
    can't drift apart. fg/bg PAIRING ITSELF is always resolved (no flag) --
    it's read from the palette's own current pairing if out_path already
    exists, else from color_roles.json filtered by the mapping (or
    unfiltered if there's no mapping); a color tagged fg/bg with no pair
    vinculada is simply ignored (warned about, not blocked)."""
    parser.add_argument("--eco", choices=["on", "off", "toggle"], default=None,
                         metavar="on|off|toggle",
                         help="Force the same hue on foreground/background pairs that contrast "
                              "by luminance, instead of letting each keep its own hue "
                              "(default: keep the saved preference -- from that palette if it already "
                              "exists, else the project's, which starts at 'off').")


def _add_hallucinate_generation_arg(parser):
    """Whether a FRESH generation (palette generate / automatic --from-image)
    against a monochrome source image synthesizes a saturated accent (+ a
    shading ramp off it) instead of a genuinely greyscale palette -- see
    palette_generator.generate_palette's hallucinate param. Shared so the
    two entry points can't drift apart."""
    parser.add_argument("--hallucinate", choices=["on", "off", "toggle"], default=None,
                         metavar="on|off|toggle",
                         help="Against a monochrome image: synthesize a saturated accent + a shading "
                              "ramp off it ('on', default: keep the saved preference "
                              "-- from the output palette if it already exists, else the project's, "
                              "which starts at 'on') instead of a genuinely grey palette ('off'). No "
                              "effect if the image isn't monochrome.")


def _add_regenerate_arg(parser):
    """Whether a FRESH generation (palette generate / automatic --from-image)
    forces regenerating even if a palette for this exact image already
    exists -- see palette_store.find_palettes_for_image. Without this flag,
    an existing palette for the image is reused as-is instead of being
    silently overwritten (this REPLACES the old "generate always overwrites
    one canonical generated.csv" behavior). When used with no explicit
    --colors, falls back to the existing palette's own color count, or (if
    there's no existing palette at all) the mapping's usual fallback."""
    parser.add_argument("--regenerate", action="store_true",
                         help="Force a regeneration even if a generated palette for this image "
                              "already exists (without --colors, uses the existing palette's own "
                              "color count, or if there's none, whatever the mapping needs).")


def build_parser():
    parser = argparse.ArgumentParser(description="Color Switcher — CLI")
    # not required: no subcommand at all means "launch the GUI" (see main())
    sub = parser.add_subparsers(dest="command", required=False)

    p = sub.add_parser("detect", help="Scan files and update detected_palette.csv")
    p.add_argument("--dry-run", action="store_true", help="Don't overwrite the CSV, just show")
    p.set_defaults(func=cmd_detect)

    cf = sub.add_parser("config", help="Project configuration (config.json)")
    cfsub = cf.add_subparsers(dest="config_command", required=True)

    cfl = cfsub.add_parser("files", help="Files to scan (files_to_replace)")
    cflsub = cfl.add_subparsers(dest="files_command", required=True)

    cfl_list = cflsub.add_parser("list", help="List configured files")
    cfl_list.set_defaults(func=cmd_config_files_list)

    cfl_add = cflsub.add_parser("add", help="Add a file to scan")
    cfl_add.add_argument("path", help="Path to the file (absolute or with ~)")
    cfl_add.set_defaults(func=cmd_config_files_add)

    cfl_remove = cflsub.add_parser("remove", help="Remove a file from the list")
    cfl_remove.add_argument("path", help="As it appears in 'config files list', or its absolute path")
    cfl_remove.set_defaults(func=cmd_config_files_remove)

    cfl_scan = cflsub.add_parser(
        "scan-config", help="Search ~/.config for files with colors and add them to the list")
    cfl_scan.add_argument("--dry-run", action="store_true",
                           help="Just show what it would find, without adding anything")
    cfl_scan.add_argument("--yes", action="store_true", help="Add without asking for confirmation")
    cfl_scan.set_defaults(func=cmd_config_files_scan)

    pp = sub.add_parser("palette", help="Manage created palettes")
    psub = pp.add_subparsers(dest="palette_command", required=True)

    pc = psub.add_parser("create", help="Create a palette CSV")
    pc.add_argument("path", help="Output path (relative to palettes/created/ if not absolute)")
    pc.add_argument("--add", nargs="+", metavar="HEX", action="append",
                     help="Add a color: HEX LABEL, or HEX LABEL ROLE "
                          "(ROLE: foreground|background|none) (can repeat)")
    _add_apply_args(pc)
    pc.set_defaults(func=cmd_palette_create)

    pl = psub.add_parser("list", help="List created palettes")
    pl.set_defaults(func=cmd_palette_list)

    ps = psub.add_parser("show", help="Show a palette and its colors in the terminal")
    ps.add_argument("path", nargs="?", default=None,
                    help="Path to a CSV or JSON, or '-' for JSON via stdin "
                         "(default: whatever the current mapping applies, via #new_palette=)")
    _add_apply_args(ps, apply_help="Also apply the mapping against this palette (replacement for 'automatic apply').")
    ps.set_defaults(func=cmd_palette_show)

    pa = psub.add_parser("add-color", help="Add a color to an existing palette")
    pa.add_argument("path", nargs="?", default=None,
                    help="Palette (default: whatever the current mapping applies, via #new_palette=)")
    pa.add_argument("hex")
    pa.add_argument("--label", default="", help="Label for the color (default: empty)")
    pa.add_argument("--role", choices=["foreground", "background", "none"], default=None,
                    help="Contrast role of the color (default: unmarked)")
    pa.add_argument("--link", default=None, metavar="ID-OR-HEX",
                    help="Link this (just-added) color as the fg/bg pair of another color in the "
                         "same palette, by id or hex (default: not linked).")
    _add_apply_args(pa)
    pa.set_defaults(func=cmd_palette_add_color)

    ped = psub.add_parser("edit", help="Change one color in a palette for another")
    ped.add_argument("palette", nargs="?", default=None,
                     help="Palette to edit (default: whatever the current mapping applies, via #new_palette=)")
    ped.add_argument("target", help="Color to change: its id or its hex")
    ped.add_argument("new_hex", metavar="new-hex", help="New color (hex)")
    ped.add_argument("--role", choices=["foreground", "background", "none"], default=None,
                     help="Also set the contrast role (default: leave it untouched)")
    ped.add_argument("--link", default=None, metavar="ID-OR-HEX",
                     help="Also link this color as the fg/bg pair of another color in the same "
                          "palette, by id or hex ('none' to unlink it; default: leave it untouched).")
    _add_apply_args(ped)
    ped.set_defaults(func=cmd_palette_edit)

    prm = psub.add_parser("remove", help="Delete a color from a palette (adjusts the mapping if needed)")
    prm.add_argument("palette", nargs="?", default=None,
                     help="Palette (default: whatever the current mapping applies, via #new_palette=)")
    prm.add_argument("target", help="Color to delete: its id or its hex")
    _add_apply_args(prm)
    prm.set_defaults(func=cmd_palette_remove)

    pg = psub.add_parser("generate", help="Generate a palette from an image (wallpaper)")
    pg.add_argument("image", help="Path to the image")
    pg.add_argument("--colors", type=int, default=None,
                     help="Number of colors to generate (default: the number of distinct roles "
                          "the mapping uses -- see --mapping --, or 6 if there's no mapping)")
    pg.add_argument("--sample-size", type=int, default=40000, help="Pixels to sample (default: 40000)")
    pg.add_argument("--mode", choices=["balanced", "contrast", "shading"], default="contrast",
                     help="How to choose secondary/auxN (default: contrast). 'balanced': secondary by score, "
                          "with no bias regarding contrast with primary. 'contrast': secondary maximizes contrast "
                          "with primary (original behavior). 'shading': the rest of the palette are "
                          "monochromatic variants (same hue) of primary")
    pg.add_argument("--my-eyes", action="store_true",
                     help="Saturate the chosen colors right before saving them")
    _add_my_eyes_generation_args(pg)
    pg.add_argument("--ying-yang", type=_parse_on_off, default=False, metavar="on|off",
                     help="Ying Yang: use the complementary palette (every color rotated 180° in "
                          "hue). 'on' or 'off' (default: off)")
    _add_scoring_args(pg)
    pg.add_argument(
        "--no-weighted-contrast", dest="weighted_contrast", action="store_false", default=True,
        help="Compare contrast only against the image's most dominant cluster, instead of the "
             "weighted system against all clusters (default: weighted, recommended; works better for "
             "images with a single-color light background).",
    )
    _add_shuffle_args(pg)
    _add_shading_generation_args(pg)
    _add_keep_custom_generation_arg(pg)
    _add_eco_generation_arg(pg)
    _add_hallucinate_generation_arg(pg)
    _add_regenerate_arg(pg)
    pg.add_argument("--out", help="Explicit output path (default: a persistent per-image file, "
                                  "under palettes/created/ -- see find_palettes_for_image/--regenerate. "
                                  "If passed, always (re)generates there, without looking for an existing palette.")
    _add_apply_args(pg)
    pg.set_defaults(func=cmd_palette_generate)

    psh = psub.add_parser("shift", help="Change a palette's modifiers and, optionally, reapply")
    psh.add_argument("palette", nargs="?", default=None,
                     help="Palette to shift (default: whatever the current mapping applies, via #new_palette=)")
    _add_shift_args(psh)
    _add_apply_args(psh, apply_help="Also apply the mapping against the shifted palette (default: don't apply).")
    psh.set_defaults(func=cmd_palette_shift)

    mp = sub.add_parser("mapping", help="Manage mappings")
    msub = mp.add_subparsers(dest="mapping_command", required=True)

    mn = msub.add_parser("new", help="Create an interactive mapping")
    mn.add_argument("target_palette", help="Path to the target palette")
    mn.add_argument("--detected-palette", help="Use a specific detected_palette.csv")
    mn.add_argument("--out", help="Explicit standalone output path (default: this palette's own "
                                  "section in the mapping registry, mappings/mappings.json -- see "
                                  "'mapping list'). If passed, creates a separate file, never "
                                  "auto-discovered afterward.")
    mn.set_defaults(func=cmd_mapping_new)

    ms = msub.add_parser("show", help="Show a mapping and its conflicts")
    ms.add_argument("path", nargs="?", default=None,
                    help="Standalone (legacy) mapping file. Omit to use --palette or the "
                         "active mapping.")
    ms.add_argument("--palette", help="Show this specific palette's mapping (its own "
                                      "section in the registry), without changing which one's active.")
    ms.set_defaults(func=cmd_mapping_show)

    ml = msub.add_parser("list", help="List every palette that has a mapping, and which one is active")
    ml.set_defaults(func=cmd_mapping_list)

    mr = msub.add_parser(
        "relink",
        help="Relink entries whose detected color changed id on the last scan "
             "(see 'ucs detect'/'ucs apply' -- they warn when there's something to relink)",
    )
    mr.add_argument("--mapping", help="Standalone (legacy) mapping file. default: --palette, or "
                                      "if that's not passed either, the active mapping.")
    mr.add_argument("--palette", help="Relink this specific palette's mapping, without changing "
                                      "which one's active.")
    mr.add_argument("--yes", action="store_true", help="Relink without asking for confirmation")
    mr.set_defaults(func=cmd_mapping_relink)

    t = sub.add_parser("test", help="Simulate applying a mapping (doesn't modify files)")
    t.add_argument("--mapping", help="default: mappings/mapping.csv, the canonical mapping")
    t.add_argument("--force", action="store_true", help="Ignore case 1 conflicts")
    t.set_defaults(func=cmd_test)

    a = sub.add_parser("apply", help="Apply a mapping (makes a real backup)")
    a.add_argument("--mapping", help="default: mappings/mapping.csv, the canonical mapping")
    a.add_argument("--force", action="store_true", help="Ignore case 1 conflicts")
    a.set_defaults(func=cmd_apply)

    r = sub.add_parser("restore", help="Restore files from the backup")
    r_postcommands = r.add_mutually_exclusive_group()
    r_postcommands.add_argument("--postcommands", dest="postcommands", action="store_true", default=None,
                                 help="Run the configured post-apply scripts without asking")
    r_postcommands.add_argument("--no-postcommands", dest="postcommands", action="store_false",
                                 help="Don't run post-apply scripts or ask (skips the confirmation)")
    r.add_argument("--yolo", action="store_true",
                    help="Don't ask for confirmation before restoring (overwrites complete files, "
                         "not just the colors)")
    r.set_defaults(func=cmd_restore)

    g = sub.add_parser("gui", help="Launch the graphical interface (GTK4 + libadwaita)")
    g.set_defaults(func=cmd_gui)

    au = sub.add_parser("automatic", help="GUIless mode: apply a palette using an existing mapping")
    au_sub = au.add_subparsers(dest="auto_cmd")

    # `automatic apply` — the original behavior. Bare `automatic <palette>` /
    # `automatic --from-image ...` still work: main() injects "apply" when the
    # first token after `automatic` isn't a known subcommand (see _inject_automatic_apply).
    ap = au_sub.add_parser("apply", help="Apply a palette (or generate it from an image) against the mapping")
    ap.add_argument("palette", nargs="?", default=None,
                    help="Path to a CSV (id,#hex,label) or JSON [{hex,label}, ...], or '-' for JSON via stdin. "
                         "Omit if using --from-image")
    ap.add_argument("--from-image", help="Generate the palette from this image (wallpaper) instead of passing a path")
    ap.add_argument("--colors", type=int,
                    help="Number of colors to generate with --from-image "
                         "(default: the number of distinct roles the mapping uses)")
    ap.add_argument("--sample-size", type=int, default=40000, help="Pixels to sample with --from-image (default: 40000)")
    ap.add_argument("--mode", choices=["balanced", "contrast", "shading"], default="contrast",
                    help="How to choose secondary/auxN with --from-image (default: contrast, see 'palette generate --help')")
    ap.add_argument("--my-eyes", action="store_true",
                    help="Saturate the colors generated with --from-image right before applying them")
    _add_my_eyes_generation_args(ap)
    ap.add_argument("--ying-yang", type=_parse_on_off, default=False, metavar="on|off",
                    help="Ying Yang: use the complementary palette with --from-image (hues rotated 180°). "
                         "'on' or 'off' (default: off)")
    _add_scoring_args(ap, from_image_note=" with --from-image")
    ap.add_argument(
        "--no-weighted-contrast", dest="weighted_contrast", action="store_false", default=True,
        help="Compare contrast only against the image's most dominant cluster with --from-image, "
             "instead of the weighted system against all clusters (default: weighted, recommended).",
    )
    _add_shuffle_args(ap, from_image_note=" with --from-image")
    _add_shading_generation_args(ap)
    _add_keep_custom_generation_arg(ap)
    _add_eco_generation_arg(ap)
    _add_hallucinate_generation_arg(ap)
    _add_regenerate_arg(ap)
    ap.add_argument("--mapping", help="default: mappings/mapping.csv, the canonical mapping")
    ap.add_argument("--test", action="store_true", help="Simulate, don't modify files")
    ap.add_argument("--force", action="store_true",
                    help="Apply even if there are case 1/convergence conflicts "
                         "(doesn't skip the insufficient-palette or leftover-colors blocks)")
    ap.add_argument("--yolo", action="store_true",
                    help="Apply even if the palette has leftover colors (the extras go unused). "
                         "Separate from --force, which is only for conflicts.")
    ap.set_defaults(func=cmd_automatic)

    # `automatic shift` — re-tweak the currently-applied palette's modifiers and
    # re-apply, without re-passing the image/flags. Reads provenance from the
    # palette's #ucs-meta (see palette_shift). Kept only for backward
    # compatibility (e.g. existing wallpaper-switch hooks) -- `palette shift`
    # is the preferred entry point now, with --apply opt-in instead of implied.
    # Same cmd_palette_shift, same _add_shift_args -- the two can't drift
    # apart -- just with apply forced on via set_defaults (no --apply flag
    # shown here, since automatic's whole point was always "shift AND apply").
    sh = au_sub.add_parser("shift", help="Change the current palette's modifiers and reapply (without repeating the image)")
    sh.add_argument("palette", nargs="?", default=None,
                    help="Palette to shift (default: whatever the current mapping applies, via #new_palette=)")
    _add_shift_args(sh)
    sh.add_argument("--mapping", help="default: mappings/mapping.csv, the canonical mapping")
    sh.add_argument("--test", action="store_true", help="Simulate: doesn't rewrite the palette or touch files")
    sh.add_argument("--force", action="store_true", help="Apply even if there are case 1/convergence conflicts")
    sh.add_argument("--yolo", action="store_true", help="Apply even if the palette has leftover colors")
    sh.set_defaults(func=cmd_palette_shift, apply=True)

    mg = sub.add_parser("manage", help="View/delete saved mappings and palettes")
    mg_sub = mg.add_subparsers(dest="manage_command", required=True)

    _target_help = (
        "Omit for the active one. 'all' for every one. Or a path to a wallpaper or a "
        "palette (.csv) for whichever is associated with that path."
    )

    mgm = mg_sub.add_parser("mappings", help="View or delete saved mappings")
    mgm_sub = mgm.add_subparsers(dest="manage_mappings_command", required=True)

    mgms = mgm_sub.add_parser("show", help="Show saved mapping(s)")
    mgms.add_argument("target", nargs="?", default=None, help=_target_help)
    mgms.set_defaults(func=cmd_manage_mappings_show)

    mgmd = mgm_sub.add_parser("delete", help="Delete saved mapping(s)")
    mgmd.add_argument("target", nargs="?", default=None, help=_target_help)
    mgmd.add_argument("--idc", action="store_true", help="Don't ask for confirmation (\"I don't care\")")
    mgmd.set_defaults(func=cmd_manage_mappings_delete)

    mgp = mg_sub.add_parser("palette", help="View or delete saved palettes")
    mgp_sub = mgp.add_subparsers(dest="manage_palette_command", required=True)

    mgps = mgp_sub.add_parser("show", help="Show saved palette(s)")
    mgps.add_argument("target", nargs="?", default=None, help=_target_help)
    mgps.set_defaults(func=cmd_manage_palette_show)

    mgpd = mgp_sub.add_parser("delete", help="Delete saved palette(s)")
    mgpd.add_argument("target", nargs="?", default=None, help=_target_help)
    mgpd.add_argument("--idc", action="store_true", help="Don't ask for confirmation (\"I don't care\")")
    mgpd.set_defaults(func=cmd_manage_palette_delete)

    return parser


def _inject_automatic_apply(argv):
    """Keep `automatic <palette>` / `automatic --from-image ...` working now that
    `automatic` has subcommands: if the token after `automatic` isn't a known
    subcommand, insert the implicit "apply". Leaves `automatic shift ...` and an
    explicit `automatic apply ...` untouched."""
    try:
        i = argv.index("automatic")
    except ValueError:
        return argv
    rest = argv[i + 1:]
    # Let `automatic --help`/`-h` reach the automatic-level help (which lists the
    # apply/shift subcommands) instead of jumping into `apply`.
    if rest and rest[0] in ("apply", "shift", "-h", "--help"):
        return argv
    return argv[:i + 1] + ["apply"] + rest


def main():
    parser = build_parser()
    args = parser.parse_args(_inject_automatic_apply(sys.argv[1:]))

    if args.command is None:
        args.func = cmd_gui

    if getattr(args, "auto_cmd", None) == "apply" and args.palette == "-":
        args.palette = json.load(sys.stdin)
    if getattr(args, "palette_command", None) == "show" and args.path == "-":
        args.path = json.load(sys.stdin)

    config = load_config()
    try:
        args.func(args, config)
    except (palette_generator.ImageLoadError, palette_shift.ShiftError,
            palette_shift.PaletteEditError) as e:
        print(e)
        sys.exit(1)


if __name__ == "__main__":
    main()
