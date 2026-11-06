"""Continuidade visual: identidade fixa, seed e retratos consistentes."""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import art
import db
import main


class ArtIdentityTest(unittest.TestCase):
    def test_stable_seed_is_deterministic(self):
        self.assertEqual(art.stable_seed(7, "Mira"), art.stable_seed(7, "Mira"))
        self.assertNotEqual(art.stable_seed(7, "Mira"), art.stable_seed(8, "Mira"))

    def test_local_identity_is_stable_and_preserves_appearance(self):
        body = {
            "character_id": 7,
            "name": "Mira",
            "appearance": "mulher jovem de cabelo vermelho e olhos verdes",
            "tags": ["guarda"],
        }
        first = art._local_identity(body)
        second = art._local_identity(body)
        self.assertEqual(first, second)
        self.assertIn("mulher jovem de cabelo vermelho", first)
        self.assertIn("Fixed palette", first)
        self.assertIn("every image", first)

    def test_different_characters_get_different_features(self):
        mira = art._local_identity({"character_id": 1, "name": "Mira"})
        bram = art._local_identity({"character_id": 2, "name": "Bram"})
        self.assertNotEqual(mira, bram)

    def test_identity_cover_prompt_locks_identity_and_expression(self):
        prompt = art._identity_cover_prompt(
            "Mira. pale skin, amber eyes, short black hair.",
            "tense guarded expression",
            "anime",
        )
        self.assertIn("Mira", prompt)
        self.assertIn("tense guarded expression", prompt)
        self.assertIn("identity lock", prompt.lower())
        self.assertIn("same hairstyle", prompt.lower())
        self.assertIn("no text", prompt.lower())

    def test_saved_identity_is_reused_without_llm(self):
        out = asyncio.run(
            art.ensure_identity(
                {
                    "character_id": 3,
                    "name": "Mira",
                    "visual_prompt": "Mira. fixed visual identity.",
                    "art_seed": 4242,
                    "visual_style": "pintura",
                }
            )
        )
        self.assertEqual(out["prompt"], "Mira. fixed visual identity.")
        self.assertEqual(out["seed"], 4242)
        self.assertEqual(out["style"], "pintura")
        self.assertEqual(out["source"], "saved")


class PortraitConsistencyApiTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = tmp.name
        self.old_db = db.DB_PATH
        db.DB_PATH = self.db_path

    def tearDown(self):
        db.DB_PATH = self.old_db
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_portraits_use_saved_identity_and_seed(self):
        with TestClient(main.app) as client:
            char = client.post(
                "/api/characters",
                json={
                    "kind": "npc",
                    "name": "Mira",
                    "appearance": "mulher jovem de cabelo vermelho",
                },
            ).json()
            identity = "Mira. fixed visual identity with red hair."
            conn = db.connect()
            conn.execute(
                "UPDATE characters SET visual_prompt=?, art_seed=? WHERE id=?",
                (identity, 424242, char["id"]),
            )
            conn.commit()
            conn.close()

            calls = []

            async def fake_cover(body, reload_models=None):
                calls.append(dict(body))
                return {"url": f"/art/{body['expression'].split()[0]}.jpg"}

            with mock.patch.object(main.art, "generate_cover", fake_cover):
                r = client.post(f"/api/characters/{char['id']}/portraits")

            self.assertEqual(r.status_code, 200)
            self.assertEqual(len(calls), 3)
            self.assertTrue(all(c["visual_prompt"] == identity for c in calls))
            self.assertTrue(all(c["seed"] == 424242 for c in calls))
            self.assertEqual(
                {c["expression"] for c in calls},
                {
                    "calm neutral expression",
                    "tense guarded expression, furrowed brows",
                    "warm soft smile",
                },
            )
            self.assertEqual(
                set(r.json()["portraits"]), {"neutral", "tense", "smile"}
            )
            saved = client.get(f"/api/characters/{char['id']}").json()
            self.assertEqual(saved["visual_prompt"], identity)
            self.assertEqual(saved["art_seed"], 424242)
            self.assertEqual(saved["avatar"], "/art/calm.jpg")

    def test_cover_returns_identity_for_new_character(self):
        async def fake_identity(body):
            return "Mira. fixed visual identity.", "local", ""

        calls = []

        async def fake_cover(body, reload_models=None):
            calls.append(dict(body))
            return {"url": "/art/new.jpg"}

        with TestClient(main.app) as client:
            with (
                mock.patch.object(main.art, "_build_identity", fake_identity),
                mock.patch.object(main.art, "generate_cover", fake_cover),
            ):
                r = client.post(
                    "/api/art/cover",
                    json={
                        "name": "Mira",
                        "kind": "npc",
                        "appearance": "cabelo vermelho",
                        "tags": ["guarda"],
                        "style": "anime",
                    },
                )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["visual_prompt"], "Mira. fixed visual identity.")
        self.assertEqual(data["visual_style"], "anime")
        self.assertGreater(data["art_seed"], 0)
        self.assertEqual(calls[0]["visual_prompt"], data["visual_prompt"])
        self.assertEqual(calls[0]["seed"], data["art_seed"])

    def test_appearance_change_clears_identity_and_portraits(self):
        with TestClient(main.app) as client:
            char = client.post(
                "/api/characters",
                json={"kind": "npc", "name": "Mira", "appearance": "cabelo curto"},
            ).json()
            conn = db.connect()
            conn.execute(
                "UPDATE characters SET visual_prompt='old', art_seed=9,"
                " avatar='/art/old.jpg',"
                " portraits='{\"neutral\":\"/art/old.jpg\"}' WHERE id=?",
                (char["id"],),
            )
            conn.commit()
            conn.close()

            updated = client.put(
                f"/api/characters/{char['id']}",
                json={
                    "kind": "npc",
                    "name": "Mira",
                    "avatar": "/art/old.jpg",
                    "appearance": "cabelo longo e prateado",
                },
            ).json()

            self.assertEqual(updated["visual_prompt"], "")
            self.assertEqual(updated["art_seed"], 0)
            self.assertEqual(updated["portraits"], {})
            self.assertEqual(updated["avatar"], "")

    def test_new_identity_survives_appearance_change(self):
        with TestClient(main.app) as client:
            char = client.post(
                "/api/characters",
                json={"kind": "npc", "name": "Mira", "appearance": "cabelo curto"},
            ).json()
            conn = db.connect()
            conn.execute(
                "UPDATE characters SET visual_prompt='old', art_seed=9,"
                " portraits='{\"neutral\":\"/art/old.jpg\"}' WHERE id=?",
                (char["id"],),
            )
            conn.commit()
            conn.close()

            updated = client.put(
                f"/api/characters/{char['id']}",
                json={
                    "kind": "npc",
                    "name": "Mira",
                    "avatar": "/art/new.jpg",
                    "appearance": "cabelo longo e prateado",
                    "visual_prompt": "Mira. new fixed identity.",
                    "art_seed": 555,
                    "visual_style": "anime",
                },
            ).json()

            self.assertEqual(updated["visual_prompt"], "Mira. new fixed identity.")
            self.assertEqual(updated["art_seed"], 555)
            self.assertEqual(updated["portraits"], {})
            self.assertEqual(updated["avatar"], "/art/new.jpg")


if __name__ == "__main__":
    unittest.main()
