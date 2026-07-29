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
        print(f"⚠ {len(drift['driftable'])} color(es) mapeados cambiaron de id en el último escaneo "
              "(el color real sigue existiendo, solo se movió de posición). Ejecutá "
              "'ucs mapping relink' para corregirlos.")
    if drift["orphaned"]:
        print(f"⚠ {len(drift['orphaned'])} color(es) mapeados ya no aparecen en tus archivos escaneados. "
              "Revisalos a mano ('ucs mapping show').")


def cmd_detect(args, config):
    result = detect_diff.detect_with_route(config, save=not args.dry_run)
    route = result["route"]

    if route == "a":
        print("Ruta a: primera detección (no había detected_palette.csv previo).")
    elif route == "b":
        print("Ruta b: la detección coincide con la guardada. Sin cambios.")
    else:
        d = result["diff"]
        print("Ruta c: los colores detectados cambiaron desde la última vez.")
        if d["added"]:
            print(f"  Nuevos ({len(d['added'])}):")
            for c in d["added"]:
                print(f"    + #{c['color']} ({c['type']}) x{c['count']}")
        if d["removed"]:
            print(f"  Desaparecidos ({len(d['removed'])}):")
            for c in d["removed"]:
                print(f"    - #{c['color']} ({c['type']}) x{c['count']}")
        print("  Se recomienda crear un mapping nuevo.")

    print(f"\nColores detectados: {len(result['colors'])}")
    for c in result["colors"]:
        print(f"  ID {c['id']:>3} | {c['type']:<12} | #{c['color']} | x{c['count']:<4} | {len(c['files'])} archivo(s)")

    if not args.dry_run:
        print(f"\nGuardado en: {config.detected_palette_csv}")

    _report_mapping_drift(config, result["colors"], persist=False)


def cmd_config_files_list(args, config):
    files = read_files_to_replace(config)
    if not files:
        print("No hay archivos configurados para escanear.")
        return
    for f in files:
        marker = "" if os.path.isfile(color_detector.expand_path(f)) else "  (no encontrado)"
        print(f"  {f}{marker}")


def cmd_config_files_add(args, config):
    files = read_files_to_replace(config)
    entry = to_home_relative(args.path)
    if entry in files:
        print(f"Ya está en la lista: {entry}")
        return
    files.append(entry)
    write_files_to_replace(config, files)
    print(f"Agregado: {entry}")
    print("Corré 'ucs detect' para actualizar los colores detectados.")


def cmd_config_files_remove(args, config):
    files = read_files_to_replace(config)
    target_expanded = color_detector.expand_path(args.path)
    remaining = [f for f in files if f != args.path and color_detector.expand_path(f) != target_expanded]
    if len(remaining) == len(files):
        print(f"No estaba en la lista: {args.path}")
        return
    write_files_to_replace(config, remaining)
    print(f"Eliminado: {args.path}")
    print("Corré 'ucs detect' para actualizar los colores detectados.")


def cmd_config_files_scan(args, config):
    print("Buscando archivos con colores en ~/.config …")
    found = color_detector.scan_config_dir_for_color_files()
    if not found:
        print("No se encontró ningún archivo con colores.")
        return

    existing = set(read_files_to_replace(config))
    new_paths = [to_home_relative(p) for p in found]
    new_paths = [hr for hr in new_paths if hr not in existing]

    for folder, files in color_detector.group_paths_by_top_level(found):
        print(f"\n{to_home_relative(folder)}/")
        for p, display in files:
            hr = to_home_relative(p)
            marker = "  (ya en la lista)" if hr in existing else ""
            print(f"    {display}{marker}")

    print(f"\nTotal: {len(found)} archivo(s) con colores, {len(new_paths)} nuevo(s).")
    print("⚠ Puede incluir hex que no son colores (ej. direcciones 0xADDR) o archivos que no querés "
          "modificar. Quitá lo que no sirva con: ucs config files remove <ruta>")

    if args.dry_run:
        print("\n(--dry-run: no se agregó nada)")
        return
    if not new_paths:
        print("\nTodos ya estaban en la lista, nada para agregar.")
        return
    if not args.yes:
        try:
            answer = input(f"\n¿Agregar los {len(new_paths)} archivo(s) nuevo(s)? (y/N): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in ("y", "yes", "s", "si", "sí"):
            print("Cancelado.")
            return

    merged = read_files_to_replace(config)
    merged.extend(new_paths)
    write_files_to_replace(config, merged)
    print(f"Agregados {len(new_paths)} archivo(s). Corré 'ucs detect' para actualizar los colores detectados.")


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
            print(f"--add espera HEX LABEL [ROLE], se recibieron {len(parts)} valor(es): {parts}")
            sys.exit(1)
        hexval, label = parts[0], parts[1]
        role = None
        if len(parts) == 3:
            role_raw = parts[2]
            if role_raw not in ("foreground", "background", "none"):
                print(f"--add: rol inválido {role_raw!r} (usá foreground, background o none)")
                sys.exit(1)
            role = _role_arg_to_value(role_raw)
        next_id = max((e["id"] for e in entries), default=0) + 1
        entry = {"id": next_id, "hex": hexval.lstrip("#").lower(), "label": label}
        if role:
            entry["role"] = role
        entries.append(entry)
    palette_store.write_palette_csv(path, entries)
    print(f"Paleta creada: {path} ({len(entries)} colores)")
    _print_palette(entries)
    _maybe_apply_after_edit(args, config, path, target_palette=path)


def cmd_palette_list(args, config):
    for p in palette_store.list_palettes(config.palettes_created_dir):
        entries = palette_store.read_palette_csv(p)
        strip = "".join(_color_swatch(e["hex"], width=2) for e in entries)
        sep = "  " if strip else ""
        print(f"{p} ({len(entries)} colores){sep}{strip}")


def cmd_palette_show(args, config):
    """Also doubles as `automatic apply <ruta-existente>`'s replacement: reads
    CSV, JSON, or (via main()'s stdin swap on '-') an already-loaded list --
    same tolerant guiless.load_palette every apply path uses -- and can
    --apply it against the mapping right after showing it."""
    resolved = _resolve_target_palette(args.path, config, mapping_path=args.mapping)
    entries = guiless.load_palette(resolved)
    label = resolved if isinstance(resolved, str) else "(stdin)"
    if not entries:
        print(f"Paleta vacía o no encontrada: {label}")
        sys.exit(1)
    display = [{"id": i + 1, **e} for i, e in enumerate(entries)]
    print(f"{label} ({len(entries)} colores):")
    _print_palette(display)
    _maybe_apply_after_edit(args, config, resolved, mapping_path=args.mapping,
                            target_palette=resolved if isinstance(resolved, str) else None)


def cmd_palette_add_color(args, config):
    path = _resolve_target_palette(args.path, config, mapping_path=args.mapping)
    entry = palette_shift.add_color(path, args.hex, args.label or "", role=_role_arg_to_value(args.role))
    if args.link is not None:
        palette_shift.set_pair(path, entry["id"], args.link)
    swatch = _color_swatch(entry["hex"])
    cells = ["Agregado:", swatch, f"#{entry['hex']}", entry.get("label", "")]
    print(" ".join(c for c in cells if c))
    print("Paleta actual:")
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
            "No se indicó paleta y el mapping no referencia ninguna (#new_palette=). "
            "Pasá la ruta de la paleta explícitamente."
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
        print(f"⚠ {dropped} asignación(es) del mapping apuntaban a ese color y quedaron sin asignar.")


def cmd_palette_edit(args, config):
    path = _resolve_target_palette(args.palette, config, mapping_path=args.mapping)
    if args.role is not None:
        new_id = palette_shift.edit_color(path, args.target, args.new_hex, role=_role_arg_to_value(args.role))
    else:
        new_id = palette_shift.edit_color(path, args.target, args.new_hex)
    if args.link is not None:
        link_target = None if args.link == "none" else args.link
        palette_shift.set_pair(path, new_id, link_target)
    print(f"Editado en {path}:")
    _print_palette(palette_store.read_palette_csv(path))
    _maybe_apply_after_edit(args, config, path, mapping_path=args.mapping, target_palette=path)


def cmd_palette_remove(args, config):
    path = _resolve_target_palette(args.palette, config, mapping_path=args.mapping)
    deleted_id = palette_shift.delete_color(path, args.target)
    _adjust_mapping_after_palette_delete(config, args.mapping, path, deleted_id)
    print(f"Borrado el color {deleted_id} de {path}:")
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
    raise argparse.ArgumentTypeError(f"valor debe ser 'on' u 'off', se recibió {raw!r}")


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
        print(f"Ya existe una paleta generada para esta imagen: {reused_path} ({len(entries)} color(es)) "
              "-- se usa esa. Pasá --regenerate para forzar una nueva generación.")
        print(f"\nGuardada en: {reused_path}")
        if not args.apply:
            print(f"Para aplicarla: ucs palette show {reused_path} --apply")
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

    print(f"Paleta generada desde {args.image} ({n_colors} color(es)):")
    _print_palette(entries)
    print(f"\nGuardada en: {saved_path}")
    if not args.apply:
        print(f"Para aplicarla: ucs palette show {saved_path} --apply")
    # entries is already the in-memory, just-computed list -- guiless accepts
    # it directly, no need to round-trip it back through the file we just wrote.
    _maybe_apply_after_edit(args, config, entries, mapping_path=args.mapping, target_palette=saved_path)


def _resolve_detected_csv(args, config):
    return args.detected_palette or config.detected_palette_csv


def cmd_mapping_new(args, config):
    detected_path = _resolve_detected_csv(args, config)
    detected_colors = color_detector.read_detected_csv(detected_path)
    if not detected_colors:
        print(f"No hay colores detectados en {detected_path}. Corre 'detect' primero.")
        sys.exit(1)

    target_palette = _resolve_path(args.target_palette, config.palettes_created_dir, config.project_dir)
    new_palette = palette_store.read_palette_csv(target_palette)
    if not new_palette:
        print(f"Paleta objetivo vacía o no encontrada: {target_palette}")
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
        location = f"{target_palette}  (registro: {config.mapping_registry_json})"

    siblings = conflicts.find_case2_siblings(detected_colors)
    color_by_id = {c["id"]: c for c in detected_colors}

    print(f"Paleta objetivo: {target_palette} ({len(new_palette)} colores)")
    print("Colores detectados:")
    for c in detected_colors:
        twin_note = ""
        group = siblings.get(c["color"].lower())
        if group and len(group) > 1:
            other_ids = [g["id"] for g in group if g["id"] != c["id"]]
            twin_note = f"  (mismo color también como id {other_ids})"
        print(f"  ID {c['id']:>3} | {c['type']:<12} | #{c['color']} | x{c['count']:<4}{twin_note}")

    print("\nPaleta objetivo:")
    for p in new_palette:
        print(f"  ID {p['id']:>3} | #{p['hex']} | {p['label']}")

    print(f"\nIngresá pares 'old_id new_id' (ENTER vacío para terminar). Mapping: {location}")
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
            print("Formato inválido. Usá: <old_id> <new_id>")
            continue
        old_id, new_id = int(parts[0]), int(parts[1])
        store.add_or_update(old_id, new_id)  # persists immediately

        collisions = conflicts.find_case1_collisions(detected_colors, new_palette, store.resolved_entries())
        relevant = [c for c in collisions if c["old_id"] == old_id]
        for c in relevant:
            print(f"  ⚠ #{c['new_hex']} ya existe en la paleta detectada (id {c['conflict_with_ids']}).")

        old_color_entry = color_by_id.get(old_id)
        group = siblings.get(old_color_entry["color"].lower()) if old_color_entry else None
        if group and len(group) > 1:
            twins = [g["id"] for g in group if g["id"] != old_id]
            for twin_id in twins:
                if store._find(twin_id) is None:
                    print(f"  ⚠ El id {old_id} también aparece como id {twin_id} en otro formato. "
                          f"Corré: {twin_id} {new_id}  (o dejalo para mapearlo distinto)")

    if not store.resolved_entries():
        print("No se guardó ningún mapping (vacío).")
        return

    print(f"\nMapping guardado: {location} ({len(store.resolved_entries())} entradas)")


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


def cmd_mapping_show(args, config):
    store = _resolve_mapping_store_for_show(args, config)
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
        print("\nConflictos (caso 1):")
        for c in collisions:
            print(f"  old_id {c['old_id']} -> #{c['new_hex']} colisiona con id(s) {c['conflict_with_ids']}")


def cmd_mapping_relink(args, config):
    store = _resolve_mapping_store_for_show(args, config)
    entries = store.entries
    if not entries:
        print("Mapping vacío o no encontrado.")
        return

    detected_path = store.old_palette or config.detected_palette_csv
    detected_colors = color_detector.read_detected_csv(detected_path)
    drift = mapping_store.detect_drift(entries, detected_colors)

    if not drift["driftable"] and not drift["orphaned"]:
        print("Nada para re-vincular: el mapping está al día con los colores detectados.")
        return

    if drift["driftable"]:
        print(f"{len(drift['driftable'])} color(es) para re-vincular (el color real sigue existiendo, "
              "solo se movió de id):")
        for d in drift["driftable"]:
            print(f"  old_id {d['old_id']} -> {d['correct_old_id']}  (#{d['hex']}, {d['type']})")
    if drift["orphaned"]:
        print(f"\n{len(drift['orphaned'])} color(es) huérfano(s) -- ya no existen, no se auto-resuelven:")
        for d in drift["orphaned"]:
            print(f"  old_id {d['old_id']}  (#{d['hex']}, {d['type']}) -- corregilo a mano.")

    if not drift["driftable"]:
        return

    if not args.yes:
        try:
            answer = input(f"\n¿Re-vincular {len(drift['driftable'])} entrada(s)? (y/N): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in ("y", "yes", "s", "si", "sí"):
            print("Cancelado.")
            return

    relinked = store.apply_drift_relinks(drift["driftable"])
    print(f"\n{relinked} entrada(s) re-vinculada(s).")


def cmd_mapping_list(args, config):
    registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
    sections = registry.all_sections()
    if not sections:
        print("No hay ningún mapping todavía.")
        return
    active = registry.active_palette_path()
    for palette_path, store in sections:
        marker = "  (activo)" if palette_path == active else ""
        print(f"  {palette_path}{marker} -- {len(store.resolved_entries())} entrada(s)")


def _print_reorder_banner(warning: str) -> None:
    """An unmissable, visually distinct banner for tier="compacted" applies --
    deliberately louder than an ordinary "⚠ ..." line, since this is
    specifically the "your mapping's assignment order changed and may break
    a previously-tuned theme" risk resolve_apply_targets exists to surface."""
    bar = "=" * 70
    print(f"\n{bar}\n¡ATENCIÓN! REASIGNACIÓN DE MAPPING\n{bar}")
    print(warning)
    print(f"{bar}\n")


def _apply_or_test(args, config, mode):
    if args.mapping:
        store = mapping_store.MappingStore(args.mapping, project_dir=config.project_dir).load()
    else:
        registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
        store = registry.for_active()
    new_p, entries = store.new_palette, store.entries
    if not entries:
        print(f"Mapping vacío o no encontrado: {args.mapping or '(ningún mapping activo)'}")
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
        print(f"⚠ La paleta {new_p or '(no definida)'} tiene {resolution['available']} color(es), "
              f"pero el mapping necesita al menos {resolution['needed']} (cantidad de ids "
              "distintos usados). Revisá el mapping ('ucs mapping show <mapping>') o "
              "regenerá/reimportá la paleta.")
        sys.exit(1)
    if resolution["tier"] == "compacted":
        _print_reorder_banner(resolution["warning"])
    entries = resolution["final_entries"]

    siblings = conflicts.find_case2_siblings(detected_colors)
    collisions = conflicts.find_case1_collisions(detected_colors, new_palette, entries)
    convergence = conflicts.find_target_convergence(detected_colors, new_palette, entries, sibling_groups=siblings)
    if (collisions or convergence) and not args.force:
        print("⚠ Se detectaron conflictos. Usá --force para continuar de todas formas:")
        for c in collisions:
            print(f"  [caso 1] old_id {c['old_id']} -> #{c['new_hex']} colisiona con id(s) {c['conflict_with_ids']}")
        for c in convergence:
            print(f"  [convergencia] ids {c['old_ids']} -> #{c['target_hex']} (se pierde la distinción entre ellos)")
        sys.exit(1)

    dry_run = mode == "test"
    results = color_replacer.apply_mapping(
        detected_colors, new_palette, entries, config.backup_dir, dry_run=dry_run
    )
    total = sum(r.get("count", 0) for r in results)
    errors = [r for r in results if "error" in r]
    label = "Simulación (test)" if dry_run else "Aplicado"
    print(f"{label}: {total} reemplazos en {len(results)} operaciones de archivo.")
    for r in results:
        if r.get("count", 0) > 0 or "error" in r:
            status = r.get("error", f"x{r['count']}")
            print(f"  {r['file']}: {r['old_color']} -> {r['new_color']} [{r['type']}] {status}")
    if errors:
        print(f"Errores: {len(errors)}")
    if (collisions or convergence) and args.force and not dry_run:
        print("⚠ Se forzó un conflicto/convergencia: el mapping usado probablemente quedó desactualizado "
              "(algunos ids ya no representan el mismo color real). Conviene rehacerlo antes de reusarlo.")
    if not dry_run:
        roles_path = os.path.join(os.path.dirname(detected_path), "color_roles.json")
        role_collisions, pair_collisions = color_detector.rekey_roles_after_apply(
            roles_path, detected_colors, new_palette, entries
        )
        for new_key, old_keys in role_collisions:
            print(f"⚠ {new_key} quedó sin rol asignado: los colores {old_keys} tenían roles distintos "
                  "y convergieron en él. Reasigná el rol a mano si corresponde.")
        for fg_key, dangling_bg_key in pair_collisions:
            print(f"⚠ {fg_key} perdió su vínculo con {dangling_bg_key}: ese background ya no existe "
                  "como tal tras el apply. Re-vinculalo a mano si corresponde.")
        # The colors just replaced no longer exist in the files BY DESIGN --
        # re-stamp this mapping's identity to the NEW colors now actually
        # there, so the drift refresh below doesn't mistake "I just replaced
        # this" for real drift and flag it orphaned.
        store.entries = mapping_store.stamp_applied_entries(store.entries, entries, new_palette, detected_colors)
        store.save()
        print(f"Backup en: {config.backup_dir}")
        print("Para deshacer: ucs restore")
        _refresh_detected_after_change(config)
        started = restart_actions.run_enabled(restart_actions.read_restart_actions(config))
        for a in started:
            print(f"  Reiniciando: {a['label']}" + ("" if a["started"] else f" (error: {a['error']})"))


def _refresh_detected_after_change(config):
    """Re-scan and persist detected_palette.csv after files on disk changed
    (a real apply/automatic or a restore) — otherwise the old mapping keeps
    pointing at colors that no longer exist in the files."""
    colors = detect_diff.run_detect(config)
    color_detector.write_detected_csv(colors, config.detected_palette_csv)
    print(f"Colores re-detectados y guardados en: {config.detected_palette_csv}")
    _report_mapping_drift(config, colors, persist=True)


def cmd_test(args, config):
    _apply_or_test(args, config, "test")


def cmd_apply(args, config):
    _apply_or_test(args, config, "apply")


def cmd_gui(args, config):
    from .gui.app import main as gui_main
    sys.exit(gui_main())


def cmd_restore(args, config):
    results = color_replacer.restore_files(config.files_to_replace, config.backup_dir)
    for r in results:
        status = "restaurado" if r["restored"] else "sin backup"
        print(f"  {r['file']}: {status}")

    _refresh_detected_after_change(config)

    if args.restart is not None:
        do_restart = args.restart
    else:
        try:
            answer = input("¿Reiniciar los servicios configurados? (y/N): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        do_restart = answer in ("y", "yes", "s", "si", "sí")

    if do_restart:
        started = restart_actions.run_enabled(restart_actions.read_restart_actions(config))
        for a in started:
            print(f"  Reiniciando: {a['label']}" + ("" if a["started"] else f" (error: {a['error']})"))


def cmd_automatic(args, config):
    if bool(args.palette) == bool(args.from_image):
        print("Pasá exactamente uno: la paleta (posicional) o --from-image <wallpaper>.")
        sys.exit(1)

    registry = None
    if args.mapping:
        store = mapping_store.MappingStore(args.mapping, project_dir=config.project_dir).load()
        mapping_desc = args.mapping
    else:
        registry = mapping_store.MappingRegistry(config.mapping_registry_json, project_dir=config.project_dir)
        store = registry.for_active()
        mapping_desc = "el mapping activo"

    palette_source = args.palette
    target_path = None
    if args.from_image:
        if not store.entries:
            print(f"Mapping vacío o no encontrado: {mapping_desc}")
            sys.exit(1)
        fallback_colors = len({e["new_id"] for e in store.entries})
        out_path, n_colors, reused_path = _resolve_generate_target(
            config, args.from_image, None, args.regenerate, args.colors, fallback_colors,
        )
        if reused_path:
            palette_source = palette_store.read_palette_csv(reused_path)
            target_path = reused_path
            print(f"Ya existe una paleta generada para esta imagen: {reused_path} "
                  f"({len(palette_source)} color(es)) -- se usa esa. Pasá --regenerate para forzar "
                  "una nueva generación.")
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
            print(f"Paleta generada desde {args.from_image} ({n_colors} color(es)) — guardada en: {saved_path}")
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
    _report_apply_result(result, args, config, mapping_desc)


def _report_apply_result(result, args, config, mapping_desc):
    """Shared by `automatic` and `automatic shift`: turn a guiless.apply_palette
    result into CLI output (and the post-apply detect refresh / restarts on a
    real apply). Exits 1 on any non-applied status. mapping_desc is a plain
    display string (a path, or "el mapping activo") -- only used in messages."""
    status = result["status"]
    if status == "insufficient_palette":
        print(f"La paleta nueva tiene {result['available']} color(es), pero el mapping necesita "
              f"{result['needed']} (roles distintos usados). Generá una paleta con al menos "
              f"{result['needed']} colores (--colors {result['needed']}).")
        sys.exit(1)
    elif status == "needs_confirmation":
        print(f"La paleta nueva tiene {result['surplus_palette_count']} color(es) de más que no se van a usar.")
        print("Volvé a correr con --yolo para aplicar de todas formas.")
        sys.exit(1)
    elif status == "conflicts":
        print("⚠ Se detectaron conflictos. Usá --force para continuar de todas formas:")
        for c in result["conflicts"]:
            print(f"  [caso 1] old_id {c['old_id']} -> #{c['new_hex']} colisiona con id(s) {c['conflict_with_ids']}")
        for c in result.get("convergence", []):
            print(f"  [convergencia] ids {c['old_ids']} -> #{c['target_hex']} (se pierde la distinción entre ellos)")
        sys.exit(1)
    elif status == "empty_mapping":
        print(f"Mapping vacío o no encontrado: {mapping_desc}")
        sys.exit(1)
    else:
        results = result["results"]
        total = sum(r.get("count", 0) for r in results)
        label = "Simulación (test)" if args.test else "Aplicado"
        print(f"{label}: {total} reemplazos en {len(results)} operaciones de archivo.")
        if result.get("stale_mapping_warning"):
            print("⚠ Se forzó un conflicto/convergencia: el mapping usado probablemente quedó desactualizado "
                  "(algunos ids ya no representan el mismo color real). Conviene rehacerlo antes de reusarlo.")
        for new_key, old_keys in result.get("role_collisions", []):
            print(f"⚠ {new_key} quedó sin rol asignado: los colores {old_keys} tenían roles distintos "
                  "y convergieron en él. Reasigná el rol a mano si corresponde.")
        if not args.test:
            print(f"Backup en: {config.backup_dir}")
            _refresh_detected_after_change(config)
            started = restart_actions.run_enabled(restart_actions.read_restart_actions(config))
            for a in started:
                print(f"  Reiniciando: {a['label']}" + ("" if a["started"] else f" (error: {a['error']})"))


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
            mapping_desc = "el mapping activo"
    result = guiless.apply_palette(
        palette_source, store, config.backup_dir,
        dry_run=args.test, force=args.force, yolo=args.yolo, project_dir=config.project_dir,
    )
    _report_apply_result(result, args, config, mapping_desc)


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
    print(f"Paleta {'regenerada' if result['regenerated'] else 'ajustada'}"
          f"{' (simulación, no se guardó)' if args.test else ''}: {palette_path}")
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
            raise argparse.ArgumentTypeError(f"formato inválido: {part!r} (esperado clave=valor)")
        key, _, value = part.partition("=")
        key = key.strip()
        try:
            percentages[key] = float(value.strip())
        except ValueError:
            raise argparse.ArgumentTypeError(f"valor no numérico para {key!r}: {value.strip()!r}")

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
        raise argparse.ArgumentTypeError("--shuffle debe ser un entero >= 0 o 'next'")
    if value < 0:
        raise argparse.ArgumentTypeError("--shuffle debe ser un entero >= 0 o 'next'")
    return value


def _parse_overfetch(raw: str):
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError("--overfetch debe ser un entero >= 0")
    if value < 0:
        raise argparse.ArgumentTypeError("--overfetch debe ser un entero >= 0")
    return value


def _add_shuffle_args(parser, from_image_note=""):
    parser.add_argument(
        "--shuffle", type=_parse_shuffle, default=None,
        help=f"Saltear los primeros N candidatos a primary{from_image_note} (todo lo demás se elige "
             "relativo a ese nuevo primary). También acepta 'next': retoma desde el último valor "
             "usado + 1, cíclico según el tamaño del pool (--colors + --overfetch) -- pensado para "
             "scripts que llaman esto repetidamente para ir probando variantes.",
    )
    parser.add_argument(
        "--overfetch", type=_parse_overfetch, default=0,
        help=f"Candidatos extra a considerar más allá de --colors{from_image_note} (default: 0). "
             "auxN: se eligen entre n_needed+overfetch candidatos y sobreviven los de mejor score. "
             "shading: el ramp se genera como si hicieran falta n_needed+overfetch shades y se "
             "conservan los más cercanos a primary (más densos). También le da a --shuffle más "
             "margen para saltear sin quedarse sin candidatos.",
    )


def _add_scoring_args(parser, from_image_note=""):
    parser.add_argument(
        "--scoring", choices=["default", "alternative", "custom"], default="default",
        help=f"Cómo ponderar coverage/saturación/midtone/contraste al elegir colores{from_image_note} "
             "(default: default). 'custom' usa --custom-scoring-values, o si no se pasa, los valores "
             "guardados en config.json (y si tampoco hay, cae a 'default').",
    )
    parser.add_argument(
        "--custom-scoring-values", type=_parse_custom_scoring_values,
        help="Solo con --scoring custom: 'coverage=20,saturation=40,midtone=30,contrast=10' "
             "(deben sumar 100). Tiene prioridad sobre lo guardado en config.json.",
    )


def _add_apply_args(parser, apply_help=None):
    """Shared by every palette-mutating `palette` subcommand: after the
    mutation, --apply re-applies the (canonical, or --mapping) mapping
    against the result -- reusing the exact same guiless.apply_palette
    pipeline `automatic` already uses (see _maybe_apply_after_edit). Without
    --apply, these flags are simply unused."""
    parser.add_argument(
        "--apply", action="store_true",
        help=apply_help or "Además, aplicar el mapping contra la paleta resultante (como 'automatic apply').",
    )
    parser.add_argument("--mapping", help="default: mappings/mapping.csv, el mapping canónico")
    parser.add_argument("--test", action="store_true", help="Con --apply: simular, no modificar archivos")
    parser.add_argument("--force", action="store_true",
                         help="Con --apply: aplicar aunque haya conflictos de caso 1/convergencia")
    parser.add_argument("--yolo", action="store_true",
                         help="Con --apply: aplicar aunque sobren colores en la paleta")


def _add_shift_args(parser):
    """The modifier flags for a shift (my-eyes/ying-yang always valid; the
    rest only regenerate a GENERATED palette -- see palette_shift.shift_palette).
    Shared verbatim by `palette shift` and the back-compat `automatic shift`,
    so the two entry points can never drift apart."""
    onofftoggle = ["on", "off", "toggle"]
    parser.add_argument("--my-eyes", choices=onofftoggle, default=None, metavar="on|off|toggle",
                         help="Saturación extra. 'on'/'off' fija el valor, 'toggle' lo invierte. "
                              "Se aplica sin regenerar, aun en paletas creadas (default: mantener).")
    parser.add_argument("--my-eyes-factor", type=float, default=None,
                         help="Con --my-eyes: multiplicador de croma CIELAB (default: mantener). "
                              "Se aplica sin regenerar, aun en paletas creadas.")
    parser.add_argument("--my-eyes-max-chroma", type=float, default=None,
                         help="Con --my-eyes: tope de croma CIELAB resultante (default: mantener). "
                              "Se aplica sin regenerar, aun en paletas creadas.")
    parser.add_argument("--ying-yang", choices=onofftoggle, default=None, metavar="on|off|toggle",
                         help="Paleta complementaria (tonos +180°). 'on'/'off'/'toggle' (default: mantener). "
                              "Se aplica sin regenerar, aun en paletas creadas.")
    parser.add_argument("--mode", choices=["balanced", "contrast", "shading"], default=None,
                         help="Solo paletas generadas: regenera con este modo (default: mantener).")
    parser.add_argument("--scoring", choices=["default", "alternative", "custom"], default=None,
                         help="Solo paletas generadas: regenera con esta ponderación (default: mantener).")
    parser.add_argument("--custom-scoring-values", type=_parse_custom_scoring_values, default=None,
                         help="Con --scoring custom: 'coverage=20,saturation=40,midtone=30,contrast=10' (suman 100).")
    parser.add_argument("--weighted-contrast", choices=["on", "off"], default=None, metavar="on|off",
                         help="Solo paletas generadas: contraste ponderado contra todos los clusters "
                              "(default: mantener).")
    parser.add_argument("--shuffle", type=_parse_shuffle, default=None,
                         help="Solo paletas generadas: saltear N candidatos a primary, o 'next' (cíclico). Regenera.")
    parser.add_argument("--overfetch", type=_parse_overfetch, default=None,
                         help="Solo paletas generadas: candidatos extra más allá de --colors. Regenera.")
    parser.add_argument("--colors", type=int, default=None,
                         help="Solo paletas generadas: regenera con esta cantidad de colores (default: mantener).")
    parser.add_argument("--shading-direction", choices=["dark", "light", "toggle"], default=None,
                         help="Solo paletas generadas en modo shading: 'dark'/'light' fija la dirección "
                              "del ramp, 'toggle' la invierte (default: mantener). Regenera.")
    parser.add_argument("--shading-min-luminance", type=float, default=None,
                         help="Solo con --shading-direction dark: luminancia mínima del ramp "
                              "(default: mantener). Regenera.")
    parser.add_argument("--shading-max-luminance", type=float, default=None,
                         help="Solo con --shading-direction light: luminancia máxima del ramp "
                              "(default: mantener). Regenera.")
    parser.add_argument("--keep-custom", choices=onofftoggle, default=None, metavar="on|off|toggle",
                         help="Al regenerar (paletas generadas), preservar los colores agregados/editados "
                              "a mano en su mismo lugar en vez de descartarlos (default: mantener la "
                              "preferencia guardada en la paleta, que empieza en 'on'). No dispara una "
                              "regeneración por sí solo.")
    parser.add_argument("--eco", choices=onofftoggle, default=None, metavar="on|off|toggle",
                         help="Al regenerar (paletas generadas), forzar mismo tono en las parejas "
                              "foreground/background que contrastan por luminancia (contraste solo por "
                              "luminancia, sin variedad de tono) (default: mantener la preferencia "
                              "guardada, que empieza en 'off'). No dispara una regeneración por sí solo. "
                              "Los colores tageados fg/bg sin pareja vinculada se ignoran para la "
                              "generación (se avisa, sin bloquear).")
    parser.add_argument("--hallucinate", choices=onofftoggle, default=None, metavar="on|off|toggle",
                         help="Al regenerar (paletas generadas) contra una imagen monocromática, "
                              "sintetizar un acento saturado + un ramp de shading a partir de él en vez "
                              "de una paleta gris de verdad (default: mantener la preferencia guardada, "
                              "que empieza en 'on'). No dispara una regeneración por sí solo.")


def _add_my_eyes_generation_args(parser):
    """--my-eyes' chroma-boost knobs, for a FRESH generation (palette
    generate / automatic --from-image) -- fixed defaults matching
    palette_generator._MY_EYES_CHROMA_FACTOR/_MY_EYES_CHROMA_MAX. Shared so
    the two entry points can't drift apart; see
    palette_generator._boost_saturation for what these mean."""
    parser.add_argument("--my-eyes-factor", type=float, default=palette_generator._MY_EYES_CHROMA_FACTOR,
                         help=f"Con --my-eyes: multiplicador de croma CIELAB "
                              f"(default: {palette_generator._MY_EYES_CHROMA_FACTOR}).")
    parser.add_argument("--my-eyes-max-chroma", type=float, default=palette_generator._MY_EYES_CHROMA_MAX,
                         help=f"Con --my-eyes: tope de croma CIELAB resultante "
                              f"(default: {palette_generator._MY_EYES_CHROMA_MAX}).")


def _add_shading_generation_args(parser):
    """--mode shading's direction/luminance-bounds knobs, for a FRESH
    generation (palette generate / automatic --from-image) -- fixed
    defaults (dark, 8, 92), no "toggle" (there's no stored prior state to
    invert yet). Shared so the two entry points can't drift apart; see
    palette_generator.generate_shading_series for what these mean."""
    parser.add_argument("--shading-direction", choices=["dark", "light"], default="dark",
                         help="Solo con --mode shading: hacia dónde generar los shades. 'dark' "
                              "(default): oscurece hacia el negro (una 'shade' de verdad). 'light': "
                              "aclara hacia el blanco (técnicamente un 'tint').")
    parser.add_argument("--shading-min-luminance", type=float, default=8.0,
                         help="Solo con --mode shading --shading-direction dark: luminancia mínima "
                              "del ramp (default: 8).")
    parser.add_argument("--shading-max-luminance", type=float, default=92.0,
                         help="Solo con --mode shading --shading-direction light: luminancia máxima "
                              "del ramp (default: 92).")


def _add_keep_custom_generation_arg(parser):
    """Whether a FRESH generation (palette generate / automatic --from-image)
    preserves hand-added/edited colors already at the output path, when it
    already exists as a palette (e.g. a wallpaper-switch hook regenerating
    the same generated.csv every time) -- see
    palette_shift.generate_and_save_palette. Shared so the two entry points
    can't drift apart."""
    parser.add_argument("--keep-custom", choices=["on", "off", "toggle"], default=None, metavar="on|off|toggle",
                         help="Si la ruta de salida ya existe como paleta: preservar sus colores "
                              "agregados/editados a mano en vez de descartarlos (default: mantener la "
                              "preferencia guardada -- de esa paleta si ya existe, si no la del proyecto, "
                              "que empieza en 'on').")


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
                         help="Forzar mismo tono en las parejas foreground/background que contrastan "
                              "por luminancia, en vez de dejar que cada una mantenga su propio tono "
                              "(default: mantener la preferencia guardada -- de esa paleta si ya existe, "
                              "si no la del proyecto, que empieza en 'off').")


def _add_hallucinate_generation_arg(parser):
    """Whether a FRESH generation (palette generate / automatic --from-image)
    against a monochrome source image synthesizes a saturated accent (+ a
    shading ramp off it) instead of a genuinely greyscale palette -- see
    palette_generator.generate_palette's hallucinate param. Shared so the
    two entry points can't drift apart."""
    parser.add_argument("--hallucinate", choices=["on", "off", "toggle"], default=None,
                         metavar="on|off|toggle",
                         help="Contra una imagen monocromática: sintetizar un acento saturado + un ramp "
                              "de shading a partir de él ('on', default: mantener la preferencia guardada "
                              "-- de la paleta de salida si ya existe, si no la del proyecto, que empieza "
                              "en 'on') en vez de una paleta gris de verdad ('off'). Sin efecto si la "
                              "imagen no es monocromática.")


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
                         help="Forzar una regeneración aunque ya exista una paleta generada para esta "
                              "imagen (sin --colors, usa la cantidad de colores de la paleta existente, "
                              "o si no hay ninguna, la que necesite el mapping).")


def build_parser():
    parser = argparse.ArgumentParser(description="Color Switcher — CLI")
    # not required: no subcommand at all means "launch the GUI" (see main())
    sub = parser.add_subparsers(dest="command", required=False)

    p = sub.add_parser("detect", help="Escanear archivos y actualizar detected_palette.csv")
    p.add_argument("--dry-run", action="store_true", help="No sobrescribir el CSV, solo mostrar")
    p.set_defaults(func=cmd_detect)

    cf = sub.add_parser("config", help="Configuración del proyecto (config.json)")
    cfsub = cf.add_subparsers(dest="config_command", required=True)

    cfl = cfsub.add_parser("files", help="Archivos a escanear (files_to_replace)")
    cflsub = cfl.add_subparsers(dest="files_command", required=True)

    cfl_list = cflsub.add_parser("list", help="Listar archivos configurados")
    cfl_list.set_defaults(func=cmd_config_files_list)

    cfl_add = cflsub.add_parser("add", help="Agregar un archivo a escanear")
    cfl_add.add_argument("path", help="Ruta al archivo (absoluta o con ~)")
    cfl_add.set_defaults(func=cmd_config_files_add)

    cfl_remove = cflsub.add_parser("remove", help="Quitar un archivo de la lista")
    cfl_remove.add_argument("path", help="Tal como aparece en 'config files list', o su ruta absoluta")
    cfl_remove.set_defaults(func=cmd_config_files_remove)

    cfl_scan = cflsub.add_parser(
        "scan-config", help="Buscar en ~/.config archivos con colores y agregarlos a la lista")
    cfl_scan.add_argument("--dry-run", action="store_true",
                           help="Solo mostrar lo que encontraría, sin agregar nada")
    cfl_scan.add_argument("--yes", action="store_true", help="Agregar sin pedir confirmación")
    cfl_scan.set_defaults(func=cmd_config_files_scan)

    pp = sub.add_parser("palette", help="Gestión de paletas creadas")
    psub = pp.add_subparsers(dest="palette_command", required=True)

    pc = psub.add_parser("create", help="Crear una paleta CSV")
    pc.add_argument("path", help="Ruta de salida (relativa a palettes/created/ si no es absoluta)")
    pc.add_argument("--add", nargs="+", metavar="HEX", action="append",
                     help="Agregar un color: HEX LABEL, o HEX LABEL ROLE "
                          "(ROLE: foreground|background|none) (puede repetirse)")
    _add_apply_args(pc)
    pc.set_defaults(func=cmd_palette_create)

    pl = psub.add_parser("list", help="Listar paletas creadas")
    pl.set_defaults(func=cmd_palette_list)

    ps = psub.add_parser("show", help="Mostrar una paleta con sus colores en la terminal")
    ps.add_argument("path", nargs="?", default=None,
                    help="Ruta a un CSV o JSON, o '-' para JSON por stdin "
                         "(default: la que aplica el mapping actual, vía #new_palette=)")
    _add_apply_args(ps, apply_help="Además, aplicar el mapping contra esta paleta (reemplazo de 'automatic apply').")
    ps.set_defaults(func=cmd_palette_show)

    pa = psub.add_parser("add-color", help="Agregar un color a una paleta existente")
    pa.add_argument("path", nargs="?", default=None,
                    help="Paleta (default: la que aplica el mapping actual, vía #new_palette=)")
    pa.add_argument("hex")
    pa.add_argument("--label", default="", help="Etiqueta para el color (default: vacía)")
    pa.add_argument("--role", choices=["foreground", "background", "none"], default=None,
                    help="Rol de contraste del color (default: sin marcar)")
    pa.add_argument("--link", default=None, metavar="ID-O-HEX",
                    help="Vincular este color (recién agregado) como pareja fg/bg de otro color de la "
                         "misma paleta, por id o hex (default: sin vincular).")
    _add_apply_args(pa)
    pa.set_defaults(func=cmd_palette_add_color)

    ped = psub.add_parser("edit", help="Cambiar un color de una paleta por otro")
    ped.add_argument("palette", nargs="?", default=None,
                     help="Paleta a editar (default: la que aplica el mapping actual, vía #new_palette=)")
    ped.add_argument("target", help="Color a cambiar: su id o su hex")
    ped.add_argument("new_hex", metavar="new-hex", help="Nuevo color (hex)")
    ped.add_argument("--role", choices=["foreground", "background", "none"], default=None,
                     help="Además, fijar el rol de contraste (default: no lo toca)")
    ped.add_argument("--link", default=None, metavar="ID-O-HEX",
                     help="Además, vincular este color como pareja fg/bg de otro color de la misma "
                          "paleta, por id o hex ('none' para desvincularlo; default: no lo toca).")
    _add_apply_args(ped)
    ped.set_defaults(func=cmd_palette_edit)

    prm = psub.add_parser("remove", help="Borrar un color de una paleta (ajusta el mapping si corresponde)")
    prm.add_argument("palette", nargs="?", default=None,
                     help="Paleta (default: la que aplica el mapping actual, vía #new_palette=)")
    prm.add_argument("target", help="Color a borrar: su id o su hex")
    _add_apply_args(prm)
    prm.set_defaults(func=cmd_palette_remove)

    pg = psub.add_parser("generate", help="Generar una paleta a partir de una imagen (wallpaper)")
    pg.add_argument("image", help="Ruta a la imagen")
    pg.add_argument("--colors", type=int, default=None,
                     help="Cantidad de colores a generar (default: la cantidad de roles distintos que "
                          "usa el mapping -- ver --mapping --, o 6 si no hay mapping)")
    pg.add_argument("--sample-size", type=int, default=40000, help="Píxeles a samplear (default: 40000)")
    pg.add_argument("--mode", choices=["balanced", "contrast", "shading"], default="contrast",
                     help="Cómo elegir secondary/auxN (default: contrast). 'balanced': secondary por score, "
                          "sin sesgo respecto al contraste con primary. 'contrast': secondary maximiza contraste "
                          "con primary (comportamiento original). 'shading': el resto de la paleta son variantes "
                          "monocromáticas (mismo tono) de primary")
    pg.add_argument("--my-eyes", action="store_true",
                     help="Saturar los colores elegidos justo antes de guardarlos")
    _add_my_eyes_generation_args(pg)
    pg.add_argument("--ying-yang", type=_parse_on_off, default=False, metavar="on|off",
                     help="Ying Yang: usar la paleta complementaria (todos los colores rotados 180° en el "
                          "tono). 'on' u 'off' (default: off)")
    _add_scoring_args(pg)
    pg.add_argument(
        "--no-weighted-contrast", dest="weighted_contrast", action="store_false", default=True,
        help="Comparar contraste solo contra el cluster más dominante de la imagen, en vez del sistema "
             "ponderado contra todos los clusters (default: ponderado, recomendado; sirve mejor para "
             "imágenes con un background de un solo color claro).",
    )
    _add_shuffle_args(pg)
    _add_shading_generation_args(pg)
    _add_keep_custom_generation_arg(pg)
    _add_eco_generation_arg(pg)
    _add_hallucinate_generation_arg(pg)
    _add_regenerate_arg(pg)
    pg.add_argument("--out", help="Ruta de salida explícita (default: un archivo persistente por imagen, "
                                  "bajo palettes/created/ -- ver find_palettes_for_image/--regenerate. "
                                  "Si se pasa, siempre se (re)genera ahí, sin buscar una paleta existente.")
    _add_apply_args(pg)
    pg.set_defaults(func=cmd_palette_generate)

    psh = psub.add_parser("shift", help="Cambiar modificadores de una paleta y, opcionalmente, reaplicar")
    psh.add_argument("palette", nargs="?", default=None,
                     help="Paleta a shiftear (default: la que aplica el mapping actual, vía #new_palette=)")
    _add_shift_args(psh)
    _add_apply_args(psh, apply_help="Además, aplicar el mapping contra la paleta shifteada (default: no aplicar).")
    psh.set_defaults(func=cmd_palette_shift)

    mp = sub.add_parser("mapping", help="Gestión de mappings")
    msub = mp.add_subparsers(dest="mapping_command", required=True)

    mn = msub.add_parser("new", help="Crear un mapping interactivo")
    mn.add_argument("target_palette", help="Ruta a la paleta objetivo")
    mn.add_argument("--detected-palette", help="Usar un detected_palette.csv específico")
    mn.add_argument("--out", help="Ruta de salida standalone explícita (default: la sección de esta "
                                  "paleta en el registro de mappings, mappings/mappings.json -- ver "
                                  "'mapping list'). Si se pasa, se crea un archivo aparte, nunca "
                                  "auto-detectado luego.")
    mn.set_defaults(func=cmd_mapping_new)

    ms = msub.add_parser("show", help="Mostrar un mapping y sus conflictos")
    ms.add_argument("path", nargs="?", default=None,
                    help="Archivo de mapping standalone (legacy). Omitir para usar --palette o el "
                         "mapping activo.")
    ms.add_argument("--palette", help="Mostrar el mapping de esta paleta específica (su propia "
                                      "sección en el registro), sin cambiar cuál está activa.")
    ms.set_defaults(func=cmd_mapping_show)

    ml = msub.add_parser("list", help="Listar todas las paletas que tienen mapping, y cuál está activa")
    ml.set_defaults(func=cmd_mapping_list)

    mr = msub.add_parser(
        "relink",
        help="Re-vincular entradas cuyo color detectado cambió de id en el último escaneo "
             "(ver 'ucs detect'/'ucs apply' -- avisan cuando hay algo para re-vincular)",
    )
    mr.add_argument("--mapping", help="Archivo de mapping standalone (legacy). default: --palette, o "
                                      "si tampoco se pasa, el mapping activo.")
    mr.add_argument("--palette", help="Re-vincular el mapping de esta paleta específica, sin cambiar "
                                      "cuál está activa.")
    mr.add_argument("--yes", action="store_true", help="Re-vincular sin pedir confirmación")
    mr.set_defaults(func=cmd_mapping_relink)

    t = sub.add_parser("test", help="Simular aplicar un mapping (no modifica archivos)")
    t.add_argument("--mapping", help="default: mappings/mapping.csv, el mapping canónico")
    t.add_argument("--force", action="store_true", help="Ignorar conflictos de caso 1")
    t.set_defaults(func=cmd_test)

    a = sub.add_parser("apply", help="Aplicar un mapping (hace backup real)")
    a.add_argument("--mapping", help="default: mappings/mapping.csv, el mapping canónico")
    a.add_argument("--force", action="store_true", help="Ignorar conflictos de caso 1")
    a.set_defaults(func=cmd_apply)

    r = sub.add_parser("restore", help="Restaurar archivos desde el backup")
    r_restart = r.add_mutually_exclusive_group()
    r_restart.add_argument("--restart", dest="restart", action="store_true", default=None,
                            help="Reiniciar los servicios configurados sin preguntar")
    r_restart.add_argument("--no-restart", dest="restart", action="store_false",
                            help="No reiniciar servicios ni preguntar (omite la confirmación)")
    r.set_defaults(func=cmd_restore)

    g = sub.add_parser("gui", help="Lanzar la interfaz gráfica (GTK4 + libadwaita)")
    g.set_defaults(func=cmd_gui)

    au = sub.add_parser("automatic", help="Modo GUIless: aplicar una paleta usando un mapping existente")
    au_sub = au.add_subparsers(dest="auto_cmd")

    # `automatic apply` — the original behavior. Bare `automatic <palette>` /
    # `automatic --from-image ...` still work: main() injects "apply" when the
    # first token after `automatic` isn't a known subcommand (see _inject_automatic_apply).
    ap = au_sub.add_parser("apply", help="Aplicar una paleta (o generarla desde una imagen) contra el mapping")
    ap.add_argument("palette", nargs="?", default=None,
                    help="Ruta a un CSV (id,#hex,label) o JSON [{hex,label}, ...], o '-' para JSON por stdin. "
                         "Omitir si usás --from-image")
    ap.add_argument("--from-image", help="Generar la paleta desde esta imagen (wallpaper) en vez de pasar una ruta")
    ap.add_argument("--colors", type=int,
                    help="Cantidad de colores a generar con --from-image "
                         "(default: la cantidad de roles distintos que usa el mapping)")
    ap.add_argument("--sample-size", type=int, default=40000, help="Píxeles a samplear con --from-image (default: 40000)")
    ap.add_argument("--mode", choices=["balanced", "contrast", "shading"], default="contrast",
                    help="Cómo elegir secondary/auxN con --from-image (default: contrast, ver 'palette generate --help')")
    ap.add_argument("--my-eyes", action="store_true",
                    help="Saturar los colores generados con --from-image justo antes de aplicarlos")
    _add_my_eyes_generation_args(ap)
    ap.add_argument("--ying-yang", type=_parse_on_off, default=False, metavar="on|off",
                    help="Ying Yang: usar la paleta complementaria con --from-image (tonos rotados 180°). "
                         "'on' u 'off' (default: off)")
    _add_scoring_args(ap, from_image_note=" con --from-image")
    ap.add_argument(
        "--no-weighted-contrast", dest="weighted_contrast", action="store_false", default=True,
        help="Comparar contraste solo contra el cluster más dominante de la imagen con --from-image, "
             "en vez del sistema ponderado contra todos los clusters (default: ponderado, recomendado).",
    )
    _add_shuffle_args(ap, from_image_note=" con --from-image")
    _add_shading_generation_args(ap)
    _add_keep_custom_generation_arg(ap)
    _add_eco_generation_arg(ap)
    _add_hallucinate_generation_arg(ap)
    _add_regenerate_arg(ap)
    ap.add_argument("--mapping", help="default: mappings/mapping.csv, el mapping canónico")
    ap.add_argument("--test", action="store_true", help="Simular, no modificar archivos")
    ap.add_argument("--force", action="store_true",
                    help="Aplicar aunque haya conflictos de caso 1/convergencia "
                         "(no salta el bloqueo por paleta insuficiente ni el de colores sobrantes)")
    ap.add_argument("--yolo", action="store_true",
                    help="Aplicar aunque sobren colores en la paleta (los de más quedan sin usar). "
                         "Separado de --force, que es solo para conflictos.")
    ap.set_defaults(func=cmd_automatic)

    # `automatic shift` — re-tweak the currently-applied palette's modifiers and
    # re-apply, without re-passing the image/flags. Reads provenance from the
    # palette's #ucs-meta (see palette_shift). Kept only for backward
    # compatibility (e.g. existing wallpaper-switch hooks) -- `palette shift`
    # is the preferred entry point now, with --apply opt-in instead of implied.
    # Same cmd_palette_shift, same _add_shift_args -- the two can't drift
    # apart -- just with apply forced on via set_defaults (no --apply flag
    # shown here, since automatic's whole point was always "shift AND apply").
    sh = au_sub.add_parser("shift", help="Cambiar modificadores de la paleta actual y reaplicar (sin repetir la imagen)")
    sh.add_argument("palette", nargs="?", default=None,
                    help="Paleta a shiftear (default: la que aplica el mapping actual, vía #new_palette=)")
    _add_shift_args(sh)
    sh.add_argument("--mapping", help="default: mappings/mapping.csv, el mapping canónico")
    sh.add_argument("--test", action="store_true", help="Simular: no reescribe la paleta ni toca archivos")
    sh.add_argument("--force", action="store_true", help="Aplicar aunque haya conflictos de caso 1/convergencia")
    sh.add_argument("--yolo", action="store_true", help="Aplicar aunque sobren colores en la paleta")
    sh.set_defaults(func=cmd_palette_shift, apply=True)

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
