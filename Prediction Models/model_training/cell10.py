# =========================
# STEP 7 — Compare Actual vs Predicted (Enhanced & Descriptive)
# =========================
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

# Directories where Step 6 saved predictions (relative to this script)
BASE_DIR = Path(__file__).resolve().parent
BASE = BASE_DIR / "content" / "data" / "analysis_outputs"
pred_paths = {
    "hourly": BASE / "preds_hourly",
    "15min":  BASE / "preds_15min"
}

def evaluate_metrics(y_true, y_pred):
    """Compute key error metrics."""
    mae  = np.mean(np.abs(y_true - y_pred))
    rmse = np.sqrt(np.mean((y_true - y_pred)**2))
    mape = np.mean(np.abs((y_true - y_pred) / (y_true + 1e-8))) * 100
    return round(mae, 2), round(rmse, 2), round(mape, 2)

def show_side_by_side(gran):
    print(f"\n==============================\n{gran.upper()} — Actual vs Predicted\n==============================")
    p = pred_paths[gran]

    # ---- Load actuals ----
    y_true = pd.read_csv(p / f"y_test_{gran}.csv", index_col=0)
    y_true.columns = ["y_actual"]

    # ---- Load predictions ----
    preds = []
    for model in ["SARIMAX", "Prophet", "GradientBoosting", "Ridge"]:
        f = p / f"pred_{model}.csv"
        if f.exists():
            df = pd.read_csv(f, index_col=0)
            df.columns = [f"y_pred_{model}"]
            preds.append(df)
    merged = y_true.copy()
    for df in preds:
        merged = merged.join(df, how="left")

    # show top few rows
    display(merged.head(10))

    # ---- Metrics table ----
    metrics_summary = []
    for col in merged.columns[1:]:
        model_name = col.replace("y_pred_", "")
        mae, rmse, mape = evaluate_metrics(merged["y_actual"], merged[col])
        metrics_summary.append({
            "Model": model_name,
            "MAE": mae,
            "RMSE": rmse,
            "MAPE (%)": mape
        })
    metrics_df = pd.DataFrame(metrics_summary).sort_values("RMSE").reset_index(drop=True)

    print("\n📊 Performance Summary:")
    display(metrics_df)

    # ---- Visualization ----
    plt.figure(figsize=(14, 6))

    # Actual (dark, bold)
    plt.plot(
        merged.index,
        merged["y_actual"],
        label="Actual (Ground Truth)",
        color="#002366",  # dark royal blue
        linewidth=3.5,
        linestyle="-",
        alpha=0.95,
        zorder=5
    )

    # Predicted (lighter, dashed)
    color_map = {
        "SARIMAX": "#DC143C",         # crimson
        "Prophet": "#9370DB",         # purple
        "GradientBoosting": "#FF8C00",# orange
        "Ridge": "#32CD32"            # lime green
    }

    for col in merged.columns[1:]:
        model = col.replace("y_pred_", "")
        plt.plot(
            merged.index,
            merged[col],
            label=f"{model} (Predicted)",
            color=color_map.get(model, "gray"),
            linewidth=1.8,
            linestyle="--",
            alpha=0.7
        )

    plt.title(f"{gran.upper()} — Actual vs Predicted Energy Consumption", fontsize=14, fontweight="bold")
    plt.xlabel("Timestamp", fontsize=12)
    plt.ylabel("Energy Consumption (kWh)", fontsize=12)
    plt.legend(title="Models", fontsize=10, title_fontsize=11, loc="best", frameon=True)
    plt.grid(alpha=0.3, linestyle=":")
    plt.tight_layout()
    plt.show()

    # ---- Summary interpretation ----
    print(f"🧠 Insights for {gran.upper()}:")
    best_model = metrics_df.loc[0, 'Model']
    print(f"• 🏆 Best overall model by RMSE: **{best_model}**")
    print(f"• Average MAE across models: {metrics_df['MAE'].mean():,.2f}")
    print(f"• The closer predicted lines follow the dark blue Actual, the more reliable the model.")
    print("• Larger deviations indicate where models struggle (e.g., peaks or dips).")
    print("• Use RMSE for overall fit, MAPE for percentage accuracy.\n")

# Run for both granularities
show_side_by_side("hourly")
show_side_by_side("15min")
