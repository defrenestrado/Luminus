"""Coerencia de ambiente: local da cena x local do estado."""

import unittest

import aux

NPC = {"id": 1, "name": "Lala"}


def make_state(location="Nadir"):
    return {
        "turn": 3,
        "location": location,
        "scene": {
            "id": 1,
            "title": "Consultorio",
            "location": "Nadir",
            "present": ["Lala"],
            "action": "iniciar",
            "turn": 2,
            "ended": False,
            "transition": "chegada",
            "reason": "abertura",
        },
    }


class SceneCoherenceTest(unittest.TestCase):
    def test_location_change_on_continue_promotes_to_swap(self):
        st = make_state()
        st["location"] = "Consultorio em Nadir"
        plan = {"cena": {"acao": "continuar", "presentes": ["Lala"]}, "foco": "Lala"}
        scene = aux.apply_scene(st, plan, NPC, [])
        self.assertEqual(scene["action"], "trocar")
        self.assertEqual(scene["id"], 2)
        self.assertEqual(scene["location"], "Consultorio em Nadir")
        self.assertTrue(scene["transition"])
        self.assertIn("Deslocamento", scene["transition"])

    def test_continue_clears_spurious_transition(self):
        st = make_state()
        plan = {
            "cena": {
                "acao": "continuar",
                "presentes": ["Lala"],
                "transicao": "silencio",
                "motivo": "jogador parado",
            },
            "foco": "Lala",
        }
        scene = aux.apply_scene(st, plan, NPC, [])
        self.assertEqual(scene["action"], "continuar")
        self.assertEqual(scene["transition"], "")
        self.assertEqual(scene["reason"], "")

    def test_explicit_swap_uses_local(self):
        st = make_state()
        plan = {
            "cena": {
                "acao": "trocar",
                "local": "Docas",
                "presentes": ["Lala"],
                "transicao": "corrida",
            },
            "foco": "Lala",
        }
        scene = aux.apply_scene(st, plan, NPC, [])
        self.assertEqual(scene["id"], 2)
        self.assertEqual(scene["location"], "Docas")
        self.assertEqual(st["location"], "Docas")
        self.assertEqual(scene["transition"], "corrida")


if __name__ == "__main__":
    unittest.main()
