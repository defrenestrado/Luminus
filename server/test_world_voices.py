"""Testes dos World Voices (artefatos com conhecimento limitado)."""

import os
import sqlite3
import tempfile
import time
import unittest

import db
import world_voices


STATE = {
    "turn": 6,
    "seed": "sessao-voz",
    "facts": ["A Guarda cobra propina no porto."],
    "pending": "",
    "mood": "tenso",
    "scene": {"location": "Porto Cinza", "present": ["Mira"]},
}


class GenerateTest(unittest.TestCase):
    def test_generates_on_schedule(self):
        self.assertIsNone(world_voices.generate({"turn": 5, "seed": "x"}, force=False))
        voice = world_voices.generate(dict(STATE), force=False)
        self.assertIsNotNone(voice)
        self.assertIn(voice["kind"], ("rumor", "aviso", "carta", "relatorio"))
        self.assertTrue(voice["body"])
        self.assertTrue(voice["claims"])
        self.assertIn(voice["truth_status"], ("verdadeiro", "distorcido", "incerto", "falso"))

    def test_deterministic(self):
        a = world_voices.generate(dict(STATE), force=True)
        b = world_voices.generate(dict(STATE), force=True)
        self.assertEqual(a["body"], b["body"])
        self.assertEqual(a["author"], b["author"])

    def test_offscreen_material(self):
        voice = world_voices.generate(
            dict(STATE),
            events=[{"npc": "Bram", "text": "Bram prendeu um inocente."}],
            force=True,
        )
        self.assertIn("Bram", voice["body"])

    def test_block_bounded_knowledge(self):
        voice = world_voices.generate(dict(STATE), force=True)
        text = world_voices.block(voice)
        self.assertIn("não necessariamente a verdade", text)
        self.assertIn("não transforme a alegação", text.lower())
        self.assertIn(voice["author"], text)


class PersistenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(db.SCHEMA)
        now = time.time()
        self.conn.execute(
            "INSERT INTO worlds (name, description, created_at) VALUES (?,?,?)",
            ("Porto Cinza", "", now),
        )
        self.wid = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.execute(
            "INSERT INTO characters (name, world_id, created_at) VALUES (?,?,?)",
            ("Mira", self.wid, now),
        )
        self.cid = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.execute(
            "INSERT INTO sessions (character_id, title, created_at, updated_at) "
            "VALUES (?,?,?,?)",
            (self.cid, "Teste", now, now),
        )
        self.sid = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_save_and_recent(self):
        voice = world_voices.generate(
            dict(STATE), force=True, session_id=self.sid, world={"id": self.wid}
        )
        vid = world_voices.save(self.conn, voice)
        self.conn.commit()
        rows = world_voices.recent(self.conn, self.sid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], vid)
        self.assertEqual(rows[0]["kind"], voice["kind"])
        self.assertIsInstance(rows[0]["claims"], list)


if __name__ == "__main__":
    unittest.main()
