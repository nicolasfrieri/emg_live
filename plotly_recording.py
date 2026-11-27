"""Interactive, self-contained Plotly view of an existing recording."""
import csv
import json
from pathlib import Path


def make_recording_plot(directory, y_limits=(-2000, 2000)):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    directory = Path(directory)
    with (directory / "metadata.json").open(encoding="utf-8") as f:
        metadata = json.load(f)
    channels = int(metadata.get("channels", 2))
    clicks_path = directory / "clicks.csv"
    has_clicks = clicks_path.exists()
    names = metadata.get("channel_names") or [f"Canal {i+1}" for i in range(channels)]
    names = (names + [f"Canal {i+1}" for i in range(channels)])[0:channels]
    fig = make_subplots(rows=channels + int(has_clicks), cols=1, shared_xaxes=True,
                        subplot_titles=names + (["Clics registrados"] if has_clicks else []))
    times, filtered, rms = [[] for _ in range(channels)], [[] for _ in range(channels)], [[] for _ in range(channels)]
    independent = metadata.get("independent_ports", False)
    for i in range(channels):
        path = directory / ("emg_ch2.csv" if independent and i == 1 else "emg.csv")
        field = 1 if independent else i + 1
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                t = float(row["time_s_est"])
                if int(row.get("missing_before") or 0):
                    times[i].append(t)
                    filtered[i].append(None)
                    rms[i].append(None)
                times[i].append(t)
                filtered[i].append(float(row[f"ch{field}_filtered"]))
                rms[i].append(float(row[f"ch{field}_rms_100ms"]))
    for i in range(channels):
        for values, name, color in ((filtered[i], "EMG filtrada", "#1872a2"), (rms[i], "RMS 100 ms", "#df8312")):
            fig.add_trace(go.Scattergl(x=times[i], y=values, mode="lines", name=name,
                                       legendgroup=name, showlegend=i == 0,
                                       line=dict(color=color, width=1), connectgaps=False), row=i+1, col=1)
        fig.update_yaxes(title_text="Amplitud (ADC)", range=list(y_limits), row=i+1, col=1)
    if has_clicks:
        with clicks_path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        for button, y, color in (("left", 1, "#168a73"), ("right", 0, "#9b58b5")):
            for action, symbol in (("press", "circle"), ("release", "x")):
                xs = [float(r["time_s"]) for r in rows if r["button"] == button and r["action"] == action]
                fig.add_trace(go.Scatter(x=xs, y=[y]*len(xs), mode="markers", name=f"{button} {action}",
                                         marker=dict(color=color, symbol=symbol, size=8)), row=channels+1, col=1)
        fig.update_yaxes(tickvals=[0, 1], ticktext=["Derecho", "Izquierdo"], range=[-.6, 1.6], row=channels+1, col=1)
    fig.update_xaxes(title_text="Tiempo desde Grabar (s)", row=channels+int(has_clicks), col=1)
    fig.update_layout(title=metadata.get("recording_name", directory.name), height=350*(channels+int(has_clicks)),
                      hovermode="x unified", template="plotly_white")
    return fig
