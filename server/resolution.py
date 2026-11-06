"""Resolução determinística de ações (inspirado no clockwork-dark).

A ideia central: o motor resolve a parte incerta; o Narrador apenas narra o
recibo. Isso evita que o modelo transforme uma falha em sucesso (ou vice-versa)
e deixa o resultado reproduzível por seed.

Escopo deliberadamente leve:
- classificação por intenção (palavras-chave em PT-BR);
- d20 + modificador x CD derivada do estado;
- resultado: sucesso crítico, sucesso, parcial, falha, falha crítica;
- consequências genéricas por intenção/resultado;
- sem chamadas de LLM e sem alterar NR/inventário automaticamente.
"""

from __future__ import annotations

import json
import random
import re
import unicodedata
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional


INTENT_LABELS = {
    "combate": "combate",
    "furtividade": "furtividade",
    "social": "social",
    "investigacao": "investigação",
    "movimento": "movimento",
    "resistencia": "resistência",
}
INTENT_ALIASES = {
    "combate": "combate",
    "fight": "combate",
    "furtividade": "furtividade",
    "stealth": "furtividade",
    "social": "social",
    "presenca": "social",
    "investigacao": "investigacao",
    "investigation": "investigacao",
    "movimento": "movimento",
    "movement": "movimento",
    "resistencia": "resistencia",
    "endurance": "resistencia",
}

ATTRIBUTE_KEYS = ("corpo", "mente", "presenca", "sorte")
ATTRIBUTE_LABELS = {
    "corpo": "Corpo",
    "mente": "Mente",
    "presenca": "Presença",
    "sorte": "Sorte",
}
ATTRIBUTE_DEFAULT = 10
ATTRIBUTE_MIN = 3
ATTRIBUTE_MAX = 18
ATTRIBUTE_ALIASES = {
    "corpo": "corpo",
    "body": "corpo",
    "mente": "mente",
    "mind": "mente",
    "presenca": "presenca",
    "presence": "presenca",
    "sorte": "sorte",
    "luck": "sorte",
}
INTENT_ATTRIBUTE = {
    "combate": "corpo",
    "movimento": "corpo",
    "resistencia": "corpo",
    "furtividade": "mente",
    "investigacao": "mente",
    "social": "presenca",
}
SKILL_KEYS = (
    "luta",
    "atletismo",
    "vigor",
    "furtividade",
    "investigacao",
    "persuasao",
)
SKILL_LABELS = {
    "luta": "Luta",
    "atletismo": "Atletismo",
    "vigor": "Vigor",
    "furtividade": "Furtividade",
    "investigacao": "Investigação",
    "persuasao": "Persuasão",
}
SKILL_DEFAULT = 0
SKILL_MIN = 0
SKILL_MAX = 5
SKILL_ALIASES = {
    "luta": "luta",
    "fight": "luta",
    "combat": "luta",
    "atletismo": "atletismo",
    "athletics": "atletismo",
    "vigor": "vigor",
    "resistencia": "vigor",
    "endurance": "vigor",
    "furtividade": "furtividade",
    "stealth": "furtividade",
    "investigacao": "investigacao",
    "investigation": "investigacao",
    "percepcao": "investigacao",
    "persuasao": "persuasao",
    "persuasion": "persuasao",
    "social": "persuasao",
}
INTENT_SKILL = {
    "combate": "luta",
    "movimento": "atletismo",
    "resistencia": "vigor",
    "furtividade": "furtividade",
    "investigacao": "investigacao",
    "social": "persuasao",
}
ATTRIBUTE_POINT_POOL = 40
SKILL_POINT_POOL = 10
MOD_MIN = -4
MOD_MAX = 6

# Camada situacional declarativa. Itens carregam efeito por intenção; só o
# MELHOR item de cada intenção conta (não empilha facas). Itens fora do
# catálogo não alteram o teste — a narrativa continua livre. Ferimentos
# declaram penalidade de teste e de CD; strings antigas valem 1/1.
ITEM_CATALOG = (
    {
        "id": "arma",
        "match": (
            "arma", "espada", "faca", "adaga", "punhal", "sabre", "arco",
            "besta", "pistola", "machado", "lanca", "clava",
        ),
        "effects": {"combate": 2},
    },
    {
        "id": "disfarce",
        "match": ("manto", "capa", "capuz", "disfarce", "sombra"),
        "effects": {"furtividade": 1},
    },
    {
        "id": "traje",
        "match": (
            "traje", "vestido", "uniforme", "insignia", "joia", "perfume",
        ),
        "effects": {"social": 1},
    },
    {
        "id": "kit",
        "match": ("kit", "ferramenta", "gazua", "gazuas", "lupa", "mapa", "diario"),
        "effects": {"investigacao": 1},
    },
    {
        "id": "corda",
        "match": ("corda", "gancho", "pe de cabra"),
        "effects": {"movimento": 1},
    },
    {
        "id": "tonico",
        "match": ("tonico", "remedio", "bandagem", "erva"),
        "effects": {"resistencia": 1},
    },
)
ITEM_EFFECT_MIN = -3
ITEM_EFFECT_MAX = 3
SITUATIONAL_MIN = -3
SITUATIONAL_MAX = 3
INJURY_PENALTY_MIN = 1
INJURY_PENALTY_MAX = 3
INVENTORY_LIMIT = 20
INJURIES_LIMIT = 10

# A ordem importa: combate antes de movimento, etc.
_PATTERNS = (
    (
        "combate",
        (
            "atac", "golpe", "acert", "luta", "matar", "matei", "ferir",
            "empurr", "derrub", "sacar arma", "puxar a arma", "esfaque",
            "atirar", "disparar", "cortar", "socar", "chutar", "imobiliz",
        ),
    ),
    (
        "furtividade",
        (
            "escond", "esgueir", "furtiv", "silencios", "roub", "furtar",
            "arromb", "picking", "pickpocket", "escapar sem ser visto",
            "passar desperceb", "se camuflar",
        ),
    ),
    (
        "resistencia",
        (
            "resist", "aguent", "suport", "conter", "segurar firme",
            "nao ceder", "não ceder", "manter o controle",
        ),
    ),
    (
        "social",
        (
            "persuad", "convenc", "negoci", "mentir", "ment", "engan",
            "ameac", "intimid", "seduz", "flert", "beij", "suborn",
            "implor", "pedir ajuda", "pedir informacao", "pedir informação",
            "questionar", "desafiar", "provocar", "consolar", "acalmar",
        ),
    ),
    (
        "investigacao",
        (
            "investig", "procur", "examin", "observ", "analis", "rastre",
            "ouvir", "escuta", "ler ", "inspecion", "procur pista",
            "procurar pista", "descobrir",
        ),
    ),
    (
        "movimento",
        (
            "correr", "corro", "fugir", "fujo", "salt", "escal", "entrar",
            "sair", "viaj", "caminh", "seguir", "avanc", "avançar",
            "atravess", "subir", "descer", "forcar a porta", "forçar a porta",
        ),
    ),
)

# Ações sociais explícitas que merecem teste; conversa fiada não.
_SOCIAL_RISK = (
    "persuad", "convenc", "negoci", "mentir", "ment", "engan", "ameac",
    "intimid", "seduz", "flert", "beij", "suborn", "desafiar", "provocar",
)

_CONSEQUENCES = {
    "combate": {
        "sucesso_critico": "O oponente é dominado de forma decisiva; o jogador ganha uma abertura clara.",
        "sucesso": "O oponente cede terreno ou sofre um ferimento; a ação custa pouco.",
        "parcial": "O golpe conecta, mas o oponente revida ou o jogador fica exposto.",
        "falha": "A ação é bloqueada; o oponente ganha vantagem ou o jogador se machuca.",
        "falha_critica": "O jogador se expõe gravemente: arma perdida, queda ou ferimento sério.",
    },
    "furtividade": {
        "sucesso_critico": "O jogador passa sem deixar rastro e ainda obtém uma vantagem extra.",
        "sucesso": "O jogador não é percebido e chega onde queria.",
        "parcial": "O jogador avança, mas deixa um sinal ou quase é visto.",
        "falha": "O jogador é notado, barrado ou precisa recuar.",
        "falha_critica": "O jogador é flagrado em posição ruim e a situação escala.",
    },
    "social": {
        "sucesso_critico": "O NPC se abre de verdade; a resposta vem com um gesto de confiança.",
        "sucesso": "O NPC reage favoravelmente, mas sem entregar tudo.",
        "parcial": "O NPC hesita: dá uma migalha e cobra um preço/gesto.",
        "falha": "O NPC fecha a guarda, desconfia ou muda de assunto.",
        "falha_critica": "O NPC se ofende ou usa a tentativa contra o jogador.",
    },
    "investigacao": {
        "sucesso_critico": "O jogador encontra a pista central e uma conexão que ninguém viu.",
        "sucesso": "O jogador encontra uma pista concreta e utilizável.",
        "parcial": "O jogador encontra um indício ambíguo; falta confirmar.",
        "falha": "O jogador não encontra nada útil e perde tempo.",
        "falha_critica": "O jogador mexe no lugar errado e deixa vestígio ou atrai atenção.",
    },
    "movimento": {
        "sucesso_critico": "O jogador chega antes do esperado e em vantagem.",
        "sucesso": "O jogador chega ao destino sem custo relevante.",
        "parcial": "O jogador chega, mas cansado, atrasado ou com algo perdido.",
        "falha": "O caminho é bloqueado; o jogador precisa de outra rota.",
        "falha_critica": "O jogador cai, se perde ou é encurralado.",
    },
    "resistencia": {
        "sucesso_critico": "O jogador resiste e ainda vira a pressão contra o agressor.",
        "sucesso": "O jogador aguenta e mantém o controle.",
        "parcial": "O jogador resiste, mas cede terreno ou sente o custo.",
        "falha": "O jogador cede à pressão ou perde a posição.",
        "falha_critica": "O jogador desaba/cede de forma perigosa.",
    },
}


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _clamp_int(value, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _clean_effects(raw) -> Dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, int] = {}
    for key, value in raw.items():
        intent = INTENT_ALIASES.get(_norm(key), "")
        if not intent:
            continue
        effect = _clamp_int(value, 0, ITEM_EFFECT_MIN, ITEM_EFFECT_MAX)
        if effect:
            out[intent] = effect
    return out


def _catalog_entry(name: str):
    key = _norm(name)
    if not key:
        return None
    for entry in ITEM_CATALOG:
        for alias in entry["match"]:
            alias = _norm(alias)
            if re.search(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])", key):
                return entry
    return None


def item_info(item) -> Dict[str, Any]:
    """Normaliza um item (string legada ou dict declarativo) para o motor."""
    if isinstance(item, dict):
        name = str(
            item.get("name") or item.get("nome") or item.get("id") or ""
        ).strip()
        if not name:
            return {}
        effects = _clean_effects(item.get("effects") or item.get("efeitos"))
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            entry = _catalog_entry(name)
            item_id = entry["id"] if entry else "custom"
            if not effects and entry:
                effects = dict(entry["effects"])
        return {"id": item_id[:40], "name": name[:80], "effects": effects}
    name = str(item or "").strip()
    if not name:
        return {}
    entry = _catalog_entry(name)
    if entry:
        return {
            "id": entry["id"],
            "name": name[:80],
            "effects": dict(entry["effects"]),
        }
    return {"id": "item", "name": name[:80], "effects": {}}


def item_name(item) -> str:
    return str(item_info(item).get("name") or "")


def normalize_inventory(raw) -> List[Dict[str, Any]]:
    if not isinstance(raw, (list, tuple)):
        return []
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in raw:
        info = item_info(item)
        if not info:
            continue
        key = _norm(info["name"])
        if key in seen:
            continue
        seen.add(key)
        out.append(info)
        if len(out) >= INVENTORY_LIMIT:
            break
    return out


def effect_label(effects) -> str:
    return ", ".join(
        f"{INTENT_LABELS.get(key, key)} {value:+d}"
        for key, value in _clean_effects(effects).items()
    )


def inventory_detail(raw) -> List[Dict[str, Any]]:
    return [
        {**info, "effect_label": effect_label(info.get("effects"))}
        for info in normalize_inventory(raw)
    ]


def injury_info(raw) -> Dict[str, Any]:
    """Normaliza um ferimento (string legada ou dict com penalty/dc)."""
    if isinstance(raw, dict):
        name = str(
            raw.get("name") or raw.get("nome") or raw.get("id") or ""
        ).strip()
        if not name:
            return {}
        penalty = _clamp_int(
            raw.get("penalty", raw.get("penalidade", 1)),
            1,
            INJURY_PENALTY_MIN,
            INJURY_PENALTY_MAX,
        )
        dc = _clamp_int(raw.get("dc", raw.get("cd", penalty)), penalty, 0, 3)
        return {
            "id": str(raw.get("id") or "injury")[:40],
            "name": name[:80],
            "penalty": penalty,
            "dc": dc,
        }
    name = str(raw or "").strip()
    if not name:
        return {}
    return {
        "id": "injury",
        "name": name[:80],
        "penalty": 1,
        "dc": 1,
    }


def normalize_injuries(raw) -> List[Dict[str, Any]]:
    if not isinstance(raw, (list, tuple)):
        return []
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in raw:
        info = injury_info(item)
        if not info:
            continue
        key = _norm(info["name"])
        if key in seen:
            continue
        seen.add(key)
        out.append(info)
        if len(out) >= INJURIES_LIMIT:
            break
    return out


def injuries_detail(raw) -> List[Dict[str, Any]]:
    return normalize_injuries(raw)


def difficulty_label(dc: int) -> str:
    if dc <= 10:
        return "fácil"
    if dc <= 12:
        return "média"
    if dc <= 15:
        return "difícil"
    return "muito difícil"


def classify(action: str) -> str:
    text = _norm(action)
    # "procuro uma armadilha escondida" é investigação, não furtividade.
    if re.search(
        r"(?<![a-z])(procur|investig|examin|analis|observ|rastre|inspecion|leio|escuto)",
        text,
    ):
        return "investigacao"
    for intent, patterns in _PATTERNS:
        for pattern in patterns:
            if pattern.endswith(" "):
                if pattern in text:
                    return intent
            elif re.search(r"(?<![a-z])" + re.escape(pattern), text):
                return intent
    return ""


def _should_roll(action: str, intent: str, state: Dict[str, Any]) -> bool:
    if not intent:
        return False
    if intent in ("combate", "furtividade", "resistencia", "investigacao", "movimento"):
        # Movimento trivial ("entro na sala") não precisa de teste; só quando
        # há risco explícito ou tensão alta.
        if intent == "movimento":
            text = _norm(action)
            risky = any(
                word in text
                for word in ("correr", "corro", "fugir", "fujo", "salt", "escal",
                             "arromb", "forcar", "forçar", "atravess", "subir", "descer")
            )
            return risky or int(state.get("tension", 0) or 0) >= 4
        if intent == "investigacao":
            text = _norm(action)
            risky = any(
                word in text
                for word in ("armadilha", "escondid", "perigos", "rápido", "rapido",
                             "no escuro", "sob pressao", "sob pressão")
            )
            return risky or int(state.get("tension", 0) or 0) >= 3
        return True
    if intent == "social":
        text = _norm(action)
        return any(word in text for word in _SOCIAL_RISK)
    return False


def _dc(intent: str, state: Dict[str, Any]) -> int:
    base = {
        "combate": 13,
        "furtividade": 13,
        "social": 12,
        "investigacao": 11,
        "movimento": 12,
        "resistencia": 13,
    }.get(intent, 12)
    injuries = normalize_injuries(state.get("injuries"))
    if injuries:
        base += min(3, sum(int(item.get("dc", 1)) for item in injuries))
    if int(state.get("tension", 0) or 0) >= 4:
        base += 1
    if intent == "social":
        try:
            nr = int(state.get("nr", 20) or 20)
        except (TypeError, ValueError):
            nr = 20
        base -= max(-2, min(3, (nr - 50) // 15))
    return max(8, min(20, base))


def normalize_attributes(raw) -> Dict[str, int]:
    """Normaliza a ficha para as 4 chaves, com clamp em 3..18.

    Dict vazio/ausente significa "sem atributos" (compatível com fichas
    antigas e NPCs); um dict parcial completa as chaves faltantes com 10.
    """
    if not isinstance(raw, dict) or not raw:
        return {}
    by_name: Dict[str, Any] = {}
    for raw_key, raw_value in raw.items():
        key = ATTRIBUTE_ALIASES.get(_norm(raw_key), "")
        if key and key not in by_name:
            by_name[key] = raw_value
    out: Dict[str, int] = {}
    for key in ATTRIBUTE_KEYS:
        value = by_name.get(key, ATTRIBUTE_DEFAULT)
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = ATTRIBUTE_DEFAULT
        out[key] = max(ATTRIBUTE_MIN, min(ATTRIBUTE_MAX, value))
    return out


def normalize_skills(raw) -> Dict[str, int]:
    """Normaliza perícias para as 6 chaves, com clamp em 0..5.

    Dict vazio/ausente significa "sem treino" (compatível com fichas antigas e
    NPCs); um dict parcial completa as chaves faltantes com 0.
    """
    if not isinstance(raw, dict) or not raw:
        return {}
    by_name: Dict[str, Any] = {}
    for raw_key, raw_value in raw.items():
        key = SKILL_ALIASES.get(_norm(raw_key), "")
        if key and key not in by_name:
            by_name[key] = raw_value
    out: Dict[str, int] = {}
    for key in SKILL_KEYS:
        value = by_name.get(key, SKILL_DEFAULT)
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = SKILL_DEFAULT
        out[key] = max(SKILL_MIN, min(SKILL_MAX, value))
    return out


def _spend_down(values: Dict[str, int], pool: int, floor: int) -> Dict[str, int]:
    total = sum(values.values())
    while total > pool:
        key = max(values, key=lambda k: values[k])
        if values[key] <= floor:
            break
        values[key] -= 1
        total -= 1
    return values


def balance_attributes(raw, pool: int = ATTRIBUTE_POINT_POOL) -> Dict[str, int]:
    """Normaliza e reduz os maiores atributos até caber no orçamento."""
    attrs = normalize_attributes(raw)
    if not attrs:
        return {}
    return _spend_down(attrs, pool, ATTRIBUTE_MIN)


def balance_skills(raw, pool: int = SKILL_POINT_POOL) -> Dict[str, int]:
    """Normaliza e reduz as maiores perícias até caber no orçamento."""
    skills = normalize_skills(raw)
    if not skills:
        return {}
    return _spend_down(skills, pool, SKILL_MIN)


def attribute_modifiers(attributes, intent: str, skills=None) -> Dict[str, Any]:
    """Cálculo público do modificador de atributo/perícia/sorte da ficha."""
    attrs = normalize_attributes(attributes)
    if not attrs:
        return {
            "attribute": "",
            "attribute_value": 0,
            "attribute_mod": 0,
            "skill": "",
            "skill_level": 0,
            "skill_mod": 0,
            "luck_mod": 0,
        }
    attribute = INTENT_ATTRIBUTE.get(intent, "")
    value = attrs.get(attribute, ATTRIBUTE_DEFAULT) if attribute else 0
    attr_mod = (value - 10) // 2 if attribute else 0
    skill = INTENT_SKILL.get(intent, "")
    skill_value = normalize_skills(skills)
    skill_level = skill_value.get(skill, SKILL_DEFAULT) if skill else 0
    luck_mod = (attrs.get("sorte", ATTRIBUTE_DEFAULT) - 10) // 4
    return {
        "attribute": attribute,
        "attribute_value": value,
        "attribute_mod": attr_mod,
        "skill": skill,
        "skill_level": skill_level,
        "skill_mod": skill_level,
        "luck_mod": luck_mod,
    }


def modifier_table(
    attributes, skills=None, state: Optional[Dict[str, Any]] = None
) -> Dict[str, Dict[str, Any]]:
    """Modificadores por intenção (para a ficha).

    Com ``state``, a tabela também expõe o item que ajuda, a penalidade dos
    ferimentos e o total já com a camada situacional. ``modifier`` continua
    sendo o valor base (atributo + perícia + sorte) para compatibilidade.
    """
    attrs = normalize_attributes(attributes)
    if not attrs:
        return {}
    inventory = normalize_inventory((state or {}).get("inventory"))
    injury_penalty = -min(
        3,
        sum(
            int(item.get("penalty", 1))
            for item in normalize_injuries((state or {}).get("injuries"))
        ),
    )
    table: Dict[str, Dict[str, Any]] = {}
    for intent in INTENT_LABELS:
        info = attribute_modifiers(attrs, intent, skills)
        raw = info["attribute_mod"] + info["skill_mod"] + info["luck_mod"]
        item_bonus = 0
        item_source = ""
        for item in inventory:
            value = int((item.get("effects") or {}).get(intent, 0))
            if value > item_bonus:
                item_bonus = value
                item_source = str(item.get("name") or "")
        situational = max(
            SITUATIONAL_MIN, min(SITUATIONAL_MAX, item_bonus + injury_penalty)
        )
        row = {
            "attribute": info["attribute"],
            "value": info["attribute_value"],
            "skill": info["skill"],
            "skill_level": info["skill_level"],
            "modifier": max(MOD_MIN, min(MOD_MAX, raw)),
        }
        if state is not None:
            row.update(
                {
                    "item": item_source,
                    "item_bonus": item_bonus,
                    "injury_penalty": injury_penalty,
                    "situational": situational,
                    "total": max(MOD_MIN, min(MOD_MAX, raw + situational)),
                }
            )
        table[intent] = row
    return table


def _attributes(state: Dict[str, Any]) -> Dict[str, int]:
    raw = state.get("attributes")
    if not raw and isinstance(state.get("sheet"), dict):
        raw = state["sheet"].get("attributes")
    return normalize_attributes(raw)


def _skills(state: Dict[str, Any]) -> Dict[str, int]:
    raw = state.get("skills")
    if not raw and isinstance(state.get("sheet"), dict):
        raw = state["sheet"].get("skills")
    return normalize_skills(raw)


def _action_nuance(
    action: str, intent: str, has_item_bonus: bool
) -> tuple[int, str]:
    """Bônus de posicionamento narrado (não é item nem ficha)."""
    text = _norm(action)
    if (
        intent == "combate"
        and not has_item_bonus
        and ("soco" in text or "punho" in text)
    ):
        return 1, "golpe desarmado"
    if intent == "furtividade" and any(
        word in text for word in ("sombra", "silenci", "cuidado")
    ):
        return 1, "postura cautelosa"
    if intent == "social" and any(
        word in text for word in ("calma", "sorriso", "gentil", "firme")
    ):
        return 1, "tom de voz"
    return 0, ""


def situational_breakdown(
    action: str, intent: str, state: Dict[str, Any]
) -> tuple[int, List[Dict[str, Any]]]:
    """Soma a camada situacional e devolve as fontes (item/ferimento/postura)."""
    parts: List[Dict[str, Any]] = []
    best: Optional[Dict[str, Any]] = None
    for info in normalize_inventory(state.get("inventory")):
        value = int((info.get("effects") or {}).get(intent, 0))
        if value and (best is None or value > int(best["value"])):
            best = {"source": info["name"], "kind": "item", "value": value}
    if best is not None:
        parts.append(best)
    nuance, label = _action_nuance(action, intent, best is not None)
    if nuance:
        parts.append({"source": label, "kind": "action", "value": nuance})
    for injury in normalize_injuries(state.get("injuries")):
        parts.append(
            {
                "source": injury["name"],
                "kind": "injury",
                "value": -int(injury["penalty"]),
            }
        )
    raw = sum(int(part["value"]) for part in parts)
    total = max(SITUATIONAL_MIN, min(SITUATIONAL_MAX, raw))
    if total != raw:
        parts.append({"source": "limite", "kind": "cap", "value": total - raw})
    return total, parts


def _situational_modifier(action: str, intent: str, state: Dict[str, Any]) -> int:
    total, _ = situational_breakdown(action, intent, state)
    return total


def _modifier_breakdown(action: str, intent: str, state: Dict[str, Any]) -> Dict[str, Any]:
    info = attribute_modifiers(_attributes(state), intent, _skills(state))
    situational, parts = situational_breakdown(action, intent, state)
    total = (
        info["attribute_mod"]
        + info["skill_mod"]
        + info["luck_mod"]
        + situational
    )
    return {
        **info,
        "situational": situational,
        "situational_parts": parts,
        "modifier": max(MOD_MIN, min(MOD_MAX, total)),
    }


def _modifier(action: str, intent: str, state: Dict[str, Any]) -> int:
    return int(_modifier_breakdown(action, intent, state)["modifier"])


@dataclass
class Receipt:
    intent: str
    label: str
    dc: int
    modifier: int
    roll: int
    total: int
    outcome: str
    outcome_label: str
    consequences: str
    action: str
    seed: str
    attribute: str = ""
    attribute_value: int = 0
    attribute_mod: int = 0
    skill: str = ""
    skill_level: int = 0
    skill_mod: int = 0
    luck_mod: int = 0
    situational: int = 0
    situational_parts: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Receipt":
        """Reconstrói um recibo persistido (ex.: regen após rewind)."""
        fields = cls.__dataclass_fields__
        values = {key: data[key] for key in fields if key in data}
        parts = values.get("situational_parts")
        values["situational_parts"] = list(parts) if isinstance(parts, list) else []
        return cls(**values)

    def as_block(self) -> str:
        if self.attribute:
            formula = (
                f"d20={self.roll} + {ATTRIBUTE_LABELS.get(self.attribute, self.attribute)} "
                f"{self.attribute_value} ({self.attribute_mod:+d}) "
                f"+ {SKILL_LABELS.get(self.skill, self.skill or 'pericia')} "
                f"{self.skill_level} ({self.skill_mod:+d}) "
                f"+ sorte ({self.luck_mod:+d}) "
                f"+ situacao ({self.situational:+d}) "
                f"= {self.total}"
            )
        else:
            formula = f"d20={self.roll} + mod={self.modifier} = {self.total}"
        lines = [
            "## RESULTADO JA RESOLVIDO (motor; NAO reescreva o resultado)",
            f"Acao do jogador: {self.action[:300]}",
            f"Teste de {self.label}: CD {self.dc} | {formula} -> {self.outcome_label}",
        ]
        if self.situational_parts:
            sources = ", ".join(
                f"{part['source']} {int(part['value']):+d}"
                for part in self.situational_parts
            )
            lines.append(f"Camada situacional: {sources}")
        lines.extend(
            [
                f"Consequencia autorizada: {self.consequences}",
                "Regras: mostre este resultado na prosa; NAO transforme falha em sucesso "
                "nem sucesso em falha; nao invente um resultado diferente.",
            ]
        )
        return "\n".join(lines)


def would_roll(action: str, state: Dict[str, Any]) -> bool:
    """Diz se a acao provavelmente pede teste, sem rolar o dado."""
    intent = classify(action)
    return _should_roll(action, intent, state)


def preview(action: str, state: Dict[str, Any]) -> Dict[str, Any]:
    """Metadados de risco para exibir numa escolha, sem rolar o dado."""
    intent = classify(action)
    risky = _should_roll(action, intent, state)
    dc = _dc(intent, state) if risky else 0
    data = {
        "intent": intent or "",
        "label": INTENT_LABELS.get(intent, "livre") if intent else "livre",
        "risk": bool(risky),
        "dc": dc,
        "difficulty": difficulty_label(dc) if risky else "",
    }
    if risky:
        info = _modifier_breakdown(action, intent, state)
        data["modifier"] = info["modifier"]
        data["attribute"] = info["attribute"]
        data["skill"] = info["skill"]
        data["skill_level"] = info["skill_level"]
        data["skill_mod"] = info["skill_mod"]
        data["situational"] = info["situational"]
        data["situational_parts"] = info["situational_parts"]
    return data


def resolve(
    action: str,
    state: Dict[str, Any],
    seed: Optional[str] = None,
) -> Optional[Receipt]:
    """Resolve uma ação incerta. Retorna None se a ação não pede teste."""
    intent = classify(action)
    if not _should_roll(action, intent, state):
        return None
    seed = str(seed or state.get("seed") or "luminus")
    try:
        seq = int(state.get("roll_seq", 0) or 0) + 1
        turn = int(state.get("turn", 0) or 0)
    except (TypeError, ValueError):
        seq, turn = 1, 0
    dc = _dc(intent, state)
    info = _modifier_breakdown(action, intent, state)
    mod = int(info["modifier"])
    rng = random.Random(f"{seed}:{turn}:{seq}:{intent}")
    roll = rng.randint(1, 20)
    total = roll + mod
    if roll == 20:
        outcome = "sucesso_critico"
    elif roll == 1:
        outcome = "falha_critica"
    elif total >= dc + 5:
        outcome = "sucesso_critico"
    elif total >= dc:
        outcome = "sucesso"
    elif total >= dc - 2:
        outcome = "parcial"
    else:
        outcome = "falha"
    labels = {
        "sucesso_critico": "Sucesso crítico",
        "sucesso": "Sucesso",
        "parcial": "Sucesso parcial",
        "falha": "Falha",
        "falha_critica": "Falha crítica",
    }
    consequences = _CONSEQUENCES.get(intent, {}).get(
        outcome, "Mostre o resultado sem suavizar nem exagerar."
    )
    return Receipt(
        intent=intent,
        label=INTENT_LABELS.get(intent, intent),
        dc=dc,
        modifier=mod,
        roll=roll,
        total=total,
        outcome=outcome,
        outcome_label=labels[outcome],
        consequences=consequences,
        action=(action or "").strip(),
        seed=f"{seed}:{turn}:{seq}:{intent}",
        attribute=str(info.get("attribute") or ""),
        attribute_value=int(info.get("attribute_value") or 0),
        attribute_mod=int(info.get("attribute_mod") or 0),
        skill=str(info.get("skill") or ""),
        skill_level=int(info.get("skill_level") or 0),
        skill_mod=int(info.get("skill_mod") or 0),
        luck_mod=int(info.get("luck_mod") or 0),
        situational=int(info.get("situational") or 0),
        situational_parts=list(info.get("situational_parts") or []),
    )


def apply(state: Dict[str, Any], receipt: Receipt) -> Dict[str, Any]:
    """Persiste o recibo no estado, sem mexer em NR/inventário."""
    data = receipt.as_dict()
    state["resolution"] = data
    history = state.get("resolution_history")
    if not isinstance(history, list):
        history = []
    history.append(data)
    state["resolution_history"] = history[-8:]
    try:
        state["roll_seq"] = int(state.get("roll_seq", 0) or 0) + 1
    except (TypeError, ValueError):
        state["roll_seq"] = 1
    events = state.get("events")
    if not isinstance(events, list):
        events = []
    events.append(
        {
            "turn": int(state.get("turn", 0) or 0),
            "text": f"Teste de {receipt.label}: {receipt.outcome_label} (d20={receipt.roll}, CD {receipt.dc})",
        }
    )
    state["events"] = events[-8:]
    return state


_SUCESSO = (
    "consegue", "acerta", "vence", "passa", "funciona", "obtem", "obtém",
    "sucede", "derrota", "convence", "encontra", "escapa", "domina",
)
_FALHA = (
    "falha", "erra", "nao consegue", "não consegue", "e detido", "é detido",
    "recua", "perde", "deixa escapar", "nao encontra", "não encontra",
    "e bloqueado", "é bloqueado", "cede", "desaba",
)


def check_contradiction(text: str, receipt: Optional[Dict[str, Any]]) -> str:
    """Heurística conservadora: só sinaliza contradição óbvia (para log)."""
    if not receipt:
        return ""
    outcome = str(receipt.get("outcome") or "")
    low = _norm(text)
    has_success = any(word in low for word in _SUCESSO)
    has_failure = any(word in low for word in _FALHA)
    if outcome in ("falha", "falha_critica") and has_success and not has_failure:
        return f"prosa parece contradizer {outcome} (marcadores de sucesso)"
    if outcome in ("sucesso", "sucesso_critico") and has_failure and not has_success:
        return f"prosa parece contradizer {outcome} (marcadores de falha)"
    return ""


def seed_for_session(sid: int, created_at: float) -> str:
    return f"luminus-{sid}-{int(created_at or 0)}"
