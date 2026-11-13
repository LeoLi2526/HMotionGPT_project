"""Synthetic command-flow evidence; not hardware capability validation."""
import asyncio
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ering_rate_probe as probe


class Recorder:
    def event(self, *args, **kwargs):
        pass


class FakeLink:
    def __init__(self, reject=False, fail_window=False, reject_noop=False):
        self.config = dict(ppg_hz=25, imu_hz=50, ppg_channels=7, config_id=1,
                           accel_range_g=4, gyro_range_dps=500)
        self.reject, self.fail_window = reject, fail_window
        self.reject_noop = reject_noop
        self.writes, self.observed, self.live = [], [], False

    async def request(self, kind, raw=b""):
        if raw:
            ppg, imu, channels, reserved, cid, accel, gyro = struct.unpack("<HHBBHHH", raw)
            self.writes.append((ppg, imu, channels, reserved, cid, accel, gyro))
            if self.reject_noop or (self.reject and imu != 50):
                raise probe.transport.DeviceError(dict(code=1, origin_type=3))
            if imu != self.config['imu_hz']:
                self.config.update(imu_hz=imu, config_id=cid + 1)
        return dict(decoded=dict(self.config))

    async def set_live(self, enabled):
        self.live = enabled

    async def observe(self, seconds):
        self.observed.append(self.config['imu_hz'])
        if self.fail_window and self.config['imu_hz'] == 100:
            raise RuntimeError('sample failure')


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_noop_rejected_stops_before_rate_changes_and_verifies_original(self):
        link, report = FakeLink(reject_noop=True), {}
        with self.assertRaises(probe.transport.DeviceError):
            await probe.sequence(link, Recorder(), report, .01)
        self.assertEqual([w[1] for w in link.writes], [50])
        self.assertEqual(report['attempts'], [])
        self.assertEqual(report['no_op_control']['error_code'], 1)
        self.assertTrue(report['original_settings_verified'])
        self.assertFalse(link.live)

    async def test_accepted_rates_and_verified_restore(self):
        link, report = FakeLink(), {}
        await probe.sequence(link, Recorder(), report, .01)
        self.assertEqual(link.observed, [50, 100, 200, 50])
        self.assertTrue(report['original_settings_verified'])
        self.assertFalse(link.live)
        self.assertTrue(all((w[0], w[2], w[3], w[5], w[6]) == (25,7,0,4,500) for w in link.writes))
        self.assertEqual(link.writes[-1][4], 3)  # restore uses current config id

    async def test_rejections_preserve_baseline_and_remain_queryable(self):
        link, report = FakeLink(reject=True), {}
        await probe.sequence(link, Recorder(), report, .01)
        self.assertEqual(link.observed, [50, 50])
        self.assertTrue(report['original_settings_verified'])
        self.assertEqual([a['accepted'] for a in report['attempts']], [False, False])

    async def test_failure_after_change_restores_settings_and_stops_live(self):
        link, report = FakeLink(fail_window=True), {}
        with self.assertRaisesRegex(RuntimeError, 'sample failure'):
            await probe.sequence(link, Recorder(), report, .01)
        self.assertEqual(link.config['imu_hz'], 50)
        self.assertTrue(report['original_settings_verified'])
        self.assertFalse(link.live)

    async def test_only_correlated_configuration_errors_are_nonfatal(self):
        link = probe.ProbeLink(None, Recorder())
        future = asyncio.get_running_loop().create_future()
        link.pending = {'type':3, 'future':future}
        error = probe.transport.DeviceError(dict(code=3, origin_type=3))
        link.fail(error)
        self.assertIs(future.exception(), error)
        self.assertIsNone(link.fault)
        link.pending = None
        link.fail(error)
        self.assertIs(link.fault, error)
