"""Testes da vida fora de cena (agendas de NPCs)."""

import unittest

import offscreen


CAST = [
    {"id": 2, "name": "Bram", "tags": "guarda, patrulha", "personality": "leal e teimoso"},
    {"id": 3, "name": "Yara", "tags": "mercadora", "personality": "negociante"},
    {"id": 4, "name": "Sombra", "tags": "ladrao, contrabando", "personality": "calculista"},
]


class OffscreenTest(unittest.TestCase):
    def _state(self, turn=4, present=None):
        return {
            "turn": turn,
            "seed": "sessao-7",
            "scene": {"location": "Porto Cinza", "present": present or []},
            "offscreen": [],
        }

    def test_ticks_every_n_turns(self):
        self.assertEqual(offscreen.tick(self._state(turn=3), CAST), [])
        events = offscreen.tick(self._state(turn=4), CAST)
        self.assertTrue(1 <= len(events) <= 2)
        self.assertTrue(all(e["npc"] for e in events))

    def test_skips_present_characters(self):
        state = self._state(present=["Bram", "Yara", "Sombra"])
        self.assertEqual(offscreen.tick(state, CAST), [])

    def test_deterministic(self):
        a = offscreen.tick(self._state(), CAST)
        b = offscreen.tick(self._state(), CAST)
        self.assertEqual(a, b)

    def test_state_is_bounded(self):
        state = self._state(turn=4)
        for turn in range(4, 100, 4):
            state["turn"] = turn
            offscreen.tick(state, CAST)
        self.assertLessEqual(len(state["offscreen"]), 12)

    def test_block_has_bounded_knowledge_rule(self):
        events = offscreen.tick(self._state(), CAST)
        text = offscreen.block(events)
        self.assertIn("NÃO sabe", text)
        self.assertIn("onisciente", text)

    def test_force_even_off_turn(self):
        events = offscreen.tick(self._state(turn=3), CAST, force=True)
        self.assertTrue(events)


if __name__ == "__main__":
    unittest.main()
