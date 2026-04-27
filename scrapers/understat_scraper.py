"""
scrapers/understat_scraper.py
Extrae tiros con coordenadas xG de Understat para todas las ligas configuradas.
"""
import json
import logging
import re
from typing import Optional

import pandas as pd
from sqlalchemy.orm import Session as DBSession

from config import DEFAULT_HEADERS, UNDERSTAT_LEAGUES
from db.models import DimMatch, FactShot, get_engine, create_all_tables
from utils.helpers import build_session, safe_get, parse_minute
from utils.player_matcher import get_canonical_names, match_player

logger = logging.getLogger(__name__)
BASE_URL = "https://understat.com"


# ── Extracción ────────────────────────────────────────────────

def fetch_league_matches(league: str, season: int, session) -> list[dict]:
    try:
        from understatapi import UnderstatClient
        
        logger.info("Liga recibida: '%s'", league)
        
        with UnderstatClient() as understat:
            matches = understat.league(league).get_match_data(season=str(season))
            return matches
            
    except Exception as e:
        logger.error("Error en fetch_league_matches: %s", e)
        return []

def fetch_match_shots(match_id: str, session) -> Optional[pd.DataFrame]:
    try:
        from understatapi import UnderstatClient
        
        with UnderstatClient() as understat:
            shots_data = understat.match(match_id).get_shot_data()
        
        rows = shots_data.get("h", []) + shots_data.get("a", [])
        if not rows:
            return None

        df = pd.DataFrame(rows)
        df["x"] = df["X"].astype(float).mul(105).round(4)
        df["y"] = df["Y"].astype(float).mul(68).round(4)
        df["minute"] = df["minute"].apply(parse_minute)

        return df[[
            "player", "player_id", "minute",
            "x", "y", "xG", "result", "shotType", "situation", "h_a",
        ]].rename(columns={
            "xG": "xg",
            "shotType": "shot_type",
            "player_id": "understat_player_id",
        })

    except Exception as e:
        logger.warning("Error obteniendo tiros para match_id=%s: %s", match_id, e)
        return None

# ── Carga en BD ───────────────────────────────────────────────

def upsert_match(db: DBSession, match_data: dict, league: str, season: int) -> int:
    """Inserta o recupera un partido en dim_match. Devuelve match_id interno."""
    source_id = str(match_data.get("id", ""))
    existing = db.query(DimMatch).filter_by(
        source="understat", source_match_id=source_id
    ).first()
    if existing:
        return existing.match_id

    match = DimMatch(
        date=pd.to_datetime(match_data.get("datetime"), errors="coerce").date()
             if match_data.get("datetime") else None,
        competition=league,
        season=str(season),
        home_team=match_data.get("h", {}).get("title", ""),
        away_team=match_data.get("a", {}).get("title", ""),
        home_score=match_data.get("goals", {}).get("h"),
        away_score=match_data.get("goals", {}).get("a"),
        source="understat",
        source_match_id=source_id,
    )
    db.add(match)
    db.commit()
    db.refresh(match)
    return match.match_id


def load_shots(
    db: DBSession,
    df: pd.DataFrame,
    match_id: int,
    canonical_map: dict[str, int],
):
    """Vuelca los tiros de un partido en fact_shots."""
    for _, row in df.iterrows():
        player_id = match_player(
            incoming_name=row["player"],
            source_system="understat",
            source_id=str(row["understat_player_id"]),
            canonical_map=canonical_map,
            db=db,
        )

        shot = FactShot(
            match_id=match_id,
            player_id=player_id,
            minute=int(row["minute"]),
            x=float(row["x"]),
            y=float(row["y"]),
            xg=float(row["xg"]) if row["xg"] else None,
            result=str(row["result"]),
            shot_type=str(row["shot_type"]),
            situation=str(row["situation"]),
            source="understat",
        )

        # Evitar duplicados
        exists = db.query(FactShot).filter_by(
            match_id=match_id,
            player_id=player_id,
            minute=int(row["minute"]),
            source="understat",
        ).first()
        if not exists:
            db.add(shot)

    db.commit()


# ── Punto de entrada ──────────────────────────────────────────

def run(db_url: str, leagues: Optional[list[str]] = None, seasons: Optional[list[int]] = None):
    """
    Ejecuta el scraper completo de Understat.

    Args:
        db_url:  Cadena de conexión SQLAlchemy.
        leagues: Lista de nombres de liga (claves de UNDERSTAT_LEAGUES).
                 Si es None, usa todas las configuradas.
        seasons: Lista de años de temporada (ej: [2022, 2023]).
                 Si es None, usa [2023].
    """
    from sqlalchemy.orm import sessionmaker

    engine = get_engine(db_url)
    create_all_tables(engine)
    SessionLocal = sessionmaker(bind=engine)

    session_http = build_session(DEFAULT_HEADERS)
    leagues = leagues or list(UNDERSTAT_LEAGUES.keys())
    seasons = seasons or [2023]

    for league_name in leagues:
        league_slug = UNDERSTAT_LEAGUES.get(league_name)
        if not league_slug:
            logger.warning("Liga no reconocida: %s", league_name)
            continue

        for season in seasons:
            logger.info("▶ Procesando %s temporada %s", league_name, season)
            matches = fetch_league_matches(league_slug, season, session_http)
            logger.info("  %d partidos encontrados", len(matches))

            for match_data in matches:
                match_source_id = str(match_data.get("id", ""))
                if not match_source_id:
                    continue

                with SessionLocal() as db:
                    canonical_map = get_canonical_names(db)
                    internal_match_id = upsert_match(db, match_data, league_name, season)

                    df_shots = fetch_match_shots(match_source_id, session_http)
                    if df_shots is not None and not df_shots.empty:
                        load_shots(db, df_shots, internal_match_id, canonical_map)
                        logger.info(
                            "    ✅ Partido %s — %d tiros cargados",
                            match_source_id, len(df_shots),
                        )
                    else:
                        logger.info("    ⚠ Partido %s sin tiros", match_source_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from config import DB_URL
    run(DB_URL, leagues=["La Liga"], seasons=[2023])
