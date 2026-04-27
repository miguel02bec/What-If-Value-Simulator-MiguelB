"""
build_dataset.py
Construye el dataset unificado para el modelo de predicción de valor de mercado.
Fuentes: SofaScore (estadísticas) + Transfermarkt (valores de mercado) + DB (lesiones)
"""
import logging
import sqlite3
import pandas as pd
import numpy as np
from rapidfuzz import process, fuzz

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = "football.db"
SOFASCORE_STATS = "sofascore_player_stats.csv"
TRANSFERMARKT_VALUES = "transfermarkt_values.csv"
OUTPUT = "dataset_model.csv"


# ── 1. Cargar estadísticas de SofaScore ──────────────────────

def load_sofascore_stats() -> pd.DataFrame:
    logger.info("Cargando estadísticas de SofaScore...")
    df = pd.read_csv(SOFASCORE_STATS)
    
    # Filtrar jugadores con minutos significativos
    df = df[df["minutes_played"] >= 30].copy()
    
    # Calcular métricas por 90 minutos
    df["goals_p90"] = df["goals"] / df["minutes_played"] * 90
    df["assists_p90"] = df["goal_assist"] / df["minutes_played"] * 90
    df["shots_p90"] = df["total_shots"] / df["minutes_played"] * 90
    df["xg_p90"] = df["xg"] / df["minutes_played"] * 90
    df["xa_p90"] = df["xa"] / df["minutes_played"] * 90
    df["passes_p90"] = df["total_pass"] / df["minutes_played"] * 90
    df["key_passes_p90"] = df["key_pass"] / df["minutes_played"] * 90
    df["duels_won_p90"] = df["duel_won"] / df["minutes_played"] * 90
    df["interceptions_p90"] = df["interception_won"] / df["minutes_played"] * 90
    df["tackles_p90"] = df["total_tackle"] / df["minutes_played"] * 90
    
    # Pass accuracy
    df["pass_accuracy"] = df["accurate_pass"] / df["total_pass"].replace(0, np.nan)
    
    # Agregar por jugador
    agg = df.groupby(["sofascore_player_id", "player_name", "position"]).agg(
        matches_played=("sofascore_match_id", "count"),
        total_minutes=("minutes_played", "sum"),
        avg_rating=("rating", "mean"),
        goals_p90=("goals_p90", "mean"),
        assists_p90=("assists_p90", "mean"),
        shots_p90=("shots_p90", "mean"),
        xg_p90=("xg_p90", "mean"),
        xa_p90=("xa_p90", "mean"),
        passes_p90=("passes_p90", "mean"),
        key_passes_p90=("key_passes_p90", "mean"),
        pass_accuracy=("pass_accuracy", "mean"),
        duels_won_p90=("duels_won_p90", "mean"),
        interceptions_p90=("interceptions_p90", "mean"),
        tackles_p90=("tackles_p90", "mean"),
        avg_xg=("xg", "mean"),
        avg_xa=("xa", "mean"),
        market_value_sofascore=("proposed_market_value", "median"),
    ).reset_index()
    
    # Filtrar jugadores con suficientes partidos
    agg = agg[agg["matches_played"] >= 3]
    
    logger.info("  %d jugadores con estadísticas agregadas", len(agg))
    return agg


# ── 2. Cargar valores de mercado de Transfermarkt ────────────

def load_transfermarkt_values() -> pd.DataFrame:
    logger.info("Cargando valores de mercado de Transfermarkt...")
    df = pd.read_csv(TRANSFERMARKT_VALUES)
    df = df[df["market_value_eur"].notna()].copy()
    logger.info("  %d jugadores con valor de mercado", len(df))
    return df


# ── 3. Cargar lesiones desde la DB ──────────────────────────

def load_injury_features() -> pd.DataFrame:
    logger.info("Cargando historial de lesiones...")
    conn = sqlite3.connect(DB_PATH)
    
    query = """
        SELECT 
            p.name_canonical,
            COUNT(i.injury_id) as total_injuries,
            SUM(i.days_absent) as total_days_absent,
            SUM(i.matches_missed) as total_matches_missed,
            AVG(i.days_absent) as avg_days_per_injury
        FROM fact_injuries i
        JOIN dim_player p ON i.player_id = p.player_id
        GROUP BY p.player_id, p.name_canonical
    """
    df = pd.read_sql(query, conn)
    conn.close()
    logger.info("  %d jugadores con historial de lesiones", len(df))
    return df


# ── 4. Fuzzy matching entre SofaScore y Transfermarkt ────────

def fuzzy_match_players(
    df_stats: pd.DataFrame,
    df_values: pd.DataFrame,
    threshold: int = 80
) -> pd.DataFrame:
    logger.info("Cruzando jugadores por nombre fuzzy...")
    
    tm_names = df_values["name"].tolist()
    
    matched_values = []
    for _, row in df_stats.iterrows():
        player_name = row["player_name"]
        result = process.extractOne(
            player_name, tm_names,
            scorer=fuzz.token_sort_ratio
        )
        if result and result[1] >= threshold:
            tm_row = df_values[df_values["name"] == result[0]].iloc[0]
            matched_values.append({
                "sofascore_player_id": row["sofascore_player_id"],
                "tm_name": result[0],
                "match_score": result[1],
                "market_value_eur": tm_row["market_value_eur"],
                "team_tm": tm_row["team"],
                "position_tm": tm_row["position"],
                "nationality": tm_row["nationality"],
            })
        else:
            matched_values.append({
                "sofascore_player_id": row["sofascore_player_id"],
                "tm_name": None,
                "match_score": result[1] if result else 0,
                "market_value_eur": None,
                "team_tm": None,
                "position_tm": None,
                "nationality": None,
            })
    
    df_matched = pd.DataFrame(matched_values)
    merged = df_stats.merge(df_matched, on="sofascore_player_id", how="left")
    
    matched = merged["market_value_eur"].notna().sum()
    logger.info("  %d/%d jugadores cruzados con Transfermarkt", matched, len(merged))
    
    return merged


# ── 5. Fuzzy matching con lesiones ───────────────────────────

def add_injury_features(
    df: pd.DataFrame,
    df_injuries: pd.DataFrame,
    threshold: int = 85
) -> pd.DataFrame:
    logger.info("Añadiendo features de lesiones...")
    
    injury_names = df_injuries["name_canonical"].tolist()
    
    injury_data = []
    for _, row in df.iterrows():
        result = process.extractOne(
            row["player_name"], injury_names,
            scorer=fuzz.token_sort_ratio
        )
        if result and result[1] >= threshold:
            inj_row = df_injuries[df_injuries["name_canonical"] == result[0]].iloc[0]
            injury_data.append({
                "sofascore_player_id": row["sofascore_player_id"],
                "total_injuries": inj_row["total_injuries"],
                "total_days_absent": inj_row["total_days_absent"],
                "total_matches_missed": inj_row["total_matches_missed"],
                "avg_days_per_injury": inj_row["avg_days_per_injury"],
            })
        else:
            injury_data.append({
                "sofascore_player_id": row["sofascore_player_id"],
                "total_injuries": 0,
                "total_days_absent": 0,
                "total_matches_missed": 0,
                "avg_days_per_injury": 0,
            })
    
    df_inj = pd.DataFrame(injury_data)
    result = df.merge(df_inj, on="sofascore_player_id", how="left")
    logger.info("  Features de lesiones añadidas")
    return result


# ── 6. Construir dataset final ────────────────────────────────

def build_dataset():
    df_stats = load_sofascore_stats()
    df_values = load_transfermarkt_values()
    df_injuries = load_injury_features()
    
    # Cruzar con Transfermarkt
    df = fuzzy_match_players(df_stats, df_values, threshold=80)
    
    # Añadir lesiones
    df = add_injury_features(df, df_injuries, threshold=85)
    
    # Usar valor de Transfermarkt como target principal
    # Si no hay match, usar SofaScore como fallback
    df["target_value_eur"] = df["market_value_eur"].fillna(df["market_value_sofascore"])
    
    # Filtrar jugadores sin valor de mercado
    df_final = df[df["target_value_eur"].notna()].copy()
    
    # Log transformación del target (valores de mercado tienen distribución log-normal)
    df_final["log_target_value"] = np.log1p(df_final["target_value_eur"])
    
    # Seleccionar columnas finales
    feature_cols = [
        "sofascore_player_id", "player_name", "position",
        "matches_played", "total_minutes",
        "avg_rating", "goals_p90", "assists_p90", "shots_p90",
        "xg_p90", "xa_p90", "passes_p90", "key_passes_p90",
        "pass_accuracy", "duels_won_p90", "interceptions_p90", "tackles_p90",
        "total_injuries", "total_days_absent", "total_matches_missed",
        "avg_days_per_injury",
        "team_tm", "position_tm", "nationality",
        "market_value_eur", "market_value_sofascore",
        "target_value_eur", "log_target_value",
    ]
    
    df_final = df_final[feature_cols]
    df_final.to_csv(OUTPUT, index=False)
    
    logger.info("✅ Dataset guardado en %s", OUTPUT)
    logger.info("   Shape: %s", df_final.shape)
    logger.info("   Jugadores con valor TM: %d", df_final["market_value_eur"].notna().sum())
    logger.info("   Valor medio: €%.1fM", df_final["target_value_eur"].mean() / 1e6)
    logger.info("   Posiciones: %s", df_final["position"].value_counts().to_dict())
    
    return df_final


if __name__ == "__main__":
    df = build_dataset()
    print(df.describe())