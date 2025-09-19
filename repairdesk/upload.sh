#!/usr/bin/env sh
set -eu

# Movernos a la carpeta del script (repairdesk/)
cd "$(dirname "$0")"

SCP=${SCP:-scp}
SSH=${SSH:-ssh}

# Construir el wheel (sin -C)
uv build

cd dist
NAME=$(echo repairdesk-*-py3-none-any.whl)

$SCP "$NAME" "$1:/tmp"
$SSH "$1" "sudo pip3 install --no-deps --break-system-packages --force-reinstall /tmp/$NAME"
