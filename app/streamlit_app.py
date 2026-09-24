"""
E-Bus Planning Checker — prototype tool
Interface language: English (per assignment requirement)

Implements exactly the 12 KPIs (section 3.2) and 9 feasibility checks (section 3.3)
from the KPI and Feasibility Definitions document.

Run locally with: streamlit run app/streamlit_app.py
"""

import streamlit as st
import pandas as pd
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engine import (
    load_bus_planning, load_distance_matrix, load_timetable,
    simulate_soc, run_all_feasibility_checks, compute_kpis,
    Config, DEFAULT_CONFIG,
)
from app.visuals import build_gantt, build_soc_chart

st.set_page_config(page_title="E-Bus Planning Checker", layout="wide")

st.title("E-Bus Planning Checker")
st.caption("Prototype tool for Transdev / Hermes — bus lines 400 & 401, Eindhoven")

# ----------------------------------------------------------------------------
# Sidebar: inputs and assumptions
# ----------------------------------------------------------------------------
with st.sidebar:
    st.header("1. Input data")
    bp_file = st.file_uploader("Bus plan (.xlsx)", type=["xlsx"], key="bp")
    dm_file = st.file_uploader("Distance matrix (.xlsx)", type=["xlsx"], key="dm")
    tt_file = st.file_uploader("Timetable (.xlsx)", type=["xlsx"], key="tt")

    st.header("2. Parameter")
    battery_capacity = st.number_input("Nameplate battery capacity (kWh)", value=300.0, step=10.0)
    soh_assumed = st.slider("Assumed State of Health (SOH)", 0.80, 1.00, 0.90, 0.01)
    safety_margin = st.slider("Safety margin SOC_min (fraction of usable capacity)", 0.0, 0.30, 0.10, 0.01)
    max_charge_fraction = st.slider("Max daily charge level (fraction of usable capacity)", 0.5, 1.0, 0.90, 0.01)
    charge_fast = st.number_input("Fast charging rate up to 90% (kW)", value=450.0, step=10.0)
    charge_slow = st.number_input("Slow charging rate 90-100% (kW)", value=60.0, step=5.0)
    min_charge_min = st.number_input("Minimum charging session c_min (minutes)", value=15.0, step=1.0)
    idle_kw = st.number_input("Idle power draw (kW)", value=5.0, step=0.5)

    config = Config(
        battery_capacity_kwh=battery_capacity,
        soh_assumed=soh_assumed,
        soc_safety_margin=safety_margin,
        soc_max_charge_fraction=max_charge_fraction,
        charge_rate_fast_kw=charge_fast,
        charge_rate_slow_kw=charge_slow,
        min_charging_minutes=min_charge_min,
        idle_power_kw=idle_kw,
    )

    st.divider()
    st.caption(
        f"SOC_max (usable capacity): **{config.battery_kwh:.1f} kWh**  \n"
        f"SOC_min (safety margin): **{config.min_soc_kwh:.1f} kWh**  \n"
        f"Max daily charge level: **{config.max_daily_soc_kwh:.1f} kWh**"
    )

# ----------------------------------------------------------------------------
# Load data
# ----------------------------------------------------------------------------
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

bp_source = bp_file
dm_source = dm_file
tt_source = tt_file

if not (bp_source and dm_source and tt_source):
    st.info("Upload the bus plan, distance matrix, and timetable in the sidebar to begin.")
    st.stop()

try:
    plan_raw = load_bus_planning(bp_source)
    dmatrix = load_distance_matrix(dm_source)
    timetable = load_timetable(tt_source)
except Exception as e:
    st.error(f"Failed to load input files: {e}")
    st.stop()

valid_locations = set(dmatrix["start"]) | set(dmatrix["end"]) | {config.depot_location}

# ----------------------------------------------------------------------------
# Run checks
# ----------------------------------------------------------------------------
plan_soc = simulate_soc(plan_raw, config)
all_issues_result = run_all_feasibility_checks(plan_soc, dmatrix, timetable, valid_locations, config)
n_errors = len(all_issues_result.errors)
n_warnings = len(all_issues_result.warnings)
kpis = compute_kpis(plan_soc, config)

# ----------------------------------------------------------------------------
# Tabs
# ----------------------------------------------------------------------------
tab_overview, tab_feasibility, tab_gantt, tab_soc, tab_kpi = st.tabs(
    ["Overview", "Feasibility checks", "Gantt chart", "SOC chart", "KPIs"]
)

with tab_overview:
    st.subheader("Feasibility summary")
    col1, col2, col3 = st.columns(3)
    col1.metric("Overall feasible?", "No" if n_errors > 0 else "Yes")
    col2.metric("Feasibility errors", n_errors)
    col3.metric("Warnings", n_warnings)

    st.markdown(
        "This tool checks the uploaded bus plan against the **9 feasibility checks** "
        "(section 3.3) and reports the **12 KPIs** (section 3.2) defined for this project. "
        "See the *Feasibility checks* tab for the full list of violations, or the "
        "*KPIs* tab for the performance summary."
    )

    if n_errors > 0:
        st.error(
            f"The current plan is **not feasible**: {n_errors} violation(s) were found "
            "across the 9 feasibility checks. See the Feasibility checks tab for details."
        )
    else:
        st.success("All 9 feasibility checks pass — no violations found.")

with tab_feasibility:
    st.subheader("Feasibility checks (section 3.3)")
    st.markdown(
        "Every requirement below must be met for the plan to be feasible. "
        "Each row shows which of the 9 checks was violated, on which bus, and why."
    )
    if not all_issues_result.issues:
        st.success("No issues found — all feasibility checks pass.")
    else:
        df_issues = all_issues_result.to_dataframe()
        check_filter = st.multiselect("Filter by feasibility check",
                                       sorted(df_issues["check"].unique()),
                                       default=sorted(df_issues["check"].unique()))
        sev_filter = st.multiselect("Filter by severity", ["error", "warning"],
                                     default=["error", "warning"])
        filtered = df_issues[df_issues["check"].isin(check_filter) &
                              df_issues["severity"].isin(sev_filter)]
        st.dataframe(
            filtered.rename(columns={
                "severity": "Severity", "category": "Category", "check": "Feasibility check",
                "bus": "Bus", "row": "Row", "message": "Message",
            }),
            use_container_width=True, height=500,
        )
        st.caption(f"Showing {len(filtered)} of {len(df_issues)} issues.")

        st.markdown("**Summary per feasibility check**")
        summary = df_issues.groupby("check").size().reset_index(name="Violations")
        summary = summary.rename(columns={"check": "Feasibility check"})
        st.dataframe(summary, use_container_width=True)

with tab_gantt:
    st.subheader("Bus plan — Gantt chart")
    buses_available = sorted(plan_soc["bus"].unique())
    selected_buses = st.multiselect("Filter buses (empty = show all)", buses_available)
    plot_data = plan_soc[plan_soc["bus"].isin(selected_buses)] if selected_buses else plan_soc
    fig = build_gantt(plot_data)
    st.plotly_chart(fig, use_container_width=True)

with tab_soc:
    st.subheader("State of Charge over the day")
    buses_available = sorted(plan_soc["bus"].unique())
    selected_buses_soc = st.multiselect("Filter buses (empty = show all)", buses_available,
                                         key="soc_filter")
    fig2 = build_soc_chart(plan_soc, config, bus_filter=selected_buses_soc or None)
    st.plotly_chart(fig2, use_container_width=True)

with tab_kpi:
    st.subheader("Key Performance Indicators (section 3.2)")

    st.markdown("**Fleet size and trips**")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("1. Number of buses used", kpis["n_buses"])
    c2.metric("2. Number of service trips", kpis["n_service_trips"])
    c3.metric("5. Number of material trips", kpis["n_material_trips"])
    c4.metric("6. Number of charging sessions", kpis["n_charging_sessions"])

    st.markdown("**Ratios**")
    c5, c6 = st.columns(2)
    c5.metric("3. Deadhead ratio", f"{kpis['deadhead_ratio']:.2f}")
    c6.metric("4. Productive time ratio", f"{kpis['productive_time_ratio']:.1%}")

    st.markdown("**Time totals**")
    c7, c8, c9, c10 = st.columns(4)
    c7.metric("7. Total service hours", f"{kpis['total_service_hours']:.1f} h")
    c8.metric("8. Total material hours", f"{kpis['total_material_hours']:.1f} h")
    c9.metric("9. Total charging hours", f"{kpis['total_charging_hours']:.1f} h")
    c10.metric("10. Total idle hours", f"{kpis['total_idle_hours']:.1f} h")

    st.markdown("**Battery safety**")
    c11, c12 = st.columns(2)
    c11.metric("11. Lowest SOC reached", f"{kpis['min_soc_kwh_overall']:.1f} kWh")
    c12.metric("12. Buses breaching safety margin", kpis["buses_below_margin"])

    st.markdown("**KPI definitions**")
    st.markdown(
        "1. **Number of buses used** — min |B|\n"
        "2. **Number of service trips** — max |S|\n"
        "3. **Deadhead ratio** — material-trip hours ÷ service-trip hours (minimize)\n"
        "4. **Productive time ratio** — service-trip hours ÷ total scheduled hours (maximize)\n"
        "5. **Number of material trips** — min |M|\n"
        "6. **Number of charging sessions** — min |C|\n"
        "7. **Total service hours** — maximize\n"
        "8. **Total material hours** — minimize\n"
        "9. **Total charging hours** — minimize\n"
        "10. **Total idle hours** — minimize\n"
        "11. **Lowest SOC reached** — min SOC across all buses and routes\n"
        "12. **Number of buses breaching the safety margin** — minimize (target: 0)"
    )

st.divider()
st.caption("Prototype developed for Project 5 — not for operational use without further validation.")
