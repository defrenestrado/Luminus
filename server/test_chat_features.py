"""Integração do chat: recibos, off-screen e World Voices (LLM mockado)."""

import json
import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import db
import main
import offscreen
import world_voices


class ChatFeaturesTest(unittest.TestCase):
    def test_chat_creates_receipt_offscreen_and_voice(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        old_db = db.DB_PATH
        old_port = main.DISCOVERY_PORT
        old_planner = main.PLANNER_ENABLED
        old_guard = main.GUARD_ENABLED
        old_emb = main.embeddings.ENABLED
        old_off = offscreen.OFFSCREEN_EVERY
        old_voice = world_voices.VOICES_EVERY
        db.DB_PATH = tmp.name
        main.DISCOVERY_PORT = 5196
        main.PLANNER_ENABLED = False
        main.GUARD_ENABLED = False
        main.embeddings.ENABLED = False
        offscreen.OFFSCREEN_EVERY = 2
        world_voices.VOICES_EVERY = 2

        async def fake_stream(*args, **kwargs):
            yield "O guarda recua um passo, surpreso com a sua ousadia."

        async def fake_complete(*args, **kwargs):
            return (
                "- Eu ataco o guarda\n"
                "- Eu observo a rua com calma\n"
                "- Eu pergunto sobre a chave\n"
            )

        try:
            with (
                mock.patch.object(main.llm, "stream_chat", fake_stream),
                mock.patch.object(main.llm, "complete", fake_complete),
            ):
                with TestClient(main.app) as client:
                    wid = client.post(
                        "/api/worlds", json={"name": "Teste", "description": ""}
                    ).json()["id"]
                    npc = client.post(
                        "/api/characters",
                        json={"kind": "npc", "name": "Mira", "world_id": wid},
                    ).json()
                    protagonist = client.post(
                        "/api/characters",
                        json={"kind": "protagonist", "name": "Ren", "world_id": wid},
                    ).json()
                    bram = client.post(
                        "/api/characters",
                        json={"kind": "npc", "name": "Bram", "world_id": wid,
                              "tags": ["guarda"]},
                    ).json()
                    sid = client.post(
                        "/api/sessions",
                        json={
                            "character_id": npc["id"],
                            "protagonist_id": protagonist["id"],
                            "cast_ids": [bram["id"]],
                        },
                    ).json()["id"]
                    choices = client.post(
                        f"/api/sessions/{sid}/choices"
                    ).json()["choices"]
                    self.assertEqual(len(choices), 3)
                    self.assertTrue(all("text" in choice for choice in choices))
                    self.assertTrue(any(choice.get("risk") for choice in choices))
                    self.assertTrue(
                        any(choice.get("difficulty") for choice in choices)
                    )
                    # Bram está fora da cena: o tick off-screen deve encontrá-lo.
                    dbc = db.connect()
                    st = json.loads(
                        dbc.execute(
                            "SELECT ledger FROM sessions WHERE id=?", (sid,)
                        ).fetchone()["ledger"] or "{}"
                    )
                    st.setdefault("cast", {})[str(bram["id"])] = {
                        "status": "fora da cena", "name": "Bram"
                    }
                    dbc.execute(
                        "UPDATE sessions SET ledger=? WHERE id=?",
                        (json.dumps(st), sid),
                    )
                    dbc.commit()
                    dbc.close()

                    r1 = client.post(
                        f"/api/sessions/{sid}/chat",
                        json={"message": "Eu ataco o guarda"},
                    )
                    self.assertEqual(r1.status_code, 200)
                    self.assertIn("done", r1.text)
                    events = [
                        json.loads(line)
                        for line in r1.text.splitlines()
                        if line.strip()
                    ]
                    self.assertTrue(
                        any(event.get("resolution") for event in events)
                    )
                    self.assertIsNotNone(events[-1].get("resolution"))
                    r2 = client.post(
                        f"/api/sessions/{sid}/chat",
                        json={"message": "Eu observo a rua com calma"},
                    )
                    self.assertEqual(r2.status_code, 200)
                    messages = client.get(
                        f"/api/sessions/{sid}/messages"
                    ).json()
                    self.assertTrue(
                        any(message.get("resolution") for message in messages)
                    )
                    sheet = client.get(f"/api/sessions/{sid}/sheet").json()
                    self.assertIn("inventory", sheet)
                    self.assertIn("inventory_detail", sheet)
                    self.assertIn("injuries_detail", sheet)
                    self.assertIn("threads", sheet)
                    self.assertIn("cast", sheet)
                    self.assertTrue(
                        all(
                            "total" in entry
                            for entry in (sheet.get("modifiers") or {}).values()
                        )
                    )
                    sessions = client.get("/api/sessions").json()
                    self.assertTrue(
                        any(session.get("last_line") for session in sessions)
                    )
                    threads = client.get(
                        f"/api/sessions/{sid}/threads"
                    ).json()
                    self.assertIn("threads", threads)
                    self.assertIn("stats", threads)
                    search = client.get(
                        f"/api/sessions/{sid}/search",
                        params={"q": "guarda"},
                    ).json()
                    self.assertIn("results", search)
                    self.assertTrue(search["results"])
                    chronicle = client.get(
                        f"/api/sessions/{sid}/chronicle"
                    ).json()
                    self.assertIn("markdown", chronicle)
                    self.assertIn("#", chronicle["markdown"])
                    digest = client.get(
                        f"/api/sessions/{sid}/digest"
                    ).json()
                    self.assertIn("turn", digest)
                    self.assertIn("offscreen", digest)
                    self.assertIn("voices", digest)
                    director = client.get(
                        f"/api/sessions/{sid}/director"
                    ).json()
                    self.assertIn("state", director)
                    self.assertIn("scene", director)
                    self.assertIn("metrics", director)
                    chapters = client.get(
                        f"/api/sessions/{sid}/chapters"
                    ).json()
                    self.assertIsInstance(chapters, list)
                    codex = client.get(
                        f"/api/sessions/{sid}/codex"
                    ).json()
                    self.assertIn("people", codex)
                    self.assertIn("locations", codex)
                    self.assertIn("world_facts", codex)
                    self.assertIn("voices", codex)
                    client.post(f"/api/sessions/{sid}/branch", json={})
                    tree = client.get(
                        f"/api/sessions/{sid}/tree"
                    ).json()
                    self.assertGreaterEqual(len(tree["sessions"]), 2)

            conn = db.connect()
            row = conn.execute(
                "SELECT ledger FROM sessions WHERE id=?", (sid,)
            ).fetchone()
            ledger = json.loads(row["ledger"])
            self.assertTrue(ledger.get("resolution") or ledger.get("resolution_history"))
            self.assertGreaterEqual(int(ledger.get("last_voice_turn", -1)), 2)
            self.assertTrue(ledger.get("voices"))
            self.assertTrue(ledger.get("offscreen"))
            voices = conn.execute(
                "SELECT count(*) FROM world_voices WHERE session_id=?", (sid,)
            ).fetchone()[0]
            self.assertGreaterEqual(voices, 1)
            conn.close()
        finally:
            db.DB_PATH = old_db
            main.DISCOVERY_PORT = old_port
            main.PLANNER_ENABLED = old_planner
            main.GUARD_ENABLED = old_guard
            main.embeddings.ENABLED = old_emb
            offscreen.OFFSCREEN_EVERY = old_off
            world_voices.VOICES_EVERY = old_voice
            try:
                os.unlink(tmp.name)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
