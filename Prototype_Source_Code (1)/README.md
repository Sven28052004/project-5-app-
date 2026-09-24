# E-Bus Planning Checker — Prototype

## What this is
A prototype tool to check the feasibility of electric bus plans for Transdev/Hermes
lines 400 & 401 (Eindhoven), implementing exactly the 12 KPIs and 9 feasibility checks
defined in the "KPI and Feasibility Definitions" document (sections 3.2 and 3.3).

This version does not include the plan-optimization feature — it is a checker only.

## Running the app locally

```bash
pip install -r requirements.txt
streamlit run app/streamlit_app.py
```

Then open the URL Streamlit prints (typically http://localhost:8501).

The app defaults to the sample data in `data/` (the files originally provided on
Canvas: Bus_Planning.xlsx, DistanceMatrix.xlsx, Timetable.xlsx). Untick "Use sample
data" in the sidebar to upload your own files instead.

## Project structure

```
app/
  engine.py            Core: data loading, 9 feasibility checks, 12 KPIs
  visuals.py             Plotly Gantt & SOC charts
  streamlit_app.py       The web application
data/                    Sample input files (as provided by the client on Canvas)
requirements.txt         Python dependencies (used by Streamlit Cloud to build the app)
```

## KPIs and feasibility checks implemented

**12 KPIs (section 3.2):** number of buses used, number of service trips, deadhead
ratio, productive time ratio, number of material trips, number of charging sessions,
total service/material/charging/idle hours, lowest SOC reached, number of buses
breaching the safety margin.

**9 feasibility checks (section 3.3):**
1. SOC below safety margin
2. SOC exceeding physical battery capacity
3. Charging session below minimum duration
4. Overlapping activities (a bus cannot be in two places at once)
5. Location mismatch (a bus cannot depart from a location it has not arrived at)
6. Travel time shorter than minimum required
7. Timetable trip not covered by the plan
8. Unmatched service trip (extra trip not in the timetable)
9. Missing or invalid data

## Deploying to a public URL (Streamlit Community Cloud — free)

1. Create a free account at github.com if you don't have one.
2. Create a new repository and upload the contents of this folder: the `app/` folder,
   the `data/` folder, and `requirements.txt` (all in the repository root — do not
   nest them inside an extra folder).
3. Go to https://share.streamlit.io and sign in with your GitHub account.
4. Click "New app", select your repository, and set the main file path to:
   `app/streamlit_app.py`
5. Click "Deploy". Streamlit automatically installs everything listed in
   `requirements.txt` and starts the app.
6. After a minute or two you'll get a public URL like `https://yourapp.streamlit.app`.

### Common issue
If deployment fails with a `ModuleNotFoundError`, double-check that `requirements.txt`
sits in the repository root (same level as the `app/` folder), not inside `app/`.
