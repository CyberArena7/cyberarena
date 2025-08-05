#!/bin/sh

mkdir -p dist

echo "Creando tarball"
tar cf dist/purchase-order.tar main.py providers static templates

echo "Copiando archivos"
scp dist/purchase-order.tar purchase-order.service "$1:/tmp"

echo "Extrayendo en servidor"
ssh "$1" <<EOF
    if ! [ -d /opt/purchase-order ]; then
        sudo install -o root -g sudo -m 0775 -d /opt/purchase-order
    fi
    tar xf /tmp/purchase-order.tar -C /opt/purchase-order
    if ! [ -f /etc/systemd/system/purchase-order.service ]; then
        sudo cp /tmp/purchase-order.service /etc/systemd/system/purchase-order.service
        sudo systemctl daemon-reload
        sudo systemctl enable --now purchase-order.service
    fi
    sudo systemctl restart purchase-order.service
EOF
