"""
scrapers/statsbomb_loader.py
Carga eventos completos de StatsBomb Open Data (GitHub).
Usa la librería oficial statsbombpy — no requiere scraping.
"""
import logging
from typing import Optional

import pandas as pd
from sqlalchemy.orm import Session as DBSession, sessionmaker

from config import DB_URL, STATSBOMB_COMPETITIONS
from db.models import DimMatch, DimPlayer, FactEvent, FactShot, get_engine, create_all_tables
from utils.helpers import statsbomb_to_meters, parse_minute
from utils.player_matcher import get_canonical_names, match_player, upsert_player

logger = logging.getLogger(__name__)

try:
    from statsbombpy import sb
except ImportError:
    raise ImportError("Instala statsbombpy: pip install statsbombpy")


# ── Helpers ───────────────────────────────────────────────────

def _extract_coord(value, index: int) -> Optional[float]:
    """Extrae X o Y de una celda que puede ser lista o None."""
    try:
        if isinstance(value, list) and len(value) > index:
            return float(value[index])
    except (TypeError, ValueError):
        pass
    return None


def _parse_location(row: pd.Series, col: str) -> tuple[Optional[float], Optional[float]]:
    """Devuelve (x_metros, y_metros) desde una columna de coordenadas StatsBomb."""
    loc = row.get(col)
    x = _extract_coord(loc, 0)
    y = _extract_coord(loc, 1)
    if x is not None and y is not None:
        return statsbomb_to_meters(x, y)
    return None, None


# ── Carga en BD ───────────────────────────────────────────────

def upsert_match_sb(db: DBSession, match_row: pd.Series, competition_name: str) -> int:
    """Inserta o recupera un partido de StatsBomb. Devuelve match_id interno."""
    source_id = str(match_row["match_id"])
    existing = db.query(DimMatch).filter_by(
        source="statsbomb", source_match_id=source_id
    ).first()
    if existing:
        return existing.match_id

    match = DimMatch(
        date=pd.to_datetime(match_row.get("match_date"), errors="coerce").date()
             if match_row.get("match_date") else None,
        competition=competition_name,
        season=str(match_row.get("season_name", "")),
        home_team=str(match_row.get("home_team", "")),
        away_team=str(match_row.get("away_team", "")),
        home_score=match_row.get("home_score"),
        away_score=match_row.get("away_score"),
        source="statsbomb",
        source_match_id=source_id,
    )
    db.add(match)
    db.commit()
    db.refresh(match)
    return match.match_id


def load_events_sb(
    db: DBSession,
    events: pd.DataFrame,
    match_id: int,
    canonical_map: dict[str, int],
):
    """Carga todos los eventos de un partido en fact_events y los tiros en fact_shots."""
    shots_buffer = []
    events_buffer = []

    for _, row in events.iterrows():
        player_name = row.get("player", None)
        if pd.isna(player_name) or not player_name:
            player_id = None
        else:
            player_id = match_player(
                incoming_name=str(player_name),
                source_system="statsbomb",
                source_id=str(row.get("player_id", "")),
                canonical_map=canonical_map,
                db=db,
            )
            # Si no hay match, crea el jugador en dim_player automáticamente
            if player_id is None:
                player_id = upsert_player(
                    db,
                    name_canonical=str(player_name),
                    id_statsbomb=str(row.get("player_id", "")),
                )
                canonical_map[str(player_name)] = player_id

        x, y = _parse_location(row, "location")
        end_x, end_y = _parse_location(row, "pass_end_location")

        event_type = str(row.get("type", ""))
        minute = parse_minute(row.get("minute", 0))
        second = int(row.get("second", 0)) if not pd.isna(row.get("second", None)) else None

        # ── Evento genérico ──
        events_buffer.append(FactEvent(
            match_id=match_id,
            player_id=player_id,
            event_type=event_type,
            minute=minute,
            second=second,
            x=x,
            y=y,
            end_x=end_x,
            end_y=end_y,
            outcome=str(row.get("pass_outcome", row.get("shot_outcome", ""))),
            source="statsbomb",
        ))

        # ── Tiro específico ──
        if event_type == "Shot":
            sx, sy = _parse_location(row, "location")
            shots_buffer.append(FactShot(
                match_id=match_id,
                player_id=player_id,
                minute=minute,
                x=sx,
                y=sy,
                xg=float(row["shot_statsbomb_xg"]) if not pd.isna(row.get("shot_statsbomb_xg")) else None,
                result=str(row.get("shot_outcome", "")),
                shot_type=str(row.get("shot_body_part", "")),
                situation=str(row.get("shot_type", "")),
                source="statsbomb",
            ))

    db.bulk_save_objects(events_buffer)

    inserted_shots = 0
    for shot in shots_buffer:
        try:
            db.add(shot)
            db.flush()
            inserted_shots += 1
        except Exception:
            db.rollback()

    db.commit()
    logger.info("  ✅ %d eventos y %d tiros cargados", len(events_buffer), inserted_shots)


# ── Punto de entrada ──────────────────────────────────────────

def run(
    db_url: str,
    competitions: Optional[list[str]] = None,
):
    """
    Carga datos de StatsBomb Open Data en la BD.

    Args:
        db_url:       Cadena de conexión SQLAlchemy.
        competitions: Lista de nombres de competición (claves de STATSBOMB_COMPETITIONS).
                      Si es None, usa todas las configuradas.
    """
    engine = get_engine(db_url)
    create_all_tables(engine)
    SessionLocal = sessionmaker(bind=engine)

    competitions = competitions or list(STATSBOMB_COMPETITIONS.keys())

    for comp_name in competitions:
        comp_cfg = STATSBOMB_COMPETITIONS.get(comp_name)
        if not comp_cfg:
            logger.warning("Competición no configurada: %s", comp_name)
            continue

        logger.info("▶ Cargando %s (competition_id=%d, season_id=%d)",
                    comp_name, comp_cfg["competition_id"], comp_cfg["season_id"])

        matches = sb.matches(
            competition_id=comp_cfg["competition_id"],
            season_id=comp_cfg["season_id"],
        )
        logger.info("  %d partidos encontrados", len(matches))

        for _, match_row in matches.iterrows():
            sb_match_id = match_row["match_id"]
            logger.info("  Procesando partido %s vs %s (%s)",
                        match_row["home_team"], match_row["away_team"], sb_match_id)

            try:
                events = sb.events(match_id=sb_match_id)
            except Exception as e:
                logger.error("  Error al obtener eventos del partido %s: %s", sb_match_id, e)
                continue

            with SessionLocal() as db:
                canonical_map = get_canonical_names(db)
                internal_id = upsert_match_sb(db, match_row, comp_name)
                load_events_sb(db, events, internal_id, canonical_map)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(DB_URL, competitions=["La Liga"])
