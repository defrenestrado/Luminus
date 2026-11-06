"""Regeneração determinística: reescrever não pode sortear de novo."""

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


def _events(response):
    return [
        json.loads(line) for line in response.text.splitlines() if line.strip()
    ]


class ChatRegenTest(unittest.TestCase):
    def test_regen_reuses_authorized_receipt_after_rewind(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        old_db = db.DB_PATH
        old_port = main.DISCOVERY_PORT
        old_planner = main.PLANNER_ENABLED
        old_guard = main.GUARD_ENABLED
        old_emb = main.embeddings.ENABLED
        old_res = main.RESOLUTION_ENABLED
        old_off = offscreen.OFFSCREEN_EVERY
        old_voice = world_voices.VOICES_EVERY
        db.DB_PATH = tmp.name
        main.DISCOVERY_PORT = 5195
        main.PLANNER_ENABLED = False
        main.GUARD_ENABLED = False
        main.embeddings.ENABLED = False
        main.RESOLUTION_ENABLED = True
        offscreen.OFFSCREEN_EVERY = 9999
        world_voices.VOICES_EVERY = 9999

        async def fake_stream(*args, **kwargs):
            yield "O guarda recua um passo, surpreso com a sua ousadia."

        async def fake_complete(*args, **kwargs):
            return "- Eu ataco o guarda\n- Eu recuo\n- Eu observo"

        try:
            with (
                mock.patch.object(main.llm, "stream_chat", fake_stream),
                mock.patch.object(main.llm, "complete", fake_complete),
            ):
                with TestClient(main.app) as client:
                    wid = client.post(
                        "/api/worlds", json={"name": "Regen", "description": ""}
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
                                "corpo": 14,
                                "mente": 10,
                                "presenca": 10,
                                "sorte": 10,
                            },
                        },
                    ).json()
                    sid = client.post(
                        "/api/sessions",
                        json={
                            "character_id": npc["id"],
                            "protagonist_id": hero["id"],
                        },
                    ).json()["id"]

                    first = client.post(
                        f"/api/sessions/{sid}/chat",
                        json={"message": "Eu ataco o guarda"},
                    )
                    self.assertEqual(first.status_code, 200)
                    events = _events(first)
                    receipt = next(
                        event["resolution"]
                        for event in events
                        if event.get("resolution")
                    )
                    self.assertEqual(events[-1]["resolution"]["seed"], receipt["seed"])

                    messages = client.get(f"/api/sessions/{sid}/messages").json()
                    last_user = next(
                        message
                        for message in reversed(messages)
                        if message["role"] == "user"
                    )
                    self.assertEqual(messages[-1]["role"], "assistant")

                    # Mesmo caminho do app: rewind para a fala, depois regen.
                    rewind = client.post(
                        f"/api/sessions/{sid}/rewind",
                        json={"message_id": last_user["id"]},
                    )
                    self.assertEqual(rewind.status_code, 200)

                    second = client.post(
                        f"/api/sessions/{sid}/chat", json={"regen": True}
                    )
                    self.assertEqual(second.status_code, 200)
                    events2 = _events(second)
                    receipt2 = next(
                        event["resolution"]
                        for event in events2
                        if event.get("resolution")
                    )
                    self.assertEqual(receipt2["roll"], receipt["roll"])
                    self.assertEqual(receipt2["seed"], receipt["seed"])
                    self.assertEqual(receipt2["total"], receipt["total"])
                    self.assertEqual(
                        events2[-1]["resolution"]["roll"], receipt["roll"]
                    )

                    after = client.get(f"/api/sessions/{sid}/messages").json()
                    self.assertEqual(after[-1]["role"], "assistant")
                    self.assertEqual(
                        after[-1]["resolution"]["roll"], receipt["roll"]
                    )

                    # O recibo pendente não vaza para o turno seguinte.
                    conn = db.connect()
                    ledger = json.loads(
                        conn.execute(
                            "SELECT ledger FROM sessions WHERE id=?", (sid,)
                        ).fetchone()["ledger"]
                        or "{}"
                    )
                    conn.close()
                    self.assertNotIn("pending_resolution", ledger)
        finally:
            db.DB_PATH = old_db
            main.DISCOVERY_PORT = old_port
            main.PLANNER_ENABLED = old_planner
            main.GUARD_ENABLED = old_guard
            main.embeddings.ENABLED = old_emb
            main.RESOLUTION_ENABLED = old_res
            offscreen.OFFSCREEN_EVERY = old_off
            world_voices.VOICES_EVERY = old_voice
            os.unlink(tmp.name)


if __name__ == "__main__":
    unittest.main()
