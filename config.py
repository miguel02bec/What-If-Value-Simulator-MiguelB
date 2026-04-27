"""
config.py — Configuración central del proyecto
Cambia DB_URL según tu entorno (PostgreSQL o SQLite para pruebas).
"""

# ── Base de datos ─────────────────────────────────────────────
# SQLite para pruebas locales (sin instalar nada):
DB_URL = "sqlite:///football.db"

# PostgreSQL en producción (descomenta cuando lo tengas instalado):
# DB_URL = "postgresql+psycopg2://usuario:contraseña@localhost:5432/football_db"


# ── Ligas disponibles por fuente ──────────────────────────────

# Understat: IDs de liga usados en la URL
UNDERSTAT_LEAGUES = {
    "La Liga": "La_Liga",  # L mayúscula en Liga
    "EPL": "EPL",
    "Bundesliga": "Bundesliga",
    "Serie A": "Serie_A",
    "Ligue 1": "Ligue_1",
}
# StatsBomb: competition_id y season_id principales
STATSBOMB_COMPETITIONS = {
    "La Liga":        {"competition_id": 11, "season_id": 42},   # 2019/20
    "Champions League": {"competition_id": 16, "season_id": 4},
    "World Cup 2018": {"competition_id": 43, "season_id": 3},
}

# SofaScore: IDs de torneo
SOFASCORE_TOURNAMENTS = {
    "La Liga":        {"tournament_id": 8,  "season_id": 61643},
    "Premier League": {"tournament_id": 17, "season_id": 61627},
    "Bundesliga":     {"tournament_id": 35, "season_id": 63516},
    "Serie A":        {"tournament_id": 23, "season_id": 63515},
    "Ligue 1":        {"tournament_id": 34, "season_id": 61736},
}


# ── Headers HTTP anti-bloqueo ─────────────────────────────────
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ── Umbrales de matching de jugadores ────────────────────────
MATCH_THRESHOLD_AUTO   = 85   # >= acepta automáticamente
MATCH_THRESHOLD_REVIEW = 60   # entre 60-84 va a revisión manual
