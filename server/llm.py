import asyncio
import json
import os
import threading
import time
from typing import AsyncIterator, Callable, List, Optional, Tuple

import httpx

from gpu_guard import is_gpu_error

OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_UTILITY = os.environ.get("LUMINUS_UTILITY_URL", "http://127.0.0.1:11435")
DEFAULT_MODEL = os.environ.get("LUMINUS_MODEL", "luminus-27b")
UTILITY_MODEL = os.environ.get("LUMINUS_UTILITY_MODEL", "qwen38-9b-heretic-q5")
FALLBACK_MODEL = os.environ.get("LUMINUS_FALLBACK_MODEL", "").strip()
FALLBACK_URL = os.environ.get("LUMINUS_FALLBACK_URL", "").strip()


# Watchdog de streaming: se o llama-server morrer no meio de um turno a
# conexao HTTP pode ficar pendurada para sempre, travando o app. Primeiro
# token pode demorar (carga do modelo); depois disso, qualquer silencio maior
# que STREAM_IDLE_TIMEOUT e tratado como falha.
STREAM_FIRST_TIMEOUT = float(os.environ.get("LUMINUS_STREAM_FIRST_TIMEOUT", "300"))
STREAM_IDLE_TIMEOUT = float(os.environ.get("LUMINUS_STREAM_IDLE_TIMEOUT", "60"))
COMPLETE_TIMEOUT = float(os.environ.get("LUMINUS_COMPLETE_TIMEOUT", "300"))


KEEP_ALIVE = os.environ.get("LUMINUS_KEEP_ALIVE", "30m")
SKIP_PRIMARY = os.environ.get("LUMINUS_SKIP_PRIMARY", "0") == "1"

_primary_ok = not SKIP_PRIMARY
_cpu_mode = False
_utility_ok = True

# Modelo menor que cabe nas GPUs principais restantes. Preenchido pelo
# main.py com o valor do gpu_guard; usado para tentar um novo modelo no MESMO
# turno quando o principal falha por CUDA/VRAM.
_main_failover_model = ""

# Hook opcional (síncrono) chamado quando detectamos erro de GPU/CUDA.
_gpu_error_hook: Optional[Callable[[dict], None]] = None

# ── DeepSeek como Narrador de emergência ────────────────────────────────────
# Só entra quando TODAS as rotas locais falham (Ollama fora, contexto CUDA
# envenenado, GPUs perdidas). É a última candidata de `_candidates`, exceto
# quando não há GPU principal nem utilitária — aí vem antes do fallback CPU.
DEEPSEEK_BASE = "deepseek://"
_DEEPSEEK_PEAK_HOURS_UTC = ((1, 4), (6, 10))
_deepseek_usage_lock = threading.Lock()
_deepseek_usage = {
    "requests": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "cache_hit_tokens": 0,
    "cost_usd": 0.0,
    "last_at": 0.0,
}
_deepseek_budget_warned = False
_force_deepseek = False


def set_force_deepseek(value: bool) -> None:
    """Força o Narrador a usar DeepSeek (ignora os modelos locais)."""
    global _force_deepseek
    _force_deepseek = bool(value)


def force_deepseek() -> bool:
    return _force_deepseek


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "on", "yes")


def _deepseek_url() -> str:
    return os.environ.get(
        "LUMINUS_DEEPSEEK_URL", "https://api.deepseek.com/v1"
    ).rstrip("/")


def _deepseek_model() -> str:
    return (
        os.environ.get(
            "LUMINUS_DEEPSEEK_MODEL",
            os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
        ).strip()
        or "deepseek-flash"
    )


def _deepseek_key() -> str:
    return os.environ.get("DEEPSEEK_API_KEY", "").strip()


def _deepseek_peak() -> bool:
    tm = time.gmtime()
    return tm.tm_wday < 5 and any(
        start <= tm.tm_hour < end for start, end in _DEEPSEEK_PEAK_HOURS_UTC
    )


def _deepseek_prices() -> dict:
    peak = _deepseek_peak()
    return {
        "peak": peak,
        "input_cache_miss_per_m": 0.30 if peak else 0.15,
        "input_cache_hit_per_m": 0.006 if peak else 0.003,
        "output_per_m": 1.20 if peak else 0.60,
    }


def _deepseek_budget_usd() -> float:
    try:
        return float(os.environ.get("LUMINUS_DEEPSEEK_BUDGET_USD", "0") or 0)
    except ValueError:
        return 0.0


def _deepseek_budget_exceeded() -> bool:
    global _deepseek_budget_warned
    budget = _deepseek_budget_usd()
    if budget <= 0:
        return False
    if _deepseek_usage["cost_usd"] < budget:
        return False
    if not _deepseek_budget_warned:
        _deepseek_budget_warned = True
        print(
            f"[llm] orçamento DeepSeek esgotado "
            f"(US$ {_deepseek_usage['cost_usd']:.4f} / US$ {budget:.2f}); "
            "fallback remoto desativado até reiniciar.",
            flush=True,
        )
    return True


def _deepseek_enabled() -> bool:
    return (
        _env_bool("LUMINUS_DEEPSEEK_FALLBACK", True)
        and bool(_deepseek_key())
        and not _deepseek_budget_exceeded()
    )


def deepseek_status() -> dict:
    prices = _deepseek_prices()
    budget = _deepseek_budget_usd()
    usage = dict(_deepseek_usage)
    return {
        "enabled": _env_bool("LUMINUS_DEEPSEEK_FALLBACK", True),
        "configured": bool(_deepseek_key()),
        "active": _deepseek_enabled(),
        "model": _deepseek_model(),
        "forced": _force_deepseek,
        "budget_usd": budget,
        "budget_remaining_usd": (
            round(max(0.0, budget - usage["cost_usd"]), 4) if budget > 0 else None
        ),
        "prices": {
            "peak_now": prices["peak"],
            "input_cache_miss_per_m": prices["input_cache_miss_per_m"],
            "input_cache_hit_per_m": prices["input_cache_hit_per_m"],
            "output_per_m": prices["output_per_m"],
        },
        "usage": {
            **usage,
            "cost_usd": round(usage["cost_usd"], 6),
            "scope": "process",
        },
    }


def _record_deepseek_usage(
    usage: Optional[dict] = None,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
) -> None:
    usage = usage or {}
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    hit = usage.get("prompt_cache_hit_tokens")
    if not isinstance(prompt, int):
        prompt = int(prompt_tokens or 0)
    if not isinstance(completion, int):
        completion = int(completion_tokens or 0)
    if not isinstance(hit, int):
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict):
            hit = details.get("cached_tokens")
    if not isinstance(hit, int):
        hit = 0
    hit = max(0, min(hit, prompt))
    miss = max(0, prompt - hit)
    prices = _deepseek_prices()
    cost = (
        miss * prices["input_cache_miss_per_m"]
        + hit * prices["input_cache_hit_per_m"]
        + completion * prices["output_per_m"]
    ) / 1_000_000.0
    with _deepseek_usage_lock:
        _deepseek_usage["requests"] += 1
        _deepseek_usage["prompt_tokens"] += prompt
        _deepseek_usage["completion_tokens"] += completion
        _deepseek_usage["cache_hit_tokens"] += hit
        _deepseek_usage["cost_usd"] += cost
        _deepseek_usage["last_at"] = time.time()
        snapshot = dict(_deepseek_usage)
    print(
        f"[llm] DeepSeek usado: in={prompt} out={completion} cache_hit={hit} "
        f"custo≈US$ {cost:.5f} (acumulado≈US$ {snapshot['cost_usd']:.4f})",
        flush=True,
    )
    try:
        log_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"
        )
        os.makedirs(log_dir, exist_ok=True)
        with open(
            os.path.join(log_dir, "deepseek_usage.jsonl"), "a", encoding="utf-8"
        ) as handle:
            handle.write(
                json.dumps(
                    {
                        "timestamp": time.time(),
                        "model": _deepseek_model(),
                        "prompt_tokens": prompt,
                        "completion_tokens": completion,
                        "cache_hit_tokens": hit,
                        "cost_usd": round(cost, 8),
                        "peak": prices["peak"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass


def _deepseek_candidate() -> Optional[Tuple[str, str]]:
    if not _deepseek_enabled():
        return None
    return (_deepseek_model(), DEEPSEEK_BASE)


def _estimate_tokens(messages: List[dict]) -> int:
    chars = 0
    for message in messages:
        chars += len(str(message.get("content") or ""))
    return max(1, chars // 4)


def _deepseek_payload(
    model: str,
    messages: List[dict],
    stream: bool,
    options: Optional[dict] = None,
    fmt: dict | str | None = None,
) -> dict:
    opts = options or {}
    max_tokens = int(
        opts.get("num_predict")
        or opts.get("max_tokens")
        or 512
    )
    payload = {
        "model": model or _deepseek_model(),
        "messages": messages,
        "stream": stream,
        "temperature": float(opts.get("temperature", 0.85)),
        "max_tokens": max(1, min(max_tokens, 8192)),
    }
    if opts.get("top_p") is not None:
        payload["top_p"] = float(opts["top_p"])
    if not _env_bool("LUMINUS_DEEPSEEK_THINKING", False):
        payload["thinking"] = {"type": "disabled"}
    if fmt is not None:
        payload["response_format"] = {"type": "json_object"}
    stop = opts.get("stop")
    if isinstance(stop, list) and stop:
        payload["stop"] = [str(item) for item in stop][:8]
    return payload


def _deepseek_headers() -> dict:
    return {
        "Authorization": f"Bearer {_deepseek_key()}",
        "Content-Type": "application/json",
    }


def _parse_deepseek_sse(line: str) -> Tuple[str, Optional[dict], bool, str]:
    """Interpreta uma linha SSE do DeepSeek: (texto, usage, terminou, motivo)."""
    line = (line or "").strip()
    if not line or line.startswith(":"):
        return "", None, False, ""
    if line.startswith("data:"):
        line = line[5:].strip()
    if line == "[DONE]":
        return "", None, True, "stop"
    try:
        part = json.loads(line)
    except ValueError:
        return "", None, False, ""
    usage = part.get("usage") if isinstance(part.get("usage"), dict) else None
    choices = part.get("choices") or []
    if not choices:
        return "", usage, False, ""
    delta = choices[0].get("delta") or {}
    text = delta.get("content") or ""
    reason = str(choices[0].get("finish_reason") or "")
    done = bool(reason)
    return text, usage, done, reason


async def _stream_deepseek(
    model: str, messages: List[dict], options: Optional[dict] = None
) -> AsyncIterator[str]:
    if not _deepseek_key():
        raise RuntimeError("DEEPSEEK_API_KEY ausente")
    started = time.perf_counter()
    prompt_est = _estimate_tokens(messages)
    chars = 0
    usage = None
    payload = _deepseek_payload(model, messages, True, options)
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream(
            "POST",
            f"{_deepseek_url()}/chat/completions",
            json=payload,
            headers=_deepseek_headers(),
        ) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", "replace")
                raise RuntimeError(
                    f"DeepSeek HTTP {response.status_code}: {body[:500]}"
                )
            async for line in response.aiter_lines():
                text, chunk_usage, done, reason = _parse_deepseek_sse(line)
                if chunk_usage:
                    usage = chunk_usage
                if text:
                    chars += len(text)
                    yield text
                if done:
                    if isinstance(options, dict):
                        options["_done_reason"] = reason or "stop"
                    break
    if isinstance(options, dict) and not options.get("_done_reason"):
        options["_done_reason"] = "stop"
    if usage is None:
        _record_deepseek_usage(
            prompt_tokens=prompt_est,
            completion_tokens=max(1, chars // 4),
        )
    else:
        _record_deepseek_usage(usage=usage)
    if chars == 0:
        raise RuntimeError("DeepSeek retornou resposta vazia")
    print(
        f"[llm] DeepSeek respondeu em {time.perf_counter() - started:.1f}s "
        f"({chars} chars).",
        flush=True,
    )


async def _complete_deepseek(
    model: str,
    messages: List[dict],
    options: Optional[dict] = None,
    fmt: dict | str | None = None,
) -> str:
    if not _deepseek_key():
        raise RuntimeError("DEEPSEEK_API_KEY ausente")
    payload = _deepseek_payload(model, messages, False, options, fmt)
    async with httpx.AsyncClient(timeout=None) as client:
        response = await client.post(
            f"{_deepseek_url()}/chat/completions",
            json=payload,
            headers=_deepseek_headers(),
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"DeepSeek HTTP {response.status_code}: {response.text[:500]}"
            )
        data = response.json()
    try:
        content = str(data["choices"][0]["message"].get("content") or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("DeepSeek: resposta sem choices válidos") from exc
    _record_deepseek_usage(
        usage=data.get("usage") if isinstance(data.get("usage"), dict) else None,
        prompt_tokens=_estimate_tokens(messages),
        completion_tokens=max(1, len(content) // 4),
    )
    if not content.strip():
        raise RuntimeError("DeepSeek retornou resposta vazia")
    return content


def disable_primary() -> None:
    global _primary_ok
    _primary_ok = False


def disable_primary_permanently() -> None:
    global SKIP_PRIMARY, _primary_ok
    SKIP_PRIMARY = True
    _primary_ok = False


def reset_primary() -> None:
    global _primary_ok
    _primary_ok = not SKIP_PRIMARY


def primary_ok() -> bool:
    return _primary_ok


def set_cpu_mode(value: bool) -> None:
    global _cpu_mode
    _cpu_mode = value


def cpu_mode() -> bool:
    return _cpu_mode


def set_utility_available(value: bool) -> None:
    """Indica se a GPU utilitária (RTX 2060) está saudável.

    Quando ela cai, as chamadas que pedem ``OLLAMA_UTILITY`` são
    reencaminhadas automaticamente para o Ollama principal (mesmo acervo de
    modelos), preservando planner/resumo/capítulos.
    """
    global _utility_ok
    _utility_ok = bool(value)


def utility_available() -> bool:
    return _utility_ok


def utility_base() -> str:
    """URL dos modelos auxiliares (sempre a instância utilitária).

    Quando ``utility_available()`` é falso, os chamadores devem desistir do
    recurso auxiliar em vez de usar o Ollama principal: carregar outro modelo
    ali pode despejar o Narrador da VRAM.
    """
    return OLLAMA_UTILITY


def set_main_failover_model(model: str) -> None:
    """Modelo menor para tentar nas GPUs principais quando o principal falhar."""
    global _main_failover_model
    _main_failover_model = (model or "").strip()


def set_gpu_error_hook(hook: Optional[Callable[[dict], None]]) -> None:
    global _gpu_error_hook
    _gpu_error_hook = hook


def _is_utility(base: str | None) -> bool:
    return bool(base) and base.rstrip("/") == OLLAMA_UTILITY.rstrip("/")


def _effective_base(base: str | None) -> str | None:
    """Reserva do Narrador: se a GPU utilitária caiu, usa o Ollama principal."""
    if base is None:
        return None
    if not _utility_ok and _is_utility(base):
        return OLLAMA
    return base


def _notify_gpu_error(
    model: str, detail: str, base: str | None = None
) -> None:
    print(
        f"[llm] erro de GPU/CUDA no modelo {model}: {detail[:240]}",
        flush=True,
    )
    if _gpu_error_hook is None:
        return
    try:
        _gpu_error_hook(
            {
                "model": model,
                "detail": detail,
                "base": base,
                "at": time.time(),
            }
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[llm] hook de erro de GPU falhou: {exc}", flush=True)


async def _exception_detail(exc: BaseException) -> str:
    """Inclui o corpo da resposta HTTP (onde o Ollama escreve o erro CUDA)."""
    parts = [f"{type(exc).__name__}: {exc}"]
    response = getattr(exc, "response", None)
    if response is not None:
        text = ""
        try:
            text = response.text
        except Exception:  # noqa: BLE001
            pass
        if not text:
            try:
                raw = await response.aread()
                text = raw.decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
        if text:
            parts.append(text[:2000])
    return " | ".join(parts)


def _candidates(model: str, base: str | None = None) -> List[tuple[str, str | None]]:
    first = (model or DEFAULT_MODEL).strip()
    if base is not None:
        return [(first, base)]

    ds = _deepseek_candidate()
    if _force_deepseek:
        if not ds:
            raise RuntimeError(
                "DeepSeek forçado, mas a chave/limite não está configurado"
            )
        return [ds]

    # Sem GPU principal e sem GPU utilitária: o DeepSeek vem antes de qualquer
    # fallback de CPU (que seria lentíssimo). Se o DeepSeek também falhar, o
    # CPU ainda fica como último recurso.
    if not _primary_ok and not _utility_ok:
        out: List[tuple[str, str | None]] = []
        if ds:
            out.append(ds)
        if FALLBACK_MODEL:
            out.append(
                (FALLBACK_MODEL, _effective_base(FALLBACK_URL) or OLLAMA)
            )
        else:
            out.append((UTILITY_MODEL, _effective_base(OLLAMA_UTILITY) or OLLAMA))
        return out

    out: List[tuple[str, str | None]] = []
    if _primary_ok:
        out.append((first, None))
        # Failover imediato no mesmo pool de GPUs (2 GPUs -> modelo menor).
        # Só faz sentido quando o modelo pedido é o principal; se o pedido já
        # é o modelo menor, não "subimos" para um modelo maior.
        if (
            _main_failover_model
            and _same_model(first, DEFAULT_MODEL)
            and not any(
                _same_model(_main_failover_model, existing[0]) for existing in out
            )
        ):
            out.append((_main_failover_model, None))
        fallback = None
        if FALLBACK_MODEL:
            fallback = (
                FALLBACK_MODEL,
                _effective_base(FALLBACK_URL) or OLLAMA,
            )
        elif UTILITY_MODEL:
            # Reserva padrão: GPU utilitária (quando ela existe).
            fallback = (UTILITY_MODEL, OLLAMA_UTILITY)
        add_fallback = bool(fallback) and (
            _utility_ok or not ds
        )
        if add_fallback and not any(
            _same_model(fallback[0], existing[0])
            and (fallback[1] or OLLAMA) == (existing[1] or OLLAMA)
            for existing in out
        ):
            out.append(fallback)
        if ds:
            out.append(ds)
        # Se a GPU utilitária caiu, o fallback "local" acabaria na CPU do
        # Ollama principal. Deixa isso só como último recurso, depois do
        # DeepSeek.
        if fallback and not add_fallback and not any(
            _same_model(fallback[0], existing[0])
            and (fallback[1] or OLLAMA) == (existing[1] or OLLAMA)
            for existing in out
        ):
            out.append(fallback)
        return out

    local = None
    if FALLBACK_MODEL:
        local = (FALLBACK_MODEL, _effective_base(FALLBACK_URL) or OLLAMA)
    else:
        local = (UTILITY_MODEL, _effective_base(OLLAMA_UTILITY) or OLLAMA)
    if local:
        out.append(local)
    if ds:
        out.append(ds)
    return out

def _payload(model, messages, stream, think, options, fmt=None):
    p = {
        "model": model or DEFAULT_MODEL,
        "messages": messages,
        "stream": stream,
        "think": think,
        "keep_alive": KEEP_ALIVE,
        "options": options or {},
    }
    if fmt is not None:
        p["format"] = fmt
    return p


async def list_models() -> List[str]:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{OLLAMA}/api/tags")
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]


async def loaded(base: str | None = None) -> List[str]:
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get(f"{base or OLLAMA}/api/ps")
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]


def _same_model(a: str, b: str) -> bool:
    return a.split(":")[0] == b.split(":")[0]


async def model_vram(model: str, base: str | None = None) -> int | None:
    """VRAM em bytes usada pelo modelo carregado; None se ele nao esta carregado."""
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{base or OLLAMA}/api/ps")
            r.raise_for_status()
            for item in r.json().get("models", []):
                if _same_model(item.get("name", ""), model):
                    return int(item.get("size_vram") or 0)
    except Exception:
        pass
    return None


async def unload(model: str, base: str | None = None) -> None:
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            await c.post(
                f"{base or OLLAMA}/api/generate",
                json={"model": model, "keep_alive": 0},
            )
    except Exception:
        pass


async def warm(model: str, num_ctx: int, base: str | None = None) -> None:
    if _is_utility(base) and not _utility_ok:
        raise RuntimeError("GPU utilitária indisponível; warmup cancelado")
    async with httpx.AsyncClient(timeout=600) as c:
        r = await c.post(
            f"{base or OLLAMA}/api/generate",
            json={
                "model": model,
                "prompt": "ok",
                "stream": False,
                "keep_alive": KEEP_ALIVE,
                "options": {"num_predict": 1, "num_ctx": num_ctx},
            },
        )
        r.raise_for_status()


_RETRY_ERRORS = (
    httpx.ConnectError,
    httpx.TimeoutException,
    httpx.ReadError,
    httpx.RemoteProtocolError,
    httpx.HTTPStatusError,
)
# Erros levantados pelos adaptadores (ex.: DeepSeek HTTP 401/429) também
# precisam acionar o próximo candidato/registro, em vez de estourar cru.
_ATTEMPT_ERRORS = _RETRY_ERRORS + (RuntimeError,)


async def _iter_lines_watchdog(
    lines: AsyncIterator[str],
    first_timeout: float = STREAM_FIRST_TIMEOUT,
    idle_timeout: float = STREAM_IDLE_TIMEOUT,
) -> AsyncIterator[str]:
    """Repassa linhas do stream, abortando se o servidor ficar mudo."""
    timeout = first_timeout if first_timeout > 0 else None
    while True:
        try:
            if timeout is None:
                line = await lines.__anext__()
            else:
                line = await asyncio.wait_for(lines.__anext__(), timeout=timeout)
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                "o modelo parou de responder "
                f"({timeout:.0f}s sem dados do llama-server)"
            ) from exc
        yield line
        timeout = idle_timeout if idle_timeout > 0 else None


async def _stream_one(
    model: str,
    base: str | None,
    messages: List[dict],
    think: bool,
    options: dict | None,
) -> AsyncIterator[str]:
    if base == DEEPSEEK_BASE:
        async for delta in _stream_deepseek(model, messages, options):
            yield delta
        return
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream(
            "POST",
            f"{base or OLLAMA}/api/chat",
            json=_payload(model, messages, True, think, options),
        ) as r:
            r.raise_for_status()
            async for line in _iter_lines_watchdog(r.aiter_lines()):
                if not line:
                    continue
                try:
                    part = json.loads(line)
                except ValueError:
                    continue
                delta = part.get("message", {}).get("content", "")
                if delta:
                    yield delta
                if part.get("done"):
                    if isinstance(options, dict):
                        options["_done_reason"] = (
                            str(part.get("done_reason") or "stop")
                        )
                    break
    if isinstance(options, dict) and not options.get("_done_reason"):
        options["_done_reason"] = "stop"


async def _check_vram_cpu_fallback(model: str) -> None:
    """Se o modelo principal subiu 100% na CPU, degrada e avisa o vigia."""
    if not _primary_ok:
        return
    vram = await model_vram(model or DEFAULT_MODEL, OLLAMA)
    if vram == 0:
        detail = "modelo principal carregado 100% na CPU (sem VRAM)"
        print(f"[llm] {detail}; usando fallback", flush=True)
        _notify_gpu_error(model or DEFAULT_MODEL, detail, OLLAMA)
        set_cpu_mode(True)
        disable_primary()


async def stream_chat(
    model: str,
    messages: List[dict],
    think: bool = False,
    options: dict | None = None,
    base: str | None = None,
    force_utility: bool = False,
) -> AsyncIterator[str]:
    if options is None:
        options = {}
    options["_done_reason"] = ""
    if base is None:
        await _check_vram_cpu_fallback(model)
    elif _is_utility(base) and not _utility_ok and not force_utility:
        raise RuntimeError(
            "GPU utilitária indisponível; chamada auxiliar ignorada"
        )

    candidates = _candidates(model, base)
    last_exc: BaseException | None = None
    skip_main = False

    for index, (candidate, candidate_base) in enumerate(candidates):
        if skip_main and candidate_base is None:
            continue
        emitted = False
        options["_done_reason"] = ""
        try:
            async for delta in _stream_one(
                candidate, candidate_base, messages, think, options
            ):
                emitted = True
                yield delta
            return
        except _ATTEMPT_ERRORS as exc:
            last_exc = exc
            detail = await _exception_detail(exc)
            gpu_error = (
                candidate_base != DEEPSEEK_BASE and is_gpu_error(detail)
            )
            if gpu_error:
                _notify_gpu_error(candidate, detail, candidate_base)

            if emitted:
                # Já entregamos texto ao jogador; reiniciar duplicaria a
                # narrativa. Propaga para o app e o vigia ajusta o próximo turno.
                if gpu_error:
                    print(
                        f"[llm] GPU falhou no meio do streaming de {candidate}; "
                        "abortando o turno atual.",
                        flush=True,
                    )
                raise

            server_down = isinstance(
                exc, (httpx.ConnectError, httpx.ConnectTimeout)
            )

            # Erro de GPU no pool principal: tenta o modelo menor no MESMO
            # pool antes de cair para a GPU utilitária.
            if (
                gpu_error
                and not server_down
                and candidate_base is None
                and index + 1 < len(candidates)
                and candidates[index + 1][1] is None
            ):
                await unload(candidate, candidate_base)
                nxt = candidates[index + 1][0]
                print(
                    f"[llm] trocando {candidate} -> {nxt} nas GPUs principais "
                    f"após erro de GPU ({detail[:160]})",
                    flush=True,
                )
                continue

            if candidate_base is None:
                disable_primary()
                skip_main = True
            if index + 1 >= len(candidates):
                raise
            nxt = candidates[index + 1][0]
            print(
                f"[llm] modelo {candidate} falhou ({exc}); tentando fallback {nxt}",
                flush=True,
            )

    if last_exc is not None:
        raise last_exc


async def complete(
    model: str,
    messages: List[dict],
    think: bool = False,
    options: dict | None = None,
    fmt: dict | str | None = None,
    base: str | None = None,
    force_utility: bool = False,
) -> str:
    if base is None:
        await _check_vram_cpu_fallback(model)
    elif _is_utility(base) and not _utility_ok and not force_utility:
        raise RuntimeError(
            "GPU utilitária indisponível; chamada auxiliar ignorada"
        )

    candidates = _candidates(model, base)
    last_exc: BaseException | None = None
    skip_main = False

    for index, (candidate, candidate_base) in enumerate(candidates):
        if skip_main and candidate_base is None:
            continue
        try:
            if candidate_base == DEEPSEEK_BASE:
                return await _complete_deepseek(
                    candidate, messages, options, fmt
                )
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=15.0,
                    read=COMPLETE_TIMEOUT,
                    write=COMPLETE_TIMEOUT,
                    pool=15.0,
                )
            ) as client:
                r = await client.post(
                    f"{candidate_base or OLLAMA}/api/chat",
                    json=_payload(candidate, messages, False, think, options, fmt),
                )
                r.raise_for_status()
                return r.json().get("message", {}).get("content", "")
        except _ATTEMPT_ERRORS as exc:
            last_exc = exc
            detail = await _exception_detail(exc)
            gpu_error = (
                candidate_base != DEEPSEEK_BASE and is_gpu_error(detail)
            )
            if gpu_error:
                _notify_gpu_error(candidate, detail, candidate_base)

            server_down = isinstance(
                exc, (httpx.ConnectError, httpx.ConnectTimeout)
            )

            if (
                gpu_error
                and not server_down
                and candidate_base is None
                and index + 1 < len(candidates)
                and candidates[index + 1][1] is None
            ):
                await unload(candidate, candidate_base)
                nxt = candidates[index + 1][0]
                print(
                    f"[llm] trocando {candidate} -> {nxt} nas GPUs principais "
                    f"após erro de GPU ({detail[:160]})",
                    flush=True,
                )
                continue

            if candidate_base is None:
                disable_primary()
                skip_main = True
            if index + 1 >= len(candidates):
                raise
            nxt = candidates[index + 1][0]
            print(
                f"[llm] modelo {candidate} falhou ({exc}); tentando fallback {nxt}",
                flush=True,
            )

    if last_exc is not None:
        raise last_exc
    return ""
