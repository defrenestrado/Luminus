import json
import os
import re
import time

import aux
import db
import llm
import prompts

MODEL = os.environ.get(
    "LUMINUS_CONSISTENCY_MODEL",
    os.environ.get("LUMINUS_PLANNER_MODEL", llm.UTILITY_MODEL),
)

JUDGE_SYSTEM = """\
Voce e um auditor de continuidade de uma historia interativa em PT-BR.
Recebera o mundo, o NPC, o estado atual, threads abertas, fatos por entidade e as
ultimas mensagens. Procure contradicoes factuais, de personagem, de mundo, de
causalidade e de memoria. Nao invente problemas. Cite evidencia curta do texto.

REGRAS
- goal, mood, pending, vinculo, intencao e consequencia sao direcao/estimativa do Narrador,
  nao fatos. NUNCA marque contradicao baseada nesses campos, mesmo que parecam desatualizados.
- A fala/acao do jogador descrevendo o que ele faz ou observa NAO e um fato objetivo do mundo
  ate a narracao confirmar. Nao exija que o ESTADO registre a percepcao subjetiva do jogador.
- Segredo vazio no cadastro nao significa que o NPC nao possa ter conhecimento tecnico.
- Marque apenas contradicao factual clara: morto que age, local impossivel, objeto usado sem posse,
  evento ja resolvido repetido, fato canonico negado, conhecimento impossivel.
- Nao marque silencio, subtexto, mentira, mudanca de planos, ambiguidade ou interpretacao.
- Nao marque "estado nao atualizado" so porque um campo narrativo nao mudou; exija uma
  contradicao observavel entre a NARRACAO e um fato do ESTADO (local, objeto, ferimento,
  presenca ou evento).
- OMISSAO NAO E CONTRADICAO: nao mencionar um ferimento, flag, objeto ou NPC do estado
  em um turno nao e erro. So marque se a narracao AFIRMAR o oposto (ferimento curado,
  objeto perdido, NPC ausente, evento desfeito).
- Na duvida, aprove (ok=true): prefira falso negativo a inventar problema.
- Se nao houver contradicao clara, use ok=true, severity=baixa e issues=[].
- THREADS abertas sao compromissos: prazo vencido sem avanco/resolucao e falha de continuidade.
Responda APENAS com JSON valido, sem markdown e sem texto fora dele.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "severity": {"type": "string", "enum": ["baixa", "media", "alta"]},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tipo": {"type": "string"},
                    "gravidade": {
                        "type": "string",
                        "enum": ["baixa", "media", "alta"],
                    },
                    "descricao": {"type": "string"},
                    "evidencia": {"type": "string"},
                },
                "required": ["tipo", "gravidade", "descricao"],
            },
        },
        "resumo": {"type": "string"},
    },
    "required": ["ok", "severity", "issues", "resumo"],
}

_SEVERITY = {"baixa": 0, "media": 1, "alta": 2}


def _extract_json(text):
    raw = (text or "").strip()
    raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    i, j = raw.find("{"), raw.rfind("}")
    if i == -1 or j == -1:
        raise ValueError("JSON nao encontrado")
    data = json.loads(raw[i:j + 1])
    if not isinstance(data, dict):
        raise ValueError("JSON invalido")
    return data


def _world_facts(conn, world, limit=12):
    if not world:
        return []
    rows = conn.execute(
        "SELECT text FROM world_facts WHERE world_id=? ORDER BY id",
        (world["id"],),
    ).fetchall()
    return [row["text"][:220] for row in rows[:limit]]


def _threads(conn, sid, limit=8):
    rows = conn.execute(
        "SELECT id, kind, title, status, due_turn FROM threads "
        "WHERE session_id=? AND status='aberta' ORDER BY id DESC LIMIT ?",
        (sid, limit),
    ).fetchall()
    return [
        f"#{row['id']} [{row['kind']}] {row['title']}"
        + (f" (prazo: turno {row['due_turn']})" if row["due_turn"] else "")
        for row in rows
    ]


def _entities(conn, sid, npc, protagonist, limit=10):
    names = []
    for char in (npc, protagonist):
        if char and char.get("name"):
            names.append(char["name"])
    if not names:
        return []
    placeholders = ",".join("?" for _ in names)
    rows = conn.execute(
        "SELECT entity, fact FROM entity_facts "
        f"WHERE session_id=? AND entity IN ({placeholders}) "
        "ORDER BY id DESC LIMIT ?",
        (sid, *names, limit),
    ).fetchall()
    return [f"{row['entity']}: {row['fact']}" for row in rows]


def _messages(conn, sid, limit=10, size=600):
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE session_id=? "
        "ORDER BY id DESC LIMIT ?",
        (sid, limit),
    ).fetchall()
    lines = []
    for row in reversed(rows):
        content = (row["content"] or "").strip().replace("\n", " ")
        lines.append(f"{row['role']}: {content[:size]}")
    return lines


def _normalize(data):
    issues = []
    for item in data.get("issues") or []:
        if not isinstance(item, dict):
            continue
        severity = str(item.get("gravidade") or "baixa").lower()
        if severity not in _SEVERITY:
            severity = "baixa"
        issues.append(
            {
                "tipo": str(item.get("tipo") or "outro")[:60],
                "gravidade": severity,
                "descricao": str(item.get("descricao") or "")[:300],
                "evidencia": str(item.get("evidencia") or "")[:300],
            }
        )
        if len(issues) >= 8:
            break
    severity = str(data.get("severity") or "baixa").lower()
    if severity not in _SEVERITY:
        severity = "baixa"
    if any(item["gravidade"] == "alta" for item in issues):
        severity = "alta"
    ok = bool(data.get("ok")) and severity != "alta"
    return {
        "ok": ok,
        "severity": severity,
        "issues": issues,
        "summary": str(data.get("resumo") or "")[:400],
    }


async def check(conn, sid, limit=10):
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        raise LookupError("sessao nao encontrada")
    npc_row = conn.execute(
        "SELECT * FROM characters WHERE id=?", (sess["character_id"],)
    ).fetchone()
    protagonist_row = conn.execute(
        "SELECT * FROM characters WHERE id=?", (sess["protagonist_id"],)
    ).fetchone() if sess["protagonist_id"] else None
    npc = db.rowdict(npc_row)
    protagonist = db.rowdict(protagonist_row)
    world = None
    if npc and npc.get("world_id"):
        world = db.rowdict(
            conn.execute(
                "SELECT * FROM worlds WHERE id=?", (npc["world_id"],)
            ).fetchone()
        )
    state = aux.load_state(sess, world)
    state_view = {
        key: state.get(key)
        for key in (
            "turn",
            "location",
            "time",
            "goal",
            "mood",
            "pending",
            "inventory",
            "objects",
            "npcs",
            "injuries",
            "flags",
            "facts",
            "events",
            "scene",
            "relationship_events",
            "pacing",
        )
    }
    parts = []
    if world:
        parts.append("## MUNDO\n" + prompts.world_card(world))
        facts = _world_facts(conn, world)
        if facts:
            parts.append("## FATOS DO MUNDO\n- " + "\n- ".join(facts))
    if npc:
        parts.append("## NPC\n" + prompts.npc_card(npc))
    if protagonist:
        parts.append("## JOGADOR\n" + prompts.protagonist_card(protagonist))
    parts.append(
        "## ESTADO ATUAL\n"
        + json.dumps(state_view, ensure_ascii=False)[:3000]
    )
    threads = _threads(conn, sid)
    if threads:
        parts.append("## THREADS ABERTAS\n- " + "\n- ".join(threads))
    entities = _entities(conn, sid, npc, protagonist)
    if entities:
        parts.append("## FATOS POR ENTIDADE\n- " + "\n- ".join(entities))
    messages = _messages(conn, sid, limit=limit)
    if messages:
        parts.append("## ULTIMAS MENSAGENS\n" + "\n\n".join(messages))
    parts.append(
        "Avalie a consistencia do estado e das ultimas mensagens. "
        "Use ok=false se houver contradicao relevante; severidade alta para "
        "contradicao factual clara, media para ambiguidade/risco, baixa para detalhe."
    )
    data = None
    last_error = None
    for attempt in range(2):
        out = await llm.complete(
            MODEL,
            [
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": "\n\n".join(parts)},
            ],
            False,
            {
                "temperature": 0.1 if attempt == 0 else 0.2,
                "num_ctx": 8192,
                "num_predict": 600,
            },
            fmt=SCHEMA,
            base=llm.OLLAMA_UTILITY,
        )
        try:
            data = _extract_json(out)
            break
        except Exception as exc:
            last_error = exc
            print(
                "[consistency-raw]",
                (out or "")[:4000].replace("\n", " | "),
            )
    if data is None:
        raise last_error or ValueError("juiz sem JSON valido")
    result = _normalize(data)
    result["model"] = MODEL
    result["turn"] = int(state.get("turn", 0) or 0)
    result["checked_at"] = time.time()
    return result


def save(conn, sid, result):
    conn.execute(
        "INSERT INTO consistency_checks "
        "(session_id, turn, ok, severity, issues, summary, model, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            sid,
            int(result.get("turn", 0) or 0),
            1 if result.get("ok") else 0,
            str(result.get("severity") or "baixa"),
            json.dumps(result.get("issues") or [], ensure_ascii=False),
            str(result.get("summary") or ""),
            str(result.get("model") or ""),
            float(result.get("checked_at") or time.time()),
        ),
    )
    conn.commit()


def recent(conn, sid, limit=10):
    rows = conn.execute(
        "SELECT * FROM consistency_checks WHERE session_id=? "
        "ORDER BY id DESC LIMIT ?",
        (sid, max(1, int(limit))),
    ).fetchall()
    out = []
    for row in rows:
        item = db.rowdict(row)
        try:
            item["issues"] = json.loads(item.get("issues") or "[]")
        except ValueError:
            item["issues"] = []
        item["ok"] = bool(item.get("ok"))
        out.append(item)
    return out
