#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"
uv build

SCP=${SCP:-scp}         # permite inyectar opciones (puerto) desde el workflow
SSH=${SSH:-ssh}

uv build
cd dist

# captura el wheel generado
NAME=$(echo holded-*-py3-none-any.whl)

"$SCP" "$NAME" "$1:/tmp"
"$SSH" "$1" "sudo pip3 install --no-deps --break-system-packages --force-reinstall /tmp/$NAME"

