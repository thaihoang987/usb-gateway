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
- All gateway bindings use `/dev/serial/by-path` (USB topology). By-id, serial numbers and tty names are diagnostic information only.
- Legacy bindings using by-id or tty are retained but disabled on load; select a physical USB port and enable them again. No automatic identity-based fallback is used.
- Keep the USB controller, hub and physical cabling unchanged to preserve topology. Moving a device to another port does not move its gateway binding.
- Notes for each gateway describe the USB device and its purpose.
- Saved gateways remain visible in gray when their USB is unplugged, including after app restarts. Notes and settings are retained until explicitly deleted.
- Persistent configuration at `/mnt/user/appdata/usb-gateway/config.json`.
- Automatic gateway recovery after Docker or Unraid restarts.
- Live Raw gateway TX/RX byte and chunk totals in the gateway list.

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

The template loads `icon.png` from this GitHub repository. Its WebUI shortcut opens port 8098; update the WebUI URL too if you change WEB_PORT.

The Communication tab supports bounded passive UART captures, HEX/UTF-8 sends on Raw gateways, and Modbus FC01–04 reads on either gateway mode. Results show TX/RX bytes and timing; TCP chunks are not decoded Modbus frames. Raw captures can include other clients’ responses.

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

Raw traffic counters are kept for the lifetime of the container and survive
gateway enable/disable or configuration edits. They reset when the container
restarts. Modbus counters are shown as unavailable because traffic passes
through `mbusd` and cannot be counted reliably without verbose debug logging.

## Development

```bash
docker compose up --build
python -m unittest discover -s tests -v
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for implementation details.

## USB reconnect in Docker

With the existing privileged container and read-only /dev/serial mount, workers recreate a missing ttyUSB/ttyACM device node in the container’s private /dev using the configured by-path symlink and live USB serial metadata from sysfs. Only serial majors 188/166 are accepted; existing nodes are never replaced. No whole-host /dev mount is needed. Waiting and node restoration are logged. The USB must return at the saved topology. Update the container image to receive this fix; TCP clients must reconnect after interruption.

Communication accepts function codes 1–127 in decimal, with an optional custom HEX body after the function byte. FC05/06 support address/value entry. Other functions use the HEX body; CRC or MBAP is added for the gateway mode. Named form templates are persisted in /config/communication-presets.json; loading a template never sends it. Use Run to send after reviewing the selected gateway and values.
