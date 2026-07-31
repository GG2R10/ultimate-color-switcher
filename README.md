<div align="center">

<img src="assets/ucs_banner.png" alt="Ultimate Color Switcher" width="100%">

**🎬 Watch UCS in action!**

https://github.com/user-attachments/assets/13d33e55-ccdc-46f9-b0aa-ab062967a2ee

![License](https://img.shields.io/badge/license-GPL--2.0-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![AUR](https://img.shields.io/badge/AUR-ucs--git-1793d1)
![Visits](https://komarev.com/ghpvc/?username=GG2R10&repo=ultimate-color-switcher&label=Visits&color=blueviolet&style=flat)

Welcome to UCS! The ultimate tool for automatic (and manual 😉) color theming in Linux! Detect every HEX and RGB color in your dotfiles, create a palette or generate one straight from your wallpaper, and apply the mapping safely — with backups and a test/simulate mode — before touching anything for real.

</div>

<table>
<tr>
<td width="50%" valign="top">

### 🎨 Automatic palette generation
Generates a palette straight from your wallpaper (hand-rolled K-Means + Lab/ΔE space): 3 selection modes, configurable scoring weights, a complementary palette (Ying Yang), shuffle/overfetch variants, and a saturation boost.

</td>
<td width="50%" valign="top">

### 🖥️ GUI + CLI
Same backend, two ways to drive it: a GTK4/libadwaita window for interactive use, or a full CLI for scripts and hooks.

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🛡️ Safe by default
Automatic backup of every file before it's touched, a simulate mode (`test`/`--test`), and conflict detection before anything is actually applied.

</td>
<td width="50%" valign="top">

### 🔗 Automatable
One command (`ucs automatic --from-image`) to select and generate or regenerate the whole theme. Perfect as a hook when your wallpaper changes.

</td>
</tr>
</table>

## ✨ Features Showcase

<table>
<tr>
<td width="50%" valign="top">

**Detected colors, as clickable pills**
Every hex/rgb color found in your dotfiles becomes a pill — pick it, assign it a palette color, and that's a mapping entry.

<img src="assets/detected%20color%20file%20pills.gif" alt="Detected color pills" width="100%">

</td>
<td width="50%" valign="top">

**Foreground/background role tagging**
Tag any detected color as foreground or background (click the little F/B badge to cycle) so palette generation can build real contrasting pairs instead of guessing.

<img src="assets/role%20button%20gui%20showcase.gif" alt="Foreground/background role button" width="100%">

</td>
</tr>
<tr>
<td width="50%" valign="top">

**Post-apply scripts**
Run any shell command right after a real Apply — restart Waybar, reload a script, or hand off to your own wallpaper-setter with `$UCS_WALLPAPER` already sitting in its environment.

<img src="assets/post%20scripts%20menu%20showcase.gif" alt="Post-apply scripts menu" width="100%">

</td>
<td width="50%" valign="top">

**Easy implementation with other apps**
Waybar, Waypaper (or anything that can use commands) can integrate UCS thanks to its CLI interface :D

<img src="assets/waybar%20ucs%20mods%20menu.gif" alt="Waybar picking up the new colors" width="100%">

</td>
</tr>
<tr>
<td width="50%" valign="top">

**Live modifiers & shuffle**
Tweak an already-generated palette's modifiers (saturation, Ying Yang, regeneration mode, shuffle…) with a live preview before committing to anything.

<img src="assets/modifiers%20GUI%20menu%20and%20shuffle%20showcase.gif" alt="Modifiers menu and shuffle" width="100%">

</td>
<td width="50%" valign="top">

**Manage every saved palette**
Wallpaper thumbnail, color swatches, and its own mapping, all in one list — load, delete, or bulk-clean them without leaving the dialog.

<img src="assets/manage%20paletes%20and%20mappings%20menu.gif" alt="Manage palettes and mappings" width="100%">

</td>
</tr>
</table>

## Contents

- [Installation](#installation)
- [Initial setup](#initial-setup)
- [Usage — GUI](#usage--gui)
- [Usage — CLI, step by step](#usage--cli-step-by-step)
- [Quick command reference](#quick-command-reference)
- [Development](#development)

## Installation

### Arch Linux (AUR)

```bash
paru -S ucs-git   # or yay, or makepkg -si from packaging/PKGBUILD
```

This installs the `ucs` command, its menu entry (`.desktop` + icon), and automatically resolves the system dependencies (GTK4, libadwaita, PyGObject, numpy, Pillow).

<details>
<summary><b>Manual / other distros</b> (not everyone runs Arch)</summary>

<br>

It also works as a regular Python package. GTK4, libadwaita, and PyGObject are system libraries, so they **don't** come from pip. Install them with your distro's package manager first:

```bash
# Arch
sudo pacman -S python-gobject gtk4 libadwaita blueprint-compiler

# Debian/Ubuntu
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 blueprint-compiler

# Fedora
sudo dnf install python3-gobject gtk4 libadwaita blueprint-compiler
```

Most distros (Debian/Ubuntu/Fedora included) block a global or per-user `pip`, so to install in other distros you can:

## 1. Use the virtual environment and install in ~/.local/bin/ucs

```bash
git clone https://github.com/GG2R10/ultimate-color-switcher.git
cd ultimate-color-switcher

# compile the GTK interfaces once (if you don't have blueprint-compiler,
# they compile themselves the first time you run the GUI, so this step
# is optional)
for blp in color_switcher/gui/blueprints/*.blp; do
    blueprint-compiler compile "$blp" --output "${blp%.blp}.ui"
done

python -m venv --system-site-packages ~/.local/share/ucs-venv
~/.local/share/ucs-venv/bin/pip install .          # or "pip install -e ." for development
ln -s ~/.local/share/ucs-venv/bin/ucs ~/.local/bin/ucs # you need to have the route in your $PATH
```

## 2. Or, use pipx 
install [pipx](https://pipx.pypa.io/), and then:

```
pipx install --system-site-packages .
``` 

Again, **you need** `~/.local/bin/` in your `$PATH` env.

</details>

## Initial setup

User data (config, palettes, mappings) lives under `~/.config/ucs/`, separate from the code — it's created the first time you run `ucs`. The central file is `~/.config/ucs/config.json`:

```json
{
  "files_to_replace": [
    "$HOME/.config/hypr/hyprlock.conf",
    "$HOME/.config/waybar/colors.css"
  ],
  "backup_dir": "$HOME/.config-colors-backup"
}
```

- `files_to_replace`: the config files where colors get searched and replaced. You can manage this without editing the JSON by hand — see [`ucs config files`](#usage--cli-step-by-step) or the GUI's **Files to scan…** menu entry. If it's empty (first run), right after the welcome screen the GUI offers to **automatically search `~/.config`** for files that have colors (with a tree to review and drop folders/files before confirming), add them by hand, or edit `config.json`. Same thing from the CLI with [`ucs config files scan-config`](#1-choose-which-files-to-scan).
- `backup_dir`: where a copy of every file is kept before it actually gets touched.

Paths accept `$HOME`/`~` and are stored that way (not absolute), so `config.json` stays valid across machines that share these dotfiles.

## Usage — GUI

```bash
ucs            # or: ucs gui
```

<div align="center">
<img src="assets/gui.png" alt="Ultimate Color Switcher main window" width="85%">
</div>

The main window has two columns: detected colors (left) and the target palette (right). Pick a detected color, assign it a palette color, and that's how you build the mapping. The menu (⋮) has:

- **New palette… / Import palette… / Save palette as… / Add color…** — manage the target palette.
- **Generate palette from image…** — build a palette automatically from a wallpaper.
- **Configure palette generation…** — selection mode (contrast / balanced / shading), extra saturation (`--my-eyes`), **Ying Yang** (complementary palette), scoring weights (default / alternative / custom), contrast comparison (weighted vs. background-only), and advanced options (overfetch and shuffle to explore variants).
- **Manage palettes…** — browse every saved palette (wallpaper thumbnail, colors, its own mapping) and **load**, delete, or bulk-clean them.
- **Other…** — fine-tuning knobs (like the `--my-eyes` saturation boost) plus backup status/delete.
- **Auto-link hex/rgb** — the same real color appearing in two different formats across your files is treated as one by default.
- **Files to scan…** — add/remove files from `files_to_replace` (includes the automatic `~/.config` search).
- **Post-apply scripts…** — commands to run after a real apply (e.g. restarting Waybar, or handing your wallpaper-setter `$UCS_WALLPAPER`). Each one can be turned off for CLI-triggered applies, so it doesn't double-fire when `ucs automatic` is chained as another tool's postcommand hook.
- **Save detection snapshot…** — save a named copy of the current detection, to reuse later.

The **Simulate** and **Apply** buttons do the same thing as `test`/`apply` on the CLI.

## Usage — CLI, step by step

Every command is non-interactive except `mapping new` (a guided session) and `restore` (which asks whether to run the post-apply scripts, unless you pass `--postcommands`/`--no-postcommands`).

### 1. Choose which files to scan

```bash
ucs config files list
ucs config files add ~/.config/waybar/colors.css
ucs config files remove '$HOME/.config/waybar/colors.css'

# or let it search ~/.config on its own for files with colors:
ucs config files scan-config --dry-run   # preview without adding
ucs config files scan-config             # search + confirm + add
```

`scan-config` walks `~/.config` filtering by format (`.conf`, `.toml`, `.css`, `.rasi`, `.lua`, … and files literally called `config`), skips binaries, large files, and cache/logs/`.git`/`node_modules` directories, and never follows directory symlinks. It's a starting point: it may include hex-looking values that aren't colors (e.g. `0xADDR`) or files you don't want touched — review them and drop what doesn't apply with `ucs config files remove`.

### 2. Detect the current colors

```bash
ucs detect
```

Scans `files_to_replace`, saves the result to `~/.config/ucs/palettes/detected/detected_palette.csv`, and shows every detected color with an `id` — that `id` is what you'll use to build the mapping.

### 3. Get a target palette

Option A — by hand:

```bash
ucs palette create my-theme.csv --add ff00aa primary --add 00ccff secondary
```

Option B — generated automatically from a wallpaper:

```bash
ucs palette generate ~/wallpapers/current.jpg --colors 4 --mode contrast
ucs palette show palettes/created/generated.csv   # view it in the terminal with swatches
```

`palette generate` options (all also available on `automatic --from-image`):

| Option | What it does |
|---|---|
| `--mode contrast\|balanced\|shading` | `contrast` (default): the secondary contrasts with the primary. `balanced`: the secondary is picked on overall quality, without that bias (tends to look closer to the primary). `shading`: the rest of the palette are luminance variants of the primary. |
| `--my-eyes` | Saturates the chosen colors a bit more. |
| `--ying-yang on\|off` | Ying Yang (default `off`): uses the complementary palette (rotates every hue 180°). |
| `--scoring default\|alternative\|custom` | How coverage / saturation / midtone / contrast are weighted when picking colors. `custom` uses `--custom-scoring-values coverage=20,saturation=40,midtone=30,contrast=10` (must add up to 100), or whatever's saved in `config.json`. |
| `--no-weighted-contrast` | Compares contrast only against the single most dominant color, instead of the weighted system against every cluster (default: weighted). |
| `--shuffle N\|next` | Skips the first N primary candidates (everything else gets re-picked from there). `next` resumes from the last value + 1, cyclically — meant for scripts that iterate through variants. |
| `--overfetch N` | Considers N extra candidates beyond `--colors`, giving `--shuffle` more room to work with. |

Without `--out`, it always saves to the same file (`palettes/created/generated.csv`, overwritten each time). `ucs palette show <palette>` prints any palette with its colors in the terminal.

### 4. Build the mapping (which detected color maps to which palette color)

```bash
ucs mapping new my-theme.csv
```

An interactive session: it shows you the detected colors and the palette, and you enter `<detected id> <palette id>` pairs. Saved to `mappings/mapping.csv` (the "canonical" mapping — always the same file, reloaded automatically the next time you open the GUI or run `detect` again).

### 5. Test before applying

```bash
ucs test --mapping mappings/mapping.csv
```

Doesn't modify anything, just tells you how many replacements it would make.

### 6. Apply for real

```bash
ucs apply --mapping mappings/mapping.csv
```

Backs up every affected file (to `backup_dir`) before modifying it, and runs whichever post-apply scripts you have enabled.

### 7. Undo

```bash
ucs restore
```

Restores files from the backup. `--postcommands`/`--no-postcommands` skip the prompt about running the post-apply scripts.

### 8. Automatic mode (everything in one step)

Once you already have a mapping built (step 4), you can reapply it against a different palette without repeating the whole process — typically to change the theme every time your wallpaper changes (see the example hook below):

```bash
ucs automatic --from-image ~/wallpapers/current_wallpaper.jpg
```

`--from-image` generates the palette on the spot (using the saved settings, or any explicit [`palette generate`](#3-get-a-target-palette) option: `--colors`, `--mode`, `--my-eyes`, `--ying-yang`, `--scoring`, `--shuffle`, `--overfetch`…) and applies it against the canonical mapping. `--mapping` is optional (default: the canonical mapping). You can also pass it an already-generated palette instead of `--from-image`:

```bash
ucs automatic palettes/created/my-theme.csv --mapping mappings/mapping.csv
```

Blockers, and how to get past them:

- **Insufficient palette** (fewer colors than the mapping needs roles for): a hard block, can't be skipped — there's nowhere to pull the missing color from.
- **Extra colors** (more colors than roles, the extra ones go unused): asks for confirmation, skip it with `--yolo`.
- **Conflicts** (case 1 / convergence: a color would collide with another detected color and the distinction would be lost): asks for confirmation, skip it with `--force`.

`--force` and `--yolo` are kept separate on purpose: accepting "there are extra colors" doesn't quietly let a real conflict through, and vice versa. `--test` simulates without applying.

**Real example**: a hook to regenerate the theme automatically whenever the wallpaper changes (tacked onto the end of whatever script sets it, run in the background so it doesn't add latency):

```bash
ucs automatic --from-image "$WALLPAPER_FOLDER/current_wallpaper.jpg" &
```

## Quick command reference

<details open>
<summary>See the full table</summary>

<br>

| Command | What it does |
|---|---|
| `detect [--dry-run]` | Scans and updates `detected_palette.csv` |
| `config files list\|add\|remove` | Manages `files_to_replace` |
| `config files scan-config [--dry-run] [--yes]` | Searches `~/.config` for files with colors and adds them |
| `palette create <path> --add HEX LABEL ... [--apply]` | Creates a palette by hand |
| `palette generate <image> [--colors N] [--mode ...] [--my-eyes] [--ying-yang on\|off] [--scoring ...] [--shuffle N\|next] [--overfetch N] [--no-weighted-contrast] [--apply]` | Generates a palette from a wallpaper (without `--colors`: however many roles the mapping uses) |
| `palette show [palette\|-] [--apply]` | Shows a palette (CSV, JSON, or `-` for JSON on stdin) with its colors in the terminal |
| `palette list` | Lists created palettes |
| `palette add-color [palette] <hex> [--label LABEL] [--apply]` | Adds a color to an existing palette |
| `palette edit [palette] <id\|hex> <new-hex> [--apply]` | Swaps one color in a palette for another (keeps its position) |
| `palette remove [palette] <id\|hex> [--apply]` | Deletes a color from a palette (renumbers and adjusts the mapping if needed) |
| `palette shift [palette] [--my-eyes on\|off\|toggle] [--ying-yang on\|off\|toggle] [--mode ...] [--scoring ...] [--shuffle N\|next] [--overfetch N] [--colors N] [--apply]` | Changes an already generated/created palette's modifiers and, with `--apply`, reapplies it — without repeating the original image or flags |
| `mapping new <palette>` | Interactive session to build the mapping |
| `mapping show <mapping>` | Shows a mapping and its conflicts |
| `test [--mapping <mapping>]` | Simulates an apply |
| `apply [--mapping <mapping>]` | Applies for real (with backup) |
| `restore [--postcommands\|--no-postcommands]` | Undoes from the backup |
| `automatic <palette>\|--from-image <img> [--mapping <mapping>] [--force] [--yolo]` | Compatibility alias: `--from-image` is equivalent to `palette generate --apply`; an existing palette is equivalent to `palette show --apply`. `automatic shift` is equivalent to `palette shift --apply` |
| `gui` | Launches the graphical interface (also: `ucs` with no arguments) |

`palette create/generate/edit/remove/add-color/show/shift` without an explicit `[palette]` use whatever the current mapping applies against (its `#new_palette=`). Any of them with `--apply` also accepts `--mapping <mapping>` `--test` `--force` `--yolo`, same as the `apply` command's.

Run `ucs <command> --help` to see every option for each one.

</details>

## Development

```bash
git clone https://github.com/GG2R10/ultimate-color-switcher.git
cd ultimate-color-switcher
python -m venv --system-site-packages .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

<details>
<summary><b>Project structure</b></summary>

<br>

```
.
├── pyproject.toml
├── packaging/            # PKGBUILD + .desktop for the AUR package
├── color_switcher/
│   ├── main.py             # CLI (includes the `gui` subcommand; ucs's entry point)
│   ├── backend/             # pure logic, no GTK dependency — tested with pytest
│   └── gui/                  # main window, dialogs, and GTK4/libadwaita blueprints (.blp)
├── tests/
└── ROADMAP.md              # the original migration plan (bash → Python + GTK), kept as historical reference
```

`backend/` never depends on `gui/` — all the logic (detection, replacement, mappings, palette generation) is callable and testable without opening a single window; `main.py` and `gui/` are just two different frontends over the same backend.

</details>

## License

[GPL-2.0](LICENSE)
