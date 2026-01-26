#!/usr/bin/env python
"""
Dynamic SARIMAX trainer/forecaster for the Enfield dataset.

Example:
    python sarimax_standalone.py \
        --train content/data/train_15min.csv \
        --test  content/data/test_15min.csv \
        --target baseline.out.site_energy.total.energy_consumption.kwh \
        --seasonal-period 96

Outputs:
    - prints best-order summary
    - saves pred_SARIMAX.csv next to the test split
    - plots train vs. forecast (requires matplotlib)
"""

import argparse
from pathlib import Path
from typing import Iterable, Tuple

import numpy as np
import pandas as pd
import statsmodels.api as sm
try:
    from statsmodels.tsa.statespace.sarimax import SARIMAXResults
except ImportError:
    SARIMAXResults = object  # fallback for older statsmodels builds
try:
    from statsmodels.tools.sm_exceptions import ConvergenceWarning, ModelFitWarning
except ImportError:
    from statsmodels.tools.sm_exceptions import ConvergenceWarning
    ModelFitWarning = ConvergenceWarning
import warnings
import matplotlib.pyplot as plt

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "content" / "data"
DEFAULT_TRAIN = DATA_DIR / "train_15min.csv"
DEFAULT_TEST = DATA_DIR / "test_15min.csv"
DEFAULT_TARGET = "baseline.out.site_energy.total.energy_consumption.kwh"
DEFAULT_SEASONAL_PERIOD = 96
DEFAULT_ORDER_GRID = "0,1"
DEFAULT_SEASONAL_GRID = "0"
STATE_BUDGET = 2e7  # approximate limit for k_states * n_obs to avoid huge allocations


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone SARIMAX forecaster.")
    parser.add_argument(
        "--train",
        default=str(DEFAULT_TRAIN),
        help=f"Path to training CSV (default: {DEFAULT_TRAIN})",
    )
    parser.add_argument(
        "--test",
        default=str(DEFAULT_TEST),
        help=f"Path to test CSV (default: {DEFAULT_TEST})",
    )
    parser.add_argument(
        "--target",
        default=DEFAULT_TARGET,
        help=f"Target column name (default: {DEFAULT_TARGET})",
    )
    parser.add_argument(
        "--seasonal-period",
        type=int,
        default=DEFAULT_SEASONAL_PERIOD,
        help="Seasonal period (e.g., 96 for 24h at 15-minute frequency).",
    )
    parser.add_argument(
        "--order-grid",
        type=str,
        default=DEFAULT_ORDER_GRID,
        help=f"Comma-separated p/d/q candidates (default '{DEFAULT_ORDER_GRID}').",
    )
    parser.add_argument(
        "--seasonal-grid",
        type=str,
        default=DEFAULT_SEASONAL_GRID,
        help=f"Comma-separated P/D/Q candidates (default '{DEFAULT_SEASONAL_GRID}').",
    )
    parser.add_argument(
        "--maxiter",
        type=int,
        default=200,
        help="Max iterations for the optimizer (default 200).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional output directory (defaults to test file's parent).",
    )
    return parser.parse_args()


def load_series(csv_path: Path, target: str) -> pd.Series:
    df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
    if target not in df.columns:
        raise KeyError(f"{target!r} not found in {csv_path}")
    series = df[target].astype(float).sort_index()
    inferred = pd.infer_freq(series.index)
    if inferred:
        series.index.freq = inferred
    return series


def candidate_orders(grid: Iterable[int]) -> Iterable[Tuple[int, int, int]]:
    vals = list(grid)
    for p in vals:
        for d in vals:
            for q in vals:
                yield (p, d, q)


def should_skip(order: Tuple[int, int, int], seasonal: Tuple[int, int, int], seasonal_period: int, n_obs: int) -> bool:
    p, d, q = order
    P, D, Q = seasonal
    base_states = p + q + 1 + (1 if d > 0 else 0)
    seasonal_states = (P + Q) * seasonal_period
    if D > 0:
        seasonal_states += seasonal_period
    approx_states = base_states + seasonal_states
    return approx_states * n_obs > STATE_BUDGET


def sarimax_grid_search(
    y_train: pd.Series,
    order_candidates,
    seasonal_candidates,
    seasonal_period: int,
    maxiter: int,
) -> Tuple[SARIMAXResults, Tuple[int, int, int], Tuple[int, int, int]]:
    best_model = None
    best_aic = np.inf
    best_order = None
    best_seasonal = None

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        warnings.simplefilter("ignore", ModelFitWarning)

        for order in order_candidates:
            for seasonal in seasonal_candidates:
                seasonal_order = seasonal + (seasonal_period,)
                if (order[0] + order[2] + seasonal[0] + seasonal[2]) == 0:
                    # Skip pure-drift models (no AR or MA component) that just sit at the mean.
                    continue
                if should_skip(order, seasonal, seasonal_period, len(y_train)):
                    print(f"Skipping order={order}, seasonal={seasonal_order}: estimated state size too large")
                    continue
                try:
                    model = sm.tsa.statespace.SARIMAX(
                        y_train,
                        order=order,
                        seasonal_order=seasonal_order,
                        trend="c",
                        enforce_stationarity=False,
                        enforce_invertibility=False,
                    )
                    result = model.fit(disp=False, maxiter=maxiter)
                    aic = result.aic
                    if np.isfinite(aic) and aic < best_aic:
                        best_model = result
                        best_aic = aic
                        best_order = order
                        best_seasonal = seasonal
                    print(f"Checked order={order}, seasonal={seasonal_order}, AIC={aic:.2f}")
                except Exception as exc:  # noqa: BLE001
                    print(f"Failed order={order}, seasonal={seasonal_order}: {exc}")

    if best_model is None:
        raise RuntimeError("No SARIMAX candidate converged; adjust the grids or data.")
    return best_model, best_order, best_seasonal


def main():
    args = parse_args()
    train_path = Path(args.train)
    test_path = Path(args.test)
    if not train_path.exists():
        raise FileNotFoundError(f"Training CSV not found: {train_path.resolve()}. Provide --train.")
    if not test_path.exists():
        raise FileNotFoundError(f"Test CSV not found: {test_path.resolve()}. Provide --test.")
    out_dir = args.output_dir or test_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    y_train = load_series(train_path, args.target)
    y_test = load_series(test_path, args.target)

    order_vals = tuple(int(x.strip()) for x in args.order_grid.split(","))
    seas_vals = tuple(int(x.strip()) for x in args.seasonal_grid.split(","))
    order_candidates = list(candidate_orders(order_vals))
    seasonal_candidates = list(candidate_orders(seas_vals))

    print(f"Training points: {len(y_train)}, Test points: {len(y_test)}")
    print(f"Candidate non-seasonal orders: {order_candidates}")
    print(f"Candidate seasonal orders (without period): {seasonal_candidates}")
    print(f"Seasonal period (m): {args.seasonal_period}")

    best_model, best_order, best_seasonal = sarimax_grid_search(
        y_train,
        order_candidates,
        seasonal_candidates,
        args.seasonal_period,
        args.maxiter,
    )

    print("\nBest configuration found:")
    print(f"  order={best_order}")
    print(f"  seasonal_order={best_seasonal + (args.seasonal_period,)}")
    print(best_model.summary())

    forecast = best_model.get_forecast(steps=len(y_test))
    yhat = forecast.predicted_mean
    yhat.index = y_test.index  # align timestamps
    pred_path = out_dir / "pred_SARIMAX.csv"
    yhat.to_csv(pred_path, header=["SARIMAX"])
    print(f"\nSaved forecast to {pred_path}")

    # Quick error metrics
    mae = np.mean(np.abs(y_test - yhat))
    rmse = np.sqrt(np.mean((y_test - yhat) ** 2))
    print(f"MAE: {mae:,.2f}")
    print(f"RMSE: {rmse:,.2f}")

    # Plot comparison
    plt.figure(figsize=(14, 5))
    plt.plot(y_train.index[-len(y_test):], y_train.iloc[-len(y_test):], label="Train (tail)", color="#cccccc")
    plt.plot(y_test.index, y_test, label="Actual (test)", color="navy")
    plt.plot(yhat.index, yhat, label="SARIMAX forecast", color="crimson", linestyle="--")
    plt.title("SARIMAX forecast vs. actuals")
    plt.xlabel("Timestamp")
    plt.ylabel("Energy Consumption (kWh)")
    plt.legend()
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
