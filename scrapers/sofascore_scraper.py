"""
scrapers/sofascore_scraper.py
Extrae estadísticas individuales de jugadores de SofaScore via endpoint de lineups.
Con reconexión automática si el driver se cae.
"""
import json
import logging
import time
import random
from typing import Optional

import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from sqlalchemy.orm import sessionmaker

from config import DB_URL, SOFASCORE_TOURNAMENTS
from db.models import DimMatch, FactShot, get_engine, create_all_tables
from utils.helpers import normalize_coords_sofascore, parse_minute
from utils.player_matcher import get_canonical_names, match_player

logger = logging.getLogger(__name__)
API_BASE = "https://api.sofascore.com/api/v1"


def build_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option('excludeSwitches', ['enable-automation'])
    options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    return driver


def rebuild_driver() -> webdriver.Chrome:
    """Reconstruye el driver si se cae."""
    try:
        return build_driver()
    except Exception as e:
        logger.error("Error reconstruyendo driver: %s", e)
        time.sleep(5)
        return build_driver()


def is_driver_alive(driver: webdriver.Chrome) -> bool:
    """Comprueba si el driver sigue activo."""
    try:
        driver.current_url
        return True
    except Exception:
        return False


def api_get(driver: webdriver.Chrome, url: str) -> Optional[dict]:
    try:
        driver.get(url)
        time.sleep(random.uniform(1, 2))
        pre = driver.find_element("tag name", "pre").text
        return json.loads(pre)
    except Exception as e:
        logger.warning("Error en %s: %s", url, e)
        return None


def fetch_rounds(driver, tournament_id: int, season_id: int) -> list[int]:
    url = f"{API_BASE}/unique-tournament/{tournament_id}/season/{season_id}/rounds"
    data = api_get(driver, url)
    if not data:
        return []
    return [r["round"] for r in data.get("rounds", [])]


def fetch_round_matches(driver, tournament_id: int, season_id: int, round_num: int) -> list[dict]:
    url = f"{API_BASE}/unique-tournament/{tournament_id}/season/{season_id}/events/round/{round_num}"
    data = api_get(driver, url)
    if not data:
        return []
    return data.get("events", [])


def fetch_shotmap(driver, match_id: int) -> list[dict]:
    url = f"{API_BASE}/event/{match_id}/shotmap"
    data = api_get(driver, url)
    if not data:
        return []
    return data.get("shotmap", [])


def fetch_lineups(driver, match_id: int) -> Optional[dict]:
    url = f"{API_BASE}/event/{match_id}/lineups"
    return api_get(driver, url)


def extract_player_stats(lineups: dict, match_id: int) -> pd.DataFrame:
    """Extrae estadísticas individuales de jugadores desde el endpoint de lineups."""
    rows = []
    for side in ("home", "away"):
        team_data = lineups.get(side, {})
        for player_data in team_data.get("players", []):
            player = player_data.get("player", {})
            stats = player_data.get("statistics", {})
            if not stats:
                continue

            rows.append({
                "sofascore_match_id": match_id,
                "sofascore_player_id": player.get("id"),
                "player_name": player.get("name", ""),
                "position": player_data.get("position", ""),
                "side": side,
                "substitute": player_data.get("substitute", False),
                "minutes_played": stats.get("minutesPlayed"),
                "rating": stats.get("rating"),
                "goals": stats.get("goals", 0),
                "goal_assist": stats.get("goalAssist", 0),
                "total_shots": stats.get("totalShots", 0),
                "on_target": stats.get("onTargetScoringAttempt", 0),
                "total_pass": stats.get("totalPass", 0),
                "accurate_pass": stats.get("accuratePass", 0),
                "total_long_balls": stats.get("totalLongBalls", 0),
                "accurate_long_balls": stats.get("accurateLongBalls", 0),
                "total_cross": stats.get("totalCross", 0),
                "accurate_cross": stats.get("accurateCross", 0),
                "key_pass": stats.get("keyPass", 0),
                "duel_won": stats.get("duelWon", 0),
                "duel_lost": stats.get("duelLost", 0),
                "aerial_won": stats.get("aerialWon", 0),
                "aerial_lost": stats.get("aerialLost", 0),
                "total_contest": stats.get("totalContest", 0),
                "won_contest": stats.get("wonContest", 0),
                "interception_won": stats.get("interceptionWon", 0),
                "total_tackle": stats.get("totalTackle", 0),
                "won_tackle": stats.get("wonTackle", 0),
                "total_clearance": stats.get("totalClearance", 0),
                "ball_recovery": stats.get("ballRecovery", 0),
                "dispossessed": stats.get("dispossessed", 0),
                "possession_lost": stats.get("possessionLostCtrl", 0),
                "fouls": stats.get("fouls", 0),
                "was_fouled": stats.get("wasFouled", 0),
                "touches": stats.get("touches", 0),
                "saves": stats.get("saves", 0),
                "xg": stats.get("expectedGoals"),
                "xa": stats.get("expectedAssists"),
                "xg_on_target": stats.get("expectedGoalsOnTarget"),
                "proposed_market_value": player.get("proposedMarketValueRaw", {}).get("value"),
            })
    return pd.DataFrame(rows)


def transform_shotmap(shots: list[dict]) -> pd.DataFrame:
    rows = []
    for shot in shots:
        player_info = shot.get("player", {})
        coords = shot.get("playerCoordinates", {})
        raw_x = coords.get("x")
        raw_y = coords.get("y")
        x, y = (None, None)
        if raw_x is not None and raw_y is not None:
            x, y = normalize_coords_sofascore(float(raw_x), float(raw_y))
        rows.append({
            "player_name": player_info.get("name", ""),
            "player_sofascore_id": player_info.get("id"),
            "minute": parse_minute(shot.get("time", 0)),
            "x": x,
            "y": y,
            "xg": shot.get("xg"),
            "result": shot.get("shotType", ""),
            "shot_type": shot.get("bodyPart", ""),
            "situation": shot.get("situation", ""),
        })
    return pd.DataFrame(rows)


def upsert_match_sfs(db, event: dict, league: str) -> int:
    source_id = str(event.get("id", ""))
    existing = db.query(DimMatch).filter_by(
        source="sofascore", source_match_id=source_id
    ).first()
    if existing:
        return existing.match_id

    start_ts = event.get("startTimestamp")
    date = pd.to_datetime(start_ts, unit="s", errors="coerce").date() if start_ts else None

    match = DimMatch(
        date=date,
        competition=league,
        season=str(event.get("season", {}).get("year", "")),
        home_team=event.get("homeTeam", {}).get("name", ""),
        away_team=event.get("awayTeam", {}).get("name", ""),
        home_score=event.get("homeScore", {}).get("current"),
        away_score=event.get("awayScore", {}).get("current"),
        source="sofascore",
        source_match_id=source_id,
    )
    db.add(match)
    db.commit()
    db.refresh(match)
    return match.match_id


def run(db_url: str, leagues: Optional[list[str]] = None, season_ids: Optional[dict] = None):
    engine = get_engine(db_url)
    create_all_tables(engine)
    SessionLocal = sessionmaker(bind=engine)

    leagues = leagues or list(SOFASCORE_TOURNAMENTS.keys())
    driver = build_driver()
    all_player_stats = []

    try:
        for league_name in leagues:
            cfg = SOFASCORE_TOURNAMENTS.get(league_name)
            if not cfg:
                continue

            tournament_id = cfg["tournament_id"]
            season_id = cfg["season_id"]

            logger.info("▶ SofaScore — %s (tournament=%d, season=%d)",
                        league_name, tournament_id, season_id)

            if not is_driver_alive(driver):
                logger.warning("Driver caído antes de liga %s, reconectando...", league_name)
                driver = rebuild_driver()

            rounds = fetch_rounds(driver, tournament_id, season_id)
            logger.info("  %d jornadas encontradas", len(rounds))

            for round_num in rounds:
                if not is_driver_alive(driver):
                    logger.warning("Driver caído en jornada %d, reconectando...", round_num)
                    driver = rebuild_driver()

                events = fetch_round_matches(driver, tournament_id, season_id, round_num)

                for event in events:
                    match_id = event.get("id")
                    if not match_id:
                        continue

                    if not is_driver_alive(driver):
                        logger.warning("Driver caído en partido %s, reconectando...", match_id)
                        driver = rebuild_driver()

                    # Estadísticas individuales via lineups
                    lineups = fetch_lineups(driver, match_id)
                    if lineups:
                        df_stats = extract_player_stats(lineups, match_id)
                        if not df_stats.empty:
                            df_stats["league"] = league_name
                            all_player_stats.append(df_stats)

                    if not is_driver_alive(driver):
                        driver = rebuild_driver()

                    # Tiros con xG
                    shotmap = fetch_shotmap(driver, match_id)
                    if shotmap:
                        df_shots = transform_shotmap(shotmap)
                        if not df_shots.empty:
                            with SessionLocal() as db:
                                canonical_map = get_canonical_names(db)
                                internal_id = upsert_match_sfs(db, event, league_name)
                                for _, row in df_shots.iterrows():
                                    player_id = match_player(
                                        incoming_name=str(row["player_name"]),
                                        source_system="sofascore",
                                        source_id=str(row["player_sofascore_id"]),
                                        canonical_map=canonical_map,
                                        db=db,
                                    )
                                    try:
                                        db.add(FactShot(
                                            match_id=internal_id,
                                            player_id=player_id,
                                            minute=int(row["minute"]) if row["minute"] is not None else None,
                                            x=row["x"],
                                            y=row["y"],
                                            xg=float(row["xg"]) if row["xg"] is not None else None,
                                            result=str(row["result"]),
                                            shot_type=str(row["shot_type"]),
                                            situation=str(row["situation"]),
                                            source="sofascore",
                                        ))
                                        db.flush()
                                    except Exception:
                                        db.rollback()
                                db.commit()
                                logger.info("    ✅ Partido %s — %d tiros", match_id, len(df_shots))

                time.sleep(random.uniform(1, 2))

    finally:
        try:
            driver.quit()
        except Exception:
            pass

    if all_player_stats:
        df_all = pd.concat(all_player_stats, ignore_index=True)
        # Combinar con CSV existente
        try:
            df_existing = pd.read_csv("sofascore_player_stats.csv")
            df_all = pd.concat([df_existing, df_all], ignore_index=True)
            df_all = df_all.drop_duplicates(subset=["sofascore_match_id", "sofascore_player_id"])
        except FileNotFoundError:
            pass
        df_all.to_csv("sofascore_player_stats.csv", index=False)
        logger.info("✅ %d registros guardados en sofascore_player_stats.csv", len(df_all))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(DB_URL, leagues=["La Liga"])