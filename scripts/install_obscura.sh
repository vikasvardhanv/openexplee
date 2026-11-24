#!/usr/bin/env bash
set -e

VERSION="v0.2.7"
ROOT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." >/dev/null 2>&1 && pwd )"
BIN_DIR="$ROOT_DIR/bin"
mkdir -p "$BIN_DIR"

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"

case "$OS" in
    darwin)
        if [ "$ARCH" = "arm64" ]; then
            TARGET="obscura-aarch64-macos-stealth"
        else
            TARGET="obscura-x86_64-macos-stealth"
        fi
        ;;
    linux)
        if [ "$ARCH" = "aarch64" ]; then
            TARGET="obscura-aarch64-linux-stealth"
        else
            TARGET="obscura-x86_64-linux-stealth"
        fi
        ;;
    *)
        echo "Unsupported OS: $OS"
        exit 1
        ;;
esac

TAR_NAME="${TARGET}.tar.gz"
DOWNLOAD_URL="https://github.com/h4ckf0r0day/obscura/releases/download/${VERSION}/${TAR_NAME}"

echo "[Obscura] Downloading $TARGET from $DOWNLOAD_URL..."
curl -fsSL -o "$BIN_DIR/$TAR_NAME" "$DOWNLOAD_URL"

echo "[Obscura] Extracting to $BIN_DIR..."
tar -xzf "$BIN_DIR/$TAR_NAME" -C "$BIN_DIR"
rm -f "$BIN_DIR/$TAR_NAME"
chmod +x "$BIN_DIR/obscura"

echo "[Obscura] Successfully installed $("$BIN_DIR/obscura" --version)"
