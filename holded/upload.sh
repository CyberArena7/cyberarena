#!/bin/sh
uv build
cd dist
NAME=(holded-*-py3-none-any.whl)
scp $NAME "$1:/tmp"
ssh $1 sudo pip3 install --no-deps --break-system-packages --force-reinstall /tmp/$NAME
