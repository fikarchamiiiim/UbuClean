#!/usr/bin/env bash
# UbuClean installer for Ubuntu Desktop 24.04.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN=0
INSTALL_DEPS=1

usage() {
  cat <<'EOF'
Usage: sudo ./install.sh [--dry-run] [--no-deps]

  --dry-run   Show the steps without changing the system.
  --no-deps   Do not install apt packages (assume dependencies are present).
  -h, --help  Show this help.
EOF
}

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --no-deps) INSTALL_DEPS=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $arg" >&2; usage; exit 2 ;;
  esac
done

if [[ "$DRY_RUN" -eq 0 && "${EUID}" -ne 0 ]]; then
  echo "Installation requires administrator rights. Run: sudo ./install.sh" >&2
  exit 1
fi

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '+ %s\n' "$*"
  else
    "$@"
  fi
}

say() {
  printf '\n==> %s\n' "$*"
}

say "UbuClean 1.0.0 — installation"
if [[ "$INSTALL_DEPS" -eq 1 ]]; then
  say "Installing apt dependencies"
  run apt-get install -y --no-install-recommends \
    python3 \
    python3-gi \
    python3-gi-cairo \
    python3-psutil \
    gir1.2-gtk-4.0 \
    gir1.2-adw-1 \
    libnotify-bin \
    psmisc \
    policykit-1
else
  say "Skipping apt dependencies (--no-deps)"
fi

say "Copying the application to /usr/lib/ubuclean"
run install -d /usr/lib/ubuclean/bin
run cp -a "$SCRIPT_DIR/ubuclean" /usr/lib/ubuclean/
run install -m 0755 "$SCRIPT_DIR/bin/ubuclean" /usr/lib/ubuclean/bin/ubuclean
run install -m 0755 "$SCRIPT_DIR/bin/ubuclean-helper" /usr/lib/ubuclean/bin/ubuclean-helper

if [[ "$DRY_RUN" -eq 0 ]]; then
  find /usr/lib/ubuclean -type d -name __pycache__ -prune -exec rm -rf {} +
fi

say "Creating /usr/bin launchers"
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "+ write /usr/bin/ubuclean and /usr/bin/ubuclean-helper"
else
  cat > /usr/bin/ubuclean <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /usr/lib/ubuclean/bin/ubuclean "$@"
EOF
  cat > /usr/bin/ubuclean-helper <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /usr/lib/ubuclean/bin/ubuclean-helper "$@"
EOF
  chmod 0755 /usr/bin/ubuclean /usr/bin/ubuclean-helper
fi

say "Installing desktop data, icons, polkit policy, and notifier unit"
run install -Dm644 "$SCRIPT_DIR/ubuclean/data/com.ubuclean.App.desktop" \
  /usr/share/applications/com.ubuclean.App.desktop
run install -Dm644 "$SCRIPT_DIR/ubuclean/data/icons/hicolor/scalable/apps/com.ubuclean.App.svg" \
  /usr/share/icons/hicolor/scalable/apps/com.ubuclean.App.svg
run install -Dm644 "$SCRIPT_DIR/ubuclean/data/com.ubuclean.helper.policy" \
  /usr/share/polkit-1/actions/com.ubuclean.helper.policy
run install -Dm644 "$SCRIPT_DIR/ubuclean/data/ubuclean-notifier.service" \
  /usr/lib/systemd/user/ubuclean-notifier.service

if [[ "$DRY_RUN" -eq 0 ]]; then
  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
  fi
  if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q /usr/share/icons/hicolor || true
  fi
fi

say "Done"
cat <<'EOF'
Run UbuClean from the application menu or type: ubuclean
The GUI must not be run as root; system operations use pkexec.
Enable the notification watcher from Settings or:
  systemctl --user daemon-reload
  systemctl --user enable --now ubuclean-notifier.service
EOF
