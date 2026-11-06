"""Integração HTTP: world packs e World Voices (sem LLM)."""

import os
import tempfile
import unittest

from fastapi.testclient import TestClient

import db
import main


class FeaturesIntegrationTest(unittest.TestCase):
    def test_world_pack_and_voices_roundtrip(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        old_db = db.DB_PATH
        old_port = main.DISCOVERY_PORT
        db.DB_PATH = tmp.name
        main.DISCOVERY_PORT = 5197
        try:
            with TestClient(main.app) as client:
                world = client.post(
                    "/api/worlds",
                    json={"name": "Porto Cinza", "description": "Cidade portuária."},
                ).json()
                wid = world["id"]
                npc = client.post(
                    "/api/characters",
                    json={
                        "kind": "npc",
                        "name": "Mira",
                        "world_id": wid,
                        "greeting": "A oficina cheira a óleo queimado.",
                        "personality": "Irônica e competente.",
                    },
                ).json()
                protagonist = client.post(
                    "/api/characters",
                    json={"kind": "protagonist", "name": "Ren", "world_id": wid},
                ).json()
                client.post(
                    f"/api/worlds/{wid}/facts",
                    json={"text": "A Guarda cobra propina no porto.", "keys": "guarda"},
                )
                session = client.post(
                    "/api/sessions",
                    json={
                        "character_id": npc["id"],
                        "protagonist_id": protagonist["id"],
                    },
                ).json()
                sid = session["id"]

                pack = client.get(f"/api/worlds/{wid}/export").json()
                self.assertEqual(pack["format"], "luminus-world-pack")
                self.assertEqual(pack["world"]["name"], "Porto Cinza")
                self.assertEqual(len(pack["sessions"]), 1)
                self.assertEqual(len(pack["sessions"][0]["messages"]), 1)

                imported = client.post("/api/worlds/import", json=pack).json()
                new_wid = imported["imported"]["world_id"]
                self.assertNotEqual(new_wid, wid)
                self.assertEqual(imported["imported"]["sessions"], 1)

                voices = client.get(f"/api/sessions/{sid}/voices").json()["voices"]
                self.assertEqual(voices, [])
                voice = client.post(f"/api/sessions/{sid}/voices").json()
                self.assertIn(voice["kind"], ("rumor", "aviso", "carta", "relatorio"))
                self.assertTrue(voice["body"])
                self.assertTrue(voice["claims"])
                voices = client.get(f"/api/sessions/{sid}/voices").json()["voices"]
                self.assertEqual(len(voices), 1)
                self.assertEqual(voices[0]["id"], voice["id"])
        finally:
            db.DB_PATH = old_db
            main.DISCOVERY_PORT = old_port
            try:
                os.unlink(tmp.name)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
