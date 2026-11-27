"""Acquisition protocol, signal processing and durable session files.

No GUI dependencies: can be exercised with synthetic data and unit tests.
"""
from __future__ import annotations

import binascii
from collections import deque
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import struct
import time

FS = 2000
BAUD = 500000
FRAME = struct.Struct("<2sIIHHHBBH")
MAGIC = b"\xa5\x5a"


@dataclass(frozen=True)
class Packet:
    sequence: int
    micros: int
    ch1: int
    ch2: int
    delta_us: int
    channels: int
    adc_bits: int


def encode(packet: Packet) -> bytes:
    raw = FRAME.pack(MAGIC, packet.sequence, packet.micros, packet.ch1,
                     packet.ch2, packet.delta_us,
                     (packet.adc_bits << 2) | packet.channels, 1, 0)
    return raw[:-2] + struct.pack("<H", binascii.crc_hqx(raw[:-2], 0xFFFF))


class Parser:
    def __init__(self):
        self.buffer = bytearray()
        self.bad_frames = 0
        self.discarded_bytes = 0

    def feed(self, data: bytes) -> list[Packet]:
        self.buffer.extend(data)
        result = []
        while len(self.buffer) >= FRAME.size:
            index = self.buffer.find(MAGIC)
            if index < 0:
                self.discarded_bytes += len(self.buffer) - 1
                del self.buffer[:-1]
                break
            if index:
                self.discarded_bytes += index
                del self.buffer[:index]
            if len(self.buffer) < FRAME.size:
                break
            raw = bytes(self.buffer[:FRAME.size])
            _, seq, us, c1, c2, delta, info, version, crc = FRAME.unpack(raw)
            channels, bits = info & 3, info >> 2
            valid = (version == 1 and channels in (1, 2) and bits == 14
                     and c1 < (1 << bits) and delta < 500
                     and ((channels == 1 and c2 == 65535 and delta == 0)
                          or (channels == 2 and c2 < (1 << bits)))
                     and crc == binascii.crc_hqx(raw[:-2], 0xFFFF))
            if not valid:
                self.bad_frames += 1
                del self.buffer[0]
                continue
            del self.buffer[:FRAME.size]
            result.append(Packet(seq, us, c1, c2, delta, channels, bits))
        return result


class DeviceClock:
    """Unwrap hardware counters, rejecting resets instead of joining timelines."""
    def __init__(self):
        self.last = None
        self.extended_us = 0
        self.lost = 0

    def update(self, packet: Packet) -> tuple[int, int]:
        gap = 0
        if self.last is None:
            self.extended_us = packet.micros
        else:
            step = (packet.sequence - self.last.sequence) & 0xFFFFFFFF
            elapsed = (packet.micros - self.last.micros) & 0xFFFFFFFF
            if step == 0 or step > 2**31 or elapsed == 0 or elapsed > 2**31:
                raise ValueError("El Arduino se reinició o repitió paquetes. Reconecta para otra sesión.")
            self.extended_us += elapsed
            gap = step - 1
            self.lost += gap
        self.last = packet
        return self.extended_us, gap


class ChannelFilter:
    """20 Hz DC blocker, optional notch, trailing 100 ms RMS in ADC counts."""
    def __init__(self, notch: int = 50):
        self.notch = notch
        self.alpha = math.exp(-2 * math.pi * 20 / FS)
        w = 2 * math.pi * notch / FS
        a = math.sin(w) / (2 * 30)
        self.b0 = 1 / (1 + a)
        self.b1 = -2 * math.cos(w) / (1 + a)
        self.b2 = self.b0
        self.a1 = self.b1
        self.a2 = (1 - a) / (1 + a)
        self.reset()

    def reset(self):
        self.previous_x = None
        self.previous_y = self.z1 = self.z2 = self.sum_squares = 0.0
        self.squares = deque()

    def update(self, raw: int) -> tuple[float, float]:
        if self.previous_x is None:
            self.previous_x = raw
        high = self.alpha * (self.previous_y + raw - self.previous_x)
        self.previous_x, self.previous_y = raw, high
        if self.notch:
            value = self.b0 * high + self.z1
            self.z1 = self.b1 * high - self.a1 * value + self.z2
            self.z2 = self.b2 * high - self.a2 * value
        else:
            value = high
        self.squares.append(value * value)
        self.sum_squares += self.squares[-1]
        if len(self.squares) > FS // 10:
            self.sum_squares -= self.squares.popleft()
        rms = math.sqrt(max(0, self.sum_squares) / len(self.squares))
        return value, rms


@dataclass(frozen=True)
class Sample:
    packet: Packet
    device_us: int
    rx_ns: int
    estimated_ns: int
    gap: int
    filtered: tuple[float, ...]
    rms: tuple[float, ...]


class Processor:
    def __init__(self, channels: int, notch: int, calibration_s: float = 1.0):
        self.channels = channels
        self.clock = DeviceClock()
        self.filters = [ChannelFilter(notch) for _ in range(channels)]
        self.calibration_s = calibration_s
        self.first_rx = None
        self.offset_ns = None
        self.ready = False

    def update(self, packet: Packet, rx_ns: int) -> Sample | None:
        if packet.channels != self.channels:
            raise ValueError("El número de canales recibido no coincide con el seleccionado.")
        us, gap = self.clock.update(packet)
        if self.first_rx is None:
            self.first_rx = rx_ns
        if not self.ready:
            candidate = rx_ns - us * 1000
            self.offset_ns = candidate if self.offset_ns is None else min(self.offset_ns, candidate)
            self.ready = (rx_ns - self.first_rx) / 1e9 >= self.calibration_s
        if gap:
            for filt in self.filters:
                filt.reset()
        values = [f.update(x) for f, x in zip(self.filters, (packet.ch1, packet.ch2))]
        if not self.ready:
            return None
        return Sample(packet, us, rx_ns, us * 1000 + self.offset_ns, gap,
                      tuple(v[0] for v in values), tuple(v[1] for v in values))


class Session:
    """Single-threaded writer; all calls are made by the GUI/main consumer."""
    def __init__(self, parent: Path, metadata: dict, start_ns: int | None = None):
        self.start_ns = time.perf_counter_ns() if start_ns is None else start_ns
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.path = parent / ("DEMO_" + stamp if metadata.get("demo") else stamp)
        self.directory_id = self.path.name
        self.path.mkdir(parents=True, exist_ok=False)
        self.capture_mouse = bool(metadata.get("capture_mouse_events", False))
        self.metadata = dict(metadata, started_utc=datetime.now(timezone.utc).isoformat(),
                             start_pc_monotonic_ns=self.start_ns, complete=False,
                             sample_rate_hz=FS, units="ADC counts (not calibrated muscle voltage)",
                             capture_mouse_events=self.capture_mouse,
                             clock_alignment="minimum USB receive offset during first second; fixed thereafter; approximate")
        if self.capture_mouse:
            self.metadata["mouse_timing"] = "OS callback perf_counter_ns; not physical switch contact time"
        else:
            self.metadata.pop("finger_mapping", None)
        self.samples = self.clicks = self.lost = self.clipped = 0
        self.last_flush = time.monotonic()
        self.closed = False
        self.handles = []
        try:
            names = ["emg.csv", "markers.csv"]
            if self.capture_mouse:
                names.append("clicks.csv")
            for name in names:
                self.handles.append((self.path / name).open("w", newline="", encoding="utf-8"))
            self.emg, self.markers = [csv.writer(f) for f in self.handles[:2]]
            self.events = csv.writer(self.handles[2]) if self.capture_mouse else None
            self.emg.writerow(["sequence", "device_us", "ch2_delta_us", "pc_rx_ns", "pc_est_ns",
                               "time_s_est", "ch1_adc", "ch2_adc", "ch1_filtered", "ch2_filtered",
                               "ch1_rms_100ms", "ch2_rms_100ms", "missing_before", "adc_bits"])
            self.channel_writers = [self.emg]
            if metadata.get("independent_ports"):
                handle = (self.path / "emg_ch2.csv").open("w", newline="", encoding="utf-8")
                self.handles.append(handle)
                writer = csv.writer(handle)
                writer.writerow(["sequence", "device_us", "ch2_delta_us", "pc_rx_ns", "pc_est_ns",
                                 "time_s_est", "ch1_adc", "ch2_adc", "ch1_filtered", "ch2_filtered",
                                 "ch1_rms_100ms", "ch2_rms_100ms", "missing_before", "adc_bits"])
                self.channel_writers.append(writer)
            if self.events is not None:
                self.events.writerow(["pc_ns", "time_s", "button", "action", "assigned_finger"])
            self.markers.writerow(["pc_ns", "time_s", "label"])
            self.write_metadata()
        except Exception:
            for handle in self.handles:
                handle.close()
            raise

    def write_metadata(self):
        temporary = self.path / "metadata.tmp"
        temporary.write_text(json.dumps(self.metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path / "metadata.json")

    def rename(self, name: str):
        """Name a closed recording, retaining its unique timestamp and all files."""
        if not self.closed:
            raise ValueError("Detén la grabación antes de nombrarla.")
        name = name.strip()
        reserved = {"CON", "PRN", "AUX", "NUL"} | {f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10)}
        if (not name or len(name) > 80 or name.endswith(".")
                or any(c in '<>:"/\\|?*' or ord(c) < 32 for c in name)
                or name.split(".")[0].upper() in reserved):
            raise ValueError('Usa entre 1 y 80 caracteres, sin <>:"/\\|?*, sin punto final ni nombres reservados de Windows.')
        parent = self.path.parent.resolve()
        target = parent / f"{name}_{self.directory_id}"
        if self.path.resolve().parent != parent or target.resolve().parent != parent:
            raise ValueError("El nombre debe permanecer dentro de la carpeta de grabaciones.")
        if target != self.path.resolve():
            if target.exists():
                raise FileExistsError("Ya existe una grabación con ese nombre. Elige otro.")
            self.path.rename(target)
            self.path = target
        self.metadata["recording_name"] = name
        self.write_metadata()

    def sample(self, s: Sample, channel=0):
        if s.estimated_ns < self.start_ns:
            return
        p = s.packet
        second = p.channels == 2
        self.channel_writers[channel].writerow([p.sequence, s.device_us, p.delta_us, s.rx_ns, s.estimated_ns,
                           f"{(s.estimated_ns-self.start_ns)/1e9:.9f}", p.ch1, p.ch2 if second else "",
                           f"{s.filtered[0]:.6f}", f"{s.filtered[1]:.6f}" if second else "",
                           f"{s.rms[0]:.6f}", f"{s.rms[1]:.6f}" if second else "", s.gap, p.adc_bits])
        self.samples += 1
        self.lost += s.gap
        self.clipped += int(any(x in (0, (1 << p.adc_bits) - 1) for x in
                                ((p.ch1, p.ch2) if second else (p.ch1,))))
        if time.monotonic() - self.last_flush > 1:
            self.flush()

    def click(self, ns: int, button: str, pressed: bool):
        if not self.capture_mouse or ns < self.start_ns:
            return
        finger = self.metadata.get("finger_mapping", {}).get(button, "no_asignado")
        self.events.writerow([ns, f"{(ns-self.start_ns)/1e9:.9f}", button,
                              "press" if pressed else "release", finger])
        self.clicks += int(pressed)

    def marker(self, label: str, ns: int | None = None):
        ns = time.perf_counter_ns() if ns is None else ns
        self.markers.writerow([ns, f"{(ns-self.start_ns)/1e9:.9f}", label])
        self.flush()

    def flush(self):
        for handle in self.handles:
            handle.flush()
        self.last_flush = time.monotonic()

    def close(self, error: str | None = None, end_ns: int | None = None):
        if self.closed:
            return
        self.closed = True
        try:
            self.flush()
        finally:
            for handle in self.handles:
                handle.close()
        end_ns = time.perf_counter_ns() if end_ns is None else end_ns
        self.metadata.update(complete=error is None, error=error, samples=self.samples,
                             missing_samples=self.lost,
                             clipped_samples=self.clipped,
                             end_pc_monotonic_ns=end_ns,
                             duration_s=max(0, (end_ns - self.start_ns) / 1e9))
        if self.capture_mouse:
            self.metadata["click_presses"] = self.clicks
        self.write_metadata()
