"""Command-line entry point for the enhanced load forecasting pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from feature_engineering import build_feature_table


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "content" / "data"
MODEL_DIR = DATA_DIR / "models"
ANALYSIS_DIR = DATA_DIR / "analysis_outputs"
MODEL_DIR.mkdir(parents=True, exist_ok=True)
ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

TARGET_COL = "baseline.out.site_energy.total.energy_consumption.kwh"

MODEL_FILENAMES = {
    "GradientBoosting": "gbr.joblib",
    "HistGradientBoosting": "hgb.joblib",
    "Ridge": "ridge.joblib",
}


MODEL_SPECS = {
    "GradientBoosting": GradientBoostingRegressor(
        random_state=42,
        n_estimators=600,
        learning_rate=0.05,
        max_depth=4,
        min_samples_split=5,
        min_samples_leaf=8,
        subsample=0.9,
    ),
    "HistGradientBoosting": HistGradientBoostingRegressor(
        random_state=42,
        max_iter=600,
        learning_rate=0.08,
        max_depth=7,
        l2_regularization=0.1,
        early_stopping="auto",
    ),
    "Ridge": make_pipeline(StandardScaler(with_mean=False), Ridge(alpha=0.5)),
}


@dataclass
class FeatureSet:
    train_df: pd.DataFrame
    test_df: pd.DataFrame
    X_train: pd.DataFrame
    y_train: pd.Series
    X_test: pd.DataFrame
    y_test: pd.Series


_CACHE: Dict[str, FeatureSet] = {}


def _read_split_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    first = df.columns[0]
    df[first] = pd.to_datetime(df[first], errors="coerce")
    df = df.set_index(first).sort_index()
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_feature_sets(granularity: str) -> FeatureSet:
    gran_key = granularity.lower()
    if gran_key in _CACHE:
        return _CACHE[gran_key]

    train_df = _read_split_csv(DATA_DIR / f"train_{gran_key}.csv")
    test_df = _read_split_csv(DATA_DIR / f"test_{gran_key}.csv")
    combined = pd.concat([train_df, test_df]).sort_index()

    X_all, y_all = build_feature_table(combined, TARGET_COL, gran_key, include_submeters=True)

    train_idx = X_all.index.intersection(train_df.index)
    test_idx = X_all.index.intersection(test_df.index)

    feature_set = FeatureSet(
        train_df=train_df,
        test_df=test_df,
        X_train=X_all.loc[train_idx],
        y_train=y_all.loc[train_idx],
        X_test=X_all.loc[test_idx],
        y_test=y_all.loc[test_idx],
    )
    _CACHE[gran_key] = feature_set
    return feature_set


def train_models(granularity: str) -> Dict[str, Path]:
    data = load_feature_sets(granularity)
    out_dir = MODEL_DIR / granularity
    out_dir.mkdir(parents=True, exist_ok=True)
    feature_names = data.X_train.columns.tolist()

    artifacts = {}
    for name, estimator in MODEL_SPECS.items():
        model = estimator.fit(data.X_train, data.y_train)
        joblib.dump(
            {
                "model": model,
                "features": feature_names,
                "granularity": granularity,
                "feature_version": "enhanced_v1",
            },
            out_dir / MODEL_FILENAMES[name],
        )
        artifacts[name] = out_dir / MODEL_FILENAMES[name]
    return artifacts


def predict_models(granularity: str) -> Dict[str, pd.Series]:
    data = load_feature_sets(granularity)
    preds_dir = ANALYSIS_DIR / f"preds_{granularity}"
    preds_dir.mkdir(parents=True, exist_ok=True)
    data.y_test.to_csv(preds_dir / f"y_test_{granularity}.csv")

    predictions: Dict[str, pd.Series] = {}
    for name, filename in MODEL_FILENAMES.items():
        model_path = MODEL_DIR / granularity / filename
        if not model_path.exists():
            continue
        bundle = joblib.load(model_path)
        model = bundle["model"]
        feature_names = bundle["features"]
        X_aligned = data.X_test.reindex(columns=feature_names, fill_value=0.0)
        preds = pd.Series(model.predict(X_aligned), index=data.y_test.index, name=name)
        preds.to_csv(preds_dir / f"pred_{name}.csv")
        predictions[name] = preds
    return predictions


def evaluate_predictions(y_true: pd.Series, preds: Dict[str, pd.Series]) -> Dict[str, Dict[str, float]]:
    metrics = {}
    for name, series in preds.items():
        mae = mean_absolute_error(y_true, series)
        rmse = np.sqrt(mean_squared_error(y_true, series))
        mape = float(np.mean(np.abs((y_true - series) / (y_true + 1e-8))) * 100)
        r2 = r2_score(y_true, series)
        bias = float(np.mean(series - y_true))
        metrics[name] = {
            "MAE": float(mae),
            "RMSE": float(rmse),
            "MAPE": mape,
            "R2": float(r2),
            "Bias": bias,
        }
    return metrics


def main() -> None:
    all_metrics: Dict[str, Dict[str, Dict[str, float]]] = {}
    for gran in ("hourly", "15min"):
        print(f"Running enhanced pipeline for {gran} data ...")
        train_models(gran)
        preds = predict_models(gran)
        y_test = load_feature_sets(gran).y_test
        all_metrics[gran] = evaluate_predictions(y_test, preds)

    metrics_path = ANALYSIS_DIR / "enhanced_metrics.json"
    with metrics_path.open("w") as fh:
        json.dump(all_metrics, fh, indent=2)
    print(f"Enhanced metrics written to {metrics_path}")


if __name__ == "__main__":
    main()


