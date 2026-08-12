# dota2-metagrid-bot

[Русский](README.md) | **English**

A terminal utility for Windows that downloads the daily meta hero grid for
Dota 2 from [dota2protracker.com/meta-hero-grids](https://dota2protracker.com/meta-hero-grids)
(**D2PT Rating** mode) and writes it straight into the game config:

```
<Steam>/userdata/<steam_account_id>/570/remote/cfg/hero_grid_config.json
```

Set it up once — and your draft always shows the current meta. The grid on
the site is updated daily; the utility lives in autostart and does
everything by itself.

Data source: [dota2protracker.com](https://dota2protracker.com) — thanks to
the site's authors for their work.

## Installation (step by step)

No additional software is required — no Python, nothing else. Just the
utility itself.

### Step 1. Download the program

Open the
[Releases](https://github.com/paintdrip/dota2-metagrid-bot/releases/latest)
page and download **`dota2-metagrid.exe`** (Assets → dota2-metagrid.exe).

### Step 2. Move the file to a permanent folder

Create a folder, e.g. `C:\Tools\dota2-metagrid\`, and move the downloaded
file there. Don't leave it in "Downloads" — it's easy to delete it
accidentally, and the autostart task will be bound to this path.

### Step 3. First run

Open the folder in Explorer, hold `Shift`, right-click on an empty area and
choose "Open PowerShell window here" (or "Open in Terminal"). Type:

```
.\dota2-metagrid.exe
```

On the first run Windows may show a blue "Windows protected your PC"
SmartScreen dialog — this is a standard warning for any new program without
a digital signature. Click **"More info" → "Run anyway"**.

The program will download the grid and write it into the Dota 2 config. At
the end you will see a line like:

```
Готово: сетка D2PT Rating записана в C:\Program Files (x86)\Steam\userdata\...\570\remote\cfg\hero_grid_config.json
```

(Done: D2PT Rating grid written to ...\hero_grid_config.json)

If an error appears instead — read the message: it explains what went wrong
(e.g. Steam not found or the site temporarily unavailable). Note: program
messages are in Russian.

### Step 4. Enable autostart

In the same window run:

```
.\dota2-metagrid.exe --install
```

Done. The grid will now update itself: at Windows logon and daily at 12:00.
There will be no redundant runs — if the grid is fresh (updated less than
20 hours ago), the program simply exits.

You can remove it from autostart at any time:

```
.\dota2-metagrid.exe --uninstall
```

### Step 5. Check in game

Launch Dota 2, open the "Heroes" tab and pick **Dota2ProTracker … - All
Roles** (or one of the role-specific grids) in the grid dropdown.

> Important: Dota 2 reads `hero_grid_config.json` at startup and overwrites
> it on exit. Update the grid while the game is closed — the autostart
> tasks usually fire exactly at such moments.

## Building it yourself

Requires Python 3.10+ and installed Google Chrome.

```bat
git clone https://github.com/paintdrip/dota2-metagrid-bot.git
cd dota2-metagrid-bot
build.bat
```

The built file appears at `dist\dota2-metagrid.exe`.

Running from source without building:

```bat
pip install -r requirements.txt
python metagrid.py
```

## Usage (all commands)

```
dota2-metagrid.exe                single update run with progress output
dota2-metagrid.exe --auto         quiet mode for autostart: skips if the grid
                                  was updated less than 20 hours ago
dota2-metagrid.exe --auto --force ignore the 20-hour check
dota2-metagrid.exe --dry-run      download and parse the grid without writing
dota2-metagrid.exe --install      create Task Scheduler tasks (autostart)
dota2-metagrid.exe --uninstall    remove the Task Scheduler tasks
dota2-metagrid.exe --mode matches_wr   different hero selection mode
dota2-metagrid.exe --steam-path "D:\Steam"   set Steam location manually
dota2-metagrid.exe --user-id 123456789      update a single account only
dota2-metagrid.exe --verbose      verbose output
```

Exit codes: `0` — success (including a skip in `--auto`), `1` — error
(the message is printed in Russian to stderr).

## Autostart (details)

`--install` creates two Windows Task Scheduler tasks:

- **Dota2MetaGrid-Logon** — runs at user logon (`--auto`);
- **Dota2MetaGrid-Daily** — runs daily at 12:00 (`--auto`).

The moment of the last successful update is stored in `state.json` next to
the exe (or in `%LOCALAPPDATA%\dota2-metagrid\state.json`).

## What exactly is written

The `hero_grid_config.json` file is the native Dota 2 hero grid format:

```json
{
  "configs": [
    {
      "config_name": "Dota2ProTracker 7.xx - All Roles",
      "categories": [
        {
          "category_name": "Carry",
          "x_position": 0,
          "y_position": 0,
          "width": 240,
          "height": 120,
          "hero_ids": [74, 11, 50]
        }
      ]
    }
  ],
  "version": 3
}
```

The utility does not construct this structure itself — it extracts the
ready-made object from the page data and writes it as is.

**All** accounts found in `<Steam>/userdata` are updated (if the
`570/remote/cfg` folder doesn't exist, it is created).

Before overwriting an existing config for the first time, a backup is made:
`hero_grid_config_backup.json` (an existing backup is never overwritten).

Writes are atomic: temporary file + `os.replace`, so an interrupted write
cannot corrupt the config.

Steam is located via the registry (`HKCU\Software\Valve\Steam`, `SteamPath`
value); if needed, the path can be set with the `--steam-path` flag.

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/          # tests (no network calls)
python metagrid.py --dry-run --verbose
```

On Linux/macOS, for development, Steam is searched in `~/.steam/steam` and
`~/.local/share/Steam`.

## License

[MIT](LICENSE)
