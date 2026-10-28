#!/usr/bin/env bash
# Build the UbuClean .deb package without root privileges.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(grep -oP '(?<=^version = ")[^"]+' "$ROOT/pyproject.toml" | head -1)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

echo "==> Preparing staging directory $STAGE"
install -d "$STAGE/DEBIAN" \
  "$STAGE/usr/lib/ubuclean/bin" \
  "$STAGE/usr/bin" \
  "$STAGE/usr/share/applications" \
  "$STAGE/usr/share/icons/hicolor/scalable/apps" \
  "$STAGE/usr/share/polkit-1/actions" \
  "$STAGE/usr/lib/systemd/user"

cp -a "$ROOT/ubuclean" "$STAGE/usr/lib/ubuclean/"
find "$STAGE/usr/lib/ubuclean" -type d -name __pycache__ -prune -exec rm -rf {} +
install -m0755 "$ROOT/bin/ubuclean" "$STAGE/usr/lib/ubuclean/bin/ubuclean"
install -m0755 "$ROOT/bin/ubuclean-helper" "$STAGE/usr/lib/ubuclean/bin/ubuclean-helper"

cat > "$STAGE/usr/bin/ubuclean" <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /usr/lib/ubuclean/bin/ubuclean "$@"
EOF
cat > "$STAGE/usr/bin/ubuclean-helper" <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /usr/lib/ubuclean/bin/ubuclean-helper "$@"
EOF
chmod 0755 "$STAGE/usr/bin/ubuclean" "$STAGE/usr/bin/ubuclean-helper"

install -m0644 "$ROOT/ubuclean/data/com.ubuclean.App.desktop" "$STAGE/usr/share/applications/"
install -m0644 "$ROOT/ubuclean/data/icons/hicolor/scalable/apps/com.ubuclean.App.svg" \
  "$STAGE/usr/share/icons/hicolor/scalable/apps/"
install -m0644 "$ROOT/ubuclean/data/com.ubuclean.helper.policy" "$STAGE/usr/share/polkit-1/actions/"
install -m0644 "$ROOT/ubuclean/data/ubuclean-notifier.service" "$STAGE/usr/lib/systemd/user/"

cat > "$STAGE/DEBIAN/control" <<EOF
Package: ubuclean
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.12), python3-gi, python3-gi-cairo, python3-psutil, gir1.2-gtk-4.0, gir1.2-adw-1, libnotify-bin, psmisc, policykit-1
Maintainer: UbuClean <noreply@ubuclean.local>
Homepage: https://github.com/ubuclean/ubuclean
Description: System cleaner and maintenance for Ubuntu Desktop
 UbuClean provides a system cache and junk cleaner with previews,
 a RAM cache cleaner, a system monitor, disk analysis, a duplicate finder,
 a startup manager, systemd cleanup scheduling, a restorable quarantine,
 an application uninstaller, and smart notifications.
EOF

cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ -x /usr/bin/update-desktop-database ]; then
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi
if [ -x /usr/bin/gtk-update-icon-cache ]; then
  gtk-update-icon-cache -q /usr/share/icons/hicolor || true
fi
exit 0
EOF
chmod 0755 "$STAGE/DEBIAN/postinst"

mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/ubuclean_${VERSION}_all.deb"
dpkg-deb --root-owner-group --build "$STAGE" "$OUT"
echo "==> Package created: $OUT"
