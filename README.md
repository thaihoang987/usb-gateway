# USB Gateway

> [!WARNING]
> **Experimental software.** Test each USB device and TCP client carefully
> before relying on USB Gateway for production workloads.

<a href="https://buymeacoffee.com/leon_bell" target="_blank"><img src="https://img.buymeacoffee.com/button-api/?text=Buy%20me%20a%20beer&emoji=%F0%9F%8D%BA&slug=leon_bell&button_colour=FFDD00&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=ffffff" alt="Buy me a beer" height="50"></a>
<a href="https://ko-fi.com/leonbell" target="_blank"><img src="https://ko-fi.com/img/githubbutton_sm.svg" alt="Support me on Ko-fi" height="50"></a>
<a href="https://paypal.me/leonbell95" target="_blank"><img src="https://img.shields.io/badge/PayPal-Donate-00457C?style=for-the-badge&logo=paypal&logoColor=white" alt="Donate with PayPal" height="50"></a>

🍺 [Buy me a beer](https://buymeacoffee.com/leon_bell) · ☕ [Ko-fi](https://ko-fi.com/leonbell) · 💙 [PayPal](https://paypal.me/leonbell95)

USB Gateway is a standalone Unraid Docker app for managing multiple USB
serial gateways from one Web UI:

- Modbus TCP to Modbus RTU through one `mbusd` process per USB device.
- Raw TCP to UART for Arduino, text, JSON, and custom binary protocols.
- Discovery through `/dev/serial/by-id` and `/dev/serial/by-path`.
- Persistent configuration at `/mnt/user/appdata/usb-gateway/config.json`.
- Automatic gateway recovery after Docker or Unraid restarts.

USB Gateway does not modify the existing `mbusd-gateway` container or replace
another USB management service. Do not enable the same USB device in multiple
services at the same time.

Author: [thaihoang987](https://github.com/thaihoang987)

## Install on Unraid with the template

Run this once in the Unraid terminal:

```bash
mkdir -p /boot/config/plugins/dockerMan/templates-user
wget -O /boot/config/plugins/dockerMan/templates-user/my-usb-gateway.xml \
  https://raw.githubusercontent.com/thaihoang987/usb-gateway/master/unraid/my-usb-gateway.xml
```

Open **Docker -> Add Container**, select the `usb-gateway` template, review the
settings, and click **Apply**. Unraid will automatically pull
`ghcr.io/thaihoang987/usb-gateway:latest`.

The default Web UI address is `http://UNRAID_IP:8098`.

The template uses this local icon file:
`/mnt/user/App_Custom/Icon_app/usb manager.png`.

## Install with the helper script

```bash
git clone https://github.com/thaihoang987/usb-gateway.git /tmp/usb-gateway
cd /tmp/usb-gateway
sh install-unraid.sh
```

The script pulls the image, creates the appdata directory, and installs the
DockerMan template. It does not stop, delete, or modify an existing gateway
container.

To build the image directly on Unraid instead:

```bash
BUILD_LOCAL=1 IMAGE_NAME=ghcr.io/thaihoang987/usb-gateway:latest sh install-unraid.sh
```

## Run directly

```bash
docker run -d \
  --name usb-gateway \
  --network host \
  --privileged \
  --restart unless-stopped \
  -e WEB_PORT=8098 \
  -e CONFIG_PATH=/config/config.json \
  -v /mnt/user/appdata/usb-gateway:/config \
  -v /dev/serial:/dev/serial:ro \
  ghcr.io/thaihoang987/usb-gateway:latest
```

## Safe migration

1. Install USB Gateway without adding or enabling any gateways.
2. Add one USB device that is not used by another process.
3. Test the TCP listener in the Web UI.
4. Connect Node-RED or another client to the Unraid IP and selected TCP port.
5. Stop the old gateway only after the new connection is verified.

Do not use TCP port `8888` while an existing `mbusd-gateway` still listens on
that port. Use a temporary port such as `8890` or `8891` during testing.

Raw mode transfers bytes unchanged and does not parse messages, delimiters, or
checksums. DTR and RTS are disabled by default to reduce unwanted Arduino
resets when the serial port is opened.

## Development

```bash
docker compose up --build
python -m unittest discover -s tests -v
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for implementation details.
