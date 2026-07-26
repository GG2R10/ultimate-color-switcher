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


def _print_palette(entries, show_id: bool = True) -> None:
    """Print a palette one color per line, each led by its swatch (falls back
    to just hex + label when color is off)."""
    for e in entries:
        swatch = _color_swatch(e["hex"])
        prefix = f"  {e['id']}) " if show_id else "  "
        cells = [swatch, f"#{e['hex']}", e.get("label", "")]
        print((prefix + "  ".join(c for c in cells if c)).rstrip())


def cmd_palette_create(args, config):
    path = args.path
    if not os.path.isabs(path):
        path = os.path.join(config.palettes_created_dir, path)
    entries = []
    for hexval, label in (args.add or []):
        next_id = max((e["id"] for e in entries), default=0) + 1
        entries.append({"id": next_id, "hex": hexval.lstrip("#").lower(), "label": label})
    palette_store.write_palette_csv(path, entries)
    print(f"Paleta creada: {path} ({len(entries)} colores)")
    _print_palette(entries)
    _maybe_apply_after_edit(args, config, path)


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
    _maybe_apply_after_edit(args, config, resolved, mapping_path=args.mapping)


def cmd_palette_add_color(args, config):
    path = _resolve_target_palette(args.path, config, mapping_path=args.mapping)
    entry = palette_shift.add_color(path, args.hex, args.label or "")
    swatch = _color_swatch(entry["hex"])
    cells = ["Agregado:", swatch, f"#{entry['hex']}", entry.get("label", "")]
    print(" ".join(c for c in cells if c))
    print("Paleta actual:")
    _print_palette(palette_store.read_palette_csv(path))
    _maybe_apply_after_edit(args, config, path, mapping_path=args.mapping)


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
    mapping_path = mapping_path or config.mapping_csv
    _old, new_p, _entries = mapping_store.read_mapping_csv(mapping_path, project_dir=config.project_dir)
    if not new_p:
        raise palette_shift.PaletteEditError(
            "No se indicó paleta y el mapping no referencia ninguna (#new_palette=). "
            "Pasá la ruta de la paleta explícitamente."
        )
    return new_p


def _adjust_mapping_after_palette_delete(config, mapping_path, palette_path, deleted_id):
    """If the palette a color was just deleted from is the one `mapping_path`
    applies, unassign entries that pointed at it and shift the rest, so the
    mapping stays aligned (see mapping_store.drop_and_shift_new_id)."""
    old_p, new_p, entries = mapping_store.read_mapping_csv(mapping_path, project_dir=config.project_dir)
    if not entries or not new_p:
        return
    same = (os.path.abspath(color_detector.expand_path(new_p))
            == os.path.abspath(color_detector.expand_path(palette_path)))
    if not same:
        return
    adjusted = mapping_store.drop_and_shift_new_id(entries, deleted_id)
    dropped = sum(1 for e, a in zip(entries, adjusted)
                  if e["new_id"] is not None and a["new_id"] is None)
    mapping_store.write_mapping_csv(mapping_path, old_p, new_p, adjusted, project_dir=config.project_dir)
    if dropped:
        print(f"⚠ {dropped} asignación(es) del mapping apuntaban a ese color y quedaron sin asignar.")


def cmd_palette_edit(args, config):
    mapping_path = args.mapping or config.mapping_csv
    path = _resolve_target_palette(args.palette, config, mapping_path=mapping_path)
    palette_shift.edit_color(path, args.target, args.new_hex)
    print(f"Editado en {path}:")
    _print_palette(palette_store.read_palette_csv(path))
    _maybe_apply_after_edit(args, config, path, mapping_path=mapping_path)


def cmd_palette_remove(args, config):
    mapping_path = args.mapping or config.mapping_csv
    path = _resolve_target_palette(args.palette, config, mapping_path=mapping_path)
    deleted_id = palette_shift.delete_color(path, args.target)
    _adjust_mapping_after_palette_delete(config, mapping_path, path, deleted_id)
    print(f"Borrado el color {deleted_id} de {path}:")
    _print_palette(palette_store.read_palette_csv(path))
    _maybe_apply_after_edit(args, config, path, mapping_path=mapping_path)


def _parse_on_off(raw: str) -> bool:
    """--ying-yang on|off -- argparse type= callable (ArgumentTypeError for a
    clean CLI error instead of a traceback)."""
    v = raw.strip().lower()
    if v in ("on", "true", "1", "yes", "si", "sí"):
        return True
    if v in ("off", "false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError(f"valor debe ser 'on' u 'off', se recibió {raw!r}")


def _generate_and_save_palette(config, image, n_colors, sample_size, mode, saturate,
                                out_path=None, scoring="default", custom_scoring_values=None,
                                weighted_contrast=True, shuffle=None, overfetch=0, ying_yang=False,
                                my_eyes_factor=palette_generator._MY_EYES_CHROMA_FACTOR,
                                my_eyes_max_chroma=palette_generator._MY_EYES_CHROMA_MAX,
                                shading_direction="dark", shading_min_luminance=8.0,
                                shading_max_luminance=92.0):
    """Shared by `palette generate` and `automatic --from-image`. Returns
    (entries, saved_path) — entries is the id/hex/label list ready to hand
    to guiless.apply_palette directly (no need to re-read the file back).

    shuffle=None means "--shuffle wasn't passed at all" -- distinct from an
    explicit 0, since resolving it (and persisting it as the new
    "last_shuffle" anchor for a future --shuffle next) only happens when
    the flag was actually used.

    my_eyes_factor/my_eyes_max_chroma: only used when saturate=True -- see
    palette_generator._boost_saturation.

    shading_direction/shading_min_luminance/shading_max_luminance: only used
    when mode="shading" -- see palette_generator.generate_shading_series."""
    weights = palette_generator.resolve_scoring_weights(
        scoring, custom_percentages=custom_scoring_values, config=config,
    )
    resolved_shuffle = (
        palette_generator.resolve_shuffle_index(shuffle, n_colors, overfetch=overfetch, config=config)
        if shuffle is not None else 0
    )
    colors, base_colors = palette_generator.generate_palette(
        image, n_colors=n_colors, sample_size=sample_size, mode=mode,
        saturate=saturate, weights=weights, weighted_contrast=weighted_contrast,
        shuffle=resolved_shuffle, overfetch=overfetch, ying_yang=ying_yang,
        saturate_factor=my_eyes_factor, saturate_max_chroma=my_eyes_max_chroma,
        shading_direction=shading_direction, shading_min_l=shading_min_luminance,
        shading_max_l=shading_max_luminance, with_base=True,
    )

    if not out_path:
        out_path = config.generated_palette_csv
    if not out_path.endswith(".csv"):
        out_path += ".csv"
    if not os.path.isabs(out_path):
        out_path = os.path.join(config.palettes_created_dir, out_path)

    # Rows are the EFFECTIVE (applied) colors; the pre-post-mod base + the
    # generation params ride along in #ucs-meta so `shift` can re-tweak & re-apply
    # this palette without re-passing the image or the whole flag set.
    entries = [{"id": i + 1, "hex": c["hex"], "label": c["label"], "origin": "gen"}
               for i, c in enumerate(colors)]
    meta = palette_store.default_meta()
    meta.update({
        "generated": True,
        "image": to_home_relative(image),
        "gen": {
            "colors": n_colors,
            "sample_size": sample_size,
            "mode": mode,
            "scoring": scoring,
            "custom_percentages": custom_scoring_values,
            "weighted_contrast": weighted_contrast,
            "shuffle": resolved_shuffle,
            "overfetch": overfetch,
            "shading_direction": shading_direction,
            "shading_min_luminance": shading_min_luminance,
            "shading_max_luminance": shading_max_luminance,
        },
        "post": {"my_eyes": bool(saturate), "ying_yang": bool(ying_yang),
                 "my_eyes_factor": my_eyes_factor, "my_eyes_max_chroma": my_eyes_max_chroma},
        "base": [{"hex": c["hex"], "label": c["label"], "origin": "gen"} for c in base_colors],
    })
    palette_store.write_palette_csv(out_path, entries, meta=meta)
    return entries, out_path


def cmd_palette_generate(args, config):
    mapping_path = args.mapping or config.mapping_csv
    n_colors = args.colors
    if n_colors is None:
        # No --colors given -- infer it from how many distinct roles the
        # mapping actually needs, same convenience `automatic --from-image`
        # already had, so you don't have to know/remember that number.
        _old_p, _new_p, mapping_entries = mapping_store.read_mapping_csv(mapping_path, project_dir=config.project_dir)
        n_colors = len({e["new_id"] for e in mapping_entries}) if mapping_entries else 6

    entries, out_path = _generate_and_save_palette(
        config, args.image, n_colors, args.sample_size, args.mode, args.my_eyes, args.out,
        scoring=args.scoring, custom_scoring_values=args.custom_scoring_values,
        weighted_contrast=args.weighted_contrast, shuffle=args.shuffle, overfetch=args.overfetch,
        ying_yang=args.ying_yang, my_eyes_factor=args.my_eyes_factor, my_eyes_max_chroma=args.my_eyes_max_chroma,
        shading_direction=args.shading_direction,
        shading_min_luminance=args.shading_min_luminance, shading_max_luminance=args.shading_max_luminance,
    )

    print(f"Paleta generada desde {args.image} ({n_colors} color(es)):")
    _print_palette(entries)
    print(f"\nGuardada en: {out_path}")
    if not args.apply:
        print(f"Para aplicarla: ucs palette show {out_path} --apply")
    # entries is already the in-memory, just-computed list -- guiless accepts
    # it directly, no need to round-trip it back through the file we just wrote.
    _maybe_apply_after_edit(args, config, entries, mapping_path=mapping_path)


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

    out_path = args.out
    if not out_path:
        out_path = config.mapping_csv
    else:
        if not out_path.endswith(".csv"):
            out_path += ".csv"
        if not os.path.isabs(out_path):
            out_path = os.path.join(config.mappings_dir, out_path)
    store = mapping_store.MappingStore(
        out_path, old_palette=detected_path, new_palette=target_palette, project_dir=config.project_dir
    )

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

    print(f"\nIngresá pares 'old_id new_id' (ENTER vacío para terminar). Mapping: {out_path}")
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

    print(f"\nMapping guardado: {out_path} ({len(store.resolved_entries())} entradas)")


def cmd_mapping_show(args, config):
    old_p, new_p, entries = mapping_store.read_mapping_csv(args.path, project_dir=config.project_dir)
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


def _apply_or_test(args, config, mode):
    mapping_path = args.mapping or config.mapping_csv
    old_p, new_p, entries = mapping_store.read_mapping_csv(mapping_path, project_dir=config.project_dir)
    if not entries:
        print(f"Mapping vacío o no encontrado: {mapping_path}")
        sys.exit(1)

    detected_path = old_p or config.detected_palette_csv
    detected_colors = color_detector.read_detected_csv(detected_path)
    new_palette = palette_store.read_palette_csv(new_p) if new_p else []

    # A new_id the mapping references but the palette doesn't have (stale
    # after the palette was edited/regenerated with fewer/renumbered colors,
    # a missing #new_palette= file, or plain hand-edited corruption) must be
    # a hard error here -- unlike guiless.apply_palette's positional
    # compaction, this path matches new_id directly against the palette's own
    # ids, so silently ignoring the mismatch would either drop that
    # replacement outright or (if the palette file is simply missing) report
    # a misleadingly "successful" 0-replacements run.
    missing_new_ids = sorted({e["new_id"] for e in entries} - {p["id"] for p in new_palette})
    if missing_new_ids:
        print(f"⚠ El mapping referencia color(es) de paleta que no existen: id(s) {missing_new_ids}. "
              f"La paleta {new_p or '(no definida)'} tiene {len(new_palette)} color(es). "
              "Revisá el mapping ('ucs mapping show <mapping>') o regenerá/reimportá la paleta.")
        sys.exit(1)

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

    mapping_path = args.mapping or config.mapping_csv
    palette_source = args.palette
    if args.from_image:
        _old_p, _new_p, entries = mapping_store.read_mapping_csv(mapping_path, project_dir=config.project_dir)
        if not entries:
            print(f"Mapping vacío o no encontrado: {mapping_path}")
            sys.exit(1)
        n_colors = args.colors or len({e["new_id"] for e in entries})
        palette_source, saved_path = _generate_and_save_palette(
            config, args.from_image, n_colors, args.sample_size, args.mode, args.my_eyes,
            scoring=args.scoring, custom_scoring_values=args.custom_scoring_values,
            weighted_contrast=args.weighted_contrast, shuffle=args.shuffle, overfetch=args.overfetch,
            ying_yang=args.ying_yang, my_eyes_factor=args.my_eyes_factor, my_eyes_max_chroma=args.my_eyes_max_chroma,
            shading_direction=args.shading_direction,
            shading_min_luminance=args.shading_min_luminance, shading_max_luminance=args.shading_max_luminance,
        )
        print(f"Paleta generada desde {args.from_image} ({n_colors} color(es)) — guardada en: {saved_path}")
        _print_palette(palette_source)

    result = guiless.apply_palette(
        palette_source,
        mapping_path,
        config.backup_dir,
        dry_run=args.test,
        force=args.force,
        yolo=args.yolo,
        project_dir=config.project_dir,
    )
    _report_apply_result(result, args, config, mapping_path)


def _report_apply_result(result, args, config, mapping_path):
    """Shared by `automatic` and `automatic shift`: turn a guiless.apply_palette
    result into CLI output (and the post-apply detect refresh / restarts on a
    real apply). Exits 1 on any non-applied status."""
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
        print(f"Mapping vacío o no encontrado: {mapping_path}")
        sys.exit(1)
    else:
        results = result["results"]
        total = sum(r.get("count", 0) for r in results)
        label = "Simulación (test)" if args.test else "Aplicado"
        print(f"{label}: {total} reemplazos en {len(results)} operaciones de archivo.")
        if result.get("stale_mapping_warning"):
            print("⚠ Se forzó un conflicto/convergencia: el mapping usado probablemente quedó desactualizado "
                  "(algunos ids ya no representan el mismo color real). Conviene rehacerlo antes de reusarlo.")
        if not args.test:
            print(f"Backup en: {config.backup_dir}")
            _refresh_detected_after_change(config)
            started = restart_actions.run_enabled(restart_actions.read_restart_actions(config))
            for a in started:
                print(f"  Reiniciando: {a['label']}" + ("" if a["started"] else f" (error: {a['error']})"))


def _maybe_apply_after_edit(args, config, palette_source, mapping_path=None):
    """Shared tail for every palette-mutating command's `--apply`: reuses the
    exact same guiless.apply_palette + _report_apply_result pipeline
    `automatic`/`automatic shift` already use, so "mutate a palette, then
    apply it" never needs its own bespoke apply logic. No-op if --apply
    wasn't passed. `palette_source` is a path OR an already-loaded list
    (guiless.load_palette accepts either) -- `palette generate --apply`
    passes the just-computed in-memory entries directly, no file round-trip."""
    if not getattr(args, "apply", False):
        return
    mapping_path = mapping_path or args.mapping or config.mapping_csv
    result = guiless.apply_palette(
        palette_source, mapping_path, config.backup_dir,
        dry_run=args.test, force=args.force, yolo=args.yolo, project_dir=config.project_dir,
    )
    _report_apply_result(result, args, config, mapping_path)


def cmd_palette_shift(args, config):
    """`palette shift` (--apply defaults False) and the back-compat `automatic
    shift` (which forces apply=True via set_defaults, no --apply flag shown)
    both point here -- one function, no duplicated shift-then-apply logic."""
    mapping_path = args.mapping or config.mapping_csv
    palette_path = _resolve_target_palette(args.palette, config, mapping_path=mapping_path)

    weighted_contrast = None if args.weighted_contrast is None else (args.weighted_contrast == "on")
    result = palette_shift.shift_palette(
        palette_path, config,
        my_eyes=args.my_eyes, ying_yang=args.ying_yang,
        my_eyes_factor=args.my_eyes_factor, my_eyes_max_chroma=args.my_eyes_max_chroma,
        mode=args.mode, scoring=args.scoring, custom_scoring_values=args.custom_scoring_values,
        weighted_contrast=weighted_contrast, shuffle=args.shuffle, overfetch=args.overfetch,
        colors=args.colors, shading_direction=args.shading_direction,
        shading_min_luminance=args.shading_min_luminance, shading_max_luminance=args.shading_max_luminance,
        write=not args.test,
    )
    for w in result["warnings"]:
        print(f"⚠ {w}")
    print(f"Paleta {'regenerada' if result['regenerated'] else 'ajustada'}"
          f"{' (simulación, no se guardó)' if args.test else ''}: {palette_path}")
    _print_palette(result["entries"])

    palette_source = [{"hex": e["hex"], "label": e.get("label", "")} for e in result["entries"]]
    _maybe_apply_after_edit(args, config, palette_source, mapping_path=mapping_path)


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
             "Le da a --shuffle más margen para saltear sin quedarse sin candidatos.",
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
    pc.add_argument("--add", nargs=2, metavar=("HEX", "LABEL"), action="append",
                     help="Agregar un color (puede repetirse)")
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
    _add_apply_args(pa)
    pa.set_defaults(func=cmd_palette_add_color)

    ped = psub.add_parser("edit", help="Cambiar un color de una paleta por otro")
    ped.add_argument("palette", nargs="?", default=None,
                     help="Paleta a editar (default: la que aplica el mapping actual, vía #new_palette=)")
    ped.add_argument("target", help="Color a cambiar: su id o su hex")
    ped.add_argument("new_hex", metavar="new-hex", help="Nuevo color (hex)")
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
    pg.add_argument("--out", help="Ruta de salida (default: palettes/created/generated.csv, se reemplaza en cada corrida)")
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
    mn.add_argument("--out", help="Ruta de salida del mapping (default: mappings/mapping.csv, el mapping canónico)")
    mn.set_defaults(func=cmd_mapping_new)

    ms = msub.add_parser("show", help="Mostrar un mapping y sus conflictos")
    ms.add_argument("path")
    ms.set_defaults(func=cmd_mapping_show)

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
