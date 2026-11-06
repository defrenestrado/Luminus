"""Testes dos tres pacotes: compromissos, PHASE-Tree e cartoes de memoria."""
import json
import os
import tempfile
import time
import unittest

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["LUMINUS_DB"] = _tmp.name

import aux  # noqa: E402
import commitments  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402
import prompts  # noqa: E402
import worldcheck  # noqa: E402


class PackagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init()
        cls.conn = db.connect()
        now = time.time()
        cls.conn.execute(
            "INSERT INTO worlds (name, description, created_at) VALUES (?,?,?)",
            ("Mundo Teste", "Um mundo de teste.", now),
        )
        wid = cls.conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        cls.npc_id = cls._char(
            "Milim", wid,
            tagline="Sobrevivente desconfiada",
            personality="Teimosa, leal, esconde o medo com ironia.",
            speech_style="Curta, seca, sarcastica.",
            fears="Perder a liberdade.",
            dislikes="Ser chamada de coelha.",
            secrets="Ela tem uma divida com o Coro.",
        )
        cls.other_id = cls._char(
            "Tomo", wid,
            tagline="Informante do Coro",
            personality="Simpatico por fora, calculista por dentro.",
            speech_style="Sussurra e sorri.",
            fears="Ser descoberto.",
            dislikes="Violencia gratuita.",
            secrets="Vendeu o endereco do grupo.",
        )
        cls.prot_id = cls._char(
            "Ren", wid, kind="protagonist",
            tagline="Forasteiro", personality="Curioso e teimoso.",
            speech_style="Direto.",
        )
        cls.conn.execute(
            "INSERT INTO sessions (character_id, protagonist_id, title, summary,"
            " summarized_until, turn_count, opening_generated, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (cls.npc_id, cls.prot_id, "Teste", "", 0, 0, 0, now, now),
        )
        cls.sid = cls.conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        try:
            os.unlink(_tmp.name)
        except OSError:
            pass

    @classmethod
    def _char(cls, name, wid, kind="npc", **fields):
        cols = ["kind", "name", "world_id", "created_at"]
        vals = [kind, name, wid, time.time()]
        for key, value in fields.items():
            cols.append(key)
            vals.append(value)
        cls.conn.execute(
            "INSERT INTO characters (" + ",".join(cols) + ") VALUES ("
            + ",".join("?" for _ in vals) + ")",
            vals,
        )
        return cls.conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def _state(self):
        sess = self.conn.execute(
            "SELECT * FROM sessions WHERE id=?", (self.sid,)
        ).fetchone()
        world = db.rowdict(self.conn.execute(
            "SELECT * FROM worlds WHERE id=1"
        ).fetchone())
        npc = self.conn.execute(
            "SELECT * FROM characters WHERE id=?", (self.npc_id,)
        ).fetchone()
        state = aux.load_state(sess, world, db.rowdict(npc))
        cast = [db.rowdict(self.conn.execute(
            "SELECT * FROM characters WHERE id=?", (self.other_id,)
        ).fetchone())]
        aux.ensure_cast_state(state, cast)
        return state, cast

    def test_phase_tree_layers_and_events(self):
        state, cast = self._state()
        self.assertTrue(state["identity"]["anchors"])
        self.assertEqual(state["identity"]["character_id"], self.npc_id)
        self.assertTrue(state["session"]["nr"] >= 0)
        self.assertTrue(state["cast"][str(self.other_id)]["identity"]["anchors"])
        plan = {
            "foco": "Milim",
            "nr_delta": 5,
            "nr_por_npc": {"Tomo": -2},
            "npc_intencao": "pedir ajuda sem admitir que precisa",
            "npc_emocao": "aliviada, mas desconfiada",
            "vinculo": "Ela comeca a admitir que precisa de ajuda.",
            "objetivo": "Consertar o braco",
            "consequencia": "Ela aceita a ajuda, sem agradecer.",
            "estado": {"npcs": {"Milim": "presente", "Tomo": "observando"}},
            "fatos": [],
            "threads": [],
        }
        before = dict(state["identity"])
        new = aux.update_state(
            state, "Cuido do braco dela.", "", False, plan_data=plan,
            main_npc={"id": self.npc_id, "name": "Milim"}, cast=cast,
        )
        self.assertEqual(new["identity"]["anchors"], before["anchors"])
        self.assertEqual(new["moment"]["mood"], "aliviada, mas desconfiada")
        self.assertIn("ajuda", new["moment"]["intent"])
        self.assertIn("ajuda", new["persona"]["arc"])
        self.assertTrue(new["persona"]["history"])
        self.assertTrue(new["events"])
        self.assertEqual(new["session"]["nr"], 25)
        self.assertEqual(new["cast"][str(self.other_id)]["session"]["nr"], 18)
        self.assertEqual(new["cast"][str(self.other_id)]["moment"]["mood"], "")

    def test_commitments_validation_audit_and_stats(self):
        state, _ = self._state()
        now = time.time()
        self.conn.execute(
            "INSERT INTO threads (session_id, kind, title, status, setup, payoff,"
            " due_turn, entities, last_turn, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (self.sid, "promessa", "Prometer o cachecol", "aberta", "", "",
             1, '["Milim"]', 0, now, now),
        )
        self.conn.commit()
        thread_id = self.conn.execute(
            "SELECT last_insert_rowid() AS id"
        ).fetchone()["id"]

        plan = {"threads": [], "fatos": [], "fatos_entidade": [],
                "estado": {}, "consequencia": ""}
        plan, warnings = commitments.validate_plan(self.conn, self.sid, plan, state)
        self.assertEqual(plan["threads"], [])
        commitments.audit_plan(self.conn, self.sid, plan, state, warnings)
        stats = commitments.stats(self.conn, self.sid, 3)
        self.assertEqual(stats["failures"], 1)
        self.assertEqual(stats["overdue"], 1)

        plan2 = {
            "threads": [{
                "id": thread_id, "acao": "avancar", "titulo": "Prometer o cachecol",
                "tipo": "promessa", "entidades": ["Milim"], "prazo_turno": 3,
                "payoff": "",
            }],
            "fatos": [], "fatos_entidade": [], "estado": {},
            "consequencia": "Ela entrega o cachecol.",
        }
        plan2, warnings2 = commitments.validate_plan(
            self.conn, self.sid, plan2, state
        )
        self.assertEqual(plan2["threads"][0]["id"], thread_id)
        commitments.audit_plan(self.conn, self.sid, plan2, state, warnings2)
        commitments.audit_narration(
            self.conn, self.sid, 3, "Ela entrega o cachecol sem dizer nada.", plan2
        )
        stats2 = commitments.stats(self.conn, self.sid, 3)
        self.assertGreater(stats2["survival"], 0)
        block = commitments.block(self.conn, self.sid, turn=3)
        self.assertIn("COMPROMISSOS ABERTOS", block)
        self.assertIn("Prometer o cachecol", block)

    def test_infer_commitment_from_user_promise(self):
        plan = {"threads": [], "fatos": [], "fatos_entidade": [], "estado": {}}
        plan = commitments.infer_from_user(
            plan, "Prometo que volto com o remedio antes do amanhecer.",
            "Bete Ferrugem", ["Bete Ferrugem"],
        )
        self.assertEqual(len(plan["threads"]), 1)
        self.assertEqual(plan["threads"][0]["acao"], "abrir")
        self.assertIn("Promessa", plan["threads"][0]["titulo"])
        self.assertEqual(plan["threads"][0]["entidades"], ["Bete Ferrugem"])

    def test_apply_plan_threads_and_facts(self):
        state, _ = self._state()
        plan = {
            "objetivo": "Abrir a caixa",
            "threads": [{
                "id": 0, "acao": "abrir", "tipo": "misterio",
                "titulo": "O que ha na caixa", "entidades": ["Milim"],
                "prazo_turno": 2, "payoff": "",
            }],
            "fatos_entidade": [{
                "entidade": "Milim", "fato": "A caixa tem um shard.",
                "sabem": ["Milim"],
            }],
        }
        plan, warnings = commitments.validate_plan(self.conn, self.sid, plan, state)
        main._apply_plan_threads(self.conn, self.sid, plan, 2)
        row = self.conn.execute(
            "SELECT * FROM threads WHERE session_id=? AND title=?",
            (self.sid, "O que ha na caixa"),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["last_turn"], 2)
        self.assertEqual(row["status"], "aberta")
        tid = row["id"]
        plan2 = {
            "objetivo": "Abrir a caixa",
            "threads": [{
                "id": tid, "acao": "resolver", "tipo": "misterio",
                "titulo": "O que ha na caixa", "entidades": ["Milim"],
                "prazo_turno": 2, "payoff": "O shard aparece.",
            }],
            "fatos_entidade": [{
                "entidade": "Milim", "fato": "A caixa tem um shard.",
                "sabem": ["Tomo"],
            }],
        }
        plan2, _ = commitments.validate_plan(self.conn, self.sid, plan2, state)
        main._apply_plan_threads(self.conn, self.sid, plan2, 3)
        row = self.conn.execute("SELECT * FROM threads WHERE id=?", (tid,)).fetchone()
        self.assertEqual(row["status"], "resolvida")
        self.assertEqual(row["last_turn"], 3)
        fact = self.conn.execute(
            "SELECT known_by FROM entity_facts WHERE session_id=? AND fact=?",
            (self.sid, "A caixa tem um shard."),
        ).fetchone()
        self.assertIn("Tomo", fact["known_by"])

    def test_scene_manager_turning_point_and_pacing(self):
        state, cast = self._state()
        npc = db.rowdict(self.conn.execute(
            "SELECT * FROM characters WHERE id=?", (self.npc_id,)
        ).fetchone())
        plan = {
            "foco": "Milim",
            "ritmo": "revelacao",
            "cena": {
                "acao": "trocar", "titulo": "Clinica Aurora",
                "local": "Midgrid", "presentes": ["Milim", "Tomo"],
                "transicao": "O elevador abre para o corredor esteril.",
                "motivo": "Milim precisa tratar o braco.",
            },
            "marco": {
                "tipo": "reparo",
                "descricao": "Ela aceita ajuda pela primeira vez sem cobrar.",
                "impacto": "A confianca sobe um degrau.",
            },
            "nr_delta": 4,
            "nr_por_npc": {"Tomo": 1},
            "npc_intencao": "testar se o jogador fica",
            "npc_emocao": "vulneravel",
            "vinculo": "Ela aceita ajuda sem cobrar.",
            "objetivo": "Tratar o braco",
            "consequencia": "Ela senta e deixa o jogador ver o ferimento.",
            "estado": {"npcs": {"Milim": "presente", "Tomo": "observando"}},
            "fatos": [], "threads": [],
        }
        new = aux.update_state(
            state, "Levo ela para a clinica.", "", False, plan_data=plan,
            main_npc=npc, cast=cast,
        )
        self.assertEqual(new["scene"]["id"], 1)
        self.assertEqual(new["scene"]["title"], "Clinica Aurora")
        self.assertEqual(new["scene"]["location"], "Midgrid")
        self.assertIn("Milim", new["scene"]["present"])
        self.assertIn("Tomo", new["scene"]["present"])
        self.assertEqual(new["pacing"]["beat"], "revelacao")
        self.assertTrue(new["relationship_events"])
        event = next(
            e for e in new["relationship_events"] if e["npc"].startswith("Milim")
        )
        self.assertEqual(event["type"], "reparo")
        self.assertIn("ajuda", event["description"])
        self.assertEqual(event["nr_after"], event["nr_before"] + 4)
        # sair: remove Tomo
        plan2 = {
            "foco": "Milim", "ritmo": "respiro",
            "cena": {"acao": "sair", "titulo": "", "local": "",
                     "presentes": ["Tomo"], "transicao": "", "motivo": ""},
            "marco": {}, "nr_delta": 0, "nr_por_npc": {},
            "npc_intencao": "", "npc_emocao": "", "vinculo": "",
            "objetivo": "", "consequencia": "", "estado": {}, "fatos": [], "threads": [],
        }
        newer = aux.update_state(
            new, "Tomo sai.", "", False, plan_data=plan2,
            main_npc=npc, cast=cast,
        )
        self.assertNotIn("Tomo", newer["scene"]["present"])
        self.assertIn("Milim", newer["scene"]["present"])
        self.assertEqual(newer["cast"][str(self.other_id)]["status"], "fora da cena")

    def test_worldcheck_and_plan_parse(self):
        state, cast = self._state()
        npc = db.rowdict(self.conn.execute(
            "SELECT * FROM characters WHERE id=?", (self.npc_id,)
        ).fetchone())
        raw = json.dumps({
            "foco": "Milim", "ritmo": "tensao",
            "cena": {"acao": "trocar", "titulo": "Telhado", "local": "Alto",
                     "presentes": ["Milim"], "transicao": "Sobe a escada.",
                     "motivo": "Fugir."},
            "marco": {"tipo": "decisao", "descricao": "Escolhe fugir.",
                      "impacto": "Muda a rota."},
            "nr_delta": 0, "nr_por_npc": {}, "vinculo": "",
            "objetivo": "Fugir", "npc_intencao": "", "npc_emocao": "",
            "consequencia": "", "detalhes": [], "evitar": [], "fatos": [],
            "estado": {"local": "Alto", "hora": "", "inventario": [],
                       "objetos": {}, "npcs": {}, "ferimentos": [], "flags": {}},
            "threads": [], "fatos_entidade": [],
        })
        plan = aux.parse_plan_json(raw)
        self.assertEqual(plan["ritmo"], "tensao")
        self.assertEqual(plan["cena"]["acao"], "trocar")
        self.assertEqual(plan["marco"]["tipo"], "decisao")
        plan2, warnings = worldcheck.validate(plan, state, npc, cast)
        self.assertIn("presentes", plan2["cena"])
        # muda local sem transicao/motivo
        state["scene"].update({"id": 1, "title": "Cena atual", "location": "Casa"})
        plan3 = {
            "foco": "Milim", "ritmo": "",
            "cena": {"acao": "continuar", "titulo": "", "local": "Outro",
                     "presentes": ["Milim"], "transicao": "", "motivo": ""},
            "marco": {}, "nr_delta": 0,
            "nr_por_npc": {"Fantasma": 2},
            "fatos_entidade": [{"entidade": "Milim", "fato": "Ela nao confia no jogador."}],
            "estado": {}, "fatos": [],
        }
        _plan4, warnings2 = worldcheck.validate(
            plan3, state, npc, cast,
            existing_facts=[{"entity": "Milim", "fact": "Ela confia no jogador."}],
        )
        self.assertTrue(any("transicao" in w for w in warnings2))
        self.assertTrue(any("desconhecido" in w for w in warnings2))
        self.assertTrue(any("contradiz" in w for w in warnings2))

    def test_memory_card_and_layers_block(self):
        state, cast = self._state()
        layers = aux.cast_layers(cast[0])
        card = prompts.memory_card(
            "Tomo", layers=layers, memories=["Vendeu o endereco."],
            boundaries=["Nao sabe o que nao presenciou"], now="NR 20/100",
            commitments=["#1 entregar a chave"],
        )
        self.assertIn("ANCORAS", card)
        self.assertIn("Vendeu o endereco", card)
        self.assertIn("#1 entregar a chave", card)
        block = prompts.memory_cards_block([card])
        self.assertIn("CARTÕES DE MEMORIA", block)
        main_layers = aux.main_layers(state)
        self.assertIn("IDENTIDADE", prompts.state_layers_block(state, {"name": "Milim"}))
        self.assertTrue(main_layers["identity"]["anchors"])
        npc = db.rowdict(self.conn.execute(
            "SELECT * FROM characters WHERE id=?", (self.npc_id,)
        ).fetchone())
        prot = db.rowdict(self.conn.execute(
            "SELECT * FROM characters WHERE id=?", (self.prot_id,)
        ).fetchone())
        cards = main._memory_cards(self.conn, self.sid, npc, prot, cast, state)
        self.assertIn("CARTÕES DE MEMORIA", cards)
        self.assertIn("Milim", cards)
        self.assertIn("Tomo", cards)


if __name__ == "__main__":
    unittest.main(verbosity=2)
