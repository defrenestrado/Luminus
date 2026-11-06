"""Testes de export/import de World Packs."""

import json
import os
import sqlite3
import tempfile
import time
import unittest

import db
import world_pack


class WorldPackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(db.SCHEMA)
        db._ensure_column(self.conn, "sessions", "parent_id", "INTEGER")
        db._ensure_column(self.conn, "sessions", "branch_from", "INTEGER")
        db._ensure_column(self.conn, "sessions", "place_id", "INTEGER")
        now = time.time()
        self.conn.execute(
            "INSERT INTO worlds (name, description, created_at) VALUES (?,?,?)",
            ("Porto Cinza", "Cidade portuária.", now),
        )
        self.wid = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.execute(
            "INSERT INTO world_facts (world_id, text, keys, created_at) VALUES (?,?,?,?)",
            (self.wid, "A Guarda cobra propina no porto.", "guarda,porto", now),
        )
        self.conn.execute(
            "INSERT INTO characters (kind, name, personality, world_id, created_at) "
            "VALUES (?,?,?,?,?)",
            ("npc", "Mira", "Irônica e competente.", self.wid, now),
        )
        self.npc_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.execute(
            "INSERT INTO characters (kind, name, world_id, created_at) VALUES (?,?,?,?)",
            ("protagonist", "Ren", self.wid, now),
        )
        self.prot_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.execute(
            "INSERT INTO sessions (character_id, protagonist_id, title, turn_count, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (self.npc_id, self.prot_id, "Chegada", 1, now, now),
        )
        self.sid = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?,?,?,?)",
            (self.sid, "user", "Eu entro no porto.", now),
        )
        mid = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.execute(
            "INSERT INTO message_states (message_id, session_id, state, created_at) "
            "VALUES (?,?,?,?)",
            (mid, self.sid, json.dumps({"turn": 1}), now),
        )
        self.conn.execute(
            "INSERT INTO session_cast (session_id, character_id, position, created_at) "
            "VALUES (?,?,?,?)",
            (self.sid, self.npc_id, 0, now),
        )
        self.conn.execute(
            "INSERT INTO threads (session_id, kind, title, created_at, updated_at) "
            "VALUES (?,?,?,?,?)",
            (self.sid, "promessa", "Pagar a dívida", now, now),
        )
        self.conn.execute(
            "INSERT INTO world_voices (session_id, world_id, turn, kind, title, "
            "author, body, claims, truth_status, known_by, visibility, source, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                self.sid, self.wid, 1, "rumor", "Boato", "um vizinho",
                "Dizem que a Guarda cobra propina.", '["Guarda cobra propina"]',
                "verdadeiro", '["um vizinho"]', "publico", "estado", now,
            ),
        )
        self.conn.execute(
            "INSERT INTO world_voices (session_id, world_id, turn, kind, title, "
            "author, body, claims, truth_status, known_by, visibility, source, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                None, self.wid, 0, "aviso", "Aviso do porto", "a Guarda",
                "A Guarda fechou o cais.", '["cais fechado"]',
                "incerto", '["a Guarda"]', "publico", "estado", now,
            ),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_export_contains_structure(self):
        pack = world_pack.export_world(self.conn, self.wid)
        self.assertEqual(pack["format"], world_pack.PACK_FORMAT)
        self.assertEqual(pack["world"]["name"], "Porto Cinza")
        self.assertEqual(len(pack["characters"]), 2)
        self.assertEqual(len(pack["sessions"]), 1)
        self.assertEqual(len(pack["sessions"][0]["messages"]), 1)
        self.assertEqual(len(pack["sessions"][0]["voices"]), 1)
        self.assertEqual(len(pack["world_voices"]), 1)

    def test_export_without_sessions(self):
        pack = world_pack.export_world(self.conn, self.wid, include_sessions=False)
        self.assertEqual(pack["sessions"], [])

    def test_import_creates_new_ids_and_keeps_data(self):
        pack = world_pack.export_world(self.conn, self.wid)
        counts = world_pack.import_world(self.conn, pack)
        self.conn.commit()
        new_wid = counts["world_id"]
        self.assertNotEqual(new_wid, self.wid)
        self.assertEqual(counts["characters"], 2)
        self.assertEqual(counts["sessions"], 1)
        self.assertEqual(counts["messages"], 1)
        self.assertEqual(counts["voices"], 1)
        self.assertEqual(counts["world_voices"], 1)

        world = self.conn.execute(
            "SELECT * FROM worlds WHERE id=?", (new_wid,)
        ).fetchone()
        self.assertEqual(world["name"], "Porto Cinza")
        chars = self.conn.execute(
            "SELECT * FROM characters WHERE world_id=? ORDER BY id", (new_wid,)
        ).fetchall()
        self.assertEqual({c["name"] for c in chars}, {"Mira", "Ren"})
        sess = self.conn.execute(
            "SELECT * FROM sessions WHERE character_id IN "
            "(SELECT id FROM characters WHERE world_id=?)", (new_wid,)
        ).fetchone()
        self.assertNotEqual(sess["id"], self.sid)
        self.assertIn(sess["character_id"], {c["id"] for c in chars})
        msgs = self.conn.execute(
            "SELECT * FROM messages WHERE session_id=?", (sess["id"],)
        ).fetchall()
        self.assertEqual(len(msgs), 1)
        voices = self.conn.execute(
            "SELECT * FROM world_voices WHERE session_id=?", (sess["id"],)
        ).fetchall()
        self.assertEqual(len(voices), 1)
        self.assertEqual(voices[0]["world_id"], new_wid)

    def test_invalid_pack_is_rejected(self):
        with self.assertRaises(ValueError):
            world_pack.import_world(self.conn, {"format": "nope"})


if __name__ == "__main__":
    unittest.main()
