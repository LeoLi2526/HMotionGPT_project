"""Offline synthetic fixtures only: these checks are NOT hardware evidence."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import csv
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import experiment_session as tool


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "implement-stage" / "session-capture")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def cli(self, *args, code=0):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            actual = tool.main([str(arg) for arg in args])
        self.assertEqual(actual, code, err.getvalue())
        return json.loads(out.getvalue() if actual == 0 else err.getvalue() or out.getvalue())

    def create(self, subject="subject_a", session="day1", trial="trial1", **acquisition):
        options = [arg for key, value in acquisition.items() for arg in ("--" + key.replace("_", "-"), value)]
        result = self.cli("create", "--root", self.root / "episodes", "--subject", subject,
                          "--session", session, "--trial", trial, "--task", "synthetic-test",
                          "--wear-side", "left", "--consent-scope", "synthetic fixtures only", *options)
        return Path(result["episode"])

    def test_acquisition_defaults_are_engineering_and_unverified(self):
        episode = self.create()
        expected = {"purpose": "engineering", "ring_mount": "unknown",
                    "ring_power": "unknown", "wear_calibration": "unverified"}
        self.assertEqual(tool.read_json(episode / "episode.json")["acquisition_context"], expected)
        status = self.cli("status", "--episode", episode)
        self.assertEqual(status["acquisition_context"], expected)
        self.assertEqual(status["data_use"], {"decision": "engineering_only", "training_eligible": False})

    def test_acquisition_bare_board_overrides_research_for_all_power_modes(self):
        for power in ("tethered", "battery", "unknown"):
            with self.subTest(power=power):
                episode = self.create(trial=power, purpose="research", ring_mount="bare_board", ring_power=power)
                recorded = tool.read_json(episode / "episode.json")["acquisition_context"]
                self.assertEqual(recorded, {"purpose": "research", "ring_mount": "bare_board",
                                            "ring_power": power, "wear_calibration": "unverified"})
                status = self.cli("status", "--episode", episode)
                self.assertEqual(status["data_use"], {"decision": "engineering_only", "training_eligible": False})

    def test_acquisition_worn_pilot_and_research_never_eligible_regardless_of_power(self):
        for purpose in ("pilot", "research"):
            for power in ("tethered", "battery", "unknown"):
                with self.subTest(purpose=purpose, power=power):
                    episode = self.create(trial=purpose + power, purpose=purpose, ring_mount="worn", ring_power=power)
                    status = self.cli("status", "--episode", episode)
                    self.assertEqual(status["acquisition_context"]["ring_power"], power)
                    self.assertEqual(status["acquisition_context"]["wear_calibration"], "unverified")
                    self.assertEqual(status["data_use"], {"decision": "pending_validation", "training_eligible": False})
                    self.assertEqual(status["issues"], [])

    def test_acquisition_legacy_missing_metadata_stays_unknown_and_readonly(self):
        episode = self.create()
        path = episode / "episode.json"
        metadata = tool.read_json(path)
        del metadata["acquisition_context"]
        path.write_text(json.dumps(metadata), encoding="utf-8")
        before = path.read_bytes()
        status = self.cli("status", "--episode", episode)
        self.assertEqual(status["acquisition_context"], {"purpose": "unknown", "ring_mount": "unknown",
                                                        "ring_power": "unknown", "wear_calibration": "unverified"})
        self.assertEqual(status["data_use"], {"decision": "pending_validation", "training_eligible": False})
        self.assertEqual(path.read_bytes(), before)

    def test_acquisition_invalid_or_partial_context_cannot_grant_acceptance(self):
        episode = self.create()
        path = episode / "episode.json"
        metadata = tool.read_json(path)
        for context in (None, [], "research", {}, {"purpose": ["research"], "ring_mount": "invalid"},
                        {"purpose": "research", "wear_calibration": "verified", "training_eligible": True}):
            with self.subTest(context=context):
                metadata["acquisition_context"] = context
                metadata["data_use"] = {"decision": "accepted", "training_eligible": True}
                path.write_text(json.dumps(metadata), encoding="utf-8")
                status = self.cli("status", "--episode", episode)
                self.assertEqual(status["acquisition_context"]["ring_mount"], "unknown")
                self.assertEqual(status["acquisition_context"]["wear_calibration"], "unverified")
                self.assertEqual(status["data_use"], {"decision": "pending_validation", "training_eligible": False})

    def test_acquisition_cli_rejects_invalid_choices_without_creating_episode(self):
        for option in ("--purpose", "--ring-mount", "--ring-power"):
            with self.subTest(option=option), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as error:
                    tool.main(["create", "--root", str(self.root / "episodes"), "--subject", "s",
                               "--session", "d", "--trial", "t", "--task", "test", "--wear-side", "unknown",
                               "--consent-scope", "synthetic", option, "invalid"])
                self.assertEqual(error.exception.code, 2)
                self.assertFalse((self.root / "episodes").exists())

    def test_f0_multi_subject_session_isolation_and_no_overwrite(self):
        a = self.create()
        b = self.create(subject="subject_b")
        c = self.create(session="day2")
        self.assertEqual(len({a, b, c}), 3)
        before = (a / "episode.json").read_bytes()
        self.cli("create", "--root", self.root / "episodes", "--subject", "subject_a",
                 "--session", "day1", "--trial", "trial1", "--task", "different",
                 "--wear-side", "right", "--consent-scope", "test", code=1)
        self.assertEqual((a / "episode.json").read_bytes(), before)

    def test_f0_real_entrypoint_creates_artifact(self):
        result = subprocess.run([sys.executable, "-B", str(Path(tool.__file__)), "create",
                                 "--root", str(self.root), "--subject", "s", "--session", "d",
                                 "--trial", "t", "--task", "test", "--wear-side", "unknown",
                                 "--consent-scope", "synthetic"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        episode = Path(json.loads(result.stdout)["episode"])
        self.assertTrue((episode / "episode.json").is_file())

    def test_b001_rejects_case_conflict_at_each_identity_level(self):
        episode = self.create(subject="P001", session="day1", trial="T001")
        before = (episode / "episode.json").read_bytes()
        for subject, session, trial in (("p001", "day1", "T002"),
                                        ("P001", "DAY1", "T002"),
                                        ("P001", "day1", "t001")):
            with self.subTest(subject=subject, session=session, trial=trial):
                result = self.cli("create", "--root", self.root / "episodes", "--subject", subject,
                                  "--session", session, "--trial", trial, "--task", "synthetic",
                                  "--wear-side", "left", "--consent-scope", "synthetic", code=1)
                self.assertIn("ID spelling conflict", result["error"])
        self.assertEqual((episode / "episode.json").read_bytes(), before)
        self.assertEqual([p.name for p in (self.root / "episodes").iterdir()], ["P001"])
        self.assertEqual([p.name for p in episode.parent.iterdir()], ["T001"])
        second = self.create(subject="P001", session="day1", trial="T002")
        self.assertEqual(tool.read_json(second / "episode.json")["subject"], "P001")
        source = self.capture_fixture(second, capture_id="ArmMixedCase")
        self.assertEqual(source.parent.parent.name, "ArmMixedCase")

    def test_invalid_identifiers(self):
        for value in ("../escape", "CON", "com1", "", "a/b"):
            with self.assertRaises(ValueError):
                tool.identifier(value)

    def test_f1_event_clock_semantics(self):
        episode = self.create()
        with patch.object(tool.time, "time_ns", return_value=123), patch.object(tool.time, "monotonic_ns", return_value=456):
            event = self.cli("mark", "--episode", episode, "--event", "grasp", "--source", "keyboard")
        self.assertEqual(event["host_utc_ns"], 123)
        self.assertEqual(event["host_monotonic_ns"], 456)
        self.assertFalse(event["hardware_sync"])
        second = self.cli("mark", "--episode", episode, "--event", "release")
        self.assertNotEqual(event["event_id"], second["event_id"])
        self.assertEqual(len(list((episode / "events").glob("*.json"))), 2)

    def test_f1_attachment_copy_hash_and_overwrite_refusal(self):
        episode = self.create()
        source = self.root / "attachment.json"
        source.write_bytes(b"synthetic raw fixture")
        args = ("attach", "--episode", episode, "--modality", "ring", "--attachment-id", "part1", "--file", source)
        result = self.cli(*args)
        copied = Path(result["attachment"]) / result["raw_file"]
        self.assertEqual(copied.read_bytes(), source.read_bytes())
        self.assertEqual(tool.sha256(copied), result["sha256"])
        self.assertEqual(result["device_clock_source"], "unknown")
        source.write_bytes(b"changed source")
        self.cli(*args, code=1)
        self.assertEqual(copied.read_bytes(), b"synthetic raw fixture")

    def capture_fixture(self, episode, pieces=None, capture_id="cap1"):
        pieces = list(pieces if pieces is not None else [b"1 2 3 4 5 6 7 8\n"])
        class FakePort:
            is_open = False
            @property
            def in_waiting(self):
                return len(pieces[0]) if pieces else 0
            def open(self):
                self.is_open = True
            def read(self, count):
                return pieces.pop(0) if pieces else b""
            def close(self):
                self.is_open = False
            def write(self, data):
                raise AssertionError("No device commands allowed")
        port = FakePort()
        serial = SimpleNamespace(serial_for_url=lambda *a, **kw: port, SerialException=OSError,
                                 EIGHTBITS=8, PARITY_NONE="N", STOPBITS_ONE=1)
        with patch.object(tool.sichiray_capture, "require_serial", return_value=serial):
            result = self.cli("capture", "--episode", episode, "--capture-id", capture_id,
                              "--port", "COM6", "--seconds", "0.005")
        self.assertFalse(port.is_open)
        directory = Path(result["capture"])
        return next((directory / "source").iterdir())

    def test_f2_existing_capture_preserves_raw_chunks_and_refuses_overwrite(self):
        episode = self.create()
        pieces = [b"1 2 3 ", b"4 5 6 7 8\n"]
        source = self.capture_fixture(episode, pieces)
        self.assertEqual((source / "serial_raw.bin").read_bytes(), b"".join(pieces))
        chunks = [json.loads(x) for x in (source / "receive_chunks.jsonl").read_text().splitlines()]
        self.assertEqual([x["offset"] for x in chunks], [0, len(pieces[0])])
        self.assertTrue(all("host_receive_monotonic_ns" in x for x in chunks))
        with patch.object(tool.sichiray_capture, "capture") as backend:
            self.cli("capture", "--episode", episode, "--capture-id", "cap1",
                     "--port", "COM6", "--seconds", "3", code=1)
            backend.assert_not_called()

    def test_f2_failure_logged_and_duration_alias(self):
        episode = self.create()
        with patch.object(tool.sichiray_capture, "require_serial", side_effect=SystemExit("missing test dependency")):
            result = self.cli("capture", "--episode", episode, "--capture-id", "failed",
                              "--port", "COM6", "--duration", "3", code=1)
        directory = Path(result["capture"])
        self.assertIn("missing test dependency", (directory / "stderr.log").read_text())
        self.assertEqual(tool.read_json(directory / "result.json")["exit_code"], 1)

    def test_f2_invalid_duration_never_calls_backend(self):
        episode = self.create()
        with patch.object(tool.sichiray_capture, "capture") as backend:
            for duration in ("nan", "inf", "0", "-1"):
                self.cli("capture", "--episode", episode, "--capture-id", "bad",
                         "--port", "COM6", "--seconds", duration, code=1)
            backend.assert_not_called()

    def deterministic_chunks(self, source, pieces, utc=None):
        offset = 0
        chunks = []
        for index, piece in enumerate(pieces):
            chunks.append({"offset": offset, "bytes": len(piece),
                           "host_receive_utc_ns": utc[index] if utc else 1000 + index,
                           "host_receive_monotonic_ns": 2000 + index})
            offset += len(piece)
        (source / "receive_chunks.jsonl").write_text("".join(json.dumps(c) + "\n" for c in chunks), encoding="utf-8")

    def test_f3_cross_chunk_completion_mode_changes_and_fragments(self):
        episode = self.create()
        lines = [b"1 2 3 4 5 6 7 8\r\n", b"1 2 3 4 5 6 7 8 9\n",
                 b"0 1 2 3 4 5 6 7 8 9 10 11 12 13\n", b"bad line\n", b"1 2 3"]
        pieces = [lines[0][:-1], lines[0][-1:] + lines[1] + lines[2] + lines[3] + lines[4]]
        source = self.capture_fixture(episode, pieces)
        self.deterministic_chunks(source, pieces)
        result = self.cli("derive", "--episode", episode, "--capture-id", "cap1", "--derive-id", "v1")
        directory = Path(result["derived"])
        with (directory / "rows.csv").open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([int(row["column_count"]) for row in rows], [8, 9, 14])
        self.assertEqual([int(row["raw_offset"]) for row in rows], [0, len(lines[0]), len(lines[0] + lines[1])])
        self.assertEqual([row["host_receive_completion_utc_ns"] for row in rows], ["1001"] * 3)
        self.assertEqual(rows[0]["host_receive_completion_monotonic_ns"], "2001")
        self.assertIn("cross_chunk", rows[0]["qc_flags"])
        self.assertEqual(rows[0]["value_9"], "")
        self.assertEqual(rows[1]["value_9"], "9")
        self.assertEqual(rows[2]["value_14"], "13")
        self.assertTrue(all(row["device_time_ns"] == "" for row in rows))
        self.assertIn("mixed_column_widths", result["qc_flags"])
        self.assertEqual(len(result["mode_changes"]), 2)
        self.assertEqual(tool.read_json(directory / "invalid_lines.json")[0]["row_index"], 3)
        fragment = tool.read_json(directory / "fragments.json")[0]
        self.assertEqual(fragment["row_index"], 4)
        self.assertEqual(fragment["raw_hex"], lines[4].hex())
        self.cli("derive", "--episode", episode, "--capture-id", "cap1", "--derive-id", "v1", code=1)

    def test_f3_invalid_line_preserves_physical_row_index(self):
        episode = self.create()
        self.capture_fixture(episode, [b"partial\n1 2 3 4 5 6 7 8\n"])
        result = self.cli("derive", "--episode", episode, "--capture-id", "cap1", "--derive-id", "v1")
        with (Path(result["derived"]) / "rows.csv").open(newline="") as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(row["row_index"], "1")
        self.assertEqual(row["raw_offset"], "8")
        self.assertEqual(result["fragment_count"], 1)

    def test_f3_corrupt_chunks_and_raw_hash_rejected(self):
        episode = self.create()
        source = self.capture_fixture(episode)
        chunks_path = source / "receive_chunks.jsonl"
        chunk = json.loads(chunks_path.read_text())
        chunk["offset"] = 1
        chunks_path.write_text(json.dumps(chunk) + "\n")
        self.cli("derive", "--episode", episode, "--capture-id", "cap1", "--derive-id", "bad", code=1)
        self.assertFalse((episode / "derived" / "bad").exists())
        (source / "serial_raw.bin").write_bytes(b"tampered")
        error = self.cli("derive", "--episode", episode, "--capture-id", "cap1", "--derive-id", "bad", code=1)
        self.assertIn("SHA-256 mismatch", error["error"])

    def test_f3_utc_regression_preserved_not_corrected(self):
        episode = self.create()
        pieces = [b"1 2 3 4 5 6 7 8\n", b"1 2 3 4 5 6 7 8\n"]
        source = self.capture_fixture(episode, pieces)
        self.deterministic_chunks(source, pieces, utc=[1000, 999])
        result = self.cli("derive", "--episode", episode, "--capture-id", "cap1", "--derive-id", "v1")
        self.assertIn("host_utc_regression", result["qc_flags"])
        with (Path(result["derived"]) / "rows.csv").open(newline="") as stream:
            self.assertEqual([row["host_receive_completion_utc_ns"] for row in csv.DictReader(stream)], ["1000", "999"])

    def test_f3_unterminated_valid_width_is_fragment_not_sample(self):
        episode = self.create()
        self.capture_fixture(episode, [b"1 2 3 4 5 6 7 8"])
        result = self.cli("derive", "--episode", episode, "--capture-id", "cap1", "--derive-id", "v1")
        self.assertEqual(result["valid_rows"], 0)
        self.assertEqual(result["fragment_count"], 1)

    def test_f3_chunk_timestamp_validation(self):
        path = self.root / "chunks.jsonl"
        chunks = [{"offset": 0, "bytes": 2, "host_receive_utc_ns": 100, "host_receive_monotonic_ns": 20},
                  {"offset": 2, "bytes": 3, "host_receive_utc_ns": 101, "host_receive_monotonic_ns": 19}]
        path.write_text("".join(json.dumps(c) + "\n" for c in chunks))
        with self.assertRaisesRegex(ValueError, "monotonic"):
            tool.load_chunks(path, 5)
        chunks[1]["host_receive_monotonic_ns"] = 20.5
        path.write_text("".join(json.dumps(c) + "\n" for c in chunks))
        with self.assertRaisesRegex(ValueError, "integer"):
            tool.load_chunks(path, 5)

    def test_f4_status_missing_then_present_never_claims_sync(self):
        episode = self.create()
        status = self.cli("status", "--episode", episode)
        self.assertEqual(status["missing_modalities"], ["armband", "camera", "ring"])
        self.capture_fixture(episode)
        source = self.root / "fixture.bin"
        source.write_bytes(b"synthetic attachment")
        for modality in ("camera", "ring"):
            self.cli("attach", "--episode", episode, "--modality", modality,
                     "--attachment-id", "a", "--file", source)
        self.cli("mark", "--episode", episode, "--event", "synthetic-start", "--source", "keyboard")
        self.cli("derive", "--episode", episode, "--capture-id", "cap1", "--derive-id", "v1")
        status = self.cli("status", "--episode", episode)
        self.assertEqual(status["missing_modalities"], [])
        self.assertEqual(status["unknown_clock_modalities"], ["armband", "camera", "ring"])
        self.assertFalse(status["hardware_sync_verified"])
        self.assertEqual(status["synchronization"], "unverified")
        self.assertEqual(status["event_count"], 1)
        self.assertEqual(status["derived"][0]["valid_rows"], 1)
        self.assertEqual(status["data_use"], {"decision": "engineering_only", "training_eligible": False})
        # Even complete files and an operator's calibration claim cannot grant acceptance.
        path = episode / "episode.json"
        metadata = tool.read_json(path)
        metadata["acquisition_context"] = {"purpose": "research", "ring_mount": "worn",
                                           "ring_power": "battery", "wear_calibration": "verified"}
        path.write_text(json.dumps(metadata), encoding="utf-8")
        status = self.cli("status", "--episode", episode)
        self.assertEqual(status["missing_modalities"], [])
        self.assertEqual(status["data_use"], {"decision": "pending_validation", "training_eligible": False})
        del metadata["acquisition_context"]
        path.write_text(json.dumps(metadata), encoding="utf-8")
        status = self.cli("status", "--episode", episode)
        self.assertEqual(status["missing_modalities"], [])
        self.assertEqual(status["data_use"], {"decision": "pending_validation", "training_eligible": False})
        other = self.create(subject="subject_b")
        self.assertEqual(self.cli("status", "--episode", other)["missing_modalities"], ["armband", "camera", "ring"])

    def test_f4_status_detects_tamper_empty_and_pending_capture(self):
        episode = self.create()
        source = self.capture_fixture(episode)
        (source / "serial_raw.bin").write_bytes(b"changed")
        empty = self.root / "empty.bin"
        empty.write_bytes(b"")
        self.cli("attach", "--episode", episode, "--modality", "camera", "--attachment-id", "empty", "--file", empty)
        fixture = self.root / "ring.bin"
        fixture.write_bytes(b"synthetic")
        record = self.cli("attach", "--episode", episode, "--modality", "ring", "--attachment-id", "bad", "--file", fixture)
        (Path(record["attachment"]) / record["raw_file"]).write_bytes(b"tampered!")
        (episode / "captures" / "pending").mkdir()
        status = self.cli("status", "--episode", episode)
        self.assertEqual(status["missing_modalities"], ["armband", "camera", "ring"])
        self.assertTrue(any("SHA-256 mismatch" in issue for issue in status["issues"]))
        self.assertTrue(any("empty_raw" in issue for issue in status["issues"]))
        self.assertTrue(any("pending_or_incomplete" in issue for issue in status["issues"]))

    def test_f4_status_subprocess_stdout_is_json_and_readonly(self):
        episode = self.create()
        before = list(episode.rglob("*"))
        result = subprocess.run([sys.executable, "-B", str(Path(tool.__file__)), "status", "--episode", str(episode)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["synchronization"], "unverified")
        self.assertEqual(list(episode.rglob("*")), before)

    def test_b002_interrupted_event_not_counted_and_preserved(self):
        episode = self.create()
        self.cli("mark", "--episode", episode, "--event", "valid")
        broken = episode / "events" / "interrupted.json"
        broken.write_bytes(b"{")
        status = self.cli("status", "--episode", episode)
        self.assertEqual(status["event_file_count"], 2)
        self.assertEqual(status["event_count"], 1)
        self.assertIn("invalid_events", status["qc_flags"])
        self.assertTrue(any(issue.startswith("event:interrupted.json:") for issue in status["issues"]))
        self.assertEqual(broken.read_bytes(), b"{")

    def test_b002_event_required_fields_and_types_checked(self):
        episode = self.create()
        event = self.cli("mark", "--episode", episode, "--event", "valid")
        path = episode / "events" / (event["event_id"] + ".json")
        cases = [("event_id", "wrong-name"), ("event", " "), ("event", 42),
                 ("host_utc_ns", True), ("host_utc_ns", "123"),
                 ("host_monotonic_ns", 1.5), ("host_monotonic_ns", -1),
                 ("hardware_sync", True), ("hardware_sync", 0),
                 ("schema_version", True), ("source", "device"), ("notes", None),
                 ("time_semantics", ""), ("monotonic_scope", "")]
        invalid_records = [{**event, key: value} for key, value in cases]
        invalid_records += [{key: value for key, value in event.items() if key != missing} for missing in event]
        invalid_records += [[], None]
        for index, record in enumerate(invalid_records):
            with self.subTest(case=index):
                content = json.dumps(record).encode("utf-8")
                path.write_bytes(content)
                status = self.cli("status", "--episode", episode)
                self.assertEqual(status["event_file_count"], 1)
                self.assertEqual(status["event_count"], 0)
                self.assertTrue(any(issue.startswith(f"event:{path.name}:") for issue in status["issues"]))
                self.assertEqual(path.read_bytes(), content)
        path.write_text(json.dumps(event), encoding="utf-8")
        status = self.cli("status", "--episode", episode)
        self.assertEqual(status["event_count"], 1)
        self.assertNotIn("invalid_events", status["qc_flags"])


if __name__ == "__main__":
    unittest.main()
