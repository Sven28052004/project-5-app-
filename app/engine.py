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
    battery_capacity_kwh: float = 300.0          # Total battery capacity (300kwh)
    soh_assumed: float = 0.90                    # assumed State of Health in % so how much of the battery kwh you can still use
    soc_safety_margin: float = 0.10              # Minimum State of Charge in %
    soc_max_charge_fraction: float = 0.90        # buses not charged above this in daily ops (so 90% of total(300kwh))
    charge_rate_fast_kw: float = 450.0           # charge rate up to 90% SOH
    charge_rate_slow_kw: float = 60.0            # charge rate lowers for the last 10% (so from 90% to 100%)
    min_charging_minutes: float = 15.0           # minimal charging minutes (so 15 minutes)
    idle_power_kw: float = 5.0                   # consumption while doing nothing
    depot_location: str = "ehvgar"               # Location of depot

    @property
    def usable_battery_capacity_kwh(self) -> float:
        """Returns the usable battery capacity after accounting for SOH."""
        return self.battery_capacity_kwh * self.soh_assumed

    @property
    def min_soc_kwh(self) -> float:
        """Calculates the safety margin, in kWh."""
        return self.usable_battery_capacity_kwh * self.soc_safety_margin

    @property
    def max_daily_soc_kwh(self) -> float:
        """The daily kwh you can charge"""
        return self.battery_kwh * self.soc_max_charge_fraction


DEFAULT_CONFIG = Config()


# ----------------------------------------------------------------------------
# Data loading
# ----------------------------------------------------------------------------

def _parse_time_to_minutes(t) -> float:
    """
    Convert a time value to the number of minutes since midnight.

    Accepted formats:
    - String: HH:MM or HH:MM:SS
    - datetime.time
    - datetime
    - pandas.Timestamp
    """
    if pd.isna(t):
        return np.nan
    if isinstance(t, str):
        try:
            parts = t.strip().split(":")                                # remove spaces
            if len(parts) not in {2, 3}:                                # Time must contain hours an minutes, seconds are optional
                raise ValueError
            hours = int(parts[0])                                       # turns time into integer
            minutes = int(parts[1])
            seconds = int(parts[2]) if len(parts) == 3 else 0
            if not 0 <= hours <= 23:                                    # Checks if the amount of hours/minutes/seconds is possible in normal time
                raise ValueError
            if not 0 <= minutes <= 59:
                raise ValueError
            if not 0 <= seconds <= 59:
                raise ValueError
            return hours * 60 + minutes + seconds / 60                  # converts time into minutes
        except ValueError:                                              # Gives error if time is not valid
            raise ValueError(
                f"Invalid time value: {t!r}. "
                "Expected HH:MM or HH:MM:SS."
            )
    if isinstance(t, time):                # Checks whether the value contains a time, but no date.
        return (                           # Convert the time in minutes
            t.hour * 60
            + t.minute
            + t.second / 60
        )
    if isinstance(t, (pd.Timestamp, datetime)):      # Check whether the value contain both a date and a time. 
        return (                                     # ignore the date and convert the time in minutes
            t.hour * 60
            + t.minute
            + t.second / 60
        )
    raise ValueError(                                # raise an error if the data is not a accepted data type
        f"Unrecognised time value: {t!r}"
    )

BUS_PLAN_COLUMNS = {                        # Define the columns that must be present in the bus planning file.
    "start location",
    "end location",
    "start time",
    "end time",
    "activity",
    "line",
    "energy consumption",
    "bus",
}

def load_bus_planning(path: str) -> pd.DataFrame:
    """Load and prepare a bus planning Excel file."""

    df = pd.read_excel(path)                                    # reads the bus plan from the excel file and stores it in a df
    df.columns = [                                              # Clean all column names: treated as text, removes spaces at beginning and end, converts to lowercase.
        str(column).strip().lower()
        for column in df.columns
    ]
    missing_columns = BUS_PLAN_COLUMNS - set(df.columns)        # Find which required columns are missing and removes all columns that are present in df.columns so only the missing columns remain.
    if missing_columns:                                         # Gives an error if there is/are columns missing
        raise ValueError(
            "Bus plan is missing expected columns: "
            f"{sorted(missing_columns)}"
        )
    text_columns = [                                            # list of columns that should be cleaned
        "start location",
        "end location",
        "activity",
    ]
    for column in text_columns:                                 # Cleans the columns seperatly
        df[column] = (
            df[column]
            .astype("string")                                   # Converts all values to a string
            .str.strip()                                        # removes spaces at the beginning/end 
            .str.lower()                                        # Changes uppercase letters to lowercase
        )
    df["energy consumption"] = pd.to_numeric(                   # Convert the energy consumption column to numeric values. Error become NaN
        df["energy consumption"],
        errors="coerce",
    )
    df["bus"] = pd.to_numeric(                                  # Convert the bus column to numeric values. Error become NaN
        df["bus"],
        errors="coerce",
    )
    df["start_min"] = df["start time"].apply(                   # Convert every start time to the number of minutes since midnight
        _parse_time_to_minutes
    )
    df["end_min"] = df["end time"].apply(                       # Convert every end time to the number of minutes since midnight
        _parse_time_to_minutes
    )
    df["end_min_adj"] = df["end_min"]                           # Create separate adjusted end-time column. Can later be corrected for activities after midnight.
    overnight_mask = df["end_min"] < df["start_min"]            # find activities where endtime is smaller than starttime (activity goes through midnight)
    df.loc[overnight_mask, "end_min_adj"] += 24 * 60            # adds 24 hours when endtime goes behond midnight
    df["duration_min"] = (                                      # calculate the duration of every activity in minutes
        df["end_min_adj"] - df["start_min"]
    )
    return (                                                    # Sort bus plan on bus number and as second sorter starttime
        df.sort_values(["bus", "start_min"])
        .reset_index(drop=True)
    )


def load_distance_matrix(path: str) -> pd.DataFrame:
    """
    Load the distance matrix from an Excel file.
     
    The function also cleans the column names so they can be used
    consistently throughout the engine.
    """
    df = pd.read_excel(path)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    return df


def load_timetable(path: str) -> pd.DataFrame:
    """
    Load the timetable from an Excel file and prepare the data.
     
    The function first cleans the column names. Converts departure times to minutes since midnight.
    And lastly sorts the timetable by line and departure time.
    """
    df = pd.read_excel(path)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    df["departure_min"] = df["departure_time"].apply(_parse_time_to_minutes)
    return df.sort_values(["line", "departure_min"]).reset_index(drop=True)


# ----------------------------------------------------------------------------
# Validation issue container
# ----------------------------------------------------------------------------

@dataclass
class Issue:
    severity: str                        # error or warning
    category: str                        # What category for example "data_quality", "feasibility", "coverage"
    check: str                           # which of the 9 feasibility checks this belongs to
    bus: Optional[int]                   # which bus gives the issue
    row_index: Optional[int]             # which row gives the issue
    message: str                         # contains readable explenation of the issue


@dataclass
class ValidationResult:
    """ Store all errors and warnings found during the validation process. """
    issues: list = field(default_factory=list)             # create empty list for all issues we find

    def add(self, severity, category, check, bus, row_index, message):         
        """ Create a new Issue and add it to the list of validation issues. """
        self.issues.append(Issue(severity, category, check, bus, row_index, message))

    @property
    def errors(self):
        """ Return a list containing only validation errors """
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self):
        """ Return a list containing only validation warnings """
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def is_feasible(self):
        """ Return True if the bus plan contains no validation errors """
        return len(self.errors) == 0

    def to_dataframe(self) -> pd.DataFrame:
        """ Convert all validation issues to a pandas DataFrame """
        return pd.DataFrame([{
            "severity": i.severity, "category": i.category, "check": i.check,
            "bus": i.bus, "row": i.row_index, "message": i.message
        } for i in self.issues])

# ----------------------------------------------------------------------------
# Feasibility checks — section 3.3 of the KPI and Feasibility Definitions document
# ----------------------------------------------------------------------------

def check_data_quality(plan: pd.DataFrame, valid_locations: set) -> ValidationResult:
    """Checks the time logic and data quality of the bus plan."""
    vr = ValidationResult()                       # create an empty validationresult object where all errors and warning can be stored
    cfg = DEFAULT_CONFIG                          # Load the default configuration settings.
    for idx, row in plan.iterrows():                                                             # go through every activity in the bus plan
        if pd.isna(row["start_min"]) or pd.isna(row["end_min"]):                                 # check if start or end time are missing
            vr.add("error", "data_quality", "9. Missing or invalid data", row["bus"], idx,          # add an error indicating that activity has missing data
                   "Missing start or end time.")
            continue                                    # skip remaining checks in this row because it can't be checked
        if row["duration_min"] < 0:                 # Checks if duration is negative which means it ends before it starts (even after correcting midnight)
            vr.add("error", "data_quality", "9. Missing or invalid data", row["bus"], idx,
                   f"Negative duration ({row['duration_min']:.1f} min) after rollover correction.")
        if row["start location"] not in valid_locations:            # Checks if the start location is in a known location
            vr.add("error", "data_quality", "9. Missing or invalid data", row["bus"], idx,
                   f"Unknown start location '{row['start location']}'.")
        if row["end location"] not in valid_locations:                # Checks if the end location is in a known location
            vr.add("error", "data_quality", "9. Missing or invalid data", row["bus"], idx,
                   f"Unknown end location '{row['end location']}'.")
        if row["activity"] not in {"service trip", "material trip", "idle", "charging"}:        # Checks if activity is a known activity
            vr.add("error", "data_quality", "9. Missing or invalid data", row["bus"], idx,
                   f"Unknown activity type '{row['activity']}'.")
        if row["activity"] == "service trip" and pd.isna(row["line"]):                    # Geeft een warning als er een service trip is zonder line nummer
            vr.add("warning", "data_quality", "9. Missing or invalid data", row["bus"], idx,
                   "Service trip has no line number.")
        if row["activity"] == "charging" and row["duration_min"] < cfg.min_charging_minutes:       # controleert of de charging duration van een activity 'charging' niet onder de minimale charging duration ligt
            vr.add("error", "feasibility", "3. Charging session below minimum duration",
                   row["bus"], idx,
                   f"Charging session is only {row['duration_min']:.0f} min "
                   f"(c_min = {cfg.min_charging_minutes:.0f} min).")

    for bus, grp in plan.groupby("bus"):                    # Group the activities by bus so that every bus is checked separately
        grp = grp.sort_values("start_min").reset_index()                # Sort the activities of the bus by their start time
        for i in range(len(grp) - 1):                            # Compare every activity with the one after it
            cur, nxt = grp.iloc[i], grp.iloc[i + 1]                    # select the current activity and the next activity
            if cur["end location"] != nxt["start location"]:               #  Checks if the end location of the current activity is the start location of the next activity
                vr.add("error", "data_quality",
                       "5. Location mismatch (a bus cannot depart from a location it has not arrived at)",
                       bus, nxt["index"],
                       f"Location mismatch: bus {bus} ends route at '{cur['end location']}' "
                       f"but next route starts at '{nxt['start location']}'.")
            if nxt["start_min"] < cur["end_min_adj"] - 1e-6:               # Checks if the end time of the current activity is bigger as the start time of the next activity
                vr.add("error", "data_quality",
                       "4. Overlapping activities (a bus cannot be in two places at once)",
                       bus, nxt["index"],
                       f"Overlapping activities for bus {bus}: route starting at "
                       f"{nxt['start time']} begins before previous route ends.")
    return vr               # returns the Validationresult containing all errors and warnings


def check_travel_time(plan: pd.DataFrame, dmatrix: pd.DataFrame) -> ValidationResult:
    """Checks if travel time is shorter than minimum required (tau_br < tau_min_br)."""
    vr = ValidationResult()                     # create an empty validationresult object where all errors and warning can be stored
    for idx, row in plan.iterrows():                        # go through every line in the bus plan
        if row["activity"] not in {"service trip", "material trip"}:          # Checks if the bus is traveling to another location if not skip this part
            continue
        if row["start location"] == row["end location"]:                      # Checks if the bus is traveling to another location if not skip this part
            continue
        subset = dmatrix[(dmatrix["start"] == row["start location"]) &                        # Search the distance matrix for rows that match both: The start and end location of the activity.
                          (dmatrix["end"] == row["end location"])]
        if row.get("line") is not None and not pd.isna(row.get("line")) and (subset["line"] == row["line"]).any():    # Check if the activity has a line number and if this line number can be found in the distance matrix
            subset = subset[subset["line"] == row["line"]]            # Only keep the distance matrix row for the correct line
        if subset.empty:            # Checks if the distance matrix contains no matching routes
            continue              # already flagged by check_data_quality as unknown location
        min_travel_time = float(subset.iloc[0]["min_travel_time"])                    # Select minimum required travel time
        if row["duration_min"] < min_travel_time - 1e-6:                # Checks if minimum duration is smaller as the minimum required travel time and gives an error if this is the case
            vr.add("error", "feasibility", "6. Travel time shorter than minimum required",
                   row["bus"], idx,
                   f"Scheduled travel time is {row['duration_min']:.1f} min, "
                   f"below the minimum required {min_travel_time:.1f} min "
                   f"({row['start location']} \u2192 {row['end location']}).")
    return vr               # returns the Validationresult containing all errors and warnings


def simulate_soc(plan: pd.DataFrame, config: Config = DEFAULT_CONFIG) -> pd.DataFrame:
    """
    Simulate the battery SOC for every bus.

    Positive energy consumption lowers the SOC.
    Negative energy consumption increases the SOC.
    """
    plan = plan.copy()            # make a copy of the database this prevents the original from being changed
    plan["soc_start_kwh"] = np.nan            # Create a new column for the SOC at the start of each activity
    plan["soc_end_kwh"] = np.nan              # Create a new column for the SOC at the end of each activity, they will both be empty because we haven't calculated the SOC yet
    bus_groups = plan.groupby("bus").groups             # Groups de row index by bus numbers
    for bus, indexes in bus_groups.items():                # Go through every bus and the indexes of its activities
        sorted_indexes = sorted(indexes, key=lambda index: plan.loc[index, "start_min"],)            # sort the activity indexes by their starting time 
        soc = config.max_daily_soc_kwh                # assuming that every bus starts with the maximum SOC
        for index in sorted_indexes:                # go through all the activities of the current bus
            plan.at[index, "soc_start_kwh"] = soc                # store the current SOC as the SOC at the start of the activity 
            energy = plan.at[index, "energy consumption"]            # get the energy consumption of the current activity
            if pd.isna(energy):       # check whether the energy consumption is missing if so the end SOC can't be calculated so skip the rest of this activity
                plan.at[index, "soc_end_kwh"] = np.nan
                continue
            soc = soc - float(energy)            # calculate the new SOC after the activity
            plan.at[index, "soc_end_kwh"] = soc                # store the calculated SOC as the new SOC at the end of the activity
    return plan                # Return the bus plan with the calculated SOC columns

    
def check_soc_feasibility(plan_with_soc: pd.DataFrame, config: Config = DEFAULT_CONFIG,) -> ValidationResult:
    """
    Check whether the SOC stays within the battery limits.
    Checks if SOC is below the safety margin and if SOC is above the usable battery capacity.
    """
    vr = ValidationResult()                 # create an empty validationresult object where all errors and warning can be stored
    for idx, row in plan_with_soc.iterrows():            # go through every row in the bus plan with calculated SOC
        if pd.isna(row["soc_end_kwh"]):                # Checks if the SOC at the end of the trip is missing
            vr.add("error", "data_quality", "9. Missing or invalid data", row["bus"], idx,               # adds a data quality because SOC could not be calculated for this activity
                "SOC could not be calculated because "
                "energy data is missing.",
            )
            continue                # Skip the remaining SOC checks for this row
        if row["soc_end_kwh"] < config.min_soc_kwh - 1e-6:                    # Checks if the SOC at the end of a activity is below the minimum SOC
            vr.add("error", "feasibility", "1. SOC below safety margin", row["bus"], idx,
                (
                    f"SOC drops to {row['soc_end_kwh']:.1f} kWh, "
                    f"below the safety margin of "
                    f"{config.min_soc_kwh:.1f} kWh."
                ),
            )
        if (row["soc_end_kwh"] > config.usable_battery_capacity_kwh + 1e-6):            # Checks that the SOC at the end of a trip is bigger as the SOH and if so gives an error as output
            vr.add("error", "feasibility", "2. SOC exceeding physical battery capacity", row["bus"], idx,
                (
                    f"SOC increases to {row['soc_end_kwh']:.1f} kWh, "
                    f"above the usable battery capacity of "
                    f"{config.usable_battery_capacity_kwh:.1f} kWh."
                ),
            )
    return vr               # returns the Validationresult containing all errors and warnings

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
