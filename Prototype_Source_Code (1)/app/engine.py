"""
Core engine for the E-Bus Planning Checker prototype.

KPIs and feasibility checks implemented here follow exactly the definitions in the
"KPI and Feasibility Definitions" document (sections 3.2 and 3.3).

Responsibilities:
- Load & validate input data (timetable, distance matrix, bus plan)
- Simulate State of Charge (SOC) per bus over the day
- Check feasibility (9 checks, section 3.3)
- Compute KPIs (12 KPIs, section 3.2)
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Optional


# ----------------------------------------------------------------------------
# Configuration / parameter assumptions (see "Parameter assumptions" block in the
# KPI and Feasibility Definitions document)
# ----------------------------------------------------------------------------

@dataclass
class Config:
    battery_capacity_kwh: float = 300.0        # nameplate capacity
    soh_assumed: float = 0.90                    # assumed State of Health
    soc_safety_margin: float = 0.10              # SOC_min, fraction of usable capacity
    soc_max_charge_fraction: float = 0.90        # buses not charged above this in daily ops
    charge_rate_fast_kw: float = 450.0           # up to 90% SOH
    charge_rate_slow_kw: float = 60.0            # last 10% (90%-100%)
    min_charging_minutes: float = 15.0           # c_min
    idle_power_kw: float = 5.0                   # consumption while stationary
    depot_location: str = "ehvgar"

    @property
    def battery_kwh(self) -> float:
        """SOC_max: usable capacity at the assumed SOH."""
        return self.battery_capacity_kwh * self.soh_assumed

    @property
    def min_soc_kwh(self) -> float:
        """SOC_min: the safety margin, in kWh."""
        return self.battery_kwh * self.soc_safety_margin

    @property
    def max_daily_soc_kwh(self) -> float:
        return self.battery_kwh * self.soc_max_charge_fraction


DEFAULT_CONFIG = Config()


# ----------------------------------------------------------------------------
# Data loading
# ----------------------------------------------------------------------------

def _parse_time_to_minutes(t) -> float:
    """Parse a time-like value (str 'HH:MM[:SS]', datetime.time, Timestamp) to minutes since 00:00."""
    if pd.isna(t):
        return np.nan
    if isinstance(t, str):
        parts = t.split(":")
        h, m = int(parts[0]), int(parts[1])
        s = int(parts[2]) if len(parts) > 2 else 0
        return h * 60 + m + s / 60
    if isinstance(t, time):
        return t.hour * 60 + t.minute + t.second / 60
    if isinstance(t, pd.Timestamp) or isinstance(t, datetime):
        return t.hour * 60 + t.minute + t.second / 60
    raise ValueError(f"Unrecognised time value: {t!r}")


def load_bus_planning(path: str) -> pd.DataFrame:
    df = pd.read_excel(path)
    df.columns = [c.strip().lower() for c in df.columns]
    expected = {"start location", "end location", "start time", "end time",
                "activity", "line", "energy consumption", "bus"}
    missing = expected - set(df.columns)
    if missing:
        raise ValueError(f"Bus plan is missing expected columns: {missing}")
    df["start_min"] = df["start time"].apply(_parse_time_to_minutes)
    df["end_min"] = df["end time"].apply(_parse_time_to_minutes)
    df["end_min_adj"] = df["end_min"]
    df.loc[df["end_min"] < df["start_min"], "end_min_adj"] += 24 * 60
    df["duration_min"] = df["end_min_adj"] - df["start_min"]
    return df.sort_values(["bus", "start_min"]).reset_index(drop=True)


def load_distance_matrix(path: str) -> pd.DataFrame:
    df = pd.read_excel(path)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    return df


def load_timetable(path: str) -> pd.DataFrame:
    df = pd.read_excel(path)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    df["departure_min"] = df["departure_time"].apply(_parse_time_to_minutes)
    return df.sort_values(["line", "departure_min"]).reset_index(drop=True)


# ----------------------------------------------------------------------------
# Validation issue container
# ----------------------------------------------------------------------------

@dataclass
class Issue:
    severity: str      # "error" | "warning"
    category: str       # e.g. "data_quality", "feasibility", "coverage"
    check: str           # which of the 9 feasibility checks (section 3.3) this belongs to
    bus: Optional[int]
    row_index: Optional[int]
    message: str


@dataclass
class ValidationResult:
    issues: list = field(default_factory=list)

    def add(self, severity, category, check, bus, row_index, message):
        self.issues.append(Issue(severity, category, check, bus, row_index, message))

    @property
    def errors(self):
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self):
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def is_feasible(self):
        return len(self.errors) == 0

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame([{
            "severity": i.severity, "category": i.category, "check": i.check,
            "bus": i.bus, "row": i.row_index, "message": i.message
        } for i in self.issues])


# ----------------------------------------------------------------------------
# Feasibility checks — section 3.3 of the KPI and Feasibility Definitions document
# ----------------------------------------------------------------------------

def check_data_quality(plan: pd.DataFrame, valid_locations: set) -> ValidationResult:
    """Checks 4, 5, 6, 9 from section 3.3 (time/logic and data quality)."""
    vr = ValidationResult()
    cfg = DEFAULT_CONFIG
    for idx, row in plan.iterrows():
        # Check 9: missing or invalid data
        if pd.isna(row["start_min"]) or pd.isna(row["end_min"]):
            vr.add("error", "data_quality", "9. Missing or invalid data", row["bus"], idx,
                   "Missing start or end time.")
            continue
        if row["duration_min"] < 0:
            vr.add("error", "data_quality", "9. Missing or invalid data", row["bus"], idx,
                   f"Negative duration ({row['duration_min']:.1f} min) after rollover correction.")
        if row["start location"] not in valid_locations:
            vr.add("error", "data_quality", "9. Missing or invalid data", row["bus"], idx,
                   f"Unknown start location '{row['start location']}'.")
        if row["end location"] not in valid_locations:
            vr.add("error", "data_quality", "9. Missing or invalid data", row["bus"], idx,
                   f"Unknown end location '{row['end location']}'.")
        if row["activity"] not in {"service trip", "material trip", "idle", "charging"}:
            vr.add("error", "data_quality", "9. Missing or invalid data", row["bus"], idx,
                   f"Unknown activity type '{row['activity']}'.")
        if row["activity"] == "service trip" and pd.isna(row["line"]):
            vr.add("warning", "data_quality", "9. Missing or invalid data", row["bus"], idx,
                   "Service trip has no line number.")
        # Check 3: charging session below minimum duration (c_bi < c_min)
        if row["activity"] == "charging" and row["duration_min"] < cfg.min_charging_minutes:
            vr.add("error", "feasibility", "3. Charging session below minimum duration",
                   row["bus"], idx,
                   f"Charging session is only {row['duration_min']:.0f} min "
                   f"(c_min = {cfg.min_charging_minutes:.0f} min).")

    # Check 4: overlapping activities (a bus cannot be in two places at once)
    # Check 5: location mismatch (a bus cannot depart from a location it has not arrived at)
    for bus, grp in plan.groupby("bus"):
        grp = grp.sort_values("start_min").reset_index()
        for i in range(len(grp) - 1):
            cur, nxt = grp.iloc[i], grp.iloc[i + 1]
            if cur["end location"] != nxt["start location"]:
                vr.add("error", "data_quality",
                       "5. Location mismatch (a bus cannot depart from a location it has not arrived at)",
                       bus, nxt["index"],
                       f"Location mismatch: bus {bus} ends route at '{cur['end location']}' "
                       f"but next route starts at '{nxt['start location']}'.")
            if nxt["start_min"] < cur["end_min_adj"] - 1e-6:
                vr.add("error", "data_quality",
                       "4. Overlapping activities (a bus cannot be in two places at once)",
                       bus, nxt["index"],
                       f"Overlapping activities for bus {bus}: route starting at "
                       f"{nxt['start time']} begins before previous route ends.")
    return vr


def check_travel_time(plan: pd.DataFrame, dmatrix: pd.DataFrame) -> ValidationResult:
    """Check 6: travel time shorter than minimum required (tau_br < tau_min_br)."""
    vr = ValidationResult()
    for idx, row in plan.iterrows():
        if row["activity"] not in {"service trip", "material trip"}:
            continue
        if row["start location"] == row["end location"]:
            continue
        subset = dmatrix[(dmatrix["start"] == row["start location"]) &
                          (dmatrix["end"] == row["end location"])]
        if row.get("line") is not None and not pd.isna(row.get("line")) and (subset["line"] == row["line"]).any():
            subset = subset[subset["line"] == row["line"]]
        if subset.empty:
            continue  # already flagged by check_data_quality as unknown location
        min_travel_time = float(subset.iloc[0]["min_travel_time"])
        if row["duration_min"] < min_travel_time - 1e-6:
            vr.add("error", "feasibility", "6. Travel time shorter than minimum required",
                   row["bus"], idx,
                   f"Scheduled travel time is {row['duration_min']:.1f} min, "
                   f"below the minimum required {min_travel_time:.1f} min "
                   f"({row['start location']} \u2192 {row['end location']}).")
    return vr


def simulate_soc(plan: pd.DataFrame, config: Config = DEFAULT_CONFIG) -> pd.DataFrame:
    """Simulates SOC_br for each bus b and route r, in chronological order.
    Energy consumption is signed: positive = discharge, negative = charge."""
    plan = plan.copy()
    plan["soc_start_kwh"] = np.nan
    plan["soc_end_kwh"] = np.nan

    for bus, idx in plan.groupby("bus").groups.items():
        idx = sorted(idx, key=lambda i: plan.loc[i, "start_min"])
        soc = config.max_daily_soc_kwh
        for i in idx:
            plan.at[i, "soc_start_kwh"] = soc
            delta = plan.at[i, "energy consumption"]
            soc = soc - delta
            soc = min(soc, config.battery_kwh)
            plan.at[i, "soc_end_kwh"] = soc
    return plan


def check_soc_feasibility(plan_with_soc: pd.DataFrame, config: Config = DEFAULT_CONFIG) -> ValidationResult:
    """Check 1: SOC below safety margin (SOC_br < SOC_min).
    Check 2: SOC exceeding physical battery capacity (SOC_br > SOC_max)."""
    vr = ValidationResult()
    for idx, row in plan_with_soc.iterrows():
        if row["soc_end_kwh"] < config.min_soc_kwh - 1e-6:
            vr.add("error", "feasibility", "1. SOC below safety margin", row["bus"], idx,
                   f"SOC drops to {row['soc_end_kwh']:.1f} kWh, below the safety margin "
                   f"SOC_min = {config.min_soc_kwh:.1f} kWh ({config.soc_safety_margin:.0%} of usable capacity).")
        if row["soc_end_kwh"] > config.battery_kwh + 1e-6:
            vr.add("error", "feasibility", "2. SOC exceeding physical battery capacity", row["bus"], idx,
                   f"SOC exceeds the physical battery capacity SOC_max = {config.battery_kwh:.1f} kWh.")
    return vr


def check_timetable_coverage(plan: pd.DataFrame, timetable: pd.DataFrame,
                              tolerance_min: float = 1.0) -> ValidationResult:
    """Check 7: timetable trip not covered by the plan (T \\ P != empty).
    Check 8: unmatched service trip, extra trip not in the timetable (P \\ T != empty)."""
    vr = ValidationResult()
    service = plan[plan["activity"] == "service trip"].copy()
    matched = set()
    for tidx, trow in timetable.iterrows():
        candidates = service[
            (service["line"] == trow["line"]) &
            (service["start location"] == trow["start"]) &
            (service["end location"] == trow["end"]) &
            (service["start_min"].sub(trow["departure_min"]).abs() <= tolerance_min)
        ]
        if candidates.empty:
            vr.add("error", "coverage", "7. Timetable trip not covered by the plan", None, tidx,
                   f"Timetable trip line {trow['line']} {trow['start']}->{trow['end']} "
                   f"at {trow['departure_time']} is not covered by any bus in the plan.")
        else:
            matched.add(candidates.index[0])
    unmatched_service = set(service.index) - matched
    for i in unmatched_service:
        vr.add("error", "coverage", "8. Unmatched service trip (extra trip not in the timetable)",
               plan.loc[i, "bus"], i,
               "Service trip in bus plan does not match any timetable entry "
               "(check line/location/time).")
    return vr


def run_all_feasibility_checks(plan_with_soc: pd.DataFrame, dmatrix: pd.DataFrame,
                                timetable: pd.DataFrame, valid_locations: set,
                                config: Config = DEFAULT_CONFIG) -> ValidationResult:
    """Runs all 9 feasibility checks from section 3.3 and combines the results."""
    dq = check_data_quality(plan_with_soc, valid_locations)
    tt = check_travel_time(plan_with_soc, dmatrix)
    soc = check_soc_feasibility(plan_with_soc, config)
    cov = check_timetable_coverage(plan_with_soc, timetable)
    return ValidationResult(issues=dq.issues + tt.issues + soc.issues + cov.issues)


# ----------------------------------------------------------------------------
# KPI computation — section 3.2 of the KPI and Feasibility Definitions document
# ----------------------------------------------------------------------------

def compute_kpis(plan_with_soc: pd.DataFrame, config: Config = DEFAULT_CONFIG) -> dict:
    """Computes all 12 KPIs from section 3.2, in the same order and ranking."""
    B = plan_with_soc["bus"].unique()
    n_buses = len(B)  # KPI 1: |B|

    service = plan_with_soc[plan_with_soc["activity"] == "service trip"]      # S
    material = plan_with_soc[plan_with_soc["activity"] == "material trip"]     # M
    charging = plan_with_soc[plan_with_soc["activity"] == "charging"]          # C
    idle = plan_with_soc[plan_with_soc["activity"] == "idle"]

    n_service_trips = len(service)      # KPI 2: |S|
    n_material_trips = len(material)    # KPI 5: |M|
    n_charging_sessions = len(charging)  # KPI 6: |C|

    total_service_min = service["duration_min"].sum()    # KPI 7
    total_material_min = material["duration_min"].sum()  # KPI 8
    total_charging_min = charging["duration_min"].sum()   # KPI 9
    total_idle_min = idle["duration_min"].sum()            # KPI 10
    total_min = plan_with_soc["duration_min"].sum()

    # KPI 3: deadhead ratio
    deadhead_ratio = total_material_min / total_service_min if total_service_min else np.nan
    # KPI 4: productive time ratio
    productive_time_ratio = total_service_min / total_min if total_min else np.nan

    # KPI 11: lowest SOC reached = min_{b,r} SOC_br
    min_soc_overall = plan_with_soc["soc_end_kwh"].min()

    # KPI 12: number of buses breaching the safety margin
    min_soc_per_bus = plan_with_soc.groupby("bus")["soc_end_kwh"].min()
    buses_below_margin = int((min_soc_per_bus < config.min_soc_kwh).sum())

    return {
        "n_buses": n_buses,                              # 1
        "n_service_trips": n_service_trips,               # 2
        "deadhead_ratio": deadhead_ratio,                  # 3
        "productive_time_ratio": productive_time_ratio,    # 4
        "n_material_trips": n_material_trips,               # 5
        "n_charging_sessions": n_charging_sessions,          # 6
        "total_service_hours": total_service_min / 60,        # 7
        "total_material_hours": total_material_min / 60,       # 8
        "total_charging_hours": total_charging_min / 60,        # 9
        "total_idle_hours": total_idle_min / 60,                  # 10
        "min_soc_kwh_overall": min_soc_overall,                    # 11
        "buses_below_margin": buses_below_margin,                   # 12
    }


# ----------------------------------------------------------------------------
# Full pipeline
# ----------------------------------------------------------------------------

def run_full_check(bus_planning_path: str, distance_matrix_path: str, timetable_path: str,
                    config: Config = DEFAULT_CONFIG):
    plan = load_bus_planning(bus_planning_path)
    dmatrix = load_distance_matrix(distance_matrix_path)
    timetable = load_timetable(timetable_path)

    valid_locations = set(dmatrix["start"]) | set(dmatrix["end"]) | {config.depot_location}

    plan_soc = simulate_soc(plan, config)
    all_issues = run_all_feasibility_checks(plan_soc, dmatrix, timetable, valid_locations, config)
    kpis = compute_kpis(plan_soc, config)

    return {
        "plan": plan_soc,
        "distance_matrix": dmatrix,
        "timetable": timetable,
        "validation": all_issues,
        "kpis": kpis,
        "config": config,
    }
