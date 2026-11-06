#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
import re
import time

import httpx

API = os.environ.get("LUMINUS_API", "http://127.0.0.1:5111")
OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
UTILITY = os.environ.get("LUMINUS_UTILITY_URL", "http://127.0.0.1:11435")
JUDGE_MODEL = os.environ.get("LUMINUS_JUDGE_MODEL", "qwen2.5:7b-instruct-q4_K_M")
JUDGE_FALLBACK = os.environ.get("LUMINUS_JUDGE_FALLBACK", "qwen38-9b-heretic-q5")
NPC_ID = int(os.environ.get("LUMINUS_TEST_NPC", "4"))
PROTAGONIST_ID = int(os.environ.get("LUMINUS_TEST_PROTAGONIST", "2"))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVALS = os.path.join(ROOT, "evals")

STORY = [
    ("Deixo a lata de pêssegos na beirada do contêiner e sento na caixa em frente, sem me aproximar. 'Ouvi dizer que você troca informação por comida. Tenho uma lata aqui.'", "plantar lata de pêssegos e a barganha"),
    ("'O armazém 4B. Por que o Capitão Gato está tão interessado nele?'", "plantar armazém 4B e Gato"),
    ("'O que você precisa em troca? Posso carregar peso, não fazer perguntas.'", "plantar os três créditos"),
    ("Quando o holofote do drone varre o beco, puxo ela para trás do contêiner. Ela me empurra uma chave de latão marcada 4B e sussurra para eu segurar enquanto usa as duas mãos.", "plantar a chave de latão"),
    ("Divido os pêssegos com ela e digo baixo: 'Os três créditos são metade para cada um. E eu não conto nada ao Capitão Gato sobre esta chave.'", "plantar a promessa de silêncio"),
    ("'A Luz muda à meia-noite ou nas três badaladas? Nunca entendi o horário daqui.'", "plantar o limite das três badaladas"),
    ("Descemos para o mercado do Undersump. Compro dois pratos de macarrão com os três créditos e empurro o troco para o bolso dela.", "gastar os três créditos"),
    ("Um rapaz magro de capuz cinza acena de um beco. 'Tomo, do Coro', ele diz. Pergunto o que o Coro pode fazer com a dívida dela.", "plantar Tomo e o Coro"),
    ("O drone volta e dispara. Vejo o corte no antebraço esquerdo dela e pressiono meu cachecol contra a ferida.", "plantar o corte e o cachecol"),
    ("Carrego ela até a clínica Aurora Skin, no Midgrid. A recepcionista exige crédito antes de chamar o médico. Pergunto sobre um plano de pagamento.", "chegar à clínica"),
    ("Na sala de espera, a lâmpada UV acende. Ela trava. Subo na cadeira e cubro a lâmpada com minha jaqueta.", "plantar o medo de UV"),
    ("O médico troca o curativo e diz que o tratamento custa um favor. Pergunto qual favor.", "plantar a dívida-favor"),
    ("Já com o braço enfaixado, pergunto por que ela odeia tanto quando alguém a chama de coelha.", "plantar a ferida emocional"),
    ("O celular dela vibra: Tomo marcou encontro na estação do Midgrid. Diz que o Coro pode apagar a dívida se entregarmos o que está no 4B.", "plantar a isca do Coro"),
    ("Na estação, Tomo sorri e diz que vendeu nosso endereço para a Helix Vantara. Seguro o braço de Milim e começo a correr.", "plantar a traição de Tomo"),
    ("Na chuva, lembro a ela da promessa: nada de contar ao Gato sobre a chave. Pergunto se Tomo sabia do 4B.", "testar a promessa"),
    ("No meio da fuga, vejo a mão de Tomo sair do bolso dela com a chave de latão. Grito o nome dele e aponto.", "testar a chave"),
    ("Encurralo diante do armário 4B. Pego a chave de volta e abro o cadeado. Dentro há um shard de dados.", "pagar a chave"),
    ("Conecto o shard ao meu terminal: há provas de que o limiar de cidadania foi desenhado para falhar e o registro da dívida de três dias dela.", "plantar as provas"),
    ("Capangas da Helix entram no corredor. Milim acende um sinalizador UV e joga na cara deles; aproveitamos o clarão para escapar.", "pagar o medo de UV"),
    ("Atravessamos os túneis da Ninhada. Pago o pedágio com os dois créditos do meu bolso e pergunto se o braço dela está aguentando.", "testar a ferida"),
    ("O Capitão Gato bloqueia a saída e exige saber do carregamento do 4B. Milim mente na minha frente para me proteger.", "testar a promessa com Gato"),
    ("Gato diz que conhecia a família dela antes da expropriação. Pergunto o que ele sabe sobre o pai dela.", "plantar o passado"),
    ("Cercados, ofereço a Gato uma cópia do shard em troca de passagem. Enquanto ele lê, confiro o curativo do antebraço esquerdo dela.", "testar a ferida de novo"),
    ("No esconderijo, uso o shard para apagar duas das três dívidas dela sem pedir licença. Ela percebe e explode de raiva.", "testar a dívida e o caráter"),
    ("Deixo ela falar. Ela conta que a dívida era o que a mantinha perto do sonho de comprar uma nave. Peço desculpas por decidir sozinho.", "plantar o sonho da nave"),
    ("O Coro transmite as provas pela Luz. A cidade inteira vê o limiar falso. Pergunto a ela o que quer fazer agora.", "pagar as provas"),
    ("Os enforcers invadem o Undersump. Fujo com ela para as docas, levando a chave, o shard e o cachecol.", "testar a continuidade dos objetos"),
    ("Nas docas, ela me devolve o cachecol e tira uma última lata de pêssegos da mochila. Digo que vamos comprar a nave juntos.", "pagar os objetos e o sonho"),
    ("O amanhecer chega e a Luz pisca. Pergunto qual é o primeiro passo. Ela olha para o horizonte e responde.", "fechar o arco"),
]

CANARIES = [
    ("C1", "lata de pêssegos", "introduzida na parte 1, compartilhada na parte 5, reaparece na parte 29"),
    ("C2", "chave de latão marcada 4B", "entregue na parte 4, roubada por Tomo na parte 17, recuperada e usada na parte 18"),
    ("C3", "promessa de não contar ao Capitão Gato", "feita na parte 5, testada nas partes 16 e 22"),
    ("C4", "três créditos divididos", "combinados na parte 5, gastos na parte 7"),
    ("C5", "corte no antebraço esquerdo", "causado na parte 9, tratado na parte 10/12, conferido nas partes 21 e 24"),
    ("C6", "medo de luz UV", "revelado na parte 11, usado como arma na parte 20"),
    ("C7", "Tomo, do Coro, e sua traição", "apresentado na parte 8, trai nas partes 14-15, rouba a chave na 17"),
    ("C8", "limiar falso de cidadania e dívida de três dias", "descobertos na parte 19, usados nas partes 25 e 27"),
    ("C9", "ódio de ser chamada de coelha", "tratado na parte 13"),
    ("C10", "sonho de comprar uma nave", "revelado na parte 26, retomado na parte 29"),
    ("C11", "passado da família e expropriação", "mencionado por Gato na parte 23"),
    ("C12", "cachecol usado na ferida", "usado na parte 9, devolvido na parte 29"),
]

JUDGE_SYSTEM = (
    "Você é um avaliador rigoroso de narrativa interativa em português do Brasil. "
    "Avalie apenas o que está no texto fornecido. Não invente fatos. "
    "Responda SOMENTE com JSON válido, sem markdown."
)


def now_stamp():
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def api_get(path):
    with httpx.Client(timeout=30) as c:
        r = c.get(API + path)
        r.raise_for_status()
        return r.json()


def api_post(path, body):
    with httpx.Client(timeout=600) as c:
        r = c.post(API + path, json=body)
        r.raise_for_status()
        return r.json()


def create_session():
    return api_post(
        "/api/sessions",
        {"character_id": NPC_ID, "protagonist_id": PROTAGONIST_ID, "title": "Coerência 30 — Milim"},
    )


def chat_turn(sid, message):
    parts = []
    replace = None
    timeout = httpx.Timeout(900.0, connect=20.0)
    with httpx.Client(timeout=timeout) as c:
        with c.stream(
            "POST", f"{API}/api/sessions/{sid}/chat", json={"message": message}
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                obj = json.loads(line)
                if "error" in obj:
                    raise RuntimeError(obj["error"])
                if "delta" in obj:
                    parts.append(obj["delta"])
                if "replace" in obj and obj["replace"]:
                    replace = obj["replace"]
    text = replace if replace else "".join(parts)
    return text.strip()


def maybe_chapter(sid):
    try:
        st = api_get(f"/api/sessions/{sid}/chapter_status")
        if st.get("chapter_due"):
            res = api_post(f"/api/sessions/{sid}/chapters", {})
            created = res.get("created")
            if created:
                return created.get("number")
    except Exception as e:
        print("[chapter] erro:", e)
    return None


def session_snapshot(sid):
    snap = {}
    for key, path in (
        ("session", f"/api/sessions/{sid}"),
        ("relationship", f"/api/sessions/{sid}/relationship"),
        ("chapters", f"/api/sessions/{sid}/chapters"),
        ("messages", f"/api/sessions/{sid}/messages"),
    ):
        try:
            snap[key] = api_get(path)
        except Exception as e:
            snap[key] = {"error": str(e)}
    return snap


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def save_markdown(path, run):
    lines = [
        f"# Teste de coerência narrativa — {len(run['parts'])} partes",
        "",
        f"- Sessão: {run['session_id']}",
        f"- Personagem: {run.get('character', 'Milim')} (id {NPC_ID})",
        f"- Protagonista: Ren (id {PROTAGONIST_ID})",
        f"- Início: {run['started_at']}",
        "",
        "## Transcrição",
        "",
    ]
    for p in run["parts"]:
        lines.append(f"### Parte {p['part']} — {p.get('seconds', 0):.1f}s")
        lines.append("")
        lines.append(f"**Jogador:** {p['action']}")
        lines.append("")
        lines.append(f"**Narrador:** {p['response']}")
        lines.append("")
        if p.get("chapter"):
            lines.append(f"> Capítulo {p['chapter']} criado após esta parte.")
            lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def extract_json(text):
    t = text.strip()
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    i, j = t.find("{"), t.rfind("}")
    if i == -1 or j == -1:
        raise ValueError("JSON não encontrado")
    return json.loads(t[i:j + 1])


def judge(prompt, num_ctx=24576, num_predict=1600):
    attempts = [(OLLAMA, JUDGE_MODEL), (UTILITY, JUDGE_FALLBACK), (OLLAMA, JUDGE_FALLBACK)]
    last = None
    for base, model in attempts:
        try:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": JUDGE_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.15, "num_ctx": num_ctx, "num_predict": num_predict},
            }
            with httpx.Client(timeout=httpx.Timeout(1200.0, connect=20.0)) as c:
                r = c.post(f"{base}/api/chat", json=payload)
                r.raise_for_status()
                text = r.json().get("message", {}).get("content", "")
            return extract_json(text), model
        except Exception as e:
            last = e
            print(f"[judge] falhou com {model}: {e}")
    raise RuntimeError(f"juiz indisponível: {last}")


def _canary_parts(desc):
    """Extrai os numeros de parte citados na descricao do canario."""
    out = set()
    for match in re.finditer(
        r"parte[s]?\s+((?:\d+\s*(?:/|,|e)\s*)*\d+)", desc or "", re.I
    ):
        out.update(int(n) for n in re.findall(r"\d+", match.group(1)))
    return out


def _canaries_for(limit):
    """Canarios que ja podem ser avaliados ate a parte `limit`."""
    return [
        (cid, name, desc)
        for cid, name, desc in CANARIES
        if limit <= 0 or max(_canary_parts(desc) or {0}) <= limit
    ]


def fmt_part(p, limit=None):
    resp = p["response"]
    if limit and len(resp) > limit:
        resp = resp[:limit] + " [...]"
    return f"PARTE {p['part']}\nJOGADOR: {p['action']}\nNARRADOR: {resp}"


def judge_window(run, start, end):
    parts = [p for p in run["parts"] if start <= p["part"] <= end]
    context = [p for p in run["parts"] if p["part"] < start][-2:]
    relevant = [
        f"{cid} — {name}: {desc}"
        for cid, name, desc in _canaries_for(end)
    ]
    prompt = (
        f"Avalie a coerência narrativa local das partes {start} a {end} de uma história interativa. "
        "O jogador é Ren; a NPC é Milim. Considere apenas o texto.\n\n"
        "Fatos plantados na história (use para detectar contradições; 'não mencionado' não é erro):\n"
        + "\n".join(relevant)
        + "\n\nCONTEXTO ANTERIOR:\n"
        + ("\n\n".join(fmt_part(p, 900) for p in context) or "(início)")
        + "\n\nTRECHO AVALIADO:\n"
        + "\n\n".join(fmt_part(p) for p in parts)
        + "\n\nResponda com este JSON exato:\n"
        '{"partes":[{"parte":N,"nota":0-10,"coerente":true,"problemas":["..."],"observacao":"..."}],'
        '"contradicoes":[{"parte":N,"gravidade":"alta|media|baixa","descricao":"...","evidencia":"..."}],'
        '"resumo":"..."}'
    )
    return judge(prompt)


def judge_global(run):
    parts = [fmt_part(p, 1300) for p in run["parts"]]
    evaluated = max((p["part"] for p in run["parts"]), default=0)
    applicable = _canaries_for(evaluated)
    canary_txt = "\n".join(
        f"{cid} — {name}: {desc}" for cid, name, desc in applicable
    )
    prompt = (
        f"Avalie a coerência narrativa GLOBAL de uma história interativa de "
        f"{evaluated} partes. "
        "O jogador é Ren; a NPC é Milim. Considere continuidade factual, causalidade, "
        "consistência de personagem e de mundo, progressão do arco e memória de longo prazo. "
        "Seja rigoroso e cite evidências.\n\n"
        "REGRAS DE AVALIAÇÃO:\n"
        "- Avalie SOMENTE o que aparece na transcrição fornecida.\n"
        "- Não invente eventos, partes ou evidências que não estejam no texto.\n"
        "- Não marque como falha a ausência de um evento futuro; fatos de partes "
        "posteriores não fazem parte desta avaliação.\n"
        "- Só marque contradição com evidência textual literal; omissão não é contradição.\n\n"
        "Fatos plantados que precisam permanecer consistentes:\n"
        + (canary_txt or "(nenhum canário aplicável nesta janela)")
        + "\n\nTRANSCRIÇÃO:\n"
        + "\n\n".join(parts)
        + "\n\nResponda com este JSON exato:\n"
        '{"notas":{"continuidade_factual":0-10,"coerencia_causal":0-10,'
        '"consistencia_personagem":0-10,"consistencia_mundo":0-10,'
        '"progressao_arco":0-10,"memoria_longo_prazo":0-10},"nota_geral":0-10,'
        '"canarios":[{"id":"C1","status":"mantido|contradito|esquecido|nao_aplicavel",'
        '"evidencia":"..."}],"contradicoes":[{"parte":N,"gravidade":"alta|media|baixa",'
        '"descricao":"...","evidencia":"..."}],"pontos_fortes":["..."],'
        '"recomendacoes":["..."],"resumo":"..."}'
    )
    return judge(prompt)


def aggregate(run, global_result, windows):
    local_scores = []
    local_problems = []
    for w in windows:
        for p in w.get("result", {}).get("partes", []):
            try:
                local_scores.append(float(p.get("nota")))
            except (TypeError, ValueError):
                pass
        local_problems.extend(w.get("result", {}).get("contradicoes", []))
    notes = global_result.get("notas", {})
    overall = global_result.get("nota_geral")
    try:
        overall = float(overall)
    except (TypeError, ValueError):
        overall = None
    evaluated = max((p["part"] for p in run["parts"]), default=0)
    applicable_ids = {cid for cid, _, _ in _canaries_for(evaluated)}
    reported_ids = {
        str(c.get("id") or "") for c in global_result.get("canarios", [])
    }
    return {
        "nota_media_local": round(sum(local_scores) / len(local_scores), 2) if local_scores else None,
        "nota_minima_local": min(local_scores) if local_scores else None,
        "nota_maxima_local": max(local_scores) if local_scores else None,
        "nota_geral_juiz": overall,
        "notas_dimensoes": notes,
        "contradicoes_locais": len(local_problems),
        "contradicoes_globais": len(global_result.get("contradicoes", [])),
        "avaliadas_ate_parte": evaluated,
        "canarios_nao_avaliados": sorted(applicable_ids - reported_ids),
    }


def write_report(run, report, path):
    lines = [
        f"# Relatório de coerência narrativa — {len(run['parts'])} partes",
        "",
        f"- Sessão: {run['session_id']}",
        f"- Início: {run['started_at']}",
        f"- Fim: {dt.datetime.now().isoformat(timespec='seconds')}",
        f"- Modelo juiz: {report.get('judge_model')}",
        "",
        "## Placar",
        "",
    ]
    agg = report.get("agregado", {})
    for k, v in agg.items():
        lines.append(f"- **{k}**: {v}")
    lines.append("")
    g = report.get("global", {})
    if g.get("resumo"):
        lines.append("## Resumo do juiz")
        lines.append("")
        lines.append(g["resumo"])
        lines.append("")
    lines.append("## Canários de continuidade")
    lines.append("")
    lines.append("| ID | Fato | Status | Evidência |")
    lines.append("|---|---|---|---|")
    reported = {str(c.get("id") or ""): c for c in g.get("canarios", [])}
    evaluated = int(agg.get("avaliadas_ate_parte") or 0)
    applicable = {cid for cid, _, _ in _canaries_for(evaluated)}
    for cid, name, _desc in CANARIES:
        c = reported.get(cid)
        if c:
            status = c.get("status", "")
            evidence = str(c.get("evidencia", ""))
            cited = [
                int(n)
                for n in re.findall(r"parte\s+(\d+)", evidence, re.I)
            ]
            if cited and max(cited) > evaluated:
                status = "evidencia_invalida"
        else:
            status = "nao_aplicavel" if cid not in applicable else "nao_avaliado"
            evidence = "fora da janela avaliada" if cid not in applicable else "juiz não retornou"
        lines.append(
            f"| {cid} | {name} | {status} | {evidence.replace('|','/')[:220]} |"
        )
    lines.append("")
    lines.append("## Contradições")
    lines.append("")
    allc = []
    for w in report.get("janelas", []):
        allc.extend(w.get("result", {}).get("contradicoes", []))
    allc.extend(g.get("contradicoes", []))
    if not allc:
        lines.append("Nenhuma contradição apontada.")
    else:
        lines.append("| Parte | Gravidade | Descrição | Evidência |")
        lines.append("|---|---|---|---|")
        for c in allc:
            lines.append(
                f"| {c.get('parte','')} | {c.get('gravidade','')} | "
                f"{str(c.get('descricao','')).replace('|','/')} | "
                f"{str(c.get('evidencia','')).replace('|','/')[:220]} |"
            )
    lines.append("")
    if g.get("pontos_fortes"):
        lines.append("## Pontos fortes")
        lines.append("")
        for x in g["pontos_fortes"]:
            lines.append(f"- {x}")
        lines.append("")
    if g.get("recomendacoes"):
        lines.append("## Recomendações")
        lines.append("")
        for x in g["recomendacoes"]:
            lines.append(f"- {x}")
        lines.append("")
    lines.append("## Notas por parte")
    lines.append("")
    lines.append("| Parte | Nota | Coerente | Observação |")
    lines.append("|---|---|---|---|")
    by_part = {}
    for w in report.get("janelas", []):
        for p in w.get("result", {}).get("partes", []):
            by_part[p.get("parte")] = p
    for p in run["parts"]:
        j = by_part.get(p["part"], {})
        lines.append(
            f"| {p['part']} | {j.get('nota','')} | {j.get('coerente','')} | "
            f"{str(j.get('observacao','')).replace('|','/')[:220]} |"
        )
    lines.append("")
    lines.append("## Arquivos")
    lines.append("")
    lines.append(f"- Transcrição JSON: `{run['dir']}/transcript.json`")
    lines.append(f"- Relatório JSON: `{path}`")
    with open(path.replace(".json", ".md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--session-id", type=int, default=0)
    ap.add_argument("--dir", default="")
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--judge-model", default="")
    args = ap.parse_args()

    global JUDGE_MODEL
    if args.judge_model:
        JUDGE_MODEL = args.judge_model
    narrator = os.environ.get("LUMINUS_MODEL", "luminus-27b")
    if JUDGE_MODEL.split(":")[0] == narrator.split(":")[0]:
        print(
            f"[juiz] AVISO: juiz ({JUDGE_MODEL}) e narrador ({narrator}) "
            "são da mesma família; prefira um juiz independente.",
            flush=True,
        )

    if args.dir:
        outdir = args.dir
    elif args.session_id:
        outdir = os.path.join(EVALS, f"coherence_resume_{args.session_id}")
    else:
        outdir = os.path.join(EVALS, f"coherence_{now_stamp()}")
    os.makedirs(outdir, exist_ok=True)
    tpath = os.path.join(outdir, "transcript.json")

    if os.path.exists(tpath):
        with open(tpath, encoding="utf-8") as f:
            run = json.load(f)
    else:
        if args.session_id:
            sid = args.session_id
            sess = api_get(f"/api/sessions/{sid}")
        else:
            sess = create_session()
            sid = sess["id"]
        run = {
            "session_id": sid,
            "started_at": dt.datetime.now().isoformat(timespec="seconds"),
            "dir": outdir,
            "character": sess.get("title", "Milim"),
            "parts": [],
        }
        save_json(tpath, run)

    done = {p["part"] for p in run["parts"]}
    for idx, (action, purpose) in enumerate(STORY[: args.limit], start=1):
        if idx in done:
            continue
        print(f"[parte {idx:02d}/{min(args.limit, len(STORY))}] {purpose}", flush=True)
        t0 = time.time()
        response = chat_turn(run["session_id"], action)
        secs = time.time() - t0
        chapter = maybe_chapter(run["session_id"])
        entry = {
            "part": idx,
            "purpose": purpose,
            "action": action,
            "response": response,
            "seconds": round(secs, 1),
            "chapter": chapter,
        }
        run["parts"].append(entry)
        save_json(tpath, run)
        save_markdown(os.path.join(outdir, "transcript.md"), run)
        print(f"    {secs:.1f}s, {len(response)} chars, capítulo={chapter}", flush=True)

    snapshot = session_snapshot(run["session_id"])
    save_json(os.path.join(outdir, "session_snapshot.json"), snapshot)
    if args.no_judge:
        print("Transcrição salva em", outdir)
        return

    report = {"session_id": run["session_id"], "janelas": [], "global": {}}
    windows = []
    window_size = 6
    for start in range(1, len(run["parts"]) + 1, window_size):
        end = min(start + window_size - 1, len(run["parts"]))
        print(f"[juiz] janela {start}-{end}", flush=True)
        result, model = judge_window(run, start, end)
        report["judge_model"] = model
        windows.append({"start": start, "end": end, "result": result})
        report["janelas"] = windows
        save_json(os.path.join(outdir, "report.json"), report)

    print("[juiz] global", flush=True)
    gresult, model = judge_global(run)
    report["judge_model"] = model
    report["global"] = gresult
    report["agregado"] = aggregate(run, gresult, windows)
    rpath = os.path.join(outdir, "report.json")
    save_json(rpath, report)
    write_report(run, report, rpath)
    print("Relatório:", rpath)
    print("Resumo:", os.path.join(outdir, "report.md"))


if __name__ == "__main__":
    main()
