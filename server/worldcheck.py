"""worldcheck.py — validacao simbolica do plano (IVIE) e conflitos (MOSAIC).

Nao chama LLM. Confere cena/presenca/local, inventario/objetos e fatos por
entidade antes de o plano virar narracao. Devolve avisos para o auditor.
"""

import aux
import resolution

SCENE_ACTIONS = {"continuar", "iniciar", "trocar", "entrar", "sair", "encerrar"}
_BAD_OBJECT = ("destruid", "quebrad", "consumid", "perdid", "roubad")


def _norm(text):
    return aux._name_key(text)


def _facts_from_state(state):
    out = []
    for item in (state or {}).get("facts") or []:
        text = str(item or "").strip()
        if text:
            out.append(("", text))
    return out


def _strip_neg(text):
    for neg in ("nao ", "nunca ", "jamais "):
        if neg in text:
            return text.replace(neg, "", 1).strip()
    return text


def _conflict(a, b):
    a = _norm(a)
    b = _norm(b)
    if not a or not b or a == b:
        return False
    return _strip_neg(a) == b or _strip_neg(b) == a


def validate(plan_data, state, npc=None, cast=None, existing_facts=None):
    warnings = []
    names = aux._name_map(npc, cast)
    known = set(names.values())
    scene = (state or {}).get("scene") or {}
    cena = plan_data.get("cena") if isinstance(plan_data.get("cena"), dict) else {}
    action = str(cena.get("acao") or "continuar").strip().lower()
    if action not in SCENE_ACTIONS:
        action = "continuar"
        cena["acao"] = action

    present = cena.get("presentes")
    present = (
        [str(name).strip() for name in present if str(name).strip()]
        if isinstance(present, list) else []
    )
    present = [names.get(_norm(name), name) for name in present]
    present = list(dict.fromkeys(present))[:10]
    foco = str(plan_data.get("foco") or "").strip()
    foco_name = names.get(_norm(foco), foco)

    if not scene.get("id") and not str(scene.get("title") or "").strip():
        if action not in ("iniciar", "trocar"):
            warnings.append("primeira cena ainda nao iniciada; convertida para iniciar")
            action = "iniciar"
            cena["acao"] = "iniciar"
            if not str(cena.get("titulo") or "").strip():
                cena["titulo"] = "Cena inicial"
            if not str(cena.get("local") or "").strip():
                cena["local"] = str((state or {}).get("location") or "")[:120]
    if scene.get("ended") and action not in ("iniciar", "trocar"):
        warnings.append("cena anterior encerrada; use iniciar/trocar para abrir nova cena")

    if action in ("iniciar", "trocar"):
        if not str(cena.get("local") or "").strip() and not str(cena.get("titulo") or "").strip():
            warnings.append(f"cena {action} sem local/titulo; transicao fica ambigua")
        old_local = _norm(scene.get("location") or "")
        new_local = _norm(cena.get("local") or "")
        if action == "trocar" and old_local and new_local and old_local == new_local:
            if _norm(scene.get("title") or "") == _norm(cena.get("titulo") or ""):
                warnings.append("troca de cena sem mudanca de local/titulo")
    elif action == "continuar":
        old_local = _norm(scene.get("location") or "")
        new_local = _norm(cena.get("local") or "")
        if new_local and old_local and new_local != old_local:
            if not str(cena.get("motivo") or "").strip() and not str(cena.get("transicao") or "").strip():
                warnings.append("mudanca de local sem transicao/motivo; local ignorado")
                cena["local"] = ""
    elif action in ("entrar", "sair") and not present:
        warnings.append(f"acao {action} sem personagens em 'presentes'")

    if foco_name and action not in ("sair", "encerrar"):
        if present and foco_name not in present:
            present.append(foco_name)
            warnings.append(f"foco {foco_name[:60]} nao estava em presentes; incluido")
        elif not present and foco_name not in (scene.get("present") or []):
            present = list(scene.get("present") or [])
            if foco_name not in present:
                present.append(foco_name)
    cena["presentes"] = present[:10]
    plan_data["cena"] = cena

    npcs = ((plan_data.get("estado") or {}).get("npcs") or {})
    if isinstance(npcs, dict):
        for name in list(npcs)[:20]:
            canonical = names.get(_norm(name))
            if canonical and present and canonical not in present:
                if action not in ("sair", "encerrar"):
                    warnings.append(
                        f"{canonical[:60]} aparece em estado.npcs mas nao em cena.presentes"
                    )
            if not canonical and _norm(name) not in {_norm(v) for v in known}:
                warnings.append(f"NPC fora do elenco em estado.npcs: {str(name)[:60]}")

    inventory = ((plan_data.get("estado") or {}).get("inventario"))
    if isinstance(inventory, list):
        cleaned = []
        seen = set()
        for item in inventory:
            if isinstance(item, dict):
                info = resolution.item_info(item)
                if not info:
                    continue
                norm = _norm(info["name"])
                if norm in seen:
                    continue
                seen.add(norm)
                entry = {"name": info["name"]}
                if info.get("effects"):
                    entry["effects"] = info["effects"]
                cleaned.append(entry)
            else:
                text = str(item).strip()
                if not text or _norm(text) in seen:
                    continue
                seen.add(_norm(text))
                cleaned.append(text[:80])
        plan_data["estado"]["inventario"] = cleaned[:20]
        objects = (state or {}).get("objects") or {}
        old_inv = {
            _norm(resolution.item_name(item))
            for item in ((state or {}).get("inventory") or [])
        }
        for item in cleaned:
            name = resolution.item_name(item) if isinstance(item, dict) else item
            for obj_name, obj_state in objects.items():
                if _norm(obj_name) == _norm(name) and any(
                    word in _norm(obj_state) for word in _BAD_OBJECT
                ):
                    warnings.append(
                        f"inventario inclui objeto indisponivel: {str(name)[:60]}"
                    )
        for item in old_inv:
            if item and item not in seen:
                # Remocao nao e erro: pode ter sido consumida/entregue. So registra
                # quando o plano tambem afirma que o objeto continua com o jogador.
                text = _norm(plan_data.get("consequencia") or "")
                if item in text and any(w in text for w in ("ainda", "continua", "guardo")):
                    warnings.append(f"objeto removido do inventario mas narrado como presente: {item[:60]}")

    if isinstance(plan_data.get("nr_por_npc"), dict):
        for name in list(plan_data["nr_por_npc"])[:8]:
            if _norm(name) not in names and _norm(name) not in {_norm(n) for n in known}:
                warnings.append(f"nr_por_npc para NPC desconhecido: {str(name)[:60]}")

    candidates = list(_facts_from_state(state))
    for row in existing_facts or []:
        entity = str(row.get("entity") or "")
        fact = str(row.get("fact") or "")
        if fact:
            candidates.append((entity, fact))
    for item in plan_data.get("fatos_entidade") or []:
        entity = str(item.get("entidade") or "")
        fact = str(item.get("fato") or "")
        if not fact:
            continue
        for old_entity, old_fact in candidates:
            if old_entity:
                if _norm(entity) != _norm(old_entity):
                    continue
            elif entity:
                continue
            if _conflict(fact, old_fact):
                warnings.append(
                    f"fato contradiz o canonico: {entity[:50]}: {fact[:80]}"
                )
                break

    seen = set()
    out = []
    for warning in warnings:
        if warning not in seen:
            seen.add(warning)
            out.append(warning)
    return plan_data, out[:12]
