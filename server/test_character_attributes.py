"""Ficha: atributos, perícias, orçamento, compatibilidade e migração."""

import os
import tempfile
import unittest

from fastapi.testclient import TestClient

import db
import main


class CharacterSheetTest(unittest.TestCase):
    def test_creation_update_sheet_and_backfill(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        old_db = db.DB_PATH
        old_port = main.DISCOVERY_PORT
        db.DB_PATH = tmp.name
        main.DISCOVERY_PORT = 5197
        try:
            with TestClient(main.app) as client:
                wid = client.post(
                    "/api/worlds", json={"name": "Ficha", "description": ""}
                ).json()["id"]
                npc = client.post(
                    "/api/characters",
                    json={"kind": "npc", "name": "Mira", "world_id": wid},
                ).json()
                hero = client.post(
                    "/api/characters",
                    json={
                        "kind": "protagonist",
                        "name": "Ren",
                        "world_id": wid,
                        "attributes": {
                            "corpo": 16,
                            "mente": 8,
                            "presenca": 12,
                            "sorte": 4,
                        },
                        "skills": {"luta": 3, "furtividade": 2},
                    },
                ).json()
                self.assertEqual(
                    hero["attributes"],
                    {"corpo": 16, "mente": 8, "presenca": 12, "sorte": 4},
                )
                self.assertEqual(hero["skills"]["luta"], 3)
                self.assertEqual(hero["skills"]["furtividade"], 2)
                self.assertEqual(hero["skills"]["vigor"], 0)
                self.assertEqual(npc["attributes"], {})
                self.assertEqual(npc["skills"], {})

                # Cliente antigo (payload sem a ficha) preserva atributos e perícias.
                kept = client.put(
                    f"/api/characters/{hero['id']}",
                    json={"kind": "protagonist", "name": "Ren", "world_id": wid},
                ).json()
                self.assertEqual(kept["attributes"], hero["attributes"])
                self.assertEqual(kept["skills"], hero["skills"])

                clamped = client.put(
                    f"/api/characters/{hero['id']}",
                    json={
                        "kind": "protagonist",
                        "name": "Ren",
                        "world_id": wid,
                        "attributes": {"corpo": 99, "sorte": 1},
                        "skills": {"luta": 9},
                    },
                ).json()
                self.assertEqual(sum(clamped["attributes"].values()), 40)
                self.assertEqual(clamped["attributes"]["corpo"], 17)
                self.assertEqual(clamped["attributes"]["sorte"], 3)
                self.assertEqual(clamped["skills"]["luta"], 5)
                self.assertEqual(sum(clamped["skills"].values()), 5)

                sid = client.post(
                    "/api/sessions",
                    json={
                        "character_id": npc["id"],
                        "protagonist_id": hero["id"],
                    },
                ).json()["id"]
                sheet = client.get(f"/api/sessions/{sid}/sheet").json()
                self.assertEqual(sheet["attributes"], clamped["attributes"])
                self.assertEqual(sheet["skills"], clamped["skills"])
                self.assertEqual(sheet["modifiers"]["combate"]["skill"], "luta")
                self.assertEqual(sheet["modifiers"]["combate"]["skill_level"], 5)
                self.assertEqual(sheet["modifiers"]["combate"]["modifier"], 6)

                # Banco antigo: ficha vazia ganha os padrões.
                conn = db.connect()
                conn.execute(
                    "UPDATE characters SET attributes='{}', skills='{}' WHERE id=?",
                    (hero["id"],),
                )
                conn.commit()
                conn.close()
                db.init()
                fresh = client.get(f"/api/characters/{hero['id']}").json()
                self.assertEqual(
                    fresh["attributes"],
                    {"corpo": 10, "mente": 10, "presenca": 10, "sorte": 10},
                )
                self.assertEqual(
                    fresh["skills"],
                    {
                        "luta": 0,
                        "atletismo": 0,
                        "vigor": 0,
                        "furtividade": 0,
                        "investigacao": 0,
                        "persuasao": 0,
                    },
                )
        finally:
            db.DB_PATH = old_db
            main.DISCOVERY_PORT = old_port
            os.unlink(tmp.name)
