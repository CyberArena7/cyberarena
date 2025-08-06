#!/bin/sh

NAME=trade-in

mkdir -p dist

echo "Creando tarball"
tar cf dist/$NAME.tar *.py static templates

echo "Copiando archivos"
scp dist/$NAME.tar $NAME.service "$1:/tmp"

echo "Extrayendo en servidor"
ssh "$1" <<EOF
    if ! [ -d /opt/$NAME ]; then
        sudo install -o root -g sudo -m 0775 -d /opt/$NAME
    fi
    tar xf /tmp/$NAME.tar -C /opt/$NAME
    if ! [ -f /etc/systemd/system/$NAME.service ]; then
        sudo cp /tmp/$NAME.service /etc/systemd/system/$NAME.service
        sudo systemctl daemon-reload
        sudo systemctl enable $NAME.service
    fi
    sudo systemctl restart $NAME.service
EOF
