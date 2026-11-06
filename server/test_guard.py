"""Guarda pos-geracao: camera do NPC e puppeting do jogador."""

import unittest

import guard

NPC = {"name": "Lala", "secrets": ""}


class GuardTest(unittest.TestCase):
    def test_detects_npc_interiority(self):
        text = "Ele esta calculando, pensa Lala, sentindo a adrenalina subir."
        issues = guard.detect(text, NPC, {}, "")
        self.assertIn("narra interioridade do NPC", issues)
        fixed = guard.clean(text, issues, NPC)
        self.assertNotIn("pensa Lala", fixed)

    def test_detects_npc_internal_verb_before_name(self):
        text = "Lala sente o cheiro de ozonio e recua."
        issues = guard.detect(text, NPC, {}, "")
        self.assertIn("narra interioridade do NPC", issues)

    def test_allows_external_behavior(self):
        text = "Lala cruza os bracos, desvia o olhar e segura a respiracao."
        issues = guard.detect(text, NPC, {}, "")
        self.assertNotIn("narra interioridade do NPC", issues)

    def test_detects_player_puppeting(self):
        text = "Voce decide que ja viu o suficiente e se levanta."
        issues = guard.detect(text, NPC, {}, "")
        self.assertIn("narra pensamento/decisao do jogador", issues)


if __name__ == "__main__":
    unittest.main()
