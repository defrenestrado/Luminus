"""Vida fora de cena: agendas de NPCs entre turnos.

Inspirado no WorldLines/Multihog: o mundo não espera o jogador. A cada N turnos,
1–2 NPCs do elenco (que não estão na cena) avançam uma agenda própria. Isso é
determinístico, não chama LLM e é injetado como rumor/contexto — o Narrador não
deve virar onisciente nem revelar o que o jogador não presenciou.
"""

from __future__ import annotations

import os
import random
import unicodedata
from typing import Any, Dict, Iterable, List, Optional


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        return default


OFFSCREEN_EVERY = _env_int("LUMINUS_OFFSCREEN_EVERY", 4)
MAX_EVENTS = 6


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _archetype(char: Dict[str, Any]) -> str:
    blob = _norm(
        " ".join(
            str(char.get(key) or "")
            for key in ("name", "tagline", "personality", "backstory", "tags", "relationship")
        )
    )
    rules = (
        ("guarda", ("guarda", "soldado", "capitao", "capitão", "patrulha", "milicia", "milícia", "sentinela")),
        ("nobre", ("nobre", "nobreza", "senhor", "senhora", "herdeiro", "herdeira", "corte", "duque", "duquesa", "principe", "príncipe")),
        ("criminoso", ("ladrao", "ladrão", "ladra", "contraband", "assass", "gangue", "crime", "furto", "mercado negro")),
        ("mercador", ("mercador", "comerciante", "loja", "vendedor", "vendedora", "oficina", "ferreiro", "boticario", "boticário")),
        ("sagrado", ("sacerdote", "sacerdotisa", "padre", "igreja", "templo", "curandeir", "medico", "médica", "oraculo", "oráculo")),
        ("estudioso", ("estudioso", "estudiosa", "mago", "maga", "erudit", "biblioteca", "alquim", "pesquisador", "pesquisadora")),
        ("nobre", ("politic", "conselho", "diplomata", "embaixador")),
    )
    for kind, words in rules:
        if any(word in blob for word in words):
            return kind
    return "comum"


_TEMPLATES = {
    "guarda": (
        ("patrulha", "mudou a rota de patrulha e passou a revistar quem entra e sai"),
        ("boato", "prendeu alguém por engano e a notícia correu mais rápido que a verdade"),
        ("pressao", "recebeu ordem de apertar o cerco e está cobrando propina de quem tem pressa"),
    ),
    "nobre": (
        ("corte", "enviou uma carta selada para um aliado fora da cidade"),
        ("faccao", "recebeu um rival em particular; ninguém sabe o que foi negociado"),
        ("rumor", "espalhou um boato conveniente sobre a própria família"),
    ),
    "criminoso": (
        ("servico", "aceitou um serviço que exige alguém de fora da cidade"),
        ("esconderijo", "mudou de esconderijo e deixou para trás uma pista involuntária"),
        ("divida", "cobrou uma dívida de um nome conhecido na cena"),
    ),
    "mercador": (
        ("estoque", "comprou um lote suspeito e agora precisa vendê-lo rápido"),
        ("preco", "subiu os preços de itens que vão faltar"),
        ("visita", "recebeu uma visita fora do horário e fechou a loja mais cedo"),
    ),
    "sagrado": (
        ("sinal", "interpretou um sinal e mudou o tom dos sermões"),
        ("doente", "atendeu alguém ferido que não quis se identificar"),
        ("rumor", "ouviu uma confissão e agora evita certas perguntas"),
    ),
    "estudioso": (
        ("descoberta", "encontrou uma referência que contradiz a versão oficial"),
        ("pedido", "encomendou um material raro sem explicar para quê"),
        ("aviso", "mandou um aviso cifrado para um contato distante"),
    ),
    "comum": (
        ("rotina", "mudou a rotina e foi visto em um lugar onde não costuma ir"),
        ("conversa", "teve uma conversa tensa com alguém do elenco"),
        ("noticia", "ouviu uma notícia e está decidindo o que fazer com ela"),
    ),
}

_VISIBILITY = {
    "patrulha": "publico",
    "pressao": "rumor",
    "prendeu": "publico",
    "corte": "privado",
    "faccao": "rumor",
    "rumor": "rumor",
    "servico": "privado",
    "esconderijo": "privado",
    "divida": "rumor",
    "estoque": "privado",
    "preco": "publico",
    "visita": "rumor",
    "sinal": "publico",
    "doente": "privado",
    "descoberta": "privado",
    "pedido": "privado",
    "aviso": "privado",
    "rotina": "publico",
    "conversa": "rumor",
    "noticia": "rumor",
}


def _present_names(state: Dict[str, Any]) -> set[str]:
    scene = state.get("scene") if isinstance(state.get("scene"), dict) else {}
    present = scene.get("present") or []
    return {_norm(str(name)) for name in present if str(name).strip()}


def _candidates(
    state: Dict[str, Any],
    cast: Iterable[Dict[str, Any]],
    npc: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    present = _present_names(state)
    people: List[Dict[str, Any]] = [c for c in (cast or []) if c]
    if npc:
        people.append(npc)
    seen = set()
    out = []
    for char in people:
        key = str(char.get("id") or char.get("name") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        name = str(char.get("name") or "").strip()
        if not name or _norm(name) in present:
            continue
        out.append(char)
    return out


def tick(
    state: Dict[str, Any],
    cast: Iterable[Dict[str, Any]],
    npc: Optional[Dict[str, Any]] = None,
    force: bool = False,
    seed: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Avança agendas fora de cena. Retorna [] quando não é turno de tick."""
    try:
        turn = int(state.get("turn", 0) or 0)
    except (TypeError, ValueError):
        turn = 0
    every = max(2, OFFSCREEN_EVERY)
    if not force and turn <= 0:
        return []
    if not force and turn % every != 0:
        return []

    people = _candidates(state, cast, npc)
    if not people:
        return []

    seed = str(seed or state.get("seed") or "luminus")
    rng = random.Random(f"{seed}:offscreen:{turn}")
    count = 1 if len(people) == 1 else 2
    if len(people) > 3 and rng.random() < 0.25:
        count = 2
    chosen = rng.sample(people, k=min(count, len(people)))

    location = str(
        (state.get("scene") or {}).get("location")
        or state.get("location")
        or "fora de cena"
    )[:80]
    events: List[Dict[str, Any]] = []
    for char in chosen:
        kind = _archetype(char)
        template = rng.choice(_TEMPLATES.get(kind, _TEMPLATES["comum"]))
        action, text = template
        visibility = _VISIBILITY.get(action, "rumor")
        if kind == "comum" and rng.random() < 0.35:
            visibility = "privado"
        event = {
            "turn": turn,
            "npc": str(char.get("name") or "NPC")[:60],
            "npc_id": char.get("id"),
            "archetype": kind,
            "kind": action,
            "text": f"{char.get('name') or 'NPC'} {text}.",
            "visibility": visibility,
            "location": location,
            "consequence": (
                "Pode chegar como rumor; não é conhecimento automático do jogador."
                if visibility != "privado"
                else "Ainda não é do conhecimento do jogador."
            ),
        }
        events.append(event)

    existing = state.get("offscreen")
    if not isinstance(existing, list):
        existing = []
    existing.extend(events)
    state["offscreen"] = existing[-12:]
    return events


def block(events: List[Dict[str, Any]]) -> str:
    if not events:
        return ""
    lines = [
        "## EVENTOS FORA DE CENA (o mundo andou sem o jogador)",
        "Use no máximo UM destes eventos, e só se ele couber naturalmente na cena.",
        "O jogador NÃO sabe disso automaticamente: mostre apenas se ele presenciar, "
        "ouvir um rumor ou encontrar um vestígio. Não vire narrador onisciente.",
    ]
    for event in events[:4]:
        vis = event.get("visibility") or "rumor"
        lines.append(
            f"- [{vis}] T{event.get('turn')} {event.get('text')} "
            f"({event.get('consequence')})"
        )
    return "\n".join(lines)
