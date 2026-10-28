# UbuClean

System cleaner, monitor, and maintenance tool for **Ubuntu Desktop 24.04** (GNOME, Wayland/X11). GTK4 + libadwaita 1.5 interface, with text entirely in English.

UbuClean is built around a **scan → preview → confirm → execute** flow. The GUI never runs as root; system operations are isolated in a CLI helper invoked through `pkexec` that only accepts whitelisted actions.

---

## Features

1. **Junk cleaner** — apt cache, unused packages, old kernels, snap revisions, Flatpak runtimes, journald, crash reports, user caches, browser caches (optional), old files in `/tmp`, and Trash. Each category shows an estimated size and a list of items you can review.
2. **Clear RAM** — RAM/swap/cache statistics before and after, `sync` + `drop_caches` level 1/2/3, plus swap refresh that only runs when enough free RAM is available.
3. **System monitor** — per-core CPU, RAM, swap, disk, network, temperature, a 60-second graph, and a list of top processes with confirmation before ending a process. Critical system processes are always refused.
4. **Disk analysis** — folder scanning on a separate thread with progress and a cancel button, treemap, list of the largest folders/files, and a three-stage duplicate finder (size → partial hash → full hash). Duplicate files are only moved to Trash through Gio and are never deleted automatically.
5. **Startup management** — XDG autostart from `~/.config/autostart` and `/etc/xdg/autostart`, plus systemd user services. System entries are disabled through user overrides without deleting the original files.
6. **Automatic schedule** — create/remove the systemd user `ubuclean-clean.timer`, choose categories, and run headless with `ubuclean --run-scheduled`.
7. **Home & Smart Clean** — System Health Score ring from 0–100, Disk/RAM/CPU/Junk cards, relevant recommendations, and a quick scan.
8. **Quarantine (undo)** — items from safe categories are moved to `~/.local/share/ubuclean/quarantine` for 7 days (configurable) and can be restored from the History page. Categories that make no sense to quarantine (apt cache, journald, snap) are explained in the UI.
9. **Developer cleaner** — Docker per object type (dangling images, stopped containers, volumes, build cache), pip/npm/yarn/pnpm/cargo/gradle/Go caches, and discovery of `node_modules`, `__pycache__`, `target/`, and `.venv` in projects that have not been touched for a long time.
10. **Application uninstaller** — apt/snap/flatpak list with size, version, and install date, search and source filters, core package protection, dependent warnings, and an offer to clean up leftover configuration with a preview.
11. **Smart notifications** — a lightweight user service that watches disk and RAM, notifies with “Clean now” / “Open monitor” actions, complete with cooldown, thresholds, and mute mode.

---

## System requirements

- Ubuntu Desktop 24.04 (Python 3.12, GTK 4.14, libadwaita 1.5)
- apt packages:

```bash
sudo apt install python3-gi python3-gi-cairo python3-psutil \
  gir1.2-gtk-4.0 gir1.2-adw-1 libnotify-bin psmisc policykit-1
```

`python3-gi-cairo` is required for the score ring and the Cairo charts. Without it the application still runs, but the charts are replaced by a warning tooltip.

`psutil` is required for the UI and the monitor; `install.sh` installs it. `pytest` is for development only and is not installed by the installer (use the venv below or `sudo apt install python3-pytest`).

## Installation

### Install script

```bash
git clone <repo> ubuclean
cd ubuclean
sudo ./install.sh            # install dependencies + application
sudo ./install.sh --dry-run  # show the steps without changing the system
sudo ./install.sh --no-deps  # skip apt, when dependencies are already present
```

When it finishes, launch **UbuClean** from the application menu or run:

```bash
ubuclean
```

### .deb package

```bash
./packaging/build-deb.sh
sudo apt install ./dist/ubuclean_1.0.0_all.deb
```

### Uninstall

```bash
sudo ./uninstall.sh         # keep configuration, logs, and quarantine
sudo ./uninstall.sh --purge # also delete user data
```

## Running from source (development)

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install psutil pytest
.venv/bin/python -m ubuclean
```

Or use the development launcher:

```bash
bin/ubuclean
```

The root helper is not run directly during development; `pkexec` calls use the installed helper. To test the helper without pkexec (root only):

```bash
sudo UBUCLEAN_HELPER_ALLOW_ROOT=1 python3 -m ubuclean.helper clean-tmp 7
```

## CLI

```bash
ubuclean                          # open the GUI
ubuclean --version
ubuclean --page clean             # open the GUI directly on a specific page

ubuclean scan --all --json        # scan all categories, output JSON
ubuclean scan --category user-cache
ubuclean clean --category user-cache --dry-run   # preview without changes
ubuclean clean --category user-cache --yes       # non-interactive execution
ubuclean status                   # RAM/swap/disk/CPU summary
ubuclean history --json

ubuclean --run-scheduled          # used by the systemd timer (headless)
ubuclean --watch                  # notification watcher
```

Category ids: `apt-cache`, `apt-autoremove`, `kernels`, `snap-revisions`, `flatpak-unused`, `journal`, `crash`, `tmp`, `user-cache`, `browser-cache`, `trash`, `dev-docker`, `dev-caches`, `dev-projects`.

The headless schedule does **not** run operations that require root authentication. Categories such as apt cache, journald, snap, and `/tmp` that need root are recorded as “skipped” in the log and in the notification summary. Risky categories (old kernels, browser cache) are never scheduled.

## Security

- **The GUI refuses root**; `ubuclean` returns an error code when started with `euid == 0`.
- **Path whitelist** in `ubuclean/core/paths.py`: only `~/.cache/**`, `~/.local/share/Trash/**`, the UbuClean quarantine, `/tmp/**`, and `/var/crash/**`. The home directory, `/`, `/etc`, `/boot`, `/usr`, `/var`, personal data (Documents/Pictures/…), `.ssh`, `.gnupg`, and `.config` are refused.
- Symlinks are resolved before validation and are never followed during deletion.
- **The helper** only accepts whitelisted subcommands with regex/range validation; there are no free-form commands and no `shell=True` anywhere in the project.
- **Polkit** action `com.ubuclean.helper.run` (`auth_admin_keep`) with an English message and an annotated helper path.
- **Preview token**: execution is only accepted for a preview scanned in the same session, so deletion without a preview is impossible.
- **Kernels**: the running kernel and at least one backup kernel are always kept; the helper re-verifies this before purging.
- **Quarantine**: safe items are moved first; restoring returns them to their original path and gives them an alternative name on conflicts.
- Every action is logged to `~/.local/state/ubuclean/log` (JSON Lines) with time, category, item count, size, result, and errors.

## Project structure

```
ubuclean/
├── ubuclean/
│   ├── core/          # cleaners, ram, monitor, disk, health, startup,
│   │                  # scheduler, scheduled, notifier, uninstaller
│   ├── helper/        # root helper + whitelist validation
│   ├── ui/            # Adw pages, Cairo widgets, style.css
│   ├── data/          # .desktop, icon, polkit policy, notifier unit
│   ├── config.py
│   └── cli.py
├── bin/               # development launcher
├── tests/             # pytest, fake filesystem, FakeRunner
├── packaging/         # build-deb.sh
├── install.sh
├── uninstall.sh
└── README.md
```

## Tests

```bash
.venv/bin/pytest -q
```

The tests never touch the real system: the filesystem uses `tmp_path`, subprocesses use `FakeRunner`, and home is isolated. Coverage includes the path whitelist, symlink escapes, quarantine/restore, the preview/clean pipeline, user & system cleaners, kernel guards, RAM/swap, the monitor, disk & duplicate analysis, startup, the scheduler, headless mode, the notifier, the uninstaller, and the CLI.

## Design notes & limitations

- The quarantine lives on the same disk, so space is only truly freed once retention expires (7 days by default). The UI distinguishes between “moved to quarantine” and “freed”.
- `drop_caches` only drops caches that will be rebuilt anyway; the UI shows an honest note and does not promise a speed boost.
- Disk and duplicate scans run on worker threads and can be cancelled. A cleanup preview cannot be cancelled halfway, but items can still be restored from quarantine.
- Temperature sensors depend on kernel/psutil support; when unavailable, the UI shows “Sensor unavailable”.
- Docker operations require access to the Docker socket (the `docker` group) or root authentication through the helper. `docker system prune -a` is never used.
- Notifications require `libnotify-bin` (`notify-send`). Notification actions open the corresponding UbuClean page.
- Animations follow the “reduce animations”/`gtk-enable-animations` setting; dark/light mode follows the system theme.
- `Adw.ShortcutsDialog` is not yet available in libadwaita 1.5 (the Ubuntu 24.04 version), so F1 opens an `Adw.Dialog` with equivalent shortcut content.
- The uninstaller is opened from a button in the header of the Clean page or from Settings → Installed applications.

## License

GPL-3.0-or-later. See `LICENSE`.
