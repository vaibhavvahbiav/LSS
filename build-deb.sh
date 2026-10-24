#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VERSION=1.1.0
PACKAGE=linux-panic-button
BUILD_DIR=$(mktemp -d)
trap 'rm -rf "$BUILD_DIR"' EXIT HUP INT TERM

DEST="$BUILD_DIR/$PACKAGE"
install -d "$DEST/DEBIAN" "$DEST/usr/lib/$PACKAGE/templates" \
    "$DEST/usr/bin" "$DEST/lib/systemd/system"
install -m 0644 "$ROOT/debian/control" "$DEST/DEBIAN/control"
sed -i "s/^Version:.*/Version: $VERSION/" "$DEST/DEBIAN/control"
install -m 0644 "$ROOT/app.py" "$ROOT/panic.py" "$DEST/usr/lib/$PACKAGE/"
install -m 0644 "$ROOT/templates/index.html" "$DEST/usr/lib/$PACKAGE/templates/"
install -m 0755 "$ROOT/debian/linux-panic-button" "$DEST/usr/bin/linux-panic-button"
install -m 0755 "$ROOT/debian/linux-panic-button-dashboard" "$DEST/usr/bin/linux-panic-button-dashboard"
install -m 0644 "$ROOT/debian/linux-panic-button.service" \
    "$DEST/lib/systemd/system/linux-panic-button.service"

install -d "$ROOT/dist"
dpkg-deb --root-owner-group --build "$DEST" \
    "$ROOT/dist/${PACKAGE}_${VERSION}_all.deb"
