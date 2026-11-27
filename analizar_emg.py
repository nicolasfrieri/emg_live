"""Generate a signal plot and continuous-wavelet scalogram from an EMG session."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pywt


def load_recording(recording: Path, channel: str):
    directory = recording if recording.is_dir() else recording.parent
    csv_path = recording / "emg.csv" if recording.is_dir() else recording
    metadata_path = directory / "metadata.json"
    if not csv_path.is_file():
        raise FileNotFoundError(f"No se encontró el archivo EMG: {csv_path}")
    if not metadata_path.is_file():
        raise FileNotFoundError(f"No se encontró metadata.json: {metadata_path}")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    sample_rate = float(metadata["sample_rate_hz"])
    if not math.isfinite(sample_rate) or sample_rate <= 0:
        raise ValueError("metadata.json debe incluir una frecuencia de muestreo positiva.")

    times, signal, rms, missing = [], [], [], []
    rms_column = channel.replace("_filtered", "_rms_100ms")
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"time_s_est", channel, rms_column, "missing_before"}
        absent = required.difference(reader.fieldnames or ())
        if absent:
            raise ValueError(f"Faltan columnas en emg.csv: {', '.join(sorted(absent))}")
        for line, row in enumerate(reader, start=2):
            try:
                values = (float(row["time_s_est"]), float(row[channel]),
                          float(row[rms_column]), int(row["missing_before"]))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Valor no válido en emg.csv, línea {line}.") from exc
            if not all(math.isfinite(value) for value in values[:3]) or values[3] < 0:
                raise ValueError(f"Valor no finito o pérdida negativa en emg.csv, línea {line}.")
            times.append(values[0])
            signal.append(values[1])
            rms.append(values[2])
            missing.append(values[3])

    if len(times) < 2:
        raise ValueError("La grabación necesita al menos dos muestras.")
    times = np.asarray(times)
    if np.any(np.diff(times) <= 0):
        raise ValueError("time_s_est debe ser estrictamente creciente.")

    clicks_path = directory / "clicks.csv"
    clicks = []
    if clicks_path.is_file():
        with clicks_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if row.get("action") == "press":
                    clicks.append((float(row["time_s"]), row.get("button", "")))

    return (metadata, times, np.asarray(signal), np.asarray(rms),
            np.asarray(missing), clicks, directory)


def compute_scalogram(times, signal, missing, sample_rate, low_hz=20,
                      high_hz=450, voices_per_octave=24, bin_ms=50):
    if not 0 < low_hz < high_hz < sample_rate / 2:
        raise ValueError("Las frecuencias wavelet deben estar entre 0 y Nyquist.")

    wavelet_name = "cmor1.5-1.0"
    wavelet = pywt.ContinuousWavelet(wavelet_name)
    count = int(math.ceil(math.log2(high_hz / low_hz) * voices_per_octave)) + 1
    frequencies = np.geomspace(low_hz, high_hz, count)
    scales = pywt.frequency2scale(wavelet_name, frequencies / sample_rate)
    margin = int(math.ceil(max(scales) * max(abs(wavelet.lower_bound),
                                             abs(wavelet.upper_bound))))
    bin_samples = max(1, int(round(sample_rate * bin_ms / 1000)))
    chunk_samples = max(bin_samples, (int(10 * sample_rate) // bin_samples) * bin_samples)
    split_gap = max(10 / sample_rate, 0.01)
    boundaries = [0]
    for index in range(1, len(signal)):
        if (missing[index] > sample_rate * split_gap
                or times[index] - times[index - 1] > split_gap):
            boundaries.append(index)
    boundaries.append(len(signal))

    runs = []
    for segment_start, segment_end in zip(boundaries, boundaries[1:]):
        segment_times = times[segment_start:segment_end]
        segment = signal[segment_start:segment_end]
        sample_count = int(np.floor(
            (segment_times[-1] - segment_times[0]) * sample_rate)) + 1
        uniform_times = segment_times[0] + np.arange(sample_count) / sample_rate
        segment = np.interp(uniform_times, segment_times, segment)
        if len(segment) <= 2 * margin + bin_samples:
            continue
        powers, bin_times = [], []
        for core_start in range(0, len(segment), chunk_samples):
            core_end = min(len(segment), core_start + chunk_samples)
            context_start = max(0, core_start - margin)
            context_end = min(len(segment), core_end + margin)
            pad_left = max(0, margin - core_start)
            pad_right = max(0, core_end + margin - len(segment))
            extended = segment[context_start:context_end]
            if pad_left or pad_right:
                extended = np.pad(extended, (pad_left, pad_right), mode="reflect")

            coefficients, _ = pywt.cwt(
                extended, scales, wavelet_name, sampling_period=1 / sample_rate,
                method="fft")
            offset = pad_left + core_start - context_start
            core = coefficients[:, offset:offset + core_end - core_start]

            valid_start = max(core_start, margin)
            valid_end = min(core_end, len(segment) - margin)
            first_bin = ((valid_start + bin_samples - 1) // bin_samples) * bin_samples
            last_bin = (valid_end // bin_samples) * bin_samples
            if last_bin <= first_bin:
                continue
            relative_start = first_bin - core_start
            number_of_bins = (last_bin - first_bin) // bin_samples
            binned = core[:, relative_start:relative_start + number_of_bins * bin_samples]
            binned = binned.reshape(len(scales), number_of_bins, bin_samples)
            powers.append(np.mean(np.abs(binned) ** 2, axis=2))
            sample_times = uniform_times[first_bin:first_bin + number_of_bins * bin_samples]
            bin_times.append(sample_times.reshape(number_of_bins, bin_samples).mean(axis=1))

        if powers:
            runs.append((np.concatenate(bin_times), np.concatenate(powers, axis=1)))

    if not runs:
        raise ValueError("No hay tramos continuos suficientemente largos para calcular el wavelet.")
    return frequencies, runs, margin


def _time_edges(centers, default_width):
    if len(centers) == 1:
        return np.array([centers[0] - default_width / 2,
                         centers[0] + default_width / 2])
    edges = np.empty(len(centers) + 1)
    edges[1:-1] = (centers[:-1] + centers[1:]) / 2
    edges[0] = centers[0] - (centers[1] - centers[0]) / 2
    edges[-1] = centers[-1] + (centers[-1] - centers[-2]) / 2
    return edges


def analyze_recording(recording: Path, output: Path, channel="ch1_filtered",
                      low_hz=20, high_hz=450):
    metadata, times, signal, rms, missing, clicks, directory = load_recording(
        recording, channel)
    sample_rate = float(metadata["sample_rate_hz"])
    measured_rate = 1 / float(np.median(np.diff(times)))
    if abs(measured_rate - sample_rate) / sample_rate > 0.01:
        print(f"Advertencia: fs nominal={sample_rate:.2f} Hz, "
              f"fs mediana observada={measured_rate:.2f} Hz.")

    frequencies, runs, margin = compute_scalogram(
        times, signal, missing, sample_rate, low_hz, high_hz)
    peak_power = max(float(np.max(power)) for _, power in runs)

    figure, (signal_ax, wavelet_ax) = plt.subplots(
        2, 1, figsize=(15, 9), sharex=True,
        gridspec_kw={"height_ratios": [1, 1.25]})
    plotted_signal, plotted_rms = signal.copy(), rms.copy()
    breaks = np.flatnonzero(
        (missing[1:] > 0) | (np.diff(times) > 1.5 / sample_rate)) + 1
    plotted_signal[breaks] = np.nan
    plotted_rms[breaks] = np.nan
    stride = max(1, len(signal) // 5000)
    display_indices = []
    for start in range(0, len(signal), stride):
        part = signal[start:start + stride]
        display_indices.extend((start + int(np.argmin(part)), start + int(np.argmax(part))))
    display_indices = np.asarray(sorted(set(display_indices).union(breaks.tolist())))
    signal_ax.plot(times[display_indices], plotted_signal[display_indices],
                   color="#1872a2", lw=0.65, label="EMG filtrada")
    signal_ax.plot(times[display_indices], plotted_rms[display_indices],
                   color="#df8312", lw=1.0, alpha=0.9,
                   label="RMS 100 ms")
    signal_low, signal_high = signal_ax.get_ylim()
    click_y = signal_high - (signal_high - signal_low) * 0.025
    for button, marker, color, label in (
            ("left", "v", "#168a73", "Pulsación izquierda"),
            ("right", "v", "#9b58b5", "Pulsación derecha")):
        click_times = [click_time for click_time, click_button in clicks
                       if click_button == button]
        if click_times:
            signal_ax.scatter(click_times, [click_y] * len(click_times), marker=marker,
                              s=13, color=color, alpha=0.75, label=label, zorder=3)
    signal_ax.set_ylabel("Amplitud (cuentas ADC)")
    signal_ax.set_title(
        f"{metadata.get('recording_name', directory.name)} · {channel} · "
        f"{sample_rate:g} Hz")
    signal_ax.grid(alpha=0.2)
    signal_ax.legend(loc="upper right")

    frequency_edges = np.empty(len(frequencies) + 1)
    frequency_edges[1:-1] = np.sqrt(frequencies[:-1] * frequencies[1:])
    frequency_edges[0] = frequencies[0] ** 2 / frequency_edges[1]
    frequency_edges[-1] = frequencies[-1] ** 2 / frequency_edges[-2]
    for bin_times, power in runs:
        relative_db = 10 * np.log10(np.maximum(power / peak_power, 1e-8))
        wavelet_ax.pcolormesh(
            _time_edges(bin_times, 0.05), frequency_edges, relative_db,
            shading="flat", cmap="magma", vmin=-40, vmax=0, rasterized=True)
    wavelet_ax.set_yscale("log")
    wavelet_ax.set_ylim(low_hz, high_hz)
    wavelet_ax.set_ylabel("Frecuencia (Hz)")
    wavelet_ax.set_xlabel("Tiempo desde el inicio de la grabación (s)")
    wavelet_ax.set_title(
        f"Wavelet continuo · Morlet complejo · potencia relativa · "
        f"ventana de 50 ms · brechas cortas interpoladas · "
        f"bordes omitidos ±{margin / sample_rate:.2f} s")
    wavelet_ax.grid(which="major", alpha=0.15)
    colorbar = figure.colorbar(
        wavelet_ax.collections[-1], ax=wavelet_ax, pad=0.015, label="Potencia relativa (dB)")
    colorbar.set_ticks([-40, -30, -20, -10, 0])
    figure.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=150)
    plt.close(figure)

    print(f"Grabación: {directory}")
    print(f"Muestras: {len(signal)} · duración: {times[-1] - times[0]:.2f} s · "
          f"fs nominal: {sample_rate:g} Hz")
    print(f"Muestras perdidas registradas: {int(missing.sum())} · "
          f"pulsaciones: {len(clicks)} · notch: {metadata.get('notch_hz', 'no indicado')} Hz")
    print(f"Señal analizada: {channel} · frecuencia wavelet: {low_hz:g}–{high_hz:g} Hz")
    print(f"Gráfica guardada: {output}")


def main():
    parser = argparse.ArgumentParser(
        description="Generar gráfica EMG y scalograma wavelet de una grabación guardada.")
    parser.add_argument("recording", type=Path,
                        help="Carpeta de sesión o ruta a emg.csv")
    parser.add_argument("--output", type=Path,
                        help="PNG de salida (por defecto, analysis_wavelet.png en la sesión)")
    parser.add_argument("--channel", choices=["ch1_filtered", "ch2_filtered"],
                        default="ch1_filtered", help="Canal filtrado para el análisis")
    parser.add_argument("--low-hz", type=float, default=20,
                        help="Frecuencia wavelet mínima (Hz)")
    parser.add_argument("--high-hz", type=float, default=450,
                        help="Frecuencia wavelet máxima (Hz)")
    args = parser.parse_args()
    output = args.output or (
        args.recording / "analysis_wavelet.png" if args.recording.is_dir()
        else args.recording.parent / "analysis_wavelet.png")
    analyze_recording(args.recording, output, args.channel, args.low_hz, args.high_hz)


if __name__ == "__main__":
    main()
