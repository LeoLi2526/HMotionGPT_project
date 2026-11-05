#!/usr/bin/env python3
"""Read-only serial capture for the Sichiray forearm armband.

The wire format is intentionally not decoded during capture. Both the vendor
HEX stream and the eight-integer text stream seen in supplied older code are
preserved byte-for-byte for later, hardware-backed protocol validation.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "sichiray"
FRAME_BYTES = 98  # Candidate layout in the vendor document, not yet verified.
TEXT_ROW = re.compile(rb"^\s*([+-]?\d+\s+){7}[+-]?\d+\s*$")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require_serial():
    try:
        import serial
    except ImportError as exc:
        raise SystemExit("pyserial is missing; install requirements-sichiray.txt") from exc
    return serial


def list_ports():
    require_serial()
    from serial.tools import list_ports as serial_ports

    ports = sorted(serial_ports.comports(), key=lambda p: p.device)
    if not ports:
        print("No serial ports detected. Pair the receiver, then try again.")
        return
    for port in ports:
        print(f"{port.device}\t{port.description}\t{port.hwid}")
    print("Select the armband port from the device/receiver details; this tool does not guess a port.")


def safe_label(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", value):
        raise SystemExit("--label must contain 1-48 ASCII letters, digits, _ or -")
    return value


def capture(args):
    serial = require_serial()
    label = safe_label(args.label)
    if not args.port or args.seconds <= 0 or args.baud <= 0:
        raise SystemExit("A port, positive duration, and positive baud rate are required")

    session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + label
    session = args.output_root / session_id
    session.mkdir(parents=True, exist_ok=False)
    raw_path = session / "serial_raw.bin"
    chunks_path = session / "receive_chunks.jsonl"
    metadata_path = session / "metadata.json"
    metadata = {
        "schema_version": 1,
        "device": "Sichiray 8-channel forearm EMG armband (hardware version unverified)",
        "transport": "serial_read_only",
        "port": args.port,
        "baud": args.baud,
        "serial_settings": "8N1, flow control off, no application data transmitted",
        "capture_started_utc": utc_now(),
        "requested_seconds": args.seconds,
        "host_clock": "UTC wall clock and monotonic clock sampled after each read",
        "device_timestamp_interpretation": "unknown",
        "protocol": "unknown; raw bytes retained",
        "status": "opening",
    }
    write_json(metadata_path, metadata)
    digest = hashlib.sha256()
    count = chunks = 0
    started = time.monotonic()
    port = None
    try:
        # Disable control lines before opening; never send a device command.
        port = serial.serial_for_url(args.port, do_not_open=True)
        port.baudrate = args.baud
        port.bytesize = serial.EIGHTBITS
        port.parity = serial.PARITY_NONE
        port.stopbits = serial.STOPBITS_ONE
        port.timeout = 0.2
        port.xonxoff = False
        port.rtscts = False
        port.dsrdtr = False
        port.dtr = False
        port.rts = False
        port.open()
        metadata["status"] = "capturing"
        write_json(metadata_path, metadata)
        with raw_path.open("wb") as raw, chunks_path.open("w", encoding="utf-8") as index:
            deadline = time.monotonic() + args.seconds
            while time.monotonic() < deadline:
                available = port.in_waiting
                data = port.read(max(1, min(available, 65536)))
                monotonic_ns = time.monotonic_ns()
                utc_ns = time.time_ns()
                if not data:
                    continue
                raw.write(data)
                digest.update(data)
                index.write(json.dumps({
                    "offset": count,
                    "bytes": len(data),
                    "host_receive_utc_ns": utc_ns,
                    "host_receive_monotonic_ns": monotonic_ns,
                }) + "\n")
                count += len(data)
                chunks += 1
            raw.flush()
            index.flush()
        metadata["status"] = "completed"
    except KeyboardInterrupt:
        metadata["status"] = "interrupted"
    except (OSError, serial.SerialException) as exc:
        metadata["status"] = "error"
        metadata["error"] = f"{type(exc).__name__}: {exc}"
        print(metadata["error"], file=sys.stderr)
    finally:
        if port is not None and port.is_open:
            port.close()
        metadata.update({
            "capture_ended_utc": utc_now(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "raw_bytes": count,
            "receive_chunks": chunks,
            "serial_raw_sha256": digest.hexdigest() if raw_path.exists() else None,
        })
        write_json(metadata_path, metadata)
    print(f"Session: {session}")
    print(f"Status: {metadata['status']}; bytes: {count}; chunks: {chunks}")
    if count == 0:
        print("No bytes received. Check port, pairing, streaming mode, and baud rate.")
    return 0 if metadata["status"] == "completed" and count > 0 else 1


def inspect(args):
    path = args.path
    if path.is_dir():
        path = path / "serial_raw.bin"
    if not path.is_file():
        raise SystemExit(f"Raw capture not found: {path}")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    text_lines = raw.splitlines()
    valid_text = [line for line in text_lines if TEXT_ROW.fullmatch(line)]
    lengths = Counter()
    ticks = []
    offsets = []
    i = 0
    while i <= len(raw) - FRAME_BYTES:
        if raw[i:i + 2] == b"\xaa\xaa" and raw[i + FRAME_BYTES - 1] == 0x55:
            lengths[raw[i + 2]] += 1
            ticks.append(int.from_bytes(raw[i + 3:i + 7], "big"))
            offsets.append(i)
            i += FRAME_BYTES
        else:
            i += 1
    result = {
        "file": str(path.resolve()),
        "bytes": len(raw),
        "sha256": digest,
        "eight_integer_text_lines": len(valid_text),
        "candidate_98_byte_hex_frames": len(offsets),
        "candidate_length_byte_counts": dict(sorted(lengths.items())),
        "candidate_first_offsets": offsets[:5],
        "candidate_first_device_ticks_big_endian": ticks[:5],
        "interpretation": "candidate only; verify stream, timestamp unit and EMG encoding on hardware",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("ports", help="List available serial ports without opening them")
    capture_parser = commands.add_parser("capture", help="Save raw bytes and host read times")
    capture_parser.add_argument("--port", required=True, help="Explicit COM port, e.g. COM7")
    capture_parser.add_argument("--baud", type=int, default=115200,
                                help="Candidate from supplied legacy code; verify on hardware")
    capture_parser.add_argument("--seconds", type=float, default=60)
    capture_parser.add_argument("--label", default="test")
    capture_parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    inspect_parser = commands.add_parser("inspect", help="Inspect a saved capture without changing it")
    inspect_parser.add_argument("path", type=Path, help="Session directory or serial_raw.bin")
    args = parser.parse_args()
    if args.command == "ports":
        list_ports()
        return 0
    if args.command == "capture":
        return capture(args)
    return inspect(args)


if __name__ == "__main__":
    raise SystemExit(main())
