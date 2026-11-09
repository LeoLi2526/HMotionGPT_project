"""F1 offline conformance tests; no Bluetooth, third-party modules, or fixtures on disk."""

import binascii
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ering_protocol as protocol


VECTORS = [bytes.fromhex(text) for text in (
    "41e702010000000001000000000000000000000000000000000000000403020100000000376a0c1d",
    "41e7020301000c0000000000020000000000000040420f0000000000040302010100010019003200070001000400f401adcc31fe",
    "41e702150000140000000000020000000000000040420f00000000000403020101000100505b3b00c005d901010300000000000019000000eb5a6a92",
    "41e702600200020007000000020000000000000040420f00000000000403020101000100aa5525a696a4",
)]
MTU23_A = [bytes.fromhex(text) for text in (
    "07002800000041e7020100000000010000000000",
    "070028000e000000000000000000000000000000",
    "070028001c000403020100000000376a0c1d",
)]
PERSISTENT = {0x10, 0x11, 0x12, 0x13, 0x14}
RESPONSES = {1, 2, 3, 4, 5, 0x20, 0x23, 0x30, 0x40}


def crc_frame(body):
    return bytes(body) + struct.pack("<I", binascii.crc32(body))


def frame(kind, payload=b"", *, flags=None, sequence=None, request_id=None, capture_id=2):
    if flags is None:
        flags = 1 if kind in RESPONSES else 8 if kind == 0x7F else 0
    if sequence is None:
        sequence = 1 if kind in PERSISTENT else 0
    if request_id is None:
        request_id = 0 if kind in PERSISTENT and not flags & 2 else 0x01020304
    return crc_frame(struct.pack("<HBBHHIQQIHH", 0xE741, 2, kind, flags, len(payload),
                                 sequence, capture_id, 1000000, request_id, 1, 1) + payload)


def replace_payload(raw, payload):
    header = bytearray(raw[:36])
    struct.pack_into("<H", header, 6, len(payload))
    return crc_frame(header + payload)


def audio(data=b"\x00\x80\xff\x7f", *, size=None, offset=0, flags=0):
    if size is None:
        size = len(data)
    return struct.pack("<QIIIHH", 7, size, offset, binascii.crc32(data), len(data), flags) + data


def valid_payloads():
    return {
        1: struct.pack("<HIIHBBI", 0, 0x20000, 0x800000FF, 236, 1, 0, 3600),
        2: struct.pack("<QQ", 1000000, 1000010),
        3: bytes.fromhex("19003200070001000400f401"),
        4: struct.pack("<BBH", 1, 0, 500),
        5: struct.pack("<QIQIHH", 2, 1, 3, 9, 1, 0),
        7: struct.pack("<QIHH", 3, 9, 32, 0),
        0x10: struct.pack("<QIBBBB", 1000000, 40000, 1, 7, 0, 0)
              + bytes.fromhex("010000020000030000fa"),
        0x11: struct.pack("<QIBBH6h", 1000000, 20000, 1, 0, 0, -32768, -1, 0, 1, 42, 32767),
        0x12: struct.pack("<QIII", 7, 0x80000008, 10, 99),
        0x13: struct.pack("<IIBBHQQI", 3, 7, 2, 3, 0, 100, 200, 4),
        0x14: struct.pack("<QBBHQ", 7, 4, 0, 800, 9),
        0x15: struct.pack("<IiBBhhHI", 3890000, -31000000, 255, 0xFC, -5, -22, 0, 25),
        0x20: struct.pack("<B3sQII", 1, bytes(3), 7, 4, 0x12345678),
        0x22: audio(),
        0x23: struct.pack("<QII", 7, 4, 0x12345678),
        0x30: b"\x01\x04" + struct.pack("<I", 123) + b"\x0a\x14"
              + struct.pack("<QIIB3s", 7, 4, 0x12345678, 3, bytes(3)),
        0x32: struct.pack("<BBHI", 1, 2, 5, 0),
        0x40: b"",
        0x7F: struct.pack("<HBBI", 9, 6, 0, 99),
    }


class VectorTests(unittest.TestCase):
    def test_document_request_a_exact_encoding(self):
        self.assertEqual(protocol.encode_request(1, b"", 0x01020304, sequence=1), VECTORS[0])
        # A is a host request, not a device response.
        with self.assertRaises(ValueError):
            protocol.decode_frame(VECTORS[0])

    def test_document_response_b_full_dictionary(self):
        self.assertEqual(protocol.decode_frame(VECTORS[1]), {
            "type": 3, "flags": 1, "sequence": 0, "capture_id": 2,
            "timestamp_us": 1000000, "request_id": 0x01020304,
            "sample_config_id": 1, "calibration_version": 1,
            "payload_hex": "19003200070001000400f401",
            "decoded": {"ppg_hz": 25, "imu_hz": 50, "ppg_channels": 7,
                        "config_id": 1, "accel_range_g": 4, "gyro_range_dps": 500},
        })

    def test_document_power_c(self):
        self.assertEqual(protocol.decode_frame(VECTORS[2])["decoded"], {
            "voltage_uv": 3890000, "soc_temperature_uc": 31000000,
            "charger_state": 1, "flags": 3, "voltage_error": 0,
            "charger_error": 0, "sample_age_ms": 25,
        })

    def test_document_unknown_d_lossless(self):
        d = protocol.decode_frame(VECTORS[3])
        self.assertEqual(d["decoded"], {})
        self.assertEqual((d["type"], d["flags"], d["sequence"], d["capture_id"], d["payload_hex"]),
                         (0x60, 2, 7, 2, "aa55"))
        body = struct.pack("<HBBHHIQQIHH", 0xE741, 2, d["type"], d["flags"],
                           len(bytes.fromhex(d["payload_hex"])), d["sequence"], d["capture_id"],
                           d["timestamp_us"], d["request_id"], d["sample_config_id"], d["calibration_version"])
        self.assertEqual(crc_frame(body + bytes.fromhex(d["payload_hex"])), VECTORS[3])

    def test_document_mtu23_and_all_vectors_round_trip(self):
        self.assertEqual(protocol.fragment_frame(VECTORS[0], 7), MTU23_A)
        for raw in VECTORS:
            for max_write in (7, 20, 61, 244):
                with self.subTest(raw=raw.hex(), max_write=max_write):
                    assembler = protocol.Reassembler()
                    fragments = protocol.fragment_frame(raw, 7, max_write)
                    self.assertTrue(all(6 < len(part) <= max_write for part in fragments))
                    for part in fragments[:-1]:
                        self.assertIsNone(assembler.feed(part))
                    self.assertEqual(assembler.feed(fragments[-1]), raw)

    def test_uuid_constants(self):
        self.assertEqual(protocol.SERVICE_UUID, "e7f20001-6d50-4a9c-a721-41e4bc5f8a01")
        self.assertEqual(protocol.RX_UUID, "e7f20002-6d50-4a9c-a721-41e4bc5f8a01")
        self.assertEqual(protocol.TX_UUID, "e7f20003-6d50-4a9c-a721-41e4bc5f8a01")


class EnvelopeTests(unittest.TestCase):
    def test_crc_reference_and_each_byte_corruption(self):
        self.assertEqual(binascii.crc32(b"123456789"), 0xCBF43926)
        for original in VECTORS[1:]:
            for i in range(len(original)):
                with self.subTest(vector=original[3], byte=i):
                    raw = bytearray(original)
                    raw[i] ^= 1
                    with self.assertRaises(ValueError):
                        protocol.decode_frame(raw)

    def test_bad_envelope_with_correct_crc(self):
        for offset, replacement in ((0, b"\x00\x00"), (2, b"\x01"), (2, b"\x03"),
                                    (4, b"\x10\x00"), (4, b"\x00\x80"), (6, b"\x0b\x00"),
                                    (6, b"\xc5\x00")):
            with self.subTest(offset=offset, replacement=replacement):
                body = bytearray(VECTORS[1][:-4])
                body[offset:offset+len(replacement)] = replacement
                with self.assertRaises(ValueError):
                    protocol.decode_frame(crc_frame(body))
        for raw in (b"", bytes(39), bytes(237), VECTORS[1][:-1], VECTORS[1] + b"\x00"):
            with self.assertRaises(ValueError):
                protocol.decode_frame(raw)

    def test_unknown_types_only_validate_envelope(self):
        for kind in (0, 0x60, 0xFE, 0xFF):
            for flags in range(16):
                with self.subTest(kind=kind, flags=flags):
                    payload = bytes(range(196))
                    d = protocol.decode_frame(frame(kind, payload, flags=flags, sequence=17,
                                                    request_id=0, capture_id=0))
                    self.assertEqual(d["decoded"], {})
                    self.assertEqual(d["payload_hex"], payload.hex())
                    self.assertEqual(d["flags"], flags)

    def test_type_flags_matrix_and_more_preservation(self):
        for kind, payload in valid_payloads().items():
            bases = (0, 2) if kind in PERSISTENT else (1,) if kind in RESPONSES else (8,) if kind == 0x7F else (0,)
            allowed = set(bases) | {base | 4 for base in bases}
            for flags in range(16):
                with self.subTest(kind=kind, flags=flags):
                    raw = frame(kind, payload, flags=flags)
                    if flags in allowed:
                        self.assertEqual(protocol.decode_frame(raw)["flags"], flags)
                    else:
                        with self.assertRaises(ValueError):
                            protocol.decode_frame(raw)

    def test_nonpersistent_sequence_and_request_matching_fields(self):
        for kind, payload in valid_payloads().items():
            if kind not in PERSISTENT:
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    protocol.decode_frame(frame(kind, payload, sequence=1))
            if kind in RESPONSES | {7, 0x22, 0x32, 0x7F}:
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    protocol.decode_frame(frame(kind, payload, request_id=0))
                d = protocol.decode_frame(frame(kind, payload, request_id=0xFFFFFFFF))
                self.assertEqual(d["request_id"], 0xFFFFFFFF)
        for changes in ({"sequence": 0}, {"capture_id": 0}, {"request_id": 5},
                        {"flags": 2, "request_id": 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                protocol.decode_frame(frame(0x11, valid_payloads()[0x11], **changes))

    def test_request_only_types_cannot_arrive_from_device(self):
        for kind, payload in ((6, struct.pack("<QIHH", 2, 1, 32, 0)),
                              (0x21, struct.pack("<QIHH", 7, 0, 2, 0)),
                              (0x31, struct.pack("<BBH", 2, 1, 0))):
            for flags in range(16):
                with self.subTest(kind=kind, flags=flags), self.assertRaises(ValueError):
                    protocol.decode_frame(frame(kind, payload, flags=flags))


class PayloadTests(unittest.TestCase):
    def test_every_known_device_payload_and_fixed_lengths(self):
        for kind, payload in valid_payloads().items():
            with self.subTest(kind=kind):
                self.assertIsInstance(protocol.decode_frame(frame(kind, payload))["decoded"], dict)
                if payload:
                    with self.assertRaises(ValueError):
                        protocol.decode_frame(frame(kind, payload[:-1]))
                with self.assertRaises(ValueError):
                    protocol.decode_frame(frame(kind, payload + b"\x00"))

    def test_required_field_names_and_raw_values(self):
        d = {kind: protocol.decode_frame(frame(kind, p))["decoded"] for kind, p in valid_payloads().items()}
        self.assertEqual(d[1]["firmware_version"], 0x20000)
        self.assertEqual(d[1]["feature_bits"], 0x800000FF)
        self.assertEqual(d[2], {"device_rx_us": 1000000, "device_tx_us": 1000010})
        self.assertEqual(d[5], {"oldest_capture_id": 2, "oldest_sequence": 1,
                               "next_capture_id": 3, "next_sequence": 9, "storage_flags": 1})
        self.assertEqual(d[7], {"next_capture_id": 3, "next_sequence": 9, "sent_frames": 32, "result": 0})
        self.assertEqual(d[0x11]["samples"], [[-32768, -1, 0, 1, 42, 32767]])
        self.assertEqual(d[0x11]["first_sample_us"], 1000000)
        self.assertEqual(d[0x11]["period_us"], 20000)
        self.assertEqual(d[0x10]["samples"], [{"green": 1, "red": 2, "ir": 3, "quality": 250}])
        self.assertEqual(d[0x12]["bits"], 0x80000008)
        self.assertEqual(d[0x15]["soc_temperature_uc"], -31000000)
        self.assertEqual(d[0x15]["voltage_error"], -5)
        self.assertEqual(d[0x15]["charger_error"], -22)

    def test_reserved_fields_and_invalid_enums(self):
        mutations = {
            1: [(13, 1)], 3: [(4, 8), (5, 1)], 4: [(0, 2), (1, 1)],
            5: [(24, 2), (26, 1)], 7: [(12, 33), (14, 2)],
            0x10: [(12, 0), (12, 2), (13, 0), (13, 8), (14, 1), (15, 1)],
            0x11: [(12, 0), (12, 2), (13, 1), (14, 1), (15, 1)],
            0x13: [(8, 4), (9, 0), (9, 4), (10, 1), (11, 1)],
            0x14: [(8, 0), (8, 8), (9, 1)], 0x15: [(8, 4), (14, 1), (15, 1)],
            0x20: [(0, 0), (0, 2), (0, 9), (1, 1), (2, 1), (3, 1)],
            0x32: [(2, 7)], 0x7F: [(3, 1)],
        }
        for kind, changes in mutations.items():
            for offset, value in changes:
                with self.subTest(kind=kind, offset=offset, value=value):
                    payload = bytearray(valid_payloads()[kind])
                    payload[offset] = value
                    with self.assertRaises(ValueError):
                        protocol.decode_frame(frame(kind, payload))

    def test_ppg_u24_all_masks_and_quality_u8(self):
        values = (0xFFFFFF, 0x800000, 0x123456)
        for mask in range(1, 8):
            channels = [name for i, name in enumerate(("green", "red", "ir")) if mask & (1 << i)]
            sample = b"".join(values[i].to_bytes(3, "little") for i in range(3) if mask & (1 << i)) + b"\xff"
            payload = struct.pack("<QIBBBB", 17, 40000, 1, mask, 0, 0) + sample
            d = protocol.decode_frame(frame(0x10, payload))["decoded"]
            expected = {name: values[i] for i, name in enumerate(("green", "red", "ir")) if name in channels}
            expected["quality"] = 255
            self.assertEqual(d["samples"], [expected])

    def test_batch_limits_and_imu_zero_count_length_equation(self):
        imu = struct.pack("<QIBBH", 1, 20000, 15, 0, 0) + struct.pack("<6h", *range(6)) * 15
        self.assertEqual(len(frame(0x11, imu)), 236)
        self.assertEqual(len(protocol.decode_frame(frame(0x11, imu))["decoded"]["samples"]), 15)
        ppg = struct.pack("<QIBBBB", 1, 40000, 18, 7, 0, 0) + bytes(180)
        self.assertEqual(len(protocol.decode_frame(frame(0x10, ppg))["decoded"]["samples"]), 18)
        zero = struct.pack("<QIBBH", 1, 20000, 0, 0, 0)
        self.assertEqual(protocol.decode_frame(frame(0x11, zero))["decoded"]["samples"], [])
        with self.assertRaises(ValueError):
            protocol.decode_frame(frame(0x10, struct.pack("<QIBBBB", 1, 40000, 0, 7, 0, 0)))

    def test_audio_data_internal_crc_range_parity_and_eof(self):
        good = audio()
        bad = []
        for offset, value in ((0, 0), (8, 3), (12, 1), (12, 6), (16, 0), (20, 2), (22, 2), (22, 1)):
            payload = bytearray(good)
            payload[offset] = value
            bad.append(payload)
        bad += [audio(b"\x01"), audio(b"", size=4, flags=1), audio(b"12", size=4, flags=0),
                audio(bytes(174))]
        for payload in bad:
            with self.subTest(payload=bytes(payload).hex()), self.assertRaises(ValueError):
                protocol.decode_frame(frame(0x22, payload))
        for payload in (audio(b"", size=4, offset=4), audio(b"12", size=4, flags=1), audio(bytes(172))):
            d = protocol.decode_frame(frame(0x22, payload))["decoded"]
            self.assertEqual(d["data_hex"], payload[24:].hex())
        corrupt = bytearray(good)
        corrupt[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "chunk CRC32"):
            protocol.decode_frame(frame(0x22, corrupt))

    def test_audio_list_wire_count_zero_through_eight(self):
        for count in range(9):
            payload = bytes([count, 0, 0, 0]) + struct.pack("<QII", 7, 4, 123) * count
            d = protocol.decode_frame(frame(0x20, payload))["decoded"]
            self.assertEqual(len(d["sessions"]), count)

    def test_diagnostic_known_unknown_repeated_tlvs_and_bounds(self):
        payload = b"\xfe\x03abc\xfe\x00" + b"\x01\x04" + struct.pack("<I", 0xFFFFFFFF)
        d = protocol.decode_frame(frame(0x30, payload))["decoded"]["tlvs"]
        self.assertEqual(d[:2], [{"tag": 254, "length": 3, "value_hex": "616263"},
                                 {"tag": 254, "length": 0, "value_hex": ""}])
        self.assertEqual(d[2]["value"], 0xFFFFFFFF)
        self.assertEqual(protocol.decode_frame(frame(0x30))["decoded"], {"tlvs": []})
        bad = [b"\x01", b"\xfe\x02x", b"\x01\x03abc", b"\x0b\x05abcde", b"\x0a\x00"]
        for offset, value in ((16, 4), (17, 1), (18, 1), (19, 1)):
            block = bytearray(struct.pack("<QIIB3s", 7, 4, 123, 3, bytes(3)))
            block[offset] = value
            bad.append(b"\x0a\x14" + block)
        for payload in bad:
            with self.subTest(payload=payload.hex()), self.assertRaises(ValueError):
                protocol.decode_frame(frame(0x30, payload))


class RequestTests(unittest.TestCase):
    def test_all_request_layouts_and_zeroed_device_fields(self):
        payloads = {1: b"", 2: struct.pack("<QQ", 123, 456), 3: b"", 4: struct.pack("<BBH", 1, 0, 500),
                    5: b"", 6: struct.pack("<QIHH", 2, 1, 65535, 0), 0x20: b"",
                    0x21: struct.pack("<QIHH", 7, 2, 172, 0), 0x23: struct.pack("<QII", 7, 4, 123),
                    0x30: b"", 0x31: struct.pack("<BBH", 2, 3, 0), 0x40: b"", 0x60: bytes(range(196))}
        for kind, payload in payloads.items():
            with self.subTest(kind=kind):
                raw = protocol.encode_request(kind, payload, 0xFFFFFFFF, 0xFFFFFFFF)
                h = struct.unpack("<HBBHHIQQIHH", raw[:36])
                self.assertEqual(h, (0xE741, 2, kind, 0, len(payload), 0xFFFFFFFF, 0, 0, 0xFFFFFFFF, 0, 0))
                self.assertEqual(raw[36:-4], payload)
                self.assertEqual(crc_frame(raw[:-4]), raw)
        self.assertEqual(protocol.encode_request(3, valid_payloads()[3], 1)[36:-4], valid_payloads()[3])

    def test_reject_wrong_request_lengths_reserved_and_fields(self):
        bad = [(1, b"x"), (2, bytes(15)), (3, bytes(11)), (4, b"\x02\x00\xf4\x01"),
               (4, b"\x01\x01\xf4\x01"), (5, b"x"), (0x20, bytes(4)), (0x30, b"x"), (0x40, b"x"),
               (6, struct.pack("<QIHH", 2, 1, 0, 0)), (6, struct.pack("<QIHH", 2, 1, 32, 1)),
               (0x23, bytes(15)), (0x31, b"\x00\x01\x00\x00"), (0x31, b"\x02\x04\x00\x00"),
               (0x31, b"\x02\x01\x01\x00"), (0x60, bytes(197))]
        bad += [(0x21, struct.pack("<QIHH", *args)) for args in (
            (0, 0, 2, 0), (7, 1, 2, 0), (7, 0, 0, 0), (7, 0, 3, 0), (7, 0, 174, 0), (7, 0, 2, 1))]
        for kind, payload in bad:
            with self.subTest(kind=kind, payload=payload.hex()), self.assertRaises(ValueError):
                protocol.encode_request(kind, payload, 1)
        for kind in PERSISTENT | {7, 0x15, 0x22, 0x32, 0x7F}:
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                protocol.encode_request(kind, valid_payloads()[kind], 1)

    def test_integer_and_bytes_input_validation(self):
        for kwargs in ({"type": -1}, {"type": 256}, {"type": True}, {"type": 1.0},
                       {"request_id": 0}, {"request_id": 2**32}, {"request_id": -1},
                       {"sequence": -1}, {"sequence": 2**32}, {"sequence": 1.5}, {"payload": ""}):
            args = {"type": 1, "payload": b"", "request_id": 1}
            args.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                protocol.encode_request(**args)
        for raw in (None, 40, "abc"):
            with self.assertRaises(ValueError):
                protocol.decode_frame(raw)


class FragmentTests(unittest.TestCase):
    def test_duplicate_fragments_and_complete_frame_retransmission(self):
        assembler = protocol.Reassembler()
        for part in MTU23_A[:-1]:
            self.assertIsNone(assembler.feed(part))
            self.assertIsNone(assembler.feed(part))
        self.assertIsNone(assembler.feed(MTU23_A[0]))
        self.assertEqual(assembler.feed(MTU23_A[-1]), VECTORS[0])
        self.assertIsNone(assembler.feed(MTU23_A[-1]))
        self.assertIsNone(assembler.feed(MTU23_A[1]))
        for part in MTU23_A[:-1]:
            self.assertIsNone(assembler.feed(part))
        self.assertEqual(assembler.feed(MTU23_A[-1]), VECTORS[0])

    def test_bad_fragments_reset_and_allow_clean_retry(self):
        data = VECTORS[0]
        invalid = [b"", bytes(6), b"abc", None, "fragment",
                   struct.pack("<HHH", 7, 39, 14) + b"x",
                   struct.pack("<HHH", 7, 237, 14) + b"x",
                   struct.pack("<HHH", 7, 40, 40) + b"x",
                   struct.pack("<HHH", 7, 40, 65535) + b"x",
                   struct.pack("<HHH", 8, 40, 0) + data[:14],
                   struct.pack("<HHH", 7, 41, 14) + data[14:28],
                   struct.pack("<HHH", 7, 40, 0) + b"x" * 14,
                   struct.pack("<HHH", 7, 40, 7) + data[7:21],
                   struct.pack("<HHH", 7, 40, 0) + data[:7],
                   MTU23_A[2]]
        for part in invalid:
            with self.subTest(part=part):
                assembler = protocol.Reassembler()
                assembler.feed(MTU23_A[0])
                with self.assertRaises(ValueError) as caught:
                    assembler.feed(part)
                self.assertIs(type(caught.exception), ValueError)
                self.assertFalse(hasattr(caught.exception, "raw_frame"))
                with self.assertRaises(ValueError):
                    assembler.feed(MTU23_A[1])
                self.assertIsNone(assembler.feed(MTU23_A[0]))
                self.assertIsNone(assembler.feed(MTU23_A[1]))
                self.assertEqual(assembler.feed(MTU23_A[2]), data)

    def test_reset_disconnect_frame_id_wrap_and_single_fragment(self):
        assembler = protocol.Reassembler()
        assembler.feed(MTU23_A[0])
        assembler.reset()
        with self.assertRaises(ValueError):
            assembler.feed(MTU23_A[1])
        for fid in (65535, 0, 0, 1):
            parts = protocol.fragment_frame(VECTORS[1], fid, 244)
            self.assertEqual(len(parts), 1)
            self.assertEqual(assembler.feed(parts[0]), VECTORS[1])

    def test_bad_complete_envelope_preserves_raw_frame_and_resets(self):
        for corrupt_offset, error in ((-1, "CRC32"), (0, "magic"), (2, "version"),
                                      (4, "unknown flags"), (6, "payload length")):
            for max_write in (20, 244):
                with self.subTest(offset=corrupt_offset, max_write=max_write):
                    # Corrupt after valid fragmentation, exercising reception
                    # rather than failing the strict sending helper first.
                    parts = [bytearray(p) for p in protocol.fragment_frame(VECTORS[1], 7, max_write)]
                    raw = bytearray(VECTORS[1])
                    mask = 0x10 if corrupt_offset == 4 else 1
                    raw[corrupt_offset] ^= mask
                    position = corrupt_offset % len(raw)
                    for part in parts:
                        offset = struct.unpack_from("<H", part, 4)[0]
                        if offset <= position < offset + len(part) - 6:
                            part[6 + position - offset] ^= mask
                    assembler = protocol.Reassembler()
                    for part in parts[:-1]:
                        self.assertIsNone(assembler.feed(part))
                    with self.assertRaisesRegex(protocol.FrameValidationError, error) as caught:
                        assembler.feed(parts[-1])
                    failure = caught.exception
                    self.assertIsInstance(failure, ValueError)
                    self.assertIs(type(failure.raw_frame), bytes)
                    self.assertEqual(failure.raw_frame, bytes(raw))
                    self.assertIs(type(failure.__cause__), ValueError)
                    # Reset discarded the failed frame, including duplicate state.
                    with self.assertRaises(ValueError) as partial:
                        assembler.feed(MTU23_A[1])
                    self.assertIs(type(partial.exception), ValueError)
                    self.assertFalse(hasattr(partial.exception, "raw_frame"))
                    for part in MTU23_A[:-1]:
                        self.assertIsNone(assembler.feed(part))
                    self.assertEqual(assembler.feed(MTU23_A[-1]), VECTORS[0])
                    # Caller buffers and later frames cannot change the evidence.
                    parts[-1][-1] ^= 0xFF
                    self.assertEqual(failure.raw_frame, bytes(raw))
                    with self.assertRaises(ValueError) as sender:
                        protocol.fragment_frame(raw, 7)
                    self.assertIs(type(sender.exception), ValueError)
                    with self.assertRaises(ValueError) as decoder:
                        protocol.decode_frame(raw)
                    self.assertIs(type(decoder.exception), ValueError)

    def test_fragment_parameters_and_maximum_frame(self):
        for fid in (-1, 65536, True, 1.5):
            with self.assertRaises(ValueError):
                protocol.fragment_frame(VECTORS[0], fid)
        for size in (0, 6, -1, True, 20.5):
            with self.assertRaises(ValueError):
                protocol.fragment_frame(VECTORS[0], 1, size)
        with self.assertRaises(ValueError):
            protocol.fragment_frame(bytes(40), 1)
        raw = protocol.encode_request(0x60, bytes(range(196)), 1)
        assembler = protocol.Reassembler()
        parts = protocol.fragment_frame(raw, 65535)
        self.assertEqual(len(parts), 17)
        for part in parts[:-1]:
            self.assertIsNone(assembler.feed(part))
        self.assertEqual(assembler.feed(parts[-1]), raw)


if __name__ == "__main__":
    unittest.main()
