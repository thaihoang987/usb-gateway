# USB Gateway architecture

## Project status

USB Gateway is experimental software. Test each serial device and TCP client
carefully before relying on it for production workloads.

## Goal

Run a separate Unraid Docker app named `usb-gateway`. It does not replace or
modify the existing `mbusd-gateway` container or Home Assistant USB Manager
add-on. A port is only claimed after it is enabled in this app.

## Runtime model

One supervisor process owns the Web UI, persisted configuration and workers.
Each enabled port has exactly one worker:

- `modbus`: starts one foreground `mbusd` process. `mbusd` converts standard
  Modbus TCP (MBAP) to Modbus RTU and serializes access from TCP masters.
- `raw`: opens the UART with pyserial and forwards bytes unchanged between the
  serial device and all connected TCP clients. Writes are locked so client
  payloads cannot interleave.

The app uses host networking. Docker cannot add a new published port to an
already-running bridge container, while USB Gateway must be able to add a TCP
listener immediately from the UI.

## Ownership and isolation

- Enabled ports must have unique resolved device paths and unique TCP ports.
- The container is separate from existing production services.
- Do not enable a device here while USB Manager, Node-RED serial or another
  process owns that same UART.
- Configuration is written atomically to `/config/config.json`.
- A missing USB leaves its worker in `waiting`; hot-plugging it starts the
  gateway without editing the configuration.

## Raw UART behavior

Raw mode does not parse text, JSON, delimiters or checksums. Commands such as
`Read_Value:T3T4$` and responses from Arduino are transferred byte-for-byte.
DTR and RTS default to off and are set before opening the serial port to avoid
repeated Arduino auto-reset.

## API

- `GET /api/ports`, `POST /api/ports`
- `PUT /api/ports/{id}`, `DELETE /api/ports/{id}`
- `POST /api/ports/{id}/restart`, `POST /api/ports/{id}/test`
- `GET /api/ports/{id}/logs`
- `GET /api/devices`, `GET /api/health`

The first release is intended for a trusted LAN. Do not expose the Web UI to
the public Internet without an authenticated reverse proxy.
