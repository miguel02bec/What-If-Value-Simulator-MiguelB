"""
scrapers/whoscored_scraper.py
Extrae eventos Opta embebidos en WhoScored mediante Selenium.
⚠ Alta protección anti-bot. Lee los comentarios antes de ejecutar.
"""
import json
import logging
import re
import time
import random
from typing import Optional

import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import WebDriverException
from sqlalchemy.orm import Session as DBSession, sessionmaker

from config import DB_URL, DEFAULT_HEADERS
from db.models import DimMatch, FactEvent, FactShot, get_engine, create_all_tables
from utils.helpers import parse_minute
from utils.player_matcher import get_canonical_names, match_player

logger = logging.getLogger(__name__)
BASE_URL = "https://www.whoscored.com"

# Ligas WhoScored — slug y region_id para construir URLs
WHOSCORED_LEAGUES = {
    "La Liga":        {"region": 206, "tournament": 4,  "name": "Spain-LaLiga"},
    "Premier League": {"region": 252, "tournament": 2,  "name": "England-PremierLeague"},
    "Bundesliga":     {"region": 81,  "tournament": 3,  "name": "Germany-Bundesliga"},
    "Serie A":        {"region": 108, "tournament": 5,  "name": "Italy-SerieA"},
    "Ligue 1":        {"region": 74,  "tournament": 22, "name": "France-Ligue1"},
}


# ── Driver ────────────────────────────────────────────────────

def build_driver(headless: bool = True) -> webdriver.Chrome:
    """
    Crea un driver de Chrome.
    Si headless=True WhoScored puede detectarlo; prueba headless=False en depuración.
    """
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(f"user-agent={DEFAULT_HEADERS['User-Agent']}")
    options.add_argument("--window-size=1920,1080")

    driver = webdriver.Chrome(options=options)
    # Ocultar el flag webdriver
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
    )
    return driver


# ── Extracción ────────────────────────────────────────────────

def fetch_match_data(match_url: str, driver: webdriver.Chrome) -> Optional[dict]:
    try:
        driver.get(match_url)
        time.sleep(random.uniform(6, 9))

        source = driver.page_source

        # Buscar en el HTML directo
        for pattern in [
            r"matchCentreData\s*=\s*(\{.*?\});",
            r"initialMatchDataForScripts\s*=\s*(\{.*?\});",
        ]:
            m = re.search(pattern, source, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1))
                except json.JSONDecodeError:
                    continue

        # Buscar en scripts inline
        scripts = driver.find_elements("tag name", "script")
        for script in scripts:
            content = script.get_attribute("innerHTML") or ""
            if "incidentEvents" in content or "matchCentreData" in content:
                for pattern in [
                    r"matchCentreData\s*=\s*(\{.+?\});",
                    r"(\{[^{}]*?incidentEvents[^{}]*?\})",
                ]:
                    m = re.search(pattern, content, re.DOTALL)
                    if m:
                        try:
                            return json.loads(m.group(1))
                        except json.JSONDecodeError:
                            continue

        logger.warning("No se encontró matchCentreData en %s", match_url)
        return None

    except (WebDriverException, json.JSONDecodeError) as e:
        logger.error("Error extrayendo %s: %s", match_url, e)
        return None

def fetch_league_matches_ws(league_slug: str, season: str, driver: webdriver.Chrome) -> list[dict]:
    """
    Obtiene la lista de partidos de una liga desde la página de fixtures.
    Devuelve una lista de dicts con {match_id, url, home, away, date}.
    """
    url = f"{BASE_URL}/Regions/{league_slug}/Tournaments/show#"
    driver.get(url)
    time.sleep(random.uniform(5, 8))

    # Extrae matchesData del JS
    source = driver.page_source
    pattern = r"matchesData\s*=\s*(\[.*?\]);"
    raw = re.search(pattern, source, re.DOTALL)
    if not raw:
        logger.warning("No se encontraron matchesData para %s", league_slug)
        return []

    try:
        return json.loads(raw.group(1))
    except json.JSONDecodeError:
        return []


# ── Transformación ────────────────────────────────────────────

def transform_events(data: dict, match_id: int) -> pd.DataFrame:
    """
    Convierte matchCentreData en un DataFrame de eventos.
    Los eventos Opta usan coordenadas 0-100 (porcentaje del campo).
    """
    rows = []
    for side in ("home", "away"):
        team_events = data.get(side, {}).get("incidentEvents", []) or \
                      data.get(side, {}).get("events", [])
        for evt in team_events:
            x_raw = evt.get("x")
            y_raw = evt.get("y")
            end_x_raw = evt.get("endX")
            end_y_raw = evt.get("endY")

            rows.append({
                "player_name": evt.get("playerName", ""),
                "player_id": evt.get("playerId"),
                "event_type": evt.get("type", {}).get("displayName", "") if isinstance(evt.get("type"), dict) else str(evt.get("type", "")),
                "minute": parse_minute(evt.get("minute", 0)),
                "second": evt.get("second"),
                # Coordenadas Opta 0-100 → metros
                "x": round(float(x_raw) / 100 * 105, 4) if x_raw is not None else None,
                "y": round(float(y_raw) / 100 * 68, 4) if y_raw is not None else None,
                "end_x": round(float(end_x_raw) / 100 * 105, 4) if end_x_raw is not None else None,
                "end_y": round(float(end_y_raw) / 100 * 68, 4) if end_y_raw is not None else None,
                "outcome": evt.get("outcomeType", {}).get("displayName", "") if isinstance(evt.get("outcomeType"), dict) else "",
                "side": side,
            })
    return pd.DataFrame(rows)


# ── Carga en BD ───────────────────────────────────────────────

def upsert_match_ws(db: DBSession, match_data: dict, league: str) -> int:
    source_id = str(match_data.get("id") or match_data.get("matchId", ""))
    existing = db.query(DimMatch).filter_by(
        source="whoscored", source_match_id=source_id
    ).first()
    if existing:
        return existing.match_id

    match = DimMatch(
        date=pd.to_datetime(match_data.get("startDate"), errors="coerce").date()
             if match_data.get("startDate") else None,
        competition=league,
        season=str(match_data.get("season", "")),
        home_team=match_data.get("home", {}).get("name", "") if isinstance(match_data.get("home"), dict) else "",
        away_team=match_data.get("away", {}).get("name", "") if isinstance(match_data.get("away"), dict) else "",
        home_score=match_data.get("homeScore"),
        away_score=match_data.get("awayScore"),
        source="whoscored",
        source_match_id=source_id,
    )
    db.add(match)
    db.commit()
    db.refresh(match)
    return match.match_id


def load_events_ws(
    db: DBSession,
    df: pd.DataFrame,
    match_id: int,
    canonical_map: dict[str, int],
):
    buffer = []
    for _, row in df.iterrows():
        player_id = None
        if row["player_name"]:
            player_id = match_player(
                incoming_name=str(row["player_name"]),
                source_system="whoscored",
                source_id=str(row["player_id"]),
                canonical_map=canonical_map,
                db=db,
            )
        buffer.append(FactEvent(
            match_id=match_id,
            player_id=player_id,
            event_type=str(row["event_type"]),
            minute=int(row["minute"]) if row["minute"] is not None else None,
            second=int(row["second"]) if row["second"] is not None else None,
            x=row["x"],
            y=row["y"],
            end_x=row["end_x"],
            end_y=row["end_y"],
            outcome=str(row["outcome"]),
            source="whoscored",
        ))
    db.bulk_save_objects(buffer)
    db.commit()
    logger.info("  ✅ %d eventos cargados", len(buffer))


# ── Punto de entrada ──────────────────────────────────────────

def run(
    db_url: str,
    match_urls: Optional[list[str]] = None,
    headless: bool = True,
):
    """
    Extrae eventos de WhoScored para una lista de URLs de partidos.

    Args:
        db_url:      Cadena de conexión.
        match_urls:  Lista de URLs directas de partidos WhoScored.
                     Ejemplo: ['https://www.whoscored.com/Matches/1729832/Live']
        headless:    False para depuración (evita detección bot).
    """
    if not match_urls:
        logger.warning("No se han proporcionado URLs de partidos. Nada que procesar.")
        return

    engine = get_engine(db_url)
    create_all_tables(engine)
    SessionLocal = sessionmaker(bind=engine)

    driver = build_driver(headless=headless)

    try:
        for url in match_urls:
            logger.info("▶ WhoScored — %s", url)
            data = fetch_match_data(url, driver)
            if not data:
                continue

            df_events = transform_events(data, match_id=0)  # match_id se asigna tras insertar

            with SessionLocal() as db:
                canonical_map = get_canonical_names(db)
                internal_id = upsert_match_ws(db, data, league="Unknown")
                load_events_ws(db, df_events, internal_id, canonical_map)

            time.sleep(random.uniform(3, 6))
    finally:
        driver.quit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Pasa una lista de URLs de partidos concretos
    run(
        DB_URL,
        match_urls=["https://www.whoscored.com/Matches/1729832/Live"],
        headless=False,  # Usa False en pruebas para evitar detección
    )
