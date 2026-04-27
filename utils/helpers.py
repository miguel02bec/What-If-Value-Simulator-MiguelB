"""
utils/helpers.py — Funciones de limpieza, conversión y logging
"""
import re
import time
import random
import logging
from datetime import datetime
from typing import Optional

import pandas as pd
import requests
from requests import Session

logger = logging.getLogger(__name__)


# ── Coordenadas ───────────────────────────────────────────────

def normalize_coords_understat(x: float, y: float) -> tuple[float, float]:
    """
    Understat usa coordenadas 0-1.
    Devuelve metros en campo estándar (105 x 68 m).
    """
    return round(x * 105, 4), round(y * 68, 4)


def normalize_coords_sofascore(x: float, y: float) -> tuple[float, float]:
    """
    SofaScore usa coordenadas 0-100 (porcentaje del campo).
    Devuelve metros.
    """
    return round(x / 100 * 105, 4), round(y / 100 * 68, 4)


def statsbomb_to_meters(x: float, y: float) -> tuple[float, float]:
    """
    StatsBomb usa 120x80 unidades propias.
    Convierte a metros (campo 105x68).
    """
    return round(x / 120 * 105, 4), round(y / 80 * 68, 4)


# ── Tiempo / minutos ──────────────────────────────────────────

def parse_minute(raw: str | int) -> int:
    """
    Normaliza distintos formatos de minuto a entero.
    '45+2' → 47,  '90+3' → 93,  45 → 45
    """
    if isinstance(raw, int):
        return raw
    raw = str(raw).strip()
    match = re.match(r"(\d+)\+(\d+)", raw)
    if match:
        return int(match.group(1)) + int(match.group(2))
    return int(re.sub(r"[^\d]", "", raw) or 0)


# ── Fechas ────────────────────────────────────────────────────

def parse_date(raw: str, fmt: Optional[str] = None) -> Optional[datetime]:
    """
    Intenta parsear una fecha. Si no se indica formato, prueba los más comunes.
    Transfermarkt usa DD.MM.YYYY; otras fuentes YYYY-MM-DD.
    """
    if not raw or str(raw).strip() in ("", "-", "N/A"):
        return None
    formats = [fmt] if fmt else ["%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"]
    for f in formats:
        try:
            return datetime.strptime(raw.strip(), f)
        except ValueError:
            continue
    logger.warning("No se pudo parsear la fecha: %s", raw)
    return None


# ── HTTP helpers ──────────────────────────────────────────────

def build_session(headers: Optional[dict] = None) -> Session:
    """Crea una sesión de requests con headers personalizados."""
    session = requests.Session()
    if headers:
        session.headers.update(headers)
    return session


def safe_get(
    session: Session,
    url: str,
    retries: int = 3,
    sleep_range: tuple = (2, 5),
    **kwargs,
) -> Optional[requests.Response]:
    """
    GET con reintentos y pausa aleatoria entre peticiones.
    Devuelve None si todos los intentos fallan.
    """
    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, timeout=20, **kwargs)
            response.raise_for_status()
            time.sleep(random.uniform(*sleep_range))
            return response
        except requests.HTTPError as e:
            logger.warning("HTTP %s en %s (intento %d/%d)", e.response.status_code, url, attempt, retries)
            if e.response.status_code in (403, 429):
                time.sleep(random.uniform(10, 20))  # pausa larga si bloqueado
        except requests.RequestException as e:
            logger.warning("Error de red en %s: %s (intento %d/%d)", url, e, attempt, retries)
            time.sleep(random.uniform(*sleep_range))
    logger.error("Todos los intentos fallaron para %s", url)
    return None


# ── DataFrame helpers ─────────────────────────────────────────

def clean_integer(value) -> Optional[int]:
    """Limpia strings como '14 partidos' o '-' y devuelve int o None."""
    if pd.isna(value) or str(value).strip() in ("", "-", "?"):
        return None
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None
