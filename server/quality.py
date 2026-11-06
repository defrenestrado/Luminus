"""Pos-processamento deterministico da narracao do Luminus.

Tres responsabilidades, todas locais e sem chamada de modelo:

- format_narration: deixa o texto legivel no celular (paragrafos curtos,
  fala em linha propria) sem cortar conteudo.
- detect_logic_issues / drop_logic_sentences: pega exigencias de NPC que
  nao fazem sentido (ex.: cobrar para o jogador ouvir a musica que o
  proprio NPC esta tocando) e remove a frase ofensora.
- location_transition_ok: impede teleporte silencioso de cena.

O modulo nao decide estilo literario; so corrige defeitos objetivos.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from difflib import SequenceMatcher

MAX_PARAGRAPH_CHARS = 380
MAX_PARAGRAPHS = 10

_STOP = {
    "de", "da", "do", "das", "dos", "que", "e", "o", "a", "os", "as", "um",
    "uma", "para", "por", "com", "no", "na", "nos", "nas", "ao", "aos", "em",
    "se", "sua", "seu", "ele", "ela", "voce", "você", "nao", "não", "mais",
    "como", "mas", "foi", "ser", "ter", "tem", "ja", "já", "pelo", "pela",
    "dos", "das", "isso", "aqui", "ali", "the", "andar",
}

_MOVEMENT = re.compile(
    r"\b(chega|cheguei|chegou|entra|entrei|entrou|sai|saí|saiu|segue|seguiu|"
    r"atravessa|atravessou|desce|desceu|sobe|subiu|vai para|foi para|"
    r"caminha ate|caminhou ate|rumei|rumou|partiu para)\b",
    re.I,
)

_SENT_SPLIT = re.compile(
    r"(?<=[.!?…])\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÀÇ\"“—*])"
)

_DIALOGUE_INLINE = re.compile(
    r"(?:(?<=[.!?…])|(?<=\n)|^)[ \t]*[—–][ \t]*(?=[\"“])",
    re.M,
)
_CLOSED_QUOTE_FIX = re.compile(
    r"([.!?…])\s*[—–]\s*\"\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÀÇ])"
)

_META_START = re.compile(
    r"(?m)^(?![\s\"“—–*])[ \t]*(espero que|claro[,!]|aqui est[áa]|"
    r"com certeza|fico feliz|qualquer coisa|se precisar|qualquer d[úu]vida)\b[^\n]*",
    re.I,
)

_KNOWN_FIXES = (
    (re.compile(r"\bengluvindo\b", re.I), "engolindo"),
    (re.compile(r"\bengluvir\b", re.I), "engolir"),
    (re.compile(r"\bzunha\b", re.I), "zune"),
    (re.compile(r"\bbatede\b", re.I), "batendo"),
    (re.compile(r"\bcoml\b", re.I), "com"),
    (re.compile(r"\bvoce\b", re.I), "você"),
)

_PAY = re.compile(
    r"\b(pagar?|pague|paga|pagamento|pre[çc]o|custa|custam|cobr\w*|"
    r"cr[eé]ditos?|taxa|entrada)\b",
    re.I,
)
_FREE = re.compile(
    r"\b(gr[áa]tis|de gra[çc]a|sem cobrar|n[ãa]o custa|por conta da casa)\b",
    re.I,
)
_PERFORM = re.compile(
    r"\b(toc\w*|cant\w*|mostr\w*|exib\w*|dan[çc]\w*|"
    r"serve|servem|servir|servindo|ofere[çc]\w*|"
    r"ensin\w*|recit\w*|apresent\w*)\b",
    re.I,
)
_LISTEN = re.compile(
    r"\b(m[úu]sica|can[çc][ãa]o|show|apresenta[çc][ãa]o|performance|"
    r"concerto|espet[áa]culo|violino|piano|guitarra|banda|orquestra|jazz)\b",
    re.I,
)
_LISTEN_VERB = re.compile(
    r"\b(ouvir|escutar|assistir)\b"
    r"(?!\s+(?:a\s+)?(?:hist[óo]ria|conversa|fala|voz|voc[êe]|isso|"
    r"aquilo|o que|algu[ée]m|explica[çc][ãa]o))",
    re.I,
)
_MOTIVE = re.compile(
    r"\b(porque|pois|para|pra|em troca|se voc[êe]|se n[ãa]o|sen[ãa]o|"
    r"d[íi]vida|regra|lei|entrada|taxa|servi[çc]o|aluguel|conserto|"
    r"rem[ée]dio|comida|prote[çc][ãa]o|informa[çc][ãa]o)\b",
    re.I,
)
_DEMAND = re.compile(
    r"\b(pague|exijo|exige|me d[êe]|me entregue|me deve|"
    r"obede[çc]a|entregue|devolva)\b",
    re.I,
)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^0-9a-z ]", " ", text.lower())


def _key(text: str) -> str:
    return _norm(text).strip()


def apply_known_fixes(text: str) -> str:
    out = text or ""
    for pattern, replacement in _KNOWN_FIXES:
        out = pattern.sub(replacement, out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    return out


def strip_meta_voice(text: str) -> str:
    """Remove frases de assistente ("Espero que...") que quebram a cena."""
    out = _META_START.sub("", text or "")
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def _split_long(sentence: str, max_chars: int) -> list[str]:
    if len(sentence) <= max_chars:
        return [sentence]
    out: list[str] = []
    current = ""
    for word in sentence.split():
        if current and len(current) + 1 + len(word) > max_chars:
            out.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        out.append(current)
    return out


def _split_block(block: str, max_chars: int) -> list[str]:
    block = " ".join(part.strip() for part in block.splitlines() if part.strip())
    if not block:
        return []
    if len(block) <= max_chars:
        return [block]
    sentences: list[str] = []
    for sentence in _SENT_SPLIT.split(block):
        sentence = sentence.strip()
        if sentence:
            sentences.extend(_split_long(sentence, max_chars))
    out: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + 1 + len(sentence) > max_chars:
            out.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        out.append(current)
    return out


def format_narration(text: str, max_chars: int = MAX_PARAGRAPH_CHARS) -> str:
    """Normaliza a narracao para leitura no celular.

    - remove voz de assistente e erros lexicais conhecidos;
    - coloca cada fala (linha iniciada por travessao + aspas) em bloco proprio;
    - quebra paragrafos longos em blocos de ate `max_chars` caracteres.
    """
    if not (text or "").strip():
        return text or ""
    out = apply_known_fixes(strip_meta_voice(text))
    out = out.replace("\r\n", "\n").replace("\r", "\n")
    out = _CLOSED_QUOTE_FIX.sub(r'\1" — ', out)
    out = _DIALOGUE_INLINE.sub("\n\n— ", out)
    blocks: list[str] = []
    for raw in re.split(r"\n\s*\n", out):
        blocks.extend(_split_block(raw, max_chars))
    if len(blocks) > MAX_PARAGRAPHS:
        # Mantem a ordem e junta apenas blocos vizinhos curtos.
        merged: list[str] = []
        for block in blocks:
            if (
                merged
                and len(merged[-1]) + len(block) + 2 <= max_chars
                and not block.startswith("—")
                and not merged[-1].startswith("—")
            ):
                merged[-1] = f"{merged[-1]} {block}"
            else:
                merged.append(block)
        blocks = merged
    return "\n\n".join(part.strip() for part in blocks if part.strip()).strip()


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENT_SPLIT.split(text or "") if part.strip()]


def detect_logic_issues(text: str, npc=None, state=None) -> list[str]:
    """Detecta exigencias de NPC sem nexo material.

    Casos cobertos:
    - cobrar do jogador para ouvir/assistir algo que o proprio NPC executa;
    - cobrar e dizer que e gratis na mesma fala;
    - exigir algo sem nenhum motivo, troca ou consequencia declarada.
    """
    issues: list[str] = []
    if not (text or "").strip():
        return issues
    npc_key = _key(str((npc or {}).get("name") or ""))
    for paragraph in re.split(r"\n\s*\n", text):
        phrases = _sentences(paragraph)
        for index, sentence in enumerate(phrases):
            if not _PAY.search(sentence):
                continue
            if _FREE.search(sentence):
                issues.append("cobranca_contraditoria")
                continue
            listens = bool(
                _LISTEN.search(sentence) or _LISTEN_VERB.search(sentence)
            )
            if not listens:
                if _DEMAND.search(sentence) and not _MOTIVE.search(sentence):
                    issues.append("exigencia_sem_motivo")
                continue
            performs = bool(_PERFORM.search(sentence))
            if not performs and npc_key and npc_key in _norm(sentence):
                performs = True
            if not performs and index > 0:
                previous = phrases[index - 1]
                performs = bool(_PERFORM.search(previous))
                if not performs and npc_key and npc_key in _norm(previous):
                    performs = True
            if performs:
                issues.append("cobra_pelo_que_oferece")
    return list(dict.fromkeys(issues))


def drop_logic_sentences(text: str, issues: list[str]) -> str:
    """Remove as frases que sustentam uma cobranca ilogica."""
    if not issues:
        return text
    active = set(issues) & {"cobra_pelo_que_oferece", "cobranca_contraditoria"}
    if not active:
        return text
    out: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text or ""):
        keep: list[str] = []
        for sentence in _sentences(paragraph):
            bad = False
            if "cobra_pelo_que_oferece" in active:
                bad = bool(
                    _PAY.search(sentence)
                    and (
                        _LISTEN.search(sentence)
                        or _LISTEN_VERB.search(sentence)
                        or _PERFORM.search(sentence)
                    )
                )
            if not bad and "cobranca_contraditoria" in active:
                bad = bool(_PAY.search(sentence) and _FREE.search(sentence))
            if not bad:
                keep.append(sentence)
        if keep:
            out.append(" ".join(keep))
    return "\n\n".join(out).strip()


def logic_review_prompt(text: str, issues: list[str], npc=None) -> str:
    name = str((npc or {}).get("name") or "o NPC")
    return (
        "Voce revisa a logica material de uma cena de RPG em PT-BR. "
        f"O NPC em foco e {name}. Corrija APENAS os problemas listados, "
        "preservando fatos, tom e tamanho.\n\n"
        "PROBLEMAS:\n- " + "\n- ".join(issues) + "\n\n"
        "REGRAS:\n"
        "- Toda exigencia do NPC precisa ter motivo material ou relacional claro.\n"
        "- Nunca cobre do jogador por algo que o proprio NPC esta oferecendo, "
        "tocando, cantando ou mostrando.\n"
        "- Se o NPC e o artista, quem cobra e o estabelecimento, nao ele, pelo "
        "ato de o jogador ouvir.\n"
        "- Nao invente pedagio, taxa ou divida sem dono, regra ou contrapartida.\n"
        "- Mantenha a mesma cena, personagens e falas que NAO tem problema.\n"
        "- Responda apenas com a narrativa reescrita.\n\n"
        f"TEXTO:\n{text}"
    )


def location_transition_ok(text: str, previous: str, current: str) -> bool:
    """True se a narracao justifica sair de `previous` para `current`."""
    prev, cur = (previous or "").strip(), (current or "").strip()
    if not prev or not cur or _key(prev) == _key(cur):
        return True
    hay = _norm(text)
    tokens = [
        token for token in _key(cur).split()
        if len(token) >= 4 and token not in _STOP
    ]
    if tokens and any(token in hay for token in tokens):
        return True
    return bool(_MOVEMENT.search(text or ""))


def text_fingerprint(text: str) -> str:
    return hashlib.sha1(_key(text).encode("utf-8")).hexdigest()


def is_duplicate(a: str, b: str, threshold: float = 0.92) -> bool:
    left, right = _norm(a), _norm(b)
    if not left or not right:
        return False
    if left == right:
        return True
    return SequenceMatcher(None, left, right).ratio() >= threshold
