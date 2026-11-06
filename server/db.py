import json
import os
import re
import sqlite3

DB_PATH = os.environ.get("LUMINUS_DB", os.path.join(os.path.dirname(__file__), "data.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS worlds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS places (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    world_id INTEGER,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'ambiente',
    description TEXT NOT NULL DEFAULT '',
    atmosphere TEXT NOT NULL DEFAULT '',
    rules TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    FOREIGN KEY(world_id) REFERENCES worlds(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_places_world ON places(world_id);
CREATE TABLE IF NOT EXISTS chat_requests (
    request_key TEXT PRIMARY KEY,
    session_id INTEGER NOT NULL,
    turn INTEGER NOT NULL DEFAULT 0,
    user_content TEXT NOT NULL DEFAULT '',
    assistant_content TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_requests_session
    ON chat_requests(session_id, created_at);
CREATE TABLE IF NOT EXISTS world_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    world_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    keys TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    FOREIGN KEY(world_id) REFERENCES worlds(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_world_facts ON world_facts(world_id);
CREATE TABLE IF NOT EXISTS characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'npc',
    name TEXT NOT NULL,
    avatar TEXT NOT NULL DEFAULT '',
    tagline TEXT NOT NULL DEFAULT '',
    description_public TEXT NOT NULL DEFAULT '',
    persona TEXT NOT NULL DEFAULT '',
    greeting TEXT NOT NULL DEFAULT '',
    appearance TEXT NOT NULL DEFAULT '',
    personality TEXT NOT NULL DEFAULT '',
    backstory TEXT NOT NULL DEFAULT '',
    speech_style TEXT NOT NULL DEFAULT '',
    likes TEXT NOT NULL DEFAULT '',
    dislikes TEXT NOT NULL DEFAULT '',
    fears TEXT NOT NULL DEFAULT '',
    relationship TEXT NOT NULL DEFAULT '',
    secrets TEXT NOT NULL DEFAULT '',
    example_dialogue TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    world_id INTEGER,
    model TEXT NOT NULL DEFAULT '',
    voice TEXT NOT NULL DEFAULT '',
    portraits TEXT NOT NULL DEFAULT '{}',
    visual_prompt TEXT NOT NULL DEFAULT '',
    art_seed INTEGER NOT NULL DEFAULT 0,
    visual_style TEXT NOT NULL DEFAULT 'anime',
    archived INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    FOREIGN KEY(world_id) REFERENCES worlds(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER NOT NULL,
    protagonist_id INTEGER,
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    summarized_until INTEGER NOT NULL DEFAULT 0,
    turn_count INTEGER NOT NULL DEFAULT 0,
    opening_generated INTEGER NOT NULL DEFAULT 0,
    scene_image TEXT NOT NULL DEFAULT '',
    scene_prompt TEXT NOT NULL DEFAULT '',
    ledger TEXT NOT NULL DEFAULT '',
    recap TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(character_id) REFERENCES characters(id) ON DELETE CASCADE,
    FOREIGN KEY(protagonist_id) REFERENCES characters(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS session_cast (
    session_id INTEGER NOT NULL,
    character_id INTEGER NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    PRIMARY KEY (session_id, character_id),
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY(character_id) REFERENCES characters(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_session_cast_session ON session_cast(session_id);
CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    number INTEGER NOT NULL,
    summary TEXT NOT NULL,
    start_id INTEGER NOT NULL,
    end_id INTEGER NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chapters_session ON chapters(session_id);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE TABLE IF NOT EXISTS message_states (
    message_id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL,
    state TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_message_states_session
    ON message_states(session_id, message_id);
CREATE TABLE IF NOT EXISTS message_vectors (
    message_id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_message_vectors_session
    ON message_vectors(session_id, model);
CREATE TABLE IF NOT EXISTS fts_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(
    content,
    role UNINDEXED,
    session_id UNINDEXED,
    content='messages',
    content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
    INSERT INTO message_fts(rowid, content, role, session_id)
    VALUES (new.id, new.content, new.role, new.session_id);
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
    INSERT INTO message_fts(message_fts, rowid, content, role, session_id)
    VALUES ('delete', old.id, old.content, old.role, old.session_id);
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE ON messages BEGIN
    INSERT INTO message_fts(message_fts, rowid, content, role, session_id)
    VALUES ('delete', old.id, old.content, old.role, old.session_id);
    INSERT INTO message_fts(rowid, content, role, session_id)
    VALUES (new.id, new.content, new.role, new.session_id);
END;
CREATE TABLE IF NOT EXISTS threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'promessa',
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'aberta',
    setup TEXT NOT NULL DEFAULT '',
    payoff TEXT NOT NULL DEFAULT '',
    due_turn INTEGER NOT NULL DEFAULT 0,
    entities TEXT NOT NULL DEFAULT '[]',
    last_turn INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_threads_session ON threads(session_id, status);
CREATE TABLE IF NOT EXISTS entity_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    entity TEXT NOT NULL,
    fact TEXT NOT NULL,
    known_by TEXT NOT NULL DEFAULT '[]',
    source_turn INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_entity_facts_unique
    ON entity_facts(session_id, entity, fact);
CREATE INDEX IF NOT EXISTS idx_entity_facts_session
    ON entity_facts(session_id, entity);
CREATE TABLE IF NOT EXISTS commitment_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    turn INTEGER NOT NULL DEFAULT 0,
    thread_id INTEGER,
    check_type TEXT NOT NULL DEFAULT '',
    ok INTEGER NOT NULL DEFAULT 1,
    detail TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY(thread_id) REFERENCES threads(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_commitment_checks_session
    ON commitment_checks(session_id, turn);
CREATE TABLE IF NOT EXISTS world_voices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    world_id INTEGER,
    turn INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'rumor',
    title TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    claims TEXT NOT NULL DEFAULT '[]',
    truth_status TEXT NOT NULL DEFAULT 'incerto',
    known_by TEXT NOT NULL DEFAULT '[]',
    visibility TEXT NOT NULL DEFAULT 'rumor',
    source TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY(world_id) REFERENCES worlds(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_world_voices_session
    ON world_voices(session_id, id);
CREATE INDEX IF NOT EXISTS idx_world_voices_world
    ON world_voices(world_id, id);
CREATE TABLE IF NOT EXISTS consistency_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    turn INTEGER NOT NULL DEFAULT 0,
    ok INTEGER NOT NULL DEFAULT 1,
    severity TEXT NOT NULL DEFAULT 'baixa',
    issues TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_consistency_session
    ON consistency_checks(session_id, id);
CREATE TABLE IF NOT EXISTS mana (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    balance INTEGER NOT NULL DEFAULT 0,
    last_daily REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0
);
"""


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init():
    conn = connect()
    conn.executescript(SCHEMA)
    _ensure_column(conn, "characters", "voice", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "characters", "portraits", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(conn, "characters", "visual_prompt", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "characters", "art_seed", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "characters", "visual_style", "TEXT NOT NULL DEFAULT 'anime'")
    _ensure_column(conn, "characters", "archived", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "characters", "attributes", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(conn, "characters", "skills", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(conn, "sessions", "archived", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "sessions", "ledger", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "sessions", "recap", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "sessions", "opening_generated", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "sessions", "scene_image", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "sessions", "scene_prompt", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "sessions", "parent_id", "INTEGER")
    _ensure_column(conn, "sessions", "branch_from", "INTEGER")
    _ensure_column(conn, "sessions", "place_id", "INTEGER")
    _ensure_column(conn, "threads", "last_turn", "INTEGER NOT NULL DEFAULT 0")
    default_attrs = json.dumps(
        {"corpo": 10, "mente": 10, "presenca": 10, "sorte": 10},
        ensure_ascii=False,
    )
    conn.execute(
        "UPDATE characters SET attributes=? WHERE kind='protagonist' "
        "AND (attributes IS NULL OR attributes='' OR attributes='{}')",
        (default_attrs,),
    )
    default_skills = json.dumps(
        {
            "luta": 0,
            "atletismo": 0,
            "vigor": 0,
            "furtividade": 0,
            "investigacao": 0,
            "persuasao": 0,
        },
        ensure_ascii=False,
    )
    conn.execute(
        "UPDATE characters SET skills=? WHERE kind='protagonist' "
        "AND (skills IS NULL OR skills='' OR skills='{}')",
        (default_skills,),
    )
    try:
        done = conn.execute(
            "SELECT value FROM fts_meta WHERE key='messages'"
        ).fetchone()
        if not done:
            conn.execute("INSERT INTO message_fts(message_fts) VALUES('rebuild')")
            conn.execute(
                "INSERT OR REPLACE INTO fts_meta (key, value) VALUES ('messages','1')"
            )
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()


def _ensure_column(conn, table, column, ddl):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def rowdict(row):
    return dict(row) if row is not None else None


_FTS_STOP = {
    "para", "como", "voce", "você", "mais", "isso", "aqui", "onde",
    "porque", "sobre", "entre", "quando", "depois", "antes", "nada",
    "alguem", "algum", "muito", "pouco", "esta", "este", "esse", "essa",
}
_FTS_WORD = re.compile(r"[0-9a-záéíóúâêôãõàç]{4,}", re.I)


def search_messages(conn, sid, query, limit=6, exclude_ids=None):
    terms = []
    seen = set()
    for word in _FTS_WORD.findall((query or "").lower()):
        if word in _FTS_STOP or word in seen:
            continue
        seen.add(word)
        terms.append(word)
        if len(terms) >= 12:
            break
    if not terms:
        return []
    match = " OR ".join('"' + term + '"' for term in terms)
    params = [match, sid]
    sql = (
        "SELECT m.id, m.role, m.content, bm25(message_fts) AS rank "
        "FROM message_fts JOIN messages m ON m.id = message_fts.rowid "
        "WHERE message_fts MATCH ? AND m.session_id=? "
    )
    excluded = [int(i) for i in (exclude_ids or [])]
    if excluded:
        sql += "AND m.id NOT IN (" + ",".join("?" for _ in excluded) + ") "
        params.extend(excluded)
    sql += "ORDER BY rank LIMIT ?"
    params.append(max(1, int(limit)))
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []
