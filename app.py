"""Live EMG recording. Optional mouse events require --mouse-events explicitly."""
from __future__ import annotations

import argparse
from collections import deque
import math
from pathlib import Path
import queue
import threading
import time

from core import BAUD, FS, Packet, Parser, Processor, Session

ROOT = Path(__file__).resolve().parent


def format_duration(seconds):
    tenths = max(0, int(seconds * 10))
    hours, remainder = divmod(tenths, 36000)
    minutes, remainder = divmod(remainder, 600)
    whole_seconds, fraction = divmod(remainder, 10)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{fraction}"


def demo_packet(index: int, channels: int) -> Packet:
    """Synthetic bursts: deliberately NOT a model for classifying fingers."""
    t = index / FS
    phase = t % 4
    burst = math.exp(-((phase - 1) / .13) ** 2) + .7 * math.exp(-((phase - 3) / .16) ** 2)
    c1 = int(8192 + (70 + 1350 * burst) * (math.sin(2 * math.pi * 139 * t)
                                         + .28 * math.sin(2 * math.pi * 257 * t)))
    c2 = int(8192 + (60 + 1050 * burst) * math.sin(2 * math.pi * 181 * t))
    return Packet(index & 0xFFFFFFFF, (index * 500) & 0xFFFFFFFF,
                  c1, c2 if channels == 2 else 65535, 20 if channels == 2 else 0, channels, 14)


class Source(threading.Thread):
    def __init__(self, inbox: queue.Queue, demo: bool, port: str, channels: int, notch: int):
        super().__init__(daemon=True, name="EMG acquisition")
        self.inbox, self.demo, self.port = inbox, demo, port
        self.channels = channels
        self.stop_event = threading.Event()
        self.processor = Processor(channels, notch)
        self.parser = Parser()
        self.error = None
        self.received = 0

    def send(self, kind: str, payload):
        try:
            self.inbox.put_nowait((kind, payload))
        except queue.Full:
            raise RuntimeError("La aplicación no procesa los datos a tiempo. Se detuvo la captura para evitar pérdida silenciosa.")

    def consume(self, packets, rx_ns):
        samples = []
        for packet in packets:
            sample = self.processor.update(packet, rx_ns)
            self.received += 1
            if sample:
                samples.append(sample)
        if samples:
            self.send("samples", samples)

    def run(self):
        try:
            if self.demo:
                self.run_demo()
            else:
                self.run_serial()
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.stop_event.set()

    def run_demo(self):
        origin = time.perf_counter_ns()
        index = 0
        while not self.stop_event.wait(.01):
            now = time.perf_counter_ns()
            end = int((now - origin) / 1e9 * FS)
            self.consume([demo_packet(i, self.channels) for i in range(index, end)], now)
            index = end

    def run_serial(self):
        import serial
        with serial.Serial(self.port, BAUD, timeout=.05, write_timeout=.5) as connection:
            # R4 native USB does not necessarily reset when opened; always stop first.
            connection.write(b"S")
            if self.stop_event.wait(1.5):
                return
            connection.reset_input_buffer()
            connection.write(str(self.channels).encode("ascii"))
            last_packet = time.monotonic()
            try:
                while not self.stop_event.is_set():
                    raw = connection.read(min(4096, connection.in_waiting or 1))
                    rx_ns = time.perf_counter_ns()
                    packets = self.parser.feed(raw)
                    if packets:
                        last_packet = time.monotonic()
                        self.consume(packets, rx_ns)
                    if time.monotonic() - last_packet > 4:
                        raise RuntimeError("No llegan paquetes válidos. Carga arduino/emg_capture/emg_capture.ino, verifica el puerto y cierra Chords/Monitor Serie.")
            finally:
                try:
                    connection.write(b"S")
                except serial.SerialException:
                    pass


class TaggedInbox:
    def __init__(self, inbox, channel):
        self.inbox, self.channel = inbox, channel

    def put_nowait(self, item):
        kind, payload = item
        self.inbox.put_nowait(("channel_samples", (self.channel, payload)))


class MultiSource:
    """Two independent single-channel clocks, each calibrated against the PC."""
    def __init__(self, inbox, ports, notch):
        self.sources = [Source(TaggedInbox(inbox, i), False, port, 1, notch)
                        for i, port in enumerate(ports)]
        self.demo, self.channels, self.port = False, 2, ", ".join(ports)
        self.processor, self.parser = self.sources[0].processor, self.sources[0].parser
        self.stop_event = self

    def set(self):
        for source in self.sources:
            source.stop_event.set()

    def start(self):
        for source in self.sources:
            source.start()

    def join(self, timeout=None):
        for source in self.sources:
            source.join(timeout=timeout)

    def is_alive(self):
        return all(source.is_alive() for source in self.sources)

    def any_alive(self):
        return any(source.is_alive() for source in self.sources)

    @property
    def error(self):
        return next((f"{s.port}: {s.error}" for s in self.sources if s.error), None)

    def details(self):
        return [{"port": s.port, "offset_ns": s.processor.offset_ns,
                 "lost": s.processor.clock.lost, "bad_frames": s.parser.bad_frames}
                for s in self.sources]


def make_figure(channels, names, mouse_events=False, y_limits=(-2000, 2000)):
    from matplotlib.figure import Figure
    figure = Figure(figsize=(11, 6.4), dpi=100, facecolor="#f5f7fa")
    axes = figure.subplots(channels + int(mouse_events), 1, sharex=True, squeeze=False).ravel()
    figure.subplots_adjust(left=.09, right=.90, top=.94, bottom=.10, hspace=.45)
    raw_lines, rms_lines = [], []
    for i in range(channels):
        ax = axes[i]
        ax.set_title(names[i], loc="left", fontsize=11, fontweight="bold")
        ax.set_ylabel("Amplitud (ADC)")
        ax.set_ylim(*y_limits)
        ax.set_autoscaley_on(False)
        ax.grid(alpha=.18)
        ax.axhline(0, color="#64748b", lw=.6, alpha=.5)
        raw_lines.append(ax.plot([], [], color="#1872a2", lw=.65, label="EMG filtrada")[0])
        rms_lines.append(ax.plot([], [], color="#df8312", lw=1.4, label="RMS 100 ms")[0])
        ax.legend(loc="upper right", fontsize=8)
    axes[-1].set_xlabel("Tiempo desde Grabar (s)")
    events = {}
    if mouse_events:
        event_ax = axes[-1]
        event_ax.set_title("Clics registrados · ● pulsar   × soltar", loc="left", fontsize=11)
        event_ax.set_yticks([0, 1], ["Derecho", "Izquierdo"])
        event_ax.set_ylim(-.6, 1.6)
        event_ax.grid(alpha=.18)
        for button, color in (("left", "#168a73"), ("right", "#9b58b5")):
            for pressed in (True, False):
                events[button, pressed] = event_ax.plot([], [], linestyle="none",
                    marker="o" if pressed else "x", markersize=6, color=color)[0]
    return figure, axes, raw_lines, rms_lines, events


def draw_data(plots, samples, events, origin_ns, end_s, window=8, preview=False):
    import numpy as np
    figure, axes, raw_lines, rms_lines, event_lines = plots
    start_s = -window if preview else max(0, end_s - window)
    selected = [s for s in samples if start_s <= (s.estimated_ns - origin_ns) / 1e9 <= end_s]
    if selected:
        times = np.array([(s.estimated_ns - origin_ns) / 1e9 for s in selected])
        for i, line in enumerate(raw_lines):
            values = np.array([s.filtered[i] for s in selected])
            # Min/max per display bin preserves short bursts instead of aliasing by stride.
            stride = max(1, len(values) // 1200)
            indices = []
            for begin in range(0, len(values), stride):
                part = values[begin:begin + stride]
                indices.extend(sorted({begin + int(part.argmin()), begin + int(part.argmax())}))
            # Explicit NaN at every gap prevents interpolating across missing samples.
            indices = sorted(set(indices) | {j for j, s in enumerate(selected) if s.gap})
            y = values[indices].copy()
            for k, j in enumerate(indices):
                if selected[j].gap:
                    y[k] = float("nan")
            line.set_data(times[indices], y)
            rms = np.array([s.rms[i] for s in selected])
            rms_draw = rms[indices].copy()
            for k, j in enumerate(indices):
                if selected[j].gap:
                    rms_draw[k] = float("nan")
            rms_lines[i].set_data(times[indices], rms_draw)
    else:
        for line in raw_lines + rms_lines:
            line.set_data([], [])
    for (button, pressed), line in event_lines.items():
        xs = [(ns - origin_ns) / 1e9 for ns, b, p in events
              if b == button and p == pressed and start_s <= (ns - origin_ns) / 1e9 <= end_s]
        line.set_data(xs, [1 if button == "left" else 0] * len(xs))
    axes[-1].set_xlim(start_s, 0 if preview else max(window, end_s))


class App:
    def __init__(self, root, args):
        import tkinter as tk
        from tkinter import ttk
        self.root, self.args = root, args
        self.capture_mouse = args.mouse_events
        self.source = self.session = self.listener = None
        self.inbox = queue.Queue(maxsize=3000)
        self.history = deque(maxlen=FS * 12)
        self.second_history = deque(maxlen=FS * 12)
        self.events = deque(maxlen=3000)
        self.origin_ns = 0
        self.recording_end_ns = None
        self.last_session = None
        self.last_plot = 0
        self.click_error = None
        self.y_limits = (-2000.0, 2000.0)
        title = "EMG + clics" if self.capture_mouse else "EMG"
        root.title(f"{title} · UNO R4 Minima")
        root.geometry("1180x850")
        root.minsize(900, 740)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TButton", padding=6)
        style.configure("TLabel", padding=2)
        main = ttk.Frame(root, padding=12)
        main.pack(fill="both", expand=True)
        ttk.Label(main, text=title, font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(main, text="Señal azul · RMS naranja · 2.000 muestras/s/canal · sincronización USB aproximada").pack(anchor="w")
        connection = ttk.Frame(main)
        connection.pack(fill="x", pady=6)
        self.mode = tk.StringVar(value="Demo" if args.demo else "Arduino")
        self.port = tk.StringVar(value=args.port or "")
        self.port2 = tk.StringVar(value="")
        self.two_ports = tk.BooleanVar(value=False)
        self.channels = tk.StringVar(value=str(args.channels))
        self.notch = tk.StringVar(value="50")
        self.controls = []
        for text, variable, choices, width in (
            ("Fuente", self.mode, ["Arduino", "Demo"], 9),
            ("Puerto", self.port, [], 12),
            ("Canales", self.channels, ["1", "2"], 3),
            ("Notch Hz", self.notch, ["0", "50", "60"], 4),
        ):
            ttk.Label(connection, text=text).pack(side="left")
            widget = ttk.Combobox(connection, textvariable=variable, values=choices, width=width,
                                  state="normal" if text == "Puerto" else "readonly")
            widget.pack(side="left", padx=(0, 8))
            self.controls.append(widget)
            if text == "Puerto":
                self.port_box = widget
        self.refresh_btn = ttk.Button(connection, text="Buscar", command=self.refresh_ports)
        self.refresh_btn.pack(side="left")
        self.connect_btn = ttk.Button(connection, text="Conectar", command=self.connect)
        self.connect_btn.pack(side="left", padx=6)
        self.disconnect_btn = ttk.Button(connection, text="Desconectar", command=self.disconnect, state="disabled")
        self.disconnect_btn.pack(side="left")
        ports_row = ttk.Frame(main)
        ports_row.pack(fill="x")
        self.multi_check = ttk.Checkbutton(ports_row, text="Un Arduino por canal", variable=self.two_ports)
        self.multi_check.pack(side="left")
        self.controls.append(self.multi_check)
        ttk.Label(ports_row, text="Puerto canal 2 (A0)").pack(side="left", padx=8)
        self.port2_box = ttk.Combobox(ports_row, textvariable=self.port2, width=12)
        self.port2_box.pack(side="left")
        self.controls.append(self.port2_box)
        labels = ttk.Frame(main)
        labels.pack(fill="x")
        self.name1 = tk.StringVar(value="CH1 · extensores · A0")
        self.name2 = tk.StringVar(value="CH2 · flexores")
        for label, var in (("Canal 1", self.name1), ("Canal 2", self.name2)):
            ttk.Label(labels, text=label).pack(side="left")
            entry = ttk.Entry(labels, textvariable=var, width=30)
            entry.pack(side="left", padx=5)
            self.controls.append(entry)
        self.mapping = tk.StringVar(value="Izq=índice / Der=medio")
        if self.capture_mouse:
            mapping = ttk.Combobox(labels, textvariable=self.mapping,
                values=["Izq=índice / Der=medio", "Izq=medio / Der=índice", "Sin asignar dedos"],
                width=25, state="readonly")
            mapping.pack(side="left", padx=4)
            self.controls.append(mapping)
        recording = ttk.Frame(main)
        recording.pack(fill="x", pady=6)
        self.record_btn = ttk.Button(recording, text="● Grabar  [F8]", command=self.toggle_record, state="disabled")
        self.record_btn.pack(side="left")
        self.label = tk.StringVar(value="reposo")
        ttk.Combobox(recording, textvariable=self.label, values=["reposo", "índice", "medio", "libre"],
                     width=13).pack(side="left", padx=8)
        ttk.Button(recording, text="Marcar bloque [F9]", command=self.mark).pack(side="left")
        ttk.Button(recording, text="Guardar gráfica", command=self.save_plot).pack(side="left", padx=8)
        self.record_status = tk.StringVar(value="Sin grabación")
        ttk.Label(recording, textvariable=self.record_status, foreground="#b52b34").pack(side="left")
        timing = ttk.Frame(main)
        timing.pack(fill="x", pady=(0, 6))
        self.timer = tk.StringVar(value="00:00:00.0")
        ttk.Label(timing, text="Tiempo grabado:").pack(side="left")
        ttk.Label(timing, textvariable=self.timer, font=("Consolas", 18, "bold")).pack(side="left", padx=8)
        ttk.Button(timing, text="Abrir gráfica Plotly", command=self.open_plotly).pack(side="left", padx=8)
        ttk.Button(timing, text="Cómo se captan clics", command=self.open_click_help).pack(side="left", padx=4)
        naming = ttk.Frame(main)
        naming.pack(fill="x", pady=(0, 6))
        ttk.Label(naming, text="Nombre al detener:").pack(side="left")
        self.recording_name = tk.StringVar()
        self.name_entry = ttk.Entry(naming, textvariable=self.recording_name, width=32, state="disabled")
        self.name_entry.pack(side="left", padx=8)
        self.name_entry.bind("<Return>", lambda event: self.rename_recording())
        self.rename_btn = ttk.Button(naming, text="Guardar nombre", command=self.rename_recording, state="disabled")
        self.rename_btn.pack(side="left")
        scale = ttk.Frame(main)
        scale.pack(fill="x", pady=(0, 6))
        ttk.Label(scale, text="Eje Y manual (ADC) · ambos canales").pack(side="left")
        self.y_min = tk.StringVar(value="-2000")
        self.y_max = tk.StringVar(value="2000")
        for label, variable in (("Mínimo", self.y_min), ("Máximo", self.y_max)):
            ttk.Label(scale, text=label).pack(side="left", padx=(8, 0))
            entry = ttk.Entry(scale, textvariable=variable, width=10)
            entry.pack(side="left", padx=4)
            entry.bind("<Return>", lambda event: self.apply_y_limits())
        ttk.Button(scale, text="Aplicar escala", command=self.apply_y_limits).pack(side="left", padx=6)
        ttk.Label(main, text="Azul y naranja usan la misma escala. El RMS resume la amplitud; no es una envolvente de picos.").pack(anchor="w")
        self.plot_frame = ttk.Frame(main)
        self.plot_frame.pack(fill="both", expand=True)
        self.status = tk.StringVar(value=(
            "Selecciona Arduino o Demo. Conecta para visualizar; Grabar guarda los datos y activa el registro global de botones."
            if self.capture_mouse else "Selecciona Arduino o Demo. Conecta para visualizar y pulsa Grabar para guardar la EMG."))
        ttk.Label(main, textvariable=self.status, wraplength=1100).pack(anchor="w", pady=4)
        self.path_status = tk.StringVar(value=f"Carpeta de grabaciones: {args.output}")
        ttk.Label(main, textvariable=self.path_status, wraplength=1100).pack(anchor="w")
        self.build_plot()
        self.refresh_ports()
        root.bind("<F8>", lambda event: self.toggle_record())
        root.bind("<F9>", lambda event: self.mark())
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(30, self.poll)

    def build_plot(self):
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        for child in self.plot_frame.winfo_children():
            child.destroy()
        self.plots = make_figure(int(self.channels.get()), [self.name1.get(), self.name2.get()],
                                 self.capture_mouse, self.y_limits)
        self.canvas = FigureCanvasTkAgg(self.plots[0], master=self.plot_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.canvas.draw()

    def update_plot(self, now_ns):
        if self.session or self.recording_end_ns is not None:
            end_ns = self.recording_end_ns if self.recording_end_ns is not None else now_ns
            elapsed = max(0, (end_ns - self.origin_ns) / 1e9)
            self.timer.set(format_duration(elapsed))
            self.plots[1][-1].set_xlabel("Tiempo desde Grabar (s)")
            self.draw_current(self.origin_ns, elapsed)
        else:
            self.plots[1][-1].set_xlabel("Vista previa · segundos respecto de ahora")
            self.draw_current(now_ns, 0, preview=True)
        self.canvas.draw_idle()

    def draw_current(self, origin, elapsed, preview=False):
        if isinstance(self.source, MultiSource):
            fig, axes, raw, rms, events = self.plots
            draw_data((fig, axes, raw[:1], rms[:1], events), self.history,
                      self.events, origin, elapsed, preview=preview)
            draw_data((fig, axes, raw[1:], rms[1:], {}), self.second_history,
                      [], origin, elapsed, preview=preview)
        else:
            draw_data(self.plots, self.history, self.events, origin, elapsed, preview=preview)

    def rename_recording(self):
        from tkinter import messagebox
        if self.session or self.last_session is None:
            return
        try:
            self.last_session.rename(self.recording_name.get())
            self.recording_name.set(self.last_session.metadata["recording_name"])
        except (ValueError, OSError) as exc:
            messagebox.showerror("Nombre de grabación", str(exc))
        finally:
            self.path_status.set(str(self.last_session.path))

    def apply_y_limits(self):
        from tkinter import messagebox
        try:
            low, high = float(self.y_min.get()), float(self.y_max.get())
            if not (math.isfinite(low) and math.isfinite(high) and low < high
                    and math.isfinite(high - low)):
                raise ValueError
        except ValueError:
            messagebox.showerror("Escala Y", "Introduce números finitos, con el mínimo menor que el máximo.")
            return
        self.y_limits = (low, high)
        for line in self.plots[2]:
            line.axes.set_ylim(low, high)
            line.axes.set_autoscaley_on(False)
        self.canvas.draw_idle()

    def refresh_ports(self):
        from serial.tools import list_ports
        ports = [p.device for p in list_ports.comports()]
        self.port_box["values"] = ports
        self.port2_box["values"] = ports
        if not self.port.get() and ports:
            self.port.set(ports[0])
        if not self.port2.get() and len(ports) > 1:
            self.port2.set(ports[1])

    def connect(self):
        from tkinter import messagebox
        if self.source:
            return
        demo = self.mode.get() == "Demo"
        multi = self.two_ports.get()
        if multi and (demo or self.channels.get() != "2"):
            messagebox.showinfo("Dos puertos", "Selecciona Arduino y 2 canales para usar un Arduino por canal.")
            return
        if multi and (not self.port2.get().strip() or self.port.get().strip().lower() == self.port2.get().strip().lower()):
            messagebox.showinfo("Dos puertos", "Selecciona dos puertos diferentes, uno para cada Arduino.")
            return
        if not demo and not self.port.get().strip():
            messagebox.showinfo("Puerto", "Conecta el R4, pulsa Buscar y selecciona su puerto COM. También puedes probar Demo.")
            return
        self.history.clear()
        self.second_history.clear()
        self.events.clear()
        self.inbox = queue.Queue(maxsize=3000)
        self.click_error = None
        self.origin_ns = time.perf_counter_ns()
        self.recording_end_ns = None
        self.timer.set("00:00:00.0")
        self.build_plot()
        self.update_plot(self.origin_ns)
        self.source = (MultiSource(self.inbox, [self.port.get().strip(), self.port2.get().strip()], int(self.notch.get()))
                       if multi else Source(self.inbox, demo, self.port.get().strip(), int(self.channels.get()), int(self.notch.get())))
        self.source.start()
        for widget in self.controls:
            widget.configure(state="disabled")
        self.connect_btn.configure(state="disabled")
        self.refresh_btn.configure(state="disabled")
        self.disconnect_btn.configure(state="normal")
        self.status.set("Conectando y estimando el desfase USB…" if not demo else "DEMO: señales sintéticas. Preparando visualización…")

    def mouse_callback(self, x, y, button, pressed):
        # OS hook thread: never touch Tk, write files, or block here.
        if button.name in ("left", "right"):
            try:
                self.inbox.put_nowait(("click", (time.perf_counter_ns(), button.name, pressed)))
            except queue.Full:
                self.click_error = "Cola llena al recibir un clic. La grabación se detuvo."
                return False

    def toggle_record(self):
        from tkinter import messagebox
        if self.session:
            self.stop_record()
            return
        if not self.source or not self.history or (isinstance(self.source, MultiSource) and not self.second_history):
            return
        start_ns = time.perf_counter_ns()
        mapping = {}
        if self.capture_mouse:
            mapping = {"left": "indice", "right": "medio"}
            if self.mapping.get().startswith("Izq=medio"):
                mapping = {"left": "medio", "right": "indice"}
            if self.mapping.get() == "Sin asignar dedos":
                mapping = {}
        try:
            if self.capture_mouse:
                from pynput import mouse
                self.listener = mouse.Listener(on_click=self.mouse_callback)
                self.listener.start()
                self.listener.wait()
            self.session = Session(self.args.output, {
                "demo": self.source.demo, "port": self.source.port, "channels": self.source.channels,
                "capture_mouse_events": self.capture_mouse,
                "channel_names": [self.name1.get(), self.name2.get()][:self.source.channels],
                "notch_hz": int(self.notch.get()), "highpass_hz": 20, "rms_window_ms": 100,
                "finger_mapping": mapping, "device_pc_offset_ns": self.source.processor.offset_ns,
                "protocol_version": 1, "adc_bits": 14,
                "connection_sequence_losses_at_start": self.source.processor.clock.lost,
                "bad_frames_at_start": self.source.parser.bad_frames,
                "independent_ports": isinstance(self.source, MultiSource),
                "devices_at_start": self.source.details() if isinstance(self.source, MultiSource) else None,
            }, start_ns=start_ns)
            self.session.marker(self.label.get(), start_ns)
            self.origin_ns = self.session.start_ns
            self.recording_end_ns = None
            self.events.clear()
            self.click_error = None
            self.recording_name.set("")
            self.name_entry.configure(state="disabled")
            self.rename_btn.configure(state="disabled")
            self.update_plot(start_ns)
            self.record_btn.configure(text="■ Detener  [F8]")
            self.path_status.set(str(self.session.path))
        except Exception as exc:
            if self.listener:
                self.listener.stop()
                self.listener = None
            if self.session:
                self.session.close(str(exc))
                self.session = None
            messagebox.showerror("No se pudo iniciar", str(exc))

    def drain(self, stop_ns=None):
        # Bounded work per UI turn; acquisition itself continues on another thread.
        for _ in range(3000):
            try:
                kind, payload = self.inbox.get_nowait()
            except queue.Empty:
                break
            if kind == "samples":
                for sample in payload:
                    self.history.append(sample)
                    if self.session and (stop_ns is None or sample.estimated_ns <= stop_ns):
                        self.session.sample(sample)
            elif kind == "channel_samples":
                channel, samples = payload
                history = self.history if channel == 0 else self.second_history
                for sample in samples:
                    history.append(sample)
                    if self.session and (stop_ns is None or sample.estimated_ns <= stop_ns):
                        self.session.sample(sample, channel)
            elif kind == "click":
                ns, button, pressed = payload
                if self.session and ns >= self.session.start_ns and (stop_ns is None or ns <= stop_ns):
                    self.events.append(payload)
                    self.session.click(ns, button, pressed)

    def stop_record(self, error=None):
        if not self.session:
            return
        end_ns = time.perf_counter_ns()
        if self.listener:
            self.listener.stop()
            self.listener.join(timeout=1)
            self.listener = None
        session = self.session
        self.recording_end_ns = end_ns
        try:
            self.drain(end_ns)
        except Exception as exc:
            error = str(exc)
        finally:
            self.session = None
            try:
                if self.source:
                    session.metadata.update(bad_frames_at_end=self.source.parser.bad_frames,
                                            connection_sequence_losses_at_end=self.source.processor.clock.lost)
                    if isinstance(self.source, MultiSource):
                        session.metadata["devices_at_end"] = self.source.details()
                session.close(error, end_ns)
                clicks = f" · {session.clicks} clics" if self.capture_mouse else ""
                self.record_status.set(f"{'Interrumpida' if error else 'Guardada'}: {session.samples} muestras{clicks}")
                self.last_session = session
                self.recording_name.set("")
                self.name_entry.configure(state="normal")
                self.rename_btn.configure(state="normal")
                self.name_entry.focus_set()
            except OSError as exc:
                self.record_status.set(f"Error al cerrar archivos: {exc}")
            self.record_btn.configure(text="● Grabar  [F8]")
            self.path_status.set(str(session.path))
            self.update_plot(end_ns)

    def mark(self):
        if self.session:
            try:
                self.session.marker(self.label.get())
            except OSError as exc:
                self.stop_record(str(exc))

    def poll(self):
        try:
            self.drain()
            if self.session:
                self.timer.set(format_duration((time.perf_counter_ns() - self.session.start_ns) / 1e9))
            if self.source:
                if self.source.error or self.click_error:
                    error = self.source.error or self.click_error
                    self.disconnect(error)
                elif not self.source.is_alive():
                    self.disconnect("La fuente de datos se detuvo.")
                elif self.history and (not isinstance(self.source, MultiSource) or self.second_history):
                    self.record_btn.configure(state="normal")
                    s = self.history[-1]
                    age = (time.perf_counter_ns() - s.rx_ns) / 1e9
                    mode = "DEMO · señal sintética" if self.source.demo else "Arduino R4"
                    self.status.set(f"{mode} · {self.source.channels} canal(es) · pérdidas: {self.source.processor.clock.lost} · "
                                    f"paquetes inválidos: {self.source.parser.bad_frames} · última recepción: {age:.2f} s")
                    if isinstance(self.source, MultiSource):
                        self.status.set(" · ".join(f"{d['port']}: pérdidas {d['lost']}, inválidos {d['bad_frames']}" for d in self.source.details()))
                    if self.session:
                        if self.listener and not self.listener.is_alive():
                            self.disconnect("El registro de clics dejó de funcionar.")
                        else:
                            clicks = f" · {self.session.clicks} clics" if self.capture_mouse else ""
                            self.record_status.set(f"GRABANDO · {self.session.samples} muestras{clicks} · saturadas: {self.session.clipped}")
                    if self.recording_end_ns is None and time.monotonic() - self.last_plot > .10:
                        self.update_plot(time.perf_counter_ns())
                        self.last_plot = time.monotonic()
        except Exception as exc:
            self.disconnect(str(exc))
        self.root.after(30, self.poll)

    def disconnect(self, error=None):
        if self.source:
            self.source.stop_event.set()
            self.source.join(timeout=2)
            if (self.source.any_alive() if isinstance(self.source, MultiSource) else self.source.is_alive()):
                self.status.set("Esperando el cierre del puerto…")
                return
            error = error or self.source.error
        self.stop_record(error)
        self.source = None
        for widget in self.controls:
            kind = widget.winfo_class()
            widget.configure(state="readonly" if kind == "TCombobox" and widget not in (self.port_box, self.port2_box) else "normal")
        self.connect_btn.configure(state="normal")
        self.refresh_btn.configure(state="normal")
        self.disconnect_btn.configure(state="disabled")
        self.record_btn.configure(state="disabled")
        self.status.set(f"Captura detenida: {error}" if error else "Desconectado. Los archivos de las grabaciones se conservaron.")

    def save_plot(self):
        from tkinter import filedialog, messagebox
        filename = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("Imagen PNG", "*.png")])
        if filename:
            try:
                self.plots[0].savefig(filename, dpi=160)
            except OSError as exc:
                messagebox.showerror("Gráfica", str(exc))

    def open_click_help(self):
        import os
        from tkinter import messagebox
        try:
            os.startfile(str(ROOT / "CLICS.md"))
        except OSError as exc:
            messagebox.showerror("Ayuda", str(exc))

    def open_plotly(self):
        import webbrowser
        from tkinter import filedialog, messagebox
        if self.session:
            messagebox.showinfo("Plotly", "Detén la grabación antes de abrir su gráfica completa.")
            return
        directory = self.last_session.path if self.last_session else None
        if directory is None:
            directory = filedialog.askdirectory(title="Selecciona una carpeta de grabación", initialdir=self.args.output)
        if not directory:
            return
        try:
            from plotly_recording import make_recording_plot
            path = Path(directory) / "grafica_plotly.html"
            make_recording_plot(directory, self.y_limits).write_html(str(path), include_plotlyjs=True)
            webbrowser.open(path.resolve().as_uri())
        except (OSError, ValueError, KeyError, ImportError) as exc:
            messagebox.showerror("Plotly", f"No se pudo abrir la gráfica: {exc}")

    def close(self):
        self.disconnect()
        if self.source is None:
            self.root.destroy()


def headless_demo(args):
    import matplotlib
    matplotlib.use("Agg")
    parser = Parser()
    processor = Processor(args.channels, 50, calibration_s=0)
    origin = time.perf_counter_ns()
    session = Session(args.output, {"demo": True, "channels": args.channels,
        "capture_mouse_events": args.mouse_events,
        "notch_hz": 50, "finger_mapping": {"left": "indice", "right": "medio"}}, origin)
    samples, events = [], []
    from core import encode
    try:
        for i in range(int(args.seconds * FS)):
            p = demo_packet(i, args.channels)
            # Exercise framing, checksums, device clock, filters and CSV writer end to end.
            raw = encode(p)
            decoded = parser.feed(raw[:7]) + parser.feed(raw[7:])
            s = processor.update(decoded[0], origin + i * 500000)
            samples.append(s)
            session.sample(s)
            if args.mouse_events and i % (FS * 4) in (FS, FS + FS // 8, FS * 3, FS * 3 + FS // 8):
                button = "left" if i % (FS * 4) < FS * 2 else "right"
                pressed = i % FS == 0
                event = (origin + i * 500000, button, pressed)
                events.append(event)
                session.click(*event)
        plots = make_figure(args.channels, ["DEMO · CH1 extensores", "DEMO · CH2 flexores"], args.mouse_events)
        draw_data(plots, samples, events, origin, args.seconds)
        plots[0].savefig(session.path / "preview.png", dpi=120)
        session.close(end_ns=origin + int(args.seconds * 1e9))
    except Exception as exc:
        session.close(str(exc))
        raise
    clicks = f", {session.clicks} clics sintéticos" if args.mouse_events else " de EMG"
    print(f"Demo verificada: {session.samples} muestras{clicks}.\n{session.path}")


def main():
    parser = argparse.ArgumentParser(description="Visualizar y grabar EMG, Arduino UNO R4 Minima")
    parser.add_argument("--mouse-events", action="store_true",
                        help="Activar opcionalmente el registro de botones del mouse (desactivado por defecto)")
    parser.add_argument("--demo", action="store_true", help="Seleccionar señal sintética")
    parser.add_argument("--port", help="Puerto, por ejemplo COM3")
    parser.add_argument("--channels", type=int, choices=[1, 2], default=2)
    parser.add_argument("--output", type=Path, default=ROOT / "recordings")
    parser.add_argument("--headless", action="store_true", help="Prueba sintética sin ventana ni captura del mouse")
    parser.add_argument("--seconds", type=float, default=6)
    args = parser.parse_args()
    if args.headless:
        if not args.demo or not 0 < args.seconds <= 60:
            parser.error("--headless requiere --demo y --seconds entre 0 y 60")
        headless_demo(args)
    else:
        import tkinter as tk
        root = tk.Tk()
        App(root, args)
        root.mainloop()


if __name__ == "__main__":
    main()
