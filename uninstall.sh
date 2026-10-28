#!/usr/bin/env bash
# UbuClean uninstaller.
set -euo pipefail

PURGE=0
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    -h|--help)
      cat <<'EOF'
Usage: sudo ./uninstall.sh [--purge]

  --purge  Also delete the user's configuration, logs, and quarantine.
           Without this flag, user data is preserved.
EOF
      exit 0
      ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo ./uninstall.sh" >&2
  exit 1
fi

echo "==> Disabling user schedule units (if present)"
TARGET_HOME=""
if [[ -n "${SUDO_USER:-}" ]]; then
  TARGET_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6 || true)"
fi
if [[ -n "$TARGET_HOME" ]]; then
  rm -f "$TARGET_HOME/.config/systemd/user/ubuclean-clean.timer" \
        "$TARGET_HOME/.config/systemd/user/ubuclean-clean.service" \
        "$TARGET_HOME/.config/systemd/user/ubuclean-notifier.service"
  if [[ "$PURGE" -eq 1 ]]; then
    rm -rf "$TARGET_HOME/.config/ubuclean" \
           "$TARGET_HOME/.local/state/ubuclean" \
           "$TARGET_HOME/.local/share/ubuclean"
  fi
fi

echo "==> Removing system files"
rm -f /usr/bin/ubuclean /usr/bin/ubuclean-helper
rm -rf /usr/lib/ubuclean
rm -f /usr/share/applications/com.ubuclean.App.desktop
rm -f /usr/share/icons/hicolor/scalable/apps/com.ubuclean.App.svg
rm -f /usr/share/polkit-1/actions/com.ubuclean.helper.policy
rm -f /usr/lib/systemd/user/ubuclean-notifier.service

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q /usr/share/icons/hicolor || true

if [[ "$PURGE" -eq 0 ]]; then
  echo "User data (configuration, logs, quarantine) preserved. Use --purge to delete it."
else
  echo "User data deleted as well."
fi
echo "Done. Run 'systemctl --user daemon-reload' as the user if needed."
