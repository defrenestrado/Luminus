"""Testes da resolução determinística (recibos)."""

import unittest

import resolution


class ClassifyTest(unittest.TestCase):
    def test_intents(self):
        self.assertEqual(resolution.classify("Eu ataco o guarda"), "combate")
        self.assertEqual(resolution.classify("Vou me esconder atrás das caixas"), "furtividade")
        self.assertEqual(resolution.classify("Tento convencer ela a me ajudar"), "social")
        self.assertEqual(resolution.classify("Eu procuro pistas no quarto"), "investigacao")
        self.assertEqual(resolution.classify("Corro até a saída"), "movimento")
        self.assertEqual(resolution.classify("Resisto à pressão"), "resistencia")

    def test_trivial_action_has_no_roll(self):
        state = {"turn": 3, "tension": 0, "injuries": [], "nr": 20}
        self.assertIsNone(resolution.resolve("Eu entro na sala", state, seed="x"))
        self.assertIsNone(resolution.resolve("Eu pergunto o nome dela", state, seed="x"))
        self.assertIsNone(resolution.resolve("Eu olho pela janela", state, seed="x"))

    def test_risky_investigation_has_roll(self):
        state = {"turn": 3, "tension": 0, "injuries": [], "nr": 20}
        receipt = resolution.resolve("Eu procuro uma armadilha escondida", state, seed="x")
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.intent, "investigacao")


class DeterminismTest(unittest.TestCase):
    def _state(self):
        return {
            "turn": 7,
            "tension": 2,
            "injuries": [],
            "nr": 50,
            "inventory": ["faca"],
            "seed": "sessao-1",
            "roll_seq": 0,
        }

    def test_same_seed_same_receipt(self):
        a = resolution.resolve("Eu ataco o guarda", self._state(), seed="abc")
        b = resolution.resolve("Eu ataco o guarda", self._state(), seed="abc")
        self.assertEqual(a.as_dict(), b.as_dict())

    def test_different_seed_changes_roll(self):
        rolls = set()
        for n in range(20):
            receipt = resolution.resolve("Eu ataco o guarda", self._state(), seed=f"s{n}")
            rolls.add(receipt.roll)
        self.assertGreater(len(rolls), 1)

    def test_outcome_and_consequence_are_consistent(self):
        receipt = resolution.resolve("Eu ataco o guarda", self._state(), seed="abc")
        self.assertIn(
            receipt.outcome,
            ("sucesso_critico", "sucesso", "parcial", "falha", "falha_critica"),
        )
        self.assertTrue(receipt.consequences)
        self.assertIn("NAO reescreva", receipt.as_block())

    def test_apply_is_bounded(self):
        state = self._state()
        for n in range(12):
            receipt = resolution.resolve("Eu ataco o guarda", state, seed=f"s{n}")
            if receipt:
                state = resolution.apply(state, receipt)
        self.assertLessEqual(len(state["resolution_history"]), 8)
        self.assertLessEqual(len(state["events"]), 8)
        self.assertEqual(state["roll_seq"], 12)


class AttributeTest(unittest.TestCase):
    def _state(self, **attrs):
        state = {
            "turn": 3,
            "tension": 0,
            "injuries": [],
            "nr": 20,
            "inventory": [],
            "seed": "ficha",
        }
        if attrs:
            state["attributes"] = attrs
        return state

    def test_default_values_do_not_change_roll(self):
        plain = resolution.resolve("Eu ataco o guarda", self._state(), seed="x")
        default = resolution.resolve(
            "Eu ataco o guarda",
            self._state(corpo=10, mente=10, presenca=10, sorte=10),
            seed="x",
        )
        self.assertEqual(plain.roll, default.roll)
        self.assertEqual(plain.modifier, default.modifier)
        self.assertEqual(default.attribute, "corpo")
        self.assertEqual(default.attribute_mod, 0)

    def test_strong_attribute_helps_and_weak_hurts(self):
        strong = resolution.resolve(
            "Eu ataco o guarda",
            self._state(corpo=18, mente=10, presenca=10, sorte=10),
            seed="x",
        )
        weak = resolution.resolve(
            "Eu ataco o guarda",
            self._state(corpo=4, mente=10, presenca=10, sorte=10),
            seed="x",
        )
        self.assertGreater(strong.modifier, weak.modifier)
        self.assertEqual(strong.attribute, "corpo")
        self.assertEqual(strong.attribute_value, 18)
        self.assertEqual(strong.attribute_mod, 4)
        self.assertEqual(weak.attribute_mod, -3)

    def test_intent_uses_correct_attribute(self):
        state = self._state(corpo=8, mente=18, presenca=6, sorte=10)
        social = resolution.resolve(
            "Tento convencer ela a me ajudar", state, seed="x"
        )
        stealth = resolution.resolve(
            "Vou me esconder atrás das caixas", state, seed="x"
        )
        self.assertEqual(social.attribute, "presenca")
        self.assertEqual(social.attribute_value, 6)
        self.assertEqual(stealth.attribute, "mente")
        self.assertEqual(stealth.attribute_value, 18)

    def test_luck_applies_to_every_intent(self):
        lucky = self._state(corpo=10, mente=10, presenca=10, sorte=18)
        unlucky = self._state(corpo=10, mente=10, presenca=10, sorte=4)
        a = resolution.resolve("Eu procuro uma armadilha escondida", lucky, seed="x")
        b = resolution.resolve("Eu procuro uma armadilha escondida", unlucky, seed="x")
        self.assertEqual(a.luck_mod, 2)
        self.assertEqual(b.luck_mod, -2)
        self.assertGreater(a.modifier, b.modifier)
        self.assertLessEqual(a.modifier, resolution.MOD_MAX)

    def test_modifier_breakdown_is_capped(self):
        state = self._state(corpo=18, mente=18, presenca=18, sorte=18)
        state["inventory"] = ["espada"]
        receipt = resolution.resolve("Eu ataco o guarda", state, seed="x")
        self.assertEqual(receipt.modifier, resolution.MOD_MAX)
        self.assertIn("Corpo", receipt.as_block())

    def test_normalize_attributes_clamps_and_fills(self):
        attrs = resolution.normalize_attributes(
            {"corpo": 99, "mente": "7", "presenca": -2}
        )
        self.assertEqual(attrs["corpo"], 18)
        self.assertEqual(attrs["mente"], 7)
        self.assertEqual(attrs["presenca"], 3)
        self.assertEqual(attrs["sorte"], 10)
        self.assertEqual(resolution.normalize_attributes({}), {})

    def test_normalize_attributes_accepts_aliases_and_case(self):
        attrs = resolution.normalize_attributes(
            {"Corpo": "16", "presença": 7, "Luck": 99, "MIND": "x"}
        )
        self.assertEqual(
            attrs, {"corpo": 16, "mente": 10, "presenca": 7, "sorte": 18}
        )

    def test_modifier_table(self):
        table = resolution.modifier_table(
            {"corpo": 18, "mente": 10, "presenca": 6, "sorte": 14}
        )
        self.assertEqual(table["combate"]["modifier"], 5)
        self.assertEqual(table["social"]["modifier"], -1)
        self.assertEqual(table["investigacao"]["modifier"], 1)

    def test_skill_bonus_applies_to_matching_intent(self):
        state = self._state(corpo=10, mente=10, presenca=10, sorte=10)
        state["skills"] = {"luta": 3, "furtividade": 2}
        fight = resolution.resolve("Eu ataco o guarda", state, seed="x")
        stealth = resolution.resolve(
            "Vou me esconder atrás das caixas", state, seed="x"
        )
        self.assertEqual(fight.skill, "luta")
        self.assertEqual(fight.skill_level, 3)
        self.assertEqual(fight.skill_mod, 3)
        self.assertEqual(fight.modifier, 3)
        self.assertEqual(stealth.skill, "furtividade")
        self.assertEqual(stealth.modifier, 2)

    def test_untrained_skill_changes_nothing(self):
        state = self._state(corpo=10, mente=10, presenca=10, sorte=10)
        state["skills"] = {"luta": 0, "atletismo": 0}
        receipt = resolution.resolve("Eu ataco o guarda", state, seed="x")
        self.assertEqual(receipt.skill_level, 0)
        self.assertEqual(receipt.modifier, 0)

    def test_skill_and_attribute_are_capped(self):
        state = self._state(corpo=18, mente=10, presenca=10, sorte=18)
        state["skills"] = {"luta": 5}
        receipt = resolution.resolve("Eu ataco o guarda", state, seed="x")
        self.assertEqual(receipt.attribute_mod, 4)
        self.assertEqual(receipt.skill_mod, 5)
        self.assertEqual(receipt.luck_mod, 2)
        self.assertEqual(receipt.modifier, resolution.MOD_MAX)
        self.assertIn("Luta", receipt.as_block())

    def test_normalize_skills_clamps_and_accepts_aliases(self):
        skills = resolution.normalize_skills(
            {"Luta": 9, "stealth": 2, "Persuasão": -1, "investigation": 3}
        )
        self.assertEqual(skills["luta"], 5)
        self.assertEqual(skills["furtividade"], 2)
        self.assertEqual(skills["persuasao"], 0)
        self.assertEqual(skills["investigacao"], 3)
        self.assertEqual(resolution.normalize_skills({}), {})

    def test_balance_attribute_budget(self):
        attrs = resolution.balance_attributes(
            {"corpo": 18, "mente": 18, "presenca": 18, "sorte": 18}
        )
        self.assertEqual(sum(attrs.values()), resolution.ATTRIBUTE_POINT_POOL)
        self.assertTrue(
            all(
                resolution.ATTRIBUTE_MIN <= value <= resolution.ATTRIBUTE_MAX
                for value in attrs.values()
            )
        )
        default = resolution.balance_attributes(
            {"corpo": 10, "mente": 10, "presenca": 10, "sorte": 10}
        )
        self.assertEqual(default, {"corpo": 10, "mente": 10, "presenca": 10, "sorte": 10})

    def test_balance_skill_budget(self):
        skills = resolution.balance_skills(
            {"luta": 5, "atletismo": 5, "vigor": 5, "furtividade": 5}
        )
        self.assertEqual(sum(skills.values()), resolution.SKILL_POINT_POOL)
        self.assertTrue(all(value <= resolution.SKILL_MAX for value in skills.values()))

    def test_modifier_table_with_skills(self):
        table = resolution.modifier_table(
            {"corpo": 14, "mente": 10, "presenca": 10, "sorte": 14},
            {"luta": 2, "persuasao": 1},
        )
        self.assertEqual(table["combate"]["skill"], "luta")
        self.assertEqual(table["combate"]["skill_level"], 2)
        self.assertEqual(table["combate"]["modifier"], 5)
        self.assertEqual(table["social"]["skill"], "persuasao")
        self.assertEqual(table["social"]["modifier"], 2)

    def test_preview_exposes_skill(self):
        state = self._state(corpo=10, mente=10, presenca=10, sorte=10)
        state["skills"] = {"luta": 4}
        preview = resolution.preview("Eu ataco o guarda", state)
        self.assertEqual(preview["skill"], "luta")
        self.assertEqual(preview["skill_level"], 4)


class ContradictionTest(unittest.TestCase):
    def test_failure_narrated_as_success(self):
        receipt = {"outcome": "falha"}
        issue = resolution.check_contradiction(
            "Você consegue passar pelo guarda sem dificuldade.", receipt
        )
        self.assertTrue(issue)

    def test_failure_narrated_as_failure_is_ok(self):
        receipt = {"outcome": "falha"}
        self.assertEqual(
            resolution.check_contradiction(
                "Você não consegue passar; o guarda bloqueia o caminho.", receipt
            ),
            "",
        )

    def test_success_narrated_as_success_is_ok(self):
        receipt = {"outcome": "sucesso"}
        self.assertEqual(
            resolution.check_contradiction("Você consegue abrir a porta.", receipt),
            "",
        )


class SituationalTest(unittest.TestCase):
    def _state(self, inventory=None, injuries=None):
        return {
            "turn": 2,
            "tension": 0,
            "injuries": injuries or [],
            "nr": 20,
            "inventory": inventory or [],
            "seed": "camada",
        }

    def test_catalog_item_applies_to_its_intent(self):
        with_knife = resolution.resolve(
            "Eu ataco o guarda", self._state(["faca"]), seed="x"
        )
        without = resolution.resolve("Eu ataco o guarda", self._state([]), seed="x")
        self.assertEqual(with_knife.situational, 2)
        self.assertEqual(without.situational, 0)
        self.assertEqual(with_knife.modifier, without.modifier + 2)
        self.assertEqual(with_knife.situational_parts[0]["source"], "faca")

    def test_catalog_item_does_not_apply_to_other_intent(self):
        receipt = resolution.resolve(
            "Tento convencer ela", self._state(["faca"]), seed="x"
        )
        self.assertEqual(receipt.situational, 0)

    def test_unknown_item_does_not_change_roll(self):
        a = resolution.resolve(
            "Eu ataco o guarda", self._state(["pedra bonita"]), seed="x"
        )
        b = resolution.resolve("Eu ataco o guarda", self._state([]), seed="x")
        self.assertEqual(a.modifier, b.modifier)

    def test_structured_item_declares_effect(self):
        state = self._state(
            [{"name": "Faca de sal", "effects": {"combate": 1, "furtividade": 2}}]
        )
        fight = resolution.resolve("Eu ataco o guarda", state, seed="x")
        stealth = resolution.resolve(
            "Vou me esconder atrás das caixas", state, seed="x"
        )
        self.assertEqual(fight.situational, 1)
        self.assertEqual(stealth.situational, 2)

    def test_only_best_item_counts(self):
        state = self._state(
            ["faca", {"name": "espada longa", "effects": {"combate": 3}}]
        )
        receipt = resolution.resolve("Eu ataco o guarda", state, seed="x")
        self.assertEqual(receipt.situational, 3)
        self.assertEqual(receipt.situational_parts[0]["source"], "espada longa")

    def test_structured_injury_adds_penalty_and_dc(self):
        plain = resolution.resolve("Eu ataco o guarda", self._state([]), seed="x")
        hurt = resolution.resolve(
            "Eu ataco o guarda",
            self._state(injuries=[{"name": "costela quebrada", "penalty": 2, "dc": 2}]),
            seed="x",
        )
        self.assertEqual(hurt.situational, -2)
        self.assertEqual(hurt.modifier, plain.modifier - 2)
        self.assertEqual(hurt.dc, plain.dc + 2)
        self.assertEqual(hurt.situational_parts[-1]["kind"], "injury")

    def test_parts_add_up_and_cap_is_recorded(self):
        state = self._state(
            ["faca"],
            [
                {"name": "corte", "penalty": 3, "dc": 3},
                {"name": "queimadura", "penalty": 3, "dc": 0},
            ],
        )
        receipt = resolution.resolve("Eu ataco o guarda", state, seed="x")
        self.assertEqual(receipt.situational, resolution.SITUATIONAL_MIN)
        self.assertEqual(
            sum(part["value"] for part in receipt.situational_parts),
            receipt.situational,
        )
        self.assertTrue(
            any(part["kind"] == "cap" for part in receipt.situational_parts)
        )
        self.assertIn("Camada situacional", receipt.as_block())

    def test_modifier_table_includes_state_layer(self):
        table = resolution.modifier_table(
            {"corpo": 10, "mente": 10, "presenca": 10, "sorte": 10},
            {"luta": 2},
            {"inventory": ["faca"], "injuries": ["corte"]},
        )
        self.assertEqual(table["combate"]["modifier"], 2)
        self.assertEqual(table["combate"]["item_bonus"], 2)
        self.assertEqual(table["combate"]["injury_penalty"], -1)
        self.assertEqual(table["combate"]["situational"], 1)
        self.assertEqual(table["combate"]["total"], 3)
        self.assertEqual(table["social"]["item_bonus"], 0)
        self.assertEqual(table["social"]["total"], -1)

    def test_inventory_detail_and_preview_difficulty(self):
        detail = resolution.inventory_detail(["faca", "pedra"])
        self.assertEqual(detail[0]["effect_label"], "combate +2")
        self.assertEqual(detail[1]["effect_label"], "")
        preview = resolution.preview("Eu ataco o guarda", self._state([]))
        self.assertTrue(preview["difficulty"])
        self.assertIn("situational_parts", preview)


if __name__ == "__main__":
    unittest.main()
