"""
scrapers/sofifa_scraper.py
Extrae atributos de jugadores de SoFIFA para las 5 grandes ligas.
"""
import logging
import time
import random
import re
from typing import Optional

import pandas as pd
from bs4 import BeautifulSoup

from config import DB_URL

logger = logging.getLogger(__name__)
BASE_URL = "https://sofifa.com"

LEAGUE_IDS = {
    "Premier League": 13,
    "La Liga": 14,
    "Bundesliga": 19,
    "Serie A": 16,
    "Ligue 1": 31,
}

ATTR_MAP = {
    "Centros": "crossing", "Definición": "finishing",
    "Precisión cabeza": "heading_accuracy", "Pases cortos": "short_passing",
    "Voleas": "volleys", "Regates": "dribbling", "Efecto": "curve",
    "Precisión faltas": "fk_accuracy", "Pases largos": "long_passing",
    "Control del balón": "ball_control", "Aceleración": "acceleration",
    "Velocidad": "sprint_speed", "Agilidad": "agility",
    "Reflejos": "reactions", "Equilibrio": "balance",
    "Potencia": "shot_power", "Salto": "jumping",
    "Resistencia": "stamina", "Fuerza": "strength",
    "Tiros lejanos": "long_shots", "Agresividad": "aggression",
    "Intercep.": "interceptions", "Pos. ataque": "positioning",
    "Vision": "vision", "Penaltis": "penalties",
    "Compostura": "composure", "Conciencia defensiva": "defensive_awareness",
    "Robos": "standing_tackle", "Entrada agresiva": "sliding_tackle",
}


def parse_value(value_str: str) -> Optional[float]:
    """Convierte '€172.5M' o '€390K' a float en euros."""
    if not value_str:
        return None
    value_str = value_str.replace("€", "").strip()
    if "M" in value_str:
        return float(value_str.replace("M", "")) * 1_000_000
    if "K" in value_str:
        return float(value_str.replace("K", "")) * 1_000
    try:
        return float(value_str)
    except ValueError:
        return None


def fetch_player_list(league_ids: list[int], max_pages: int = 50) -> list[dict]:
    """Recorre la lista paginada de jugadores con Playwright."""
    from playwright.sync_api import sync_playwright

    players = []
    league_params = "&".join([f"lg%5B%5D={lid}" for lid in league_ids])

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()

        for offset in range(0, max_pages * 60, 60):
            url = f"{BASE_URL}/players?type=all&{league_params}&offset={offset}"
            page.goto(url)
            page.wait_for_load_state("networkidle")
            time.sleep(random.uniform(2, 4))

            if offset == 0:
                with open("sofifa_debug.html", "w", encoding="utf-8") as f:
                    f.write(page.content())
                logger.info("HTML guardado en sofifa_debug.html")

            soup = BeautifulSoup(page.content(), "html.parser")
            rows = soup.select("table tbody tr")

            if not rows:
                logger.info("  Sin más filas en offset=%d, parando", offset)
                break

            for row in rows:
                link = row.select_one("td a[href*='/player/']")
                if not link:
                    continue

                href = link.get("href", "")
                m = re.search(r"/player/(\d+)/", href)
                if not m:
                    continue

                player_id = m.group(1)

                value_td = row.select_one("td[data-col='vl']")
                value_str = value_td.text.strip() if value_td else ""

                age_td = row.select_one("td[data-col='ae']")
                age = int(age_td.text.strip()) if age_td else None

                overall_td = row.select_one("td[data-col='oa'] em")
                overall = int(overall_td.text.strip()) if overall_td else None

                potential_td = row.select_one("td[data-col='pt'] em")
                potential = int(potential_td.text.strip()) if potential_td else None

                players.append({
                    "sofifa_id": player_id,
                    "url": f"{BASE_URL}{href}",
                    "age": age,
                    "overall": overall,
                    "potential": potential,
                    "market_value_raw": value_str,
                })

            logger.info("  offset=%d — %d jugadores acumulados", offset, len(players))

        browser.close()

    return players


def fetch_player_attributes(page, player_url: str) -> Optional[dict]:
    """Visita el perfil de un jugador con Playwright y extrae sus atributos."""
    try:
        page.goto(player_url, timeout=15000, wait_until="domcontentloaded")
        time.sleep(random.uniform(1, 1.5))

        soup = BeautifulSoup(page.content(), "html.parser")
        attrs = {}

        # Nombre
        name_tag = soup.select_one("h1")
        attrs["name"] = name_tag.text.strip() if name_tag else ""

        # Posición
        pos_tag = soup.select_one(".pos")
        attrs["position"] = pos_tag.text.strip() if pos_tag else ""

        # Equipo
        team_tag = soup.select_one(".team a")
        attrs["team"] = team_tag.text.strip() if team_tag else ""

        # Atributos numéricos
        stat_items = soup.select("li.ellipsis")
        for item in stat_items:
            value_span = item.select_one("span:first-child")
            label_span = item.select_one("span:last-child")
            if not value_span or not label_span:
                continue
            label = label_span.text.strip()
            try:
                value = int(value_span.text.strip())
            except ValueError:
                continue
            if label in ATTR_MAP:
                attrs[ATTR_MAP[label]] = value

        # Pierna hábil, filigranas, pierna mala, reputación
        profile_items = soup.select("div.meta span")
        for item in profile_items:
            text = item.text.strip()
            if "Pierna hábil" in text:
                attrs["preferred_foot"] = text.split()[-1]
            elif "Filigranas" in text:
                try:
                    attrs["skill_moves"] = int(text[0])
                except (ValueError, IndexError):
                    pass
            elif "Pierna mala" in text:
                try:
                    attrs["weak_foot"] = int(text[0])
                except (ValueError, IndexError):
                    pass
            elif "Reputación" in text:
                try:
                    attrs["international_reputation"] = int(text[0])
                except (ValueError, IndexError):
                    pass

        return attrs if attrs.get("name") else None

    except Exception as e:
        logger.warning("Error en %s: %s", player_url, e)
        return None

    except Exception as e:
        logger.warning("Error en %s: %s", player_url, e)
        return None


def run(db_url: str, leagues: Optional[list[str]] = None, max_pages: int = 50):
    """Ejecuta el scraper completo de SoFIFA."""
    from playwright.sync_api import sync_playwright

    league_ids = []
    for league in (leagues or list(LEAGUE_IDS.keys())):
        lid = LEAGUE_IDS.get(league)
        if lid:
            league_ids.append(lid)
    if not league_ids:
        league_ids = list(LEAGUE_IDS.values())

    logger.info("▶ SoFIFA — obteniendo lista de jugadores...")
    players = fetch_player_list(league_ids, max_pages=max_pages)
    logger.info("  %d jugadores encontrados en total", len(players))

    if not players:
        logger.warning("No se obtuvieron jugadores")
        return pd.DataFrame()

    all_data = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()

        for i, player in enumerate(players):
            logger.info("  → Jugador %d/%d: %s", i + 1, len(players), player["url"])
            attrs = fetch_player_attributes(page, player["url"])
            if attrs:
                merged = {**player, **attrs}
                all_data.append(merged)
            if (i + 1) % 50 == 0:
                logger.info("  Procesados %d/%d jugadores", i + 1, len(players))
                pd.DataFrame(all_data).to_csv("sofifa_players.csv", index=False)

        browser.close()

    if not all_data:
        logger.warning("No se obtuvieron datos")
        return pd.DataFrame()

    df = pd.DataFrame(all_data)
    if "market_value_raw" in df.columns:
        df["market_value_eur"] = df["market_value_raw"].apply(parse_value)
    df.to_csv("sofifa_players.csv", index=False)
    logger.info("✅ %d jugadores guardados en sofifa_players.csv", len(df))

    return df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(DB_URL)