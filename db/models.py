"""
db/models.py — Definición de tablas con SQLAlchemy 2.x
"""
from sqlalchemy import (
    create_engine, Column, Integer, SmallInteger, String,
    Date, DateTime, Numeric, Boolean, ForeignKey, UniqueConstraint, text
)
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


# ── Dimensiones ──────────────────────────────────────────────

class DimTeam(Base):
    __tablename__ = "dim_team"

    team_id    = Column(Integer, primary_key=True, autoincrement=True)
    name       = Column(String(100), nullable=False, unique=True)
    country    = Column(String(80))
    created_at = Column(DateTime, server_default=func.now())


class DimPlayer(Base):
    __tablename__ = "dim_player"

    player_id        = Column(Integer, primary_key=True, autoincrement=True)
    name_canonical   = Column(String(150), nullable=False)
    nationality      = Column(String(80))
    birth_date       = Column(Date)
    position         = Column(String(50))
    id_understat     = Column(Integer)
    id_sofascore     = Column(Integer)
    id_transfermarkt = Column(Integer)
    id_statsbomb     = Column(String(50))
    id_whoscored     = Column(Integer)
    created_at       = Column(DateTime, server_default=func.now())


class DimMatch(Base):
    __tablename__ = "dim_match"

    match_id        = Column(Integer, primary_key=True, autoincrement=True)
    date            = Column(Date)
    competition     = Column(String(100))
    season          = Column(String(20))
    home_team       = Column(String(100))
    away_team       = Column(String(100))
    home_score      = Column(SmallInteger)
    away_score      = Column(SmallInteger)
    source          = Column(String(50))
    source_match_id = Column(String(50))


# ── Hechos ────────────────────────────────────────────────────

class FactShot(Base):
    __tablename__ = "fact_shots"
    __table_args__ = (
        UniqueConstraint("match_id", "player_id", "minute", "source",
                         name="idx_shots_unique"),
    )

    shot_id   = Column(Integer, primary_key=True, autoincrement=True)
    match_id  = Column(Integer, ForeignKey("dim_match.match_id"))
    player_id = Column(Integer, ForeignKey("dim_player.player_id"))
    team_id   = Column(Integer, ForeignKey("dim_team.team_id"))
    minute    = Column(SmallInteger)
    x         = Column(Numeric(6, 4))
    y         = Column(Numeric(6, 4))
    xg        = Column(Numeric(6, 4))
    result    = Column(String(30))
    shot_type = Column(String(30))
    situation = Column(String(50))
    source    = Column(String(30))


class FactEvent(Base):
    __tablename__ = "fact_events"

    event_id   = Column(Integer, primary_key=True, autoincrement=True)
    match_id   = Column(Integer, ForeignKey("dim_match.match_id"))
    player_id  = Column(Integer, ForeignKey("dim_player.player_id"))
    team_id    = Column(Integer, ForeignKey("dim_team.team_id"))
    event_type = Column(String(50))
    minute     = Column(SmallInteger)
    second     = Column(SmallInteger)
    x          = Column(Numeric(6, 4))
    y          = Column(Numeric(6, 4))
    end_x      = Column(Numeric(6, 4))
    end_y      = Column(Numeric(6, 4))
    outcome    = Column(String(50))
    source     = Column(String(30))


class FactInjury(Base):
    __tablename__ = "fact_injuries"

    injury_id      = Column(Integer, primary_key=True, autoincrement=True)
    player_id      = Column(Integer, ForeignKey("dim_player.player_id"))
    season         = Column(String(20))
    injury_type    = Column(String(200))
    date_from      = Column(Date)
    date_until     = Column(Date)  # NULL si sigue lesionado
    days_absent    = Column(Integer)
    matches_missed = Column(SmallInteger)


class PlayerMatchReview(Base):
    __tablename__ = "player_match_review"

    review_id           = Column(Integer, primary_key=True, autoincrement=True)
    source_name         = Column(String(150))
    source_system       = Column(String(50))
    source_id           = Column(String(50))
    suggested_canonical = Column(String(150))
    similarity_score    = Column(SmallInteger)
    resolved            = Column(Boolean, default=False)
    player_id_assigned  = Column(Integer, ForeignKey("dim_player.player_id"))


# ── Helpers ───────────────────────────────────────────────────

def get_engine(db_url: str):
    """Crea y devuelve un engine de SQLAlchemy."""
    return create_engine(db_url, echo=False)


def create_all_tables(engine):
    """Crea todas las tablas si no existen."""
    Base.metadata.create_all(engine)
    print("✅ Tablas creadas correctamente.")
