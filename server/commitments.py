"""commitments.py — compromissos narrativos (threads) com validacao e auditoria.

Inspirado em NCP-Bench (preservacao de compromissos) e NstAgent (estado
narrativo com eventos passados e requisitos futuros). O banco `threads` e a
fonte de verdade; este modulo valida o plano, audita o cumprimento e expoe um
bloco compacto para os prompts.
"""

import json
import re
import time

VALID_ACTIONS = {"abrir", "avancar", "resolver", "abandonar"}
_KIND_DEFAULT = "promessa"
_PROMISE_RE = re.compile(
    r"\b(prometo|prometi|prometido|juro|combinado|combinamos|"
    r"vou (?:voltar|trazer|buscar|fazer|ajudar|pagar|devolver)|"
    r"te devo|devo (?:voltar|trazer|pagar|devolver)|"
    r"amanha|depois eu)\b",
    re.I,
)
_STOP = {
    "para", "como", "voce", "você", "mais", "isso", "aqui", "onde",
    "porque", "sobre", "entre", "quando", "depois", "antes", "nada",
    "alguem", "algum", "muito", "pouco", "esta", "este", "esse", "essa",
    "dele", "dela", "deles", "delas", "numa", "num", "pelo", "pela",
}


def _norm(text):
    s = str(text or "").lower()
    s = (
        s.replace("á", "a").replace("à", "a").replace("â", "a")
        .replace("ã", "a").replace("é", "e").replace("ê", "e")
        .replace("í", "i").replace("ó", "o").replace("ô", "o")
        .replace("õ", "o").replace("ú", "u").replace("ç", "c")
    )
    return re.sub(r"\s+", " ", s).strip()


def _keywords(text, min_len=4, limit=10):
    words = re.findall(r"[a-z0-9]{%d,}" % min_len, _norm(text))
    out = []
    for word in words:
        if word in _STOP or word in out:
            continue
        out.append(word)
        if len(out) >= limit:
            break
    return out


def _json_list(raw):
    try:
        value = json.loads(raw or "[]")
    except ValueError:
        return []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _row_to_commitment(row):
    data = dict(row)
    data["entities"] = _json_list(data.get("entities"))
    data["due_turn"] = int(data.get("due_turn") or 0)
    data["last_turn"] = int(data.get("last_turn") or 0)
    return data


def open_commitments(conn, sid, limit=12):
    rows = conn.execute(
        "SELECT id, kind, title, status, setup, payoff, due_turn, entities, "
        "last_turn, created_at, updated_at FROM threads "
        "WHERE session_id=? AND status='aberta' "
        "ORDER BY CASE WHEN due_turn>0 THEN due_turn ELSE 999999 END, id "
        "LIMIT ?",
        (sid, max(1, int(limit))),
    ).fetchall()
    return [_row_to_commitment(row) for row in rows]


def all_commitments(conn, sid, limit=100):
    rows = conn.execute(
        "SELECT id, kind, title, status, setup, payoff, due_turn, entities, "
        "last_turn, created_at, updated_at FROM threads "
        "WHERE session_id=? "
        "ORDER BY CASE status WHEN 'aberta' THEN 0 WHEN 'resolvida' THEN 1 "
        "ELSE 2 END, CASE WHEN due_turn>0 THEN due_turn ELSE 999999 END, id DESC "
        "LIMIT ?",
        (sid, max(1, int(limit))),
    ).fetchall()
    return [_row_to_commitment(row) for row in rows]


def _by_id(conn, sid):
    rows = conn.execute(
        "SELECT id, kind, title, status, setup, payoff, due_turn, entities, "
        "last_turn, created_at, updated_at FROM threads WHERE session_id=?",
        (sid,),
    ).fetchall()
    return {int(row["id"]): _row_to_commitment(row) for row in rows}


def block(conn, sid, turn=0, limit=8):
    items = open_commitments(conn, sid, limit=limit)
    if not items:
        return ""
    turn = int(turn or 0)
    lines = []
    for item in items:
        due = item["due_turn"]
        marker = ""
        if due:
            marker = f" (prazo: turno {due}"
            marker += ", VENCIDO" if turn and due <= turn else ""
            marker += ")"
        entities = item["entities"]
        ent = ""
        if entities:
            ent = " (entidades: " + ", ".join(entities[:4]) + ")"
        lines.append(
            f"- #{item['id']} [{item['kind']}] {item['title']}{marker}{ent}"
        )
    return (
        "## COMPROMISSOS ABERTOS (promessas, dividas, misterios, objetivos)\n"
        + "\n".join(lines)
        + "\n- Se um compromisso estiver VENCIDO, avance ou resolva agora; "
          "nao o ignore nem o contradiga."
    )


def infer_from_user(plan_data, user_msg, foco="", entities=None):
    """Rede de seguranca: se o jogador prometeu algo e o plano nao abriu thread,
    registra a promessa para nao perde-la."""
    if plan_data.get("threads"):
        return plan_data
    text = str(user_msg or "").strip()
    if not text or not _PROMISE_RE.search(_norm(text)):
        return plan_data
    title = re.sub(r"\s+", " ", text).strip()[:150]
    entity_list = []
    for value in [foco, *(entities or [])]:
        value = str(value or "").strip()
        if value and value not in entity_list:
            entity_list.append(value)
    plan_data["threads"] = [{
        "id": 0,
        "acao": "abrir",
        "tipo": "promessa",
        "titulo": "Promessa: " + title,
        "entidades": entity_list[:4],
        "prazo_turno": 0,
        "payoff": "",
    }]
    return plan_data


def validate_plan(conn, sid, plan_data, state):
    """Normaliza e valida o plano antes de aplicar efeitos colaterais.

    Retorna (plan_data, warnings). Nao grava nada no banco.
    """
    warnings = []
    turn = int((state or {}).get("turn", 0) or 0) + 1
    current = _by_id(conn, sid)
    open_items = {
        tid: item for tid, item in current.items()
        if item["status"] == "aberta"
    }
    by_title = {_norm(item["title"]): item for item in open_items.values()}

    seen_threads = set()
    cleaned_threads = []
    for raw in plan_data.get("threads") or []:
        if not isinstance(raw, dict):
            continue
        action = str(raw.get("acao") or "").strip().lower()
        if action not in VALID_ACTIONS:
            warnings.append(f"acao de thread invalida: {action or '(vazia)'}")
            continue
        title = str(raw.get("titulo") or "").strip()[:200]
        if not title:
            warnings.append("thread sem titulo descartada")
            continue

        item = {
            "id": 0,
            "acao": action,
            "tipo": str(raw.get("tipo") or _KIND_DEFAULT).strip()[:40],
            "titulo": title,
            "entidades": [
                str(value).strip()[:60]
                for value in (raw.get("entidades") or [])
                if str(value).strip()
            ][:6] if isinstance(raw.get("entidades"), list) else [],
            "prazo_turno": 0,
            "payoff": str(raw.get("payoff") or "").strip()[:300],
        }
        try:
            item["prazo_turno"] = max(0, int(raw.get("prazo_turno") or 0))
        except (ValueError, TypeError):
            item["prazo_turno"] = 0

        if action == "abrir":
            existing = by_title.get(_norm(title))
            if existing:
                item["acao"] = "avancar"
                item["id"] = existing["id"]
                warnings.append(
                    f"thread duplicada virou avancar: {existing['title'][:80]}"
                )
        else:
            try:
                tid = int(raw.get("id") or 0)
            except (ValueError, TypeError):
                tid = 0
            if tid not in open_items:
                match = by_title.get(_norm(title))
                if match:
                    item["id"] = match["id"]
                    warnings.append(
                        f"thread sem id valido; casada por titulo: "
                        f"{match['title'][:80]}"
                    )
                else:
                    warnings.append(
                        f"thread #{tid} nao esta aberta; acao {action} descartada"
                    )
                    continue
            else:
                item["id"] = tid

        key = (item["id"], item["acao"], _norm(item["titulo"]))
        if key in seen_threads:
            continue
        seen_threads.add(key)

        if item["prazo_turno"] and item["prazo_turno"] < turn:
            warnings.append(
                f"prazo de '{item['titulo'][:60]}' estava no passado; ajustado"
            )
            item["prazo_turno"] = turn

        if item["acao"] == "resolver" and not item["payoff"]:
            fallback = str(plan_data.get("consequencia") or "").strip()[:300]
            if fallback:
                item["payoff"] = fallback
            else:
                warnings.append(
                    f"thread #{item['id']} resolvida sem payoff explicito"
                )
        cleaned_threads.append(item)
    plan_data["threads"] = cleaned_threads

    seen_facts = {_norm(f) for f in (state or {}).get("facts") or []}
    facts = []
    for fact in plan_data.get("fatos") or []:
        text = str(fact or "").strip()
        key = _norm(text)
        if not text or key in seen_facts:
            continue
        seen_facts.add(key)
        facts.append(text[:180])
    plan_data["fatos"] = facts[:4]

    seen_entity = set()
    facts_entity = []
    for raw in plan_data.get("fatos_entidade") or []:
        if not isinstance(raw, dict):
            continue
        entity = str(raw.get("entidade") or "").strip()[:80]
        fact = str(raw.get("fato") or "").strip()[:240]
        if not entity or not fact:
            continue
        key = (_norm(entity), _norm(fact))
        if key in seen_entity:
            continue
        seen_entity.add(key)
        facts_entity.append({
            "entidade": entity,
            "fato": fact,
            "sabem": [
                str(value).strip()[:60]
                for value in (raw.get("sabem") or [])
                if str(value).strip()
            ][:8] if isinstance(raw.get("sabem"), list) else [],
        })
    plan_data["fatos_entidade"] = facts_entity[:6]

    foco = str(plan_data.get("foco") or "").strip()
    if foco:
        estado = plan_data.get("estado")
        if not isinstance(estado, dict):
            estado = {}
            plan_data["estado"] = estado
        npcs = estado.get("npcs")
        if not isinstance(npcs, dict):
            npcs = {}
            estado["npcs"] = npcs
        if not any(_norm(name) == _norm(foco) for name in npcs):
            npcs[foco] = "presente na cena"
    return plan_data, warnings


def _record(conn, sid, turn, thread_id, check_type, ok, detail):
    conn.execute(
        "INSERT INTO commitment_checks "
        "(session_id, turn, thread_id, check_type, ok, detail, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            sid,
            int(turn or 0),
            int(thread_id) if thread_id else None,
            str(check_type or "")[:40],
            1 if ok else 0,
            str(detail or "")[:400],
            time.time(),
        ),
    )


def audit_plan(conn, sid, plan_data, state, warnings=None):
    """Audita o plano contra os compromissos abertos antes de aplica-lo."""
    turn = int((state or {}).get("turn", 0) or 0) + 1
    open_items = open_commitments(conn, sid, limit=60)
    touched = {
        int(item.get("id") or 0)
        for item in plan_data.get("threads") or []
        if item.get("acao") in ("avancar", "resolver", "abandonar")
    }
    for item in open_items:
        due = int(item.get("due_turn") or 0)
        if not due or due > turn:
            continue
        ok = item["id"] in touched
        detail = (
            f"#{item['id']} {item['title'][:160]} | prazo turno {due}"
            + ("" if ok else " | nao avancado/resolvido no plano")
        )
        _record(conn, sid, turn, item["id"], "due", ok, detail)

    by_id = {item["id"]: item for item in open_items}
    for item in plan_data.get("threads") or []:
        if item.get("acao") != "resolver":
            continue
        tid = int(item.get("id") or 0)
        source = by_id.get(tid) or {}
        ok = bool(str(item.get("payoff") or "").strip())
        detail = (
            f"#{tid} {source.get('title', item.get('titulo', ''))[:160]}"
            + (" | resolvido com payoff" if ok else " | resolvido sem payoff")
        )
        _record(conn, sid, turn, tid, "resolved", ok, detail)

    record_warnings(conn, sid, turn, warnings, "plan")


def record_warnings(conn, sid, turn, warnings, warning_type="plan"):
    for warning in warnings or []:
        _record(conn, sid, turn, None, warning_type, False, warning)
    conn.commit()


def audit_narration(conn, sid, turn, text, plan_data=None):
    """Audita se os compromissos vencidos apareceram na narracao."""
    turn = int(turn or 0)
    open_items = open_commitments(conn, sid, limit=60)
    touched = {
        int(item.get("id") or 0)
        for item in (plan_data or {}).get("threads") or []
        if item.get("acao") in ("avancar", "resolver", "abandonar")
    }
    hay = _norm(text or "")
    for item in open_items:
        due = int(item.get("due_turn") or 0)
        if not due or due > turn:
            continue
        words = _keywords(item.get("title") or "", min_len=4, limit=6)
        words += [_norm(entity) for entity in item.get("entities") or []]
        mentioned = any(
            word and re.search(r"\b" + re.escape(word) + r"\b", hay)
            for word in words
        )
        ok = mentioned or item["id"] in touched
        detail = (
            f"#{item['id']} {item['title'][:160]} | prazo turno {due}"
            + ("" if ok else " | nao mencionado na narracao")
        )
        _record(conn, sid, turn, item["id"], "narration", ok, detail)
    conn.commit()


def stats(conn, sid, turn=0):
    turn = int(turn or 0)
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n, "
        "SUM(CASE WHEN due_turn>0 AND due_turn<=? AND status='aberta' "
        "THEN 1 ELSE 0 END) AS overdue FROM threads "
        "WHERE session_id=? GROUP BY status",
        (turn, sid),
    ).fetchall()
    counts = {"aberta": 0, "resolvida": 0, "abandonada": 0}
    overdue = 0
    for row in rows:
        counts[str(row["status"])] = int(row["n"])
        overdue += int(row["overdue"] or 0)
    checks = conn.execute(
        "SELECT COUNT(*) AS n, "
        "SUM(CASE WHEN ok=0 AND check_type NOT IN ('plan','world') THEN 1 ELSE 0 END) AS failures, "
        "SUM(CASE WHEN ok=0 AND check_type IN ('plan','world') THEN 1 ELSE 0 END) AS warnings "
        "FROM commitment_checks WHERE session_id=?",
        (sid,),
    ).fetchone()
    total_checks = int(checks["n"] or 0)
    failures = int(checks["failures"] or 0)
    plan_warnings = int(checks["warnings"] or 0)
    scored = max(0, total_checks - plan_warnings)
    passed = max(0, scored - failures)
    return {
        "open": counts.get("aberta", 0),
        "resolved": counts.get("resolvida", 0),
        "abandoned": counts.get("abandonada", 0),
        "overdue": overdue,
        "checks": total_checks,
        "failures": failures,
        "warnings": plan_warnings,
        "survival": round(passed / scored, 3) if scored else 1.0,
    }


def recent_checks(conn, sid, limit=20):
    rows = conn.execute(
        "SELECT id, turn, thread_id, check_type, ok, detail, created_at "
        "FROM commitment_checks WHERE session_id=? ORDER BY id DESC LIMIT ?",
        (sid, max(1, int(limit))),
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["ok"] = bool(item.get("ok"))
        out.append(item)
    return out
