"""Storage fixtures are synthetic software evidence, never hardware evidence."""

import hashlib
import csv
import json
import os
from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ering_store as store


VECTOR_A = "41e702010000000001000000000000000000000000000000000000000403020100000000376a0c1d"
VECTOR_B = "41e7020301000c0000000000020000000000000040420f0000000000040302010100010019003200070001000400f401adcc31fe"
VECTOR_D = "41e702600200020007000000020000000000000040420f00000000000403020101000100aa5525a696a4"


def frame(message_type=0x11, flags=0, sequence=1, capture_id=2, timestamp_us=1000,
          request_id=0, config_id=1, calibration=1, payload=None):
    if payload is None:
        payload = struct.pack("<QIBBH6h", timestamp_us, 20000, 1, 0, 0, -32768, 0, 16384, 32767, -10, 100)
    header = struct.pack("<HBBHHIQQIHH", 0xE741, 2, message_type, flags, len(payload),
                         sequence, capture_id, timestamp_us, request_id, config_id, calibration)
    data = header + payload
    return data + struct.pack("<I", zlib.crc32(data))


def config(capture_id=2, config_id=1, accel=4, gyro=500, flags=1):
    return frame(3, flags=flags, sequence=0, capture_id=capture_id, request_id=1, config_id=config_id,
                 payload=struct.pack("<HHBBHHH", 25, 50, 7, 0, config_id, accel, gyro))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        try:
            import ering_protocol  # noqa: F401 -- integration dependency
        except ImportError:
            self.skipTest("codec worker has not supplied ering_protocol.py yet")

    def recorder(self):
        recorder = store.Recorder(self.root / "capture", {"evidence_kind": "synthetic"})
        self.addCleanup(recorder.close)
        return recorder

    def test_f0_import_preserves_original_and_missing_times(self):
        source = self.root / "input.jsonl"
        original = (json.dumps({"raw_hex": VECTOR_B}) + "\r\n" + json.dumps({"raw_hex": VECTOR_D}) + "\n").encode()
        source.write_bytes(original)
        result = store.import_frames(source, self.root / "capture")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["imu_samples"], 0)
        self.assertEqual((self.root / "capture/source.jsonl").read_bytes(), original)
        self.assertEqual(result["files"]["source.jsonl"]["sha256"], hashlib.sha256(original).hexdigest())
        rows = [json.loads(line) for line in (self.root / "capture/raw_frames.jsonl").read_text().splitlines()]
        self.assertTrue(all(row["host_utc_ns"] is None and row["host_monotonic_ns"] is None for row in rows))
        self.assertFalse(result["hardware_verified"])

    def test_f0_bad_input_retains_original_and_errors(self):
        source = self.root / "bad.jsonl"
        source.write_text('{"raw_hex":"00"}\nnot json\n{"raw_hex":"zz"}\n', encoding="utf-8")
        result = store.import_frames(source, self.root / "capture")
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(len(result["errors"]), 3)
        self.assertEqual((self.root / "capture/source.jsonl").read_bytes(), source.read_bytes())
        self.assertIn('"raw_hex": "00"', (self.root / "capture/raw_frames.jsonl").read_text())
        self.assertIn('"valid": false', (self.root / "capture/validation.jsonl").read_text())

    def test_exclusive_directory(self):
        self.recorder()
        with self.assertRaises(FileExistsError):
            store.Recorder(self.root / "capture", {})

    def test_original_is_written_before_decode(self):
        recorder = self.recorder()
        raw = frame()
        def observe(data):
            self.assertEqual(json.loads((recorder.directory / "raw_frames.jsonl").read_text())["raw_hex"], raw.hex())
            raise ValueError("deliberate decoder error")
        with patch.object(store, "decode_frame", side_effect=observe), self.assertRaises(ValueError):
            recorder.accept(raw, None, None)
        self.assertEqual(recorder.finish("failed")["invalid_frames"], 1)

    def read_csv(self, output):
        with (output / "imu.csv").open(newline="", encoding="utf-8") as stream:
            return list(csv.DictReader(stream))

    def test_replay_then_live_prefers_live_and_counts_all_wire_receipts(self):
        recorder = self.recorder()
        first = recorder.accept(frame(flags=2, request_id=10), 100, 200, context="ble")
        second = recorder.accept(frame(flags=2, request_id=11), 300, 400, context="ble")
        third = recorder.accept(frame(), 500, 600, context="ble")
        fourth = recorder.accept(frame(flags=2, request_id=12), 700, 800, context="ble")
        self.assertEqual([f["duplicate"] for f in (first, second, third, fourth)], [False, True, True, True])
        result = recorder.finish("completed")
        self.assertEqual(result["frames_received"], 4)
        self.assertEqual(result["replay_frames_received"], 3)
        self.assertEqual(result["imu_samples"], 1)
        self.assertEqual(result["live_imu_samples"], 1)
        self.assertEqual(result["replay_imu_samples"], 0)
        self.assertEqual(len((recorder.directory / "raw_frames.jsonl").read_text().splitlines()), 4)
        report = store.verify_capture(recorder.directory)
        self.assertEqual(report["live_imu_samples"], 1)
        output = self.root / "derived"
        store.derive(recorder.directory, output)
        rows = self.read_csv(output)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["host_utc_ns"], "500")
        self.assertEqual(rows[0]["replay"], "0")

    def test_replay_only_not_current_trial_and_opt_in_unknown_attribution(self):
        recorder = self.recorder()
        recorder.accept(frame(flags=2, request_id=1), None, None, context="ble")
        recorder.finish("completed")
        report = store.verify_capture(recorder.directory)
        self.assertEqual(report["live_imu_samples"], 0)
        self.assertEqual(report["replay_imu_samples"], 1)
        excluded = store.derive(recorder.directory, self.root / "default")
        self.assertEqual(excluded["imu_samples"], 0)
        self.assertEqual(excluded["excluded_replay_samples"], 1)
        included = store.derive(recorder.directory, self.root / "replay", True)
        self.assertEqual(included["imu_samples"], 1)
        row = self.read_csv(self.root / "replay")[0]
        self.assertEqual(row["attribution"], "unknown")
        self.assertEqual(row["device_utc_ns"], "")
        self.assertEqual(row["host_utc_ns"], "")

    def test_two_pass_config_scaling_and_device_period_not_host_arrival(self):
        recorder = self.recorder()
        payload = struct.pack("<QIBBH12h", 1000, 20000, 2, 0, 0, *([-32768, 0, 16384, 32767, -10, 100] * 2))
        recorder.accept(frame(payload=payload), 900000000000, 42, "ble")
        recorder.accept(config(flags=5), 900000000001, 43, "ble")
        recorder.finish("completed")
        store.derive(recorder.directory, self.root / "derived")
        rows = self.read_csv(self.root / "derived")
        self.assertEqual([row["device_time_us"] for row in rows], ["1000", "21000"])
        self.assertEqual([row["host_utc_ns"] for row in rows], ["900000000000"] * 2)
        self.assertEqual(float(rows[0]["ax_g"]), -4)
        self.assertEqual(float(rows[0]["az_g"]), 2)
        self.assertEqual(float(rows[0]["gx_dps"]), 32767 * 500 / 32768)
        self.assertEqual(rows[0]["ax_raw"], "-32768")

    def test_more_config_matches_normal_response_and_preserves_flags(self):
        recorder = self.recorder()
        recorder.accept(frame(), None, None)
        normal = recorder.accept(config(flags=1), None, None)
        more = recorder.accept(config(flags=5), None, None)
        self.assertEqual(normal["flags"], 1)
        self.assertEqual(more["flags"], 5)
        recorder.finish("completed")
        result = store.derive(recorder.directory, self.root / "derived")
        self.assertEqual(result["exit_code"], 0)
        self.assertNotIn("conflicting_sample_config", result["qc_flags"])
        self.assertNotIn("missing_or_conflicting_sample_config", result["qc_flags"])
        self.assertEqual(float(self.read_csv(self.root / "derived")[0]["ax_g"]), -4)
        self.assertEqual(result["replay_frames_received"], 0)

    def test_zero_period_preserves_raw_values_without_inventing_sample_times(self):
        axes = [-32768, 0, 16384, 32767, -10, 100]
        payload = struct.pack("<QIBBH12h", 1000, 0, 2, 0, 0, *(axes * 2))
        for status in ("completed", "failed", "interrupted"):
            with self.subTest(status=status):
                recorder = store.Recorder(self.root / status, {"evidence_kind": "synthetic"})
                self.addCleanup(recorder.close)
                recorder.accept(frame(payload=payload), 111, 222)
                recorder.accept(frame(sequence=2, timestamp_us=41000), 333, 444)
                recorder.accept(config(flags=5), None, None)
                recorder.finish(status, None if status == "completed" else "ended early")
                recorder.close()
                originals = {p.name: p.read_bytes() for p in recorder.directory.iterdir()}
                output = self.root / (status + "_derived")
                result = store.derive(recorder.directory, output)
                rows = self.read_csv(output)
                self.assertEqual(len(rows), 3)
                self.assertEqual([r["device_time_us"] for r in rows], ["", "", "41000"])
                self.assertEqual([r["period_us"] for r in rows], ["0", "0", "20000"])
                self.assertEqual([r["first_sample_us"] for r in rows], ["1000", "1000", "41000"])
                for row in rows[:2]:
                    self.assertEqual([int(row[axis + "_raw"]) for axis in ("ax", "ay", "az", "gx", "gy", "gz")], axes)
                    self.assertEqual(row["host_utc_ns"], "111")
                    self.assertEqual(row["host_monotonic_ns"], "222")
                    self.assertEqual(row["device_utc_ns"], "")
                    self.assertEqual(float(row["ax_g"]), -4)
                self.assertEqual(result["exit_code"], 1)
                self.assertEqual(result["status"], "failed" if status == "completed" else status)
                self.assertEqual(result["capture_status"], status)
                self.assertTrue(result["raw_verified"])
                self.assertIn("invalid_nominal_period", result["qc_flags"])
                self.assertNotIn("device_time_discontinuity", result["qc_flags"])
                if status != "completed":
                    self.assertIn("capture_not_completed", result["qc_flags"])
                if status == "interrupted":
                    self.assertIn("capture_interrupted", result["qc_flags"])
                self.assertEqual(json.loads((output / "qc.json").read_text())["exit_code"], 1)
                self.assertEqual(originals, {p.name: p.read_bytes() for p in recorder.directory.iterdir()})

    def test_config_scope_and_conflicts_do_not_guess_ranges(self):
        recorder = self.recorder()
        for generation, cid in ((2, 1), (3, 1), (2, 2)):
            recorder.accept(frame(capture_id=generation, config_id=cid, sequence=cid), None, None)
        recorder.accept(config(), None, None)
        recorder.accept(config(accel=8), None, None)
        recorder.finish("completed")
        result = store.derive(recorder.directory, self.root / "derived")
        self.assertIn("conflicting_sample_config", result["qc_flags"])
        rows = self.read_csv(self.root / "derived")
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["ax_g"] == "" and row["gx_dps"] == "" for row in rows))

    def test_config_id_match_alone_cannot_convert_another_generation(self):
        recorder = self.recorder()
        recorder.accept(frame(capture_id=3), None, None)
        recorder.accept(config(capture_id=2), None, None)
        recorder.finish("completed")
        store.derive(recorder.directory, self.root / "derived")
        self.assertEqual(self.read_csv(self.root / "derived")[0]["ax_g"], "")

    def test_persistent_conflict_quarantines_key_and_retains_both_originals(self):
        recorder = self.recorder()
        recorder.accept(frame(), None, None)
        with self.assertRaisesRegex(ValueError, "conflicting persistent"):
            recorder.accept(frame(timestamp_us=999), None, None)
        result = recorder.finish("failed")
        self.assertEqual(result["imu_samples"], 0)
        self.assertEqual(result["conflicting_keys"], [[2, 1]])
        self.assertEqual(len((recorder.directory / "raw_frames.jsonl").read_text().splitlines()), 2)
        report = store.derive(recorder.directory, self.root / "derived")
        self.assertEqual(report["imu_samples"], 0)
        self.assertIn("persistent_frame_conflict", report["qc_flags"])

    def test_normalization_preserves_every_other_field(self):
        variations = ({"flags": 6}, {"timestamp_us": 1001}, {"config_id": 2},
                      {"calibration": 2}, {"payload": b"different"}, {"message_type": 0x61})
        for number, variation in enumerate(variations):
            with self.subTest(variation=variation):
                recorder = store.Recorder(self.root / str(number), {})
                self.addCleanup(recorder.close)
                base = dict(message_type=0x60, payload=b"a", flags=2, request_id=5)
                recorder.accept(frame(**base), None, None)
                with self.assertRaisesRegex(ValueError, "conflicting persistent"):
                    recorder.accept(frame(**{**base, **variation}), None, None)

    def test_unknown_persistent_retained_and_known_nonpersistent_never_counted(self):
        recorder = self.recorder()
        self.assertFalse(recorder.accept(bytes.fromhex(VECTOR_D), None, None)["duplicate"])
        self.assertTrue(recorder.accept(bytes.fromhex(VECTOR_D), None, None)["duplicate"])
        for _ in range(2):
            self.assertFalse(recorder.accept(config(), None, None)["duplicate"])
        with self.assertRaises(ValueError):
            recorder.accept(frame(0x15, flags=2, request_id=1, payload=bytes(20)), None, None)
        report = recorder.finish("failed")
        self.assertEqual(report["replay_frames_received"], 2)
        self.assertEqual(report["unique_persistent_frames"], 1)
        self.assertEqual(report["imu_samples"], 0)

    def test_unknown_nonpersistent_not_deduplicated(self):
        recorder = self.recorder()
        for _ in range(2):
            self.assertFalse(recorder.accept(frame(0x60, sequence=0, payload=b"?"), None, None)["duplicate"])
        self.assertEqual(recorder.finish("completed")["unique_persistent_frames"], 0)

    def test_generation_is_part_of_dedup_key(self):
        recorder = self.recorder()
        recorder.accept(frame(capture_id=2), None, None)
        recorder.accept(frame(capture_id=3), None, None)
        self.assertEqual(recorder.finish("completed")["imu_samples"], 2)

    def test_notifications_events_and_read_only_verification(self):
        recorder = self.recorder()
        recorder.notification(b"\x00\xff\x01", 123, 456)
        recorder.event("time_sync", capture_id=2, device_rx_us=1, device_tx_us=2)
        recorder.accept(frame(), None, None)
        recorder.finish("completed")
        recorder.close()
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in recorder.directory.iterdir()}
        report = store.verify_capture(recorder.directory)
        self.assertTrue(report["raw_verified"])
        self.assertFalse(report["hardware_verified"])
        self.assertIn("synthetic_evidence", report["qc_flags"])
        self.assertEqual(report["synchronization"], "unverified")
        after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in recorder.directory.iterdir()}
        self.assertEqual(before, after)
        self.assertEqual(json.loads((recorder.directory / "notifications.jsonl").read_text())["raw_hex"], "00ff01")

    def test_tampering_detected_and_no_samples_exported(self):
        recorder = self.recorder()
        recorder.accept(frame(), None, None)
        recorder.finish("completed")
        recorder.close()
        path = recorder.directory / "raw_frames.jsonl"
        path.write_bytes(path.read_bytes().replace(b'"context": "live"', b'"context": "other"'))
        report = store.verify_capture(recorder.directory)
        self.assertFalse(report["raw_verified"])
        result = store.derive(recorder.directory, self.root / "derived")
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(result["imu_samples"], 0)

    def test_verify_recomputes_samples_and_checks_all_evidence(self):
        recorder = self.recorder()
        recorder.accept(frame(), None, None)
        recorder.finish("completed")
        recorder.close()
        path = recorder.directory / "capture.json"
        manifest = json.loads(path.read_text())
        manifest["imu_samples"] = 999
        path.write_text(json.dumps(manifest), encoding="utf-8")
        result = store.verify_capture(recorder.directory)
        self.assertEqual(result["imu_samples"], 1)
        self.assertIn("manifest_summary_mismatch", result["qc_flags"])
        (recorder.directory / "notifications.jsonl").unlink()
        self.assertFalse(store.verify_capture(recorder.directory)["raw_verified"])

    def test_cursor_syncs_all_logs_before_atomic_replace(self):
        recorder = self.recorder()
        recorder.accept(frame(flags=2, request_id=9), None, None)
        synced = set()
        original_sync, original_replace = os.fsync, os.replace
        def sync(fd):
            synced.add(fd)
            return original_sync(fd)
        def replace(source, target):
            if Path(target).name == "cursor.json":
                self.assertTrue({s.fileno() for s in recorder._handles.values()} <= synced)
                self.assertEqual(json.loads(Path(source).read_text())["sequence"], 7)
            return original_replace(source, target)
        with patch.object(store.os, "fsync", side_effect=sync), patch.object(store.os, "replace", side_effect=replace):
            recorder.commit_cursor(2, 7, 9)
        self.assertEqual(json.loads((recorder.directory / "cursor.json").read_text()),
                         {"capture_id": 2, "sequence": 7, "request_id": 9})
        result = recorder.finish("completed")
        self.assertIn("cursor.json", result["files"])

    def test_fsync_failure_preserves_previous_cursor(self):
        recorder = self.recorder()
        recorder.commit_cursor(2, 7, 9)
        before = (recorder.directory / "cursor.json").read_bytes()
        recorder.accept(frame(flags=2, request_id=10), None, None)
        with patch.object(store.os, "fsync", side_effect=OSError("disk failed")), self.assertRaises(OSError):
            recorder.commit_cursor(2, 8, 10)
        self.assertEqual((recorder.directory / "cursor.json").read_bytes(), before)

    def test_atomic_replace_failure_preserves_previous_cursor_and_cleans_temp(self):
        recorder = self.recorder()
        recorder.commit_cursor(2, 7, 9)
        before = (recorder.directory / "cursor.json").read_bytes()
        with patch.object(store.os, "replace", side_effect=OSError("replace failed")), self.assertRaises(OSError):
            recorder.commit_cursor(3, 1, 10)
        self.assertEqual((recorder.directory / "cursor.json").read_bytes(), before)
        self.assertEqual(list(recorder.directory.glob(".cursor.json.*")), [])

    def test_missing_manifest_is_not_verified(self):
        self.assertFalse(store.verify_capture(self.root / "missing")["raw_verified"])

    def test_invalid_host_timestamp_is_preserved_not_coerced(self):
        recorder = self.recorder()
        with self.assertRaises(ValueError):
            recorder.accept(frame(), "bad", None)
        receipt = json.loads((recorder.directory / "raw_frames.jsonl").read_text())
        self.assertEqual(receipt["host_utc_ns"], "bad")

    def test_device_direction_validation_is_preserved(self):
        source = self.root / "request.jsonl"
        source.write_text(json.dumps({"raw_hex": VECTOR_A}) + "\n")
        result = store.import_frames(source, self.root / "capture")
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(result["frames_received"], 1)

    def test_import_continues_after_bad_line_and_original_hash_is_checked(self):
        source = self.root / "input.jsonl"
        source.write_bytes(b"\xff\n" + json.dumps({"raw_hex": frame().hex()}).encode() + b"\n")
        result = store.import_frames(source, self.root / "capture")
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(result["imu_samples"], 1)
        (self.root / "capture/source.jsonl").write_bytes(b"changed")
        self.assertFalse(store.verify_capture(self.root / "capture")["raw_verified"])

    def test_finished_capture_cannot_mutate_and_outputs_are_exclusive(self):
        recorder = self.recorder()
        recorder.finish("completed")
        with self.assertRaises(ValueError):
            recorder.accept(frame(), None, None)
        with self.assertRaises(ValueError):
            recorder.commit_cursor(2, 2, 1)
        store.derive(recorder.directory, self.root / "derived")
        with self.assertRaises(FileExistsError):
            store.derive(recorder.directory, self.root / "derived")
        with self.assertRaises(ValueError):
            store.derive(recorder.directory, recorder.directory / "derived")

    def test_incomplete_capture_preserves_valid_data_but_marks_qc_and_failure(self):
        for status in ("failed", "interrupted", "cancelled", "recording"):
            with self.subTest(status=status):
                recorder = store.Recorder(self.root / status, {"evidence_kind": "synthetic"})
                self.addCleanup(recorder.close)
                recorder.accept(frame(), None, None)
                recorder.finish(status, "transport ended early")
                result = store.derive(recorder.directory, self.root / (status + "_derived"))
                self.assertTrue(result["raw_verified"])
                self.assertEqual(result["capture_status"], status)
                self.assertEqual(result["capture_error"], "transport ended early")
                self.assertEqual(result["imu_samples"], 1)
                self.assertEqual(result["exit_code"], 1)
                self.assertIn("capture_not_completed", result["qc_flags"])
                self.assertIn("capture_error", result["qc_flags"])
                if status in ("interrupted", "cancelled"):
                    self.assertIn("capture_interrupted", result["qc_flags"])


if __name__ == "__main__":
    unittest.main()
