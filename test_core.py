import csv
import json
import math
from pathlib import Path
import tempfile
import unittest

from core import ChannelFilter, DeviceClock, Packet, Parser, Processor, Session, encode


def packet(seq=0, us=1000, channels=2):
    return Packet(seq, us, 8192, 8000 if channels == 2 else 65535,
                  20 if channels == 2 else 0, channels, 14)


class ProtocolTests(unittest.TestCase):
    def test_arbitrary_chunking_noise_crc_and_resync(self):
        parser = Parser()
        corrupt = bytearray(encode(packet(1, 1500)))
        corrupt[10] ^= 1
        stream = b"boot noise" + encode(packet()) + corrupt + encode(packet(2, 2000))
        result = []
        for byte in stream:
            result.extend(parser.feed(bytes([byte])))
        self.assertEqual([p.sequence for p in result], [0, 2])
        self.assertGreater(parser.bad_frames, 0)
        self.assertLess(len(parser.buffer), 20)

    def test_both_counter_wraps_and_missing_samples(self):
        clock = DeviceClock()
        clock.update(packet(0xFFFFFFFE, 0xFFFFFE0C))
        us, gap = clock.update(packet(0, 500))
        self.assertEqual(us, 2**32 + 500)
        self.assertEqual(gap, 1)
        self.assertEqual(clock.lost, 1)

    def test_reset_is_not_interpreted_as_hours_of_data(self):
        clock = DeviceClock()
        clock.update(packet(3000, 1500000))
        with self.assertRaises(ValueError):
            clock.update(packet(0, 100))

    def test_one_channel_sentinel(self):
        p = packet(channels=1)
        self.assertEqual(Parser().feed(encode(p)), [p])

    def test_clock_offset_is_calibrated_then_fixed(self):
        p = Processor(2, 50, calibration_s=.001)
        p.update(packet(0, 1000), 2000000)
        p.update(packet(1, 1500), 2600000)
        sample = p.update(packet(2, 2000), 3100000)
        self.assertTrue(p.ready)
        fixed = p.offset_ns
        later = p.update(packet(3, 2500), 9000000)  # USB buffering changes receive time
        self.assertEqual(later.estimated_ns - sample.estimated_ns, 500000)
        self.assertEqual(p.offset_ns, fixed)

    def test_filter_is_reset_after_gap(self):
        p = Processor(2, 0, calibration_s=0)
        p.update(packet(), 100000000)
        p.update(Packet(1, 1500, 10000, 8000, 20, 2, 14), 100500000)
        s = p.update(Packet(3, 2500, 10000, 8000, 20, 2, 14), 101500000)
        self.assertEqual(s.gap, 1)
        self.assertEqual(s.rms, (0, 0))


class FilterTests(unittest.TestCase):
    def test_dc_notch_and_passband(self):
        def rms_at(hz, notch):
            f = ChannelFilter(notch)
            values = [f.update(8192 + 500 * math.sin(2 * math.pi * hz * i / 2000))[0]
                      for i in range(8000)]
            return math.sqrt(sum(x*x for x in values[-2000:]) / 2000)
        self.assertLess(rms_at(50, 50), rms_at(50, 0) * .05)
        self.assertGreater(rms_at(140, 50), 300)
        f = ChannelFilter(50)
        self.assertEqual(f.update(8192), (0, 0))


class RecordingTests(unittest.TestCase):
    def test_name_closed_recording_preserves_files_and_unique_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Session(Path(tmp), {"demo": True}, 1_000_000_000)
            with self.assertRaises(ValueError):
                session.rename("Prueba")
            session.close(end_ns=3_500_000_000)
            original = session.path
            data = (original / "emg.csv").read_bytes()
            session.rename("Prueba índice")
            self.assertFalse(original.exists())
            self.assertEqual(session.path.parent, Path(tmp).resolve())
            self.assertEqual(session.path.name, "Prueba índice_" + original.name)
            self.assertEqual((session.path / "emg.csv").read_bytes(), data)
            meta = json.loads((session.path / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["recording_name"], "Prueba índice")
            self.assertEqual(meta["duration_s"], 2.5)
            session.rename("Prueba índice")
            session.rename("Segundo nombre")
            self.assertEqual(session.path.name, "Segundo nombre_" + original.name)
            for invalid in ("", "../escape", "a/b", "a\\b", "CON", "x.", "a:b", "x" * 81):
                with self.assertRaises(ValueError):
                    session.rename(invalid)
            occupied = Path(tmp) / ("Ocupado_" + original.name)
            occupied.mkdir()
            with self.assertRaises(FileExistsError):
                session.rename("Ocupado")
            self.assertTrue(session.path.exists())
            self.assertEqual((session.path / "emg.csv").read_bytes(), data)

    def test_timestamps_finger_mapping_and_incomplete_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            origin = 1000000000
            session = Session(Path(tmp), {"demo": True, "capture_mouse_events": True,
                                          "finger_mapping": {"left": "indice"}}, origin)
            processor = Processor(1, 0, calibration_s=0)
            s = processor.update(packet(channels=1), origin)
            session.sample(s)
            session.click(origin-1, "left", True)  # pre-recording event excluded
            session.click(origin+10000000, "left", True)
            session.click(origin+30000000, "left", False)
            session.marker("índice", origin+5000000)
            session.close("USB desconectado", origin+100000000)
            session.close()  # idempotent
            meta = json.loads((session.path / "metadata.json").read_text(encoding="utf-8"))
            self.assertFalse(meta["complete"])
            self.assertEqual(meta["click_presses"], 1)
            self.assertEqual(meta["samples"], 1)
            with (session.path / "emg.csv").open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["ch2_adc"], "")
            with (session.path / "clicks.csv").open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["assigned_finger"], "indice")
            self.assertAlmostEqual(float(rows[0]["time_s"]), .01)

    def test_default_session_records_only_emg_and_manual_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            origin = 1000000000
            session = Session(Path(tmp), {"demo": True}, origin)
            processor = Processor(2, 0, calibration_s=0)
            session.sample(processor.update(packet(), origin))
            session.click(origin+10000000, "left", True)
            session.marker("reposo", origin)
            session.close(end_ns=origin+100000000)
            self.assertEqual({p.name for p in session.path.iterdir()},
                             {"emg.csv", "markers.csv", "metadata.json"})
            meta = json.loads((session.path / "metadata.json").read_text(encoding="utf-8"))
            self.assertFalse(meta["capture_mouse_events"])
            self.assertNotIn("mouse_timing", meta)
            self.assertNotIn("click_presses", meta)
            self.assertEqual(meta["samples"], 1)
            self.assertTrue(meta["complete"])


if __name__ == "__main__":
    unittest.main()
