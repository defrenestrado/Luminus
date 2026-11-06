"""guard.py — Fase 2: gate pos-geracao (anti-puppeting, anti-vazamento, repeticao).

Deterministico para detectar; usa o modelo utilitario (9B Q5 na 2060) apenas
quando ha violacao, para reescrever cirurgicamente sem perder fatos/estilo.
"""

import re

STOP = {
    "de", "da", "do", "das", "dos", "que", "e", "o", "a", "os", "as", "um", "uma",
    "para", "por", "com", "no", "na", "nos", "nas", "ao", "aos", "em", "se", "sua",
    "seu", "ele", "ela", "voce", "você", "nao", "não", "mais", "como", "mas",
    "foi", "ser", "ter", "tem", "ja", "já", "pelo", "pela", "dos", "das", "isso",
}


def _norm(s):
    return re.sub(r"[^a-z0-9áéíóúâêíóúôãõàç ]", " ", (s or "").lower())


def _words(s):
    return [w for w in _norm(s).split() if len(w) >= 4 and w not in STOP]


FAMILY = {"filha", "filho", "pai", "mae", "mãe", "familia", "família",
          "irmao", "irmão", "irmaos", "irmãos"}


def _npc_interior_re(npc):
    """Regex para interioridade do NPC: "pensa Lala" / "Lala sente que...".

    A camera do Luminus narra o que o jogador percebe; pensamentos e sensacoes
    internas do NPC so podem aparecer como sinais externos.
    """
    name = str((npc or {}).get("name") or "").strip().split()
    if not name:
        return None
    first = re.escape(name[0])
    verbs = (
        r"(pensa|pensou|sente|sentiu|quer|quis|decide|decidiu|percebe|"
        r"percebeu|sabe|sabia|teme|temeu)"
    )
    return re.compile(rf"\b{verbs}\s+{first}\b|\b{first}\s+{verbs}\b", re.I)


def secret_terms(npc):
    secrets = (npc.get("secrets") or "") if npc else ""
    words = set(_words(secrets))
    if words & FAMILY:
        words |= FAMILY
    return words


def detect(narrative, npc, state, user_msg):
    issues = []
    nar = _norm(narrative)
    umsg = _norm(user_msg)

    # 1) eco da acao/fala do jogador no inicio
    quoted = re.findall(r'"([^"]{12,})"', user_msg or "")
    for q in quoted:
        if _norm(q)[:40] in nar[:260]:
            issues.append("narra a fala do jogador")
            break
    if len(umsg) > 15:
        head = " ".join(umsg.split()[:9])
        if head and head in nar[:220]:
            issues.append("repete a acao do jogador")

    # 2) narra decisao/pensamento do jogador
    if re.search(
        r"\b(voce|você|ren)\s+(?:n[ãa]o\s+)?(decide|decidiu|sente|sentiu|pensa|"
        r"pensou|percebe que|quer|quis|sabe que|entende que|conclui)\b",
        nar,
    ):
        issues.append("narra pensamento/decisao do jogador")

    # 2b) narra acao/estado do jogador sem ele ter declarado
    if re.search(
        r"\b(voce|você|ren)\s+(?:n[ãa]o\s+)?(est[áa]|estava|veio|foi|vai|faz|fez|"
        r"pega|pegou|levanta|levantou|senta|sentou|caminha|caminhou|olha|"
        r"olhou|diz|disse|fala|falou|concorda|recusa|aceita|puxa|empurra|"
        r"avan[çc]a|recua|hesita|respira)\b",
        nar,
    ) and not re.search(r"\b(voce|você)\s+(ve|vê|ouve|escuta|percebe|nota|repara)\b", nar):
        issues.append("narra acao do jogador")

    # 3) vazamento de segredo nao solicitado (bloqueado so abaixo do limiar de NR)
    sk = secret_terms(npc)
    uk = set(_words(user_msg))
    try:
        nr = int(state.get("nr", 20) or 20) if state else 20
    except (ValueError, TypeError):
        nr = 20
    if sk and nr < 65:
        toks = set(nar.split())
        leaked = [w for w in sk if w in toks and w not in uk]
        if len(leaked) >= 2:
            issues.append("vaza segredo nao solicitado")

    # 4) interioridade do NPC (quebra a camera: so sinais externos)
    npc_re = _npc_interior_re(npc)
    if npc_re and npc_re.search(narrative or ""):
        issues.append("narra interioridade do NPC")

    # 5) repeticao de frases
    sents = [s.strip() for s in re.split(r"[.!?]", narrative or "") if len(s.strip()) > 50]
    if len(sents) != len(set(sents)):
        issues.append("repete frases")

    # 6) interioridade do NPC em italico (pensamento disfarcado de enfase)
    for italic in re.findall(r"\*([^*]{8,})\*", narrative or ""):
        if re.search(
            r"\b(pensa|pensou|sente|sentiu|quer|queria|sabe|sabia|teme|temia|"
            r"lembra|lembrou|precisa|precisava|decide|decidiu)\b",
            italic,
            re.I,
        ):
            issues.append("narra interioridade do NPC")
            break

    # dedup
    out = []
    for i in issues:
        if i not in out:
            out.append(i)
    return out


_REWRITE_ISSUES = {
    "narra pensamento/decisao do jogador",
    "narra acao do jogador",
    "narra interioridade do NPC",
}


def needs_rewrite(issues):
    return any(issue in _REWRITE_ISSUES for issue in (issues or []))


def rewrite_prompt(narrative, issues, npc, user_msg):
    secrets = (npc.get("secrets") or "").strip() if npc else ""
    return (
        "Voce e um revisor de narrativa de RPG. Reescreva o texto abaixo corrigindo APENAS:\n"
        + "\n".join(f"- {i}" for i in issues)
        + "\n\nRegras obrigatorias:\n"
        "- Mantenha os mesmos fatos, personagens, tom e comprimento aproximado.\n"
        "- REMOVA qualquer frase que diga o que o jogador sente, pensa, decide, "
        "deseja ou faz por conta propria (ex.: 'voce sente', 'voce decide', "
        "'voce recua', 'voce esta sentado', 'voce veio ate aqui'). "
        "O jogador e controlado pelo usuario; narre apenas o que ele percebe.\n"
        "- REMOVA qualquer revelacao direta dos segredos abaixo; no lugar, deixe uma pista ambigua "
        "ou uma negativa evasiva.\n"
        f"- SEGREDOS QUE NAO PODEM SER REVELADOS: {secrets or '(nenhum)'}\n"
        "- Mantenha 2a pessoa para narrar o que o jogador PERCEBE, falas entre aspas e acoes em *italico*.\n"
        "- Responda apenas com a narrativa reescrita.\n\n"
        f"ACAO DO JOGADOR (nao repita):\n{user_msg}\n\n"
        f"TEXTO A REESCREVER:\n{narrative}"
    )


PUPPET_RE = re.compile(
    r"\b(voce|você|ren)\s+(sente|sentiu|decide|decidiu|pensa|pensou|quer|quis|"
    r"recua|avança|avanca|concorda|hesita|sabe que|entende que|conclui)\b",
    re.I,
)

SAFE_SECRET_LINE = "Ela abre a boca, mas algo a faz engolir as palavras e desviar o assunto."


def _sentences(text):
    return [x for x in re.split(r"(?<=[.!?])\s+", text or "") if x.strip()]


def redact(narrative, issues, npc):
    if not issues:
        return narrative
    secrets = secret_terms(npc)
    out = []
    seen = set()
    echo_done = False
    for s in _sentences(narrative):
        low = _norm(s)
        toks = set(low.split())
        if "narra pensamento/decisao do jogador" in issues and PUPPET_RE.search(s):
            continue
        if "narra interioridade do NPC" in issues:
            npc_re = _npc_interior_re(npc)
            if npc_re and npc_re.search(s):
                continue
        if ("repete a acao do jogador" in issues or "narra a fala do jogador" in issues) and not out:
            continue
        if "vaza segredo nao solicitado" in issues and secrets:
            if any(w in toks for w in secrets):
                if not out or out[-1] != SAFE_SECRET_LINE:
                    out.append(SAFE_SECRET_LINE)
                continue
        if "repete frases" in issues:
            key = low.strip()
            if key in seen:
                continue
            seen.add(key)
        out.append(s.strip())
    return " ".join(out).strip()


def scrub(text, npc, allow_secrets=False):
    if allow_secrets:
        return text
    terms = secret_terms(npc)
    if not terms or not text:
        return text
    out = []
    for para in text.split("\n"):
        toks = set(_norm(para).split())
        if any(w in toks for w in terms):
            if not out or out[-1].strip() != SAFE_SECRET_LINE:
                out.append(SAFE_SECRET_LINE)
        else:
            out.append(para)
    return "\n".join(out).strip()


def clean(text, issues, npc):
    t = text
    if "vaza segredo nao solicitado" in issues:
        t = scrub(t, npc)
    other = [i for i in issues if i != "vaza segredo nao solicitado"]
    if other:
        t = redact(t, other, npc)
    return t.strip()
