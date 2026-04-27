"""
scrapers/transfermarkt_scraper.py
Extrae historial de lesiones y valores de mercado de Transfermarkt.
"""
import logging
import re
import time
import random
from typing import Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session as DBSession, sessionmaker

from config import DB_URL, DEFAULT_HEADERS
from db.models import DimPlayer, FactInjury, get_engine, create_all_tables
from utils.helpers import build_session, safe_get, parse_date, clean_integer
from utils.player_matcher import upsert_player, get_canonical_names

logger = logging.getLogger(__name__)
BASE_URL = "https://www.transfermarkt.es"

TEAM_SLUGS = {
    "Real Madrid":       ("real-madrid", 418),
    "Barcelona":         ("fc-barcelona", 131),
    "Atletico Madrid":   ("atletico-madrid", 13),
    "Manchester City":   ("manchester-city", 281),
    "Arsenal":           ("fc-arsenal", 11),
    "Bayern Munich":     ("fc-bayern-munchen", 27),
    "Borussia Dortmund": ("borussia-dortmund", 16),
    "Juventus":          ("juventus-turin", 506),
    "AC Milan":          ("ac-mailand", 5),
    "Paris SG":          ("paris-saint-germain", 583),
    "Liverpool":         ("fc-liverpool", 31),
    "Chelsea":           ("fc-chelsea", 631),
    "Inter":             ("inter-mailand", 46),
    "Napoli":            ("ssc-neapel", 6195),
    "Borussia MG":       ("borussia-mgladbach", 18),
    "Sevilla":           ("fc-sevilla", 368),
    "Valencia":          ("fc-valencia", 1049),
    "Villarreal":        ("fc-villarreal", 1050),
    "Real Sociedad":     ("real-sociedad-san-sebastian", 681),
    "Athletic Club":     ("athletic-club-bilbao", 621),
    # La Liga
    "Real Betis":        ("real-betis-sevilla", 150),
    "Osasuna":           ("ca-osasuna", 331),
    "Getafe":            ("getafe-cf", 3709),
    "Rayo Vallecano":    ("rayo-vallecano", 367),
    "Celta Vigo":        ("rc-celta-vigo", 940),
    "Girona":            ("fc-girona", 12321),
    "Mallorca":          ("rcd-mallorca", 237),
    "Alaves":            ("deportivo-alaves", 1108),

    # Premier League
    "Manchester United": ("manchester-united", 985),
    "Tottenham":         ("tottenham-hotspur", 148),
    "Newcastle":         ("newcastle-united", 762),
    "Aston Villa":       ("aston-villa", 405),
    "West Ham":          ("west-ham-united", 379),
    "Brighton":          ("brighton-hove-albion", 1237),
    "Everton":           ("fc-everton", 29),
    "Fulham":            ("fc-fulham", 931),
    "Wolves":            ("wolverhampton-wanderers", 543),
    "Brentford":         ("brentford-fc", 1763),

    # Bundesliga
    "Bayer Leverkusen":  ("bayer-04-leverkusen", 15),
    "RB Leipzig":        ("rb-leipzig", 23826),
    "Eintracht Frankfurt": ("eintracht-frankfurt", 24),
    "Stuttgart":         ("vfb-stuttgart", 79),
    "Wolfsburg":         ("vfl-wolfsburg", 82),
    "Freiburg":          ("sc-freiburg", 17),
    "Hoffenheim":        ("tsg-1899-hoffenheim", 533),

    # Serie A
    "Roma":              ("as-rom", 12),
    "Lazio":             ("ss-lazio", 398),
    "Atalanta":          ("atalanta-bc", 800),
    "Fiorentina":        ("acf-fiorentina", 430),
    "Torino":            ("fc-turin", 416),
    "Bologna":           ("fc-bologna", 1025),

    # Ligue 1
    "Marseille":         ("olympique-marseille", 244),
    "Lyon":              ("olympique-lyonnais", 1041),
    "Monaco":            ("as-monaco", 162),
    "Lille":             ("losc-lille", 1082),
    "Rennes":            ("stade-rennais-fc", 3),
    "Nice":              ("ogc-nice", 417),
    "Lens":              ("rc-lens", 826),
}


def parse_market_value(value_str: str) -> Optional[float]:
    """Convierte '20,00 mill. €' o '400 mil €' a float en euros."""
    if not value_str:
        return None
    value_str = value_str.replace("€", "").replace("\xa0", "").strip()
    if "mill." in value_str:
        return float(value_str.replace("mill.", "").replace(",", ".").strip()) * 1_000_000
    if "mil" in value_str:
        return float(value_str.replace("mil", "").replace(",", ".").strip()) * 1_000
    try:
        return float(value_str.replace(",", "."))
    except ValueError:
        return None


def fetch_squad(team_slug: str, team_id: int, session) -> pd.DataFrame:
    """
    Extrae la plantilla de un equipo de Transfermarkt incluyendo valor de mercado.
    """
    url = f"{BASE_URL}/{team_slug}/kader/verein/{team_id}/saison_id/2024"
    response = safe_get(session, url)
    if not response:
        return pd.DataFrame()

    soup = BeautifulSoup(response.content, "lxml")
    table = soup.find("table", class_="items")
    if not table:
        logger.warning("No se encontró tabla de plantilla en %s", url)
        return pd.DataFrame()

    players = []
    for row in table.find_all("tr", class_=["odd", "even"]):
        cols = row.find_all("td")
        if len(cols) < 5:
            continue

        name_td = row.find("td", class_="hauptlink")
        if not name_td or not name_td.find("a"):
            continue

        link = name_td.find("a")
        href = link.get("href", "")
        tm_id_match = re.search(r"/spieler/(\d+)", href)
        slug_match = re.match(r"/([^/]+)/", href)

        # Valor de mercado
        value_td = row.find("td", class_="rechts hauptlink")
        value_raw = value_td.text.strip() if value_td else ""
        market_value = parse_market_value(value_raw)

        players.append({
            "name": link.text.strip(),
            "player_slug": slug_match.group(1) if slug_match else "",
            "tm_id": int(tm_id_match.group(1)) if tm_id_match else None,
            "position": cols[4].text.strip() if len(cols) > 4 else "",
            "nationality": cols[-2].find("img")["title"] if cols[-2].find("img") else "",
            "market_value_eur": market_value,
        })

    return pd.DataFrame(players)


def fetch_player_injuries(player_slug: str, player_id: int, session) -> pd.DataFrame:
    """
    Extrae el historial completo de lesiones de un jugador.
    """
    url = f"{BASE_URL}/{player_slug}/verletzungen/spieler/{player_id}"
    response = safe_get(session, url, sleep_range=(3, 6))
    if not response:
        return pd.DataFrame()

    soup = BeautifulSoup(response.content, "lxml")
    table = soup.find("table", class_="items")
    if not table:
        logger.info("Sin historial de lesiones para player_id=%d", player_id)
        return pd.DataFrame()

    rows = table.find_all("tr", class_=["odd", "even"])
    injuries = []
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 6:
            continue
        injuries.append({
            "season":         cols[0].text.strip(),
            "injury_type":    cols[1].text.strip(),
            "date_from_raw":  cols[2].text.strip(),
            "date_until_raw": cols[3].text.strip(),
            "days_absent":    cols[4].text.strip(),
            "matches_missed": cols[5].text.strip(),
        })

    if not injuries:
        return pd.DataFrame()

    df = pd.DataFrame(injuries)
    df["date_from"]      = df["date_from_raw"].apply(lambda v: parse_date(v))
    df["date_until"]     = df["date_until_raw"].apply(lambda v: parse_date(v) if v not in ("-", "") else None)
    df["days_absent"]    = df["days_absent"].apply(clean_integer)
    df["matches_missed"] = df["matches_missed"].apply(clean_integer)

    return df[["season", "injury_type", "date_from", "date_until", "days_absent", "matches_missed"]]


def load_squad_to_dim(db: DBSession, squad_df: pd.DataFrame) -> dict[int, int]:
    """
    Inserta jugadores de la plantilla en dim_player si no existen.
    Devuelve {tm_id: player_id_interno}.
    """
    tm_to_internal = {}
    for _, row in squad_df.iterrows():
        if not row.get("tm_id"):
            continue
        player_id = upsert_player(
            db,
            name_canonical=row["name"],
            position=row.get("position"),
            nationality=row.get("nationality"),
            id_transfermarkt=int(row["tm_id"]),
        )
        tm_to_internal[int(row["tm_id"])] = player_id
    return tm_to_internal


def load_injuries(db: DBSession, df: pd.DataFrame, player_id: int):
    """Inserta las lesiones de un jugador en fact_injuries (evita duplicados)."""
    for _, row in df.iterrows():
        exists = db.query(FactInjury).filter_by(
            player_id=player_id,
            date_from=row["date_from"],
        ).first()
        if exists:
            continue

        db.add(FactInjury(
            player_id=player_id,
            season=str(row["season"]),
            injury_type=str(row["injury_type"]),
            date_from=row["date_from"],
            date_until=row["date_until"] if pd.notna(row["date_until"]) else None,
            days_absent=row["days_absent"],
            matches_missed=row["matches_missed"],
        ))
    db.commit()


def run(
    db_url: str,
    teams: Optional[list[str]] = None,
):
    """
    Para cada equipo configurado:
    1. Extrae la plantilla con valores de mercado y la guarda en CSV.
    2. Carga jugadores en dim_player.
    3. Extrae historial de lesiones y lo carga en fact_injuries.
    """
    engine = get_engine(db_url)
    create_all_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    session_http = build_session(DEFAULT_HEADERS)

    teams = teams or list(TEAM_SLUGS.keys())
    all_squads = []

    for team_name in teams:
        team_info = TEAM_SLUGS.get(team_name)
        if not team_info:
            logger.warning("Equipo no configurado: %s", team_name)
            continue

        team_slug, team_id = team_info
        logger.info("▶ Transfermarkt — Plantilla de %s", team_name)

        squad_df = fetch_squad(team_slug, team_id, session_http)
        if squad_df.empty:
            logger.warning("  Plantilla vacía para %s", team_name)
            continue

        squad_df["team"] = team_name
        all_squads.append(squad_df)
        logger.info("  %d jugadores en plantilla", len(squad_df))

        with SessionLocal() as db:
            tm_map = load_squad_to_dim(db, squad_df)

        for _, player_row in squad_df.iterrows():
            tm_id = player_row.get("tm_id")
            slug  = player_row.get("player_slug")
            name  = player_row.get("name", "?")

            if not tm_id or not slug:
                continue

            logger.info("  → Lesiones de %s (tm_id=%s)", name, tm_id)
            injuries_df = fetch_player_injuries(slug, int(tm_id), session_http)

            if injuries_df.empty:
                continue

            with SessionLocal() as db:
                internal_id = tm_map.get(int(tm_id))
                if not internal_id:
                    internal_id = upsert_player(db, name_canonical=name, id_transfermarkt=int(tm_id))
                load_injuries(db, injuries_df, internal_id)
                logger.info("    ✅ %d lesiones cargadas para %s", len(injuries_df), name)

            time.sleep(random.uniform(4, 8))

    # Guardar valores de mercado en CSV
    if all_squads:
        df_values = pd.concat(all_squads, ignore_index=True)
        df_values = df_values[["name", "tm_id", "team", "position", "nationality", "market_value_eur"]]
        df_values.to_csv("transfermarkt_values.csv", index=False)
        logger.info("✅ %d jugadores con valor de mercado guardados en transfermarkt_values.csv", len(df_values))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(DB_URL)