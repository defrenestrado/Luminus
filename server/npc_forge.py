"""Criação profunda de NPCs: Pensador -> Juiz -> Síntese.

Inspirado no freellmpool (https://github.com/0xzr/freellmpool): um pool de
rotas OpenAI-compatible com failover, aliases de qualidade (``quality``/``auto``)
e um painel de segunda opinião. Aqui o "painel" é interno: o Pensador abre o
dossiê psicológico, o Juiz procura clichês/contradições e a Síntese transforma
tudo na ficha JSON que o app já usa.

Rotas (em ordem de qualidade, com failover):
  1. ``LUMINUS_NPC_POOL_URL``  — proxy freellmpool/OpenAI-compatible (opcional)
  2. DeepSeek API              — se ``LUMINUS_NPC_USE_DEEPSEEK=1`` e houver chave
  3. Ollama utilitário (9B)    — GPU 2060 ou CPU, conforme disponibilidade
  4. Ollama principal (27B)    — só com ``LUMINUS_NPC_ALLOW_MAIN=1``

A ideia é opcional e não adiciona dependência: se nenhum pool estiver
configurado, o fluxo profundo usa o modelo utilitário local. Se a GPU
utilitária estiver em CPU, o fluxo profundo é pulado para não travar a criação.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx

import llm
import metrics
import prompts


class NpcForgeError(RuntimeError):
    pass


_active_main_model = ""


def set_active_main_model(model: str) -> None:
    """O main.py informa qual modelo o pool principal está usando agora.

    Em modo degradado (2 GPUs) o Narrador usa o 9B Q8; a criação profunda deve
    usar o MESMO modelo para não pedir o 27B, que não cabe / falhou.
    """
    global _active_main_model
    _active_main_model = (model or "").strip()


def _main_model() -> str:
    return _active_main_model or llm.DEFAULT_MODEL


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "on", "yes")


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        return default


# ─── Rotas / pool ───────────────────────────────────────────────────────────


@dataclass
class Route:
    name: str
    kind: str  # pool | deepseek | local
    model: str
    base: Optional[str] = None
    api_key: str = ""
    task: str = ""
    force_utility: bool = False


def _pool_url() -> str:
    return os.environ.get("LUMINUS_NPC_POOL_URL", "").strip()


def _deepseek_key() -> str:
    return os.environ.get("DEEPSEEK_API_KEY", "").strip()


def _has_remote() -> bool:
    return bool(_pool_url()) or (
        _env_bool("LUMINUS_NPC_USE_DEEPSEEK", False) and bool(_deepseek_key())
    )


def _allow_cpu() -> bool:
    return _env_bool("LUMINUS_NPC_ALLOW_CPU", False)


def _routes() -> List[Route]:
    routes: List[Route] = []
    pool = _pool_url()
    if pool:
        routes.append(
            Route(
                name="freellmpool",
                kind="pool",
                model=os.environ.get("LUMINUS_NPC_POOL_MODEL", "quality").strip()
                or "quality",
                base=pool,
                api_key=os.environ.get("LUMINUS_NPC_POOL_KEY", "").strip(),
                task=os.environ.get("LUMINUS_NPC_POOL_TASK", "general").strip(),
            )
        )

    if _env_bool("LUMINUS_NPC_USE_DEEPSEEK", False) and _deepseek_key():
        routes.append(
            Route(
                name="deepseek",
                kind="deepseek",
                model=os.environ.get(
                    "LUMINUS_NPC_DEEPSEEK_MODEL",
                    os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
                ).strip()
                or "deepseek-chat",
                base=os.environ.get(
                    "LUMINUS_NPC_DEEPSEEK_URL", "https://api.deepseek.com/v1"
                ).rstrip("/"),
                api_key=_deepseek_key(),
            )
        )

    allow_main = _env_bool("LUMINUS_NPC_ALLOW_MAIN", False)
    # O utilitário local é a rota de menor custo. Em GPU ele é rápido; em CPU
    # só entra se não houver alternativa remota, se o usuário permitir CPU ou
    # se não houver o modelo principal como reserva.
    if (
        llm.utility_available()
        or _allow_cpu()
        or (not _has_remote() and not allow_main)
    ):
        routes.append(
            Route(
                name="utility",
                kind="local",
                model=os.environ.get(
                    "LUMINUS_PLANNER_MODEL", llm.UTILITY_MODEL
                ).strip()
                or llm.UTILITY_MODEL,
                base=llm.OLLAMA_UTILITY,
                force_utility=True,
            )
        )

    if allow_main and llm.primary_ok():
        routes.append(
            Route(
                name="main",
                kind="local",
                model=_main_model(),
                base=None,
            )
        )

    return routes


def _quality_mode() -> str:
    raw = (os.environ.get("LUMINUS_QUALITY_MODE", "balanced") or "").strip().lower()
    return raw if raw in ("fast", "balanced", "max") else "balanced"


def _thinker_candidates() -> int:
    raw = os.environ.get("LUMINUS_NPC_THINKER_CANDIDATES", "").strip()
    if raw:
        try:
            return max(1, min(4, int(raw)))
        except ValueError:
            pass
    return 2 if _quality_mode() == "max" else 1


def _stage_timeout() -> float:
    if "LUMINUS_NPC_STAGE_TIMEOUT" in os.environ:
        return _env_float("LUMINUS_NPC_STAGE_TIMEOUT", 90.0)
    return 120.0 if _quality_mode() == "max" else 90.0


def _synth_prefer() -> str:
    prefer = os.environ.get("LUMINUS_NPC_SYNTH_ROUTE", "").strip().lower()
    if prefer in ("main", "utility", "pool", "auto"):
        return prefer
    return "main" if _quality_mode() == "max" else "auto"


def _pool_attempts() -> int:
    """Tentativas por etapa no pool. O freellmpool já faz failover interno;
    uma segunda tentativa cobre 404/429 transitórios de provedor."""
    return max(1, min(4, _env_int("LUMINUS_NPC_POOL_ATTEMPTS", 2)))


def _pool_timeout() -> float:
    """Tempo máximo de UMA chamada ao pool, antes de tentar de novo ou cair
    para a próxima rota. Evita que um provedor lento coma a etapa inteira."""
    return max(10.0, _env_float("LUMINUS_NPC_POOL_TIMEOUT", 75.0))


def _thinker_grace() -> float:
    """Depois do 1º dossiê válido, quanto esperar pelos demais pensadores.
    Sem isso, um candidato lento (ou uma rota local falhando) segura a criação
    inteira mesmo quando já há material suficiente."""
    return max(0.0, _env_float("LUMINUS_NPC_THINKER_GRACE", 8.0))


def _ordered_routes(prefer: str = "") -> List[Route]:
    routes = _routes()
    if not prefer or prefer == "auto":
        return routes
    order = {
        "main": {"main": 0, "utility": 1, "freellmpool": 2, "deepseek": 2},
        "utility": {"utility": 0, "main": 1, "freellmpool": 2, "deepseek": 2},
        "pool": {"freellmpool": 0, "deepseek": 1, "main": 2, "utility": 3},
    }.get(prefer, {})
    return sorted(routes, key=lambda r: order.get(r.name, 9))


def deep_available() -> bool:
    """Se o fluxo Pensador->Juiz->Síntese pode rodar com segurança agora."""
    if not _env_bool("LUMINUS_NPC_DEEP", True):
        return False
    if _has_remote():
        return bool(_routes())
    # Sem pool/DeepSeek: GPU utilitária, opt-in explícito de CPU ou o modelo
    # principal (27B) como pensador reserva.
    return (
        llm.utility_available()
        or _allow_cpu()
        or (
            _env_bool("LUMINUS_NPC_ALLOW_MAIN", False)
            and llm.primary_ok()
        )
    )


def status() -> Dict[str, Any]:
    routes = _routes()
    return {
        "enabled": _env_bool("LUMINUS_NPC_DEEP", True),
        "judge": _env_bool("LUMINUS_NPC_JUDGE", True),
        "deep_available": deep_available(),
        "pool_configured": bool(_pool_url()),
        "allow_cpu": _allow_cpu(),
        "allow_main": _env_bool("LUMINUS_NPC_ALLOW_MAIN", False),
        "main_model": _main_model(),
        "quality_mode": _quality_mode(),
        "thinker_candidates": _thinker_candidates(),
        "thinker_grace": _thinker_grace(),
        "pool_attempts": _pool_attempts(),
        "pool_timeout": _pool_timeout(),
        "synth_route": _synth_prefer(),
        "routes": [
            {"name": route.name, "kind": route.kind, "model": route.model}
            for route in routes
        ],
    }


# ─── HTTP OpenAI-compatible (freellmpool / DeepSeek) ────────────────────────


def _with_schema(messages: List[dict], schema: Dict[str, Any]) -> List[dict]:
    """Injeta o JSON Schema no prompt das rotas remotas.

    O Ollama local recebe ``fmt=schema``; um endpoint OpenAI-compatible não.
    Sem isso o modelo gratuito inventa nomes de campos (ex.: juiz em
    português) e o parecer chega fora do contrato.
    """
    if not schema:
        return list(messages)
    note = (
        "FORMATO OBRIGATÓRIO: responda APENAS com um objeto JSON válido neste "
        "JSON Schema. Use exatamente os nomes de campos abaixo, sem traduzir, "
        "sem markdown e sem texto fora do JSON.\n"
        + json.dumps(schema, ensure_ascii=False)
    )
    out = list(messages)
    index = 1 if out and out[0].get("role") == "system" else 0
    out.insert(index, {"role": "system", "content": note})
    return out


async def _openai_chat(
    route: Route,
    messages: List[dict],
    schema: Dict[str, Any],
    temperature: float,
    max_tokens: int,
    timeout: float,
) -> str:
    url = f"{(route.base or '').rstrip('/')}/chat/completions"
    payload: Dict[str, Any] = {
        "model": route.model,
        "messages": _with_schema(messages, schema),
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if route.task:
        # Extensão aceita pelo freellmpool para roteamento por intenção.
        payload["task"] = route.task
    if route.kind == "deepseek":
        payload["response_format"] = {"type": "json_object"}

    headers = {"Content-Type": "application/json"}
    if route.api_key:
        headers["Authorization"] = f"Bearer {route.api_key}"

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            body = (response.text or "")[:400]
            raise NpcForgeError(
                f"{route.name}: HTTP {response.status_code} {body}"
            )
        data = response.json()

    try:
        return str(data["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise NpcForgeError(f"{route.name}: resposta sem choices válidos") from exc


async def _local_chat(
    route: Route,
    messages: List[dict],
    schema: Dict[str, Any],
    temperature: float,
    max_tokens: int,
) -> str:
    return await llm.complete(
        route.model,
        messages,
        False,
        {
            "temperature": temperature,
            "top_p": 0.92,
            "num_ctx": 8192,
            "num_predict": max_tokens,
        },
        fmt=schema,
        base=route.base,
        force_utility=route.force_utility,
    )


async def _call_route(
    route: Route,
    messages: List[dict],
    schema: Dict[str, Any],
    temperature: float,
    max_tokens: int,
    timeout: float,
) -> str:
    if route.kind in ("pool", "deepseek"):
        return await _openai_chat(
            route, messages, schema, temperature, max_tokens, timeout
        )
    return await _local_chat(route, messages, schema, temperature, max_tokens)


async def _complete(
    messages: List[dict],
    schema: Dict[str, Any],
    temperature: float,
    max_tokens: int,
    stage: str,
    prefer: str = "",
) -> Tuple[str, str]:
    """Tenta as rotas em ordem; devolve (texto, nome_da_rota_que_respondeu)."""
    routes = _ordered_routes(prefer)
    if not routes:
        raise NpcForgeError("nenhuma rota de modelo configurada")
    timeout = _stage_timeout()
    errors: List[str] = []
    for route in routes:
        started = time.perf_counter()
        attempts = _pool_attempts() if route.kind == "pool" else 1
        call_timeout = _pool_timeout() if route.kind == "pool" else timeout
        last_exc: Optional[Exception] = None
        try:
            text = ""
            for attempt in range(1, attempts + 1):
                if attempt > 1:
                    metrics.inc("npc_forge_pool_retries")
                    await asyncio.sleep(min(4.0, float(attempt)))
                attempt_started = time.perf_counter()
                try:
                    text = await asyncio.wait_for(
                        _call_route(
                            route, messages, schema, temperature, max_tokens,
                            call_timeout,
                        ),
                        timeout=call_timeout + 5.0,
                    )
                    if not (text or "").strip():
                        raise NpcForgeError("resposta vazia")
                    last_exc = None
                    break
                except Exception as exc:  # noqa: BLE001 - retry/failover
                    last_exc = exc
                    print(
                        f"[npc-forge] {stage}: {route.name} tentativa "
                        f"{attempt}/{attempts} falhou em "
                        f"{time.perf_counter() - attempt_started:.1f}s ({exc})",
                        flush=True,
                    )
            if last_exc is not None:
                raise last_exc
            elapsed = time.perf_counter() - started
            print(
                f"[npc-forge] {stage}: {route.name}/{route.model} "
                f"em {elapsed:.1f}s ({len(text)} chars)",
                flush=True,
            )
            return text, route.name
        except Exception as exc:  # noqa: BLE001 - failover é o objetivo
            elapsed = time.perf_counter() - started
            errors.append(f"{route.name}: {exc}")
            print(
                f"[npc-forge] {stage}: rota {route.name} falhou em "
                f"{elapsed:.1f}s ({exc}); tentando a próxima",
                flush=True,
            )
    raise NpcForgeError(
        f"{stage}: todas as rotas falharam: " + " | ".join(errors)
    )


# ─── JSON ───────────────────────────────────────────────────────────────────


def _extract_json(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise NpcForgeError("resposta sem objeto JSON")
    data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise NpcForgeError("JSON não é um objeto")
    return data


_REQUIRED = list(prompts.CHARACTER_SCHEMA["required"])


def _normalize_draft(draft: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in _REQUIRED:
        value = draft.get(key)
        if key == "tags":
            if isinstance(value, str):
                value = [item.strip() for item in value.split(",") if item.strip()]
            if not isinstance(value, list):
                value = []
            out[key] = [str(item).strip()[:40] for item in value if str(item).strip()][:8]
        else:
            out[key] = "" if value is None else str(value).strip()
    return out


def _missing(draft: Dict[str, Any]) -> List[str]:
    missing = []
    for key in _REQUIRED:
        if key == "tags":
            if not draft.get(key):
                missing.append(key)
        elif not str(draft.get(key) or "").strip():
            missing.append(key)
    return missing


def _valid_judge(value: Any) -> bool:
    """Confere se o parecer do Juiz respeitou o JUDGE_SCHEMA.

    Modelos gratuitos às vezes devolvem as chaves em português ("aprovado",
    "issues"...) ou omitem "score". O parecer é opcional, mas um parecer
    torto não pode entrar no prompt da Síntese como se fosse válido.
    """
    if not isinstance(value, dict):
        return False
    if not isinstance(value.get("ok"), bool):
        return False
    score = value.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return False
    for key in ("issues", "missing"):
        if not isinstance(value.get(key), list):
            return False
    for key in ("strongest", "verdict"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            return False
    return True


# ─── Prompts do painel ──────────────────────────────────────────────────────

THINKER_SYSTEM = """\
Você é o PENSADOR do Luminus. Sua função NÃO é escrever a ficha final: é abrir
um dossiê interno profundo sobre um personagem de RPG narrativo adulto (PT-BR,
2a pessoa, estilo visual novel). Pense como roteirista + psicólogo + ator.

O dossiê precisa dar munição para a cena, não encher linguiça. Trabalhe:
- Núcleo: desejo consciente x necessidade inconsciente; ferida, vergonha, orgulho.
- Contradição produtiva: duas forças que puxam o personagem para lados opostos.
- Economia de segredos: o que esconde, por quê, e o que revelaria em cada nível de vínculo.
- Voz: ritmo, vocabulário, tiques, o que evita dizer, como mente.
- Agência: objetivos de curto/longo prazo; o que faria se o jogador não existisse.
- Atrito: como cria conflito com o jogador e com o mundo sem ser vilão de papelão.
- Ganchos: 3 ações/falas futuras que o Narrador pode usar.
- Aparência: detalhes sensoriais que revelam algo (não um catálogo de roupas).
- Anti-clichê: 2 desvios concretos do óbvio para ESTE personagem.
- Perguntas abertas: 2 ambiguidades férteis para o jogo explorar.
- Segurança: se a premissa for adulta, trate com maturidade, consentimento e tom;
  não transforme trauma em espetáculo gratuito.

Se o usuário deu pouco, invente com coerência e bom gosto. Não contradiga o que
ele escreveu. Responda APENAS com JSON válido no schema fornecido.
"""

THINKER_SCHEMA = {
    "type": "object",
    "properties": {
        "core": {"type": "string"},
        "psychology": {"type": "string"},
        "contradiction": {"type": "string"},
        "secret_economy": {"type": "string"},
        "voice": {"type": "string"},
        "agency": {"type": "string"},
        "friction": {"type": "string"},
        "narrative_hooks": {"type": "array", "items": {"type": "string"}},
        "appearance_cues": {"type": "string"},
        "anti_cliche": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "dossier": {"type": "string"},
    },
    "required": [
        "core", "psychology", "contradiction", "secret_economy", "voice",
        "agency", "friction", "narrative_hooks", "appearance_cues",
        "anti_cliche", "open_questions", "dossier",
    ],
}

JUDGE_SYSTEM = """\
Você é o JUIZ de personagens do Luminus. Recebe a ideia do jogador, o mundo e o
dossiê do Pensador. Sua função é apertar o dossiê antes da ficha final.

Procure defeitos REAIS, sem inventar:
- contradição interna (desejo x necessidade, passado x comportamento);
- clichê vazio, personagem passivo, voz genérica ou "explicada" demais;
- segredo sem custo/risco, medo sem gatilho concreto, motivação sem stakes;
- incoerência com o mundo/tom pedido; exposição que o Narrador não conseguiria mostrar;
- ganchos fracos ou repetitivos; aparência sem função dramática;
- conteúdo adulto tratado de forma gratuita ou fora do tom.

Para cada problema, diga o campo, a gravidade e uma correção CONCRETA que o
Sintetizador possa aplicar. Se o dossiê estiver bom, aprove sem inflar issues.
Responda APENAS com JSON válido no schema fornecido.
"""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "score": {"type": "number"},
        "strongest": {"type": "string"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "campo": {"type": "string"},
                    "gravidade": {
                        "type": "string",
                        "enum": ["baixa", "media", "alta"],
                    },
                    "problema": {"type": "string"},
                    "correcao": {"type": "string"},
                },
                "required": ["campo", "gravidade", "problema", "correcao"],
            },
        },
        "missing": {"type": "array", "items": {"type": "string"}},
        "verdict": {"type": "string"},
    },
    "required": ["ok", "score", "strongest", "issues", "missing", "verdict"],
}

SYNTH_SYSTEM = """\
Você é o SINTETIZADOR do Luminus. Transforme o dossiê do Pensador e o parecer
do Juiz em UMA ficha de personagem pronta para o app.

Regras:
- Preserve a ideia original do jogador; incorpore as correções do Juiz.
- Tons de cinza: qualidades, defeitos, desejos, medos e contradições. Nada de
  vilão de papelão nem santo sem fissura.
- "personality": mostre a contradição e como ela aparece em cena.
- "backstory": só o que explica o presente e cria ganchos; não escreva biografia.
- "speech_style": ritmo, vocabulário, tiques, formalidade e o que a pessoa evita.
- "greeting": cena/fala inicial em 2a pessoa, ação entre *asteriscos* e diálogo
  entre aspas. Não fale/decida pelo jogador.
- "example_dialogue": 2 a 4 falas que só funcionariam com ESTE personagem.
- "appearance": detalhes sensoriais com função, não catálogo.
- "tags": 3 a 6 tags curtas e úteis.
- Se a premissa for adulta/NSFW, mantenha o tom com maturidade e consentimento;
  não adicione conteúdo explícito que o jogador não pediu.
- Nada de meta-comentário, markdown ou texto fora do JSON.
Responda APENAS com JSON válido no schema fornecido.
"""


def _brief(seed, kind, tone, world_name, world_desc, name) -> str:
    text = prompts.creation_prompt(
        seed, kind, tone, world_name, world_desc, name
    )
    return text[:6000]


async def _collect_dossiers(
    messages: List[dict], candidates: int
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Roda os Pensadores e fica com o 1º dossiê válido.

    Os candidatos extras ganham só uma janela de tolerância depois do primeiro
    sucesso. Assim um provedor lento não segura a criação inteira quando já há
    material suficiente para o Juiz/Síntese.
    """
    stages = [
        f"pensador{index + 1}" if candidates > 1 else "pensador"
        for index in range(candidates)
    ]
    tasks = {
        asyncio.create_task(
            _complete(
                messages,
                THINKER_SCHEMA,
                0.72 + 0.08 * index,
                1400,
                stages[index],
                prefer="pool",
            )
        ): stages[index]
        for index in range(candidates)
    }
    pending = set(tasks)
    dossiers: List[Dict[str, Any]] = []
    routes: List[str] = []
    grace = _thinker_grace()
    cancelled = 0
    try:
        while pending:
            timeout = grace if dossiers else None
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED, timeout=timeout
            )
            if not done:
                break
            for task in done:
                stage = tasks[task]
                if task.cancelled():
                    continue
                exc = task.exception()
                if exc is not None:
                    print(f"[npc-forge] {stage} descartado ({exc})", flush=True)
                    continue
                raw, route = task.result()
                try:
                    dossiers.append(_extract_json(raw))
                except Exception as parse_exc:  # noqa: BLE001
                    print(
                        f"[npc-forge] {stage}: dossiê inválido descartado "
                        f"({parse_exc})",
                        flush=True,
                    )
                    continue
                routes.append(route)
                print(
                    f"[npc-forge] {stage}: dossiê válido via {route} "
                    f"({len(dossiers)} no total)",
                    flush=True,
                )
    finally:
        for task in pending:
            task.cancel()
            cancelled += 1
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if cancelled:
            print(
                f"[npc-forge] {cancelled} pensador(es) cancelado(s) após "
                f"{grace:g}s de tolerância",
                flush=True,
            )
    return dossiers, routes


async def forge(
    seed: str,
    kind: str = "npc",
    tone: str = "",
    world_name: str = "",
    world_desc: str = "",
    name: str = "",
    use_judge: Optional[bool] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Executa Pensador -> (Juiz) -> Síntese e devolve (ficha, metadados)."""
    started = time.perf_counter()
    if not deep_available():
        raise NpcForgeError(
            "criação profunda indisponível: configure LUMINUS_NPC_POOL_URL, "
            "ative DeepSeek ou libere a GPU utilitária"
        )
    judge_enabled = (
        _env_bool("LUMINUS_NPC_JUDGE", True)
        if use_judge is None
        else bool(use_judge)
    )
    brief = _brief(seed, kind, tone, world_name, world_desc, name)

    metrics.inc("npc_forge_runs")
    stages: Dict[str, Any] = {}

    thinker_messages = [
        {"role": "system", "content": THINKER_SYSTEM},
        {
            "role": "user",
            "content": (
                "BRIEF DO JOGADOR:\n" + brief
                + "\n\nAbra o dossiê interno deste personagem."
            ),
        },
    ]
    candidates = _thinker_candidates()
    dossiers, thinker_routes = await _collect_dossiers(
        thinker_messages, candidates
    )
    if not dossiers:
        raise NpcForgeError("pensador: nenhum dossiê válido")
    thinker_route = thinker_routes[0]
    dossier_payload: Any = dossiers[0] if len(dossiers) == 1 else dossiers
    dossier_label = (
        "DOSSIÊS DOS PENSADORES (JSON)"
        if len(dossiers) > 1
        else "DOSSIÊ DO PENSADOR (JSON)"
    )
    multi_note = (
        "\n\nHá vários dossiês independentes. Combine as melhores ideias, "
        "resolva contradições e mantenha coerência."
        if len(dossiers) > 1
        else ""
    )
    stages["thinker"] = {"route": thinker_route, "dossier": dossier_payload}
    stages["thinkers"] = [
        {"route": route, "dossier": dossier}
        for route, dossier in zip(thinker_routes, dossiers)
    ]

    judge = None
    judge_route = ""
    if judge_enabled:
        judge_messages = [
            {"role": "system", "content": JUDGE_SYSTEM},
            {
                "role": "user",
                "content": (
                    "BRIEF DO JOGADOR:\n" + brief
                    + "\n\n" + dossier_label + ":\n"
                    + json.dumps(dossier_payload, ensure_ascii=False)[:9000]
                    + multi_note
                    + "\n\nAvalie o material para a ficha final."
                ),
            },
        ]
        try:
            judge_raw, judge_route = await _complete(
                judge_messages, JUDGE_SCHEMA, 0.2, 900, "juiz",
                prefer="pool",
            )
            candidate = _extract_json(judge_raw)
        except Exception as exc:  # noqa: BLE001 - juiz é opcional
            print(
                f"[npc-forge] juiz indisponível ({exc}); seguindo sem parecer",
                flush=True,
            )
        else:
            if _valid_judge(candidate):
                judge = candidate
                stages["judge"] = {"route": judge_route, "verdict": judge}
            else:
                print(
                    f"[npc-forge] juiz: parecer fora do schema via "
                    f"{judge_route}; pedindo segunda opinião",
                    flush=True,
                )
                try:
                    judge_raw, judge_route = await _complete(
                        judge_messages, JUDGE_SCHEMA, 0.2, 900, "juiz",
                        prefer="utility",
                    )
                    candidate = _extract_json(judge_raw)
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"[npc-forge] juiz sem segunda opinião ({exc})",
                        flush=True,
                    )
                else:
                    if _valid_judge(candidate):
                        judge = candidate
                        stages["judge"] = {
                            "route": judge_route,
                            "verdict": judge,
                        }
                    else:
                        print(
                            "[npc-forge] juiz: segunda opinião também fora "
                            "do schema; seguindo sem parecer",
                            flush=True,
                        )

    synthed = None
    synth_route = ""
    last_error: Optional[Exception] = None
    for attempt in range(2):
        correction = ""
        if judge:
            correction = (
                "\n\nPARECER DO JUIZ (JSON):\n"
                + json.dumps(judge, ensure_ascii=False)[:4000]
                + "\n\nCorrija os problemas de gravidade média/alta. "
                "Mantenha o que o Juiz elogiou."
            )
        if attempt and synthed:
            missing = _missing(synthed)
            correction += (
                "\n\nA tentativa anterior esqueceu/zerou: "
                + ", ".join(missing)
                + ". Preencha todos os campos do schema."
            )
        synth_messages = [
            {"role": "system", "content": SYNTH_SYSTEM},
            {
                "role": "user",
                "content": (
                    "BRIEF DO JOGADOR:\n" + brief
                    + "\n\n" + dossier_label + ":\n"
                    + json.dumps(dossier_payload, ensure_ascii=False)[:9000]
                    + multi_note
                    + correction
                    + "\n\nGere a ficha final."
                ),
            },
        ]
        try:
            synth_raw, synth_route = await _complete(
                synth_messages,
                prompts.CHARACTER_SCHEMA,
                0.72,
                1600,
                "sintese",
                prefer=_synth_prefer(),
            )
            synthed = _normalize_draft(_extract_json(synth_raw))
            if not _missing(synthed):
                break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"[npc-forge] síntese tentativa {attempt + 1} falhou: {exc}", flush=True)

    if synthed is None:
        metrics.inc("npc_forge_errors")
        raise NpcForgeError(f"síntese falhou: {last_error}")

    missing = _missing(synthed)
    if missing:
        print(f"[npc-forge] campos vazios após síntese: {missing}", flush=True)

    stages["synthesizer"] = {"route": synth_route}

    metrics.observe("npc_forge_seconds", time.perf_counter() - started)
    info = {
        "engine": "freellmpool-inspired",
        "quality_mode": _quality_mode(),
        "thinker_route": thinker_route,
        "thinker_routes": thinker_routes,
        "thinker_candidates": candidates,
        "dossiers": len(dossiers),
        "judge_route": judge_route,
        "synthesizer_route": synth_route,
        "synth_prefer": _synth_prefer(),
        "judge": judge,
        "missing": missing,
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "routes": status()["routes"],
    }
    return synthed, info
