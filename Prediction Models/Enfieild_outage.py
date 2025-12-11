import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

from sklearn.ensemble import GradientBoostingRegressor, HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import TimeSeriesSplit, GridSearchCV
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.metrics import r2_score

# =========================================================
# 1. CONFIG
# =========================================================

BASE_DIR = Path(__file__).resolve().parent

DATA_PATH = BASE_DIR / "model_training" / "15_minute_timeseries_data_cleaned.xlsx"
TIME_COL = "Timestamp (EST)"

# Forecast electricity total (column U)
TARGET_COL = "baseline.out.electricity.total.energy_consumption.kwh"

# Where to save the hourly dataset
HOURLY_CSV_PATH = BASE_DIR / "model_training" / "hourly_from_15min_electricity.csv"

# Where to save figures
PLOTS_DIR = BASE_DIR / "charts"
PLOTS_DIR.mkdir(exist_ok=True)


# =========================================================
# 2. METRICS
# =========================================================

def mae(y_true, y_pred):
    return float(np.mean(np.abs(y_true - y_pred)))

def rmse(y_true, y_pred):
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))

def mape(y_true, y_pred, eps=1e-8):
    y_true = np.asarray(y_true, float)
    y_pred = np.asarray(y_pred, float)
    return float(np.mean(np.abs((y_true - y_pred) / (y_true + eps))) * 100.0)

def smape(y_true, y_pred, eps=1e-8):
    y_true = np.asarray(y_true, float)
    y_pred = np.asarray(y_pred, float)
    num = np.abs(y_true - y_pred)
    den = np.abs(y_true) + np.abs(y_pred) + eps
    return float(200.0 * np.mean(num / den))

def cvrmse(y_true, y_pred):
    y_true = np.asarray(y_true, float)
    return float(100.0 * rmse(y_true, y_pred) / (np.mean(y_true) + 1e-8))

def nmbe(y_true, y_pred):
    y_true = np.asarray(y_true, float)
    y_pred = np.asarray(y_pred, float)
    T = y_true.shape[0]
    mean_y = np.mean(y_true) + 1e-8
    bias = np.sum(y_true - y_pred)
    return float(100.0 * bias / (T * mean_y))

def r2(y_true, y_pred):
    return float(r2_score(y_true, y_pred))

def compute_all_metrics(name, y_true, y_pred):
    return {
        "Model":  name,
        "MAE":    mae(y_true, y_pred),
        "RMSE":   rmse(y_true, y_pred),
        "MAPE":   mape(y_true, y_pred),
        "sMAPE":  smape(y_true, y_pred),
        "CVRMSE": cvrmse(y_true, y_pred),
        "NMBE":   nmbe(y_true, y_pred),
        "R2":     r2(y_true, y_pred),
    }


# =========================================================
# 3. BASIC CLEANING & HOURLY AGGREGATION
# =========================================================

def basic_clean(df: pd.DataFrame) -> pd.DataFrame:
    """Light cleaning: parse time, set index, sort, drop dupes, interpolate."""
    df = df.copy()
    df[TIME_COL] = pd.to_datetime(df[TIME_COL])
    df = df.dropna(subset=[TIME_COL, TARGET_COL])
    df = df.set_index(TIME_COL).sort_index()

    # Drop duplicate timestamps (keep first)
    before = len(df)
    df = df[~df.index.duplicated(keep="first")]
    after = len(df)
    if after < before:
        print(f"🧹 Dropped {before - after} duplicate timestamp rows.")

    # Interpolate any numeric gaps
    num_cols = df.select_dtypes(include=[np.number]).columns
    df[num_cols] = df[num_cols].interpolate(method="time", limit_direction="both")

    return df

def aggregate_hourly(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate a 15-minute DatetimeIndex dataframe to hourly.
    Sum energy/consumption columns, mean power columns, forward-fill text.
    """
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("❌ The dataset index must be a DatetimeIndex.")

    hourly_df = pd.DataFrame()

    for col in df.columns:
        col_lower = col.lower()

        if any(keyword in col_lower for keyword in ["kwh", "energy", "consumption"]):
            hourly_df[col] = df[col].resample("H").sum(min_count=1)

        elif any(keyword in col_lower for keyword in ["kw", "power"]):
            hourly_df[col] = df[col].resample("H").mean()

        else:
            # Non-numeric / other: forward fill or mean
            if df[col].dtype == "object":
                hourly_df[col] = df[col].resample("H").ffill()
            else:
                hourly_df[col] = df[col].resample("H").mean()

    # Clean up after aggregation
    hourly_df = hourly_df.interpolate(method="time", limit_direction="both")
    return hourly_df


# =========================================================
# 4. FEATURE ENGINEERING (CALENDAR + LAGS + ROLLING)
# =========================================================

def build_features_from_index(df: pd.DataFrame, target_col: str, granularity: str):
    """
    df must have a DatetimeIndex.
    granularity: '15min' or 'hourly'
    X = calendar features + lag features + rolling means of the target.
    y = target_col series.
    """
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("Index must be a DatetimeIndex")

    # Calendar features
    df["hour"] = df.index.hour
    df["dow"] = df.index.dayofweek          # 0 = Monday
    df["month"] = df.index.month
    df["is_weekend"] = (df["dow"] >= 5).astype(int)

    # Lag + rolling configs by granularity
    if granularity == "15min":
        # 15-minute data
        lags = [1, 2, 4, 96, 96 * 7]  # 15m, 30m, 1h, 1d, 1w
        roll_short = 96               # 1 day
        roll_long = 96 * 7            # 1 week
    elif granularity == "hourly":
        # hourly data
        lags = [1, 2, 24, 24 * 7]     # 1h, 2h, 1d, 1w
        roll_short = 24               # 1 day
        roll_long = 24 * 7            # 1 week
    else:
        raise ValueError("granularity must be '15min' or 'hourly'")

    # Lag features
    for lag in lags:
        df[f"lag_{lag}"] = df[target_col].shift(lag)

    # Rolling means
    df[f"roll_mean_{roll_short}"] = df[target_col].rolling(window=roll_short).mean()
    df[f"roll_mean_{roll_long}"] = df[target_col].rolling(window=roll_long).mean()

    # Drop rows where we don't have full lag/roll history
    df_model = df.dropna().copy()

    feature_cols = (
        ["hour", "dow", "month", "is_weekend"]
        + [f"lag_{lag}" for lag in lags]
        + [f"roll_mean_{roll_short}", f"roll_mean_{roll_long}"]
    )

    X = df_model[feature_cols]
    y = df_model[target_col]
    return X, y


# =========================================================
# 5. TRAIN & EVALUATE MODELS (+ NAIVE BASELINE)
# =========================================================

def train_and_evaluate_all_models(X, y, granularity="15min", label=""):
    """
    Train GradientBoosting, HistGradientBoosting, Ridge with MAE-based optimisation.
    Also compute a naive baseline (yesterday same time).
    Returns:
      - results_df: metrics per model (including Naive)
      - preds: dict of prediction Series
      - (X_train, X_test, y_train, y_test)
    """
    # Time-based train/test split (70% train, 30% test)
    split_idx = int(len(X) * 0.7)
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]

    print(f"\n=== {label} data ===")
    print(f"Train samples: {len(X_train)}, Test samples: {len(X_test)}")

    tscv = TimeSeriesSplit(n_splits=5)

    models = {
        "GradientBoosting": GradientBoostingRegressor(
            random_state=42,
            loss="absolute_error"  # MAE-style loss
        ),
        "HistGradientBoosting": HistGradientBoostingRegressor(
            random_state=42,
            loss="absolute_error"
        ),
        "Ridge": make_pipeline(
            StandardScaler(with_mean=False),
            Ridge(random_state=42)
        ),
    }

    param_grids = {
        "GradientBoosting": {
            "n_estimators": [200, 400],
            "learning_rate": [0.05, 0.1],
            "max_depth": [3, 4],
            "subsample": [0.7, 1.0],
        },
        "HistGradientBoosting": {
            "learning_rate": [0.05, 0.1],
            "max_depth": [4, 6],
            "max_iter": [300, 600],
            "l2_regularization": [0.0, 0.1, 1.0],
        },
        "Ridge": {
            "ridge__alpha": [0.01, 0.1, 1.0, 10.0, 100.0],
        },
    }

    results = []
    preds = {}

    # Train ML models
    for name, est in models.items():
        print(f"\n=== Training {name} ({label}, optimising MAE) ===")

        grid = GridSearchCV(
            estimator=est,
            param_grid=param_grids[name],
            scoring="neg_mean_absolute_error",  # MAE for tuning
            cv=tscv,
            n_jobs=-1
        )
        grid.fit(X_train, y_train)

        best_est = grid.best_estimator_
        print(f"Best params for {name}: {grid.best_params_}")

        # Predict on test set
        y_pred = best_est.predict(X_test)
        y_pred_series = pd.Series(y_pred, index=y_test.index, name=name)
        preds[name] = y_pred_series

        # Metrics on test set
        metrics_row = compute_all_metrics(name, y_test, y_pred)
        results.append(metrics_row)

    # Naive baseline: yesterday same time (1 day lag)
    if granularity == "15min":
        baseline_lag = 96
    elif granularity == "hourly":
        baseline_lag = 24
    else:
        baseline_lag = 1

    y_naive_full = y.shift(baseline_lag)
    y_naive_test = y_naive_full.reindex(y_test.index)
    mask = ~y_naive_test.isna()

    if mask.sum() > 0:
        naive_metrics = compute_all_metrics(
            f"Naive_lag_{baseline_lag}",
            y_test[mask],
            y_naive_test[mask]
        )
        results.append(naive_metrics)
        preds[f"Naive_lag_{baseline_lag}"] = y_naive_test

    results_df = pd.DataFrame(results)
    return results_df, preds, (X_train, X_test, y_train, y_test)


# =========================================================
# 6. PLOTS: ACTUAL vs PREDICTED (TIME SERIES)
# =========================================================

def _save_current_fig(basename: str):
    """
    Save current matplotlib figure under plots/ with the provided basename.
    """
    safe_name = basename.replace(" ", "_").replace(":", "_")
    path = PLOTS_DIR / f"{safe_name}.png"
    plt.savefig(path, bbox_inches="tight")
    plt.close()
    print(f"💾 Saved plot: {path}")


def plot_actual_vs_predicted(y_test, preds, sample_points=96, granularity="15-minute"):
    """
    Plot actual vs predicted for the last `sample_points` intervals.
    """
    if sample_points is None:
        y_plot = y_test
    else:
        y_plot = y_test.tail(sample_points)

    plt.figure(figsize=(14, 7))

    # Actual: lighter blue
    plt.plot(
        y_plot.index, y_plot.values,
        label="Actual Energy Consumption",
        color="#7FA6FF",
        linewidth=2.0,
        alpha=0.75,
        linestyle="-"
    )

    # Predictions (skip Naive in the chart for clarity)
    for name, s in preds.items():
        if name.startswith("Naive"):
            continue
        if sample_points is None:
            s_plot = s.reindex(y_test.index)
        else:
            s_plot = s.tail(sample_points)
        plt.plot(
            s_plot.index, s_plot.values,
            label=f"Predicted – {name}",
            linewidth=2.0,
            linestyle="--",
            alpha=0.9
        )

    plt.title(
        f"{granularity.capitalize()} Energy Consumption: "
        f"Actual vs Predicted"
        + ("" if sample_points is None else f" (last {sample_points} steps)")
    )
    plt.xlabel("Time")
    plt.ylabel("Energy Consumption (kWh)")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    suffix = "full_range" if sample_points is None else f"last_{sample_points}"
    _save_current_fig(f"{granularity}_actual_vs_predicted_{suffix}")


# =========================================================
# 7. BUSINESS / HOUSEHOLD / OFF-PEAK ANALYSIS
# =========================================================

def analyze_periods(y_test: pd.Series, preds: dict, granularity: str):
    """
    Compute metrics by Business / Household / Off-Peak for each model.
    Business: 08:00–16:00 (8–15)
    Household: 16:00–22:00 (16–21)
    Off-Peak: everything else
    """
    df = pd.DataFrame({"y_actual": y_test})
    for name, s in preds.items():
        df[f"y_pred_{name}"] = s.reindex(y_test.index)

    df["hour"] = df.index.hour
    df["Period"] = np.select(
        [
            df["hour"].between(8, 15),   # 8–15  -> Business
            df["hour"].between(16, 21),  # 16–21 -> Household
        ],
        ["Business", "Household"],
        default="Off-Peak"
    )

    rows = []
    model_names = [c.replace("y_pred_", "") for c in df.columns if c.startswith("y_pred_")]

    for m in model_names:
        col = f"y_pred_{m}"
        for period_name, part in df.groupby("Period"):
            part = part.dropna(subset=[col])
            if part.empty:
                continue
            y_true = part["y_actual"]
            y_pred = part[col]
            row = compute_all_metrics(f"{m} ({period_name})", y_true, y_pred)
            row["BaseModel"] = m
            row["Period"] = period_name
            rows.append(row)

    res = pd.DataFrame(rows)
    print(f"\n=== {granularity.upper()} — Error by Business / Household / Off-Peak ===")
    print(res[["Model", "Period", "MAE", "RMSE", "CVRMSE", "NMBE", "R2"]])

    # Plot MAE / RMSE / CVRMSE as grouped bars
    for metric in ["MAE", "RMSE", "CVRMSE"]:
        pivot = res.pivot(index="BaseModel", columns="Period", values=metric)
        plt.figure(figsize=(8, 4))
        pivot.plot(kind="bar", ax=plt.gca())
        unit = "kWh" if metric in ("MAE", "RMSE") else "%"
        plt.ylabel(f"{metric} ({unit})")
        plt.xlabel("Model")
        plt.title(f"{granularity.upper()} — {metric} by Period")
        plt.xticks(rotation=0)
        plt.tight_layout()
        _save_current_fig(f"{granularity}_{metric}_by_period")

    return res


# =========================================================
# 8. MAE BY HOUR-OF-DAY
# =========================================================

def plot_mae_by_hour(y_test: pd.Series, preds: dict, granularity: str):
    """
    Plot MAE by hour-of-day for each model.
    """
    df = pd.DataFrame({"y_actual": y_test})
    for name, s in preds.items():
        df[f"y_pred_{name}"] = s.reindex(y_test.index)
    df["hour"] = df.index.hour

    plt.figure(figsize=(10, 5))
    model_names = [c.replace("y_pred_", "") for c in df.columns if c.startswith("y_pred_")]

    for m in model_names:
        col = f"y_pred_{m}"
        grouped = df.dropna(subset=[col]).groupby("hour").apply(
            lambda g: mae(g["y_actual"], g[col])
        )
        plt.plot(grouped.index, grouped.values, marker="o", label=m)

    plt.xlabel("Hour of Day")
    plt.ylabel("MAE (kWh)")
    plt.title(f"{granularity.upper()} — MAE by Hour of Day")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    _save_current_fig(f"{granularity}_mae_by_hour")


# =========================================================
# 9. MONTHLY / QUARTERLY / ANNUAL AGGREGATED PLOTS
# =========================================================

def plot_aggregated_actual_vs_predicted(y_test, preds, freq="M", granularity="15-minute"):
    """
    Aggregate Actual and Predicted to a coarser time scale and plot.

    freq:
        "M"  -> monthly totals
        "Q"  -> quarterly totals
        "A"  -> annual totals
    """
    df = pd.DataFrame({"Actual": y_test})
    for name, s in preds.items():
        if name.startswith("Naive"):
            continue
        df[name] = s.reindex(y_test.index)

    df_agg = df.resample(freq).sum(min_count=1)

    freq_label = {"M": "Monthly", "Q": "Quarterly", "A": "Annual"}.get(freq, freq)

    plt.figure(figsize=(10, 5))

    for col in df_agg.columns:
        plt.plot(
            df_agg.index,
            df_agg[col].values,
            marker="o",
            linestyle="-" if col == "Actual" else "--",
            linewidth=2.0 if col == "Actual" else 1.8,
            alpha=0.9,
            label=col if col == "Actual" else f"Predicted – {col}",
        )

    plt.title(f"{granularity.capitalize()} Electricity – {freq_label} Energy Consumption")
    plt.xlabel("Time")
    plt.ylabel("Energy Consumption (kWh)")
    plt.xticks(rotation=45)
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    freq_label = freq_label.lower()
    _save_current_fig(f"{granularity}_{freq_label}_actual_vs_predicted")


# =========================================================
# 10. ADDITIONAL STORYTELLING VISUALS
# =========================================================

def plot_heatmap_consumption(y_test: pd.Series, granularity: str):
    """Day-of-week x hour average consumption heatmap for the actual series."""
    df = pd.DataFrame({"Actual": y_test})
    df["dow"] = df.index.dayofweek
    df["hour"] = df.index.hour
    pivot = df.pivot_table(values="Actual", index="dow", columns="hour", aggfunc="mean")

    plt.figure(figsize=(12, 4))
    im = plt.imshow(pivot, aspect="auto", origin="lower", cmap="viridis")
    plt.colorbar(im, label="Avg Consumption (kWh)")
    plt.xticks(range(24))
    plt.yticks(range(7), ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
    plt.xlabel("Hour of Day")
    plt.ylabel("Day of Week")
    plt.title(f"{granularity.upper()} — Avg Consumption Heatmap (DOW x Hour)")
    _save_current_fig(f"{granularity}_heatmap_consumption_dow_hour")


def plot_error_heatmaps(y_test: pd.Series, preds: dict, granularity: str):
    """Heatmaps of MAE by day-of-week and hour for each model."""
    base = pd.DataFrame({"Actual": y_test})
    base["dow"] = base.index.dayofweek
    base["hour"] = base.index.hour

    for name, s in preds.items():
        aligned = s.reindex(y_test.index)
        err = (base["Actual"] - aligned).abs()
        pivot = err.groupby([base["dow"], base["hour"]]).mean().unstack()

        plt.figure(figsize=(12, 4))
        im = plt.imshow(pivot, aspect="auto", origin="lower", cmap="magma")
        plt.colorbar(im, label="MAE (kWh)")
        plt.xticks(range(24))
        plt.yticks(range(7), ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
        plt.xlabel("Hour of Day")
        plt.ylabel("Day of Week")
        plt.title(f"{granularity.upper()} — MAE Heatmap ({name})")
        _save_current_fig(f"{granularity}_heatmap_mae_{name}")


def plot_rolling_mae(y_test: pd.Series, preds: dict, window: str, granularity: str):
    """Rolling MAE over a time-based window (e.g., '7D', '14D')."""
    plt.figure(figsize=(12, 5))
    for name, s in preds.items():
        aligned = s.reindex(y_test.index)
        rolling_mae = (y_test - aligned).abs().rolling(window=window, min_periods=1).mean()
        plt.plot(rolling_mae.index, rolling_mae.values, label=name, linewidth=1.6)

    plt.title(f"{granularity.upper()} Rolling MAE ({window})")
    plt.xlabel("Time")
    plt.ylabel("MAE (kWh)")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    _save_current_fig(f"{granularity}_rolling_mae_{window}")


def plot_monthly_error_bars(y_test: pd.Series, preds: dict, granularity: str):
    """Monthly absolute error per model (highlights small differences hidden in line plots)."""
    df = pd.DataFrame({"Actual": y_test})
    for name, s in preds.items():
        df[name] = s.reindex(y_test.index)

    monthly = df.resample("M").sum(min_count=1)
    errors = monthly.drop(columns="Actual").subtract(monthly["Actual"], axis=0).abs()

    plt.figure(figsize=(10, 4))
    errors.plot(kind="bar", ax=plt.gca())
    plt.ylabel("Monthly Absolute Error (kWh)")
    plt.xlabel("Month")
    plt.title(f"{granularity.upper()} — Monthly Absolute Error by Model")
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    _save_current_fig(f"{granularity}_monthly_abs_error_by_model")


def plot_residual_distribution(y_test: pd.Series, preds: dict, granularity: str):
    """Histogram of residuals to show bias/spread per model."""
    plt.figure(figsize=(10, 5))
    for name, s in preds.items():
        aligned = s.reindex(y_test.index)
        residuals = (y_test - aligned)
        plt.hist(residuals, bins=50, alpha=0.5, label=name)

    plt.xlabel("Residual (Actual - Predicted)")
    plt.ylabel("Count")
    plt.title(f"{granularity.upper()} — Residual Distribution")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    _save_current_fig(f"{granularity}_residual_distribution")


# =========================================================
# 10. MAIN RUN – 15-MINUTE AND HOURLY
# =========================================================

if __name__ == "__main__":
    # ---------- Load raw 15-minute data & clean ----------
    df_raw = pd.read_excel(DATA_PATH)
    df_15 = basic_clean(df_raw)

    # ---------- 15-MINUTE MODEL ----------
    X_15, y_15 = build_features_from_index(df_15, TARGET_COL, granularity="15min")
    results_15, preds_15, (X_train_15, X_test_15, y_train_15, y_test_15) = \
        train_and_evaluate_all_models(X_15, y_15, granularity="15min", label="15-minute")

    print("\n=== Test-set metrics (15-minute electricity) ===")
    print(results_15.set_index("Model"))

    # 15-min zoomed (last 24h)
    plot_actual_vs_predicted(
        y_test_15,
        preds_15,
        sample_points=96,
        granularity="15-minute"
    )

    # 15-min full range
    plot_actual_vs_predicted(
        y_test_15,
        preds_15,
        sample_points=None,
        granularity="15-minute (full range)"
    )

    # Business / Household / Off-Peak analysis (15-min)
    period_metrics_15 = analyze_periods(y_test_15, preds_15, granularity="15-minute")

    # MAE by hour-of-day (15-min)
    plot_mae_by_hour(y_test_15, preds_15, granularity="15-minute")

    # 15-min aggregated monthly / quarterly / annual
    plot_aggregated_actual_vs_predicted(y_test_15, preds_15, freq="M", granularity="15-minute")
    plot_aggregated_actual_vs_predicted(y_test_15, preds_15, freq="Q", granularity="15-minute")
    plot_aggregated_actual_vs_predicted(y_test_15, preds_15, freq="A", granularity="15-minute")
    plot_heatmap_consumption(y_test_15, granularity="15-minute")
    plot_error_heatmaps(y_test_15, preds_15, granularity="15-minute")
    plot_rolling_mae(y_test_15, preds_15, window="7D", granularity="15-minute")
    plot_monthly_error_bars(y_test_15, preds_15, granularity="15-minute")
    plot_residual_distribution(y_test_15, preds_15, granularity="15-minute")

    # ---------- HOURLY DATASET FROM 15-MIN ----------
    print("\n⏱️ Aggregating 15-minute data into hourly intervals...")
    hourly_df = aggregate_hourly(df_15)
    print("✅ Hourly dataset created successfully!")
    print(f"Rows: {hourly_df.shape[0]} | Columns: {hourly_df.shape[1]}")
    print(f"Time range: {hourly_df.index.min()} to {hourly_df.index.max()}")

    # Save hourly dataset
    hourly_df.to_csv(HOURLY_CSV_PATH)
    print(f"💾 Saved hourly dataset to {HOURLY_CSV_PATH}")

    # ---------- HOURLY MODEL ----------
    X_h, y_h = build_features_from_index(hourly_df, TARGET_COL, granularity="hourly")
    results_h, preds_h, (X_train_h, X_test_h, y_train_h, y_test_h) = \
        train_and_evaluate_all_models(X_h, y_h, granularity="hourly", label="hourly")

    print("\n=== Test-set metrics (hourly electricity) ===")
    print(results_h.set_index("Model"))

    # Hourly zoomed (last week)
    plot_actual_vs_predicted(
        y_test_h,
        preds_h,
        sample_points=168,
        granularity="hourly"
    )

    # Hourly full range
    plot_actual_vs_predicted(
        y_test_h,
        preds_h,
        sample_points=None,
        granularity="hourly (full range)"
    )

    # Business / Household / Off-Peak analysis (hourly)
    period_metrics_h = analyze_periods(y_test_h, preds_h, granularity="hourly")

    # MAE by hour-of-day (hourly)
    plot_mae_by_hour(y_test_h, preds_h, granularity="hourly")

    # Hourly aggregated monthly / quarterly / annual
    plot_aggregated_actual_vs_predicted(y_test_h, preds_h, freq="M", granularity="hourly")
    plot_aggregated_actual_vs_predicted(y_test_h, preds_h, freq="Q", granularity="hourly")
    plot_aggregated_actual_vs_predicted(y_test_h, preds_h, freq="A", granularity="hourly")
    plot_heatmap_consumption(y_test_h, granularity="hourly")
    plot_error_heatmaps(y_test_h, preds_h, granularity="hourly")
    plot_rolling_mae(y_test_h, preds_h, window="14D", granularity="hourly")
    plot_monthly_error_bars(y_test_h, preds_h, granularity="hourly")
    plot_residual_distribution(y_test_h, preds_h, granularity="hourly")
