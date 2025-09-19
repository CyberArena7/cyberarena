#!/usr/bin/env sh
set -euo pipefail

SCP=${SCP:-scp}
SSH=${SSH:-ssh}

uv build
cd dist
NAME=$(echo repairdesk-*-py3-none-any.whl)

"$SCP" "$NAME" "$1:/tmp"
"$SSH" "$1" "sudo pip3 install --no-deps --break-system-packages --force-reinstall /tmp/$NAME"
