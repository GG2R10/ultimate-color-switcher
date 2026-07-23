# Color Switcher

Herramienta para detectar los colores usados en tus dotfiles, armar una paleta nueva (a mano o generada automáticamente desde un wallpaper) y aplicar el reemplazo de forma segura — con backup y un modo de simulación antes de tocar nada de verdad.

Tiene dos formas de usarse: una interfaz gráfica (GTK4 + libadwaita) y una CLI completa para scripts/hooks (por ejemplo, para regenerar el tema cada vez que cambiás de wallpaper).

## Requisitos

- Python 3.10+
- GTK4, libadwaita 1, PyGObject y blueprint-compiler (para la GUI)
- numpy y Pillow (para la generación de paletas desde imagen)
- pytest (solo para correr los tests)

En Arch:

```bash
sudo pacman -S python-gobject gtk4 libadwaita blueprint-compiler python-numpy python-pillow python-pytest
```

O con pip, dentro de un entorno virtual (PyGObject/GTK4/libadwaita/blueprint-compiler igual hay que instalarlos por el gestor de paquetes del sistema, no son paquetes de pip):

```bash
pip install -r requirements.txt
```

## Configuración inicial

Todo el proyecto vive alrededor de un `config.json` en la raíz (al lado de esta carpeta `app/`), con al menos:

```json
{
  "files_to_replace": [
    "$HOME/.config/hypr/hyprlock.conf",
    "$HOME/.config/waybar/colors.css"
  ],
  "backup_dir": "$HOME/.config-colors-backup"
}
```

- `files_to_replace`: los archivos de configuración donde se buscan y reemplazan colores. Se pueden gestionar sin editar el JSON a mano — ver más abajo.
- `backup_dir`: dónde se guarda una copia de cada archivo antes de tocarlo de verdad.

Las rutas admiten `$HOME`/`~` y se guardan así (no absolutas), para que el `config.json` siga siendo válido si movés o compartís el proyecto entre máquinas.

## Uso — GUI

```bash
python main.py gui
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
python main.py config files list
python main.py config files add ~/.config/waybar/colors.css
python main.py config files remove '$HOME/.config/waybar/colors.css'
```

### 2. Detectar los colores actuales

```bash
python main.py detect
```

Escanea `files_to_replace`, guarda el resultado en `palettes/detected/detected_palette.csv` y te muestra cada color detectado con un `id` — ese `id` es lo que vas a usar para armar el mapping.

### 3. Conseguir una paleta objetivo

Opción A — a mano:

```bash
python main.py palette create palettes/created/mi-tema.csv \
    --add ff00aa primary --add 00ccff secondary
```

Opción B — generada automáticamente desde un wallpaper:

```bash
python main.py palette generate ~/wallpapers/actual.jpg --colors 4 --mode contrast
```

`--mode` puede ser `contrast` (default: el secondary contrasta con el primary), `balanced` (el secondary se elige por calidad general, sin ese sesgo — suele parecerse más al primary) o `shading` (el resto de la paleta son variantes de luminosidad del primary). `--my-eyes` satura un poco más los colores elegidos.

### 4. Armar el mapping (qué color detectado va a qué color de la paleta)

```bash
python main.py mapping new palettes/created/mi-tema.csv
```

Es una sesión interactiva: te muestra los colores detectados y la paleta, y vas ingresando pares `<id detectado> <id de la paleta>`. Se guarda en `mappings/mapping.csv` (el mapping "canónico" — siempre el mismo archivo, se recarga solo la próxima vez que abrís la GUI o volvés a correr `detect`).

### 5. Probar antes de aplicar

```bash
python main.py test --mapping mappings/mapping.csv
```

No modifica nada, solo te dice cuántos reemplazos haría.

### 6. Aplicar de verdad

```bash
python main.py apply --mapping mappings/mapping.csv
```

Hace backup de cada archivo afectado (a `backup_dir`) antes de modificarlo, y corre los "servicios a reiniciar" que tengas habilitados.

### 7. Deshacer

```bash
python main.py restore
```

Restaura los archivos desde el backup. `--restart`/`--no-restart` evitan la pregunta sobre reiniciar servicios.

### 8. Modo automático (todo en un paso)

Una vez que ya tenés un mapping armado (paso 4), podés reaplicarlo con una paleta distinta sin repetir todo el proceso — típicamente para regenerar el tema cada vez que cambiás de wallpaper:

```bash
python main.py automatic --from-image ~/wallpapers/nuevo.jpg --mapping mappings/mapping.csv
```

`--from-image` genera la paleta en el momento (usando la config guardada, o `--colors`/`--mode`/`--my-eyes` explícitos) y la aplica contra el mapping existente. También podés pasarle una paleta ya generada:

```bash
python main.py automatic palettes/created/mi-tema.csv --mapping mappings/mapping.csv
```

Si la paleta tiene menos colores de los que el mapping necesita, se bloquea (no hay de dónde sacar el color faltante). Si tiene de más, pide confirmación — `--force` la salta. `--test` simula sin aplicar.

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
| `test --mapping <mapping>` | Simula un apply |
| `apply --mapping <mapping>` | Aplica de verdad (con backup) |
| `restore [--restart\|--no-restart]` | Deshace desde el backup |
| `automatic <paleta>\|--from-image <img> --mapping <mapping>` | Aplica una paleta (existente o generada) contra un mapping ya armado |
| `gui` | Lanza la interfaz gráfica |

Corré `python main.py <comando> --help` para ver todas las opciones de cada uno.

## Tests

```bash
cd app
pytest
```

## Estructura del proyecto

```
app/
├── main.py           # CLI (incluye el subcomando `gui`)
├── backend/           # lógica pura, sin dependencias de GTK — testeada con pytest
├── gui/                # ventana principal, diálogos y blueprints (.blp) de GTK4/libadwaita
├── tests/
└── ROADMAP.md          # plan de migración original (bash → Python + GTK), como referencia histórica
```

`backend/` no depende de `gui/` en ningún sentido — toda la lógica (detección, reemplazo, mappings, generación de paletas) es invocable y testeable sin abrir ninguna ventana; `main.py` y `gui/` son dos frontends distintos sobre el mismo backend.
