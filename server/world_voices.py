"""World Voices: cartas, avisos, boatos e relatórios com conhecimento limitado.

Inspirado no RePoG. Um artefato pode existir sem que suas alegações sejam a
verdade objetiva: ele tem autor, canal, público e conhecimentos limitados. O
Narrador pode mostrar o texto se o jogador tiver acesso, mas não deve tratar as
alegações como fato canônico automaticamente.

Geração determinística (sem LLM): usa eventos fora de cena, fatos do estado e
fatos do mundo como matéria-prima.
"""

from __future__ import annotations

import json
import os
import random
import time
import unicodedata
from typing import Any, Dict, Iterable, List, Optional


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        return default


VOICES_EVERY = _env_int("LUMINUS_VOICES_EVERY", 6)


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _clip(value: Any, size: int) -> str:
    text = str(value or "").strip()
    return text[:size]


def _world_facts(world: Optional[Dict[str, Any]], limit: int = 4) -> List[str]:
    if not world:
        return []
    facts = world.get("facts") or []
    if not isinstance(facts, list):
        return []
    return [str(item)[:220] for item in facts[-limit:] if str(item).strip()]


def _materials(
    state: Dict[str, Any],
    world: Optional[Dict[str, Any]],
    events: Optional[Iterable[Dict[str, Any]]],
    world_facts: Optional[Iterable[str]] = None,
) -> List[str]:
    out: List[str] = []
    for event in (events or []):
        if isinstance(event, dict) and event.get("text"):
            out.append(_clip(event["text"], 220))
    facts = state.get("facts") or []
    if isinstance(facts, list):
        out.extend(_clip(item, 220) for item in facts[-3:] if str(item).strip())
    if world_facts:
        out.extend(_clip(item, 220) for item in world_facts if str(item).strip())
    out.extend(_world_facts(world))
    pending = _clip(state.get("pending"), 180)
    if pending:
        out.append(pending)
    if not out:
        scene = state.get("scene") if isinstance(state.get("scene"), dict) else {}
        location = _clip(scene.get("location") or state.get("location") or "a cidade", 80)
        mood = _clip(state.get("mood") or "tensão", 60)
        out.append(f"o clima em {location} está {mood}")
    # dedup preservando ordem
    seen = set()
    unique = []
    for item in out:
        key = _norm(item)
        if key and key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


_TEMPLATES = {
    "rumor": (
        'Dizem por aí que {claim}. Quem contou garante que ouviu de alguém que estava lá.',
        'A história corre baixinho: {claim}. Ninguém assina, todos repetem.',
    ),
    "aviso": (
        'AVISO DA AUTORIDADE\n\n{claim}\n\nA Guarda pede que qualquer informação seja levada ao posto mais próximo.',
        'AVISO\n\nConsta que {claim}. Evite o local até segunda ordem.',
    ),
    "carta": (
        'Ao meu caro aliado,\n\n{claim}\n\nQueime esta carta depois de ler. Não confie em todos os mensageiros.',
        'Meu amigo,\n\nEscrevo às pressas: {claim}\n\nNão responda por escrito.',
    ),
    "relatorio": (
        'RELATÓRIO INTERNO\n\nAssunto: {claim}\n\nObservação: a fonte é indireta e o relato ainda não foi confirmado.',
        'RELATÓRIO DE CAMPO\n\n{claim}\n\nProvidência: observar e não agir sem confirmação.',
    ),
}

_AUTHORS = {
    "rumor": ("um mensageiro anônimo", "uma voz de taberna", "um vizinho curioso"),
    "aviso": ("a Guarda local", "o Conselho do porto", "um oficial de plantão"),
    "carta": ("um aliado distante", "uma mão que não assina", "um contato antigo"),
    "relatorio": ("um escrivão", "um informante pago", "um agente de campo"),
}


def generate(
    state: Dict[str, Any],
    world: Optional[Dict[str, Any]] = None,
    npc: Optional[Dict[str, Any]] = None,
    cast: Optional[Iterable[Dict[str, Any]]] = None,
    events: Optional[Iterable[Dict[str, Any]]] = None,
    seed: Optional[str] = None,
    force: bool = False,
    session_id: Optional[int] = None,
    world_facts: Optional[Iterable[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Cria um artefato (ou None quando não é turno de gerar)."""
    try:
        turn = int(state.get("turn", 0) or 0)
    except (TypeError, ValueError):
        turn = 0
    every = max(2, VOICES_EVERY)
    last = int(state.get("last_voice_turn", -1) or -1)
    if not force:
        if turn <= 0 or turn % every != 0 or last == turn:
            return None

    materials = _materials(state, world, events, world_facts=world_facts)
    if not materials:
        return None
    seed = str(seed or state.get("seed") or "luminus")
    rng = random.Random(f"{seed}:voice:{turn}")
    kind = rng.choices(
        ("rumor", "aviso", "carta", "relatorio"),
        weights=(44, 20, 20, 16),
        k=1,
    )[0]
    primary_event = None
    for event in (events or []):
        if isinstance(event, dict) and event.get("text"):
            primary_event = event
            break
    # Se veio de um evento fora de cena, o artefato nasce dele.
    claim = (
        _clip(primary_event["text"], 260)
        if primary_event is not None
        else rng.choice(materials)
    )
    # Distorce uma parte da alegação em alguns casos (boato não é cópia fiel).
    truth_status = rng.choices(
        ("verdadeiro", "distorcido", "incerto", "falso"),
        weights=(30, 35, 25, 10),
        k=1,
    )[0]
    if truth_status in ("distorcido", "falso") and len(claim) > 40:
        claim = claim[: max(20, len(claim) // 2)].rstrip(" ,.;") + " ..."
    author = rng.choice(_AUTHORS.get(kind, _AUTHORS["rumor"]))
    event_author = None
    if primary_event and primary_event.get("npc"):
        event_author = str(primary_event["npc"])[:60]
    if event_author:
        author = event_author

    body = rng.choice(_TEMPLATES[kind]).format(claim=claim)
    known_by = [author]
    if event_author and event_author not in known_by:
        known_by.append(event_author)
    if npc and npc.get("name") and kind in ("carta", "relatorio"):
        known_by.append(str(npc["name"])[:60])
    known_by = list(dict.fromkeys(known_by))[:5]

    title = {
        "rumor": "Boato que corre",
        "aviso": "Aviso afixado",
        "carta": "Carta sem brasão",
        "relatorio": "Relatório de campo",
    }[kind]
    voice = {
        "session_id": session_id,
        "world_id": (world or {}).get("id"),
        "turn": turn,
        "kind": kind,
        "title": title,
        "author": author,
        "body": body[:1400],
        "claims": [claim[:300]],
        "truth_status": truth_status,
        "known_by": known_by,
        "visibility": "restrito" if kind in ("carta", "relatorio") else "publico",
        "source": "offscreen" if event_author else "estado",
        "created_at": time.time(),
    }
    state["last_voice_turn"] = turn
    voices = state.get("voices")
    if not isinstance(voices, list):
        voices = []
    voices.append(
        {
            "turn": turn,
            "kind": kind,
            "title": title,
            "author": author,
            "body": body[:600],
            "visibility": voice["visibility"],
        }
    )
    state["voices"] = voices[-6:]
    return voice


def block(voice: Optional[Dict[str, Any]]) -> str:
    if not voice:
        return ""
    known = ", ".join(str(name)[:40] for name in (voice.get("known_by") or [])[:5])
    lines = [
        "## ARTEFATO DO MUNDO (documento/boato)",
        f"Tipo: {voice.get('kind')} | Autor: {voice.get('author')} | "
        f"Visibilidade: {voice.get('visibility')}",
        f"Quem sabe da existência: {known or 'poucas pessoas'}",
        "Texto do artefato:",
        str(voice.get("body") or "")[:1200],
        "Regras: isto é o que o AUTOR afirma, não necessariamente a verdade. "
        "O Narrador só mostra/referencia se o jogador tiver acesso (presenciar, "
        "receber, encontrar, ouvir). Não transforme a alegação em fato canônico "
        "sem confirmação na cena.",
    ]
    return "\n".join(lines)


def save(conn, voice: Dict[str, Any]) -> int:
    cur = conn.execute(
        "INSERT INTO world_voices "
        "(session_id, world_id, turn, kind, title, author, body, claims, "
        " truth_status, known_by, visibility, source, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            voice.get("session_id"),
            voice.get("world_id"),
            int(voice.get("turn", 0) or 0),
            str(voice.get("kind") or "rumor"),
            str(voice.get("title") or "")[:160],
            str(voice.get("author") or "")[:120],
            str(voice.get("body") or "")[:2000],
            json.dumps(voice.get("claims") or [], ensure_ascii=False),
            str(voice.get("truth_status") or "incerto"),
            json.dumps(voice.get("known_by") or [], ensure_ascii=False),
            str(voice.get("visibility") or "rumor"),
            str(voice.get("source") or "")[:120],
            float(voice.get("created_at") or time.time()),
        ),
    )
    return int(cur.lastrowid)


def _row(row) -> Dict[str, Any]:
    data = dict(row)
    for key in ("claims", "known_by"):
        try:
            value = json.loads(data.get(key) or "[]")
        except ValueError:
            value = []
        data[key] = value if isinstance(value, list) else []
    return data


def recent(conn, session_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM world_voices WHERE session_id=? ORDER BY id DESC LIMIT ?",
        (session_id, max(1, int(limit))),
    ).fetchall()
    return [_row(row) for row in rows]
