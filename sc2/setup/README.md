# SC2 environments

## Windows native client

The Windows launcher uses the existing installation at
`C:\Program Files (x86)\StarCraft II`, or the `SC2PATH` environment variable.
It does not use the Linux executable or the PyGame viewer.

From the project root, with `uv` installed:

```powershell
.\sc2\setup\windows.ps1
.\sc2\start-windows.ps1
```

The setup creates `.deps/sc2-win` with Python 3.10 and the existing locked
`sc2/requirements.txt`. Press Enter in PowerShell when ready, then switch to
the native human window while it loads. The game starts automatically when
loading completes. Human matches use the engine's real-time mode; unlike the
older stepped version, there is no console wait or 3-second delay after joining.
The AI client is
hidden by its process ID after startup and map loading. The human window title
identifies it and explains how to begin. Loading may still briefly show windows.

Both participants use the installed `latest` engine, verified here as
5.0.17.98310 / Base98310 / data `9C1F3D3D30CA252E33FC67CA05CBE1EA`.
Do not combine its match statistics or replays with Linux 4.10 runs.

## Linux SC2 environment

This setup uses the official Linux 4.10 engine independently of the existing
Windows installation. It does not install or modify Windows game files.

The bootstrap is an AI testing tool covered by Blizzard's
[AI and Machine Learning License](https://blzdistsc2-a.akamaihd.net/AI_AND_MACHINE_LEARNING_LICENSE.html).
Game data is installed locally and is not part of this repository or a distributable.

## Current machine

- WSL distribution: `Ubuntu` (24.04, user `nyuny`).
- Python: `/home/nyuny/.venvs/sc2-arena/bin/python` (3.10.22).
- `SC2PATH=/home/nyuny/sc2/StarCraftII`.
- Engine identity verified by API: `4.10.0.75689`, base `75689`, data
  `B89B5D6FA7CBF6452E721311BFBC6CB2`.
- Dependency source: `sc2/requirements.in`; complete lock: `sc2/requirements.txt`.
- Linux packages: `unzip`, `libosmesa6`; map authoring additionally uses `smpq`.
- WSLg is present. The bootstrap never initializes a display window.

## Start from Windows PowerShell

From the project directory, this opens the human RGB view paused before the
first simulation step:

```powershell
.\sc2\start.ps1
```

Press Pause or Esc in the game view to begin. The same entry point forwards the
runner's arguments:

```powershell
.\sc2\start.ps1 duel --seed 2
.\sc2\start.ps1 watch --replay 'F:\project\sc2\sc2\runs\RUN_ID\game.SC2Replay'
.\sc2\start.ps1 --help
```

`human` defaults to `--rgb --start-paused`; `watch` defaults to RGB. Replay paths
can be Windows paths (including spaces), paths relative to the current
PowerShell directory, or absolute Linux paths. Other runner arguments are
forwarded unchanged; use project-relative or Linux paths for `--map`, `--rules`,
and `--output`. The script derives the project location from its own directory,
uses `wslpath`, and passes arguments directly through `wsl.exe --exec` without a
shell command string. It uses the existing Ubuntu environment and the verified
SC2/OSMesa runtime settings; it does not copy or install anything.

## Reproduce installation

Run in Ubuntu after reviewing the official license linked above:

```bash
uv python install 3.10.22
uv venv --python 3.10.22 ~/.venvs/sc2-arena
uv pip sync --python ~/.venvs/sc2-arena/bin/python sc2/requirements.txt
mkdir -p ~/.cache/sc2-arena ~/sc2
curl --fail --location --retry 3 --continue-at - \
  --output ~/.cache/sc2-arena/SC2.4.10.zip \
  https://blzdistsc2-a.akamaihd.net/Linux/SC2.4.10.zip
unzip -n -P iagreetotheeula ~/.cache/sc2-arena/SC2.4.10.zip -d ~/sc2
curl --fail --location --retry 2 \
  --output ~/.cache/sc2-arena/mini_games.zip \
  https://github.com/google-deepmind/pysc2/releases/download/v1.2/mini_games.zip
mkdir -p ~/sc2/StarCraftII/Maps
unzip -n ~/.cache/sc2-arena/mini_games.zip -d ~/sc2/StarCraftII/Maps
```

The encrypted archive password is Blizzard's published license-acceptance token,
not a credential. Install the named Linux packages through the distribution's
package manager if absent. Do not rerun installation while the environment owner
has an active download or package-manager session.

## Bootstrap

Use a new output directory for every run:

```bash
SC2PATH=/home/nyuny/sc2/StarCraftII \
  LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libstdc++.so.6 LP_NUM_THREADS=4 \
  /home/nyuny/.venvs/sc2-arena/bin/python sc2/setup/bootstrap.py \
  --osmesa --out /tmp/sc2-bootstrap-NEW
```

The script uses PySC2's process/controller lifecycle with the official
`MoveToBeacon` map, confirms a real RAW movement, reads a nonuniform 256×256 RGB
frame, and saves a replay and the engine's actual version/build/data identity.
It closes the engine on completion. This machine needs `--osmesa`: its default
EGL path has no usable display. The system C++ library preload is also necessary
because the 2019 game bundle's C++ library cannot load current Mesa. The preload
applies only to this process tree and leaves the game files intact. Four
llvmpipe threads keep this small smoke check from using all CPU threads.

The verified output contained a real two-world-unit move, no action errors, a
nonuniform RGB frame, and a saved replay. This is an integration smoke check,
not a formal arena match or proof of human UI control.

Sources: [official Linux packages and setup](https://github.com/Blizzard/s2client-proto#linux-packages),
[renderer flags](https://github.com/Blizzard/s2client-proto/blob/master/docs/linux.md),
[PySC2 setup and mini-games](https://github.com/google-deepmind/pysc2).
