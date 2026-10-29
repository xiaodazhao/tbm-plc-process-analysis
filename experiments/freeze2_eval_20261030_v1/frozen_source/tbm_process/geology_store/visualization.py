from __future__ import annotations

import io
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.ticker import FuncFormatter

from .spatial import format_chainage

COLORS = {"TSP": "#477d9c", "HSP": "#b08a42", "SKETCH": "#31856b", "DESIGN": "#806986",
          "PLC": "#555c67", "GAP": "#d7dce0", "UNION": "#5d9176"}


def axis_items(store, lower, upper, as_of=None, source_types=None, plc_day=None, ahead=None):
    query = store.query_geology_by_chainage(lower, upper, as_of=as_of, source_types=source_types)
    rows = []
    for f in query["facts"]:
        kind = f["document_type"]
        selected = (kind in {"TSP", "HSP", "DESIGN"} and f["fact_type"] == "geological_description") or (
            kind == "SKETCH" and f["information_role"] == "observation" and f["fact_type"] == "geological_description")
        if not selected:
            continue
        rows.append({"start_m": f["start_m"], "end_m": f["end_m"], "geometry": f["geometry"], "kind": kind,
                     "fact_id": f["fact_id"], "document_id": f["document_id"], "text": f["original_description"],
                     "label": store.documents[f["document_id"]]["source_file"], "source": f["source"]})
    if plc_day:
        day = store.day(plc_day)
        rows.append({"start_m": min(day["start_m"], day["end_m"]), "end_m": max(day["start_m"], day["end_m"]),
                     "geometry": "interval", "kind": "PLC", "text": plc_day, "label": "PLC当日范围"})
    else:
        bounds = store.metadata.get("plc_audit_range_m")
        if bounds:
            rows.append({"start_m": bounds[0], "end_m": bounds[1], "geometry": "interval", "kind": "PLC",
                         "text": "可靠PLC里程审计包络", "label": "PLC里程审计范围"})
    if ahead:
        rows.append({"start_m": ahead[0], "end_m": ahead[1], "geometry": "interval", "kind": "WINDOW", "text": "前方查询范围", "label": "前方查询范围"})
    return [r for r in rows if (lower <= r["start_m"] <= upper if r["geometry"] == "point"
                               else r["start_m"] < upper and r["end_m"] > lower)]


def plot_axis(store, lower, upper, *, as_of=None, plc_day=None, ahead=None, output=None):
    rows = axis_items(store, lower, upper, as_of=as_of, plc_day=plc_day, ahead=ahead)
    coverage = store.query_geological_coverage(lower, upper, as_of=as_of)
    font_path = Path("/System/Library/Fonts/STHeiti Light.ttc")
    font = FontProperties(fname=str(font_path)) if font_path.is_file() else FontProperties(family="DejaVu Sans")
    fig, ax = plt.subplots(figsize=(12, 4.7), constrained_layout=True)
    tracks = {"TSP": 6, "HSP": 5, "SKETCH": 4, "DESIGN": 3, "PLC": 2, "WINDOW": 1}
    for row in rows:
        lo, hi = row["start_m"], row["end_m"]
        y = tracks[row["kind"]]
        color = COLORS.get(row["kind"], "#8a9cbb")
        if row["geometry"] == "point":
            ax.plot(lo, y, "|", color=color, markersize=12)
        else:
            ax.broken_barh([(max(lo, lower), min(hi, upper)-max(lo, lower))], (y-.19, .38), facecolors=color, alpha=.45, edgecolors="white", linewidth=.7)
    for gap in coverage["gaps"]:
        ax.broken_barh([(gap["start_m"], gap["length_m"])], (-.18, .36), facecolors=COLORS["GAP"])
    for lo, hi in coverage["union_intervals"]:
        ax.broken_barh([(lo, hi-lo)], (-.18, .36), facecolors=COLORS["UNION"])
    ax.set_yticks(range(7), ["预报联合覆盖/空档", "前方窗口", "PLC施工范围", "独立设计资料", "素描明确观察位置", "HSP原分段", "TSP原分段"], fontproperties=font)
    ax.set_xlim(lower, upper)
    ax.set_ylim(-.6, 6.6)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: format_chainage(v)))
    ax.tick_params(axis="x", labelsize=8)
    ax.grid(axis="x", alpha=.2)
    title = "全量历史地质覆盖" if as_of is None else f"截至{as_of}之前可用地质覆盖"
    ax.set_title(title, fontproperties=font, fontsize=12)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    stream = io.BytesIO()
    fig.savefig(stream, format="png", dpi=180)
    plt.close(fig)
    if output:
        Path(output).write_bytes(stream.getvalue())
    return stream.getvalue()


def plotly_axis(store, lower, upper, **kwargs):
    """JSON Plotly specification; optional browser JS does not affect core queries."""
    rows = axis_items(store, lower, upper, **kwargs)
    traces = []
    for kind in ("TSP", "HSP", "SKETCH", "DESIGN", "PLC", "WINDOW"):
        x, y, custom = [], [], []
        for r in rows:
            if r["kind"] != kind:
                continue
            points = [r["start_m"]] if r["geometry"] == "point" else [max(lower, r["start_m"]), min(upper, r["end_m"])]
            for value in points:
                x.append(value); y.append(kind); custom.append(r.get("fact_id"))
            x.append(None); y.append(None); custom.append(None)
        traces.append({"type": "scatter", "mode": "lines+markers", "x": x, "y": y, "customdata": custom,
                       "name": kind, "line": {"width": 12, "color": COLORS.get(kind, "#8296b6")},
                       "marker": {"size": 9, "color": "white", "line": {"width": 1.5, "color": COLORS.get(kind, "#8296b6")}},
                       "hovertemplate": kind+" %{x:.3f} m<extra></extra>"})
    return {"data": traces, "layout": {"height": 340, "margin": {"l": 80, "r": 25, "t": 20, "b": 45},
             "xaxis": {"range": [lower, upper], "tickformat": ".0f", "title": "DyK里程 / m"},
             "yaxis": {"categoryorder": "array", "categoryarray": ["WINDOW", "PLC", "DESIGN", "SKETCH", "HSP", "TSP"],
                       "tickvals": ["WINDOW", "PLC", "DESIGN", "SKETCH", "HSP", "TSP"], "ticktext": ["前方窗口", "PLC", "设计", "素描", "HSP", "TSP"]},
             "showlegend": False, "paper_bgcolor": "white", "plot_bgcolor": "white", "font": {"family": "sans-serif", "size": 12}}}
