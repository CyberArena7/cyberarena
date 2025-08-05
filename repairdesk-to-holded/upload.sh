#!/bin/sh

mkdir -p dist

echo "Creando tarball"
tar cf dist/bridge.tar *.py static templates

echo "Copiando archivos"
scp dist/bridge.tar repairdesk-to-holded.service "$1:/tmp"

echo "Extrayendo en servidor"
ssh "$1" <<EOF
    if ! [ -d /opt/bridge ]; then
        sudo install -o root -g sudo -m 0775 -d /opt/bridge
    fi
    tar xf /tmp/bridge.tar -C /opt/bridge
    if ! [ -f /etc/systemd/system/repairdesk-to-holded.service ]; then
        sudo cp /tmp/repairdesk-to-holded.service /etc/systemd/system/repairdesk-to-holded.service
        sudo systemctl daemon-reload
        sudo systemctl enable repairdesk-to-holded.service
    fi
    sudo systemctl restart repairdesk-to-holded.service
EOF
