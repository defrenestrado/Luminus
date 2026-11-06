"""World Packs: exportar/importar um mundo completo do Luminus.

Formato inspirado no covel: mundo + elenco + fatos/lore + sessões (mensagens,
estados, elenco, capítulos, threads, fatos por entidade e World Voices).
Vetores de embedding e checks derivados ficam de fora (são reconstruíveis).
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

PACK_FORMAT = "luminus-world-pack"
PACK_VERSION = 1

_CHAR_COLS = (
    "kind", "name", "avatar", "tagline", "description_public", "persona",
    "greeting", "appearance", "personality", "backstory", "speech_style",
    "likes", "dislikes", "fears", "relationship", "secrets",
    "example_dialogue", "tags", "world_id", "model", "voice", "created_at",
)
_SESSION_COLS = (
    "title", "summary", "summarized_until", "turn_count", "opening_generated",
    "scene_image", "scene_prompt", "ledger", "recap", "place_id", "created_at",
    "updated_at",
)
_PLACE_COLS = (
    "world_id", "name", "kind", "description", "atmosphere", "rules", "tags",
    "created_at",
)


def _rows(conn, query: str, params: Iterable[Any]) -> List[Dict[str, Any]]:
    return [dict(row) for row in conn.execute(query, tuple(params)).fetchall()]


def export_world(conn, world_id: int, include_sessions: bool = True) -> Dict[str, Any]:
    world_row = conn.execute("SELECT * FROM worlds WHERE id=?", (world_id,)).fetchone()
    if world_row is None:
        raise ValueError("mundo nao encontrado")
    world = dict(world_row)
    facts = _rows(
        conn,
        "SELECT * FROM world_facts WHERE world_id=? ORDER BY id",
        (world_id,),
    )
    chars = _rows(
        conn,
        "SELECT * FROM characters WHERE world_id=? ORDER BY id",
        (world_id,),
    )
    places = _rows(
        conn,
        "SELECT * FROM places WHERE world_id=? ORDER BY id",
        (world_id,),
    )
    sessions: List[Dict[str, Any]] = []
    if include_sessions:
        session_rows = _rows(
            conn,
            "SELECT * FROM sessions WHERE character_id IN "
            "(SELECT id FROM characters WHERE world_id=?) ORDER BY id",
            (world_id,),
        )
        char_ids = {c["id"] for c in chars}
        for sess in session_rows:
            char_ids.add(sess["character_id"])
            if sess.get("protagonist_id"):
                char_ids.add(sess["protagonist_id"])
        if char_ids:
            placeholders = ",".join("?" for _ in char_ids)
            extra_chars = _rows(
                conn,
                f"SELECT * FROM characters WHERE id IN ({placeholders})",
                char_ids,
            )
            by_id = {c["id"]: c for c in chars}
            for char in extra_chars:
                by_id.setdefault(char["id"], char)
            chars = list(by_id.values())
        for sess in session_rows:
            sid = sess["id"]
            sessions.append(
                {
                    "session": sess,
                    "messages": _rows(
                        conn,
                        "SELECT * FROM messages WHERE session_id=? ORDER BY id",
                        (sid,),
                    ),
                    "message_states": _rows(
                        conn,
                        "SELECT * FROM message_states WHERE session_id=? ORDER BY message_id",
                        (sid,),
                    ),
                    "cast": _rows(
                        conn,
                        "SELECT * FROM session_cast WHERE session_id=? ORDER BY position",
                        (sid,),
                    ),
                    "chapters": _rows(
                        conn,
                        "SELECT * FROM chapters WHERE session_id=? ORDER BY id",
                        (sid,),
                    ),
                    "threads": _rows(
                        conn,
                        "SELECT * FROM threads WHERE session_id=? ORDER BY id",
                        (sid,),
                    ),
                    "entity_facts": _rows(
                        conn,
                        "SELECT * FROM entity_facts WHERE session_id=? ORDER BY id",
                        (sid,),
                    ),
                    "voices": _rows(
                        conn,
                        "SELECT * FROM world_voices WHERE session_id=? ORDER BY id",
                        (sid,),
                    ),
                }
            )
    world_voices = _rows(
        conn,
        "SELECT * FROM world_voices WHERE world_id=? AND session_id IS NULL "
        "ORDER BY id",
        (world_id,),
    )
    return {
        "format": PACK_FORMAT,
        "version": PACK_VERSION,
        "exported_at": time.time(),
        "world": world,
        "world_facts": facts,
        "places": places,
        "characters": chars,
        "world_voices": world_voices,
        "sessions": sessions,
        "meta": {
            "characters": len(chars),
            "sessions": len(sessions),
            "facts": len(facts),
            "places": len(places),
            "includes_sessions": bool(include_sessions),
            "world_voices": len(world_voices),
        },
    }


def _insert_fields(conn, table: str, data: Dict[str, Any], fields: Iterable[str]) -> int:
    cols = [field for field in fields if field in data]
    if not cols:
        raise ValueError(f"nenhum campo para inserir em {table}")
    sql = (
        f"INSERT INTO {table} ({','.join(cols)}) "
        f"VALUES ({','.join('?' for _ in cols)})"
    )
    cur = conn.execute(sql, tuple(data.get(col) for col in cols))
    return int(cur.lastrowid)


def import_world(conn, pack: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(pack, dict) or pack.get("format") != PACK_FORMAT:
        raise ValueError("formato de world pack invalido")
    version = int(pack.get("version") or 0)
    if version > PACK_VERSION:
        raise ValueError(f"world pack versao {version} nao suportada")
    world = pack.get("world") or {}
    name = str(world.get("name") or "").strip()
    if not name:
        raise ValueError("world pack sem nome de mundo")

    now = time.time()
    wid = _insert_fields(
        conn,
        "worlds",
        {
            "name": name,
            "description": world.get("description") or "",
            "created_at": world.get("created_at") or now,
        },
        ("name", "description", "created_at"),
    )

    place_map: Dict[int, int] = {}
    for place in pack.get("places") or []:
        data = dict(place)
        old_id = data.pop("id", None)
        if old_id is None:
            continue
        data["world_id"] = wid
        data.setdefault("created_at", now)
        new_id = _insert_fields(conn, "places", data, _PLACE_COLS)
        place_map[int(old_id)] = new_id

    char_map: Dict[int, int] = {}
    for char in pack.get("characters") or []:
        data = dict(char)
        old_id = data.pop("id", None)
        if old_id is None:
            continue
        data["world_id"] = wid if data.get("world_id") else None
        new_id = _insert_fields(conn, "characters", data, _CHAR_COLS)
        char_map[int(old_id)] = new_id

    for fact in pack.get("world_facts") or []:
        _insert_fields(
            conn,
            "world_facts",
            {
                "world_id": wid,
                "text": fact.get("text") or "",
                "keys": fact.get("keys") or "",
                "created_at": fact.get("created_at") or now,
            },
            ("world_id", "text", "keys", "created_at"),
        )

    counts = {
        "characters": len(char_map),
        "facts": len(pack.get("world_facts") or []),
        "places": len(place_map),
        "sessions": 0,
        "messages": 0,
        "voices": 0,
        "world_voices": 0,
    }
    for voice in pack.get("world_voices") or []:
        _insert_fields(
            conn,
            "world_voices",
            {
                "session_id": None,
                "world_id": wid,
                "turn": int(voice.get("turn", 0) or 0),
                "kind": voice.get("kind") or "rumor",
                "title": voice.get("title") or "",
                "author": voice.get("author") or "",
                "body": voice.get("body") or "",
                "claims": voice.get("claims") or "[]",
                "truth_status": voice.get("truth_status") or "incerto",
                "known_by": voice.get("known_by") or "[]",
                "visibility": voice.get("visibility") or "rumor",
                "source": voice.get("source") or "",
                "created_at": voice.get("created_at") or now,
            },
            (
                "session_id", "world_id", "turn", "kind", "title", "author",
                "body", "claims", "truth_status", "known_by", "visibility",
                "source", "created_at",
            ),
        )
        counts["world_voices"] += 1

    session_map: Dict[int, int] = {}
    parent_map: Dict[int, Tuple[Optional[int], Optional[int]]] = {}
    for bundle in pack.get("sessions") or []:
        sess = dict(bundle.get("session") or {})
        old_sid = sess.get("id")
        if old_sid is None:
            continue
        old_char = sess.get("character_id")
        new_char = char_map.get(int(old_char)) if old_char is not None else None
        if new_char is None:
            # Sessão sem personagem não é importável com segurança.
            continue
        old_prot = sess.get("protagonist_id")
        new_prot = char_map.get(int(old_prot)) if old_prot else None
        data = {field: sess.get(field) for field in _SESSION_COLS}
        old_place = sess.get("place_id")
        data.update(
            {
                "character_id": new_char,
                "protagonist_id": new_prot,
                "place_id": (
                    place_map.get(int(old_place)) if old_place else None
                ),
                "created_at": sess.get("created_at") or now,
                "updated_at": sess.get("updated_at") or now,
            }
        )
        new_sid = _insert_fields(
            conn,
            "sessions",
            data,
            ("character_id", "protagonist_id", *_SESSION_COLS),
        )
        session_map[int(old_sid)] = new_sid
        parent_map[int(old_sid)] = (sess.get("parent_id"), sess.get("branch_from"))
        counts["sessions"] += 1

        msg_map: Dict[int, int] = {}
        for msg in bundle.get("messages") or []:
            old_mid = msg.get("id")
            if old_mid is None:
                continue
            new_mid = _insert_fields(
                conn,
                "messages",
                {
                    "session_id": new_sid,
                    "role": msg.get("role") or "assistant",
                    "content": msg.get("content") or "",
                    "created_at": msg.get("created_at") or now,
                },
                ("session_id", "role", "content", "created_at"),
            )
            msg_map[int(old_mid)] = new_mid
            counts["messages"] += 1

        for state in bundle.get("message_states") or []:
            old_mid = state.get("message_id")
            if old_mid is None or int(old_mid) not in msg_map:
                continue
            _insert_fields(
                conn,
                "message_states",
                {
                    "message_id": msg_map[int(old_mid)],
                    "session_id": new_sid,
                    "state": state.get("state") or "{}",
                    "created_at": state.get("created_at") or now,
                },
                ("message_id", "session_id", "state", "created_at"),
            )

        for cast in bundle.get("cast") or []:
            char_id = cast.get("character_id")
            new_id = char_map.get(int(char_id)) if char_id is not None else None
            if new_id is None:
                continue
            _insert_fields(
                conn,
                "session_cast",
                {
                    "session_id": new_sid,
                    "character_id": new_id,
                    "position": int(cast.get("position", 0) or 0),
                    "created_at": cast.get("created_at") or now,
                },
                ("session_id", "character_id", "position", "created_at"),
            )

        for chapter in bundle.get("chapters") or []:
            start = chapter.get("start_id")
            end = chapter.get("end_id")
            _insert_fields(
                conn,
                "chapters",
                {
                    "session_id": new_sid,
                    "number": int(chapter.get("number", 0) or 0),
                    "summary": chapter.get("summary") or "",
                    "start_id": msg_map.get(int(start), 0) if start else 0,
                    "end_id": msg_map.get(int(end), 0) if end else 0,
                    "created_at": chapter.get("created_at") or now,
                },
                ("session_id", "number", "summary", "start_id", "end_id", "created_at"),
            )

        for thread in bundle.get("threads") or []:
            _insert_fields(
                conn,
                "threads",
                {
                    "session_id": new_sid,
                    "kind": thread.get("kind") or "promessa",
                    "title": thread.get("title") or "",
                    "status": thread.get("status") or "aberta",
                    "setup": thread.get("setup") or "",
                    "payoff": thread.get("payoff") or "",
                    "due_turn": int(thread.get("due_turn", 0) or 0),
                    "entities": thread.get("entities") or "[]",
                    "last_turn": int(thread.get("last_turn", 0) or 0),
                    "created_at": thread.get("created_at") or now,
                    "updated_at": thread.get("updated_at") or now,
                },
                (
                    "session_id", "kind", "title", "status", "setup", "payoff",
                    "due_turn", "entities", "last_turn", "created_at", "updated_at",
                ),
            )

        for fact in bundle.get("entity_facts") or []:
            _insert_fields(
                conn,
                "entity_facts",
                {
                    "session_id": new_sid,
                    "entity": fact.get("entity") or "",
                    "fact": fact.get("fact") or "",
                    "known_by": fact.get("known_by") or "[]",
                    "source_turn": int(fact.get("source_turn", 0) or 0),
                    "created_at": fact.get("created_at") or now,
                },
                ("session_id", "entity", "fact", "known_by", "source_turn", "created_at"),
            )

        for voice in bundle.get("voices") or []:
            _insert_fields(
                conn,
                "world_voices",
                {
                    "session_id": new_sid,
                    "world_id": wid,
                    "turn": int(voice.get("turn", 0) or 0),
                    "kind": voice.get("kind") or "rumor",
                    "title": voice.get("title") or "",
                    "author": voice.get("author") or "",
                    "body": voice.get("body") or "",
                    "claims": voice.get("claims") or "[]",
                    "truth_status": voice.get("truth_status") or "incerto",
                    "known_by": voice.get("known_by") or "[]",
                    "visibility": voice.get("visibility") or "rumor",
                    "source": voice.get("source") or "",
                    "created_at": voice.get("created_at") or now,
                },
                (
                    "session_id", "world_id", "turn", "kind", "title", "author",
                    "body", "claims", "truth_status", "known_by", "visibility",
                    "source", "created_at",
                ),
            )
            counts["voices"] += 1

    # Remapeia parent/branch depois que todos os ids existem.
    for old_sid, (parent, branch) in parent_map.items():
        new_sid = session_map.get(old_sid)
        if new_sid is None:
            continue
        conn.execute(
            "UPDATE sessions SET parent_id=?, branch_from=? WHERE id=?",
            (
                session_map.get(int(parent)) if parent else None,
                session_map.get(int(branch)) if branch else None,
                new_sid,
            ),
        )

    counts["world_id"] = wid
    counts["session_map"] = {str(k): v for k, v in session_map.items()}
    return counts
