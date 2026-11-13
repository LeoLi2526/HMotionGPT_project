"""Bounded, explicit eRing IMU 100/200 Hz configuration probe; restores settings.

Uses only documented SAMPLE_CONFIG and LIVE_CONTROL messages. It does not
upgrade firmware, guess commands, change PPG/ranges, or fetch historical data.
Device acceptance and observed samples are separate evidence.
"""
import argparse
import asyncio
import json
from pathlib import Path
import struct
import time

import ering_capture as transport
import ering_protocol as protocol
from ering_store import Recorder

SETTING_KEYS = ("ppg_hz", "imu_hz", "ppg_channels", "accel_range_g", "gyro_range_dps")


def settings(config):
    return tuple(config[k] for k in SETTING_KEYS)


def payload(config, imu_hz):
    if imu_hz not in (50, 100, 200):
        raise ValueError("Only explicit 50/100/200 Hz settings are supported by this probe")
    return struct.pack("<HHBBHHH", config["ppg_hz"], imu_hz, config["ppg_channels"],
                       0, config["config_id"], config["accel_range_g"], config["gyro_range_dps"])


class ProbeLink(transport.Link):
    def fail(self, error):
        # A validated, correlated SAMPLE_CONFIG rejection is an experimental
        # result, not a dead transport. All other errors retain fail-stop behavior.
        if (isinstance(error, transport.DeviceError) and error.origin_type == 3 and error.code in (1, 3)
                and self.pending and self.pending["type"] == 3):
            self.recorder.event("configuration_rejected", code=error.code, origin_type=3)
            future = self.pending["future"]
            if not future.done():
                future.set_exception(error)
            return
        super().fail(error)


async def query(link):
    return (await link.request(3))["decoded"]


async def window(link, recorder, name, seconds):
    try:
        await link.set_live(True)
        recorder.event("probe_window_start", phase=name, host_monotonic_ns=time.monotonic_ns())
        print(json.dumps({"phase": name, "state": "recording", "seconds": seconds}), flush=True)
        await link.observe(seconds)
    finally:
        await link.set_live(False)
        recorder.event("probe_window_end", phase=name, host_monotonic_ns=time.monotonic_ns())


async def sequence(link, recorder, report, seconds):
    original = await query(link)
    report["original_config"] = original
    report["attempts"] = []
    if original["imu_hz"] != 50:
        raise ValueError("Expected observed 50 Hz baseline; stop rather than overwrite a different configuration")
    try:
        await link.set_live(False)
        # Control establishes that the documented 12-byte layout is accepted.
        try:
            await link.request(3, payload(original, 50))
        except transport.DeviceError as exc:
            report["no_op_control"] = {"accepted": False, "error_code": exc.code,
                                       "origin_type": exc.origin_type}
            raise
        if settings(await query(link)) != settings(original):
            raise ValueError("No-op control changed configuration")
        report["no_op_control"] = "accepted_and_readback_matches"
        await window(link, recorder, "baseline_50", seconds)
        for hz in (100, 200):
            before = await query(link)
            attempt = {"requested_imu_hz": hz, "before": before}
            report["attempts"].append(attempt)
            recorder.event("probe_configuration_attempt", **attempt)
            try:
                response = await link.request(3, payload(before, hz))
                attempt["response"] = response["decoded"]
                attempt["accepted"] = True
            except transport.DeviceError as exc:
                attempt.update(accepted=False, error_code=exc.code, origin_type=exc.origin_type)
            current = await query(link)
            attempt["readback"] = current
            if not attempt["accepted"]:
                if settings(current) != settings(before):
                    raise ValueError("Rejected setting nevertheless changed configuration")
                continue
            expected = dict(before, imu_hz=hz)
            if settings(current) != settings(expected):
                raise ValueError("Accepted request did not read back the exact requested settings")
            await window(link, recorder, f"requested_{hz}", seconds)
    finally:
        # Query and verify even after rejection: never infer restoration from ACK.
        try:
            await link.set_live(False)
            current = await query(link)
            if settings(current) != settings(original):
                restore = dict(original, config_id=current["config_id"])
                await link.request(3, payload(restore, original["imu_hz"]))
            report["final_config"] = await query(link)
            report["original_settings_verified"] = settings(report["final_config"]) == settings(original)
            if not report["original_settings_verified"]:
                raise ValueError("Original configuration not restored")
            recorder.event("probe_restore_verified", config=report["final_config"])
        except Exception as exc:
            report["original_settings_verified"] = False
            report["restore_error"] = f"{type(exc).__name__}: {exc}"
            raise
    await window(link, recorder, "final_50", seconds)


async def run(args):
    transport.positive_seconds(args.seconds)
    if args.seconds > 30:
        raise ValueError("Short engineering probe only: seconds must be <=30")
    report = {"schema_version": 1, "scope": "documented SAMPLE_CONFIG IMU field only",
              "hardware_capability_claim": "not determined by command rejection",
              "software": transport.software_identity(), "original_settings_verified": False}
    recorder = Recorder(args.output, {"transport": "ble", "mode": "imu_rate_probe",
                                     "address": args.address, "purpose": "engineering",
                                     "software": report["software"]})
    error = None
    try:
        Client, Scanner = transport.bleak_types()
        device = await Scanner.find_device_by_address(args.address, timeout=12,
                                                       service_uuids=[protocol.SERVICE_UUID])
        if device is None:
            raise ConnectionError("Selected ring not found advertising")
        async with Client(device, pair=True, timeout=60) as client:
            link = ProbeLink(client, recorder, timeout=12)
            await client.start_notify(protocol.TX_UUID, link.notification)
            try:
                report["capabilities"] = (await link.request(1))["decoded"]
                await sequence(link, recorder, report, args.seconds)
            finally:
                if client.is_connected:
                    await client.stop_notify(protocol.TX_UUID)
    except (Exception, asyncio.CancelledError) as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        report["error"] = error
        report["capture"] = recorder.finish("failed" if error else "completed", error)
        recorder.close()
        with (args.output / "probe_report.json").open("x", encoding="utf-8") as out:
            json.dump(report, out, ensure_ascii=False, indent=2)
        print(json.dumps(report, ensure_ascii=False), flush=True)
    return 1 if error else 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--address", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--seconds", type=float, default=8)
    return asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
