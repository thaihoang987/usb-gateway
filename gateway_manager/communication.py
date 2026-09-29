from __future__ import annotations

import socket
import struct
import time

from .config import ConfigError


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return crc


def number(payload, key, default, low, high):
    try:
        value = int(payload.get(key, default))
    except (ValueError, TypeError):
        raise ConfigError(f"{key} must be an integer")
    if not low <= value <= high:
        raise ConfigError(f"{key} must be between {low} and {high}")
    return value


def decimal_number(payload, key, default, low, high):
    try:
        value = float(payload.get(key, default))
    except (ValueError, TypeError):
        raise ConfigError(f"{key} must be a number")
    if not low <= value <= high:
        raise ConfigError(f"{key} must be between {low} and {high}")
    return value


def build_request(config, payload):
    action = payload.get("action", "listen")
    if action == "listen":
        if config["mode"] != "raw":
            raise ConfigError("Passive listening requires a Raw TCP gateway; mbusd only returns replies to requests")
        return b""
    if action == "modbus":
        unit = number(payload, "unit", 1, 1, 247)
        function = number(payload, "function", 3, 1, 127)
        address = number(payload, "address", 0, 0, 65535)
        if payload.get("custom_body") or function not in {1, 2, 3, 4, 5, 6}:
            body = payload.get("body", "")
            if not isinstance(body, str) or len(body) > 2048:
                raise ConfigError("Invalid function data")
            try:
                data = bytes.fromhex(body)
            except ValueError:
                raise ConfigError("Function data must contain HEX byte pairs")
            if len(data) > 252:
                raise ConfigError("Function data exceeds 252 bytes")
            frame = bytes([unit, function]) + data
        elif function in {5, 6}:
            value = number(payload, "value", 0, 0, 65535)
            if function == 5 and value not in {0, 65280}:
                raise ConfigError("FC05 value must be 0 (OFF) or 65280 (ON)")
            frame = struct.pack(">BBHH", unit, function, address, value)
        else:
            quantity = number(payload, "quantity", 1, 1, 2000 if function < 3 else 125)
            if address + quantity > 65536:
                raise ConfigError("Address range exceeds 65535")
            frame = struct.pack(">BBHH", unit, function, address, quantity)
        if config["mode"] == "modbus":
            return struct.pack(">HHH", 1, 0, len(frame)) + frame
        return frame + struct.pack("<H", crc16(frame))
    if action not in {"hex", "text"}:
        raise ConfigError("Unknown communication action")
    if config["mode"] != "raw":
        raise ConfigError("HEX/text send requires a Raw TCP gateway")
    value = payload.get("data", "")
    if not isinstance(value, str) or len(value) > 8192:
        raise ConfigError("Data must be text up to 8192 characters")
    try:
        data = bytes.fromhex(value) if action == "hex" else value.encode("utf-8")
    except ValueError:
        raise ConfigError("Invalid HEX: use byte pairs such as 01 03 00 00")
    if not data or len(data) > 4096:
        raise ConfigError("Send 1 to 4096 bytes")
    return data


def exchange(config, payload):
    duration = decimal_number(payload, "duration", 2, 0.05, 10)
    outgoing = build_request(config, payload)
    started = time.monotonic()
    chunks = []
    total = 0
    error = None
    try:
        with socket.create_connection(("127.0.0.1", config["tcp_port"]), timeout=2) as client:
            if outgoing:
                client.sendall(outgoing)
            deadline = time.monotonic() + duration
            while total < 65536:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                client.settimeout(remaining)
                try:
                    data = client.recv(min(4096, 65536 - total))
                except socket.timeout:
                    break
                if not data:
                    error = "Gateway closed the TCP connection"
                    break
                total += len(data)
                chunks.append({"ms": round((time.monotonic() - started) * 1000), "hex": data.hex(" "), "text": data.decode("utf-8", errors="replace")})
    except OSError as exc:
        error = str(exc)
    return {"tx_hex": outgoing.hex(" "), "rx": chunks, "received_bytes": total,
            "elapsed_ms": round((time.monotonic() - started) * 1000), "error": error,
            "limit_reached": total >= 65536}
