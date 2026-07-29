# Roadmap — Color Switcher (Python backend + GTK4/libadwaita GUI)

Este documento guía la migración del sistema bash actual (`color-switching/`, ver `ARCHITECTURE.md`)
a una aplicación Python con backend aislado y GUI en GTK4 + libadwaita + Blueprint.

## Decisiones de arquitectura confirmadas

1. **Base de código**: se reutiliza y extiende el backend ya escrito en `color-gui/`
   (`color_detector.py`, `color_replacer.py`, `state_manager.py`). Se eliminan `gui/wizard.py`
   y `gui/main_window.py` (tkinter) — quedan solo como referencia de lo que la nueva GUI debe cubrir,
   no se migran directamente.
2. **Persistencia**: CSV compatible con el sistema bash. Se siguen usando
   `palettes/detected/*.csv`, `palettes/created/*.csv` y `mappings/*.csv` con los formatos definidos
   en `ARCHITECTURE.md` (`id,hex,label` para paletas; header `#old_palette=`/`#new_palette=` +
   `old_id,new_id` para mappings). Esto permite que la GUI y los scripts bash operen sobre los
   mismos archivos sin migración de datos.
3. **blueprint-compiler**: no está instalado en el sistema — lo instala el usuario
   (`sudo pacman -S blueprint-compiler`) antes de empezar la Fase 3. Los `.blp` se escriben igual
   mientras tanto; solo no se podrán compilar/probar hasta que esté disponible.
4. **Modo "GUIless" (orden de emparejamiento) — SUPERSEDIDA**: esta decisión decía que, cuando
   sobran/faltan colores entre el JSON de paleta recibido y el mapping guardado, se empareja en
   orden de inserción del mapping (el orden en que el usuario fue construyendo el mapping en la
   GUI), no por el valor numérico de `new_id`. Quedó reemplazada por el rework de mappings
   persistentes por paleta (ver notas de esa sesión): ahora TODO camino de apply (GUI, `apply`/
   `test`, `automatic`/`guiless.apply_palette`, `palette shift --apply`) usa un único resolver
   (`mapping_store.resolve_apply_targets`) que compacta por **valor numérico ascendente** de
   `new_id`, nunca por orden de inserción — necesario para que `new_id` signifique lo mismo
   (una posición literal en la paleta) en todos los caminos, no una "etiqueta de rol" opaca solo
   en `automatic`.

## Estructura de carpetas propuesta

```
color-switching/
├── switch-colors.sh          # CLI bash (sin tocar)
├── scripts/                  # scripts bash (sin tocar)
├── palettes/{detected,created}/
├── mappings/
├── ARCHITECTURE.md
├── ROADMAP.md                 # este archivo
└── color-gui/
    ├── backend/                       # lógica pura, sin dependencias de GUI
    │   ├── color_detector.py          # ya existe, se extiende (Fase 1.1)
    │   ├── color_replacer.py          # ya existe, se ajusta backup/restore (Fase 1.2)
    │   ├── palette_store.py           # NUEVO — leer/crear/importar/exportar paletas CSV
    │   ├── mapping_store.py           # NUEVO — leer/crear/actualizar mappings CSV con orden de inserción
    │   ├── detect_diff.py             # NUEVO — comparación detect actual vs guardado (rutas a/b/c)
    │   ├── guiless.py                 # NUEVO — apply automático desde JSON de paleta
    │   └── conflicts.py               # NUEVO — checks de colisión (caso 1) y formatos duplicados (caso 2)
    ├── gui/                            # GTK4 + libadwaita + Blueprint
    │   ├── main.py                     # Adw.Application entrypoint
    │   ├── blueprints/*.blp            # markup de widgets
    │   ├── window_main.py              # pantalla de mapping (layout del wireframe)
    │   ├── dialog_welcome.py           # popup inicial (ruta a)
    │   ├── dialog_stale_detect.py      # warning de colores cambiados (ruta c)
    │   └── palette_dialogs.py          # crear/importar paleta
    └── main.py                         # entrypoint CLI: gui | automatic <palette.json> | restore
```

## Fase 1 — Backend puro (sin GUI)

Objetivo: toda la lógica funcional, testeable desde terminal/REPL, sin ninguna dependencia gráfica.

### 1.1 `color_detector.py` (extender)
- [ ] Reescribir `detect_colors()` para que además de la lista plana, exponga una vista agrupada
  por valor hex real, con sub-entradas por formato (`hex`, `hex_from_rgb`) — necesario para el
  caso 2 (mismo color en distintos formatos). La lista plana actual (con IDs por `(type,color)`)
  se mantiene para compatibilidad con el CSV; la vista agrupada es una capa encima.
- [ ] Escribir/leer `palettes/detected/<timestamp o "latest">.csv` en vez de un único archivo,
  para poder comparar detect actual vs guardado (ver `detect_diff.py`).

### 1.2 `color_replacer.py` (ajustar)
- [ ] Unificar el layout de backup con `scripts/backup_files.sh`/`restore_files.sh` para que
  `restore` funcione igual sin importar si el backup lo generó bash o Python.
- [ ] Confirmar que `apply_mapping()` corta la operación completa (no aplica nada) si
  `conflicts.py` detecta un caso 1 sin confirmación explícita del usuario.

### 1.3 `mapping_store.py` (nuevo)
- [ ] Estructura en memoria: lista ordenada de `{old_id, new_id | None}` que preserva el
  **orden de inserción** (no un dict por `old_id`), para que `guiless.py` pueda emparejar
  en ese orden.
- [ ] Guardado incremental / en tiempo real: cada vez que la GUI añade una entrada al mapping,
  se persiste a disco inmediatamente (requisito del prompt original: "la UI debería guardar en
  tiempo real").
- [ ] Placeholder explícito para "color aún no asignado" (el prompt original lo pide) — usar
  `new_id = null` en el CSV/JSON intermedio, no un string mágico.
- [ ] Soporte para la opción del caso 2: marcar dos entradas (hex y rgb del mismo valor) como
  "vinculadas" para que compartan el mismo `new_id` automáticamente.

### 1.4 `detect_diff.py` (nuevo) — Rutas a/b/c del flujo de inicio
- [ ] Ruta a: no hay detección previa → señal para que la GUI muestre el popup de bienvenida.
- [ ] Ruta b: hay detección previa y coincide con un detect nuevo → sin warning, directo a mapping.
- [ ] Ruta c: hay detección previa y difiere → devolver el diff (colores nuevos/desaparecidos)
  para que la GUI arme el mensaje de warning.

### 1.5 `conflicts.py` (nuevo)
- [ ] Caso 1: mapear a un color de la paleta nueva que ya existe en la paleta detectada.
  Debe poder evaluarse "en vivo" (cada vez que cambia el mapping), no solo al aplicar.
- [ ] Caso 2: detectar pares hex/rgb del mismo valor en la paleta detectada, para que la GUI
  ofrezca la opción "mapear al mismo color que formato hex".

### 1.6 `guiless.py` (nuevo)
- [ ] `apply_from_palette_json(palette_json, mapping_path)`:
  carga el mapping guardado, recorre en **orden de inserción**, asigna secuencialmente los
  colores del JSON recibido a cada entrada del mapping.
- [ ] Si sobran colores del JSON o del mapping, no aplicar automáticamente — devolver el resumen
  y pedir confirmación (según el prompt original).
- [ ] Reutiliza `conflicts.py` (caso 1) antes de aplicar; si hay conflicto, no aplica.

### 1.7 Limpieza
- [ ] Eliminar `color-gui/gui/wizard.py` y `color-gui/gui/main_window.py` (tkinter) una vez el
  backend nuevo cubra lo que ellos hacían.

**Entregable de la Fase 1**: se puede correr todo el flujo (detect → mapping → conflictos →
apply/test → restore → modo guiless) desde una CLI mínima o un REPL, sin ninguna ventana.

## Fase 2 — Tests del backend

- [ ] Tests con tempdirs simulando archivos de config reales (mismo criterio que
  "Refactor Proposals" de `ARCHITECTURE.md`).
- [ ] Caso 1 (colisión) y caso 2 (hex/rgb mismo valor) cubiertos explícitamente.
- [ ] Test de `guiless.py` con paleta JSON con más/menos colores que el mapping.
- [ ] Test de restore contra backups generados tanto por bash como por Python.

## Fase 3 — GUI en GTK4 + libadwaita + Blueprint

**Prerrequisito**: `blueprint-compiler` instalado por el usuario.

- [ ] Esqueleto `Adw.Application` + ventana principal.
- [ ] Popups de inicio según `detect_diff.py` (rutas a/b/c).
- [ ] Pantalla principal de mapping, layout según el wireframe (`~/Documents/ejemplo_ui_super_basico.jpg`):
  - Dos columnas (colores a reemplazar | paleta nueva), separador central.
  - Arriba de cada columna: lista scrolleable de colores disponibles (se van sacando de acá
    cuando se asignan al mapping).
  - Al medio: lista de mapping con entradas desplegables (muestran archivos afectados al expandir),
    número de orden superpuesto al swatch de color vía `Gtk.Overlay`.
  - Selector para elegir color desde el menú de disponibles (fallback si no se hace drag & drop).
  - Caja de warnings visible en tiempo real (bind a `conflicts.py`).
  - Botones Apply / Restore / etc.
- [ ] Menú de paleta: crear color nuevo / importar CSV.
- [ ] Opción "mapear al mismo color que formato hex" cuando aplica (caso 2).
- [ ] Drag & drop de columnas de disponibles → mapping (best-effort); si resulta muy costoso,
  queda solo el selector desplegable como vía principal (aceptado por el prompt original).
- [ ] Guardado en tiempo real conectado a `mapping_store.py` (cada cambio en la UI persiste).

## Fase 4 — Integración final

- [ ] `color-gui/main.py` como entrypoint único: `gui`, `automatic <palette.json>`, `restore`.
- [ ] Confirmar que conviven sin pisarse los archivos usados por `switch-colors.sh` (bash) y por
  la app nueva — mismo `config.json`, mismos directorios de `palettes/`/`mappings/`.
- [ ] Documentar en `ARCHITECTURE.md` (o un README nuevo) cómo se relacionan ambos sistemas.

## Pendientes / a confirmar más adelante

- Nombre final del comando/alias para lanzar la GUI (¿entry en `switch-colors.sh gui`, o binario
  aparte?).
- Si el drag & drop en Fase 3 resulta demasiado complejo con GTK4, confirmar que el selector
  desplegable como único mecanismo es aceptable de forma permanente (no solo como fallback temporal).
