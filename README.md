# Color Switcher (`ucs`)

![License](https://img.shields.io/badge/license-GPL--2.0-blue) ![Python](https://img.shields.io/badge/python-3.10%2B-blue)

Detectá los colores usados en tus dotfiles, armá una paleta nueva —a mano o generada automáticamente desde un wallpaper— y aplicá el reemplazo de forma segura, con backup y un modo de simulación antes de tocar nada de verdad.

Tiene dos formas de usarse sobre el mismo backend: una interfaz gráfica (GTK4 + libadwaita) y una CLI completa para scripts/hooks (por ejemplo, para regenerar el tema cada vez que cambiás de wallpaper).

## Contenidos

- [Instalación](#instalación)
- [Configuración inicial](#configuración-inicial)
- [Uso — GUI](#uso--gui)
- [Uso — CLI, paso a paso](#uso--cli-paso-a-paso)
- [Referencia rápida de comandos](#referencia-rápida-de-comandos)
- [Desarrollo](#desarrollo)

## Instalación

### Arch Linux (AUR)

```bash
paru -S ucs-git   # o yay, o makepkg -si desde packaging/PKGBUILD
```

Esto instala el comando `ucs`, su entrada de menú (`.desktop`) y resuelve automáticamente las dependencias del sistema (GTK4, libadwaita, PyGObject, numpy, Pillow).

### Manual / otras distros

No todo el mundo usa Arch, así que también anda como paquete Python normal. GTK4, libadwaita y PyGObject son librerías del sistema — **no** vienen con pip, hay que instalarlas primero con el gestor de paquetes de tu distro:

```bash
# Arch
sudo pacman -S python-gobject gtk4 libadwaita blueprint-compiler

# Debian/Ubuntu
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 blueprint-compiler

# Fedora
sudo dnf install python3-gobject gtk4 libadwaita blueprint-compiler
```

Después:

```bash
git clone https://github.com/GG2R10/ultimate-color-switcher.git
cd ultimate-color-switcher

# compilar las interfaces GTK una vez (si no tenés blueprint-compiler, se
# compilan solas la primera vez que corrés la GUI, no hace falta este paso)
for blp in color_switcher/gui/blueprints/*.blp; do
    blueprint-compiler compile "$blp" --output "${blp%.blp}.ui"
done

pip install --user .          # o "pip install --user -e ." para desarrollo
```

Esto deja el comando `ucs` disponible (asegurate de que el directorio de scripts de pip de usuario esté en tu `PATH`, normalmente `~/.local/bin`).

## Configuración inicial

Los datos de usuario (config, paletas, mappings) viven en `~/.config/ucs/`, separados del código — se crean solos la primera vez que corrés `ucs`. El archivo central es `~/.config/ucs/config.json`:

```json
{
  "files_to_replace": [
    "$HOME/.config/hypr/hyprlock.conf",
    "$HOME/.config/waybar/colors.css"
  ],
  "backup_dir": "$HOME/.config-colors-backup"
}
```

- `files_to_replace`: los archivos de configuración donde se buscan y reemplazan colores. Se pueden gestionar sin editar el JSON a mano — ver [`ucs config files`](#uso--cli-paso-a-paso).
- `backup_dir`: dónde se guarda una copia de cada archivo antes de tocarlo de verdad.

Las rutas admiten `$HOME`/`~` y se guardan así (no absolutas), para que `config.json` siga siendo válido entre máquinas que comparten estos dotfiles.

## Uso — GUI

```bash
ucs            # o: ucs gui
```

La ventana principal tiene dos columnas: colores detectados (izquierda) y la paleta nueva (derecha). Seleccionás un color detectado, le asignás un color de la paleta, y así armás el mapping. El menú (⋮) tiene:

- **Nueva paleta… / Importar paleta… / Agregar color…** — gestión de la paleta objetivo.
- **Generar paleta desde imagen…** — arma una paleta automáticamente a partir de un wallpaper.
- **Configurar generación de paleta…** — modo de selección (contraste / balanceado / shading) y saturación extra (`--my-eyes`).
- **Vincular hex/rgb automáticamente** — un mismo color que aparece en dos formatos distintos en tus archivos se trata como uno solo por defecto.
- **Archivos a escanear…** — agregar/quitar archivos de `files_to_replace`.
- **Servicios a reiniciar…** — comandos a correr después de un apply real (ej. reiniciar waybar).
- **Guardar snapshot de detección…** — guarda una copia con nombre de la detección actual, para volver a usarla después.

Los botones **Simular** y **Aplicar** hacen lo mismo que `test`/`apply` en la CLI.

## Uso — CLI, paso a paso

Todos los comandos son no-interactivos salvo `mapping new` (que es una sesión guiada) y `restore` (que pregunta si reiniciar servicios, salvo que uses `--restart`/`--no-restart`).

### 1. Elegir qué archivos escanear

```bash
ucs config files list
ucs config files add ~/.config/waybar/colors.css
ucs config files remove '$HOME/.config/waybar/colors.css'
```

### 2. Detectar los colores actuales

```bash
ucs detect
```

Escanea `files_to_replace`, guarda el resultado en `~/.config/ucs/palettes/detected/detected_palette.csv` y te muestra cada color detectado con un `id` — ese `id` es lo que vas a usar para armar el mapping.

### 3. Conseguir una paleta objetivo

Opción A — a mano:

```bash
ucs palette create mi-tema.csv --add ff00aa primary --add 00ccff secondary
```

Opción B — generada automáticamente desde un wallpaper:

```bash
ucs palette generate ~/wallpapers/actual.jpg --colors 4 --mode contrast
```

`--mode` puede ser `contrast` (default: el secondary contrasta con el primary), `balanced` (el secondary se elige por calidad general, sin ese sesgo — suele parecerse más al primary) o `shading` (el resto de la paleta son variantes de luminosidad del primary). `--my-eyes` satura un poco más los colores elegidos. Sin `--out`, se guarda siempre en el mismo archivo (`palettes/created/generated.csv`, se reemplaza cada vez).

### 4. Armar el mapping (qué color detectado va a qué color de la paleta)

```bash
ucs mapping new mi-tema.csv
```

Es una sesión interactiva: te muestra los colores detectados y la paleta, y vas ingresando pares `<id detectado> <id de la paleta>`. Se guarda en `mappings/mapping.csv` (el mapping "canónico" — siempre el mismo archivo, se recarga solo la próxima vez que abrís la GUI o volvés a correr `detect`).

### 5. Probar antes de aplicar

```bash
ucs test --mapping mappings/mapping.csv
```

No modifica nada, solo te dice cuántos reemplazos haría.

### 6. Aplicar de verdad

```bash
ucs apply --mapping mappings/mapping.csv
```

Hace backup de cada archivo afectado (a `backup_dir`) antes de modificarlo, y corre los "servicios a reiniciar" que tengas habilitados.

### 7. Deshacer

```bash
ucs restore
```

Restaura los archivos desde el backup. `--restart`/`--no-restart` evitan la pregunta sobre reiniciar servicios.

### 8. Modo automático (todo en un paso)

Una vez que ya tenés un mapping armado (paso 4), podés reaplicarlo con una paleta distinta sin repetir todo el proceso — típicamente para regenerar el tema cada vez que cambiás de wallpaper (ver el hook de ejemplo más abajo):

```bash
ucs automatic --from-image ~/wallpapers/actual_wallpaper.jpg
```

`--from-image` genera la paleta en el momento (usando la configuración guardada, o `--colors`/`--mode`/`--my-eyes` explícitos) y la aplica contra el mapping canónico. `--mapping` es opcional (default: el mapping canónico). También podés pasarle una paleta ya generada en vez de `--from-image`:

```bash
ucs automatic palettes/created/mi-tema.csv --mapping mappings/mapping.csv
```

Si la paleta tiene menos colores de los que el mapping necesita, se bloquea (no hay de dónde sacar el color faltante). Si tiene de más, pide confirmación — `--force` la salta. `--test` simula sin aplicar.

**Ejemplo real**: hook para regenerar el tema automáticamente al cambiar de wallpaper (agregado al final del script que setea el wallpaper, en background para no agregar latencia):

```bash
ucs automatic --from-image "$WALLPAPER_FOLDER/actual_wallpaper.jpg" &
```

## Referencia rápida de comandos

| Comando | Qué hace |
|---|---|
| `detect [--dry-run]` | Escanea y actualiza `detected_palette.csv` |
| `config files list\|add\|remove` | Gestiona `files_to_replace` |
| `palette create <ruta> --add HEX LABEL ...` | Crea una paleta a mano |
| `palette generate <imagen> [--colors N] [--mode ...] [--my-eyes]` | Genera una paleta desde un wallpaper |
| `palette list` | Lista las paletas creadas |
| `palette add-color <paleta> <hex> [label]` | Agrega un color a una paleta existente |
| `mapping new <paleta>` | Sesión interactiva para armar el mapping |
| `mapping show <mapping>` | Muestra un mapping y sus conflictos |
| `test [--mapping <mapping>]` | Simula un apply |
| `apply [--mapping <mapping>]` | Aplica de verdad (con backup) |
| `restore [--restart\|--no-restart]` | Deshace desde el backup |
| `automatic <paleta>\|--from-image <img> [--mapping <mapping>]` | Aplica una paleta (existente o generada) contra un mapping ya armado |
| `gui` | Lanza la interfaz gráfica (también: `ucs` sin argumentos) |

Corré `ucs <comando> --help` para ver todas las opciones de cada uno.

## Desarrollo

```bash
git clone https://github.com/GG2R10/ultimate-color-switcher.git
cd ultimate-color-switcher
pip install --user -e '.[dev]'
pytest
```

### Estructura del proyecto

```
.
├── pyproject.toml
├── packaging/            # PKGBUILD + .desktop para el paquete de AUR
├── color_switcher/
│   ├── main.py             # CLI (incluye el subcomando `gui`; entry point de `ucs`)
│   ├── backend/             # lógica pura, sin dependencias de GTK — testeada con pytest
│   └── gui/                  # ventana principal, diálogos y blueprints (.blp) de GTK4/libadwaita
├── tests/
└── ROADMAP.md              # plan de migración original (bash → Python + GTK), como referencia histórica
```

`backend/` no depende de `gui/` en ningún sentido — toda la lógica (detección, reemplazo, mappings, generación de paletas) es invocable y testeable sin abrir ninguna ventana; `main.py` y `gui/` son dos frontends distintos sobre el mismo backend.

## Licencia

[GPL-2.0](LICENSE)
