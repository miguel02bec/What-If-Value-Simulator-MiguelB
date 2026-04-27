"""
train_model.py
Entrena modelos XGBoost estratificados por posición.
"""
import logging
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import shap
import xgboost as xgb
from sklearn.model_selection import KFold
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
import joblib

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATASET = "dataset_model.csv"
MODEL_OUTPUT = "model_market_value.pkl"

POSITION_GROUPS = {
    "GK":  ["G"],
    "DEF": ["D"],
    "MID": ["M"],
    "FWD": ["F"],
}

FEATURES_BY_GROUP = {
    "GK": [
        "matches_played", "total_minutes", "avg_rating",
        "passes_p90", "pass_accuracy",
        "total_injuries", "total_days_absent",
        "total_matches_missed", "avg_days_per_injury",
    ],
    "DEF": [
        "matches_played", "total_minutes", "avg_rating",
        "passes_p90", "pass_accuracy", "key_passes_p90",
        "duels_won_p90", "interceptions_p90", "tackles_p90",
        "goals_p90", "assists_p90",
        "total_injuries", "total_days_absent",
        "total_matches_missed", "avg_days_per_injury",
    ],
    "MID": [
        "matches_played", "total_minutes", "avg_rating",
        "goals_p90", "assists_p90", "shots_p90",
        "xg_p90", "xa_p90",
        "passes_p90", "key_passes_p90", "pass_accuracy",
        "duels_won_p90", "interceptions_p90", "tackles_p90",
        "total_injuries", "total_days_absent",
        "total_matches_missed", "avg_days_per_injury",
    ],
    "FWD": [
        "matches_played", "total_minutes", "avg_rating",
        "goals_p90", "assists_p90", "shots_p90",
        "xg_p90", "xa_p90",
        "passes_p90", "key_passes_p90", "pass_accuracy",
        "duels_won_p90",
        "total_injuries", "total_days_absent",
        "total_matches_missed", "avg_days_per_injury",
    ],
}

TARGET = "log_target_value"


def get_position_group(position: str) -> str:
    for group, positions in POSITION_GROUPS.items():
        if position in positions:
            return group
    return "MID"


def evaluate_model(model, X, y, group_name: str):
    kf = KFold(n_splits=5, shuffle=True, random_state=42)
    mae_scores, rmse_scores, r2_scores = [], [], []

    for fold, (train_idx, val_idx) in enumerate(kf.split(X)):
        X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]
        model.fit(X_train, y_train)
        y_pred = model.predict(X_val)
        mae_scores.append(mean_absolute_error(y_val, y_pred))
        rmse_scores.append(np.sqrt(mean_squared_error(y_val, y_pred)))
        r2_scores.append(r2_score(y_val, y_pred))
        logger.info("  [%s] Fold %d — MAE: %.3f | RMSE: %.3f | R2: %.3f",
                    group_name, fold+1, mae_scores[-1], rmse_scores[-1], r2_scores[-1])

    logger.info("[%s] Media CV — MAE: %.3f | RMSE: %.3f | R2: %.3f ± %.3f",
                group_name, np.mean(mae_scores), np.mean(rmse_scores),
                np.mean(r2_scores), np.std(r2_scores))
    return np.mean(r2_scores)


def run():
    df = pd.read_csv(DATASET)
    df["position_group"] = df["position"].apply(get_position_group)

    models = {}
    shap_importances = {}

    for group, features in FEATURES_BY_GROUP.items():
        df_group = df[df["position_group"] == group].copy()
        logger.info("▶ Grupo %s — %d jugadores", group, len(df_group))

        X = df_group[features]
        y = df_group[TARGET]

        model = xgb.XGBRegressor(
            n_estimators=500,
            learning_rate=0.05,
            max_depth=6,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            n_jobs=-1,
        )

        evaluate_model(model, X, y, group)
        model.fit(X, y)

        # SHAP
        explainer = shap.TreeExplainer(model)
        shap_vals = explainer.shap_values(X)
        mean_shap = pd.DataFrame({
            "feature": features,
            "mean_shap": np.abs(shap_vals).mean(axis=0)
        }).sort_values("mean_shap", ascending=False)

        logger.info("[%s] Top 5 features:", group)
        print(mean_shap.head(5).to_string(index=False))

        shap_importances[group] = mean_shap
        models[group] = {
            "model": model,
            "features": features,
        }

    # Guardar todos los modelos en un solo archivo
    joblib.dump({
    "models": models,
    "position_groups": POSITION_GROUPS,
    }, MODEL_OUTPUT)

    logger.info("✅ Modelos guardados en %s", MODEL_OUTPUT)

    # Métricas finales en EUR
    logger.info("\n── Métricas finales en escala EUR ──")
    for group, features in FEATURES_BY_GROUP.items():
        df_group = df[df["position_group"] == group].copy()
        X = df_group[features]
        y_true = np.expm1(df_group[TARGET])
        y_pred = np.expm1(models[group]["model"].predict(X))
        mae = mean_absolute_error(y_true, y_pred)
        r2 = r2_score(y_true, y_pred)
        logger.info("[%s] MAE: €%.1fM | R2: %.3f", group, mae/1e6, r2)


if __name__ == "__main__":
    run()