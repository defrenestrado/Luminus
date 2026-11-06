"""Confiabilidade do chat: idempotencia e continuacao de narracao."""

import unittest

import main


class RequestKeyTest(unittest.TestCase):
    def test_same_action_same_key(self):
        self.assertEqual(
            main._chat_request_key(3, "Eu abro a porta"),
            main._chat_request_key(3, "Eu  abro a porta "),
        )

    def test_different_session_different_key(self):
        self.assertNotEqual(
            main._chat_request_key(3, "Eu abro a porta"),
            main._chat_request_key(4, "Eu abro a porta"),
        )


class ContinuationTest(unittest.TestCase):
    def test_detects_cut_sentence(self):
        self.assertTrue(main._looks_truncated("Ele para na porta e"))
        self.assertFalse(main._looks_truncated("Ele para na porta."))
        self.assertFalse(main._looks_truncated('Ele diz: "Vamos."'))

    def test_merge_removes_overlap(self):
        base = "Ele para na porta e"
        extra = "porta e olha para tras."
        self.assertEqual(
            main._merge_continuation(base, extra),
            " olha para tras.",
        )

    def test_merge_keeps_new_sentence(self):
        self.assertEqual(
            main._merge_continuation("Ele para.", "A chuva cai."),
            " A chuva cai.",
        )


if __name__ == "__main__":
    unittest.main()
