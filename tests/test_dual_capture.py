"""Offline process lifecycle tests; not hardware synchronization evidence."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dual_capture as dual


class DualTests(unittest.TestCase):
    def test_success_and_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = dual.supervise({'armband': [sys.executable, '-c', 'print(42)'],
                                     'ring': [sys.executable, '-c', 'print(43)']}, Path(tmp), 10)
            self.assertTrue(result['completed'])
            self.assertEqual((Path(tmp) / 'armband.stdout.log').read_text().strip(), '42')

    def test_failed_child_stops_other(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = dual.supervise({'armband': [sys.executable, '-c', 'raise SystemExit(2)'],
                'ring': [sys.executable, '-c', 'import time; time.sleep(100)']}, Path(tmp), 10)
            self.assertFalse(result['completed'])
            self.assertEqual(result['exit_codes']['armband'], 2)
            self.assertIsNotNone(result['exit_codes']['ring'])

    def test_timeout_preserves_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = dual.supervise({'ring': [sys.executable, '-c', 'import time; time.sleep(100)']}, Path(tmp), .1)
            self.assertFalse(result['completed'])
            self.assertTrue(any('TimeoutError' in e for e in result['errors']))

    def test_spawn_failure_reaps_started_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = dual.supervise({'ring': [sys.executable, '-c', 'import time; time.sleep(100)'],
                                     'armband': ['this_executable_does_not_exist']}, Path(tmp), 10)
            self.assertFalse(result['completed'])
            self.assertIsNotNone(result['exit_codes']['ring'])

    def test_quality_rejects_empty_bad_and_short_streams(self):
        good = {'armband': {'valid_rows': 50, 'column_width_counts': {8: 50}},
                'ring': {'raw_verified': True, 'live_imu_samples': 50, 'exit_code': 0},
                'integrity': {'captures': [{'raw_verified': True, 'exit_code': 0}]},
                'timeline': {'overlap_seconds': 9, 'quality_issues': []}}
        self.assertEqual(dual.quality_issues(good, 10), [])
        for section, key, value in [('armband', 'column_width_counts', {14: 50}),
                                    ('armband', 'invalid_line_count', 1),
                                    ('ring', 'raw_verified', False),
                                    ('ring', 'time_epoch_boundaries', [1]),
                                    ('ring', 'qc_flags', ['conflicting_sample_config']),
                                    ('ring', 'qc_flags', ['device_reported_gap']),
                                    ('ring', 'qc_flags', ['config_period_mismatch']),
                                    ('ring', 'qc_flags', ['batch_timestamp_mismatch']),
                                    ('timeline', 'overlap_seconds', 1),
                                    ('timeline', 'quality_issues', ['host time backwards'])]:
            with self.subTest(key=key):
                bad = copy.deepcopy(good)
                bad[section][key] = value
                self.assertTrue(dual.quality_issues(bad, 10))

    def test_repeated_interrupt_reaps_all_children(self):
        a, b = MagicMock(), MagicMock()
        a.poll.return_value = b.poll.return_value = None
        a.wait.side_effect = [KeyboardInterrupt(), 0]
        a.returncode = b.returncode = -1
        with tempfile.TemporaryDirectory() as tmp, patch.object(dual.subprocess, 'Popen', side_effect=[a, b]), \
                patch.object(dual.time, 'sleep', side_effect=KeyboardInterrupt()):
            result = dual.supervise({'armband': ['a'], 'ring': ['b']}, Path(tmp), 10)
        self.assertFalse(result['completed'])
        self.assertTrue(a.terminate.called)
        self.assertTrue(b.send_signal.called)

    def test_postprocessing_cancel_saves_overall_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = dual.parser().parse_args(['record', '--root', tmp, '--subject', 'S01',
                '--session', 'day1', '--trial', 'T01', '--task', 'check', '--consent-scope', 'engineering',
                '--port', 'COM5', '--address', 'test'])
            with patch.object(dual, 'supervise', return_value={'completed': True, 'exit_codes': {'armband': 0, 'ring': 0}, 'errors': []}), \
                    patch.object(dual, 'process_episode', side_effect=KeyboardInterrupt()):
                result = dual.record(args)
            self.assertFalse(result['engineering_capture_ok'])
            saved = dual.session.read_json(Path(tmp) / 'S01/day1/T01/dual_run/result.json')
            self.assertEqual(saved['exit_code'], 1)

    def test_reprocess_cancel_records_failure_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            episode = Path(tmp)
            dual.session.write_new_json(episode / 'episode.json', {'schema_version': 1})
            (episode / 'dual_run').mkdir()
            dual.session.write_new_json(episode / 'dual_run/request.json', {
                'monotonic_scope': 'one orchestrator, one host and boot',
                'origin': {'host_monotonic_ns': 10}, 'requested_seconds': 10})
            args = dual.parser().parse_args(['process', '--episode', tmp, '--derive-id', 'retry'])
            with patch.object(dual, 'process_episode', side_effect=KeyboardInterrupt()):
                self.assertEqual(dual.reprocess(args)['exit_code'], 1)
            self.assertTrue((episode / 'dual_derived/retry/processing_failure.json').is_file())
            with self.assertRaises(FileExistsError):
                dual.reprocess(args)

    def test_invalid_duration_does_not_create_episode(self):
        args = dual.parser().parse_args(['record', '--subject', 'S01', '--session', 'day1',
            '--trial', 'T01', '--task', 'check', '--consent-scope', 'engineering',
            '--port', 'COM5', '--address', 'test', '--seconds', 'nan'])
        with self.assertRaises(ValueError):
            dual.record(args)


if __name__ == '__main__':
    unittest.main()
