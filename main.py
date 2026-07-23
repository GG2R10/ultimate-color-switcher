#!/usr/bin/env python3
"""
main.py — Color Switcher CLI: detect -> mapping -> conflicts -> apply/test
-> restore -> automatic, plus `gui` to launch the GTK4 + libadwaita window.

Examples:
  main.py detect
  main.py palette create palettes/created/my-theme.csv \\
      --add ff00aa primary --add 00ccff secondary
  main.py mapping new palettes/created/my-theme.csv
  main.py mapping show mappings/mapping.csv
  main.py test  --mapping mappings/mapping.csv
  main.py apply --mapping mappings/mapping.csv
  main.py restore
  main.py automatic palette.csv --mapping mappings/mapping.csv
  main.py gui
"""

import argparse
import datetime
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(SCRIPT_DIR))  # so "app.backend" style imports work if ever needed
sys.path.insert(0, SCRIPT_DIR)

from backend import (  # noqa: E402
    color_detector,
    color_replacer,
    conflicts,
    detect_diff,
    guiless,
    mapping_store,
    palette_generator,
    palette_store,
    restart_actions,
)
from backend.config import load_config, read_files_to_replace, to_home_relative, write_files_to_replace  # noqa: E402


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
    print("Corré 'main.py detect' para actualizar los colores detectados.")


def cmd_config_files_remove(args, config):
    files = read_files_to_replace(config)
    target_expanded = color_detector.expand_path(args.path)
    remaining = [f for f in files if f != args.path and color_detector.expand_path(f) != target_expanded]
    if len(remaining) == len(files):
        print(f"No estaba en la lista: {args.path}")
        return
    write_files_to_replace(config, remaining)
    print(f"Eliminado: {args.path}")
    print("Corré 'main.py detect' para actualizar los colores detectados.")


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


def cmd_palette_list(args, config):
    for p in palette_store.list_palettes(config.palettes_created_dir):
        entries = palette_store.read_palette_csv(p)
        print(f"{p} ({len(entries)} colores)")


def cmd_palette_add_color(args, config):
    entry = palette_store.add_color(args.path, args.hex, args.label or "")
    print(f"Agregado: id {entry['id']} #{entry['hex']} {entry['label']}")


def _generate_and_save_palette(config, image, n_colors, sample_size, background, mode, saturate,
                                out_path=None):
    """Shared by `palette generate` and `automatic --from-image`. Returns
    (entries, saved_path) — entries is the id/hex/label list ready to hand
    to guiless.apply_palette directly (no need to re-read the file back)."""
    colors = palette_generator.generate_palette(
        image, n_colors=n_colors, sample_size=sample_size, background_hex=background, mode=mode,
        saturate=saturate,
    )

    if not out_path:
        base = os.path.splitext(os.path.basename(image))[0]
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = f"generated-{base}-{ts}.csv"
    if not out_path.endswith(".csv"):
        out_path += ".csv"
    if not os.path.isabs(out_path):
        out_path = os.path.join(config.palettes_created_dir, out_path)

    entries = [{"id": i + 1, "hex": c["hex"], "label": c["label"]} for i, c in enumerate(colors)]
    palette_store.write_palette_csv(out_path, entries)
    return entries, out_path


def cmd_palette_generate(args, config):
    entries, out_path = _generate_and_save_palette(
        config, args.image, args.colors, args.sample_size, args.background, args.mode,
        args.my_eyes, args.out,
    )

    print(f"Paleta generada desde {args.image}:")
    for e in entries:
        print(f"  {e['id']}) #{e['hex']}  {e['label']}")
    print(f"\nGuardada en: {out_path}")
    print(f"Para aplicarla: main.py automatic {out_path} --mapping <mapping.csv>")


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
        print("Para deshacer: main.py restore")
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
    from gui.app import main as gui_main
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
            config, args.from_image, n_colors, args.sample_size, args.background, args.mode, args.my_eyes
        )
        print(f"Paleta generada desde {args.from_image} ({n_colors} color(es)) — guardada en: {saved_path}")

    result = guiless.apply_palette(
        palette_source,
        mapping_path,
        config.backup_dir,
        dry_run=args.test,
        force=args.force,
        project_dir=config.project_dir,
    )
    status = result["status"]
    if status == "insufficient_palette":
        print(f"La paleta nueva tiene {result['available']} color(es), pero el mapping necesita "
              f"{result['needed']} (roles distintos usados). Generá una paleta con al menos "
              f"{result['needed']} colores (--colors {result['needed']}).")
        sys.exit(1)
    elif status == "needs_confirmation":
        print(f"La paleta nueva tiene {result['surplus_palette_count']} color(es) de más que no se van a usar.")
        print("Volvé a correr con --force para aplicar de todas formas.")
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


def build_parser():
    parser = argparse.ArgumentParser(description="Color Switcher — CLI")
    sub = parser.add_subparsers(dest="command", required=True)

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

    pp = sub.add_parser("palette", help="Gestión de paletas creadas")
    psub = pp.add_subparsers(dest="palette_command", required=True)

    pc = psub.add_parser("create", help="Crear una paleta CSV")
    pc.add_argument("path", help="Ruta de salida (relativa a palettes/created/ si no es absoluta)")
    pc.add_argument("--add", nargs=2, metavar=("HEX", "LABEL"), action="append",
                     help="Agregar un color (puede repetirse)")
    pc.set_defaults(func=cmd_palette_create)

    pl = psub.add_parser("list", help="Listar paletas creadas")
    pl.set_defaults(func=cmd_palette_list)

    pa = psub.add_parser("add-color", help="Agregar un color a una paleta existente")
    pa.add_argument("path")
    pa.add_argument("hex")
    pa.add_argument("label", nargs="?", default="")
    pa.set_defaults(func=cmd_palette_add_color)

    pg = psub.add_parser("generate", help="Generar una paleta a partir de una imagen (wallpaper)")
    pg.add_argument("image", help="Ruta a la imagen")
    pg.add_argument("--colors", type=int, default=6, help="Cantidad de colores a generar (default: 6)")
    pg.add_argument("--sample-size", type=int, default=40000, help="Píxeles a samplear (default: 40000)")
    pg.add_argument("--background", help="Hex de fondo para validar contraste (opcional)")
    pg.add_argument("--mode", choices=["balanced", "contrast", "shading"], default="contrast",
                     help="Cómo elegir secondary/auxN (default: contrast). 'balanced': secondary por score, "
                          "sin sesgo respecto al contraste con primary. 'contrast': secondary maximiza contraste "
                          "con primary (comportamiento original). 'shading': el resto de la paleta son variantes "
                          "monocromáticas (mismo tono) de primary")
    pg.add_argument("--my-eyes", action="store_true",
                     help="Saturar los colores elegidos justo antes de guardarlos")
    pg.add_argument("--out", help="Ruta de salida (relativa a palettes/created/ si no es absoluta)")
    pg.set_defaults(func=cmd_palette_generate)

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
    au.add_argument("palette", nargs="?", default=None,
                     help="Ruta a un CSV (id,#hex,label) o JSON [{hex,label}, ...], o '-' para JSON por stdin. "
                          "Omitir si usás --from-image")
    au.add_argument("--from-image", help="Generar la paleta desde esta imagen (wallpaper) en vez de pasar una ruta")
    au.add_argument("--colors", type=int,
                     help="Cantidad de colores a generar con --from-image "
                          "(default: la cantidad de roles distintos que usa el mapping)")
    au.add_argument("--sample-size", type=int, default=40000, help="Píxeles a samplear con --from-image (default: 40000)")
    au.add_argument("--background", help="Hex de fondo para validar contraste con --from-image (opcional)")
    au.add_argument("--mode", choices=["balanced", "contrast", "shading"], default="contrast",
                     help="Cómo elegir secondary/auxN con --from-image (default: contrast, ver 'palette generate --help')")
    au.add_argument("--my-eyes", action="store_true",
                     help="Saturar los colores generados con --from-image justo antes de aplicarlos")
    au.add_argument("--mapping", help="default: mappings/mapping.csv, el mapping canónico")
    au.add_argument("--test", action="store_true", help="Simular, no modificar archivos")
    au.add_argument("--force", action="store_true",
                     help="Aplicar aunque sobren colores o haya conflictos de caso 1/convergencia "
                          "(no salta el bloqueo por paleta insuficiente)")
    au.set_defaults(func=cmd_automatic)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if getattr(args, "command", None) == "automatic" and args.palette == "-":
        args.palette = json.load(sys.stdin)

    config = load_config()
    args.func(args, config)


if __name__ == "__main__":
    main()
