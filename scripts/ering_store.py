"""Evidence-first eRing storage; no BLE, clock fitting, or hardware claims.

raw_frames.jsonl contains *every* full-frame receipt, including invalid and
duplicate frames. validation.jsonl links validation outcomes by receipt_index.
The codec is loaded lazily so the CLI can import this module independently.
Recorder is a single-writer object; callers serialize accept/finish/cursor calls.
"""

import csv
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import statistics
import tempfile


RESPONSE, REPLAY, MORE = 1, 2, 4
PERSISTENT_TYPES = frozenset(range(0x10, 0x15))
NONPERSISTENT_TYPES = frozenset((1, 2, 3, 4, 5, 6, 7, 0x15, 0x20,
                                0x21, 0x22, 0x23, 0x30, 0x31, 0x32, 0x40, 0x7F))
LOG_NAMES = ("raw_frames.jsonl", "notifications.jsonl", "events.jsonl", "validation.jsonl")


def decode_frame(raw):
    """Delegate all wire parsing to the independently implemented codec."""
    name = f"{__package__}.ering_protocol" if __package__ else "ering_protocol"
    return importlib.import_module(name).decode_frame(raw)


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"sha256": digest.hexdigest(), "size_bytes": Path(path).stat().st_size}


def _sync_directory(path):
    # Windows has no portable directory fsync. Files are fsynced before replace.
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _atomic_json(path, value):
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(_json(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _host_times(utc, mono):
    for value in (utc, mono):
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError("host timestamps must be nonnegative integer ns or null")


def _persistent(frame):
    if frame["type"] in NONPERSISTENT_TYPES:
        return False
    return frame["capture_id"] > 0 and frame["sequence"] > 0


def _canonical(raw):
    """Ignore only REPLAY, request_id and the consequent outer CRC."""
    data = bytearray(raw[:-4])
    flags = int.from_bytes(data[4:6], "little") & ~REPLAY
    data[4:6] = flags.to_bytes(2, "little")
    data[28:32] = bytes(4)
    return bytes(data)


class _Index:
    def __init__(self):
        self.unique = {}
        self.signatures = {}
        self.conflicts = set()
        self.configs = []
        self.received = self.valid = self.duplicates = self.replay_receipts = 0
        self.errors = []

    def accept(self, raw, receipt):
        frame = decode_frame(raw)
        _host_times(receipt.get("host_utc_ns"), receipt.get("host_monotonic_ns"))
        if not isinstance(receipt.get("context"), str):
            raise ValueError("context must be a string")
        if frame["type"] in PERSISTENT_TYPES and not _persistent(frame):
            raise ValueError("persistent sensor frame requires nonzero capture_id and sequence")
        duplicate = False
        entry = {"frame": frame, "receipt": receipt}
        if _persistent(frame):
            key = (frame["capture_id"], frame["sequence"])
            signature = _canonical(raw)
            if key in self.conflicts or (key in self.signatures and self.signatures[key] != signature):
                self.conflicts.add(key)
                self.unique.pop(key, None)
                raise ValueError(f"conflicting persistent frame {key}")
            duplicate = key in self.signatures
            self.signatures[key] = signature
            previous = self.unique.get(key)
            if previous is None or (previous["frame"]["flags"] & REPLAY and not frame["flags"] & REPLAY):
                self.unique[key] = entry
            if frame["flags"] == REPLAY:
                self.replay_receipts += 1
        if frame["type"] == 3 and (frame["flags"] & ~MORE) == RESPONSE:
            self.configs.append(entry)
        self.valid += 1
        self.duplicates += int(duplicate)
        return {**frame, "duplicate": duplicate}

    def summary(self):
        imu = [entry["frame"] for entry in self.unique.values() if entry["frame"]["type"] == 0x11]
        return {
            "frames_received": self.received, "valid_frames": self.valid,
            "duplicate_frames": self.duplicates, "unique_persistent_frames": len(self.unique),
            "replay_frames_received": self.replay_receipts,
            "imu_samples": sum(len(f["decoded"]["samples"]) for f in imu),
            "live_imu_samples": sum(len(f["decoded"]["samples"]) for f in imu if not f["flags"] & REPLAY),
            "replay_imu_samples": sum(len(f["decoded"]["samples"]) for f in imu if f["flags"] & REPLAY),
            "invalid_frames": len(self.errors), "conflicting_keys": [list(k) for k in sorted(self.conflicts)],
        }


class Recorder:
    """Create a new capture directory exclusively; never resume or overwrite."""

    def __init__(self, directory: Path, metadata: dict):
        self.directory = Path(directory)
        self.metadata = json.loads(_json(metadata))
        self.directory.mkdir(parents=True, exist_ok=False)
        self._handles = {}
        self._index = _Index()
        self._finished = False
        self._closed = False
        try:
            for name in LOG_NAMES:
                self._handles[name] = (self.directory / name).open("x", encoding="utf-8", newline="\n")
            _atomic_json(self.directory / "capture.json", {
                "schema_version": 1, "status": "recording", "metadata": self.metadata,
                "synchronization": "unverified", "hardware_verified": False,
            })
        except Exception:
            self.close()
            raise

    def _check_open(self):
        if self._closed or self._finished:
            raise ValueError("recorder is closed or finished")

    def _write(self, name, record):
        self._check_open()
        stream = self._handles[name]
        stream.write(_json(record) + "\n")
        # Make original bytes visible before invoking any decoder/validator.
        stream.flush()

    def accept(self, raw: bytes, host_utc_ns: int | None,
               host_monotonic_ns: int | None, context: str = "live") -> dict:
        self._check_open()
        receipt = {"receipt_index": self._index.received, "raw_hex": bytes(raw).hex(),
                   "host_utc_ns": host_utc_ns, "host_monotonic_ns": host_monotonic_ns,
                   "context": context}
        self._write("raw_frames.jsonl", receipt)
        self._index.received += 1
        try:
            decoded = self._index.accept(bytes(raw), receipt)
        except Exception as exc:
            error = {"receipt_index": receipt["receipt_index"], "valid": False,
                     "error": str(exc), "error_type": type(exc).__name__}
            self._index.errors.append(error)
            self._write("validation.jsonl", error)
            raise ValueError(str(exc)) from exc
        self._write("validation.jsonl", {"receipt_index": receipt["receipt_index"],
                                        "valid": True, **decoded})
        return decoded

    def notification(self, data: bytes, utc_ns, mono_ns):
        self._write("notifications.jsonl", {"raw_hex": bytes(data).hex(),
                                           "host_utc_ns": utc_ns, "host_monotonic_ns": mono_ns})

    def event(self, kind, **fields):
        self._write("events.jsonl", {**fields, "kind": kind})

    def flush(self):
        if self._closed:
            raise ValueError("recorder is closed")
        for stream in self._handles.values():
            stream.flush()
            os.fsync(stream.fileno())
        # Include original import copy and any earlier cursor, not just JSONL.
        for name in ("source.jsonl", "cursor.json", "capture.json"):
            path = self.directory / name
            if path.exists():
                with path.open("r+b") as stream:
                    os.fsync(stream.fileno())
        _sync_directory(self.directory)

    def commit_cursor(self, capture_id: int, sequence: int, request_id: int):
        """Caller owns REPLAY_DONE/result/count validation; this owns durability."""
        self._check_open()
        for value, bits in ((capture_id, 64), (sequence, 32), (request_id, 32)):
            if type(value) is not int or not 0 < value < (1 << bits):
                raise ValueError("cursor fields must be nonzero unsigned protocol integers")
        # This durable event records intent; cursor.json is the commit authority.
        self.event("cursor_commit_requested", capture_id=capture_id, sequence=sequence, request_id=request_id)
        self.flush()
        _atomic_json(self.directory / "cursor.json", {
            "capture_id": capture_id, "sequence": sequence, "request_id": request_id})

    def finish(self, status: str, error: str | None = None) -> dict:
        self._check_open()
        self.flush()
        files = {name: _hash(self.directory / name) for name in (*LOG_NAMES, "source.jsonl", "cursor.json")
                 if (self.directory / name).is_file()}
        result = {
            "schema_version": 1, "status": status, "error": error,
            "metadata": self.metadata, "files": files, **self._index.summary(),
            "synchronization": "unverified", "device_utc": "unknown", "hardware_verified": False,
            "evidence_kind": self.metadata.get("evidence_kind", "unverified"),
        }
        _atomic_json(self.directory / "capture.json", result)
        self._finished = True
        return result

    def close(self):
        if self._closed:
            return
        try:
            for stream in self._handles.values():
                stream.close()
        finally:
            self._closed = True


def import_frames(source: Path, directory: Path) -> dict:
    """Import project raw_hex or eRingWeb frame_hex exports without inventing times.

    eRingWeb 1.0.6 exports persistent frames (including replay), not complete
    receipt/configuration evidence. Decode the bytes; never trust JSON summaries
    or infer ranges from another capture. The exact input is kept as source.jsonl.
    """
    source = Path(source)
    recorder = Recorder(directory, {"source": str(source), "mode": "import", "evidence_kind": "unverified"})
    errors = []
    try:
        with source.open("rb") as incoming, (recorder.directory / "source.jsonl").open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        with (recorder.directory / "source.jsonl").open("rb") as incoming:
            for line_number, line in enumerate(incoming, 1):
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("JSONL row requires an object")
                    key = "raw_hex" if "raw_hex" in row else "frame_hex"
                    if not isinstance(row.get(key), str):
                        raise ValueError("JSONL row requires a string raw_hex or frame_hex")
                    raw = bytes.fromhex(row[key])
                    if "raw_hex" in row and "frame_hex" in row:
                        if not isinstance(row["frame_hex"], str) or raw != bytes.fromhex(row["frame_hex"]):
                            raise ValueError("conflicting raw_hex and frame_hex")
                    # Vendor export has no receive-time provenance. Do not adopt
                    # arbitrary added fields as acquisition timestamps.
                    vendor = key == "frame_hex"
                    recorder.accept(raw, None if vendor else row.get("host_utc_ns"),
                                    None if vendor else row.get("host_monotonic_ns"),
                                    context="eringweb_export" if vendor else row.get("context", "import"))
                except (ValueError, TypeError) as exc:
                    errors.append({"line_number": line_number, "error": str(exc)})
                    recorder.event("import_error", **errors[-1])
    except OSError as exc:
        errors.append({"error": str(exc)})
        recorder.event("import_error", **errors[-1])
    try:
        result = recorder.finish("failed" if errors else "completed",
                                 f"{len(errors)} import error(s)" if errors else None)
        return {**result, "directory": str(recorder.directory), "exit_code": 1 if errors else 0,
                "errors": errors}
    finally:
        recorder.close()


def _scan_raw(directory):
    index = _Index()
    with (directory / "raw_frames.jsonl").open("rb") as stream:
        for line_number, line in enumerate(stream, 1):
            index.received += 1
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or not isinstance(row.get("raw_hex"), str):
                    raise ValueError("raw receipt requires a string raw_hex")
                if row.get("receipt_index") != line_number - 1:
                    raise ValueError("raw receipt_index is not in receive order")
                index.accept(bytes.fromhex(row["raw_hex"]), row)
            except Exception as exc:
                index.errors.append({"line_number": line_number, "error": str(exc)})
    return index


def _configurations(index):
    configurations, signatures, conflicts = {}, {}, set()
    for entry in index.configs:
        frame = entry["frame"]
        config = frame["decoded"]
        key = (frame["capture_id"], frame["sample_config_id"])
        signature = frame["payload_hex"]
        if config.get("config_id") != key[1]:
            conflicts.add(key)
            conflicts.add((key[0], config.get("config_id")))
        if key in signatures and signatures[key] != signature:
            conflicts.add(key)
        signatures[key] = signature
        if (type(config.get("accel_range_g")) is not int or config["accel_range_g"] <= 0
                or type(config.get("gyro_range_dps")) is not int or config["gyro_range_dps"] <= 0):
            conflicts.add(key)
        configurations[key] = config
    for key in conflicts:
        configurations.pop(key, None)
    return configurations, conflicts


def _inspect_capture(directory):
    """Recompute from original receipts, never trust cached decoded/sample counts."""
    directory = Path(directory)
    qc, hash_errors = set(), []
    index = _Index()
    manifest = {}
    try:
        manifest = json.loads((directory / "capture.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("capture manifest must be an object")
    except (OSError, ValueError) as exc:
        hash_errors.append(f"capture.json: {exc}")
        qc.add("manifest_unreadable")
        manifest = {}
    files = manifest.get("files", {})
    if not isinstance(files, dict):
        files = {}
    required = set(LOG_NAMES)
    metadata = manifest.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        hash_errors.append("invalid manifest metadata")
    if metadata.get("mode") == "import":
        required.add("source.jsonl")
    # A dropped cursor/source entry must not silently disappear from verification.
    for name in ("source.jsonl", "cursor.json"):
        if (directory / name).exists():
            required.add(name)
    for name in sorted(required - files.keys()):
        hash_errors.append(f"missing manifest hash: {name}")
    for name, expected in files.items():
        if name not in (*LOG_NAMES, "source.jsonl", "cursor.json"):
            hash_errors.append(f"unrecognized evidence path: {name}")
            continue
        try:
            path = directory / name
            if path.is_symlink():
                raise ValueError("evidence must not be a symlink")
            actual = _hash(path)
            if not isinstance(expected, dict) or actual != expected:
                raise ValueError("hash or size mismatch")
        except (OSError, ValueError) as exc:
            hash_errors.append(f"{name}: {exc}")
    if hash_errors:
        qc.add("raw_integrity_failed")
    try:
        index = _scan_raw(directory)
    except OSError as exc:
        index.errors.append({"error": str(exc)})
        qc.add("raw_unreadable")
    if index.errors:
        qc.add("invalid_frames")
    if index.conflicts:
        qc.add("persistent_frame_conflict")
    configurations, config_conflicts = _configurations(index)
    if config_conflicts:
        qc.add("conflicting_sample_config")
    summary = index.summary()
    for key, value in summary.items():
        if key in manifest and manifest[key] != value:
            qc.add("manifest_summary_mismatch")
    if not summary["imu_samples"]:
        qc.add("no_imu_samples")
    elif not summary["live_imu_samples"]:
        qc.add("no_live_imu_samples")
    if summary["replay_imu_samples"]:
        qc.add("replay_attribution_unknown")
    for entry in index.unique.values():
        frame = entry["frame"]
        if frame["type"] == 0x13:
            qc.add("device_reported_gap")
        if frame["type"] not in PERSISTENT_TYPES:
            qc.add("unknown_persistent_type")
        if frame["type"] == 0x11:
            if entry["receipt"].get("context") == "eringweb_export":
                qc.add("vendor_archive_window_unverified")
            if (frame["capture_id"], frame["sample_config_id"]) not in configurations:
                qc.add("missing_or_conflicting_sample_config")
            if entry["receipt"].get("host_utc_ns") is None or entry["receipt"].get("host_monotonic_ns") is None:
                qc.add("missing_host_timestamps")
    synthetic = (manifest.get("evidence_kind") == "synthetic"
                 or metadata.get("evidence_kind") == "synthetic"
                 or any(e["receipt"].get("context") == "synthetic" for e in index.unique.values()))
    if synthetic:
        qc.add("synthetic_evidence")
    qc.update(("device_utc_unknown", "synchronization_unverified", "nominal_device_sample_times"))
    status = manifest.get("status", "unknown")
    if status != "completed":
        qc.add("capture_not_completed")
        if status == "failed":
            qc.add("capture_failed")
        elif status in ("interrupted", "cancelled", "canceled", "aborted"):
            qc.add("capture_interrupted")
    if manifest.get("error"):
        qc.add("capture_error")
        if status == "completed":
            status = "failed"
    if hash_errors or index.errors or "manifest_summary_mismatch" in qc:
        status = "failed"
    report = {**summary, "raw_verified": not hash_errors, "status": status,
              "capture_status": manifest.get("status", "unknown"), "qc_flags": sorted(qc),
              "capture_error": manifest.get("error"),
              "hash_errors": hash_errors, "errors": index.errors,
              "synchronization": "unverified", "device_utc": "unknown", "hardware_verified": False,
              "evidence_kind": "synthetic" if synthetic else manifest.get("evidence_kind", "unverified")}
    return report, index, manifest


def verify_capture(directory) -> dict:
    """Read-only hash and full-frame/IMU verification; no hardware inference.

    imu_samples counts deduplicated valid IMU samples; live_imu_samples excludes
    REPLAY-only keys. raw_verified describes archived byte integrity, separately
    from invalid_frames and status. A CRC-invalid receipt can be intact evidence.
    """
    return _inspect_capture(Path(directory))[0]


CSV_FIELDS = ["capture_id", "sequence", "sample_index", "ax_raw", "ay_raw", "az_raw",
              "gx_raw", "gy_raw", "gz_raw", "first_sample_us", "period_us", "device_time_us",
              "host_utc_ns", "host_monotonic_ns",
              "sample_config_id", "calibration_version", "ax_g", "ay_g", "az_g", "gx_dps", "gy_dps",
              "gz_dps", "device_utc_ns", "replay", "attribution", "context", "receipt_index",
              "batch_timestamp_us", "time_epoch", "device_time_basis", "nominal_boundary_delta_us"]


class _TimingQC:
    """Compare emitted IMU batches in sequence order, never host receipt order.

    Epoch numbers are local to each capture_id in this derivation. Configuration
    transitions (including A -> B -> A) and raw anchor regressions start a new
    epoch. Residual statistics pool only within-epoch comparisons, not transitions.
    No fit, reset diagnosis, loss estimate, or correction follows from a residual.
    """

    def __init__(self):
        self.previous = {}
        self.groups = {}
        self.boundaries = []

    def observe(self, frame, flags):
        capture = frame["capture_id"]
        config = frame["sample_config_id"]
        decoded = frame["decoded"]
        first, period = decoded["first_sample_us"], decoded["period_us"]
        group = self.groups.setdefault((capture, config), {"deltas": [], "epochs": set(),
                                                          "anchor_backwards_count": 0})
        previous = self.previous.get(capture)
        epoch, delta = 0, None
        if previous is not None:
            epoch = previous["epoch"]
            reason = None
            if config != previous["config"]:
                reason = "sample_config_changed"
            elif first < previous["first"]:
                reason = "raw_batch_anchor_backwards"
                group["anchor_backwards_count"] += 1
                # Retain the legacy flag for observed anchor regression only.
                flags.update(("device_time_discontinuity", "device_time_backwards"))
            if reason:
                epoch += 1
                flags.add("time_epoch_boundary")
                self.boundaries.append({
                    "capture_id": capture, "sample_config_id": config, "time_epoch": epoch,
                    "reason": reason, "sequence": frame["sequence"],
                    "previous_sequence": previous["sequence"],
                    "previous_sample_config_id": previous["config"],
                    "previous_first_sample_us": previous["first"], "first_sample_us": first,
                })
            elif period > 0 and previous["period"] > 0:
                delta = first - (previous["first"] + previous["count"] * previous["period"])
                group["deltas"].append(delta)
                if delta:
                    flags.add("nominal_batch_boundary_mismatch")
                if first < previous["first"] + (previous["count"] - 1) * previous["period"]:
                    # Overlap with a reconstructed sample is not raw clock regression.
                    flags.add("nominal_sample_time_overlap")
        group["epochs"].add(epoch)
        self.previous[capture] = {"epoch": epoch, "config": config, "first": first,
                                  "period": period, "count": len(decoded["samples"]),
                                  "sequence": frame["sequence"]}
        if len(self.previous) > 1:
            flags.add("multiple_capture_epochs")
        return epoch, delta

    def summary(self):
        result = []
        for (capture, config), group in sorted(self.groups.items()):
            deltas = group["deltas"]
            result.append({
                "capture_id": capture, "sample_config_id": config,
                "time_epochs": sorted(group["epochs"]),
                "count": len(deltas), "nonzero_count": sum(d != 0 for d in deltas),
                "min_us": min(deltas) if deltas else None,
                "max_us": max(deltas) if deltas else None,
                "median_us": statistics.median(deltas) if deltas else None,
                "anchor_backwards_count": group["anchor_backwards_count"],
            })
        return result


def derive(directory: Path, output: Path, include_replay: bool = False) -> dict:
    """Write new imu.csv/qc.json, using device nominal periods and scoped ranges.

    Raw capture files remain untouched. Config responses may follow samples.
    Replayed-only frames are excluded by default; live always wins a duplicate.
    A conflicting persistent key is quarantined in its entirety, never guessed.
    Invalid nominal periods retain raw values, but leave derived times empty and
    make the derivation unsuccessful. Incomplete captures still retain raw rows.
    Batch anchors stay verbatim; per-sample times are nominal reconstructions.
    device_time_discontinuity now means raw batch anchor regression, not a
    nonzero nominal boundary residual. Neither observation proves a device reset.
    """
    directory, output = Path(directory), Path(output)
    if output.resolve() == directory.resolve() or directory.resolve() in output.resolve().parents:
        raise ValueError("derived output must be separate from the raw capture directory")
    output.mkdir(parents=True, exist_ok=False)
    report, index, _ = _inspect_capture(directory)
    configurations, _ = _configurations(index)
    flags = set(report["qc_flags"])
    emitted = excluded = 0
    timing = _TimingQC()
    with (output / "imu.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        # Corrupt/unverifiable archives produce QC and a header, not trusted data.
        for key, entry in sorted(index.unique.items()) if report["raw_verified"] else []:
            frame, receipt = entry["frame"], entry["receipt"]
            if frame["type"] != 0x11:
                continue
            replay = bool(frame["flags"] & REPLAY)
            samples = frame["decoded"]["samples"]
            if replay and not include_replay:
                excluded += len(samples)
                continue
            config_key = (frame["capture_id"], frame["sample_config_id"])
            config = configurations.get(config_key)
            first = frame["decoded"]["first_sample_us"]
            period = frame["decoded"]["period_us"]
            if first != frame["timestamp_us"]:
                flags.add("batch_timestamp_mismatch")
            time_available = period > 0
            if not time_available:
                flags.add("invalid_nominal_period")
            if config and config.get("imu_hz", 0) > 0 and abs(period * config["imu_hz"] - 1000000) > config["imu_hz"]:
                flags.add("config_period_mismatch")
            epoch, boundary_delta = timing.observe(frame, flags)
            for sample_index, sample in enumerate(samples):
                row = dict(zip(("ax_raw", "ay_raw", "az_raw", "gx_raw", "gy_raw", "gz_raw"), sample))
                row.update(capture_id=key[0], sequence=key[1], sample_index=sample_index,
                           first_sample_us=first, period_us=period,
                           device_time_us=first + sample_index * period if time_available else None,
                           batch_timestamp_us=frame["timestamp_us"], time_epoch=epoch,
                           device_time_basis="nominal_reconstruction" if time_available else "unavailable_invalid_period",
                           nominal_boundary_delta_us=boundary_delta,
                           host_utc_ns=receipt.get("host_utc_ns"), host_monotonic_ns=receipt.get("host_monotonic_ns"),
                           sample_config_id=frame["sample_config_id"], calibration_version=frame["calibration_version"],
                           device_utc_ns=None, replay=int(replay),
                           attribution="unknown" if replay or receipt.get("context") == "eringweb_export" else "current_capture_unverified",
                           context=receipt.get("context"), receipt_index=receipt.get("receipt_index"))
                if config:
                    for axis, raw in zip(("ax", "ay", "az"), sample[:3]):
                        row[f"{axis}_g"] = raw * config["accel_range_g"] / 32768
                    for axis, raw in zip(("gx", "gy", "gz"), sample[3:]):
                        row[f"{axis}_dps"] = raw * config["gyro_range_dps"] / 32768
                writer.writerow(row)
                emitted += 1
        stream.flush()
        os.fsync(stream.fileno())
    status = report["status"]
    if "invalid_nominal_period" in flags and status == "completed":
        status = "failed"
    result = {**report, "status": status, "schema_version": 1, "source_directory": str(directory),
              "output_directory": str(output), "include_replay": bool(include_replay),
              "imu_samples": emitted, "source_imu_samples": report["imu_samples"],
              "excluded_replay_samples": excluded, "qc_flags": sorted(flags),
              "source_manifest": _hash(directory / "capture.json") if (directory / "capture.json").is_file() else None,
              "files": {"imu.csv": _hash(output / "imu.csv")},
              "time_basis": "first_sample_us + sample_index * period_us (nominal reconstruction, not per-sample measured time; empty if period_us <= 0)",
              "batch_boundary_summary": timing.summary(),
              "time_epoch_boundaries": timing.boundaries,
              "timing_qc_basis": {
                  "boundary_delta_us": "current first_sample_us - (previous first_sample_us + previous sample count * previous period_us)",
                  "scope": "emitted IMU batches in sequence order; same capture_id and contiguous time_epoch; both periods positive",
                  "summary": "per capture_id/sample_config_id; pool within-epoch deltas only; count includes zero; empty statistics are null",
                  "epochs": "capture_id isolates generations; local time_epoch increments on config transition or raw batch anchor regression",
                  "device_time_discontinuity": "legacy flag restricted to raw batch anchor regression; not nominal boundary mismatch",
                  "interpretation": "residuals do not establish packet loss or device reset; no clock fitting, correction, or UTC alignment",
              },
              "conversion_basis": "matching capture_id and sample_config_id; raw * range / 32768",
              "exit_code": 0 if status == "completed" else 1}
    _atomic_json(output / "qc.json", result)
    return result
