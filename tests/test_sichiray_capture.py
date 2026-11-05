"""Offline checks for byte preservation and conservative format inspection."""

from contextlib import redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sichiray_capture as capture_tool


class FakePort:
    def __init__(self, pieces):
        self.pieces = list(pieces)
        self.is_open = False
        self.writes = 0

    @property
    def in_waiting(self):
        return len(self.pieces[0]) if self.pieces else 0

    def open(self):
        self.is_open = True

    def read(self, count):
        return self.pieces.pop(0) if self.pieces else b""

    def write(self, data):
        self.writes += 1
        raise AssertionError("Capture must never transmit device commands")

    def close(self):
        self.is_open = False


class CaptureTests(unittest.TestCase):
    def test_capture_preserves_fragmented_bytes_and_chunk_offsets(self):
        pieces = [b"12 34 56 ", b"78 90 12 34 56\n"]
        fake_port = FakePort(pieces)
        fake_serial = SimpleNamespace(
            serial_for_url=lambda *args, **kwargs: fake_port,
            SerialException=RuntimeError,
            EIGHTBITS=8,
            PARITY_NONE="N",
            STOPBITS_ONE=1,
        )
        with TemporaryDirectory() as directory:
            args = SimpleNamespace(port="COM_TEST", baud=115200, seconds=0.02,
                                   label="offline", output_root=Path(directory))
            with patch.object(capture_tool, "require_serial", return_value=fake_serial):
                with redirect_stdout(StringIO()):
                    self.assertEqual(capture_tool.capture(args), 0)
            session = next(Path(directory).iterdir())
            raw = (session / "serial_raw.bin").read_bytes()
            self.assertEqual(raw, b"".join(pieces))
            metadata = json.loads((session / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["status"], "completed")
            self.assertEqual(metadata["serial_raw_sha256"], hashlib.sha256(raw).hexdigest())
            chunks = [json.loads(line) for line in (session / "receive_chunks.jsonl").read_text().splitlines()]
            self.assertEqual([chunk["offset"] for chunk in chunks], [0, len(pieces[0])])
            self.assertEqual([chunk["bytes"] for chunk in chunks], [len(x) for x in pieces])
            self.assertEqual(fake_port.writes, 0)

    def test_inspection_reports_candidates_without_decoding_emg(self):
        frame = b"\xaa\xaa" + bytes([98]) + (1234).to_bytes(4, "big") + bytes(90) + b"\x55"
        self.assertEqual(len(frame), 98)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "serial_raw.bin"
            path.write_bytes(b"noise" + frame)
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(capture_tool.inspect(SimpleNamespace(path=path)), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["candidate_98_byte_hex_frames"], 1)
            self.assertEqual(report["candidate_first_offsets"], [5])
            self.assertEqual(report["candidate_first_device_ticks_big_endian"], [1234])
            self.assertEqual(report["eight_integer_text_lines"], 0)


if __name__ == "__main__":
    unittest.main()
