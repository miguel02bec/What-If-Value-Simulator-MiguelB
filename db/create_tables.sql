-- ============================================================
-- football_db — Schema completo
-- Ejecutar en orden (dimensiones primero, hechos después)
-- ============================================================

-- ------------------------------------------------------------
-- DIMENSIONES
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dim_team (
    team_id     SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL UNIQUE,
    country     VARCHAR(80),
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dim_player (
    player_id           SERIAL PRIMARY KEY,
    name_canonical      VARCHAR(150) NOT NULL,
    nationality         VARCHAR(80),
    birth_date          DATE,
    position            VARCHAR(50),
    id_understat        INTEGER,
    id_sofascore        INTEGER,
    id_transfermarkt    INTEGER,
    id_statsbomb        VARCHAR(50),
    id_whoscored        INTEGER,
    created_at          TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dim_match (
    match_id        SERIAL PRIMARY KEY,
    date            DATE,
    competition     VARCHAR(100),
    season          VARCHAR(20),
    home_team       VARCHAR(100),
    away_team       VARCHAR(100),
    home_score      SMALLINT,
    away_score      SMALLINT,
    source          VARCHAR(50),
    source_match_id VARCHAR(50)  -- ID original en la fuente
);

-- ------------------------------------------------------------
-- HECHOS
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS fact_shots (
    shot_id     SERIAL PRIMARY KEY,
    match_id    INTEGER REFERENCES dim_match(match_id),
    player_id   INTEGER REFERENCES dim_player(player_id),
    team_id     INTEGER REFERENCES dim_team(team_id),
    minute      SMALLINT,
    x           DECIMAL(6,4),
    y           DECIMAL(6,4),
    xg          DECIMAL(6,4),
    result      VARCHAR(30),
    shot_type   VARCHAR(30),
    situation   VARCHAR(50),
    source      VARCHAR(30)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_shots_unique
    ON fact_shots (match_id, player_id, minute, source);

CREATE TABLE IF NOT EXISTS fact_events (
    event_id    SERIAL PRIMARY KEY,
    match_id    INTEGER REFERENCES dim_match(match_id),
    player_id   INTEGER REFERENCES dim_player(player_id),
    team_id     INTEGER REFERENCES dim_team(team_id),
    event_type  VARCHAR(50),
    minute      SMALLINT,
    second      SMALLINT,
    x           DECIMAL(6,4),
    y           DECIMAL(6,4),
    end_x       DECIMAL(6,4),
    end_y       DECIMAL(6,4),
    outcome     VARCHAR(50),
    source      VARCHAR(30)
);

CREATE TABLE IF NOT EXISTS fact_injuries (
    injury_id       SERIAL PRIMARY KEY,
    player_id       INTEGER REFERENCES dim_player(player_id),
    season          VARCHAR(20),
    injury_type     VARCHAR(200),
    date_from       DATE,
    date_until      DATE,   -- NULL si sigue lesionado
    days_absent     INTEGER,
    matches_missed  SMALLINT
);

-- ------------------------------------------------------------
-- TABLA DE REVISIÓN MANUAL (matching de jugadores)
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS player_match_review (
    review_id           SERIAL PRIMARY KEY,
    source_name         VARCHAR(150),
    source_system       VARCHAR(50),
    source_id           VARCHAR(50),
    suggested_canonical VARCHAR(150),
    similarity_score    SMALLINT,
    resolved            BOOLEAN DEFAULT FALSE,
    player_id_assigned  INTEGER REFERENCES dim_player(player_id)
);

-- ------------------------------------------------------------
-- ÍNDICES DE RENDIMIENTO
-- ------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_shots_match    ON fact_shots  (match_id);
CREATE INDEX IF NOT EXISTS idx_shots_player   ON fact_shots  (player_id);
CREATE INDEX IF NOT EXISTS idx_events_match   ON fact_events (match_id);
CREATE INDEX IF NOT EXISTS idx_events_player  ON fact_events (player_id);
CREATE INDEX IF NOT EXISTS idx_events_type    ON fact_events (event_type);
CREATE INDEX IF NOT EXISTS idx_injuries_player ON fact_injuries (player_id);
