"""Synthetic transport tests. Never open a physical Bluetooth adapter."""

import asyncio
import json
import csv
from pathlib import Path
import struct
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ering_capture as capture
import ering_protocol as protocol
from ering_store import Recorder, verify_capture, derive


def frame(kind, payload=b"", flags=0, seq=0, generation=2, rid=0, stamp=1000000):
    header = struct.pack("<HBBHHIQQIHH", 0xE741, 2, kind, flags, len(payload), seq,
                         generation, stamp, rid, 1, 1)
    raw = header + payload
    return raw + struct.pack("<I", zlib.crc32(raw))


def imu(seq=1, flags=0, rid=0):
    return frame(0x11, struct.pack("<QIBBH6h", 1000000, 20000, 1, 0, 0,
                                  -32768, 100, 32767, -1, 0, 1), flags, seq, rid=rid)


class MemoryRecorder:
    def __init__(self):
        self.frames, self.events, self.fragments, self.cursors = [], [], [], []

    def notification(self, data, utc, mono):
        self.fragments.append(data)

    def accept(self, raw, utc, mono, context="live"):
        f = protocol.decode_frame(raw)
        self.frames.append(f)
        return f

    def event(self, kind, **fields):
        self.events.append({"kind": kind, **fields})

    def commit_cursor(self, generation, sequence, request_id):
        self.cursors.append((generation, sequence, request_id))


class FakeClient:
    is_connected = True

    def __init__(self, mode="normal"):
        self.mode = mode
        self.rx = protocol.Reassembler()
        self.callback = None
        self.writes, self.requests = [], []
        self.stopped = False
        self.fid = 0
        self.replay_requests = 0

    async def start_notify(self, uuid, callback):
        assert uuid == protocol.TX_UUID
        self.callback = callback

    async def stop_notify(self, uuid):
        self.stopped = True

    def emit(self, raw):
        self.fid += 1
        for fragment in protocol.fragment_frame(raw, self.fid):
            self.callback(None, bytearray(fragment))

    def emit_bad_crc(self, raw):
        self.fid += 1
        fragments = [bytearray(f) for f in protocol.fragment_frame(raw, self.fid)]
        fragments[-1][-1] ^= 1
        for fragment in fragments:
            self.callback(None, fragment)

    async def write_gatt_char(self, uuid, data, response):
        assert response is True and uuid == protocol.RX_UUID and len(data) <= 20
        self.writes.append(bytes(data))
        raw = self.rx.feed(data)
        if raw is None:
            return
        kind, rid = raw[3], struct.unpack_from("<I", raw, 28)[0]
        payload = raw[36:-4]
        self.requests.append(kind)
        if self.mode == "timeout":
            return
        if self.mode == "bad_crc":
            self.emit_bad_crc(imu())
            return
        if self.mode == "error":
            self.emit(frame(0x7F, struct.pack("<HBBI", 7, kind, 0, 0), flags=8, rid=rid))
            return
        if kind == 1:
            answer = struct.pack("<HIIHBBI", 0, 0x20000, 2 | 16 | 64, 236, 1, 0, 3600)
        elif kind == 2:
            answer = struct.pack("<QQ", 1000000, 1000001)
        elif kind == 3:
            answer = struct.pack("<HHBBHHH", 25, 50, 7, 0, 1, 4, 500)
        elif kind == 5:
            oldest = 2 if self.mode.startswith("stale") and self.replay_requests else 1
            answer = struct.pack("<QIQIHH", 2, oldest, 2, oldest + 2, 1, 0)
            # A linked POWER_STATUS may precede the response; it is not the answer.
            self.emit(frame(0x15, struct.pack("<IiBBhhHI", 3890000, 31000000, 1, 3, 0, 0, 0, 0), rid=rid))
        elif kind == 0x30:
            answer = b"\x01\x04\x00\x00\x00\x00"
        elif kind == 4:
            answer = payload
            if payload[0]:
                self.emit(imu(seq=4 if self.mode.startswith("stale") else 3))
                # Unrelated response must never satisfy the LIVE_CONTROL waiter.
                self.emit(frame(1, struct.pack("<HIIHBBI", 0, 0x20000, 2, 236, 1, 0, 3600), flags=1, rid=999))
        elif kind == 6:
            self.replay_requests += 1
            if self.mode == "stale_always" or (self.mode == "stale_once" and self.replay_requests == 1):
                self.emit(frame(0x7F, struct.pack("<HBBI", 9, 6, 0, 0), flags=8, rid=rid))
                return
            if self.mode == "nonpersistent_replay":
                self.emit(frame(0x15, bytes(20), flags=2, seq=1, rid=rid))
                return
            first = 2 if self.mode.startswith("stale") else 1
            self.emit(imu(first, 2, rid))
            # Duplicate transmissions still count twice on the wire.
            self.emit(imu(first, 2, rid))
            self.emit(frame(0x60, b"\xaa\x55", flags=2, seq=first + 1, rid=rid))
            count = 2 if self.mode == "bad_count" else 3
            result = 1 if self.mode == "interrupted" else 0
            self.emit(frame(7, struct.pack("<QIHH", 2, first + 2, count, result),
                            flags=4 if self.mode == "more" else 0, rid=rid))
            return
        else:
            raise AssertionError(kind)
        self.emit(frame(kind, answer, flags=5 if self.mode == "more" else 1, rid=rid))


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_crc_arrives_and_full_frame_is_archived_as_invalid(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "bad"
            recorder = Recorder(directory, {"evidence_kind": "synthetic"})
            client = FakeClient("bad_crc")
            try:
                with self.assertRaises(ValueError):
                    await capture.collect(client, recorder, 0.001)
                summary = recorder.finish("failed", "synthetic bad CRC")
            finally:
                recorder.close()
            self.assertEqual(summary["frames_received"], 1)
            self.assertEqual(summary["invalid_frames"], 1)
            self.assertEqual(summary["imu_samples"], 0)
            self.assertGreater(len((directory / "notifications.jsonl").read_text().splitlines()), 1)
            self.assertEqual(len((directory / "validation.jsonl").read_text().splitlines()), 1)
            self.assertFalse((directory / "cursor.json").exists())

    async def test_more_response_and_done_complete_without_affecting_replay_count(self):
        recorder = MemoryRecorder()
        await capture.collect(FakeClient("more"), recorder, 0.001, replay_pages=1)
        self.assertEqual(recorder.cursors[0][:2], (2, 3))
        self.assertTrue(any(f["type"] == 3 and f["flags"] == 5 for f in recorder.frames))

    async def test_stale_cursor_refreshes_records_coverage_and_retries_bounded(self):
        client, recorder = FakeClient("stale_once"), MemoryRecorder()
        await capture.collect(client, recorder, 0.001, replay_pages=2)
        self.assertEqual(client.replay_requests, 2)
        self.assertEqual(len(recorder.cursors), 1)
        self.assertEqual(recorder.cursors[0][:2], (2, 4))
        stale = next(e for e in recorder.events if e["kind"] == "stale_cursor")
        self.assertEqual(stale["overwritten_interval"], [[2, 1], [2, 2]])
        self.assertEqual(client.requests.count(5), 3)  # initial, refresh, final

    async def test_repeated_stale_does_not_commit_or_loop_forever(self):
        client, recorder = FakeClient("stale_always"), MemoryRecorder()
        await capture.collect(client, recorder, 0.001, replay_pages=2)
        self.assertEqual(client.replay_requests, 2)
        self.assertEqual(recorder.cursors, [])
        summary = next(e for e in recorder.events if e["kind"] == "replay_summary")
        self.assertFalse(summary["snapshot_complete"])
        self.assertIn(4, client.requests)  # live remains usable after bounded backlog attempts

    async def test_full_fake_ble_writes_originals_derives_live_only(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "capture"
            recorder = Recorder(directory, {"evidence_kind": "synthetic"})
            try:
                await capture.collect(FakeClient(), recorder, 0.001, replay_pages=1)
                recorder.finish("completed")
            finally:
                recorder.close()
            evidence = verify_capture(directory)
            self.assertTrue(evidence["raw_verified"])
            self.assertEqual(evidence["live_imu_samples"], 1)
            self.assertEqual(evidence["imu_samples"], 2)
            result = derive(directory, Path(root) / "derived")
            self.assertEqual(result["imu_samples"], 1)
            with (Path(root) / "derived" / "imu.csv").open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["sequence"], "3")

    async def test_handshake_interleaved_notifications_and_time_evidence(self):
        client, recorder = FakeClient(), MemoryRecorder()
        await capture.collect(client, recorder, 0.001)
        self.assertTrue(client.stopped)
        self.assertEqual(client.requests[:6], [1, 2, 3, 5, 0x30, 4])
        self.assertEqual(len([f for f in recorder.frames if f["type"] == 0x11]), 1)
        records = [e for e in recorder.events if e["kind"] == "time_sync"]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["capture_id"], 2)
        self.assertEqual(records[0]["synchronization"], "unverified")
        self.assertGreaterEqual(records[0]["host_round_trip_ns"], 0)
        self.assertNotIn(0x40, client.requests)
        self.assertNotIn(0x31, client.requests)
        self.assertNotIn(0x23, client.requests)

    async def test_replay_counts_wire_duplicates_and_unknown_type(self):
        client, recorder = FakeClient(), MemoryRecorder()
        await capture.collect(client, recorder, 0.001, replay_pages=2)
        self.assertEqual(len(recorder.cursors), 1)
        self.assertEqual(recorder.cursors[0][:2], (2, 3))
        summary = next(e for e in recorder.events if e["kind"] == "replay_summary")
        self.assertTrue(summary["snapshot_complete"])

    async def test_failed_replay_never_commits(self):
        for mode in ("bad_count", "interrupted", "nonpersistent_replay"):
            with self.subTest(mode=mode):
                client, recorder = FakeClient(mode), MemoryRecorder()
                with self.assertRaises((ValueError, RuntimeError)):
                    await capture.collect(client, recorder, 0.001, replay_pages=1)
                self.assertEqual(recorder.cursors, [])
                self.assertTrue(client.stopped)

    async def test_crc_command_error_and_timeout_fail_closed(self):
        for mode in ("bad_crc", "error", "timeout"):
            with self.subTest(mode=mode):
                client, recorder = FakeClient(mode), MemoryRecorder()
                with self.assertRaises((ValueError, RuntimeError, TimeoutError)):
                    await capture.collect(client, recorder, 0.001, timeout=0.02)
                self.assertEqual(recorder.cursors, [])
                self.assertTrue(client.stopped)

    async def test_disconnect_during_live_ends_without_waiting_duration(self):
        client, recorder, disconnected = FakeClient(), MemoryRecorder(), asyncio.Event()
        asyncio.get_running_loop().call_later(0.02, disconnected.set)
        with self.assertRaises(ConnectionError):
            await asyncio.wait_for(capture.collect(client, recorder, 100, disconnected_event=disconnected), 1)

    async def test_capture_missing_device_preserves_failure_artifact(self):
        class Scanner:
            @staticmethod
            async def find_device_by_address(*args, **kwargs):
                return None
        with tempfile.TemporaryDirectory() as root, patch.object(capture, "bleak_types", return_value=(None, Scanner)):
            directory = Path(root) / "capture"
            result = await capture.capture(directory, "synthetic", 1)
            self.assertEqual(result["exit_code"], 1)
            self.assertTrue((directory / "capture.json").is_file())
            self.assertIn("not found", str(result))

    async def test_invalid_inputs_do_not_create_capture(self):
        with tempfile.TemporaryDirectory() as root:
            for duration in (0, -1, float("nan"), float("inf")):
                path = Path(root) / "invalid"
                with self.assertRaises(ValueError):
                    await capture.capture(path, "synthetic", duration)
                self.assertFalse(path.exists())


class CommandIntegrationTests(unittest.TestCase):
    def test_real_cli_import_derive_status_and_no_overwrite(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "experiment_session.py"
        def run(*args):
            proc = subprocess.run([sys.executable, "-B", str(script), *map(str, args)],
                                  capture_output=True, text=True, encoding="utf-8")
            return proc, json.loads(proc.stdout or proc.stderr)
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            proc, created = run("create", "--root", root / "episodes", "--subject", "SYNTHETIC",
                                "--session", "OFFLINE", "--trial", "T001", "--task", "fake",
                                "--wear-side", "unknown", "--consent-scope", "synthetic_no_human")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            ep = created["episode"]
            source = root / "frames.jsonl"
            config = frame(3, struct.pack("<HHBBHHH", 25, 50, 7, 0, 1, 4, 500), flags=1, rid=1)
            source.write_text("\n".join(json.dumps({"raw_hex": raw.hex()}) for raw in (config, imu())), encoding="utf-8")
            proc, result = run("ring-import", "--episode", ep, "--capture-id", "ring01", "--file", source)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            proc, result = run("ring-derive", "--episode", ep, "--capture-id", "ring01", "--derive-id", "decode01")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(result["imu_samples"], 1)
            proc, result = run("status", "--episode", ep)
            self.assertEqual(proc.returncode, 0)
            self.assertTrue(result["modality_present"]["ring"])
            self.assertFalse(result["modality_present"]["armband"])
            self.assertFalse(result["hardware_sync_verified"])
            self.assertEqual(result["ring_derived"][0]["derive_id"], "decode01")
            self.assertTrue(any(s.startswith("ring_derive:decode01:") for s in result["issues"]))
            self.assertIn("device_monotonic_us", result["device_clock_source"]["ring"])
            proc, _ = run("ring-import", "--episode", ep, "--capture-id", "ring01", "--file", source)
            self.assertNotEqual(proc.returncode, 0)

    def test_status_replay_only_is_not_current_trial_ring_data(self):
        import argparse
        import experiment_session
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            result = experiment_session.create(argparse.Namespace(root=root, subject="SYNTHETIC",
                       session="S1", trial="T1", task="fake", consent_scope="synthetic",
                       wear_side="unknown", notes="synthetic"))
            ep = Path(result["episode"])
            recorder = Recorder(ep / "ring_captures" / "backlog", {"evidence_kind": "synthetic"})
            recorder.accept(imu(1, 2, 9), None, None)
            recorder.finish("completed")
            recorder.close()
            status = experiment_session.status(argparse.Namespace(episode=ep))
            self.assertFalse(status["modality_present"]["ring"])

    def test_status_reports_invalid_ring_time_and_broken_derived_report(self):
        import argparse
        import experiment_session
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            result = experiment_session.create(argparse.Namespace(root=root, subject="SYNTHETIC",
                     session="S1", trial="T1", task="fake", consent_scope="synthetic", wear_side="unknown", notes=""))
            ep = Path(result["episode"])
            recorder = Recorder(ep / "ring_captures" / "period0", {"evidence_kind": "synthetic"})
            payload = struct.pack("<QIBBH12h", 1000, 0, 2, 0, 0, *range(12))
            recorder.accept(frame(0x11, payload, seq=1, stamp=1000), None, None)
            recorder.finish("completed")
            recorder.close()
            result = derive(ep / "ring_captures" / "period0", ep / "ring_derived" / "badtime")
            self.assertNotEqual(result["exit_code"], 0)
            broken = ep / "ring_derived" / "broken"
            broken.mkdir()
            (broken / "qc.json").write_text("{")
            status = experiment_session.status(argparse.Namespace(episode=ep))
            self.assertIn("ring_derive:badtime:invalid_nominal_period", status["issues"])
            self.assertTrue(any(s.startswith("ring_derive:broken:") for s in status["issues"]))


if __name__ == "__main__":
    unittest.main()
