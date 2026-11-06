"""aux.py — Fase 1: ledger factual, gate de conhecimento, escalator e arbiter.

Tudo com orcamento pequeno de tokens (blocos injetados ~100-250 tokens cada).
Nada aqui chama LLM; o estado e atualizado a partir do plano do Diretor.
"""

import copy
import json
import re

import resolution

PLAN_RE = re.compile(r"^\s*(OBJETIVO|NPC_INTENCAO|NPC_EMOCAO|CONSEQUENCIA|DETALHES|EVITAR|FATOS|NR_DELTA|VINCULO)\s*:\s*(.+)$", re.I)

TENSION_WORDS = (
    "sangue", "morte", "ataca", "atacar", "grito", "ameaça", "ameaca",
    "ferida", "ferido", "luta", "matar", "faca", "espada", "perigo", "corre",
)

WARM_WORDS = ("obrigad", "agradec", "por favor", "confia", "confio", "admiro",
              "admira", "bonit", "linda", "inteligente", "gentil", "carinho",
              "desculpa", "elogio", "elogia", "cuidar", "gratid")
INSULT_WORDS = ("idiota", "burra", "cala", "some", "te mato", "amea", "nojenta",
                "vadia", "puta", "inutil", "lixo")

NR_START = 20
SECRET_GATE = 65
AXES_START = {"trust": 30, "intimacy": 20, "respect": 30, "tension": 20}


def _inv_entry(item):
    """Preserva itens declarativos (dict) e normaliza strings legadas."""
    if isinstance(item, dict):
        info = resolution.item_info(item)
        if not info:
            return None
        effects = info.get("effects") or {}
        entry = {"name": info["name"]}
        if effects:
            entry["effects"] = effects
        return entry
    text = str(item or "").strip()
    return text[:80] if text else None


def _injury_entry(item):
    """Preserva ferimentos declarativos (dict) e normaliza strings legadas."""
    if isinstance(item, dict):
        info = resolution.injury_info(item)
        if not info:
            return None
        return {
            "name": info["name"],
            "penalty": info["penalty"],
            "dc": info["dc"],
        }
    text = str(item or "").strip()
    return text[:80] if text else None


def _item_text(item) -> str:
    return resolution.item_name(item) or str(item)


def _injury_text(item) -> str:
    return str(resolution.injury_info(item).get("name") or item)


def default_state(world=None):
    return {
        "turn": 0,
        "location": (world.get("name") if world else "") or "",
        "time": "",
        "goal": "",
        "mood": "neutro",
        "pending": "",
        "passive": 0,
        "tension": 0,
        "trust": 0,
        "facts": [],
        "inventory": [],
        "objects": {},
        "npcs": {},
        "injuries": [],
        "flags": {},
        "npc_notes": [],
        "nr": NR_START,
        "axes": dict(AXES_START),
        "vinculo": "",
        "cast": {},
        "events": [],
        "identity": {},
        "persona": {"arc": "", "history": []},
        "session": {
            "nr": NR_START,
            "axes": dict(AXES_START),
            "vinculo": "",
            "goal": "",
            "status": "",
        },
        "moment": {"mood": "", "intent": "", "turn": 0},
        "scene": {
            "id": 0,
            "title": "",
            "location": "",
            "present": [],
            "action": "",
            "turn": 0,
            "ended": False,
            "transition": "",
            "reason": "",
        },
        "relationship_events": [],
        "pacing": {"beat": "", "turn": 0},
    }


def load_state(sess, world=None, npc=None):
    st = default_state(world)
    raw = ""
    try:
        raw = sess["ledger"] or ""
    except (KeyError, IndexError, TypeError):
        raw = ""
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                st.update(parsed)
        except ValueError:
            pass
    try:
        st["nr"] = max(0, min(100, int(st.get("nr", NR_START) or NR_START)))
    except (ValueError, TypeError):
        st["nr"] = NR_START
    ax = st.get("axes")
    st["axes"] = {**AXES_START, **ax} if isinstance(ax, dict) else dict(AXES_START)
    if not isinstance(st.get("cast"), dict):
        st["cast"] = {}
    if not isinstance(st.get("events"), list):
        st["events"] = []
    if not isinstance(st.get("relationship_events"), list):
        st["relationship_events"] = []
    for key, default in (
        ("identity", {}),
        ("persona", {"arc": "", "history": []}),
        ("session", {}),
        ("moment", {"mood": "", "intent": "", "turn": 0}),
        ("scene", {
            "id": 0, "title": "", "location": "", "present": [],
            "action": "", "turn": 0, "ended": False,
            "transition": "", "reason": "",
        }),
        ("pacing", {"beat": "", "turn": 0}),
    ):
        if not isinstance(st.get(key), dict):
            st[key] = copy.deepcopy(default)
    st["persona"].setdefault("arc", "")
    st["persona"].setdefault("history", [])
    ensure_layers(st, npc, turn=st.get("turn", 0))
    ensure_scene(st, None, None)
    return st


def _clip(value, size):
    text = str(value or "").strip()
    return text[:size]


def _identity_from_char(char):
    """Raiz imutavel da personagem (camada PHASE-Tree 'identity')."""
    anchors = []
    for value in (
        char.get("tagline"),
        char.get("personality"),
        char.get("speech_style"),
        char.get("fears"),
    ):
        text = _clip(value, 170)
        if text and text not in anchors:
            anchors.append(text)
    boundaries = []
    if _clip(char.get("fears"), 140):
        boundaries.append("Teme: " + _clip(char.get("fears"), 140))
    if _clip(char.get("dislikes"), 140):
        boundaries.append("Nao tolera: " + _clip(char.get("dislikes"), 140))
    return {
        "character_id": char.get("id"),
        "anchors": anchors[:4],
        "voice": _clip(char.get("speech_style"), 180),
        "boundaries": boundaries[:2],
    }


def ensure_identity(target, char):
    """Mantem a raiz de identidade imutavel; so preenche o que falta."""
    if not isinstance(target.get("identity"), dict):
        target["identity"] = {}
    identity = target["identity"]
    char_id = char.get("id") if char else None
    if (
        char_id is not None
        and identity.get("character_id") not in (None, char_id)
    ):
        identity.clear()
    if char:
        base = _identity_from_char(char)
        for key in ("character_id", "anchors", "voice", "boundaries"):
            if not identity.get(key):
                identity[key] = base.get(key)
    return identity


def ensure_layers(target, char=None, turn=0):
    """Sincroniza as camadas persona/sessao/momento a partir dos campos planos."""
    ensure_identity(target, char)
    persona = target.get("persona")
    if not isinstance(persona, dict):
        persona = {}
    persona.setdefault("arc", "")
    history = persona.get("history")
    persona["history"] = history[-6:] if isinstance(history, list) else []
    if not persona.get("arc"):
        persona["arc"] = _clip(target.get("vinculo"), 220)
    target["persona"] = persona

    session = target.get("session")
    if not isinstance(session, dict):
        session = {}
    try:
        session["nr"] = max(0, min(100, int(
            target.get("nr", NR_START) or NR_START
        )))
    except (ValueError, TypeError):
        session["nr"] = NR_START
    ax = target.get("axes")
    session["axes"] = (
        {**AXES_START, **ax} if isinstance(ax, dict) else dict(AXES_START)
    )
    session["vinculo"] = _clip(target.get("vinculo"), 220)
    session["goal"] = _clip(target.get("goal"), 240)
    session["status"] = _clip(target.get("status"), 180)
    target["session"] = session

    moment = target.get("moment")
    if not isinstance(moment, dict):
        moment = {}
    moment["mood"] = _clip(target.get("mood"), 200)
    intent = _clip(target.get("intent"), 240)
    if intent or not moment.get("intent"):
        moment["intent"] = intent
    try:
        moment["turn"] = int(turn or target.get("turn", 0) or 0)
    except (ValueError, TypeError):
        moment["turn"] = 0
    target["moment"] = moment
    return target


def sync_layers(target, turn=0, persona_text=None, moment_text=None):
    """Propaga uma mudanca plana para as camadas sem tocar na identidade."""
    ensure_layers(target, None, turn=turn)
    if persona_text:
        text = _clip(persona_text, 220)
        persona = target["persona"]
        history = list(persona.get("history") or [])
        last = ""
        if history and isinstance(history[-1], dict):
            last = str(history[-1].get("text") or "")
        if text and text != last:
            persona["arc"] = text
            history.append({"turn": int(turn or 0), "text": text})
            persona["history"] = history[-6:]
    if moment_text:
        target["moment"]["mood"] = _clip(moment_text, 200)
    return target


def main_layers(state):
    ensure_layers(state, None, turn=state.get("turn", 0))
    return {
        "identity": state.get("identity") or {},
        "persona": state.get("persona") or {},
        "session": state.get("session") or {},
        "moment": state.get("moment") or {},
    }


def cast_layers(rel):
    ensure_layers(rel, None, turn=rel.get("turn", 0))
    return {
        "identity": rel.get("identity") or {},
        "persona": rel.get("persona") or {},
        "session": rel.get("session") or {},
        "moment": rel.get("moment") or {},
    }


def _name_map(main_npc=None, cast=None):
    out = {}
    for char in [main_npc, *(cast or [])]:
        if not char:
            continue
        name = str(char.get("name") or "").strip()
        if name:
            out[_name_key(name)] = name
            first = name.split()[0]
            if first:
                out.setdefault(_name_key(first), name)
    return out


def ensure_scene(state, main_npc=None, cast=None):
    scene = state.get("scene")
    if not isinstance(scene, dict):
        scene = {}
    for key, default in (
        ("id", 0),
        ("title", ""),
        ("location", ""),
        ("present", []),
        ("action", ""),
        ("turn", 0),
        ("ended", False),
        ("transition", ""),
        ("reason", ""),
    ):
        scene.setdefault(key, default)
    if not isinstance(scene.get("present"), list):
        scene["present"] = []
    scene["present"] = [str(name).strip() for name in scene["present"] if str(name).strip()]
    if not scene.get("location"):
        scene["location"] = _clip(state.get("location"), 120)
    if not scene["present"]:
        names = []
        if main_npc and main_npc.get("name"):
            names.append(str(main_npc["name"]))
        for char in cast or []:
            rel = (state.get("cast") or {}).get(str(char.get("id"))) or {}
            status = str(rel.get("status") or "").strip().lower()
            if status not in ("fora da cena", "ausente", "fora"):
                if char.get("name"):
                    names.append(str(char["name"]))
        scene["present"] = list(dict.fromkeys(names))[:12]
    state["scene"] = scene
    return scene


def _remember_codex(state, scene, turn, main_npc=None, cast=None):
    codex = state.get("codex")
    if not isinstance(codex, dict):
        codex = {}
    locations = codex.get("locations")
    if not isinstance(locations, list):
        locations = []
    location = str(scene.get("location") or "").strip()
    if location:
        key = _name_key(location)
        found = None
        for item in locations:
            if isinstance(item, dict) and _name_key(item.get("name") or "") == key:
                found = item
                break
        if found is not None:
            found["last_turn"] = turn
        else:
            locations.append(
                {"name": location[:120], "first_turn": turn, "last_turn": turn}
            )
    codex["locations"] = locations[-40:]

    people = codex.get("people")
    if not isinstance(people, list):
        people = []
    names = []
    if main_npc and main_npc.get("name"):
        names.append(str(main_npc["name"]))
    for char in cast or []:
        if char.get("name"):
            names.append(str(char["name"]))
    for name in names:
        key = _name_key(name)
        found = None
        for item in people:
            if isinstance(item, dict) and _name_key(item.get("name") or "") == key:
                found = item
                break
        if found is not None:
            found["last_turn"] = turn
        else:
            people.append(
                {"name": name[:80], "first_turn": turn, "last_turn": turn}
            )
    codex["people"] = people[-40:]
    state["codex"] = codex


def apply_scene(state, plan_data, main_npc=None, cast=None):
    cena = (plan_data or {}).get("cena")
    if not isinstance(cena, dict):
        return ensure_scene(state, main_npc, cast)
    scene = ensure_scene(state, main_npc, cast)
    action = str(cena.get("acao") or "continuar").strip().lower()
    if action not in ("continuar", "iniciar", "trocar", "entrar", "sair", "encerrar"):
        action = "continuar"

    # Coerencia de ambiente: o planejador as vezes muda o local no estado mas
    # mantem "continuar". Nao deixamos o local do estado divergir do local da
    # cena (teleporte sem transicao): promovemos para "trocar".
    title = _clip(cena.get("titulo"), 120)
    local = _clip(cena.get("local"), 120)
    state_local = _clip(state.get("location"), 120)
    target_local = local or state_local
    prev_local = _clip(scene.get("location"), 120)
    promoted = False
    if (
        action == "continuar"
        and target_local
        and prev_local
        and _name_key(target_local) != _name_key(prev_local)
    ):
        promoted = True
        action = "trocar"
        scene["ended"] = False
        scene["transition"] = _clip(
            f"Deslocamento para {target_local}", 240
        )
        scene["reason"] = "Mudanca de local detectada no estado da cena."

    names = _name_map(main_npc, cast)
    present = list(scene.get("present") or [])
    raw_present = cena.get("presentes")
    raw_present = (
        [str(name).strip() for name in raw_present if str(name).strip()]
        if isinstance(raw_present, list) else []
    )
    canonical = []
    for name in raw_present:
        canonical.append(names.get(_name_key(name), name[:60]))
    foco = str((plan_data or {}).get("foco") or "").strip()
    foco_name = names.get(_name_key(foco), foco)
    if action in ("iniciar", "trocar"):
        scene["id"] = int(scene.get("id", 0) or 0) + 1
        if raw_present:
            present = canonical
        scene["ended"] = False
    elif action == "continuar":
        if raw_present:
            present = canonical
    elif action == "entrar":
        for name in canonical:
            if name not in present:
                present.append(name)
    elif action == "sair":
        remove = set(canonical)
        if not remove and foco_name:
            remove.add(foco_name)
        present = [name for name in present if name not in remove]
    elif action == "encerrar":
        scene["ended"] = True
        if raw_present:
            present = canonical
    if foco_name and action not in ("sair", "encerrar") and foco_name not in present:
        present.append(foco_name)
    scene["present"] = list(dict.fromkeys(present))[:12]
    scene["action"] = action
    scene["turn"] = int(state.get("turn", 0) or 0)
    if title:
        scene["title"] = title
    if action in ("iniciar", "trocar") and target_local:
        scene["location"] = target_local
        state["location"] = target_local
    elif action == "continuar" and local:
        scene["location"] = local
        state["location"] = local
    if action == "continuar":
        # Nao ha transicao numa continuacao; ignora texto de transicao que o
        # planejador tenha colocado por engano.
        scene["transition"] = ""
        scene["reason"] = ""
    elif not promoted:
        scene["transition"] = _clip(cena.get("transicao"), 240)
        scene["reason"] = _clip(cena.get("motivo"), 240)
    present_keys = {_name_key(name): name for name in scene["present"]}
    if main_npc:
        main_name = (main_npc.get("name") or "").strip()
        state.setdefault("session", {})["status"] = (
            "presente" if _name_key(main_name) in present_keys else "fora"
        )
    for char in cast or []:
        rel = (state.get("cast") or {}).get(str(char.get("id")))
        if not isinstance(rel, dict):
            continue
        name = (char.get("name") or rel.get("name") or "").strip()
        here = _name_key(name) in present_keys
        if here:
            if not str(rel.get("status") or "").strip() or str(rel.get("status")).lower() in ("fora da cena", "ausente", "fora"):
                rel["status"] = "presente na cena"
            state.setdefault("npcs", {}).setdefault(name[:40], "presente na cena")
        elif action in ("iniciar", "trocar", "sair", "encerrar"):
            rel["status"] = "fora da cena"
    return scene


def ensure_cast_state(state, cast):
    """Sincroniza o estado de vinculo dos NPCs secundarios do elenco."""
    current = state.get("cast")
    if not isinstance(current, dict):
        current = {}
    if cast is None:
        state["cast"] = current
        return current
    by_id = {
        str(c["id"]): c
        for c in cast
        if c and c.get("id") is not None
    }
    for key in list(current.keys()):
        if key not in by_id:
            current.pop(key, None)
    for key, char in by_id.items():
        rel = current.get(key)
        if not isinstance(rel, dict):
            rel = {}
        rel["name"] = (char.get("name") or rel.get("name") or "").strip()
        try:
            rel["nr"] = max(0, min(100, int(rel.get("nr", NR_START) or NR_START)))
        except (ValueError, TypeError):
            rel["nr"] = NR_START
        ax = rel.get("axes")
        rel["axes"] = (
            {**AXES_START, **ax} if isinstance(ax, dict) else dict(AXES_START)
        )
        rel.setdefault("vinculo", "")
        rel.setdefault("mood", "")
        rel.setdefault("goal", "")
        rel.setdefault("status", "")
        notes = rel.get("notes")
        rel["notes"] = notes[-6:] if isinstance(notes, list) else []
        ensure_layers(rel, char, turn=state.get("turn", 0))
        current[key] = rel
    state["cast"] = current
    return current


def cast_relationship(state, char_id):
    rel = (state.get("cast") or {}).get(str(char_id)) or {}
    return {
        "nr": int(rel.get("nr", NR_START) or NR_START),
        "axes": rel.get("axes") or dict(AXES_START),
        "vinculo": rel.get("vinculo") or "",
        "mood": rel.get("mood") or "",
        "goal": rel.get("goal") or "",
        "status": rel.get("status") or "",
        "layers": {
            "identity": rel.get("identity") or {},
            "persona": rel.get("persona") or {},
            "session": rel.get("session") or {},
            "moment": rel.get("moment") or {},
        },
    }


def save_state(conn, sid, state):
    conn.execute(
        "UPDATE sessions SET ledger=? WHERE id=?",
        (json.dumps(state, ensure_ascii=False), sid),
    )


def parse_plan(plan):
    out = {}
    for line in (plan or "").splitlines():
        m = PLAN_RE.match(line)
        if m:
            out[m.group(1).upper()] = m.group(2).strip()
    return out


def _json_object_text(text):
    start = text.find("{")
    if start == -1:
        return ""
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return ""


def parse_plan_json(raw):
    text = (raw or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    candidate = _json_object_text(text)
    if not candidate:
        i, j = text.find("{"), text.rfind("}")
        if i == -1 or j == -1:
            return None
        candidate = text[i:j + 1]
    try:
        data = json.loads(candidate, strict=False)
    except ValueError:
        repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            data = json.loads(repaired, strict=False)
        except ValueError:
            return None
    if not isinstance(data, dict):
        return None

    def text_field(key, limit):
        value = data.get(key)
        return str(value).strip()[:limit] if value is not None else ""

    def list_field(key, limit, size):
        value = data.get(key)
        if not isinstance(value, list):
            return []
        return [
            str(item).strip()[:size]
            for item in value
            if str(item).strip()
        ][:limit]

    try:
        delta = int(data.get("nr_delta", 0) or 0)
    except (ValueError, TypeError):
        delta = 0
    estado = data.get("estado") if isinstance(data.get("estado"), dict) else {}
    objects = estado.get("objetos") if isinstance(estado.get("objetos"), dict) else {}
    npcs = estado.get("npcs") if isinstance(estado.get("npcs"), dict) else {}
    flags = estado.get("flags") if isinstance(estado.get("flags"), dict) else {}
    nr_by_npc = {}
    raw_nr = data.get("nr_por_npc")
    if isinstance(raw_nr, dict):
        for name, value in list(raw_nr.items())[:8]:
            try:
                nr_by_npc[str(name).strip()[:80]] = max(
                    -10, min(10, int(value or 0))
                )
            except (ValueError, TypeError):
                continue
    raw_cena = data.get("cena") if isinstance(data.get("cena"), dict) else {}
    cena_action = str(raw_cena.get("acao") or "continuar").strip().lower()
    if cena_action not in ("continuar", "iniciar", "trocar", "entrar", "sair", "encerrar"):
        cena_action = "continuar"
    cena_present = raw_cena.get("presentes")
    cena_present = (
        [str(name).strip()[:60] for name in cena_present if str(name).strip()][:10]
        if isinstance(cena_present, list) else []
    )
    raw_marco = data.get("marco") if isinstance(data.get("marco"), dict) else {}
    plan = {
        "nr_delta": max(-10, min(10, delta)),
        "foco": text_field("foco", 80),
        "ritmo": text_field("ritmo", 40),
        "cena": {
            "acao": cena_action,
            "titulo": str(raw_cena.get("titulo") or "").strip()[:120],
            "local": str(raw_cena.get("local") or "").strip()[:120],
            "presentes": cena_present,
            "transicao": str(raw_cena.get("transicao") or "").strip()[:240],
            "motivo": str(raw_cena.get("motivo") or "").strip()[:240],
        },
        "marco": {
            "tipo": str(raw_marco.get("tipo") or "").strip()[:40],
            "descricao": str(raw_marco.get("descricao") or "").strip()[:240],
            "impacto": str(raw_marco.get("impacto") or "").strip()[:180],
        },
        "nr_por_npc": nr_by_npc,
        "vinculo": text_field("vinculo", 220),
        "objetivo": text_field("objetivo", 240),
        "npc_intencao": text_field("npc_intencao", 300),
        "npc_emocao": text_field("npc_emocao", 200),
        "consequencia": text_field("consequencia", 240),
        "detalhes": list_field("detalhes", 3, 160),
        "evitar": list_field("evitar", 2, 160),
        "fatos": list_field("fatos", 2, 180),
        "estado": {
            "local": str(estado.get("local") or "").strip()[:120],
            "hora": str(estado.get("hora") or "").strip()[:80],
            "inventario": [
                str(item).strip()[:80]
                for item in (estado.get("inventario") or [])
                if str(item).strip()
            ][:20] if isinstance(estado.get("inventario"), list) else [],
            "objetos": {
                str(key)[:40]: str(value)[:100]
                for key, value in list(objects.items())[:20]
            },
            "npcs": {
                str(key)[:40]: str(value)[:160]
                for key, value in list(npcs.items())[:20]
            },
            "ferimentos": [
                str(item).strip()[:80]
                for item in (estado.get("ferimentos") or [])
                if str(item).strip()
            ][:10] if isinstance(estado.get("ferimentos"), list) else [],
            "flags": {
                str(key)[:40]: bool(value)
                for key, value in list(flags.items())[:20]
            },
        },
        "threads": [],
        "fatos_entidade": [],
    }
    for item in (data.get("threads") or [])[:5]:
        if not isinstance(item, dict):
            continue
        action = str(item.get("acao") or "").strip().lower()
        if action not in ("abrir", "avancar", "resolver", "abandonar"):
            continue
        try:
            thread_id = int(item.get("id") or 0)
        except (ValueError, TypeError):
            thread_id = 0
        try:
            due = int(item.get("prazo_turno") or 0)
        except (ValueError, TypeError):
            due = 0
        plan["threads"].append({
            "id": max(0, thread_id),
            "acao": action,
            "tipo": str(item.get("tipo") or "promessa").strip()[:40],
            "titulo": str(item.get("titulo") or "").strip()[:200],
            "entidades": [
                str(value).strip()[:60]
                for value in (item.get("entidades") or [])
                if str(value).strip()
            ][:6] if isinstance(item.get("entidades"), list) else [],
            "prazo_turno": max(0, due),
            "payoff": str(item.get("payoff") or "").strip()[:300],
        })
    for item in (data.get("fatos_entidade") or [])[:6]:
        if not isinstance(item, dict):
            continue
        entity = str(item.get("entidade") or "").strip()[:80]
        fact = str(item.get("fato") or "").strip()[:240]
        if not entity or not fact:
            continue
        plan["fatos_entidade"].append({
            "entidade": entity,
            "fato": fact,
            "sabem": [
                str(value).strip()[:60]
                for value in (item.get("sabem") or [])
                if str(value).strip()
            ][:8] if isinstance(item.get("sabem"), list) else [],
        })
    return plan


def plan_text(plan):
    labels = [
        ("objetivo", "OBJETIVO"),
        ("npc_intencao", "NPC_INTENCAO"),
        ("npc_emocao", "NPC_EMOCAO"),
        ("consequencia", "CONSEQUENCIA"),
        ("detalhes", "DETALHES"),
        ("evitar", "EVITAR"),
        ("fatos", "FATOS"),
        ("vinculo", "VINCULO"),
    ]
    lines = []
    foco = (plan.get("foco") or "").strip()
    if foco:
        lines.append("FOCO: " + foco[:80])
    ritmo = (plan.get("ritmo") or "").strip()
    if ritmo:
        lines.append("RITMO: " + ritmo[:40])
    cena = plan.get("cena") or {}
    if isinstance(cena, dict) and cena.get("acao"):
        cena_txt = str(cena.get("acao") or "continuar")
        if cena.get("titulo"):
            cena_txt += " | " + str(cena["titulo"])[:80]
        if cena.get("local"):
            cena_txt += " @ " + str(cena["local"])[:60]
        if cena.get("presentes"):
            cena_txt += " | presentes: " + ", ".join(
                str(name)[:30] for name in cena["presentes"][:6]
            )
        if cena.get("transicao"):
            cena_txt += " | " + str(cena["transicao"])[:120]
        lines.append("CENA: " + cena_txt)
    marco = plan.get("marco") or {}
    if isinstance(marco, dict) and (marco.get("tipo") or marco.get("descricao")):
        lines.append(
            "MARCO: "
            + (str(marco.get("tipo") or "mudanca")[:40])
            + " — " + str(marco.get("descricao") or "")[:160]
        )
    nr_by_npc = plan.get("nr_por_npc") or {}
    if isinstance(nr_by_npc, dict) and nr_by_npc:
        lines.append(
            "NR_POR_NPC: "
            + ", ".join(
                f"{str(name)[:40]}={int(value):+d}"
                for name, value in list(nr_by_npc.items())[:8]
            )
        )
    for key, label in labels:
        value = plan.get(key)
        if isinstance(value, list):
            value = "; ".join(value)
        if value:
            lines.append(f"{label}: {value}")
    estado = plan.get("estado") or {}
    state_parts = []
    for key, label in (
        ("local", "local"),
        ("hora", "hora"),
        ("inventario", "inventario"),
        ("ferimentos", "ferimentos"),
    ):
        value = estado.get(key)
        if isinstance(value, list) and value:
            state_parts.append(f"{label}=" + ", ".join(value))
        elif isinstance(value, str) and value:
            state_parts.append(f"{label}=" + value)
    objects = estado.get("objetos") or {}
    if objects:
        state_parts.append(
            "objetos=" + "; ".join(f"{k}={v}" for k, v in list(objects.items())[:8])
        )
    npcs = estado.get("npcs") or {}
    if npcs:
        state_parts.append(
            "npcs=" + "; ".join(f"{k}={v}" for k, v in list(npcs.items())[:8])
        )
    if state_parts:
        lines.append("ESTADO: " + "; ".join(state_parts))
    threads = plan.get("threads") or []
    if threads:
        lines.append(
            "THREADS: "
            + " | ".join(
                f'{item["acao"]} #{item["id"]} {item["titulo"]}'
                for item in threads[:4]
            )
        )
    facts = plan.get("fatos_entidade") or []
    if facts:
        lines.append(
            "FATOS_ENTIDADE: "
            + " | ".join(
                f'{item["entidade"]}: {item["fato"]}' for item in facts[:4]
            )
        )
    return "\n".join(lines)


def _parse_delta(raw):
    try:
        token = str(raw or "").strip().split()[0].replace("+", "")
        return max(-10, min(10, int(float(token))))
    except (ValueError, IndexError):
        return 0


def _name_key(name):
    s = re.sub(r"[^a-z0-9áéíóúâêíóúôãõàç ]", " ", str(name or "").lower())
    s = (
        s.replace("á", "a").replace("à", "a").replace("â", "a")
        .replace("ã", "a").replace("é", "e").replace("ê", "e")
        .replace("í", "i").replace("ó", "o").replace("ô", "o")
        .replace("õ", "o").replace("ú", "u").replace("ç", "c")
    )
    return re.sub(r"\s+", " ", s).strip()


def _bump_rel(rel, delta, vinculo=None, turn=0):
    try:
        nr = int(rel.get("nr", NR_START) or NR_START)
    except (ValueError, TypeError):
        nr = NR_START
    rel["nr"] = max(0, min(100, nr + delta))
    ax = dict(rel.get("axes") or AXES_START)
    for k in ("trust", "intimacy"):
        ax[k] = max(0, min(100, int(ax.get(k, 30)) + delta))
    ax["respect"] = max(0, min(100, int(ax.get("respect", 30)) + delta // 2))
    ax["tension"] = max(0, min(100, int(ax.get("tension", 20)) - delta))
    rel["axes"] = ax
    if vinculo:
        rel["vinculo"] = vinculo[:220]
    elif delta:
        rel["vinculo"] = "Mais aberta." if delta > 0 else "Mais fechada."
    sync_layers(rel, turn=turn, persona_text=vinculo)
    return rel


def update_state(
    state,
    user_msg,
    plan,
    continue_scene,
    plan_data=None,
    main_npc=None,
    cast=None,
):
    st = copy.deepcopy(state)
    ensure_cast_state(st, cast)
    ensure_layers(st, main_npc, turn=st.get("turn", 0))
    ensure_scene(st, main_npc, cast)
    main_name = ((main_npc.get("name") if main_npc else "") or "").strip()
    name_to_key = {}
    if main_name:
        name_to_key[_name_key(main_name)] = "main"
        first = main_name.split()[0]
        name_to_key.setdefault(_name_key(first), "main")
    for key, rel in st["cast"].items():
        name = str(rel.get("name") or "").strip()
        if name:
            name_to_key[_name_key(name)] = key
            first = name.split()[0]
            if first:
                name_to_key.setdefault(_name_key(first), key)

    st["turn"] = int(st.get("turn", 0)) + 1
    turn = st["turn"]
    foco = ""
    nr_by_npc = {}
    ritmo = ""
    marco = {}
    if isinstance(plan_data, dict):
        try:
            delta = int(plan_data.get("nr_delta", 0) or 0)
        except (ValueError, TypeError):
            delta = 0
        delta = max(-10, min(10, delta))
        objetivo = str(plan_data.get("objetivo") or "").strip()
        intencao = str(plan_data.get("npc_intencao") or "").strip()
        emocao = str(plan_data.get("npc_emocao") or "").strip()
        consequencia = str(plan_data.get("consequencia") or "").strip()
        vinculo = str(plan_data.get("vinculo") or "").strip()
        fatos = plan_data.get("fatos") or []
        if not isinstance(fatos, list):
            fatos = []
        estado = (
            plan_data.get("estado")
            if isinstance(plan_data.get("estado"), dict)
            else {}
        )
        foco = str(plan_data.get("foco") or "").strip()
        ritmo = str(plan_data.get("ritmo") or "").strip()
        if isinstance(plan_data.get("marco"), dict):
            marco = plan_data["marco"]
        raw_nr = plan_data.get("nr_por_npc")
        if isinstance(raw_nr, dict):
            nr_by_npc = raw_nr
    else:
        p = parse_plan(plan)
        delta = _parse_delta(p.get("NR_DELTA", ""))
        objetivo = (p.get("OBJETIVO") or "").strip()
        intencao = (p.get("NPC_INTENCAO") or "").strip()
        emocao = (p.get("NPC_EMOCAO") or "").strip()
        consequencia = (p.get("CONSEQUENCIA") or "").strip()
        vinculo = (p.get("VINCULO") or "").strip()
        fatos_raw = (p.get("FATOS") or "").strip()
        fatos = (
            []
            if not fatos_raw or fatos_raw == "-"
            else [f.strip() for f in fatos_raw.split(";") if f.strip()]
        )
        estado = {}
        ritmo = (p.get("RITMO") or "").strip()
    foco_key = "main"
    if foco:
        foco_key = name_to_key.get(_name_key(foco), "main")
    if ritmo:
        st["pacing"] = {"beat": ritmo[:40], "turn": turn}
    if objetivo:
        st["goal"] = objetivo[:240]
    if intencao:
        if foco_key == "main":
            st["moment"]["intent"] = intencao[:240]
            st["moment"]["turn"] = turn
        elif foco_key in st["cast"]:
            rel = st["cast"][foco_key]
            rel["moment"]["intent"] = intencao[:240]
            rel["moment"]["turn"] = turn
    if emocao:
        if foco_key == "main":
            st["mood"] = emocao[:200]
            st["moment"]["mood"] = emocao[:200]
            st["moment"]["turn"] = turn
        elif foco_key in st["cast"]:
            rel = st["cast"][foco_key]
            rel["mood"] = emocao[:200]
            rel["moment"]["mood"] = emocao[:200]
            rel["moment"]["turn"] = turn
    if consequencia:
        st["pending"] = consequencia[:240]
    if fatos:
        facts = list(st.get("facts") or [])
        for f in fatos:
            f = str(f).strip()
            if f and f not in facts:
                facts.append(f[:180])
        st["facts"] = facts[-12:]
    if isinstance(estado, dict):
        if str(estado.get("local") or "").strip():
            st["location"] = str(estado["local"]).strip()[:120]
        if str(estado.get("hora") or "").strip():
            st["time"] = str(estado["hora"]).strip()[:80]
        inventory = estado.get("inventario")
        if isinstance(inventory, list) and inventory:
            st["inventory"] = [
                entry
                for entry in (_inv_entry(item) for item in inventory)
                if entry is not None
            ][:20]
        objects = estado.get("objetos")
        if isinstance(objects, dict):
            current = dict(st.get("objects") or {})
            for key, value in list(objects.items())[:20]:
                current[str(key)[:40]] = str(value)[:100]
            st["objects"] = current
        npcs = estado.get("npcs")
        if isinstance(npcs, dict):
            current = dict(st.get("npcs") or {})
            for key, value in list(npcs.items())[:20]:
                name = str(key)[:40]
                text = str(value)[:160]
                text = re.sub(
                    r"^\s*NR\s*\d+(?:\s*/\s*\d+)?\s*[;:,-]?\s*", "", text, flags=re.I
                ).strip()
                text = re.sub(r"^\s*[|;:,/\\-]+\s*", "", text).strip()
                text = re.sub(r"^\s*Estado\s*:\s*", "", text, flags=re.I).strip()
                current[name] = text
                rel_key = name_to_key.get(_name_key(name))
                if rel_key and rel_key != "main" and rel_key in st["cast"]:
                    st["cast"][rel_key]["status"] = text
                    st["cast"][rel_key]["session"]["status"] = text
            st["npcs"] = current
        injuries = estado.get("ferimentos")
        if isinstance(injuries, list) and injuries:
            st["injuries"] = [
                entry
                for entry in (_injury_entry(item) for item in injuries)
                if entry is not None
            ][:10]
        flags = estado.get("flags")
        if isinstance(flags, dict):
            current = dict(st.get("flags") or {})
            for key, value in list(flags.items())[:20]:
                current[str(key)[:40]] = bool(value)
            st["flags"] = current

    if (
        foco_key in st["cast"]
        and not str(st["cast"][foco_key].get("status") or "").strip()
    ):
        st["cast"][foco_key]["status"] = "presente na cena"

    msg = (user_msg or "").strip()
    if continue_scene or len(msg) < 6:
        st["passive"] = int(st.get("passive", 0)) + 1
    else:
        st["passive"] = 0

    low = msg.lower()
    if any(w in low for w in WARM_WORDS) and delta <= 0:
        delta = 1
    if any(w in low for w in INSULT_WORDS):
        delta = min(delta, -3)

    deltas = {}
    if foco_key == "main":
        deltas["main"] = delta
    else:
        deltas[foco_key] = delta
    for name, value in nr_by_npc.items():
        key = name_to_key.get(_name_key(name))
        if not key:
            continue
        try:
            deltas[key] = max(-10, min(10, int(value or 0)))
        except (ValueError, TypeError):
            continue
    for key, d in deltas.items():
        v = vinculo if key == foco_key else None
        target = st if key == "main" else st["cast"].get(key)
        if not isinstance(target, dict):
            continue
        try:
            nr_before = int(target.get("nr", NR_START) or NR_START)
        except (ValueError, TypeError):
            nr_before = NR_START
        if key == "main":
            _bump_rel(st, d, v, turn=turn)
            st["trust"] = int(st.get("nr", NR_START)) // 10
            npc_name = main_name or "NPC principal"
        elif key in st["cast"]:
            _bump_rel(st["cast"][key], d, v, turn=turn)
            npc_name = str(st["cast"][key].get("name") or "NPC")
        else:
            continue
        try:
            nr_after = int(target.get("nr", NR_START) or NR_START)
        except (ValueError, TypeError):
            nr_after = NR_START
        desc = str(marco.get("descricao") or "").strip()
        mtype = str(marco.get("tipo") or "").strip()
        if (d and (desc or abs(d) >= 3)) or (desc and key == foco_key):
            events = list(st.get("relationship_events") or [])
            events.append({
                "turn": turn,
                "npc": npc_name[:60],
                "type": (mtype or ("aproximacao" if d > 0 else ("atrito" if d < 0 else "decisao")))[:40],
                "description": (desc or vinculo or f"NR {nr_before} -> {nr_after}")[:220],
                "impact": str(marco.get("impacto") or "").strip()[:180],
                "nr_before": nr_before,
                "nr_after": nr_after,
                "delta": d,
            })
            st["relationship_events"] = events[-16:]

    note = ("Jogador: " + msg[:100]) + (
        " | NPC: " + consequencia[:140] if consequencia else ""
    )
    if foco_key == "main":
        notes = list(st.get("npc_notes") or [])
        notes.append(note[:230])
        st["npc_notes"] = notes[-10:]
    elif foco_key in st["cast"]:
        rel = st["cast"][foco_key]
        notes = list(rel.get("notes") or [])
        notes.append(note[:230])
        rel["notes"] = notes[-6:]

    event_text = (consequencia or objetivo or msg)[:220]
    if event_text:
        events = list(st.get("events") or [])
        events.append({
            "turn": turn,
            "text": event_text,
            "entities": [foco] if foco else [],
        })
        st["events"] = events[-8:]

    if any(w in low for w in TENSION_WORDS):
        st["tension"] = min(10, int(st.get("tension", 0)) + 1)
    else:
        st["tension"] = max(0, int(st.get("tension", 0)) - 1)
    if isinstance(plan_data, dict):
        scene = apply_scene(st, plan_data, main_npc, cast)
    else:
        scene = ensure_scene(st, main_npc, cast)
    _remember_codex(st, scene, turn, main_npc, cast)
    sync_layers(st, turn=turn)
    for rel in st["cast"].values():
        if isinstance(rel, dict):
            sync_layers(rel, turn=turn)
    return st


def ledger_block(state):
    parts = []
    scene = state.get("scene") if isinstance(state.get("scene"), dict) else {}
    if scene.get("id") or scene.get("title"):
        present = ", ".join(str(n)[:40] for n in (scene.get("present") or [])[:8])
        line = f"Cena #{scene.get('id', 0)}"
        if scene.get("title"):
            line += ": " + str(scene["title"])[:100]
        if scene.get("location"):
            line += " @ " + str(scene["location"])[:80]
        if present:
            line += " | presentes: " + present
        if scene.get("ended"):
            line += " | ENCERRADA"
        parts.append(line)
        if scene.get("transition"):
            parts.append("Transicao de cena: " + str(scene["transition"])[:200])
    if state.get("location"):
        parts.append("Local: " + str(state["location"])[:120])
    if state.get("time"):
        parts.append("Hora: " + str(state["time"])[:80])
    if state.get("goal"):
        parts.append("Objetivo da cena: " + str(state["goal"])[:200])
    if state.get("mood"):
        parts.append("Humor do NPC: " + str(state["mood"])[:160])
    if state.get("pending"):
        parts.append("Consequencia pendente: " + str(state["pending"])[:200])
    if state.get("vinculo"):
        parts.append("Vinculo (arco): " + str(state["vinculo"])[:180])
    inventory = state.get("inventory") or []
    if inventory:
        parts.append(
            "Inventario: " + ", ".join(_item_text(item)[:60] for item in inventory[:10])
        )
    objects = state.get("objects") or {}
    if objects:
        parts.append(
            "Objetos: "
            + "; ".join(
                f"{key}={value}" for key, value in list(objects.items())[:8]
            )
        )
    npcs = state.get("npcs") or {}
    if npcs:
        parts.append(
            "NPCs: "
            + "; ".join(
                f"{key}={value}" for key, value in list(npcs.items())[:8]
            )
        )
    cast = state.get("cast") or {}
    if isinstance(cast, dict) and cast:
        cast_parts = []
        for rel in list(cast.values())[:8]:
            if not isinstance(rel, dict):
                continue
            name = str(rel.get("name") or "NPC")[:40]
            nr = int(rel.get("nr", NR_START) or NR_START)
            status = str(rel.get("status") or "").strip()
            moment = str((rel.get("moment") or {}).get("mood") or "").strip()
            extra = status[:70]
            if moment:
                extra = (extra + " | " if extra else "") + moment[:60]
            cast_parts.append(
                f"{name} (NR {nr})" + (f": {extra}" if extra else "")
            )
        if cast_parts:
            parts.append("Elenco: " + "; ".join(cast_parts))
    injuries = state.get("injuries") or []
    if injuries:
        parts.append(
            "Ferimentos: " + ", ".join(_injury_text(item)[:60] for item in injuries[:8])
        )
    flags = state.get("flags") or {}
    if flags:
        parts.append(
            "Flags: "
            + ", ".join(
                f"{key}={'sim' if value else 'nao'}"
                for key, value in list(flags.items())[:8]
            )
        )
    facts = state.get("facts") or []
    if facts:
        parts.append("Fatos canonicos: " + "; ".join(str(f)[:120] for f in facts[-6:]))
    events = state.get("events") or []
    if events:
        parts.append(
            "Eventos recentes: "
            + "; ".join(
                f"T{item.get('turn', '?')} {str(item.get('text') or '')[:110]}"
                for item in events[-4:]
                if isinstance(item, dict)
            )
        )
    rel_events = state.get("relationship_events") or []
    if rel_events:
        parts.append(
            "Marcos de relacao: "
            + "; ".join(
                f"T{item.get('turn', '?')} {str(item.get('npc') or '')[:24]} "
                f"[{str(item.get('type') or '')[:24]}] {str(item.get('description') or '')[:90]}"
                for item in rel_events[-3:]
                if isinstance(item, dict)
            )
        )
    pacing = state.get("pacing") if isinstance(state.get("pacing"), dict) else {}
    if pacing.get("beat"):
        parts.append("Ritmo do beat: " + str(pacing["beat"])[:40])
    parts.append(f"Turno: {state.get('turn', 0)} | Tensao: {state.get('tension', 0)}")
    return "## ESTADO FACTUAL (ledger; mantenha coerencia)\n" + "\n".join(parts)


def knowledge_block(npc, state):
    secrets = (npc.get("secrets") or "").strip() if npc else ""
    if not secrets:
        return ""
    nr = int(state.get("nr", NR_START) or NR_START)
    txt = (
        "## FRONTEIRA DE CONHECIMENTO\n"
        "- Voce so sabe o que testemunhou nesta cena e o que e de conhecimento publico.\n"
        "- SEGREDOS (o NR decide o quanto pode sair): " + secrets[:320]
    )
    if nr < 45:
        txt += "\n- Vinculo raso: NAO entregue segredos; no maximo pistas ambiguas."
    elif nr < SECRET_GATE:
        txt += "\n- Vinculo medio: pode revelar parte, com hesitacao; o nucleo continua protegido."
    else:
        txt += "\n- Vinculo alto: pode revelar o segredo, se a cena justificar; ainda ha medo."
    return txt


def event_block(state, user_msg, continue_scene):
    if int(state.get("passive", 0) or 0) >= 2 or (
        continue_scene and not (user_msg or "").strip()
    ):
        return (
            "## EVENTO DO TURNO (iniciativa)\n"
            "O jogador esta passivo. Faca o NPC (ou o ambiente) tomar uma iniciativa "
            "concreta e imediata. Nao decida nem fale pelo jogador."
        )
    if int(state.get("tension", 0) or 0) >= 4:
        return (
            "## EVENTO DO TURNO (tensao alta)\n"
            "A tensao esta alta e estagnada. Introduza uma complicacao concreta agora "
            "(algo quebra, alguem chega, uma ameaca se revela)."
        )
    return ""


CAMERA = (
    "## CAMERA (PFE)\n"
    "- Narre apenas o que o jogador percebe (visao, som, cheiro, tato, qi).\n"
    "- Nao mostre pensamentos/intencoes internas de NPCs; mostre sinais externos.\n"
    "- Nao decida, fale ou pense pelo jogador."
)


def _tier(nr):
    if nr < 25:
        return "fechada; so o superficial; NAO revele segredos nem medos profundos"
    if nr < 45:
        return "cautelosa; gostos, lembrancas leves e medos pequenos; NAO revele segredos"
    if nr < SECRET_GATE:
        return "aberta; vulnerabilidade e parte do passado; o segredo central continua protegido"
    if nr < 85:
        return "confiante; pode revelar segredos importantes e medos reais, com peso"
    return "total; pode revelar o segredo central e o medo mais profundo, sem reservas"


def relationship_block(state, npc):
    name = ((npc.get("name") if npc else "") or "NPC").upper()
    nr = int(state.get("nr", NR_START) or NR_START)
    ax = state.get("axes") or AXES_START
    txt = (
        f"## VINCULO COM {name} (NR {nr}/100)\n"
        f"Confianca {ax.get('trust', 30)} · Intimidade {ax.get('intimacy', 20)} · "
        f"Respeito {ax.get('respect', 30)} · Tensao {ax.get('tension', 20)}\n"
        f"Abertura: {_tier(nr)}."
    )
    v = (state.get("vinculo") or "").strip()
    if v:
        txt += "\nUltima mudanca: " + v[:180]
    return txt


def npc_memory_block(state, npc):
    notes = state.get("npc_notes") or []
    if not notes or not npc:
        return ""
    name = (npc.get("name") or "NPC").upper()
    return "## MEMORIA DE " + name + " (o que ele lembra)\n- " + "\n- ".join(notes[-6:])


def blocks(state, npc, user_msg, continue_scene):
    out = [ledger_block(state), relationship_block(state, npc), CAMERA]
    nm = npc_memory_block(state, npc)
    if nm:
        out.append(nm)
    kb = knowledge_block(npc, state)
    if kb:
        out.append(kb)
    ev = event_block(state, user_msg, continue_scene)
    if ev:
        out.append(ev)
    return out
