#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
IMAGE_NAME=${IMAGE_NAME:-ghcr.io/thaihoang987/usb-gateway:latest}
TEMPLATE_TARGET=/boot/config/plugins/dockerMan/templates-user/my-usb-gateway.xml
APPDATA_DIR=/mnt/user/appdata/usb-gateway

if [ "${BUILD_LOCAL:-0}" = "1" ]; then
    echo "Building $IMAGE_NAME ..."
    docker build -t "$IMAGE_NAME" "$SCRIPT_DIR"
else
    echo "Pulling $IMAGE_NAME ..."
    docker pull "$IMAGE_NAME"
fi

mkdir -p "$APPDATA_DIR"
mkdir -p "$(dirname "$TEMPLATE_TARGET")"

if [ -f "$TEMPLATE_TARGET" ]; then
    cp -a "$TEMPLATE_TARGET" "$TEMPLATE_TARGET.bak"
fi
cp "$SCRIPT_DIR/unraid/my-usb-gateway.xml" "$TEMPLATE_TARGET"
chmod 600 "$TEMPLATE_TARGET"

echo
echo "USB Gateway image and Unraid template are ready."
echo "Image:    $IMAGE_NAME"
echo "Template: $TEMPLATE_TARGET"
echo "Appdata:  $APPDATA_DIR"
echo
echo "Open Unraid Docker -> Add Container -> usb-gateway -> Apply."
echo "Use a new TCP port while an existing gateway still owns its USB or port."
