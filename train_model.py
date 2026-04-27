"""
train_model.py
Entrena un modelo XGBoost/LightGBM para predecir valor de mercado de jugadores.
Incluye validación cruzada, métricas y SHAP para interpretabilidad.
"""
import logging
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
#import shap
import xgboost as xgb
import lightgbm as lgb
from sklearn.model_selection import KFold, cross_val_score
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.preprocessing import LabelEncoder
import joblib

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATASET = "dataset_model.csv"
MODEL_OUTPUT = "model_market_value.pkl"

FEATURES = [
    "position",
    "matches_played", "total_minutes",
    "avg_rating",
    "goals_p90", "assists_p90", "shots_p90",
    "xg_p90", "xa_p90",
    "passes_p90", "key_passes_p90", "pass_accuracy",
    "duels_won_p90", "interceptions_p90", "tackles_p90",
    "total_injuries", "total_days_absent",
    "total_matches_missed", "avg_days_per_injury",
]

TARGET = "log_target_value"


def load_data() -> tuple[pd.DataFrame, pd.Series]:
    logger.info("Cargando dataset...")
    df = pd.read_csv(DATASET)
    
    # Encode posición
    le = LabelEncoder()
    df["position"] = le.fit_transform(df["position"])
    
    X = df[FEATURES]
    y = df[TARGET]
    
    logger.info("  Shape X: %s", X.shape)
    logger.info("  Target — media: %.2f, std: %.2f", y.mean(), y.std())
    logger.info("  Posiciones: %s", dict(zip(le.classes_, le.transform(le.classes_))))
    
    return X, y, le


def evaluate_model(model, X, y, model_name: str):
    """Evaluación con validación cruzada 5-fold."""
    logger.info("Evaluando %s con CV 5-fold...", model_name)
    
    kf = KFold(n_splits=5, shuffle=True, random_state=42)
    
    mae_scores = []
    rmse_scores = []
    r2_scores = []
    
    for fold, (train_idx, val_idx) in enumerate(kf.split(X)):
        X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]
        
        model.fit(X_train, y_train)
        y_pred = model.predict(X_val)
        
        mae = mean_absolute_error(y_val, y_pred)
        rmse = np.sqrt(mean_squared_error(y_val, y_pred))
        r2 = r2_score(y_val, y_pred)
        
        mae_scores.append(mae)
        rmse_scores.append(rmse)
        r2_scores.append(r2)
        
        logger.info("  Fold %d — MAE: %.3f | RMSE: %.3f | R2: %.3f", fold+1, mae, rmse, r2)
    
    logger.info("%s — Media CV:", model_name)
    logger.info("  MAE:  %.3f ± %.3f", np.mean(mae_scores), np.std(mae_scores))
    logger.info("  RMSE: %.3f ± %.3f", np.mean(rmse_scores), np.std(rmse_scores))
    logger.info("  R2:   %.3f ± %.3f", np.mean(r2_scores), np.std(r2_scores))
    
    return np.mean(r2_scores)


def train_final_model(X, y):
    """Entrena el modelo final con todos los datos."""
    logger.info("Entrenando modelo final XGBoost...")
    
    model = xgb.XGBRegressor(
        n_estimators=500,
        learning_rate=0.05,
        max_depth=6,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X, y)
    return model


def compute_shap(model, X):
    """Calcula SHAP values y guarda el gráfico."""
    logger.info("Calculando SHAP values...")
    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X)
    
    plt.figure(figsize=(10, 8))
    shap.summary_plot(shap_values, X, show=False)
    plt.tight_layout()
    plt.savefig("shap_summary.png", dpi=150)
    plt.close()
    logger.info("  SHAP summary guardado en shap_summary.png")
    
    # Feature importance media
    mean_shap = pd.DataFrame({
        "feature": X.columns,
        "mean_shap": np.abs(shap_values).mean(axis=0)
    }).sort_values("mean_shap", ascending=False)
    
    logger.info("Top 10 features por importancia SHAP:")
    print(mean_shap.head(10).to_string(index=False))
    mean_shap.to_csv("shap_importance.csv", index=False)
    
    return shap_values


def run():
    X, y, le = load_data()
    
    # Comparar XGBoost vs LightGBM
    xgb_model = xgb.XGBRegressor(
        n_estimators=300, learning_rate=0.05, max_depth=6,
        subsample=0.8, colsample_bytree=0.8, random_state=42, n_jobs=-1
    )
    lgb_model = lgb.LGBMRegressor(
        n_estimators=300, learning_rate=0.05, max_depth=6,
        subsample=0.8, colsample_bytree=0.8, random_state=42, n_jobs=-1
    )
    
    r2_xgb = evaluate_model(xgb_model, X, y, "XGBoost")
    r2_lgb = evaluate_model(lgb_model, X, y, "LightGBM")
    
    # Elegir el mejor
    best_name = "XGBoost" if r2_xgb >= r2_lgb else "LightGBM"
    logger.info("Mejor modelo: %s", best_name)
    
    # Entrenar modelo final
    final_model = train_final_model(X, y)
    
    # SHAP
    shap_values = compute_shap(final_model, X)
    
    # Guardar modelo
    joblib.dump({"model": final_model, "label_encoder": le, "features": FEATURES}, MODEL_OUTPUT)
    logger.info("✅ Modelo guardado en %s", MODEL_OUTPUT)
    
    # Predicciones en escala original
    y_pred_log = final_model.predict(X)
    y_pred_eur = np.expm1(y_pred_log)
    y_true_eur = np.expm1(y)
    
    mae_eur = mean_absolute_error(y_true_eur, y_pred_eur)
    r2_final = r2_score(y_true_eur, y_pred_eur)
    logger.info("Modelo final en escala EUR:")
    logger.info("  MAE: €%.1fM", mae_eur / 1e6)
    logger.info("  R2:  %.3f", r2_final)


if __name__ == "__main__":
    run()