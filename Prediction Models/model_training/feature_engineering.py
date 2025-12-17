"""Feature engineering utilities for load forecasting models.

This module centralises logic for constructing feature matrices used by
hourly and 15-minute pipelines. It augments the legacy calendar features
with lagged targets, rolling statistics, and lagged sub-meter aggregates
while keeping the implementation compatible with scikit-learn estimators.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class FeatureConfig:
    """Configuration for lag and rolling windows per granularity."""

    target_lags: Sequence[int]
    submeter_lags: Sequence[int]
    rolling_windows: Sequence[int]


def _granularity_config(granularity: str) -> FeatureConfig:
    gran = granularity.lower()
    if gran not in {"hourly", "15min", "15-minute", "15_min"}:
        raise ValueError(f"Unsupported granularity '{granularity}'")

    if gran == "hourly":
        return FeatureConfig(
            target_lags=(1, 2, 3, 6, 12, 24, 48, 72, 96, 168),
            submeter_lags=(1, 24, 168),
            rolling_windows=(24, 72, 168),
        )

    # 15-minute data ⇒ 4 timesteps per hour.
    return FeatureConfig(
        target_lags=(1, 4, 12, 24, 48, 96, 192, 384, 672),
        submeter_lags=(1, 4, 96, 672),
        rolling_windows=(96, 288, 672),
    )


def _base_time_features(index: pd.DatetimeIndex, granularity: str) -> pd.DataFrame:
    gran = granularity.lower()
    data: dict[str, Iterable[int]] = {
        "hour": index.hour,
        "day_of_week": index.dayofweek,
        "month": index.month,
        "day_of_year": index.dayofyear,
        "is_weekend": index.dayofweek.isin([5, 6]).astype(int),
    }
    if gran == "15min" or gran == "15-minute" or gran == "15_min":
        data.update({
            "minute": index.minute,
            "quarter_hour": (index.minute // 15),
        })
    return pd.DataFrame(data, index=index)


def _add_lag_features(
    frame: pd.DataFrame,
    cols: Sequence[str],
    lags: Sequence[int],
    prefix: str,
) -> pd.DataFrame:
    features = {}
    for col in cols:
        for lag in lags:
            key = f"{prefix}{col}_lag{lag}"
            features[key] = frame[col].shift(lag)
    return pd.DataFrame(features, index=frame.index)


def _add_rolling_features(
    series: pd.Series,
    windows: Sequence[int],
) -> pd.DataFrame:
    out = {}
    for window in windows:
        rolling = series.rolling(window=window, min_periods=1)
        out[f"target_roll_mean_{window}"] = rolling.mean()
        out[f"target_roll_max_{window}"] = rolling.max()
        out[f"target_roll_min_{window}"] = rolling.min()
        out[f"target_roll_std_{window}"] = rolling.std().fillna(0.0)
    return pd.DataFrame(out, index=series.index)


def build_feature_table(
    df: pd.DataFrame,
    target_col: str,
    granularity: str,
    include_submeters: bool = True,
) -> tuple[pd.DataFrame, pd.Series]:
    """Create feature matrix and aligned target series.

    Parameters
    ----------
    df:
        Cleaned dataframe indexed by timestamp.
    target_col:
        Name of the target column within ``df``.
    granularity:
        Either ``"hourly"`` or ``"15min"`` (case-insensitive).
    include_submeters:
        Whether to include lagged versions of other numeric columns.

    Returns
    -------
    (features, target)
        Feature matrix ``X`` and aligned target ``y`` with lagged rows dropped.
    """

    if target_col not in df.columns:
        raise KeyError(f"'{target_col}' missing from dataframe")

    cfg = _granularity_config(granularity)
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()

    base = _base_time_features(df.index, granularity)
    target = df[target_col].astype(float)

    lag_features = _add_lag_features(df[[target_col]], [target_col], cfg.target_lags, prefix="")
    lag_features.columns = [f"target_lag{lag}" for lag in cfg.target_lags]

    rolling_features = _add_rolling_features(target, cfg.rolling_windows)

    frames = [base, lag_features, rolling_features]

    if include_submeters:
        sub_cols = [col for col in numeric_cols if col != target_col]
        if sub_cols:
            sub_df = df[sub_cols]
            sub_lags = _add_lag_features(sub_df, sub_cols, cfg.submeter_lags, prefix="sub_")
            frames.append(sub_lags)

    X = pd.concat(frames, axis=1)

    # Drop rows with NaNs introduced by lagging/rolling.
    aligned = pd.concat([X, target], axis=1).dropna()
    y_aligned = aligned[target_col]
    X_aligned = aligned.drop(columns=[target_col])

    return X_aligned, y_aligned


__all__ = ["build_feature_table"]


