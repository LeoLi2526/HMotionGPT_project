#!/usr/bin/env python3
"""Agent-friendly episode CLI. Host timestamps are never device sample times."""

import argparse
from bisect import bisect_right
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
import uuid

import sichiray_capture

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PROJECT_ROOT / "data" / "episodes"
PURPOSES = ("engineering", "pilot", "research")
RING_MOUNTS = ("bare_board", "worn", "unknown")
RING_POWERS = ("tethered", "battery", "unknown")


def identifier(value):
    reserved = {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10)
    }
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) or value.upper() in reserved:
        raise ValueError("IDs require 1-64 ASCII letters, digits, _ or -; no Windows reserved names")
    return value


def write_new_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def stamp():
    return {"host_utc_ns": time.time_ns(), "host_monotonic_ns": time.monotonic_ns()}


def create_episode_directory(root, ids):
    parent = root.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    for index, name in enumerate(ids):
        for existing in parent.iterdir():
            if existing.name.casefold() == name.casefold() and existing.name != name:
                raise ValueError(f"ID spelling conflict: {name!r} matches existing {existing.name!r} in {parent}")
        parent = parent / name
        parent.mkdir(exist_ok=index < len(ids) - 1)
    return parent


def create(args):
    ids = [identifier(getattr(args, name)) for name in ("subject", "session", "trial")]
    if not args.task.strip() or not args.consent_scope.strip():
        raise ValueError("task and consent scope must be nonempty")
    episode = create_episode_directory(args.root, ids)
    metadata = {
        "schema_version": 1, "subject": ids[0], "session": ids[1], "trial": ids[2],
        "task": args.task, "wear_side": args.wear_side, "notes": args.notes,
        "consent_scope": args.consent_scope, "consent_basis": "operator_declaration",
        "created": stamp(), "device_clock_source": dict.fromkeys(("armband", "camera", "ring"), "unknown"),
        "synchronization": "unverified",
        "acquisition_context": {
            "purpose": getattr(args, "purpose", "engineering"),
            "ring_mount": getattr(args, "ring_mount", "unknown"),
            "ring_power": getattr(args, "ring_power", "unknown"), "wear_calibration": "unverified",
        },
    }
    write_new_json(episode / "episode.json", metadata)
    return {"episode": str(episode), "metadata": metadata}


def require_episode(path):
    path = path.resolve()
    metadata = read_json(path / "episode.json")
    if metadata.get("schema_version") != 1:
        raise ValueError("Unsupported episode schema")
    return path


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def mark(args):
    received = stamp()
    episode = require_episode(args.episode)
    if not args.event.strip():
        raise ValueError("event must be nonempty")
    directory = episode / "events"
    directory.mkdir(exist_ok=True)
    event = {"schema_version": 1, "event_id": uuid.uuid4().hex,
             "event": args.event, "source": args.source, "notes": args.notes, **received,
             "time_semantics": "host CLI event handling; not keypress or device sampling time",
             "monotonic_scope": "same host and boot only", "hardware_sync": False}
    write_new_json(directory / (event["event_id"] + ".json"), event)
    return event


def attach(args):
    episode = require_episode(args.episode)
    source = args.file.resolve()
    if not source.is_file():
        raise ValueError("Attachment must be a regular file")
    directory = episode / "attachments" / args.modality / identifier(args.attachment_id)
    expected = sha256(source)
    directory.mkdir(parents=True, exist_ok=False)
    # Use a fixed raw name so an input named attachment.json cannot collide with metadata.
    target = directory / ("raw" + source.suffix)
    with source.open("rb") as incoming, target.open("xb") as outgoing:
        for block in iter(lambda: incoming.read(1024 * 1024), b""):
            outgoing.write(block)
    digest = sha256(target)
    if digest != expected or sha256(source) != expected:
        raise ValueError("Source changed while copying; partial evidence retained, use a new attachment ID")
    record = {"schema_version": 1, "modality": args.modality,
              "attachment_id": args.attachment_id, "original_name": source.name,
              "raw_file": target.name, "bytes": target.stat().st_size, "sha256": digest,
              "device_clock_source": "unknown", "imported": stamp(),
              "import_time_semantics": "host file import, not acquisition or device time"}
    write_new_json(directory / "attachment.json", record)
    return {"attachment": str(directory), **record}


def capture(args):
    episode = require_episode(args.episode)
    capture_id = identifier(args.capture_id)
    if not re.fullmatch(r"COM[1-9][0-9]*", args.port, re.IGNORECASE):
        raise ValueError("Specify a COM port explicitly, for example COM6")
    if not math.isfinite(args.seconds) or args.seconds <= 0 or args.baud <= 0:
        raise ValueError("seconds must be finite and positive; baud must be positive")
    directory = episode / "captures" / capture_id
    directory.mkdir(parents=True, exist_ok=False)
    request = {"schema_version": 1, "capture_id": capture_id, "port": args.port,
               "baud": args.baud, "requested_seconds": args.seconds, "started": stamp(),
               "backend": "scripts/sichiray_capture.py", "device_clock_source": "unknown"}
    write_new_json(directory / "request.json", request)
    error = None
    code = 1
    # All serial handling stays in the existing capture module, including close-on-error.
    with (directory / "stdout.log").open("x", encoding="utf-8") as out, \
            (directory / "stderr.log").open("x", encoding="utf-8") as err:
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = sichiray_capture.capture(argparse.Namespace(
                    port=args.port, baud=args.baud, seconds=args.seconds,
                    label="episode", output_root=directory / "source"))
            except (Exception, SystemExit, KeyboardInterrupt) as exc:
                error = f"{type(exc).__name__}: {exc}"
                print(error, file=sys.stderr)
    result = {"schema_version": 1, "capture_id": capture_id, "capture": str(directory),
              "exit_code": code, "ended": stamp(), "error": error}
    write_new_json(directory / "result.json", result)
    return result


def capture_source(episode, capture_id):
    directory = episode / "captures" / identifier(capture_id)
    result = read_json(directory / "result.json")
    sources = list((directory / "source").glob("*/metadata.json"))
    if len(sources) != 1:
        raise ValueError("Expected one saved capture source; inspect capture logs")
    source = sources[0].parent
    return source, read_json(sources[0]), result


def load_chunks(path, raw_size):
    chunks = []
    offset = 0
    previous_mono = None
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            chunk = json.loads(line)
            keys = ("offset", "bytes", "host_receive_utc_ns", "host_receive_monotonic_ns")
            if any(type(chunk.get(key)) is not int for key in keys):
                raise ValueError("Chunk offsets, lengths and timestamps must be integer ns/bytes")
            if chunk["offset"] != offset or chunk["bytes"] <= 0:
                raise ValueError("Chunk index must cover raw bytes contiguously without overlaps")
            mono = chunk["host_receive_monotonic_ns"]
            if mono < 0 or chunk["host_receive_utc_ns"] < 0 or (previous_mono is not None and mono < previous_mono):
                raise ValueError("Invalid or decreasing monotonic chunk timestamps")
            previous_mono = mono
            offset += chunk["bytes"]
            chunks.append(chunk)
    if offset != raw_size:
        raise ValueError("Chunk coverage does not match raw byte count")
    return chunks


def derive(args):
    episode = require_episode(args.episode)
    source, metadata, result = capture_source(episode, args.capture_id)
    raw_path = source / "serial_raw.bin"
    chunk_path = source / "receive_chunks.jsonl"
    raw_hash = sha256(raw_path)
    if raw_hash != metadata.get("serial_raw_sha256"):
        raise ValueError("Raw SHA-256 mismatch with capture metadata")
    chunks = load_chunks(chunk_path, raw_path.stat().st_size)
    starts = [chunk["offset"] for chunk in chunks]
    directory = episode / "derived" / identifier(args.derive_id)
    directory.mkdir(parents=True, exist_ok=False)
    fields = ["row_index", "raw_offset", "raw_bytes", "column_count", "first_chunk_index",
              "completion_chunk_index", "host_receive_completion_utc_ns",
              "host_receive_completion_monotonic_ns", "device_time_ns", "device_clock_source",
              "qc_flags"] + [f"value_{i}" for i in range(1, 15)]
    invalid, fragments, changes = [], [], []
    widths = Counter()
    previous_width = None
    offset = 0
    with raw_path.open("rb") as raw, (directory / "rows.csv").open("x", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for row_index, line in enumerate(raw):
            first_chunk = bisect_right(starts, offset) - 1
            last_chunk = bisect_right(starts, offset + len(line) - 1) - 1
            location = {"row_index": row_index, "raw_offset": offset, "raw_bytes": len(line),
                        "first_chunk_index": first_chunk, "last_chunk_index": last_chunk}
            flags = ["device_clock_unknown", "field_meanings_unverified"]
            if offset == 0:
                flags.append("start_boundary_unverified")
            if first_chunk != last_chunk:
                flags.append("cross_chunk")
            offset += len(line)
            if not line.endswith(b"\n"):
                fragments.append({**location, "reason": "unterminated_tail", "raw_hex": line.hex(),
                                  "host_last_byte_receive_utc_ns": chunks[last_chunk]["host_receive_utc_ns"],
                                  "host_last_byte_receive_monotonic_ns": chunks[last_chunk]["host_receive_monotonic_ns"]})
                continue
            values = line.split()
            if len(values) not in (8, 9, 14) or not all(sichiray_capture.INTEGER.fullmatch(v) for v in values):
                entry = {**location, "reason": "unsupported_width_or_noninteger", "raw_hex": line.hex()}
                invalid.append(entry)
                if row_index == 0:
                    fragments.append({**entry, "reason": "possible_leading_fragment"})
                continue
            width = len(values)
            widths[width] += 1
            if previous_width is not None and width != previous_width:
                changes.append({"row_index": row_index, "raw_offset": location["raw_offset"],
                                "from_width": previous_width, "to_width": width})
                flags.append("column_width_change")
            previous_width = width
            last = chunks[last_chunk]
            row = {key: location[key] for key in ("row_index", "raw_offset", "raw_bytes", "first_chunk_index")}
            row.update({"column_count": width, "completion_chunk_index": last_chunk,
                        "host_receive_completion_utc_ns": last["host_receive_utc_ns"],
                        "host_receive_completion_monotonic_ns": last["host_receive_monotonic_ns"],
                        "device_time_ns": "", "device_clock_source": "unknown", "qc_flags": ";".join(flags)})
            row.update({f"value_{i}": value.decode("ascii") for i, value in enumerate(values, 1)})
            writer.writerow(row)
    qc = ["device_clock_unknown", "synchronization_unverified", "field_meanings_unverified"]
    if raw_path.stat().st_size:
        qc.append("start_boundary_unverified")
    if len(widths) > 1:
        qc.append("mixed_column_widths")
    if invalid:
        qc.append("invalid_lines")
    if fragments:
        qc.append("fragments_present")
    if not widths:
        qc.append("no_valid_rows")
    if result.get("exit_code") != 0 or metadata.get("status") != "completed":
        qc.append("capture_incomplete")
    if any(b["host_receive_utc_ns"] < a["host_receive_utc_ns"] for a, b in zip(chunks, chunks[1:])):
        qc.append("host_utc_regression")
    report = {"schema_version": 1, "capture_id": args.capture_id, "derive_id": args.derive_id,
              "source": str(source.relative_to(episode)), "serial_raw_sha256": raw_hash,
              "receive_chunks_sha256": sha256(chunk_path), "valid_rows": sum(widths.values()),
              "column_width_counts": dict(widths), "mode_changes": changes,
              "invalid_line_count": len(invalid), "fragment_count": len(fragments),
              "qc_flags": qc, "device_clock_source": "unknown", "synchronization": "unverified",
              "time_semantics": "host read completion of the chunk containing LF; not sample time",
              "monotonic_scope": "same host and boot only"}
    write_new_json(directory / "invalid_lines.json", invalid)
    write_new_json(directory / "fragments.json", fragments)
    write_new_json(directory / "qc.json", report)
    return {"derived": str(directory), **report}


def validate_event(path):
    event = read_json(path)
    if not isinstance(event, dict):
        raise ValueError("event must be a JSON object")
    if type(event.get("schema_version")) is not int or event["schema_version"] != 1:
        raise ValueError("event schema_version must be integer 1")
    if not isinstance(event.get("event_id"), str) or not event["event_id"] or event["event_id"] != path.stem:
        raise ValueError("event_id must match the filename stem")
    for field in ("event", "time_semantics", "monotonic_scope"):
        if not isinstance(event.get(field), str) or not event[field].strip():
            raise ValueError(f"{field} must be a nonempty string")
    for field in ("host_utc_ns", "host_monotonic_ns"):
        if type(event.get(field)) is not int or event[field] < 0:
            raise ValueError(f"{field} must be a nonnegative integer")
    if event.get("hardware_sync") is not False:
        raise ValueError("hardware_sync must be false")
    if event.get("source") not in ("agent", "keyboard"):
        raise ValueError("event source must be agent or keyboard")
    if not isinstance(event.get("notes"), str):
        raise ValueError("event notes must be a string")


def acquisition_status(metadata):
    """Classify declared acquisition conditions, never grant training acceptance."""
    recorded = metadata.get("acquisition_context")
    if not isinstance(recorded, dict):
        recorded = {}
    context = {}
    for key, choices in (("purpose", PURPOSES), ("ring_mount", RING_MOUNTS),
                         ("ring_power", RING_POWERS)):
        value = recorded.get(key)
        context[key] = value if isinstance(value, str) and value in choices else "unknown"
    # No calibration or training acceptance mechanism exists yet.
    context["wear_calibration"] = "unverified"
    # Bare-board motion is not finger motion. Power alone says nothing about IMU quality.
    engineering_only = context["purpose"] == "engineering" or context["ring_mount"] == "bare_board"
    return {"acquisition_context": context,
            "data_use": {"decision": "engineering_only" if engineering_only else "pending_validation",
                         "training_eligible": False}}


def status(args):
    episode = require_episode(args.episode)
    acquisition = acquisition_status(read_json(episode / "episode.json"))
    present = dict.fromkeys(("armband", "camera", "ring"), False)
    issues, captures, attachments, derived = [], [], [], []
    ring_captures, ring_derived = [], []
    for directory in sorted((episode / "ring_captures").glob("*")):
        if not directory.is_dir():
            continue
        try:
            from ering_store import verify_capture
            item = verify_capture(directory)
            item["capture_id"] = directory.name
            present["ring"] |= bool(item.get("raw_verified") and item.get("live_imu_samples", 0) > 0)
            issues.extend(f"ring:{directory.name}:{flag}" for flag in item.get("qc_flags", []))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            item = {"capture_id": directory.name, "raw_verified": False, "error": str(exc)}
            issues.append(f"ring:{directory.name}:{exc}")
        ring_captures.append(item)
    event_files = sorted(path for path in (episode / "events").glob("*.json") if path.is_file())
    event_count = 0
    for path in event_files:
        try:
            validate_event(path)
            event_count += 1
        except (OSError, ValueError, KeyError, TypeError) as exc:
            issues.append(f"event:{path.name}:{exc}")
    for directory in sorted((episode / "captures").glob("*")):
        if not directory.is_dir():
            continue
        item = {"capture_id": directory.name, "raw_verified": False}
        try:
            if not (directory / "result.json").is_file():
                item["status"] = "pending_or_incomplete"
                issues.append(f"capture:{directory.name}:pending_or_incomplete")
            else:
                source, metadata, result = capture_source(episode, directory.name)
                item["status"] = metadata.get("status", "unknown")
                item["exit_code"] = result.get("exit_code")
                raw = source / "serial_raw.bin"
                size = raw.stat().st_size
                if sha256(raw) != metadata.get("serial_raw_sha256"):
                    raise ValueError("raw SHA-256 mismatch")
                chunks = load_chunks(source / "receive_chunks.jsonl", size)
                item.update({"raw_verified": True, "raw_bytes": size, "receive_chunks": len(chunks)})
                present["armband"] |= size > 0
                if size == 0:
                    issues.append(f"capture:{directory.name}:empty_raw")
                if item["status"] != "completed" or result.get("exit_code") != 0:
                    issues.append(f"capture:{directory.name}:capture_incomplete")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            item["error"] = str(exc)
            issues.append(f"capture:{directory.name}:{exc}")
        captures.append(item)
    for modality in ("camera", "ring"):
        for directory in sorted((episode / "attachments" / modality).glob("*")):
            if not directory.is_dir():
                continue
            item = {"modality": modality, "attachment_id": directory.name, "raw_verified": False}
            try:
                record = read_json(directory / "attachment.json")
                raw = directory / record["raw_file"]
                if raw.stat().st_size != record["bytes"] or sha256(raw) != record["sha256"]:
                    raise ValueError("attachment size/SHA-256 mismatch")
                item.update({"raw_verified": True, "bytes": record["bytes"],
                             "device_clock_source": "unknown"})
                present[modality] |= record["bytes"] > 0
                if record["bytes"] == 0:
                    issues.append(f"attachment:{modality}/{directory.name}:empty_raw")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                item["error"] = str(exc)
                issues.append(f"attachment:{modality}/{directory.name}:{exc}")
            attachments.append(item)
    for directory in sorted((episode / "derived").glob("*")):
        if not directory.is_dir():
            continue
        try:
            report = read_json(directory / "qc.json")
            derived.append({"derive_id": directory.name, "valid_rows": report["valid_rows"],
                            "qc_flags": report["qc_flags"]})
            issues.extend(f"derive:{directory.name}:{flag}" for flag in report["qc_flags"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            issues.append(f"derive:{directory.name}:{exc}")
    for directory in sorted((episode / "ring_derived").glob("*")):
        if not directory.is_dir():
            continue
        try:
            report = read_json(directory / "qc.json")
            ring_derived.append({"derive_id": directory.name, "imu_samples": report["imu_samples"],
                                 "qc_flags": report["qc_flags"], "exit_code": report["exit_code"]})
            issues.extend(f"ring_derive:{directory.name}:{flag}" for flag in report["qc_flags"])
            if report["exit_code"] != 0:
                issues.append(f"ring_derive:{directory.name}:derive_incomplete_or_invalid")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            issues.append(f"ring_derive:{directory.name}:{exc}")
    clocks = dict.fromkeys(present, "unknown")
    if any(item.get("raw_verified") and item.get("live_imu_samples", 0) > 0 for item in ring_captures):
        clocks["ring"] = "device_monotonic_us; batch-internal sample times nominal"
    return {"schema_version": 1, "episode": str(episode), "modality_present": present,
            **acquisition,
            "missing_modalities": [name for name, found in present.items() if not found],
            "device_clock_source": clocks,
            "unknown_clock_modalities": [name for name, value in clocks.items() if value == "unknown"],
            "utc_mapping_status": dict.fromkeys(present, "unverified"), "synchronization": "unverified",
            "hardware_sync_verified": False,
            "event_count": event_count, "event_file_count": len(event_files),
            "captures": captures, "ring_captures": ring_captures, "ring_derived": ring_derived,
            "attachments": attachments, "derived": derived,
            "qc_flags": ["device_clocks_unknown", "synchronization_unverified"] +
                        (["missing_modalities"] if not all(present.values()) else []) +
                        (["invalid_events"] if event_count != len(event_files) else []),
            "issues": issues,
            "semantics": "presence and file integrity only; not signal validity, synchronization proof or training approval"}


def ring_scan(args):
    import asyncio
    import ering_capture
    return asyncio.run(ering_capture.scan(args.seconds))


def ring_capture(args):
    import asyncio
    import ering_capture
    episode = require_episode(args.episode)
    directory = episode / "ring_captures" / identifier(args.capture_id)
    return asyncio.run(ering_capture.capture(directory, args.address, args.seconds,
                                             args.replay_pages, args.timeout))


def ring_import(args):
    from ering_store import import_frames
    episode = require_episode(args.episode)
    return import_frames(args.file, episode / "ring_captures" / identifier(args.capture_id))


def ring_derive(args):
    from ering_store import derive as derive_ring
    episode = require_episode(args.episode)
    return derive_ring(episode / "ring_captures" / identifier(args.capture_id),
                       episode / "ring_derived" / identifier(args.derive_id), args.include_replay)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    p = commands.add_parser("create", help="Create a unique subject/session/trial episode")
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    for name in ("subject", "session", "trial", "task", "consent-scope"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--wear-side", choices=("left", "right", "bilateral", "unknown"), required=True)
    p.add_argument("--notes", default="")
    p.add_argument("--purpose", choices=PURPOSES, default="engineering")
    p.add_argument("--ring-mount", choices=RING_MOUNTS, default="unknown")
    p.add_argument("--ring-power", choices=RING_POWERS, default="unknown")
    p.set_defaults(handler=create)
    p = commands.add_parser("mark", help="Record a host event; does not establish hardware sync")
    p.add_argument("--episode", type=Path, required=True)
    p.add_argument("--event", required=True)
    p.add_argument("--source", choices=("agent", "keyboard"), default="agent")
    p.add_argument("--notes", default="")
    p.set_defaults(handler=mark)
    p = commands.add_parser("attach", help="Copy a camera/ring original with SHA-256")
    p.add_argument("--episode", type=Path, required=True)
    p.add_argument("--modality", choices=("camera", "ring"), required=True)
    p.add_argument("--attachment-id", required=True)
    p.add_argument("--file", type=Path, required=True)
    p.set_defaults(handler=attach)
    p = commands.add_parser("capture", help="Capture armband raw/chunks using the existing serial reader")
    p.add_argument("--episode", type=Path, required=True)
    p.add_argument("--capture-id", required=True)
    p.add_argument("--port", required=True)
    p.add_argument("--seconds", "--duration", type=float, required=True)
    p.add_argument("--baud", type=int, default=115200)
    p.set_defaults(handler=capture)
    p = commands.add_parser("derive", help="Derive 8/9/14-column candidate rows with host receive times")
    p.add_argument("--episode", type=Path, required=True)
    p.add_argument("--capture-id", required=True)
    p.add_argument("--derive-id", required=True)
    p.set_defaults(handler=derive)
    p = commands.add_parser("ring-scan", help="Explicitly scan eRing v2 BLE advertisements")
    p.add_argument("--seconds", type=float, default=5)
    p.set_defaults(handler=ring_scan)
    p = commands.add_parser("ring-capture", help="Capture eRing v2 BLE IMU and protocol evidence")
    p.add_argument("--episode", type=Path, required=True)
    p.add_argument("--capture-id", required=True)
    p.add_argument("--address", required=True)
    p.add_argument("--seconds", type=float, required=True)
    p.add_argument("--timeout", type=float, default=10)
    p.add_argument("--replay-pages", type=int, default=0)
    p.set_defaults(handler=ring_capture)
    p = commands.add_parser("ring-import", help="Archive complete v2 frames from raw_hex or eRingWeb frame_hex JSONL")
    p.add_argument("--episode", type=Path, required=True)
    p.add_argument("--capture-id", required=True)
    p.add_argument("--file", type=Path, required=True)
    p.set_defaults(handler=ring_import)
    p = commands.add_parser("ring-derive", help="Export IMU counts and nominal device sample times")
    p.add_argument("--episode", type=Path, required=True)
    p.add_argument("--capture-id", required=True)
    p.add_argument("--derive-id", required=True)
    p.add_argument("--include-replay", action="store_true")
    p.set_defaults(handler=ring_derive)
    p = commands.add_parser("status", help="Read-only modality, integrity, unknown clock and sync summary")
    p.add_argument("--episode", type=Path, required=True)
    p.set_defaults(handler=status)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = args.handler(args)
        print(json.dumps(result, ensure_ascii=False))
        return result.get("exit_code", 0)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
