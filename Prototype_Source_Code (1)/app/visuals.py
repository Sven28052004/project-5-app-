"""Plotly-based visualisations for the bus plan: Gantt chart and SOC-over-time chart."""

import plotly.graph_objects as go
import pandas as pd

ACTIVITY_COLORS = {
    "service trip": "#2563eb",   # blue
    "material trip": "#f59e0b",  # amber
    "charging": "#16a34a",       # green
    "idle": "#9ca3af",           # grey
}


def _minutes_to_label(m):
    h = int(m // 60) % 24
    mi = int(m % 60)
    return f"{h:02d}:{mi:02d}"


def build_gantt(plan: pd.DataFrame, title: str = "Bus plan — Gantt chart") -> go.Figure:
    fig = go.Figure()
    buses = sorted(plan["bus"].unique())
    shown_activities = set()

    for _, row in plan.iterrows():
        color = ACTIVITY_COLORS.get(row["activity"], "#000000")
        legend_name = row["activity"]
        show_legend = legend_name not in shown_activities
        shown_activities.add(legend_name)

        fig.add_trace(go.Bar(
            x=[row["duration_min"]],
            y=[f"Bus {row['bus']}"],
            base=[row["start_min"]],
            orientation="h",
            marker=dict(color=color, line=dict(color="white", width=0.5)),
            name=legend_name,
            legendgroup=legend_name,
            showlegend=show_legend,
            hovertemplate=(
                f"Bus {row['bus']}<br>{row['activity']}<br>"
                f"{row['start location']} → {row['end location']}<br>"
                f"{_minutes_to_label(row['start_min'])} - {_minutes_to_label(row['end_min_adj'])}<br>"
                f"Energy: {row['energy consumption']:.2f} kWh<extra></extra>"
            ),
        ))

    fig.update_layout(
        title=title,
        barmode="stack",
        xaxis_title="Time of day",
        yaxis_title="Bus",
        yaxis=dict(categoryorder="array",
                    categoryarray=[f"Bus {b}" for b in sorted(buses, reverse=True)]),
        height=max(400, 28 * len(buses)),
        xaxis=dict(
            tickmode="array",
            tickvals=list(range(0, 26 * 60, 60)),
            ticktext=[f"{h:02d}:00" for h in range(0, 26)],
        ),
        legend_title="Activity",
        margin=dict(l=80, r=20, t=60, b=40),
    )
    return fig


def build_soc_chart(plan_with_soc: pd.DataFrame, config, bus_filter=None) -> go.Figure:
    fig = go.Figure()
    buses = sorted(plan_with_soc["bus"].unique())
    if bus_filter:
        buses = [b for b in buses if b in bus_filter]

    for bus in buses:
        grp = plan_with_soc[plan_with_soc["bus"] == bus].sort_values("start_min")
        xs, ys = [], []
        for _, row in grp.iterrows():
            xs.append(row["start_min"]); ys.append(row["soc_start_kwh"])
            xs.append(row["end_min_adj"]); ys.append(row["soc_end_kwh"])
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name=f"Bus {bus}"))

    fig.add_hline(y=config.min_soc_kwh, line_dash="dash", line_color="red",
                   annotation_text="SOC_min (safety margin)")
    fig.add_hline(y=config.battery_kwh, line_dash="dot", line_color="grey",
                   annotation_text="SOC_max (usable capacity)")

    fig.update_layout(
        title="State of Charge over the day",
        xaxis_title="Time of day",
        yaxis_title="SOC (kWh)",
        xaxis=dict(
            tickmode="array",
            tickvals=list(range(0, 26 * 60, 60)),
            ticktext=[f"{h:02d}:00" for h in range(0, 26)],
        ),
        height=500,
        margin=dict(l=60, r=20, t=60, b=40),
    )
    return fig
