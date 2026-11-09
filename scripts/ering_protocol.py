"""Offline eRing v2 codec (protocol.md, 2026-11-09), using only the stdlib.

decode_frame receives device-to-host frames; encode_request is the opposite
direction. Raw integers and device times are never converted to physical units.
MORE is recognized and preserved, but has no completion/pagination semantics.
Unknown types receive only envelope validation and have an empty decoded dict.
"""

from __future__ import annotations

import struct
import zlib


SERVICE_UUID = "e7f20001-6d50-4a9c-a721-41e4bc5f8a01"
RX_UUID = "e7f20002-6d50-4a9c-a721-41e4bc5f8a01"
TX_UUID = "e7f20003-6d50-4a9c-a721-41e4bc5f8a01"

RESPONSE, REPLAY, MORE, ERROR = 1, 2, 4, 8
_HEADER = struct.Struct("<HBBHHIQQIHH")
_FRAGMENT = struct.Struct("<HHH")
_RESPONSES = {0x01, 0x02, 0x03, 0x04, 0x05, 0x20, 0x23, 0x30, 0x40}
_REQUEST_ONLY = {0x06, 0x21, 0x31}
_PERSISTENT = {0x10, 0x11, 0x12, 0x13, 0x14}
_EVENTS = {0x07, 0x15, 0x22, 0x32}
_KNOWN = _RESPONSES | _REQUEST_ONLY | _PERSISTENT | _EVENTS | {0x7F}


class FrameValidationError(ValueError):
    """An assembled envelope failed validation; retain its exact bytes for archival."""

    def __init__(self, message: str, raw_frame: bytes) -> None:
        super().__init__(message)
        self.raw_frame: bytes = bytes(raw_frame)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _bytes(value, name: str) -> bytes:
    _require(isinstance(value, (bytes, bytearray, memoryview)), name + " must be bytes")
    return bytes(value)


def _uint(value: int, bits: int, name: str, minimum: int = 0) -> None:
    _require(isinstance(value, int) and not isinstance(value, bool)
             and minimum <= value < (1 << bits), name + " out of range")


def _layout(payload: bytes, fmt: str, names: str) -> dict:
    _require(len(payload) == struct.calcsize("<" + fmt), "invalid payload length")
    result = dict(zip(names.split(), struct.unpack("<" + fmt, payload)))
    if "reserved" in result:
        reserved = result.pop("reserved")
        _require(not any(reserved) if isinstance(reserved, bytes) else reserved == 0,
                 "nonzero reserved field")
    return result


def _envelope(raw: bytes) -> tuple[dict, bytes]:
    raw = _bytes(raw, "raw")
    _require(40 <= len(raw) <= 236, "invalid frame length")
    magic, version, kind, flags, length, seq, cap, ts, req, config, cal = _HEADER.unpack_from(raw)
    _require(magic == 0xE741, "invalid magic")
    _require(version == 2, "unsupported version")
    _require(flags & ~0x0F == 0, "unknown flags")
    _require(length <= 196 and len(raw) == 40 + length, "payload length mismatch")
    _require(zlib.crc32(raw[:-4]) == struct.unpack_from("<I", raw, len(raw) - 4)[0],
             "frame CRC32 mismatch")
    payload = raw[36:-4]
    return dict(type=kind, flags=flags, sequence=seq, capture_id=cap, timestamp_us=ts,
                request_id=req, sample_config_id=config, calibration_version=cal,
                payload_hex=payload.hex()), payload


def _sample_config(payload: bytes) -> dict:
    d = _layout(payload, "HHBBHHH",
                "ppg_hz imu_hz ppg_channels reserved config_id accel_range_g gyro_range_dps")
    _require(d["ppg_channels"] & ~7 == 0, "reserved PPG channel bits")
    # Current values (25/50 Hz, 4 g, 500 dps) are not wire-format constants.
    return d


def _live_control(payload: bytes) -> dict:
    d = _layout(payload, "BBH", "enable reserved max_batch_latency_ms")
    _require(d["enable"] in (0, 1), "invalid enable")
    # Other latencies have a valid wire format, but firmware may reject them.
    return d


def _batch(kind: int, payload: bytes) -> dict:
    _require(len(payload) >= 16, "truncated batch prefix")
    if kind == 0x11:
        d = _layout(payload[:16], "QIBBH", "first_sample_us period_us count format reserved")
        _require(d["format"] == 0, "unsupported IMU format")
        _require(len(payload) == 16 + d["count"] * 12, "IMU count/length mismatch")
        # The IMU section specifies the length equation, but no minimum count.
        d["samples"] = [list(row) for row in struct.iter_unpack("<6h", payload[16:])]
    else:
        d = _layout(payload[:16], "QIBBBB",
                    "first_sample_us period_us count channel_mask quality_format reserved")
        mask = d["channel_mask"]
        _require(1 <= mask <= 7, "invalid PPG channel_mask")
        _require(d["quality_format"] == 0, "unsupported PPG quality_format")
        channels = [name for bit, name in enumerate(("green", "red", "ir")) if mask & (1 << bit)]
        stride = 1 + 3 * len(channels)
        _require(d["count"] >= 1 and len(payload) == 16 + d["count"] * stride,
                 "PPG count/length mismatch")
        samples = []
        for start in range(16, len(payload), stride):
            sample = {name: int.from_bytes(payload[start + 3*i:start + 3*i + 3], "little")
                      for i, name in enumerate(channels)}
            sample["quality"] = payload[start + stride - 1]
            samples.append(sample)
        d["samples"] = samples
    return d


def _diagnostics(payload: bytes) -> dict:
    tlvs = []
    offset = 0
    while offset < len(payload):
        _require(offset + 2 <= len(payload), "truncated TLV header")
        tag, length = payload[offset:offset + 2]
        offset += 2
        _require(offset + length <= len(payload), "TLV length exceeds payload")
        value = payload[offset:offset + length]
        offset += length
        entry = dict(tag=tag, length=length, value_hex=value.hex())
        if tag in {*range(1, 10), 11}:
            entry["value"] = _layout(value, "I", "value")["value"]
        elif tag == 10:
            entry["value"] = _layout(value, "QIIB3s", "session_id size crc32 flags reserved")
            _require(entry["value"]["flags"] & ~3 == 0, "reserved audio diagnostic flags")
        tlvs.append(entry)
    return {"tlvs": tlvs}


def _device_payload(kind: int, payload: bytes) -> dict:
    if kind == 0x01:
        return _layout(payload, "HIIHBBI",
                       "hardware_revision firmware_version feature_bits max_frame "
                       "max_audio_sessions reserved sensor_cache_seconds")
    if kind == 0x02:
        return _layout(payload, "QQ", "device_rx_us device_tx_us")
    if kind == 0x03:
        return _sample_config(payload)
    if kind == 0x04:
        return _live_control(payload)
    if kind == 0x05:
        d = _layout(payload, "QIQIHH",
                    "oldest_capture_id oldest_sequence next_capture_id next_sequence storage_flags reserved")
        _require(d["storage_flags"] & ~1 == 0, "reserved storage_flags")
        return d
    if kind == 0x07:
        d = _layout(payload, "QIHH", "next_capture_id next_sequence sent_frames result")
        _require(d["sent_frames"] <= 32, "sent_frames exceeds page limit")
        _require(d["result"] in (0, 1), "invalid replay result")
        return d
    if kind in (0x10, 0x11):
        return _batch(kind, payload)
    if kind == 0x12:
        return _layout(payload, "QIII", "event_id bits step_count value")
    if kind == 0x13:
        d = _layout(payload, "IIBBHQQI", "first_missing_sequence after_last_missing_sequence "
                    "stream_id reason reserved first_missing_us after_last_missing_us dropped_samples")
        _require(d["stream_id"] in range(4) and d["reason"] in (1, 2, 3), "invalid gap enum")
        return d
    if kind == 0x14:
        d = _layout(payload, "QBBHQ", "event_id action source duration_ms audio_session_id")
        _require(d["source"] == 0 and d["action"] in range(1, 8), "invalid button action/source")
        _require(d["action"] != 1 or d["duration_ms"] == 0, "button press duration must be zero")
        return d
    if kind == 0x15:
        d = _layout(payload, "IiBBhhHI",
                    "voltage_uv soc_temperature_uc charger_state flags voltage_error charger_error reserved sample_age_ms")
        _require(d["charger_state"] in (0, 1, 2, 3, 255), "invalid charger_state")
        return d
    if kind == 0x20:
        _require(len(payload) >= 4, "truncated audio list")
        d = _layout(payload[:4], "B3s", "count reserved")
        _require(d["count"] <= 8 and len(payload) == 4 + 16 * d["count"], "audio list count/length mismatch")
        d["sessions"] = [_layout(payload[i:i+16], "QII", "session_id size whole_crc32")
                         for i in range(4, len(payload), 16)]
        return d
    if kind == 0x22:
        _require(len(payload) >= 24, "truncated audio data")
        d = _layout(payload[:24], "QIIIHH", "session_id total_size offset chunk_crc32 data_length flags")
        data = payload[24:]
        _require(d["session_id"] != 0, "zero audio session_id")
        _require(d["flags"] & ~1 == 0, "reserved audio flags")
        _require(d["data_length"] == len(data) and len(data) <= 172 and len(data) % 2 == 0,
                 "invalid audio data_length")
        _require(d["offset"] % 2 == 0 and d["total_size"] % 2 == 0
                 and d["offset"] + len(data) <= d["total_size"], "invalid audio range")
        more = d["offset"] + len(data) < d["total_size"]
        _require(bool(d["flags"] & 1) == more, "audio more flag/range mismatch")
        _require(data or not more, "empty audio block before EOF")
        _require(zlib.crc32(data) == d["chunk_crc32"], "audio chunk CRC32 mismatch")
        d["data_hex"] = data.hex()
        return d
    if kind == 0x23:
        return _layout(payload, "QII", "session_id total_size whole_crc32")
    if kind == 0x30:
        return _diagnostics(payload)
    if kind == 0x32:
        d = _layout(payload, "BBHI", "output preset status detail")
        # Rejected commands may echo an unsupported output/preset.
        _require(d["status"] in range(7), "invalid actuator status")
        return d
    if kind == 0x40:
        return _layout(payload, "", "")
    if kind == 0x7F:
        return _layout(payload, "HBBI", "code origin_type reserved detail")
    raise ValueError("request-only type received from device")


def decode_frame(raw: bytes) -> dict:
    """Validate a device frame and return lossless header/payload plus decoded data.

    Known host requests are rejected, including a missing RESPONSE on a query
    reply. Unknown types are deliberately exempt from direction/type semantics.
    ValueError denotes invalid input; no I/O or state changes occur.
    """
    frame, payload = _envelope(raw)
    kind = frame["type"]
    if kind not in _KNOWN:
        frame["decoded"] = {}
        return frame
    flags = frame["flags"] & ~MORE
    if kind in _RESPONSES:
        _require(flags == RESPONSE, "device response requires RESPONSE flags")
    elif kind in _PERSISTENT:
        _require(flags in (0, REPLAY), "invalid persistent frame flags")
    elif kind == 0x7F:
        _require(flags == ERROR, "ERROR frame requires ERROR flags")
    else:
        _require(kind in _EVENTS and flags == 0, "invalid device type/flags")
    if kind not in _PERSISTENT:
        _require(frame["sequence"] == 0, "nonpersistent device sequence must be zero")
    if kind in _RESPONSES | {0x07, 0x22, 0x32, 0x7F} or flags == REPLAY:
        _require(frame["request_id"] != 0, "associated device frame requires request_id")
    if kind in _PERSISTENT:
        _require(frame["sequence"] > 0 and frame["capture_id"] > 0,
                 "persistent frame requires capture_id and sequence")
        if flags == 0:
            _require(frame["request_id"] == 0, "live persistent frame request_id must be zero")
    frame["decoded"] = _device_payload(kind, payload)
    return frame


def _request_payload(kind: int, payload: bytes) -> None:
    if kind not in _KNOWN:
        return
    if kind in (0x01, 0x05, 0x20, 0x30, 0x40):
        _layout(payload, "", "")
    elif kind == 0x02:
        _layout(payload, "QQ", "utc_us app_monotonic_us")
    elif kind == 0x03:
        if payload:
            _sample_config(payload)
    elif kind == 0x04:
        _live_control(payload)
    elif kind == 0x06:
        d = _layout(payload, "QIHH", "capture_id next_sequence max_frames reserved")
        _require(d["max_frames"] >= 1, "max_frames must be positive")
    elif kind == 0x21:
        d = _layout(payload, "QIHH", "session_id offset max_length reserved")
        _require(d["session_id"] != 0 and d["offset"] % 2 == 0
                 and 2 <= d["max_length"] <= 172 and d["max_length"] % 2 == 0,
                 "invalid audio read fields")
    elif kind == 0x23:
        _layout(payload, "QII", "session_id total_size whole_crc32")
    elif kind == 0x31:
        d = _layout(payload, "BBH", "output preset reserved")
        _require(d["output"] in (1, 2) and d["preset"] in range(4), "invalid actuator preset")
    else:
        raise ValueError("device-only type cannot be encoded as request")


def encode_request(type: int, payload: bytes, request_id: int, sequence: int = 0) -> bytes:
    """Encode a host request; constructing bytes never sends or executes it.

    Request ID uniqueness and firmware support are the caller's responsibility.
    Unknown request types keep their payload opaque, as required by v2.
    """
    _uint(type, 8, "type")
    _uint(request_id, 32, "request_id", minimum=1)
    _uint(sequence, 32, "sequence")
    payload = _bytes(payload, "payload")
    _require(len(payload) <= 196, "payload too long")
    _request_payload(type, payload)
    raw = _HEADER.pack(0xE741, 2, type, 0, len(payload), sequence, 0, 0, request_id, 0, 0) + payload
    return raw + struct.pack("<I", zlib.crc32(raw))


def fragment_frame(raw: bytes, frame_id: int, max_write: int = 20) -> list[bytes]:
    """Split an envelope into GATT values, including six-byte fragment headers.

    max_write is ATT_MTU - 3, not the MTU itself. Both directions are supported;
    this transport helper checks the envelope, not the direction-specific payload.
    """
    raw = _bytes(raw, "raw")
    _envelope(raw)
    _uint(frame_id, 16, "frame_id")
    _require(isinstance(max_write, int) and not isinstance(max_write, bool) and max_write >= 7,
             "max_write must leave at least one data byte")
    step = max_write - 6
    return [_FRAGMENT.pack(frame_id, len(raw), offset) + raw[offset:offset + step]
            for offset in range(0, len(raw), step)]


class Reassembler:
    """One ordered frame at a time; every invalid input resets and raises ValueError.

    Exact previously received fragments can repeat. Partial overlaps, even with
    matching bytes, are not retransmissions of an identical fragment. Completed
    envelopes are CRC checked; decode_frame subsequently validates device payloads.
    A completed envelope failing validation raises FrameValidationError with its
    raw_frame for archival, after resetting state. Fragment errors remain plain
    ValueError and do not claim to contain a complete frame.
    After completion, duplicate noninitial fragments are ignored. Offset zero
    starts a new frame, including reuse of the same frame_id (not a dedup key).
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._frame_id = None
        self._total = 0
        self._data = bytearray()
        self._fragments = {}
        self._complete = False

    def feed(self, fragment: bytes) -> bytes | None:
        try:
            return self._feed(_bytes(fragment, "fragment"))
        except ValueError:
            self.reset()
            raise

    def _feed(self, fragment: bytes) -> bytes | None:
        _require(len(fragment) > 6, "empty or truncated fragment")
        frame_id, total, offset = _FRAGMENT.unpack_from(fragment)
        data = fragment[6:]
        _require(40 <= total <= 236, "invalid fragment total_length")
        _require(offset + len(data) <= total, "fragment out of bounds")
        if self._complete and offset == 0:
            self.reset()
        if self._frame_id is None:
            _require(offset == 0, "first fragment must start at zero")
            self._frame_id, self._total = frame_id, total
        _require((frame_id, total) == (self._frame_id, self._total), "interleaved frame or changed total_length")
        if offset in self._fragments and self._fragments[offset] == data:
            return None
        _require(offset == len(self._data), "out-of-order or overlapping fragment")
        self._data.extend(data)
        self._fragments[offset] = data
        if len(self._data) == total:
            raw = bytes(self._data)
            try:
                _envelope(raw)
            except ValueError as exc:
                raise FrameValidationError(str(exc), raw) from exc
            self._complete = True
            return raw
        return None
