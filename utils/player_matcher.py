"""
utils/player_matcher.py — Matching de nombres de jugadores entre fuentes
"""
import logging
from typing import Optional

import pandas as pd
from sqlalchemy.orm import Session as DBSession
from thefuzz import process

from config import MATCH_THRESHOLD_AUTO, MATCH_THRESHOLD_REVIEW
from db.models import DimPlayer, PlayerMatchReview

logger = logging.getLogger(__name__)


def get_canonical_names(db: DBSession) -> dict[str, int]:
    """
    Devuelve {name_canonical: player_id} de todos los jugadores en dim_player.
    """
    players = db.query(DimPlayer.name_canonical, DimPlayer.player_id).all()
    return {p.name_canonical: p.player_id for p in players}


def match_player(
    incoming_name: str,
    source_system: str,
    source_id: str,
    canonical_map: dict[str, int],
    db: DBSession,
) -> Optional[int]:
    """
    Intenta encontrar el player_id canónico para un nombre entrante.

    - score >= MATCH_THRESHOLD_AUTO  → acepta automáticamente
    - score >= MATCH_THRESHOLD_REVIEW → guarda en revisión manual
    - score <  MATCH_THRESHOLD_REVIEW → descarta (requiere inserción manual)

    Devuelve player_id si hay match automático, None en caso contrario.
    """
    if not canonical_map:
        logger.warning("El mapa de jugadores canónicos está vacío.")
        return None

    best_match, score = process.extractOne(incoming_name, list(canonical_map.keys()))

    if score >= MATCH_THRESHOLD_AUTO:
        logger.debug("Match automático: '%s' → '%s' (score %d)", incoming_name, best_match, score)
        return canonical_map[best_match]

    # Guardar en tabla de revisión
    _save_review(
        db=db,
        source_name=incoming_name,
        source_system=source_system,
        source_id=source_id,
        suggested_canonical=best_match,
        similarity_score=score,
    )
    logger.info(
        "Sin match automático para '%s'. Guardado en revisión (score %d).",
        incoming_name, score,
    )
    return None


def _save_review(
    db: DBSession,
    source_name: str,
    source_system: str,
    source_id: str,
    suggested_canonical: str,
    similarity_score: int,
):
    """Inserta un caso de revisión manual si no existe ya."""
    existing = (
        db.query(PlayerMatchReview)
        .filter_by(source_name=source_name, source_system=source_system)
        .first()
    )
    if existing:
        return  # ya registrado

    review = PlayerMatchReview(
        source_name=source_name,
        source_system=source_system,
        source_id=str(source_id),
        suggested_canonical=suggested_canonical,
        similarity_score=similarity_score,
        resolved=False,
    )
    db.add(review)
    db.commit()


def upsert_player(db: DBSession, **kwargs) -> int:
    """
    Inserta un jugador en dim_player si no existe (por name_canonical).
    Devuelve el player_id.
    """
    name = kwargs.get("name_canonical", "").strip()
    player = db.query(DimPlayer).filter_by(name_canonical=name).first()
    if player:
        # Actualiza IDs externos si vienen informados
        for field in ("id_understat", "id_sofascore", "id_transfermarkt",
                      "id_statsbomb", "id_whoscored"):
            if kwargs.get(field) and not getattr(player, field):
                setattr(player, field, kwargs[field])
        db.commit()
        return player.player_id

    player = DimPlayer(**kwargs)
    db.add(player)
    db.commit()
    db.refresh(player)
    return player.player_id


def get_pending_reviews(db: DBSession) -> pd.DataFrame:
    """Devuelve los casos pendientes de revisión manual como DataFrame."""
    rows = db.query(PlayerMatchReview).filter_by(resolved=False).all()
    return pd.DataFrame([
        {
            "review_id": r.review_id,
            "source_name": r.source_name,
            "source_system": r.source_system,
            "source_id": r.source_id,
            "suggested_canonical": r.suggested_canonical,
            "similarity_score": r.similarity_score,
        }
        for r in rows
    ])
