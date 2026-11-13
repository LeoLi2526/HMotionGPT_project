"""Offline tests; fixtures use the real derive column schema, not new hardware evidence."""

import csv
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from dual_timeline import build_timeline


ARM_HEADER = "row_index,raw_offset,raw_bytes,column_count,first_chunk_index,completion_chunk_index,host_receive_completion_utc_ns,host_receive_completion_monotonic_ns,device_time_ns,device_clock_source,qc_flags," + ",".join(f"value_{i}" for i in range(1, 15))
RING_HEADER = "capture_id,sequence,sample_index,ax_raw,ay_raw,az_raw,gx_raw,gy_raw,gz_raw,first_sample_us,period_us,device_time_us,host_utc_ns,host_monotonic_ns,sample_config_id,calibration_version,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps,device_utc_ns,replay,attribution,context,receipt_index,batch_timestamp_us,time_epoch,device_time_basis,nominal_boundary_delta_us"
ARM_TIME = "host_receive_completion_monotonic_ns"
RING_TIME = "host_monotonic_ns"
ORIGIN = 339053562000000


def arm(time, **extra):
    return {"row_index": "0", "column_count": "8", "raw_offset": "0", "raw_bytes": "40",
            "first_chunk_index": "0", "completion_chunk_index": "3", ARM_TIME: str(time),
            "device_clock_source": "unknown", "qc_flags": "device_clock_unknown;cross_chunk",
            "value_1": "2041", "value_8": "2060", **extra}


def ring(time, **extra):
    return {"capture_id": "157", "sequence": "2172", "sample_index": "0", "ax_raw": "5512",
            "first_sample_us": "640283584", "period_us": "20000", "device_time_us": "640283584",
            "host_utc_ns": "1794553977634087900", RING_TIME: str(time), "sample_config_id": "1",
            "calibration_version": "1", "ax_g": "0.6728515625", "replay": "0",
            "attribution": "current_capture_unverified", "context": "ble", "receipt_index": "7",
            "batch_timestamp_us": "640283584", "time_epoch": "0",
            "device_time_basis": "nominal_reconstruction", **extra}


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.arm_path, self.ring_path = self.root / "arm.csv", self.root / "ring.csv"
        self.output = self.root / "timeline"

    def write(self, path, header, rows):
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=header.split(","))
            writer.writeheader()
            writer.writerows(rows)

    def run_timeline(self, arms, rings, origin=ORIGIN):
        self.write(self.arm_path, ARM_HEADER, arms)
        self.write(self.ring_path, RING_HEADER, rings)
        return build_timeline(self.arm_path, self.ring_path, self.output, origin)

    def read(self, name):
        with (self.output / name).open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    def test_real_schema_preserves_every_field_and_batch_time(self):
        arms = [arm(ORIGIN), arm(ORIGIN + 10_000_000_000)]
        rings = [ring(ORIGIN + 1_000_000_000),
                 ring(ORIGIN + 1_000_000_000, sample_index="1", device_time_us="640303584"),
                 ring(ORIGIN + 5_000_000_000, sequence="2173", receipt_index="8")]
        report = self.run_timeline(arms, rings)
        self.assertEqual(report["overlap_seconds"], 4)
        self.assertEqual(report["streams"]["arm"]["receive_span"]["seconds"], 10)
        self.assertEqual(report["streams"]["ring"]["rows_in_overlap"], 3)
        self.assertEqual(report["streams"]["ring"]["counts"]["rows_in_overlap"], 3)
        for name, original, header in [("arm", arms, ARM_HEADER), ("ring", rings, RING_HEADER)]:
            actual = self.read(f"{name}_receive.csv")
            self.assertEqual(len(actual), len(original))
            for before, after in zip(original, actual):
                self.assertEqual({k: after[k] for k in header.split(",")},
                                 {k: str(before.get(k, "")) for k in header.split(",")})
        self.assertEqual([r["host_relative_seconds"] for r in self.read("ring_receive.csv")],
                         ["1.000000000", "1.000000000", "5.000000000"])
        self.assertEqual(report["streams"]["ring"]["duplicate_receive_times"]["extra_rows"], 1)
        self.assertEqual(report["ring_capture_epochs"][0]["batch_receipt_groups"], 2)
        self.assertEqual(report["synchronization"], "unverified")
        self.assertEqual(report["timing_error"], "unknown")
        self.assertFalse(report["source_provenance_verified"])
        self.assertEqual(json.loads((self.output / "report.json").read_text(encoding="utf-8")), report)

    def test_zero_overlap_and_touching_endpoints(self):
        for start in (20, 10):
            self.output = self.root / str(start)
            report = self.run_timeline([arm(0), arm(10)], [ring(start), ring(start + 10)])
            self.assertEqual(report["overlap_seconds"], 0)
            self.assertIsNone(report["overlap_start_ns"])
            self.assertTrue(all(r["in_overlap"] == "0" for r in self.read("ring_receive.csv")))

    def test_missing_invalid_and_negative_times_are_reported(self):
        report = self.run_timeline([arm(0), arm(""), arm("NaN"), arm(-1), arm(10)],
                                   [ring(""), ring("1.5"), ring(2), ring(8)])
        self.assertEqual(report["overlap_seconds"], 6e-9)
        self.assertEqual(report["streams"]["arm"]["missing_host_time_rows"], 1)
        self.assertEqual(report["streams"]["arm"]["invalid_host_time_rows"], 2)
        self.assertIn("ring:missing_host_time", report["qc_flags"])
        self.assertTrue(any(i["code"] == "missing_host_time" and i["stream"] == "ring"
                            for i in report["quality_issues"]))
        for row in self.read("arm_receive.csv")[1:4]:
            self.assertEqual(row["host_relative_seconds"], "")
            self.assertEqual(row["in_overlap"], "0")

    def test_regression_withholds_overlap_without_reordering(self):
        for stream in ("arm", "ring"):
            self.output = self.root / stream
            report = self.run_timeline([arm(t) for t in ([1, 5, 3, 7] if stream == "arm" else [0, 10])],
                                       [ring(t) for t in ([1, 5, 3, 7] if stream == "ring" else [0, 10])])
            self.assertEqual(report["overlap_seconds"], 0)
            self.assertTrue(report["streams"][stream]["overlap_withheld_for_regression"])
            self.assertIn(f"{stream}:host_time_regression", report["qc_flags"])
            self.assertTrue(any(i["code"] == "host_time_regression" and i["stream"] == stream
                                for i in report["quality_issues"]))
            field = ARM_TIME if stream == "arm" else RING_TIME
            self.assertEqual([r[field] for r in self.read(f"{stream}_receive.csv")], ["1", "5", "3", "7"])

    def test_replay_nonlive_and_unknown_provenance_cannot_extend_overlap(self):
        report = self.run_timeline([arm(0), arm(100)],
            [ring(1, replay="1", attribution="unknown"), ring(20), ring(40),
             ring(99, context="eringweb_export"), ring(98, context="import"),
             ring(97, context=""), ring(96, replay=""), ring(95, attribution="unknown")])
        self.assertEqual(report["overlap_seconds"], 20e-9)
        self.assertEqual(report["streams"]["ring"]["non_realtime_rows"], 6)
        self.assertEqual([r["in_overlap"] for r in self.read("ring_receive.csv")],
                         ["0", "1", "1", "0", "0", "0", "0", "0"])
        self.assertIn("ring:host_time_regression", report["qc_flags"])
        self.assertFalse(report["streams"]["ring"]["overlap_withheld_for_regression"])
        codes = {i["code"] for i in report["quality_issues"]}
        self.assertTrue({"replay_excluded", "non_realtime_excluded"} <= codes)

    def test_replayed_same_sample_remains_excluded_inside_overlap(self):
        report = self.run_timeline([arm(0), arm(100)], [ring(10), ring(20, replay="1"), ring(30)])
        self.assertEqual(report["streams"]["ring"]["rows_in_overlap"], 2)
        self.assertEqual(self.read("ring_receive.csv")[1]["in_overlap"], "0")

    def test_device_generations_epochs_and_original_device_times(self):
        report = self.run_timeline([arm(0), arm(100)],
            [ring(10), ring(20, time_epoch="1", device_time_us="1"),
             ring(30, capture_id="158", time_epoch="0", device_time_us="0")])
        self.assertEqual(report["ring_capture_ids"], ["157", "158"])
        self.assertEqual([(g["capture_id"], g["time_epoch"]) for g in report["ring_capture_epochs"]],
                         [("157", "0"), ("157", "1"), ("158", "0")])
        self.assertEqual(report["overlap_seconds"], 20e-9)
        self.assertNotIn("ring:host_time_regression", report["qc_flags"])
        codes = {i["code"] for i in report["quality_issues"]}
        self.assertTrue({"multiple_capture_generations", "multiple_capture_epochs"} <= codes)

    def test_deduplicated_live_gap_engineering_threshold_and_counts(self):
        for gap, passed in [(2_000_000_000, True), (2_000_000_001, False)]:
            self.output = self.root / str(gap)
            report = self.run_timeline([arm(0), arm(1_000_000_000), arm(gap)],
                [ring(0), ring(0, sample_index="1"), ring(gap),
                 ring(100_000_000_000, replay="1"), ring(200_000_000_000, context="import")])
            stream = report["streams"]["ring"]
            self.assertEqual(stream["max_receive_gap_seconds"], gap / 1e9)
            self.assertEqual(stream["receive_gap_engineering_pass"], passed)
            self.assertEqual(stream["counts"]["rows"], 5)
            self.assertEqual(stream["counts"]["replay_rows"], 1)
            self.assertEqual(stream["counts"]["non_realtime_rows"], 2)
            self.assertEqual(stream["counts"]["distinct_realtime_receive_times"], 2)
            self.assertEqual(report["engineering_checks"]["receive_gap_failed_streams"], [] if passed else ["ring"])
            self.assertEqual(report["engineering_checks"]["minimum_overlap_fraction_of_requested_duration"], 0.8)
            self.assertIsInstance(report["overlap_seconds"], (int, float))
            self.assertIsInstance(report["quality_issues"], list)

    def test_all_missing_empty_and_replay_only(self):
        for name, rows in [("empty", []), ("missing", [ring("")]), ("replay", [ring(5, replay="1")])]:
            self.output = self.root / name
            report = self.run_timeline([arm(0), arm(10)], rows)
            self.assertEqual(report["overlap_seconds"], 0)
            self.assertIsNone(report["streams"]["ring"]["eligible_receive_span"]["start_ns"])
            self.assertIsNone(report["streams"]["ring"]["max_receive_gap_seconds"])

    def test_missing_time_column_is_quality_issue(self):
        self.write(self.arm_path, "value_1", [{"value_1": "2041"}])
        self.write(self.ring_path, RING_HEADER, [ring(0), ring(10)])
        report = build_timeline(self.arm_path, self.ring_path, self.output, 0)
        self.assertIn("arm:missing_host_time_column", report["qc_flags"])
        self.assertEqual(self.read("arm_receive.csv")[0]["host_relative_seconds"], "")

    def test_integer_nanosecond_precision_and_negative_relative_time(self):
        base = 10**18
        self.run_timeline([arm(base - 1), arm(base + 3)], [ring(base), ring(base + 2)], base)
        self.assertEqual(self.read("arm_receive.csv")[0]["host_relative_seconds"], "-0.000000001")
        self.assertEqual(self.read("ring_receive.csv")[1]["host_relative_seconds"], "0.000000002")

    def test_refuses_existing_directory_file_and_source(self):
        self.run_timeline([arm(0)], [ring(0)])
        before = (self.output / "report.json").read_bytes()
        for path in (self.output, self.arm_path, self.root):
            with self.assertRaises(FileExistsError):
                build_timeline(self.arm_path, self.ring_path, path, 0)
        empty = self.root / "empty"
        empty.mkdir()
        with self.assertRaises(FileExistsError):
            build_timeline(self.arm_path, self.ring_path, empty, 0)
        self.assertEqual((self.output / "report.json").read_bytes(), before)

    def test_rejects_malformed_csv_and_reserved_columns_before_output(self):
        self.write(self.ring_path, RING_HEADER, [ring(0)])
        for content in ("a,a\n1,2\n", "a\n1,2\n", "a,b\n1\n", "host_relative_seconds\n1\n"):
            self.arm_path.write_text(content, encoding="utf-8")
            with self.assertRaises(ValueError):
                build_timeline(self.arm_path, self.ring_path, self.output, 0)
            self.assertFalse(self.output.exists())

    def test_rejects_invalid_origin(self):
        for origin in (True, -1, 1.0, "0"):
            with self.assertRaises(ValueError):
                build_timeline(self.arm_path, self.ring_path, self.output, origin)

    def test_existing_real_trial_readonly_when_available(self):
        trial = Path(__file__).resolve().parents[1] / "data/episodes/DEVICECHECK/20261113_DUAL/T001"
        paths = [trial / "derived/arm_decode01/rows.csv", trial / "ring_derived/ring_decode01/imu.csv"]
        if not all(p.is_file() for p in paths):
            self.skipTest("Local engineering trial unavailable; schema fixtures still run")
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
        report = build_timeline(*paths, self.output, ORIGIN)
        self.assertEqual(report["streams"]["arm"]["rows"], 4020)
        self.assertEqual(report["streams"]["ring"]["rows"], 2025)
        self.assertEqual(report["streams"]["arm"]["receive_span"]["seconds"], 59.984)
        self.assertEqual(report["overlap_seconds"], 40.093)
        self.assertEqual(report["ring_capture_ids"], ["157"])
        self.assertEqual(report["ring_capture_epochs"][0]["batch_receipt_groups"], 135)
        self.assertEqual(report["streams"]["ring"]["duplicate_receive_times"]["timestamp_groups"], 135)
        self.assertEqual([hashlib.sha256(p.read_bytes()).hexdigest() for p in paths], before)
        for name, path in zip(("arm", "ring"), paths):
            with path.open(encoding="utf-8", newline="") as handle:
                original = list(csv.DictReader(handle))
            emitted = self.read(f"{name}_receive.csv")
            self.assertEqual([{k: r[k] for k in original[0]} for r in emitted], original)


if __name__ == "__main__":
    unittest.main()
