"""Recording lifecycle checks without opening a window or listening to the mouse."""
from collections import deque
import csv
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import matplotlib
matplotlib.use("Agg")

from app import App, MultiSource, Source, draw_data, format_duration, make_figure
from core import Packet, Sample, Session


def sample(ns):
    return Sample(Packet(0, 0, 8192, 65535, 0, 1, 14), 0, ns, ns, 0, (10,), (5,))


class RecordingDisplayTests(unittest.TestCase):
    def test_start_stop_and_second_recording_share_csv_plot_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = App.__new__(App)
            app.args = SimpleNamespace(output=Path(tmp))
            app.capture_mouse = True
            app.source = SimpleNamespace(demo=True, port="", channels=1,
                processor=SimpleNamespace(offset_ns=0, clock=SimpleNamespace(lost=0)),
                parser=SimpleNamespace(bad_frames=0))
            app.session = app.listener = app.last_session = None
            app.recording_end_ns = None
            app.origin_ns = 100
            app.history = deque([sample(100)])
            app.events = deque([(100, "left", True)])
            app.inbox = queue.Queue()
            app.plots = make_figure(1, ["CH1"], True)
            for name in ("canvas", "record_btn", "path_status", "record_status", "timer",
                         "recording_name", "name_entry", "rename_btn"):
                setattr(app, name, Mock())
            for name, value in (("mapping", "Sin asignar dedos"), ("name1", "CH1"),
                                ("name2", "CH2"), ("notch", "50"), ("label", "reposo")):
                setattr(app, name, Mock(get=Mock(return_value=value)))
            for start in (10_000_000_000, 30_000_000_000):
                fake_mouse = SimpleNamespace(mouse=SimpleNamespace(Listener=Mock()))
                with patch("app.time.perf_counter_ns", return_value=start), patch.dict("sys.modules", {"pynput": fake_mouse}):
                    app.toggle_record()
                session = app.session
                self.assertEqual(session.start_ns, start)
                self.assertEqual(app.origin_ns, start)
                self.assertEqual(len(app.events), 0)
                self.assertEqual(len(app.plots[2][0].get_xdata()), 0)
                app.timer.set.assert_called_with("00:00:00.0")
                app.inbox.put(("samples", [sample(start - 1), sample(start + 500_000_000)]))
                app.inbox.put(("click", (start - 1, "left", True)))
                app.inbox.put(("click", (start + 500_000_000, "left", True)))
                app.drain()
                app.update_plot(start + 1_000_000_000)
                self.assertEqual(list(app.plots[2][0].get_xdata()), [.5])
                self.assertEqual(list(app.plots[4]["left", True].get_xdata()), [.5])
                with patch("app.time.perf_counter_ns", return_value=start + 2_000_000_000):
                    app.stop_record()
                self.assertIsNone(app.session)
                self.assertIs(app.last_session, session)
                self.assertEqual(app.recording_end_ns, start + 2_000_000_000)
                app.timer.set.assert_called_with("00:00:02.0")
                app.name_entry.configure.assert_called_with(state="normal")
                self.assertEqual(session.metadata["duration_s"], 2)
                for filename, field in (("emg.csv", "time_s_est"), ("clicks.csv", "time_s")):
                    with (session.path / filename).open(newline="") as handle:
                        rows = list(csv.DictReader(handle))
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(float(rows[0][field]), .5)
                with (session.path / "markers.csv").open(newline="") as handle:
                    self.assertEqual(float(next(csv.DictReader(handle))["time_s"]), 0)

    def test_preview_and_empty_recording_do_not_show_previous_data(self):
        plots = make_figure(1, ["CH1"], True, (-100, 100))
        draw_data(plots, [sample(9_000_000_000)], [], 10_000_000_000, 0, preview=True)
        self.assertEqual(list(plots[2][0].get_xdata()), [-1])
        self.assertEqual(plots[1][0].get_xlim(), (-8, 0))
        draw_data(plots, [sample(9_000_000_000)], [(9_000_000_000, "left", True)], 10_000_000_000, 0)
        self.assertEqual(len(plots[2][0].get_xdata()), 0)
        self.assertEqual(len(plots[3][0].get_xdata()), 0)
        self.assertEqual(len(plots[4]["left", True].get_xdata()), 0)
        self.assertEqual(plots[1][0].get_xlim(), (0, 8))
        self.assertEqual(plots[1][0].get_ylim(), (-100, 100))

    def test_counter_handles_hours(self):
        self.assertEqual(format_duration(3661.25), "01:01:01.2")
        self.assertEqual(format_duration(-1), "00:00:00.0")

    def test_independent_ports_preserve_separate_times_files_and_plot(self):
        from plotly_recording import make_recording_plot
        with tempfile.TemporaryDirectory() as tmp:
            inbox = queue.Queue()
            sources = MultiSource(inbox, ["COM3", "COM4"], 0)
            self.assertEqual([s.port for s in sources.sources], ["COM3", "COM4"])
            for index, source in enumerate(sources.sources):
                source.send("samples", [sample(2_000_000_000 + index * 250_000_000)])
            app = App.__new__(App)
            app.inbox, app.source = inbox, sources
            app.history, app.second_history, app.events = deque(), deque(), deque()
            app.session = Session(Path(tmp), {"channels": 2, "independent_ports": True}, 1_000_000_000)
            session = app.session
            app.drain()
            app.plots = make_figure(2, ["A", "B"])
            app.draw_current(1_000_000_000, 2)
            self.assertEqual(list(app.plots[2][0].get_xdata()), [1])
            self.assertEqual(list(app.plots[2][1].get_xdata()), [1.25])
            session.close(end_ns=3_000_000_000)
            for name, expected in (("emg.csv", 1), ("emg_ch2.csv", 1.25)):
                with (session.path / name).open(newline="") as f:
                    self.assertEqual(float(next(csv.DictReader(f))["time_s_est"]), expected)
            fig = make_recording_plot(session.path)
            self.assertEqual(list(fig.data[0].x), [1])
            self.assertEqual(list(fig.data[2].x), [1.25])

    def test_multisource_stops_and_reports_each_port_failure(self):
        sources = MultiSource(queue.Queue(), ["COM3", "COM4"], 50)
        sources.sources[1].error = "sin datos"
        self.assertIn("COM4: sin datos", sources.error)
        sources.set()
        self.assertTrue(all(s.stop_event.is_set() for s in sources.sources))
        self.assertEqual([d["port"] for d in sources.details()], ["COM3", "COM4"])


if __name__ == "__main__":
    unittest.main()
