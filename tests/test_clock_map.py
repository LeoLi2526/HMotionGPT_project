"""Synthetic offline clock calibration tests; no device/sync success claims."""

import copy
from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fit_clock_map as clock_tool


def example():
    return {
        "modality": "armband", "source_clock": "device_seconds",
        "source_unit": "seconds", "target_clock": "episode_relative_seconds",
        "provenance": {"note": "synthetic fixture, not hardware evidence"},
        "anchors": [
            {"source_seconds": 0, "target_seconds": 2, "role": "fit", "event_id": "start"},
            {"source_seconds": 100, "target_seconds": 102, "role": "fit", "event_id": "end"},
            {"source_seconds": 50, "target_seconds": 52, "role": "check", "event_id": "middle"},
        ],
    }


class ClockMapTests(unittest.TestCase):
    def test_recovers_drift_and_preserves_evidence(self):
        data = example()
        for row in data["anchors"]:
            row["target_seconds"] = 1.0002 * row["source_seconds"] + 0.75
        original = copy.deepcopy(data)
        result = clock_tool.fit_clock_map(data, 0.001)
        self.assertAlmostEqual(result["mapping"]["a"], 1.0002, places=12)
        self.assertAlmostEqual(result["mapping"]["b"], 0.75, places=12)
        self.assertTrue(result["verified_for_supplied_events"])
        self.assertEqual(result["input_evidence"], original)
        self.assertEqual(data, original)
        data["provenance"]["note"] = "changed later"
        self.assertEqual(result["input_evidence"], original)
        self.assertEqual(result["algorithm"], clock_tool.ALGORITHM)

    def test_all_fit_points_contribute_to_ols(self):
        data = example()
        data["anchors"] = [
            dict(source_seconds=x, target_seconds=y, role="fit", event_id=str(x))
            for x, y in [(0, 0), (1, 2), (2, 2)]
        ]
        result = clock_tool.fit_clock_map(data, 1)
        self.assertAlmostEqual(result["mapping"]["a"], 1)
        self.assertAlmostEqual(result["mapping"]["b"], 1 / 3)

    def test_worst_heldout_error_decides_and_does_not_change_fit(self):
        data = example()
        data["anchors"].append(dict(source_seconds=75, target_seconds=77.125,
                                    role="check", event_id="bad-check"))
        result = clock_tool.fit_clock_map(data, 100)
        self.assertEqual(result["mapping"]["a"], 1)
        self.assertEqual(result["mapping"]["b"], 2)
        self.assertEqual(result["max_check_absolute_error_ms"], 125)
        self.assertFalse(result["verified_for_supplied_events"])
        self.assertIn("check_error_exceeds_threshold", result["failure_reasons"])
        self.assertTrue(clock_tool.fit_clock_map(data, 125)["verified_for_supplied_events"])

    def test_no_check_never_passes(self):
        data = example()
        data["anchors"].pop()
        result = clock_tool.fit_clock_map(data, 1000)
        self.assertFalse(result["verified_for_supplied_events"])
        self.assertIsNone(result["max_check_absolute_error_ms"])
        self.assertEqual(result["failure_reasons"], ["no_check_events"])

    def test_inconsistent_fit_cannot_pass_with_lucky_check(self):
        data = example()
        data["anchors"] = [
            dict(source_seconds=x, target_seconds=y, role=role, event_id=str(i))
            for i, (x, y, role) in enumerate([(0, 0, "fit"), (50, 80, "fit"),
                                             (100, 100, "fit"), (75, 85, "check")])]
        result = clock_tool.fit_clock_map(data, 0)
        self.assertEqual(result["max_check_absolute_error_ms"], 0)
        self.assertEqual(result["max_fit_absolute_error_ms"], 20000)
        self.assertFalse(result["verified_for_supplied_events"])
        self.assertIn("fit_error_exceeds_threshold", result["failure_reasons"])

    def test_extrapolation_never_passes_even_with_zero_residual(self):
        for x in (-1, 101):
            with self.subTest(x=x):
                data = example()
                data["anchors"][2].update(source_seconds=x, target_seconds=x + 2)
                result = clock_tool.fit_clock_map(data, 0)
                self.assertEqual(result["max_check_absolute_error_ms"], 0)
                self.assertFalse(result["verified_for_supplied_events"])
                self.assertEqual(result["extrapolated_check_event_ids"], ["middle"])

    def test_coverage_endpoints_are_inclusive(self):
        for x in (0, 100):
            data = example()
            data["anchors"][2].update(source_seconds=x, target_seconds=x + 2)
            self.assertTrue(clock_tool.fit_clock_map(data, 0)["verified_for_supplied_events"])

    def test_large_epoch_source_preserves_small_relative_times(self):
        data = example()
        for row in data["anchors"]:
            row["source_seconds"] += 1_700_000_000
        result = clock_tool.fit_clock_map(data, 0)
        self.assertTrue(result["verified_for_supplied_events"])
        self.assertEqual(result["mapping"]["b"], 2 - 1_700_000_000)

    def test_invalid_thresholds_and_required_argument(self):
        for threshold in (-1, float("nan"), float("inf"), True, "5", None):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                clock_tool.fit_clock_map(example(), threshold)
        with self.assertRaises(TypeError):
            clock_tool.fit_clock_map(example())

    def test_rejects_invalid_numeric_anchors_and_metadata(self):
        for value in (float("nan"), float("inf"), -float("inf"), True, "1", None, 10 ** 400):
            for field in ("source_seconds", "target_seconds"):
                with self.subTest(value=value, field=field), self.assertRaises(ValueError):
                    data = example()
                    data["anchors"][2][field] = value
                    clock_tool.fit_clock_map(data, 1)
        data = example()
        data["provenance"]["invalid"] = float("nan")
        with self.assertRaises(ValueError):
            clock_tool.fit_clock_map(data, 1)

    def test_rejects_degenerate_or_reverse_fit(self):
        for targets in ((2, 1), (2, 2)):
            data = example()
            for row, target in zip(data["anchors"], targets):
                row["target_seconds"] = target
            with self.assertRaisesRegex(ValueError, "scale"):
                clock_tool.fit_clock_map(data, 1)
        data = example()
        data["anchors"][1]["source_seconds"] = 0
        with self.assertRaisesRegex(ValueError, "variance"):
            clock_tool.fit_clock_map(data, 1)
        data["anchors"].pop(1)
        with self.assertRaisesRegex(ValueError, "two fit"):
            clock_tool.fit_clock_map(data, 1)

    def test_rejects_duplicate_event_and_fit_check_leakage(self):
        for index in (1, 2):
            for event_id in ("start", " start "):
                data = example()
                data["anchors"][index]["event_id"] = event_id
                with self.subTest(index=index), self.assertRaisesRegex(ValueError, "duplicate event_id"):
                    clock_tool.fit_clock_map(data, 1)

    def test_rejects_bad_schema(self):
        for field, value in (("source_unit", "ms"), ("target_clock", "host"),
                             ("source_clock", ""), ("modality", None), ("anchors", {})):
            data = example()
            data[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                clock_tool.fit_clock_map(data, 1)
        for field, value in (("role", "validation"), ("event_id", "")):
            data = example()
            data["anchors"][0][field] = value
            with self.assertRaises(ValueError):
                clock_tool.fit_clock_map(data, 1)
        for data in ([], {}, {**example(), "anchors": [None]}):
            with self.assertRaises(ValueError):
                clock_tool.fit_clock_map(data, 1)

    def test_numeric_overflow_fails_closed(self):
        data = example()
        data["anchors"][0]["source_seconds"] = -1e308
        data["anchors"][1]["source_seconds"] = 1e308
        with self.assertRaises(ValueError):
            clock_tool.fit_clock_map(data, 1)


class ClockMapCliTests(unittest.TestCase):
    def invoke(self, *args):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return clock_tool.main(list(map(str, args)))

    def test_cli_evidence_and_no_overwrite_or_raw_changes(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "anchors.json"
            output = Path(folder) / "map.json"
            raw = b"\xef\xbb\xbf" + json.dumps(example(), indent=2).encode("utf-8") + b"\r\n"
            source.write_bytes(raw)
            args = (source, "--output", output, "--max-error-ms", 0)
            self.assertEqual(self.invoke(*args), 0)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["input_file"]["text"].encode("utf-8"), raw)
            self.assertEqual(report["input_file"]["sha256"], hashlib.sha256(raw).hexdigest())
            saved = output.read_bytes()
            self.assertEqual(self.invoke(*args), 2)
            self.assertEqual(output.read_bytes(), saved)
            self.assertEqual(self.invoke(source, "--output", source, "--max-error-ms", 0), 2)
            self.assertEqual(source.read_bytes(), raw)

    def test_bad_json_or_threshold_creates_no_output(self):
        with TemporaryDirectory() as folder:
            source, output = Path(folder) / "in.json", Path(folder) / "out.json"
            for text in ('{"anchors":', '{"modality":"a","modality":"b"}',
                         json.dumps(example()).replace('"source_seconds": 0', '"source_seconds": NaN'),
                         json.dumps(example()).replace('"source_seconds": 0', '"source_seconds": 1e999')):
                source.write_text(text, encoding="utf-8")
                self.assertEqual(self.invoke(source, "--output", output, "--max-error-ms", 1), 2)
                self.assertFalse(output.exists())
            source.write_text(json.dumps(example()), encoding="utf-8")
            self.assertEqual(self.invoke(source, "--output", output, "--max-error-ms", "nan"), 2)
            self.assertFalse(output.exists())
            with self.assertRaises(SystemExit) as error:
                self.invoke(source, "--output", output)
            self.assertEqual(error.exception.code, 2)
            self.assertFalse(output.exists())

    def test_unverified_report_is_written_with_explicit_status(self):
        with TemporaryDirectory() as folder:
            source, output = Path(folder) / "in.json", Path(folder) / "out.json"
            data = example()
            data["anchors"].pop()
            source.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(self.invoke(source, "--output", output, "--max-error-ms", 1), 0)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "not_verified")
            self.assertFalse(report["verified_for_supplied_events"])


if __name__ == "__main__":
    unittest.main()
