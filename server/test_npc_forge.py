"""Testes da criação profunda de NPCs (Pensador -> Juiz -> Síntese)."""

import asyncio
import json
import os
import time
import unittest
from unittest import mock

import npc_forge


DOSSIER = {
    "core": "Deseja provar que não precisa de ninguém; precisa ser visto.",
    "psychology": "Orgulho ferido, humor seco, medo de ser descartada.",
    "contradiction": "Aproxima com ironia e afasta quando alguém responde.",
    "secret_economy": "Esconde a dívida; revelaria sob pressão de vínculo alto.",
    "voice": "Frases curtas, evita 'eu sinto', usa metáforas de oficina.",
    "agency": "Quer recuperar a oficina do pai e pagar a dívida sem ajuda.",
    "friction": "Testa o jogador com pequenas provocações para medir confiança.",
    "narrative_hooks": ["Uma peça roubada", "Um credor paciente", "Uma carta sem remetente"],
    "appearance_cues": "Mãos marcadas de graxa, casaco remendado com linha dourada.",
    "anti_cliche": ["Não é fria por trauma genérico: é engraçada e cansada.", "Não quer ser salva."],
    "open_questions": ["Quem enviou a carta?", "Ela realmente quer a oficina de volta?"],
    "dossier": "Resumo interno do personagem.",
}

JUDGE = {
    "ok": True,
    "score": 8.5,
    "strongest": "A contradição entre ironia e desejo de ser vista.",
    "issues": [],
    "missing": [],
    "verdict": "Aprovado com ajustes leves de voz.",
}

CHARACTER = {
    "name": "Mira",
    "tagline": "Mecânica que conserta o mundo para não consertar a si mesma.",
    "description_public": "Uma mecânica de língua afiada escondida atrás de uma bancada.",
    "appearance": "Mãos de graxa, casaco remendado, olhar atento.",
    "personality": "Irônica, competente, orgulhosa; pede ajuda como quem arma uma armadilha.",
    "backstory": "Herdou a oficina e a dívida do pai; transformou vergonha em ofício.",
    "speech_style": "Curta, seca, metáforas de oficina; evita falar de sentimentos.",
    "likes": "Ferramentas boas, café forte, clientes que não perguntam demais.",
    "dislikes": "Piedade, promessas vagas, gente que toca suas coisas.",
    "fears": "Ser descartada quando não for útil.",
    "greeting": "*Ela limpa uma chave inglesa sem olhar para você.* \"Se veio consertar, fale. Se veio consolar, a porta é ali.\"",
    "example_dialogue": "\"Não preciso de sócio.\" / \"Você segura ou só observa?\" / \"Isso não é gentileza, é dívida.\"",
    "tags": ["mecânica", "orgulhosa", "oficina", "devedora"],
}


class JsonHelpersTest(unittest.TestCase):
    def test_extract_json_from_fence(self):
        raw = "```json\n" + json.dumps(CHARACTER) + "\n```"
        self.assertEqual(npc_forge._extract_json(raw)["name"], "Mira")

    def test_normalize_fills_missing_and_tags(self):
        draft = npc_forge._normalize_draft({"name": "Mira", "tags": "a, b"})
        self.assertEqual(draft["tags"], ["a", "b"])
        self.assertEqual(draft["appearance"], "")
        self.assertEqual(set(draft), set(npc_forge._REQUIRED))


class ForgeTest(unittest.IsolatedAsyncioTestCase):
    async def _forge_with(self, handler):
        calls = []

        async def fake_complete(
            messages, schema, temperature, max_tokens, stage, prefer=""
        ):
            calls.append(stage)
            payload = handler(stage, calls)
            if isinstance(payload, Exception):
                raise payload
            return json.dumps(payload), "fake-route"

        with mock.patch.object(npc_forge, "deep_available", return_value=True), \
             mock.patch.object(npc_forge, "_thinker_candidates", return_value=1), \
             mock.patch.object(npc_forge, "_complete", fake_complete):
            draft, info = await npc_forge.forge(
                seed="uma mecânica endividada",
                world_name="Porto Cinza",
                world_desc="Cidade portuária industrial.",
            )
        return draft, info, calls

    async def test_full_pipeline(self):
        def handler(stage, calls):
            return {
                "pensador": DOSSIER,
                "juiz": JUDGE,
                "sintese": CHARACTER,
            }[stage]

        draft, info, calls = await self._forge_with(handler)
        self.assertEqual(draft["name"], "Mira")
        self.assertEqual(calls, ["pensador", "juiz", "sintese"])
        self.assertEqual(info["judge"]["score"], 8.5)
        self.assertEqual(info["thinker_route"], "fake-route")

    async def test_judge_failure_does_not_break(self):
        def handler(stage, calls):
            if stage == "juiz":
                return RuntimeError("juiz fora do ar")
            return {"pensador": DOSSIER, "sintese": CHARACTER}[stage]

        draft, info, calls = await self._forge_with(handler)
        self.assertEqual(draft["name"], "Mira")
        self.assertIsNone(info["judge"])
        self.assertEqual(calls, ["pensador", "juiz", "sintese"])

    async def test_synthesis_retries_missing_field(self):
        def handler(stage, calls):
            if stage == "pensador":
                return DOSSIER
            if stage == "juiz":
                return JUDGE
            if calls.count("sintese") == 1:
                broken = dict(CHARACTER)
                broken["greeting"] = ""
                return broken
            return CHARACTER

        draft, info, calls = await self._forge_with(handler)
        self.assertEqual(draft["greeting"], CHARACTER["greeting"])
        self.assertEqual(calls.count("sintese"), 2)

    async def test_slow_thinker_cancelled_after_grace(self):
        async def fake_complete(
            messages, schema, temperature, max_tokens, stage, prefer=""
        ):
            if stage == "pensador2":
                await asyncio.sleep(30)
                return json.dumps(DOSSIER), "lento"
            payload = {
                "pensador1": DOSSIER,
                "juiz": JUDGE,
                "sintese": CHARACTER,
            }[stage]
            return json.dumps(payload), "rapido"

        with mock.patch.object(npc_forge, "deep_available", return_value=True), \
             mock.patch.object(npc_forge, "_complete", fake_complete), \
             mock.patch.object(npc_forge, "_thinker_candidates", return_value=2), \
             mock.patch.dict(
                 os.environ, {"LUMINUS_NPC_THINKER_GRACE": "0.2"}
             ):
            started = time.perf_counter()
            draft, info = await npc_forge.forge(seed="teste", world_name="W")
            elapsed = time.perf_counter() - started

        self.assertEqual(draft["name"], "Mira")
        self.assertEqual(info["thinker_candidates"], 2)
        self.assertEqual(info["dossiers"], 1)
        self.assertLess(elapsed, 3.0)

    async def test_invalid_judge_asks_second_opinion(self):
        prefers = []

        async def fake_complete(
            messages, schema, temperature, max_tokens, stage, prefer=""
        ):
            if stage == "pensador":
                return json.dumps(DOSSIER), "freellmpool"
            if stage == "juiz":
                prefers.append(prefer)
                if len(prefers) == 1:
                    return json.dumps(
                        {"aprovado": False, "issues": []}
                    ), "freellmpool"
                return json.dumps(JUDGE), "utility"
            return json.dumps(CHARACTER), "main"

        with mock.patch.object(npc_forge, "deep_available", return_value=True), \
             mock.patch.object(npc_forge, "_thinker_candidates", return_value=1), \
             mock.patch.object(npc_forge, "_complete", fake_complete):
            draft, info = await npc_forge.forge(seed="teste", world_name="W")

        self.assertEqual(draft["name"], "Mira")
        self.assertEqual(prefers, ["pool", "utility"])
        self.assertEqual(info["judge_route"], "utility")
        self.assertEqual(info["judge"]["score"], 8.5)


class SchemaHelpersTest(unittest.TestCase):
    def test_with_schema_injects_after_system(self):
        messages = [
            {"role": "system", "content": "a"},
            {"role": "user", "content": "b"},
        ]
        out = npc_forge._with_schema(messages, {"type": "object"})
        self.assertEqual(len(out), 3)
        self.assertEqual(out[1]["role"], "system")
        self.assertIn('"type": "object"', out[1]["content"])
        self.assertEqual(out[2]["content"], "b")
        self.assertEqual(messages[0]["content"], "a")

    def test_valid_judge_rejects_localized_keys(self):
        self.assertTrue(npc_forge._valid_judge(JUDGE))
        self.assertFalse(
            npc_forge._valid_judge({"aprovado": False, "issues": []})
        )
        self.assertFalse(npc_forge._valid_judge({**JUDGE, "score": None}))


class CompleteRetryTest(unittest.IsolatedAsyncioTestCase):
    async def test_pool_retries_before_failover(self):
        route = npc_forge.Route(
            name="freellmpool",
            kind="pool",
            model="quality",
            base="http://127.0.0.1:8080/v1",
        )
        calls = []

        async def fake_call(
            route, messages, schema, temperature, max_tokens, timeout
        ):
            calls.append(timeout)
            if len(calls) == 1:
                raise npc_forge.NpcForgeError("HTTP 404 transitório")
            return '{"ok": true}'

        with mock.patch.object(
            npc_forge, "_ordered_routes", return_value=[route]
        ), mock.patch.object(npc_forge, "_call_route", fake_call), \
                mock.patch.dict(
                    os.environ,
                    {
                        "LUMINUS_NPC_POOL_ATTEMPTS": "2",
                        "LUMINUS_NPC_POOL_TIMEOUT": "10",
                    },
                ):
            text, name = await npc_forge._complete(
                [], {}, 0.2, 20, "pensador", prefer="pool"
            )

        self.assertEqual(text, '{"ok": true}')
        self.assertEqual(name, "freellmpool")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], 10.0)


class RoutesTest(unittest.TestCase):
    def test_pool_route_first_when_configured(self):
        env = {
            "LUMINUS_NPC_POOL_URL": "http://127.0.0.1:8080/v1",
            "LUMINUS_NPC_POOL_MODEL": "quality",
        }
        with mock.patch.dict("os.environ", env, clear=False), mock.patch.object(
            npc_forge.llm, "utility_available", return_value=False
        ):
            routes = npc_forge._routes()
        self.assertEqual(routes[0].name, "freellmpool")
        self.assertEqual(routes[0].model, "quality")
        self.assertIn(routes[0].kind, ("pool",))

    def test_deep_unavailable_without_pool_or_gpu(self):
        env = {
            "LUMINUS_NPC_DEEP": "1",
            "LUMINUS_NPC_ALLOW_CPU": "0",
            "LUMINUS_NPC_USE_DEEPSEEK": "0",
            "LUMINUS_NPC_POOL_URL": "",
            "LUMINUS_NPC_ALLOW_MAIN": "0",
        }
        with mock.patch.dict("os.environ", env, clear=False), mock.patch.object(
            npc_forge.llm, "utility_available", return_value=False
        ):
            self.assertFalse(npc_forge.deep_available())

    def test_deep_available_with_pool(self):
        env = {
            "LUMINUS_NPC_DEEP": "1",
            "LUMINUS_NPC_POOL_URL": "http://127.0.0.1:8080/v1",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            self.assertTrue(npc_forge.deep_available())

    def test_pool_preferred_then_main_for_thinker_and_judge(self):
        env = {
            "LUMINUS_NPC_POOL_URL": "http://127.0.0.1:8080/v1",
            "LUMINUS_NPC_POOL_MODEL": "quality",
            "LUMINUS_NPC_USE_DEEPSEEK": "0",
            "DEEPSEEK_API_KEY": "",
            "LUMINUS_NPC_ALLOW_MAIN": "1",
        }
        with mock.patch.dict("os.environ", env, clear=False), mock.patch.object(
            npc_forge.llm, "utility_available", return_value=True
        ), mock.patch.object(
            npc_forge.llm, "primary_ok", return_value=True
        ), mock.patch.object(
            npc_forge, "_main_model", return_value="luminus-27b"
        ):
            names = [route.name for route in npc_forge._ordered_routes("pool")]
        self.assertEqual(names[0], "freellmpool")
        self.assertLess(names.index("main"), names.index("utility"))

    def test_status_exposes_pool_tuning(self):
        env = {
            "LUMINUS_NPC_POOL_ATTEMPTS": "3",
            "LUMINUS_NPC_POOL_TIMEOUT": "42",
            "LUMINUS_NPC_THINKER_GRACE": "5",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            status = npc_forge.status()
        self.assertEqual(status["pool_attempts"], 3)
        self.assertEqual(status["pool_timeout"], 42.0)
        self.assertEqual(status["thinker_grace"], 5.0)


if __name__ == "__main__":
    unittest.main()
