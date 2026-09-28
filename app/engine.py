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
    def battery_kwh(self) -> float:
    """Alias for usable_battery_capacity_kwh, kept so older code that
    still refers to battery_kwh keeps working."""
    return self.usable_battery_capacity_kwh
    
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
        return (t.hour * 60 + t.minute + t.second / 60)                   # Convert the time in minutes
    if isinstance(t, (pd.Timestamp, datetime)):      # Check whether the value contain both a date and a time. 
        return (t.hour * 60 + t.minute + t.second / 60)                   # ignore the date and convert the time in minutes
    raise ValueError(f"Unrecognised time value: {t!r}")                   # raise an error if the data is not a accepted data type

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


def check_timetable_coverage(plan: pd.DataFrame, timetable: pd.DataFrame, tolerance_min: float = 1.0,) -> ValidationResult:
    """ Check that timetable trips and service trips match one-to-one. """
    vr = ValidationResult()                 # create an empty validationresult object where all errors and warning can be stored
    service = plan[plan["activity"] == "service trip"].copy()        # Only get the service trips from the complete bus plan
    matched_service_trips = set()            # Create an empty set to store the indexes of service trips that have already been matched to a timetable trip
    for timetable_index, timetable_row in timetable.iterrows():            # Go through every trip in the timetable
        candidates = service[                                            # Find all service trips that could match the current timetable trip it matches when: line number is the same, start and end locations are the same and The difference between start time and timetabledeparture time is within allowed tolerance
            (service["line"] == timetable_row["line"])
            & (service["start location"] == timetable_row["start"])
            & (service["end location"] == timetable_row["end"])
            & (service["start_min"].sub(timetable_row["departure_min"]).abs() <= tolerance_min)
        ]
        candidates = candidates[~candidates.index.isin(matched_service_trips)]        # Remove service trips that have already been matched to another timetable trip

        if candidates.empty:                                # check if no matching service trip is found and give an error if there is non found
            vr.add("error", "coverage", "7. Timetable trip not covered by the plan", None,
                timetable_index,
                (
                    f"Timetable trip on line "
                    f"{timetable_row['line']} from "
                    f"{timetable_row['start']} to "
                    f"{timetable_row['end']} at "
                    f"{timetable_row['departure_time']} "
                    f"is not covered by any bus."
                ),
            )
        else:                        # calculate the value between every candidates start time and the timetable departure time
            time_difference = (
                candidates["start_min"]
                .sub(timetable_row["departure_min"])
                .abs()
            )
            best_match_index = time_difference.idxmin()                # Find the index of the candidate that has the smallest ifference from the timetable departure time.
            matched_service_trips.add(best_match_index)                # mark the selected service trips as matched so it can't be used again
    unmatched_service_trips = (set(service.index) - matched_service_trips)            # Find all service trips that were not matched to a timetable trip
    for index in unmatched_service_trips:                # Go through every unmatched service trip and add an error because the trip exists in the bus plan but not in the timetable
        vr.add("error", "coverage", "8. Unmatched service trip", plan.loc[index, "bus"], index,
            (
                "Service trip in the bus plan does not match "
                "any timetable entry. Check the line, "
                "locations and departure time."
            ),
        )
    return vr               # returns the Validationresult containing all errors and warnings


def run_all_feasibility_checks(plan_with_soc: pd.DataFrame, dmatrix: pd.DataFrame, timetable: pd.DataFrame,
                               valid_locations: set, config: Config = DEFAULT_CONFIG,) -> ValidationResult:
    """
    Run all feasibility checks and combine their issues
    into one ValidationResult object.
    """
    data_quality_result = check_data_quality(plan_with_soc, valid_locations,)                # Check the bus plan for missing, invalid or conflicting data.
    travel_time_result = check_travel_time(plan_with_soc, dmatrix,)                # Check if every service and material trip has enough time to travel between locations
    soc_result = check_soc_feasibility(plan_with_soc, config,)            # Checks if the battery SOC stays between minimum and maximum allowed battery levels
    coverage_result = check_timetable_coverage(plan_with_soc, timetable,)                # Check if the service trips in the bus plan exactly matches the required trips in the timetable 
    all_issues = (data_quality_result.issues + travel_time_result.issues + soc_result.issues + coverage_result.issues)         # Combine the issues from all four results into one list
    return ValidationResult(issues=all_issues)                # Create and return one Validationresult containing all errors and warnings found by the checks above


# ----------------------------------------------------------------------------
# KPI computation — section 3.2 of the KPI and Feasibility Definitions document
# ----------------------------------------------------------------------------

def compute_kpis(plan_with_soc: pd.DataFrame, config: Config = DEFAULT_CONFIG,) -> dict:
    """Compute the 12 defined KPIs."""
    buses = plan_with_soc["bus"].dropna().unique()                # Select all unique busses used in the full bus plan
    number_of_buses = len(buses)                                  # Count all unique busses used in the full bus plan
    
    service = plan_with_soc[plan_with_soc["activity"] == "service trip"]        # Select all service trips from the full bus plan
    material = plan_with_soc[plan_with_soc["activity"] == "material trip"]      # Select all material trips from the full bus plan
    charging = plan_with_soc[plan_with_soc["activity"] == "charging"]           # Select all charging activities from the full bus plan
    idle = plan_with_soc[plan_with_soc["activity"] == "idle"]                   # Select all idle activities from the full bus plan

    number_of_service_trips = len(service)                # Count all service trips from the full bus plan
    number_of_material_trips = len(material)              # Count all material trips from the full bus plan
    number_of_charging_sessions = len(charging)           # Count all charging activities from the full bus plan

    total_service_minutes = service["duration_min"].sum()            # Calculate the full duration of all service trips in minutes
    total_material_minutes = material["duration_min"].sum()          # Calculate the full duration of all material trips in minutes
    total_charging_minutes = charging["duration_min"].sum()          # Calculate the full duration of all charging activities in minutes
    total_idle_minutes = idle["duration_min"].sum()                  # Calculate the full duration of all idle activities in minutes

    total_minutes = plan_with_soc["duration_min"].sum()              # Calculate the full duration of all activities thogether in minutes

    if total_service_minutes > 0:                                    # compares the time spent on service trips with the time spend on material trips
        deadhead_ratio = (total_material_minutes / total_service_minutes)
    else:                            # If there weren't any service trips we cannot calculate the deadhead ratio because dividing with 0 is not possible
        deadhead_ratio = np.nan

    if total_minutes > 0:                                            # Calculates the productive time ratio which compares the service time with the time of all other activities
        productive_time_ratio = (total_service_minutes / total_minutes)
    else:                           # If there weren't any minutes made we cannot calculate the productive time ratio because dividing with 0 is not possible
        productive_time_ratio = np.nan

    minimum_soc_overall = (plan_with_soc["soc_end_kwh"].min())                    # Find the lowest end SOC reached by any bus during any activity in the complete bus plan

    minimum_soc_per_bus = (plan_with_soc.groupby("bus")["soc_end_kwh"].min())           # Find the lowest end SOC reached by each individual bus
    buses_below_margin = int((minimum_soc_per_bus < config.min_soc_kwh).sum())          # Count how many buses drop below the  SOC safety margin

    return {                                        # Return all 12 KPI's in a dictionary
        "n_buses": number_of_buses,
        "n_service_trips": number_of_service_trips,
        "deadhead_ratio": deadhead_ratio,
        "productive_time_ratio": productive_time_ratio,
        "n_material_trips": number_of_material_trips,
        "n_charging_sessions": number_of_charging_sessions,
        "total_service_hours": total_service_minutes / 60,
        "total_material_hours": total_material_minutes / 60,
        "total_charging_hours": total_charging_minutes / 60,
        "total_idle_hours": total_idle_minutes / 60,
        "min_soc_kwh_overall": minimum_soc_overall,
        "buses_below_margin": buses_below_margin,
    }
    
# ----------------------------------------------------------------------------
# Full pipeline
# ----------------------------------------------------------------------------

def run_full_check(bus_planning_path: str, distance_matrix_path: str, timetable_path: str, config: Config = DEFAULT_CONFIG,) -> dict:
    """ Run the complete bus plan validation and KPI calculation process. """
    plan = load_bus_planning(bus_planning_path)                    # Load and prepare the bus planning Excel file.
    dmatrix = load_distance_matrix(distance_matrix_path)           # Load and prepare the distance matrix.
    timetable = load_timetable(timetable_path)                     # Load and prepare the timetable.
    
    valid_locations = (set(dmatrix["start"]) | set(dmatrix["end"]) | {config.depot_location})                # Create a set containing all valid locations.
    
    plan_with_soc = simulate_soc(plan, config,)                    # Simulate the battery State of Charge for every bus.
    validation_result = run_all_feasibility_checks(                # Run all data quality, travel time, SOC and timetable coverage checks
        plan_with_soc,
        dmatrix,
        timetable,
        valid_locations,
        config,
    )
    kpis = compute_kpis(plan_with_soc, config,)                    # Calculate all 12 KPIs using the bus plan

    return {                                                       # Return all loaded data and calculated result in one dictionary.
        "plan": plan_with_soc,
        "distance_matrix": dmatrix,
        "timetable": timetable,
        "validation": validation_result,
        "kpis": kpis,
        "config": config,
    }
