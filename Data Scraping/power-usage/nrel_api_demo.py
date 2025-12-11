import os
import numpy as np
import pandas as pd
import cvxpy as cp
import matplotlib.pyplot as plt
import requests
from datetime import datetime, timedelta
 
# -----------------------------
# Simple user-friendly settings
# -----------------------------
# Location (kept for NREL fetch; not used by the synthetic PV)
LAT = 35.612659
LON = -77.366356
 
# PV (solar) system - nameplate in kW
pv_capacity_kw = 50.0    # try 50 kW for a small community; change as desired
 
# Battery (aggregated second-life EVs)
battery_capacity_kwh = 200.0   # total usable energy storage (kWh)
battery_power_kw = 100.0       # how fast the battery can charge/discharge (kW)
 
# State-of-charge (SoC) settings (fractions)
soc_initial = 0.6   # start at 60% charged
soc_min = 0.10      # keep at least 10% (to protect batteries)
soc_max = 0.95      # never go above 95%
 
# Efficiency (round-trip approximately eta_ch * eta_dc)
eta_charge = 0.95
eta_discharge = 0.95
 
# Simulation length (hours)
HOURS = 48   # simulate 2 days by default
 
# Load (what community needs)
# By default we create a simple "critical load" profile (kW)
default_critical_load_kw = 80.0   # e.g., a small shelter + essential services
 
# CSV load file: if you set this variable to a filename with a column 'load_kw',
# the script will use that instead of the synthetic profile.
LOAD_CSV = None  # e.g. "community_load.csv"
 
# NREL API key (optional)
NREL_API_KEY = os.getenv("NREL_API_KEY", "").strip()
 
# -----------------------------
# Helper functions
# -----------------------------
 
def fetch_pv_from_nrel(api_key, lat, lon, system_capacity_kw, hours_needed):
    """
    Try to fetch hourly PV output from NREL PVWatts (typical-year hourly).
    Returns an array of length 'hours_needed' (kW).
    If the API call fails, raise an exception (so caller can fallback).
    """
    url = "https://developer.nrel.gov/api/solar/pvwatts/v8.json"
    params = {
        "api_key": api_key,
        "lat": lat,
        "lon": lon,
        "system_capacity": system_capacity_kw,
        "tilt": 30,
        "azimuth": 180,
        "losses": 14,
        "timeframe": "hourly"
    }
    print("Requesting PV data from NREL PVWatts...")
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "errors" in data and data["errors"]:
        raise RuntimeError("NREL returned errors: " + "; ".join(data["errors"]))
    ac_watts = np.array(data["outputs"]["ac"], dtype=float)  # watts
    ac_kw = ac_watts / 1000.0
    # some PVWatts responses have 8760 values; take the first needed hours
    if len(ac_kw) < hours_needed:
        raise RuntimeError("NREL data shorter than requested hours")
    return ac_kw[:hours_needed]
 
def make_simple_pv_curve(system_capacity_kw, hours, peak_hour=13):
    """
    Make a simple, deterministic PV curve for learning/demo.
    It's a bell-shaped daylight curve with zero at night.
    peak_hour: hour of day when PV peaks (0-23). We'll align to a repeating 24-h pattern.
    """
    pv = np.zeros(hours)
    for t in range(hours):
        hour_of_day = t % 24
        # simple bell around peak_hour: use cosine window
        distance = (hour_of_day - peak_hour)
        # wrap-around adjustment (so peak near midnight works correctly if needed)
        if distance > 12:
            distance -= 24
        if distance < -12:
            distance += 24
        # daylight window: within +/-6 hours from peak we have production
        if abs(distance) <= 6:
            # scale: at peak => system_capacity_kw; at edges => small positive number
            factor = np.cos((distance / 6) * (np.pi / 2))  # cos from 0..pi/2
            pv[t] = max(0.0, system_capacity_kw * factor)
        else:
            pv[t] = 0.0
    return pv
 
def get_load_profile(hours, csv_path=None, default_kw=default_critical_load_kw):
    """
    Build a simple hourly load profile (kW).
    If csv_path is provided and valid, use that CSV's 'load_kw' column.
    Otherwise we return a gently varying daily pattern around default_kw.
    """
    if csv_path:
        df = pd.read_csv(csv_path)
        if "load_kw" not in df.columns:
            raise RuntimeError("CSV must have a 'load_kw' column")
        if len(df) < hours:
            raise RuntimeError("CSV does not have enough hourly rows")
        return df["load_kw"].values[:hours]
    t = np.arange(hours)
    # make small day/night variation: load slightly higher in day (people awake)
    daily = 1.0 + 0.15 * np.sin(2*np.pi*(t % 24)/24 - np.pi/2)
    rng = np.random.default_rng(1)  # deterministic for reproducibility
    noise = 1.0 + 0.02 * rng.standard_normal(size=hours)
    profile = default_kw * daily * noise
    profile = np.clip(profile, 0.0, None)
    return profile
 
# -----------------------------
# Optimization function
# -----------------------------
def solve_simple_dispatch(pv_power_kw, load_kw,
                          batt_capacity_kwh=battery_capacity_kwh,
                          batt_power_kw=battery_power_kw,
                          soc0_frac=soc_initial,
                          soc_min_frac=soc_min,
                          soc_max_frac=soc_max,
                          eta_ch=eta_charge,
                          eta_dc=eta_discharge):
    """
    Build and solve a convex optimization to minimize total unmet load over the horizon.
    Variables per hour:
     - p_charge >= 0 (kW)
     - p_discharge >= 0 (kW)
     - soc (kWh)
     - unmet (kW)
     - pv_curtail >= 0 (kW)
    Time step is 1 hour, so power (kW) and energy (kWh) are aligned.
    """
    T = len(load_kw)
    assert len(pv_power_kw) >= T, "PV data must be at least as long as load horizon."
 
    # variables
    p_ch = cp.Variable(T, nonneg=True)
    p_dc = cp.Variable(T, nonneg=True)
    soc = cp.Variable(T+1)   # soc[0]..soc[T]
    unmet = cp.Variable(T, nonneg=True)
    pv_curtail = cp.Variable(T, nonneg=True)
 
    cap = batt_capacity_kwh
    p_max = batt_power_kw
 
    constraints = []
    # initial SoC
    constraints.append(soc[0] == soc0_frac * cap)
 
    for t in range(T):
        # battery power limits (we allow charge+discharge but their sum is limited to p_max)
        constraints.append(p_ch[t] + p_dc[t] <= p_max)
 
        # SoC update: soc[t+1] = soc[t] + eta_ch*p_ch - (1/eta_dc)*p_dc
        constraints.append(soc[t+1] == soc[t] + eta_ch * p_ch[t] - (1.0/eta_dc) * p_dc[t])
 
        # SoC bounds
        constraints.append(soc[t+1] >= soc_min_frac * cap)
        constraints.append(soc[t+1] <= soc_max_frac * cap)
 
        # energy balance for serving load:
        # served = pv + p_dc - p_ch - pv_curtail
        # unmet >= load - served
        constraints.append(unmet[t] >= load_kw[t] - (pv_power_kw[t] + p_dc[t] - p_ch[t] - pv_curtail[t]))
        constraints.append(unmet[t] <= load_kw[t])  # can't be more than the load
 
    # ensure we don't fully deplete below minimum at the final time
    constraints.append(soc[T] >= soc_min_frac * cap)
 
    # objective: minimize total unmet energy (kW * 1 h -> kWh)
    # small penalties on curtail and cycling to prefer using PV and conserve battery
    W_unmet = 1000.0
    W_curt = 1.0
    W_cycle = 0.01
 
    objective = cp.Minimize(W_unmet * cp.sum(unmet) + W_curt * cp.sum(pv_curtail) + W_cycle * (cp.sum(p_ch) + cp.sum(p_dc)))
 
    prob = cp.Problem(objective, constraints)
    prob.solve(verbose=False)   
 
    if prob.status not in ["optimal", "optimal_inaccurate"]:
        raise RuntimeError("Solver failed: " + str(prob.status))
 
    return {
        "p_ch": p_ch.value,
        "p_dc": p_dc.value,
        "soc": soc.value,
        "unmet": unmet.value,
        "pv_curtail": pv_curtail.value,
        "objective": prob.value
    }
 
# -----------------------------
# MAIN: build inputs, run solve, print friendly results
# -----------------------------
def main():
    print("Microgrid demo for a community")
    print(f"Simulating {HOURS} hours. PV capacity: {pv_capacity_kw:.1f} kW. Battery: {battery_capacity_kwh:.1f} kWh.\n")
 
    # 1) PV data: try NREL if user provided a key; otherwise use simple built-in curve
    if NREL_API_KEY:
        try:
            pv_kw = fetch_pv_from_nrel(NREL_API_KEY, LAT, LON, pv_capacity_kw, HOURS)
            print("Using PV data from NREL PVWatts (typical-year hours).")
        except Exception as e:
            print("Could not fetch NREL PV data (falling back to simple PV curve).")
            print("Reason:", str(e))
            pv_kw = make_simple_pv_curve(pv_capacity_kw, HOURS)
    else:
        print("No NREL API key found. Using a simple built-in PV curve (good for learning).")
        pv_kw = make_simple_pv_curve(pv_capacity_kw, HOURS)
 
    # 2) Load profile
    load_kw = get_load_profile(HOURS, LOAD_CSV, default_critical_load_kw)
 
    # 3) Solve the dispatch problem
    result = solve_simple_dispatch(pv_kw, load_kw)
 
    # 4) Build a results table and print simple summary
    times = [datetime.now() + timedelta(hours=i) for i in range(HOURS)]
    df = pd.DataFrame({
        "time": times,
        "pv_kw": pv_kw,
        "load_kw": load_kw,
        "battery_charge_kw": result["p_ch"],
        "battery_discharge_kw": result["p_dc"],
        "battery_soc_kwh": result["soc"][:-1],  # soc for hours 0..T-1
        "unmet_kw": result["unmet"],
        "pv_curtail_kw": result["pv_curtail"]
    })
    df = df.set_index("time")
 
    total_load_kwh = df["load_kw"].sum()
    total_unmet_kwh = df["unmet_kw"].sum()
    served_kwh = total_load_kwh - total_unmet_kwh
    pct_served = 100.0 * served_kwh / total_load_kwh if total_load_kwh > 0 else 0.0
 
    final_soc = result["soc"][-1]
 
    # friendly prints
    print("\n=== Summary ===")
    print(f"Total community energy needed over {HOURS} hours: {total_load_kwh:.1f} kWh")
    print(f"Energy served by system: {served_kwh:.1f} kWh")
    print(f"Energy not served (we had to shed): {total_unmet_kwh:.1f} kWh")
    print(f"Percent of energy served: {pct_served:.1f}%")
    print(f"Battery state of charge at the end: {final_soc:.1f} kWh (min allowed: {soc_min*battery_capacity_kwh:.1f} kWh)")
 
    # Save csv
    out_name = "microgrid_results.csv"
    df.to_csv(out_name)
    print(f"\nSaved per-hour results to {out_name} (open in Excel or a text editor).")
 
    # Quick plot of the first 48 hours (or full if shorter)
    to_plot = min(HOURS, 48)
    df_plot = df.iloc[:to_plot]
    plt.figure(figsize=(11,6))
    plt.plot(df_plot.index, df_plot["load_kw"], label="Load (kW)")
    plt.plot(df_plot.index, df_plot["pv_kw"], label="PV available (kW)")
    plt.plot(df_plot.index, df_plot["battery_discharge_kw"], label="Battery discharge (kW)")
    plt.plot(df_plot.index, df_plot["battery_charge_kw"], label="Battery charge (kW)")
    plt.plot(df_plot.index, df_plot["unmet_kw"], '--', label="Unmet (kW)")
    plt.legend()
    plt.xlabel("Time")
    plt.ylabel("kW / kWh")
    plt.title("Microgrid dispatch (first hours shown)")
    plt.tight_layout()
    plt.show()
 
if __name__ == "__main__":
    main()