"""eRing v2 BLE transport. Hardware validation is separate from offline tests."""

import asyncio
import importlib.metadata
import math
from pathlib import Path
import struct
import subprocess
import sys
import time

import ering_protocol as protocol
from ering_store import Recorder


class DeviceError(RuntimeError):
    def __init__(self, fields):
        self.code = fields["code"]
        self.origin_type = fields["origin_type"]
        super().__init__("eRing command error: " + str(fields))


def positive_seconds(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError("duration/timeout must be finite and positive")
    return value


def bleak_types():
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError as exc:
        raise RuntimeError("Install requirements-ering.txt in the acquisition Python environment") from exc
    return BleakClient, BleakScanner


def software_identity():
    info = {"protocol_version": 2, "python_version": sys.version.split()[0],
            "bleak_version": "not_installed", "git_commit": "unknown", "git_dirty": None}
    try:
        info["bleak_version"] = importlib.metadata.version("bleak")
    except importlib.metadata.PackageNotFoundError:
        pass
    try:
        root = Path(__file__).resolve().parents[1]
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                              text=True, check=True, timeout=2)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True,
                               text=True, check=True, timeout=2)
        info.update(git_commit=head.stdout.strip(), git_dirty=bool(dirty.stdout.strip()))
    except (OSError, subprocess.SubprocessError):
        pass
    return info


async def scan(seconds=5):
    positive_seconds(seconds)
    _, scanner = bleak_types()
    found = await scanner.discover(timeout=seconds, return_adv=True, service_uuids=[protocol.SERVICE_UUID])
    return {"devices": [{"address": device.address, "name": adv.local_name or device.name,
                         "service_uuids": adv.service_uuids, "rssi": adv.rssi}
                        for device, adv in found.values()],
            "semantics": "advertisements only; firmware and sensor operation unverified"}


class Link:
    """One connection, one outstanding request, arbitrary interleaved notifications."""

    def __init__(self, client, recorder, timeout=10):
        self.client, self.recorder, self.timeout = client, recorder, positive_seconds(timeout)
        self.assembler = protocol.Reassembler()
        self.request_id = 0
        self.frame_id = 0
        self.pending = None
        self.fault = None
        self.failed = asyncio.Event()
        self.lock = asyncio.Lock()

    def fail(self, error):
        if self.fault is None:
            self.fault = error if isinstance(error, Exception) else RuntimeError(str(error))
            self.failed.set()
            try:
                self.recorder.event("transport_error", error=str(error))
            except OSError:
                pass
            if self.pending and not self.pending["future"].done():
                self.pending["future"].set_exception(self.fault)

    def disconnected(self, _client):
        self.assembler.reset()
        self.fail(ConnectionError("eRing disconnected; incomplete replay page is not committed"))

    def notification(self, _characteristic, data):
        utc, mono = time.time_ns(), time.monotonic_ns()
        try:
            self.recorder.notification(bytes(data), utc, mono)
            raw = self.assembler.feed(bytes(data))
            if raw is None:
                return
            frame = self.recorder.accept(raw, utc, mono, context="ble")
            frame = {**frame, "_host_utc_ns": utc, "_host_monotonic_ns": mono}
            pending = self.pending
            if not pending or frame["request_id"] != pending["id"]:
                return
            future = pending["future"]
            if future.done():
                return
            if frame["type"] == 0x7F:
                error = DeviceError(frame["decoded"])
                if pending["type"] == 0x06 and error.code == 9 and error.origin_type == 0x06:
                    future.set_exception(error)
                else:
                    self.fail(error)
            elif pending["type"] == 0x06:
                if frame["type"] == 0x07 and frame["flags"] & ~4 == 0:
                    future.set_result((frame, pending["frames"]))
                elif frame["flags"] == 2 and frame["capture_id"] > 0 and frame["sequence"] > 0:
                    # Known nonpersistent frames are rejected by the protocol decoder.
                    pending["frames"].append(frame)
                    if len(pending["frames"]) > 32:
                        self.fail(ValueError("replay page exceeds 32 frames"))
            elif frame["type"] == pending["type"] and frame["flags"] & ~4 == 1:
                future.set_result(frame)
        except Exception as exc:
            # A fully assembled invalid envelope is still a complete raw receipt.
            invalid_raw = getattr(exc, "raw_frame", None)
            if invalid_raw is not None:
                try:
                    self.recorder.accept(invalid_raw, utc, mono, context="ble")
                except ValueError:
                    pass
                except Exception as persistence_error:
                    self.fail(persistence_error)
                    return
            self.fail(exc)

    async def request(self, message_type, payload=b""):
        async with self.lock:
            if self.fault:
                raise self.fault
            self.request_id += 1
            if self.request_id > 0xFFFFFFFF:
                raise ValueError("request IDs exhausted; open a new connection")
            rid = self.request_id
            future = asyncio.get_running_loop().create_future()
            self.pending = {"id": rid, "type": message_type, "future": future, "frames": []}
            raw = protocol.encode_request(message_type, payload, request_id=rid)
            self.frame_id = (self.frame_id + 1) & 0xFFFF
            self.recorder.event("request", request_id=rid, type=message_type, raw_hex=raw.hex(),
                                host_utc_ns=time.time_ns(), host_monotonic_ns=time.monotonic_ns())
            try:
                # MTU23-compatible writes. Await each write; GATT success is not command success.
                async with asyncio.timeout(self.timeout):
                    for fragment in protocol.fragment_frame(raw, self.frame_id, max_write=20):
                        await self.client.write_gatt_char(protocol.RX_UUID, fragment, response=True)
                        if self.fault:
                            raise self.fault
                    answer = await future
                if self.fault:
                    raise self.fault
                return answer
            except BaseException:
                if future.done() and not future.cancelled():
                    future.exception()  # retrieve callback exceptions even if a write failed first
                else:
                    future.cancel()
                raise
            finally:
                self.pending = None

    async def synchronize(self):
        utc0, mono0 = time.time_ns(), time.monotonic_ns()
        response = await self.request(0x02, struct.pack("<QQ", utc0 // 1000, mono0 // 1000))
        fields = response["decoded"]
        record = {"capture_id": response["capture_id"], "request_id": response["request_id"],
                  "host_send_utc_ns": utc0, "host_send_monotonic_ns": mono0,
                  "host_receive_utc_ns": response["_host_utc_ns"],
                  "host_receive_monotonic_ns": response["_host_monotonic_ns"],
                  "device_rx_us": fields["device_rx_us"], "device_tx_us": fields["device_tx_us"],
                  "host_round_trip_ns": response["_host_monotonic_ns"] - mono0,
                  "device_processing_us": fields["device_tx_us"] - fields["device_rx_us"],
                  "synchronization": "unverified", "scope": "current capture_id only; not radio timestamps"}
        if record["device_processing_us"] < 0:
            raise ValueError("device time reversed within TIME_SYNC")
        self.recorder.event("time_sync", **record)
        return record

    async def set_live(self, enable):
        answer = await self.request(0x04, struct.pack("<BBH", int(enable), 0, 500))
        fields = answer["decoded"]
        if fields["enable"] != int(enable) or fields["max_batch_latency_ms"] != 500:
            raise ValueError("LIVE_CONTROL response does not match requested state")

    async def replay(self, sync_status, max_pages):
        if type(max_pages) is not int or max_pages < 0:
            raise ValueError("replay page limit must be a nonnegative integer")
        status = sync_status["decoded"]
        if not status["storage_flags"] & 1:
            raise ValueError("eRing storage unavailable; replay cannot proceed")
        cursor = (status["oldest_capture_id"], status["oldest_sequence"])
        target = (status["next_capture_id"], status["next_sequence"])
        if cursor > target:
            raise ValueError("SYNC_STATUS oldest is after next")
        pages, attempts = 0, 0
        while cursor < target and attempts < max_pages:
            attempts += 1
            try:
                done, frames = await self.request(0x06, struct.pack("<QIHH", *cursor, 32, 0))
            except DeviceError as exc:
                if exc.code != 9 or exc.origin_type != 0x06:
                    raise
                fresh = (await self.request(0x05))["decoded"]
                oldest = (fresh["oldest_capture_id"], fresh["oldest_sequence"])
                new_target = (fresh["next_capture_id"], fresh["next_sequence"])
                self.recorder.event("stale_cursor", requested_cursor=list(cursor),
                                    new_oldest=list(oldest), new_next=list(new_target),
                                    overwritten_interval=[list(cursor), list(oldest)] if cursor < oldest else None)
                if not fresh["storage_flags"] & 1 or oldest > new_target:
                    raise ValueError("refreshed replay storage state unavailable or inconsistent")
                cursor, target = oldest, new_target
                continue
            d = done["decoded"]
            after = (d["next_capture_id"], d["next_sequence"])
            if d["result"] != 0 or d["sent_frames"] != len(frames):
                raise ValueError("replay interrupted or received frame count differs from REPLAY_DONE")
            if after <= cursor:
                raise ValueError("replay cursor did not advance")
            if any(not cursor <= (f["capture_id"], f["sequence"]) < after for f in frames):
                raise ValueError("replay frame lies outside returned cursor interval")
            if self.fault:
                raise self.fault
            self.recorder.commit_cursor(*after, request_id=done["request_id"])
            cursor = after
            pages += 1
        result = {"pages": pages, "attempts": attempts, "next_cursor": list(cursor), "snapshot_target": list(target),
                  "snapshot_complete": cursor >= target,
                  "assignment": "backlog participant/trial ownership unverified"}
        self.recorder.event("replay_summary", **result)
        return result

    async def observe(self, seconds):
        positive_seconds(seconds)
        try:
            await asyncio.wait_for(self.failed.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return
        raise self.fault


async def collect(client, recorder, seconds, replay_pages=0, timeout=10, disconnected_event=None):
    """Capture an already connected client; injectable transport for offline verification."""
    link = Link(client, recorder, timeout)
    async def watch_disconnect():
        await disconnected_event.wait()
        link.disconnected(client)
    watcher = asyncio.create_task(watch_disconnect()) if disconnected_event is not None else None
    subscribed = False
    live = False
    try:
        await client.start_notify(protocol.TX_UUID, link.notification)
        subscribed = True
        capabilities = await link.request(0x01)
        if not capabilities["decoded"]["feature_bits"] & 2:
            raise ValueError("eRing firmware does not report an available IMU")
        await link.synchronize()
        await link.request(0x03)
        sync = await link.request(0x05)
        await link.request(0x30)
        if replay_pages:
            await link.replay(sync, replay_pages)
        await link.set_live(True)
        live = True
        recorder.event("live_window_start", host_utc_ns=time.time_ns(), host_monotonic_ns=time.monotonic_ns())
        await link.observe(seconds)
        await link.set_live(False)
        live = False
        recorder.event("live_window_end", host_utc_ns=time.time_ns(), host_monotonic_ns=time.monotonic_ns())
        await link.synchronize()
        await link.request(0x05)
        await link.request(0x30)
        if link.fault:
            raise link.fault
    finally:
        if watcher:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
        if live and client.is_connected and not link.fault:
            try:
                await link.set_live(False)
            except Exception as exc:
                recorder.event("cleanup_error", error=str(exc))
        if subscribed and client.is_connected:
            await client.stop_notify(protocol.TX_UUID)
        link.assembler.reset()


async def capture(directory, address, seconds, replay_pages=0, timeout=10, pair=True):
    positive_seconds(seconds)
    positive_seconds(timeout)
    if not isinstance(address, str) or not address.strip():
        raise ValueError("An explicit ring address is required")
    if type(replay_pages) is not int or replay_pages < 0:
        raise ValueError("replay-pages must be a nonnegative integer")
    recorder = Recorder(Path(directory), {"transport": "ble", "address": address,
                         "requested_live_seconds": seconds, "replay_page_limit": replay_pages,
                         "software": software_identity(),
                         "hardware_validation": "capture_attempt_not_acceptance"})
    error = None
    try:
        Client, Scanner = bleak_types()
        device = await Scanner.find_device_by_address(address, timeout=timeout,
                                                      service_uuids=[protocol.SERVICE_UUID])
        if device is None:
            raise ConnectionError("Selected eRing was not found advertising the v2 service")
        # The context manager disconnects on success, errors and cancellation.
        disconnected = asyncio.Event()
        async with Client(device, pair=pair, timeout=max(60, timeout),
                          disconnected_callback=lambda _: disconnected.set()) as client:
            await collect(client, recorder, seconds, replay_pages, timeout, disconnected)
    except (Exception, asyncio.CancelledError) as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            report = recorder.finish("failed" if error else "completed", error)
        finally:
            recorder.close()
    return {**report, "directory": str(directory), "exit_code": 1 if error else 0}
