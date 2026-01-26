# Sensitivity sweep: explore PV size, battery size, and critical load level
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timedelta

try:
    from caas_jupyter_tools import display_dataframe_to_user
except ImportError:
    def display_dataframe_to_user(title, df):
        """Fallback display helper when caas_jupyter_tools is unavailable."""
        print(title)
        print(f"Data shape: {df.shape}")
        print(df.head(10).to_string(max_rows=10, max_cols=10))

# Base settings
eta_charge = 0.95
eta_discharge = 0.95
soc_initial = 0.6
soc_min = 0.10
soc_max = 0.95
battery_power_kw = 100.0
HOURS = 48

def make_simple_pv_curve(system_capacity_kw, hours, peak_hour=13):
    pv = np.zeros(hours)
    for t in range(hours):
        hour_of_day = t % 24
        distance = (hour_of_day - peak_hour)
        if distance > 12:
            distance -= 24
        if distance < -12:
            distance += 24
        if abs(distance) <= 6:
            factor = np.cos((distance / 6) * (np.pi / 2))
            pv[t] = max(0.0, system_capacity_kw * factor)
        else:
            pv[t] = 0.0
    return pv

def get_load_profile(hours, base_kw=80.0, seed=1):
    rng = np.random.default_rng(seed)
    t = np.arange(hours)
    daily = 1.0 + 0.15 * np.sin(2*np.pi*(t % 24)/24 - np.pi/2)
    noise = 1.0 + 0.02 * rng.standard_normal(size=hours)
    profile = base_kw * daily * noise
    profile = np.clip(profile, 0.0, None)
    return profile

def greedy_dispatch(pv_kw, load_kw,
                    batt_capacity_kwh=200.0,
                    batt_power_kw=100.0,
                    soc0_frac=soc_initial,
                    soc_min_frac=soc_min,
                    soc_max_frac=soc_max,
                    eta_ch=eta_charge,
                    eta_dc=eta_discharge):
    T = len(load_kw)
    p_ch = np.zeros(T)
    p_dc = np.zeros(T)
    unmet = np.zeros(T)
    pv_curtail = np.zeros(T)
    soc = np.zeros(T+1)
    soc[0] = soc0_frac * batt_capacity_kwh

    for t in range(T):
        remaining_load = max(0.0, load_kw[t] - pv_kw[t])
        pv_after_load = max(0.0, pv_kw[t] - load_kw[t])

        if pv_after_load > 0:
            headroom_kwh = (soc_max_frac * batt_capacity_kwh) - soc[t]
            max_p_ch_by_energy = max(0.0, headroom_kwh / max(eta_ch, 1e-6))
            p_ch[t] = min(batt_power_kw, pv_after_load, max_p_ch_by_energy)
            pv_curtail[t] = max(0.0, pv_after_load - p_ch[t])
        else:
            deficit = remaining_load
            energy_above_min = max(0.0, soc[t] - (soc_min_frac * batt_capacity_kwh))
            max_p_dc_by_energy = energy_above_min * eta_dc
            p_dc[t] = min(batt_power_kw, deficit, max_p_dc_by_energy)
            unmet[t] = max(0.0, deficit - p_dc[t])

        soc[t+1] = soc[t] + eta_ch * p_ch[t] - (1.0/eta_dc) * p_dc[t]
        soc[t+1] = min(max(soc[t+1], soc_min_frac * batt_capacity_kwh), soc_max_frac * batt_capacity_kwh)

    total_load_kwh = np.sum(load_kw)
    total_unmet_kwh = np.sum(unmet)
    pct_served = 100.0 * (total_load_kwh - total_unmet_kwh) / total_load_kwh if total_load_kwh > 0 else 0.0
    return pct_served

pv_options = [50, 100, 150]
batt_kwh_options = [200, 400, 800]
load_levels = [80, 60, 50]  # kW (critical-only downsizing scenarios)

rows = []
for base_load in load_levels:
    load_kw = get_load_profile(HOURS, base_kw=base_load)
    for pv_kw in pv_options:
        pv = make_simple_pv_curve(pv_kw, HOURS)
        for batt_kwh in batt_kwh_options:
            pct = greedy_dispatch(pv, load_kw, batt_capacity_kwh=batt_kwh, batt_power_kw=battery_power_kw)
            rows.append({
                "CriticalLoad_kW": base_load,
                "PV_kW": pv_kw,
                "Battery_kWh": batt_kwh,
                "PercentServed_%": round(pct, 1)
            })

df_sweep = pd.DataFrame(rows).sort_values(["CriticalLoad_kW", "PV_kW", "Battery_kWh"])

display_dataframe_to_user("Sensitivity: % Served vs PV/Battery/Load", df_sweep)

csv_path = "/mnt/data/sensitivity_table.csv"
df_sweep.to_csv(csv_path, index=False)
print(f"Saved sensitivity table: {csv_path}")
