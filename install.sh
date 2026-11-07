#!/usr/bin/env bash
# Install NEXUS as a `nexus` command (Ubuntu/Debian-friendly, no root needed).
#
#   git clone https://github.com/jake1402/nexus.git && cd nexus && ./install.sh
#
# Copies the single-file app to ~/.local/bin/nexus. Override the target
# directory with NEXUS_BIN_DIR=/some/dir ./install.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$SCRIPT_DIR/nexus.py"
DEST_DIR="${NEXUS_BIN_DIR:-$HOME/.local/bin}"

if [ ! -f "$SRC" ]; then
    echo "error: $SRC not found — run install.sh from the nexus repo root" >&2
    exit 1
fi

mkdir -p "$DEST_DIR"
cp "$SRC" "$DEST_DIR/nexus"
chmod +x "$DEST_DIR/nexus"

case ":$PATH:" in
    *":$DEST_DIR:"*) ;;
    *) echo "note: $DEST_DIR is not on your PATH. Add it to ~/.bashrc with:"
       echo "  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

echo "NEXUS installed -> $DEST_DIR/nexus"
echo "Run it with: nexus"
