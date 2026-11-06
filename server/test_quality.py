"""Pos-processamento: formato mobile, logica de NPC e transicao de cena."""

import unittest

import quality


class FormatTest(unittest.TestCase):
    def test_breaks_wall_of_text(self):
        text = " ".join(
            f"Frase numero {i} com detalhe sensorial e acao concreta." for i in range(20)
        )
        out = quality.format_narration(text)
        self.assertGreater(out.count("\n\n"), 2)
        for block in out.split("\n\n"):
            self.assertLessEqual(len(block), quality.MAX_PARAGRAPH_CHARS)

    def test_puts_dialogue_on_own_block(self):
        text = (
            'Ela respira fundo. — "Nao toque nisso." — A voz falha no fim.'
        )
        out = quality.format_narration(text)
        self.assertIn("\n\n— \"Nao toque nisso.\"", out)

    def test_does_not_split_malformed_closing_quote(self):
        text = 'Ela diz: — "Nao posso — " A voz falha.'
        out = quality.format_narration(text)
        self.assertIn("Nao posso", out)
        self.assertEqual(out, text)

    def test_removes_assistant_voice(self):
        text = "A porta range.\n\nEspero que você tenha gostado!"
        self.assertNotIn("Espero que", quality.format_narration(text))

    def test_fixes_known_typos(self):
        out = quality.format_narration("Ela zunha baixo e a chuva batede no metal.")
        self.assertIn("zune", out)
        self.assertIn("batendo", out)

    def test_idempotent(self):
        text = "A porta range. — \"Entre.\" — Ele espera."
        once = quality.format_narration(text)
        self.assertEqual(once, quality.format_narration(once))


class LogicTest(unittest.TestCase):
    NPC = {"name": "Elena"}

    def test_detects_pay_to_hear_own_music(self):
        text = (
            'Ela toca violino no palco. — "Se quiser ouvir a música, '
            'vai ter que pagar cinquenta créditos."'
        )
        issues = quality.detect_logic_issues(text, self.NPC)
        self.assertIn("cobra_pelo_que_oferece", issues)
        fixed = quality.drop_logic_sentences(text, issues)
        self.assertNotIn("pagar cinquenta", fixed)
        self.assertIn("toca violino", fixed)

    def test_ignores_legitimate_bar_tab(self):
        text = (
            "Ela toca violino. O barman aceita créditos ou sangue, "
            "mas prefiro não ouvir a história de quem sai devendo."
        )
        self.assertEqual(quality.detect_logic_issues(text, self.NPC), [])

    def test_ignores_metaphor(self):
        text = "Você pode consumir sem pagar o preço de existir."
        self.assertEqual(quality.detect_logic_issues(text, self.NPC), [])

    def test_detects_free_and_paid_contradiction(self):
        text = '— "A entrada é grátis, mas me pague a taxa."'
        self.assertIn(
            "cobranca_contraditoria",
            quality.detect_logic_issues(text, self.NPC),
        )


class LocationTest(unittest.TestCase):
    def test_accepts_named_destination(self):
        self.assertTrue(
            quality.location_transition_ok(
                "Você atravessa o corredor e chega à sala de testes.",
                "porão da clínica",
                "Sala de testes - Andar 42",
            )
        )

    def test_rejects_silent_teleport(self):
        self.assertFalse(
            quality.location_transition_ok(
                "O cheiro de ozônio aumenta e ela treme.",
                "porão da clínica",
                "Sala de testes - Andar 42",
            )
        )


class DuplicateTest(unittest.TestCase):
    def test_detects_identical_reply(self):
        a = "Elena solta um ar curto pelo nariz. — \"Apenas\" — ela repete."
        self.assertTrue(quality.is_duplicate(a, a))

    def test_rejects_different_reply(self):
        a = "Elena solta um ar curto pelo nariz."
        b = "O barman limpa o copo e desvia o olhar."
        self.assertFalse(quality.is_duplicate(a, b))


if __name__ == "__main__":
    unittest.main()
