import asyncio
import hashlib
import hmac
import json
import os
import re
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

import art
import aux
import commitments
import consistency
import db
import embeddings
import gpu_guard
import guard
import llm
import metrics
import npc_forge
import offscreen
import prompts
import quality
import resolution
import tts
import world_pack
import world_voices
import worldcheck

DEFAULT_MODEL = llm.DEFAULT_MODEL
NUM_CTX = int(os.environ.get("LUMINUS_NUM_CTX", "32768"))
NARRATION_TOKENS = int(os.environ.get("LUMINUS_NARRATION_TOKENS", "1000"))
NARRATION_CONTINUATIONS = int(os.environ.get("LUMINUS_NARRATION_CONTINUATIONS", "2"))
CHAT_IDEMPOTENCY_WINDOW = float(
    os.environ.get("LUMINUS_CHAT_IDEMPOTENCY_WINDOW", "180")
)
LOGIC_REVIEW_ENABLED = os.environ.get("LUMINUS_LOGIC_REVIEW", "1") == "1"
LOGIC_REVIEW_TIMEOUT = float(os.environ.get("LUMINUS_LOGIC_REVIEW_TIMEOUT", "45"))
GUARD_REWRITE_ENABLED = os.environ.get("LUMINUS_GUARD_REWRITE", "1") == "1"
GUARD_REWRITE_TIMEOUT = float(os.environ.get("LUMINUS_GUARD_REWRITE_TIMEOUT", "45"))

_chat_locks: dict[str, asyncio.Lock] = {}
_opening_locks: dict[int, asyncio.Lock] = {}

# ── segurança de GPU ────────────────────────────────────────────────────────
# O gpu_guard monitora as placas e seleciona um modelo que caiba na VRAM
# restante quando alguma GPU cai/entra em erro. Estes globais guardam a
# escolha efetiva usada pelo Narrador e o vigia criado no lifespan.
_active_main_model = DEFAULT_MODEL
_active_num_ctx = NUM_CTX
_active_tier = "full"
_utility_model_cpu_only = False
_gpu_guard: gpu_guard.GpuGuard | None = None
_gpu_apply_lock = asyncio.Lock()
_bg_tasks: set[asyncio.Task] = set()
_last_restart_request = 0.0


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


def _model_eq(a: str, b: str) -> bool:
    return (a or "").split(":")[0].lower() == (b or "").split(":")[0].lower()


def _effective_model(requested: str) -> str:
    """Modelo que o Narrador deve usar agora.

    Em modo degradado o modelo é forçado para o tier atual (ex.: 9B em 2 GPUs),
    mesmo que a sessão/NPC aponte para o modelo principal de 27B.
    """
    requested = (requested or "").strip()
    if _gpu_guard is not None and _active_tier != "full":
        if requested and not _model_eq(requested, _active_main_model):
            print(
                f"[gpu-guard] modo degradado ({_active_tier}): "
                f"trocando modelo {requested} -> {_active_main_model}",
                flush=True,
            )
        return _active_main_model
    return requested or _active_main_model


async def _apply_gpu_assessment(assessment: gpu_guard.GpuAssessment) -> None:
    """Efetiva a troca de modelo/pool decidida pelo vigia."""
    global _active_main_model, _active_num_ctx, _active_tier
    async with _gpu_apply_lock:
        previous_model = _active_main_model
        target_model = assessment.model or DEFAULT_MODEL
        target_ctx = assessment.num_ctx or NUM_CTX
        changed = (
            target_model != _active_main_model
            or target_ctx != _active_num_ctx
            or assessment.tier != _active_tier
        )
        _active_main_model = target_model
        _active_num_ctx = target_ctx
        _active_tier = assessment.tier
        npc_forge.set_active_main_model(target_model)

        # A GPU utilitária caiu ou o modelo subiu 100% na CPU? Desativa os
        # auxiliares em vez de carregar outro modelo no pool do Narrador.
        llm.set_utility_available(
            assessment.utility_healthy and not _utility_model_cpu_only
        )
        # Enquanto houver ao menos uma GPU principal sadia, o pool principal
        # continua utilizável (com um modelo menor).
        if (
            assessment.main_healthy >= 1
            and assessment.tier in ("full", "two_gpu", "one_gpu")
        ):
            llm.reset_primary()
        else:
            # Tier utility/cpu: mesmo com GPUs visíveis, o modelo principal não
            # carregou (contexto CUDA envenenado, etc.). Usa a reserva.
            llm.disable_primary()
        llm.set_cpu_mode(assessment.tier == "cpu")

        state = globals().get("_warm_state")
        if changed and isinstance(state, dict) and state.get("main") not in (
            "idle",
            "loading",
            None,
        ):
            if assessment.degraded:
                state["main"] = "fallback"
                state["fallback"] = target_model
                state["error"] = assessment.reason
            else:
                state["main"] = "ready"
                state["fallback"] = ""

        if changed:
            print(
                f"[gpu-guard] Narrador -> {target_model} "
                f"(tier {assessment.tier}, ctx {target_ctx}); {assessment.reason}",
                flush=True,
            )
            if previous_model and not _model_eq(previous_model, target_model):
                # Libera a VRAM do modelo antigo em segundo plano; o Ollama
                # também evicta ao carregar o novo, mas isso acelera e evita
                # disputa de VRAM com a placa que restou.
                _spawn(llm.unload(previous_model, llm.OLLAMA))


def _on_llm_gpu_error(info: dict) -> None:
    """Hook síncrono do llm.py: agenda a degradação no loop do servidor."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _spawn(_handle_llm_gpu_error(dict(info)))


def _maybe_request_restart(service: str, reason: str) -> None:
    """Evita enxurrada de pedidos de reinício ao helper root."""
    global _last_restart_request
    now = time.time()
    if now - _last_restart_request < 60.0:
        return
    _last_restart_request = now
    accepted = gpu_guard.request_service_restart(service, reason)
    if accepted:
        # Depois que o helper reiniciar o Ollama, o contexto CUDA volta limpo.
        # Limpa a memória de falha para o guard tentar o tier correto de novo
        # em vez de ficar 15 min no modelo de reserva.
        wait = float(os.environ.get("LUMINUS_GPU_RESTART_WAIT", "45"))
        _spawn(_reset_guard_after_restart(wait))


async def _reset_guard_after_restart(delay: float) -> None:
    await asyncio.sleep(max(5.0, delay))
    if _gpu_guard is None:
        return
    _gpu_guard.clear_override()
    try:
        await _gpu_guard.evaluate(force=True)
        print(
            "[gpu-guard] reavaliado após reinício do Ollama; "
            f"tier={_gpu_guard.tier} modelo={_active_main_model}",
            flush=True,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[gpu-guard] reavaliação pós-restart falhou: {exc}", flush=True)


async def _handle_llm_gpu_error(info: dict) -> None:
    detail = str(info.get("detail", ""))
    base = str(info.get("base") or "")
    is_utility = (
        bool(base)
        and base.rstrip("/") == llm.OLLAMA_UTILITY.rstrip("/")
    )
    if gpu_guard.is_fatal_cuda_error(detail):
        _maybe_request_restart(
            "ollama-utility" if is_utility else "ollama", detail
        )
    if is_utility:
        # Falha de GPU afetando planner/resumo/capítulos (RTX 2060). Não
        # degrada o Narrador: reavalia o hardware para detectar se a placa
        # utilitária realmente caiu e, se sim, redireciona os auxiliares.
        print(
            f"[gpu-guard] erro de GPU na GPU utilitária "
            f"({info.get('model', '?')}): {str(info.get('detail', ''))[:240]}",
            flush=True,
        )
        if _gpu_guard is not None:
            try:
                await _gpu_guard.evaluate(force=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[gpu-guard] reavaliação falhou: {exc}", flush=True)
        return

    print(
        f"[gpu-guard] erro de GPU reportado pelo LLM "
        f"({info.get('model', '?')}): {str(info.get('detail', ''))[:300]}",
        flush=True,
    )
    if _gpu_guard is None:
        return
    try:
        await _gpu_guard.note_model_failure(
            str(info.get("model", "")),
            str(info.get("detail", "erro de GPU")),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[gpu-guard] falha ao degradar: {exc}", flush=True)


SUMMARIZE_AFTER = int(os.environ.get("LUMINUS_SUMMARIZE_AFTER", "24"))
SUMMARIZE_CHUNK = int(os.environ.get("LUMINUS_SUMMARIZE_CHUNK", "12"))
HISTORY_HARD_CAP = int(os.environ.get("LUMINUS_HISTORY_CAP", "80"))
MEMORY_SEARCH = os.environ.get("LUMINUS_MEMORY_SEARCH", "1") == "1"
MEMORY_HITS = int(os.environ.get("LUMINUS_MEMORY_HITS", "6"))
MEMORY_BUDGET = int(os.environ.get("LUMINUS_MEMORY_BUDGET", "1400"))
PROMPT_BUDGET_CHARS = int(os.environ.get("LUMINUS_PROMPT_BUDGET_CHARS", "24000"))
EXTRA_BUDGET_CHARS = int(os.environ.get("LUMINUS_EXTRA_BUDGET_CHARS", "6000"))
LORE_BUDGET_CHARS = int(os.environ.get("LUMINUS_LORE_BUDGET_CHARS", "1200"))
CHAPTER_BUDGET_CHARS = int(os.environ.get("LUMINUS_CHAPTER_BUDGET_CHARS", "6000"))
SUMMARY_BUDGET_CHARS = int(os.environ.get("LUMINUS_SUMMARY_BUDGET_CHARS", "2400"))
PLANNER_ENABLED = os.environ.get("LUMINUS_PLANNER", "1") == "1"
PLANNER_MODEL = os.environ.get("LUMINUS_PLANNER_MODEL", "qwen38-9b-heretic-q5")
PLANNER_TIMEOUT = float(os.environ.get("LUMINUS_PLANNER_TIMEOUT", "45"))
EMBED_TIMEOUT = float(os.environ.get("LUMINUS_EMBED_TIMEOUT", "45"))
HEALTH_OLLAMA_TIMEOUT = float(os.environ.get("LUMINUS_HEALTH_TIMEOUT", "2.5"))
CHAPTER_EVERY = int(os.environ.get("LUMINUS_CHAPTER_EVERY", "20"))
CHAPTER_MODEL = os.environ.get("LUMINUS_CHAPTER_MODEL", PLANNER_MODEL)
GUARD_ENABLED = os.environ.get("LUMINUS_GUARD", "1") == "1"
RESOLUTION_ENABLED = os.environ.get("LUMINUS_RESOLUTION", "1") == "1"
OFFSCREEN_ENABLED = os.environ.get("LUMINUS_OFFSCREEN", "1") == "1"
VOICES_ENABLED = os.environ.get("LUMINUS_VOICES", "1") == "1"
GUARD_MODEL = os.environ.get("LUMINUS_GUARD_MODEL", PLANNER_MODEL)
CONSISTENCY_AUTO = os.environ.get("LUMINUS_CONSISTENCY_AUTO", "0") == "1"

CHAR_FIELDS = [
    "kind", "name", "avatar", "tagline", "description_public", "persona", "greeting",
    "appearance", "personality", "backstory", "speech_style", "likes", "dislikes",
    "fears", "relationship", "secrets", "example_dialogue", "tags", "world_id", "model",
]


DISCOVERY_PORT = int(os.environ.get("LUMINUS_DISCOVERY_PORT", "5112"))
API_PORT = int(os.environ.get("LUMINUS_API_PORT", "5111"))


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if not data.strip().upper().startswith(b"LUMINUS"):
            return
        try:
            self.transport.sendto(f"LUMINUS:{API_PORT}".encode(), addr)
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _gpu_guard
    db.init()
    discovery = None
    try:
        loop = asyncio.get_running_loop()
        discovery, _ = await loop.create_datagram_endpoint(
            _DiscoveryProtocol,
            local_addr=("0.0.0.0", DISCOVERY_PORT),
            reuse_port=True,
        )
        print(f"[discovery] UDP {DISCOVERY_PORT} -> porta {API_PORT}")
    except OSError as e:
        print("[discovery] indisponivel:", e)

    _gpu_guard = gpu_guard.GpuGuard(on_change=_apply_gpu_assessment)
    llm.set_main_failover_model(_gpu_guard.two_gpu_model)
    llm.set_gpu_error_hook(_on_llm_gpu_error)
    try:
        await _gpu_guard.start()
        status = _gpu_guard.status()
        print(
            f"[gpu-guard] tier={status.get('tier')} "
            f"modelo={status.get('model')} "
            f"GPUs principais={status.get('main_healthy')}/"
            f"{status.get('main_expected')} "
            f"util={status.get('utility_healthy')} | "
            f"{status.get('reason')}",
            flush=True,
        )
    except Exception as exc:  # noqa: BLE001 - nunca impede o servidor de subir
        print(f"[gpu-guard] indisponível no boot: {exc}", flush=True)

    ds = llm.deepseek_status()
    print(
        f"[deepseek] narrador de emergência "
        f"{'ATIVO' if ds['active'] else 'INATIVO'} "
        f"(modelo {ds['model']}, chave {'ok' if ds['configured'] else 'ausente'})",
        flush=True,
    )

    try:
        yield
    finally:
        if _gpu_guard is not None:
            await _gpu_guard.stop()
        if discovery is not None:
            discovery.close()


app = FastAPI(title="Luminus", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
art.ensure_dir()
app.mount("/art", StaticFiles(directory=art.ART_DIR), name="art")


class WorldIn(BaseModel):
    name: str
    description: str = ""


class ManaSpendIn(BaseModel):
    amount: int = 1
    reason: str = ""


class CharacterIn(BaseModel):
    kind: str = "npc"
    name: str
    avatar: str = ""
    tagline: str = ""
    description_public: str = ""
    persona: str = ""
    greeting: str = ""
    appearance: str = ""
    personality: str = ""
    backstory: str = ""
    speech_style: str = ""
    likes: str = ""
    dislikes: str = ""
    fears: str = ""
    relationship: str = ""
    secrets: str = ""
    example_dialogue: str = ""
    tags: list[str] = []
    world_id: int | None = None
    model: str = ""
    voice: str = ""
    visual_prompt: str = ""
    art_seed: int = 0
    visual_style: str = "anime"
    attributes: dict[str, int] | None = None
    skills: dict[str, int] | None = None


class PlaceIn(BaseModel):
    world_id: int | None = None
    name: str
    kind: str = "ambiente"
    description: str = ""
    atmosphere: str = ""
    rules: str = ""
    tags: list[str] = []


class SessionIn(BaseModel):
    character_id: int
    protagonist_id: int | None = None
    title: str = ""
    cast_ids: list[int] = []
    place_id: int | None = None


class CastIn(BaseModel):
    character_id: int


class ArchiveIn(BaseModel):
    archived: bool = True


class DeepSeekIn(BaseModel):
    enabled: bool = True


class ChatIn(BaseModel):
    message: str = ""
    model: str = ""
    ooc: bool = False
    continue_scene: bool = False
    regen: bool = False
    request_id: str = ""


class MessageIn(BaseModel):
    content: str


class AssistIn(BaseModel):
    seed: str = ""
    kind: str = "npc"
    world_id: int | None = None
    tone: str = ""
    name: str = ""
    # Criação profunda (Pensador -> Juiz -> Síntese). O app antigo não envia
    # este campo; o default mantém o fluxo novo ligado.
    deep: bool = True


class RefineIn(BaseModel):
    draft: dict
    instruction: str
    kind: str = "npc"


class BranchIn(BaseModel):
    message_id: int | None = None
    title: str = ""


class RewindIn(BaseModel):
    message_id: int


class ChapterIn(BaseModel):
    force: bool = False


class FactIn(BaseModel):
    text: str
    keys: str = ""


class CoverIn(BaseModel):
    character_id: int | None = None
    name: str = ""
    kind: str = "npc"
    appearance: str = ""
    personality: str = ""
    backstory: str = ""
    tags: list[str] = []
    world: str = ""
    style: str = "anime"
    seed: int | None = None
    width: int | None = None
    height: int | None = None
    adult: bool = False
    photoreal: bool = False
    face_detail: bool = True
    hand_detail: bool = False
    upscale: float = 1.5


class SceneIn(BaseModel):
    style: str = "anime"
    seed: int | None = None
    width: int | None = None
    height: int | None = None
    face_detail: bool = False
    hand_detail: bool = False
    upscale: float = 1.0


class ArtFreeIn(BaseModel):
    prompt: str
    style: str = "anime"
    width: int | None = None
    height: int | None = None
    seed: int | None = None
    enhance: bool = True
    adult: bool = False
    model: str = ""
    face_detail: bool = True
    hand_detail: bool = False
    upscale: float = 1.5


def _world(conn, wid):
    if wid is None:
        return None
    return db.rowdict(conn.execute("SELECT * FROM worlds WHERE id=?", (wid,)).fetchone())


def _char(conn, cid):
    if cid is None:
        return None
    return char_out(conn.execute("SELECT * FROM characters WHERE id=?", (cid,)).fetchone())


def _place(conn, pid):
    if pid is None:
        return None
    row = conn.execute("SELECT * FROM places WHERE id=?", (pid,)).fetchone()
    return db.rowdict(row)


def _session_place(conn, sess):
    if sess is None:
        return None
    try:
        pid = sess["place_id"]
    except (KeyError, IndexError, TypeError):
        pid = None
    return _place(conn, pid)


def _chat_request_key(sid, user_msg, request_id=""):
    # A chave e baseada no conteudo para capturar retry do app mesmo quando o
    # cliente gera um request_id novo. Dentro da janela curta, repetir a mesma
    # acao devolve a resposta anterior em vez de gerar outra.
    raw = f"{sid}:{' '.join((user_msg or '').split())}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _recent_chat_response(conn, request_key):
    if not request_key:
        return ""
    row = conn.execute(
        "SELECT assistant_content FROM chat_requests "
        "WHERE request_key=? AND created_at>=? ORDER BY created_at DESC LIMIT 1",
        (request_key, time.time() - CHAT_IDEMPOTENCY_WINDOW),
    ).fetchone()
    return (row["assistant_content"] or "") if row else ""


def _save_chat_response(conn, request_key, sid, turn, user_msg, assistant_msg):
    if not request_key or not (assistant_msg or "").strip():
        return
    conn.execute(
        "INSERT OR REPLACE INTO chat_requests "
        "(request_key, session_id, turn, user_content, assistant_content, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (
            request_key,
            sid,
            int(turn or 0),
            user_msg or "",
            assistant_msg,
            time.time(),
        ),
    )
    conn.commit()


def _insert_message_once(conn, sid, role, content, window=90.0):
    """Evita inserir a mesma mensagem duas vezes em sequencia.

    Retorna o id da mensagem existente quando a ultima mensagem da sessao
    tem o mesmo papel e conteudo quase identico dentro da janela.
    """
    text = (content or "").strip()
    if not text:
        return 0
    last = conn.execute(
        "SELECT id, role, content, created_at FROM messages "
        "WHERE session_id=? ORDER BY id DESC LIMIT 1",
        (sid,),
    ).fetchone()
    if (
        last
        and last["role"] == role
        and time.time() - float(last["created_at"] or 0) <= window
        and quality.is_duplicate(text, last["content"] or "")
    ):
        return int(last["id"])
    cur = conn.execute(
        "INSERT INTO messages (session_id, role, content, created_at) VALUES (?,?,?,?)",
        (sid, role, text, time.time()),
    )
    return int(cur.lastrowid or 0)


async def _replay_chat(text, state=None):
    yield json.dumps({"status": "writing"}, ensure_ascii=False) + "\n"
    yield json.dumps({"delta": text}, ensure_ascii=False) + "\n"
    done = {"done": True, "deduped": True}
    if isinstance(state, dict):
        done.update(
            {
                "nr": int(state.get("nr", aux.NR_START)),
                "axes": state.get("axes") or {},
                "vinculo": state.get("vinculo") or "",
                "scene": state.get("scene") or {},
                "pacing": state.get("pacing") or {},
                "mood": (state.get("moment") or {}).get("mood")
                or state.get("mood")
                or "",
            }
        )
    yield json.dumps(done, ensure_ascii=False) + "\n"


def _looks_truncated(text):
    tail = (text or "").rstrip()
    if not tail:
        return False
    if tail[-1] in '.!?…"\'’”)])*—':
        return False
    # Fechamento de fala/atribuicao sem pontuacao final.
    return True


def _merge_continuation(base, extra):
    base = (base or "").rstrip()
    extra = (extra or "").strip()
    if not extra:
        return ""
    limit = min(len(base), len(extra), 500)
    for size in range(limit, 3, -1):
        if base[-size:].lower() == extra[:size].lower():
            rest = extra[size:].lstrip()
            if not rest:
                return ""
            if rest[0] in ".,;:!?…":
                return rest
            return (" " if base[-1] not in " \n" else "") + rest
    if not base:
        return extra
    return (" " if base[-1] not in " \n" else "") + extra


async def _continue_narration(model, context, text, num_ctx):
    tail = (text or "")[-1600:]
    messages = list(context) + [
        {"role": "assistant", "content": tail},
        {
            "role": "user",
            "content": (
                "Continue a narracao exatamente do ponto em que parou. "
                "Nao repita frases, nao resuma, nao escreva meta-comentario. "
                "Mantenha o mesmo estilo e termine a fala/cena."
            ),
        },
    ]
    extra = await asyncio.wait_for(
        llm.complete(
            model,
            messages,
            False,
            {
                "temperature": 0.85,
                "num_ctx": num_ctx,
                "num_predict": min(500, NARRATION_TOKENS),
            },
        ),
        timeout=90,
    )
    return _merge_continuation(text, extra)


async def _review_narration(text, npc, state, user_msg, prev_location, new_location):
    """Corrige logica material, agencia e formato; devolve (texto, mudou)."""
    original = text or ""
    final = original
    issues = []
    try:
        issues = guard.detect(final, npc, state, user_msg)
    except Exception as exc:  # noqa: BLE001
        print(f"[guard] detect falhou: {exc}", flush=True)
    logic_issues = []
    try:
        logic_issues = quality.detect_logic_issues(final, npc, state)
    except Exception as exc:  # noqa: BLE001
        print(f"[logic] detect falhou: {exc}", flush=True)

    if logic_issues and LOGIC_REVIEW_ENABLED:
        try:
            reviewed = await asyncio.wait_for(
                llm.complete(
                    llm.UTILITY_MODEL,
                    [
                        {
                            "role": "system",
                            "content": "Responda apenas com a narrativa reescrita, em PT-BR.",
                        },
                        {
                            "role": "user",
                            "content": quality.logic_review_prompt(
                                final, logic_issues, npc
                            ),
                        },
                    ],
                    False,
                    {
                        "temperature": 0.3,
                        "num_ctx": 8192,
                        "num_predict": max(300, min(1200, len(final) // 3 + 200)),
                    },
                    base=llm.OLLAMA_UTILITY,
                    force_utility=True,
                ),
                timeout=LOGIC_REVIEW_TIMEOUT,
            )
            reviewed = (reviewed or "").strip()
            if len(reviewed) >= max(80, int(len(final) * 0.45)):
                final = reviewed
                logic_issues = quality.detect_logic_issues(final, npc, state)
                print("[logic] reescrito:", logic_issues or "ok", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[logic] revisao falhou: {exc}", flush=True)

    if logic_issues:
        dropped = quality.drop_logic_sentences(final, logic_issues)
        if len(dropped.strip()) >= max(40, int(len(final) * 0.35)):
            final = dropped
        else:
            print(
                "[logic] remocao deixaria a cena curta demais; mantendo original",
                flush=True,
            )

    if guard.needs_rewrite(issues) and GUARD_REWRITE_ENABLED:
        try:
            rewritten = await asyncio.wait_for(
                llm.complete(
                    llm.UTILITY_MODEL,
                    [
                        {
                            "role": "system",
                            "content": prompts.STYLE_GUIDE,
                        },
                        {
                            "role": "user",
                            "content": guard.rewrite_prompt(
                                final, issues, npc, user_msg
                            ),
                        },
                    ],
                    False,
                    {
                        "temperature": 0.35,
                        "num_ctx": 8192,
                        "num_predict": max(300, min(1200, len(final) // 3 + 200)),
                    },
                    base=llm.OLLAMA_UTILITY,
                    force_utility=True,
                ),
                timeout=GUARD_REWRITE_TIMEOUT,
            )
            rewritten = (rewritten or "").strip()
            if len(rewritten) >= max(80, int(len(final) * 0.45)):
                final = rewritten
                print("[guard] reescrito:", issues, flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[guard] revisao falhou: {exc}", flush=True)
            final = guard.clean(final, issues, npc)
    else:
        final = guard.clean(final, issues, npc)

    reverted = False
    if prev_location and new_location and not quality.location_transition_ok(
        final, prev_location, new_location
    ):
        try:
            state["location"] = prev_location
            scene = state.get("scene")
            if isinstance(scene, dict):
                scene["location"] = prev_location
            reverted = True
            print(
                f"[scene] local revertido ({new_location!r} -> {prev_location!r}): "
                "sem transicao narrada",
                flush=True,
            )
        except Exception:  # noqa: BLE001
            pass

    final = quality.format_narration(final)
    return final, final.strip() != original.strip()


def char_out(row):
    if row is None:
        return None
    d = dict(row)
    raw = d.get("tags") or ""
    if isinstance(raw, str):
        try:
            d["tags"] = json.loads(raw) if raw.strip() else []
        except ValueError:
            d["tags"] = [t.strip() for t in raw.split(",") if t.strip()]
    try:
        portraits = json.loads(d.get("portraits") or "{}")
        d["portraits"] = portraits if isinstance(portraits, dict) else {}
    except (TypeError, ValueError):
        d["portraits"] = {}
    raw_attrs = d.get("attributes")
    if isinstance(raw_attrs, str):
        try:
            raw_attrs = json.loads(raw_attrs) if raw_attrs.strip() else {}
        except ValueError:
            raw_attrs = {}
    d["attributes"] = resolution.normalize_attributes(raw_attrs)
    raw_skills = d.get("skills")
    if isinstance(raw_skills, str):
        try:
            raw_skills = json.loads(raw_skills) if raw_skills.strip() else {}
        except ValueError:
            raw_skills = {}
    d["skills"] = resolution.normalize_skills(raw_skills)
    return d


def _attributes_json(attributes, kind: str, current=None) -> str:
    if attributes is None:
        parsed = current
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed or "{}")
            except ValueError:
                parsed = {}
        attrs = resolution.normalize_attributes(parsed)
        if attrs:
            if kind == "protagonist":
                attrs = resolution.balance_attributes(attrs)
            return json.dumps(attrs, ensure_ascii=False)
        attributes = {}
    attrs = resolution.normalize_attributes(attributes)
    if kind == "protagonist":
        attrs = resolution.balance_attributes(attrs)
        if not attrs:
            attrs = {
                key: resolution.ATTRIBUTE_DEFAULT
                for key in resolution.ATTRIBUTE_KEYS
            }
    return json.dumps(attrs, ensure_ascii=False)


def _skills_json(skills, kind: str, current=None) -> str:
    if skills is None:
        parsed = current
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed or "{}")
            except ValueError:
                parsed = {}
        values = resolution.normalize_skills(parsed)
        if values:
            if kind == "protagonist":
                values = resolution.balance_skills(values)
            return json.dumps(values, ensure_ascii=False)
        skills = {}
    values = resolution.normalize_skills(skills)
    if kind == "protagonist":
        values = resolution.balance_skills(values)
        if not values:
            values = {key: resolution.SKILL_DEFAULT for key in resolution.SKILL_KEYS}
    return json.dumps(values, ensure_ascii=False)


def _inject_player_sheet(state, protagonist):
    """Coloca atributos e perícias do protagonista no estado da rolagem.

    Sobrescreve a cada request para que edições na ficha valham na hora;
    sessões sem protagonista voltam ao comportamento antigo (sem ficha).
    """
    if protagonist:
        state["attributes"] = resolution.normalize_attributes(
            protagonist.get("attributes")
        )
        state["skills"] = resolution.normalize_skills(
            protagonist.get("skills")
        )
    else:
        state.pop("attributes", None)
        state.pop("skills", None)
    return state


def _cast_rows(conn, sid):
    rows = conn.execute(
        "SELECT c.* FROM session_cast sc "
        "JOIN characters c ON c.id = sc.character_id "
        "WHERE sc.session_id=? ORDER BY sc.position, sc.character_id",
        (sid,),
    ).fetchall()
    return [char_out(row) for row in rows if row is not None]


def _cast_ids(conn, sid):
    return [
        int(row["character_id"])
        for row in conn.execute(
            "SELECT character_id FROM session_cast WHERE session_id=? "
            "ORDER BY position, character_id",
            (sid,),
        ).fetchall()
    ]


def _session_out(conn, row):
    data = db.rowdict(row)
    if data:
        data["cast_ids"] = _cast_ids(conn, data["id"])
        last = conn.execute(
            "SELECT content FROM messages WHERE session_id=? "
            "AND role='assistant' AND length(content)>0 ORDER BY id DESC LIMIT 1",
            (data["id"],),
        ).fetchone()
        data["last_line"] = (last["content"] or "")[:400] if last else ""
    return data


def _tags_json(tags):
    return json.dumps(tags or [], ensure_ascii=False)


def _looks_default_title(title, name):
    text = (title or "").strip()
    if not text:
        return True
    return text.startswith(f"{name or ''} —")


async def _generate_session_title(opening, npc, world):
    name = (npc or {}).get("name") or "a historia"
    world_name = (world or {}).get("name") or ""
    snippet = (opening or "").strip()[:1400]
    try:
        raw = await asyncio.wait_for(
            llm.complete(
                llm.UTILITY_MODEL,
                [
                    {"role": "system", "content": prompts.TITLE_SYSTEM},
                    {
                        "role": "user",
                        "content": (
                            f"Mundo: {world_name or '(nao definido)'}\n"
                            f"NPC principal: {name}\n"
                            f"Abertura:\n{snippet}"
                        ),
                    },
                ],
                False,
                {"temperature": 0.7, "num_ctx": 4096, "num_predict": 24},
                base=llm.OLLAMA_UTILITY,
            ),
            timeout=15,
        )
        title = (raw or "").strip().splitlines()[0].strip().strip('"').strip("'")
        title = re.sub(r"^#+\s*", "", title).strip()
        if len(title) >= 3:
            return title[:60]
    except Exception as exc:  # noqa: BLE001
        print(f"[title] geracao falhou: {exc}", flush=True)
    if world_name:
        return f"{world_name}: {name}"
    return f"A historia de {name}"


async def _generate_opening(npc, protagonist, world, cast=None, place=None):
    partes = ["PERSONAGEM (NPC PRINCIPAL):", prompts.npc_card(npc)]
    if cast:
        partes.append(
            "OUTROS NPCs DO ELENCO (secundarios; so aparecem se a cena pedir):\n"
            + "\n".join(
                f"- {c['name']}: {(c.get('tagline') or c.get('description_public') or '')[:160]}"
                for c in list(cast)[:5]
            )
        )
    if protagonist:
        partes.append("JOGADOR / PROTAGONISTA:")
        partes.append(prompts.protagonist_card(protagonist))
    else:
        partes.append(
            "JOGADOR / PROTAGONISTA:\n"
            "O jogador nao criou ficha. Trate-o como 'voce', sem nome, "
            "e situe-o na cena como alguem presente e capaz de agir."
        )
    if world:
        partes.append(f"MUNDO: {world.get('name') or ''}")
        if (world.get("description") or "").strip():
            partes.append(world["description"].strip())
    pb = prompts.place_block(place)
    if pb:
        partes.append(pb)
    if (npc.get("greeting") or "").strip():
        partes.append("FALA INICIAL FORNECIDA (semente):\n" + npc["greeting"].strip())
    partes.append(
        "CHECKLIST DA ABERTURA (responda a tudo dentro da cena, sem listar):\n"
        "- Onde o jogador esta, o que estava fazendo e por que esta ali?\n"
        "- Quem e o NPC e por que ele se dirige ao jogador AGORA?\n"
        "- Se o NPC pede, cobra ou exige algo: o que e, por que agora e o que esta em jogo?\n"
        "- A ultima frase permite uma resposta clara, sem o jogador precisar adivinhar?\n"
        "Se faltar algum desses dados, invente o minimo coerente com as fichas e o mundo. "
        "Escreva a abertura agora, sem titulo e sem cabecalho."
    )
    out = await llm.complete(
        _effective_model(DEFAULT_MODEL),
        [
            {"role": "system", "content": prompts.OPENING_SYSTEM},
            {"role": "user", "content": "\n\n".join(partes)},
        ],
        False,
        {
            "temperature": 0.85,
            "top_p": 0.95,
            "num_ctx": _active_num_ctx,
            "num_predict": max(640, NARRATION_TOKENS),
        },
    )
    out = quality.drop_logic_sentences(
        out, quality.detect_logic_issues(out, npc, {})
    )
    try:
        issues = guard.detect(out, npc, {}, "")
        out = guard.clean(out, issues, npc)
    except Exception:  # noqa: BLE001
        pass
    return quality.format_narration(out.strip())


def _threads_block(conn, sid, turn=0, limit=8):
    return commitments.block(conn, sid, turn=turn, limit=limit)


def _entity_block(conn, sid, npc, protagonist, cast=None, limit=10):
    names = []
    for char in [npc, protagonist, *(cast or [])]:
        if char and char.get("name"):
            names.append(char["name"])
    if not names:
        return ""
    placeholders = ",".join("?" for _ in names)
    rows = conn.execute(
        "SELECT entity, fact, known_by FROM entity_facts "
        f"WHERE session_id=? AND entity IN ({placeholders}) "
        "ORDER BY id DESC LIMIT ?",
        (sid, *names, limit),
    ).fetchall()
    if not rows:
        return ""
    lines = []
    for row in rows:
        who = ""
        try:
            known = json.loads(row["known_by"] or "[]")
            if isinstance(known, list) and known:
                who = " (sabem: " + ", ".join(
                    str(item) for item in known[:4]
                ) + ")"
        except ValueError:
            pass
        lines.append(f"- {row['entity']}: {row['fact']}{who}")
    return "## FATOS CANONICOS POR ENTIDADE\n" + "\n".join(lines)


def _facts_for(conn, sid, name, limit=3):
    key = aux._name_key(name)
    if not key:
        return []
    out = []
    rows = conn.execute(
        "SELECT entity, fact, known_by FROM entity_facts "
        "WHERE session_id=? ORDER BY id DESC LIMIT 60",
        (sid,),
    ).fetchall()
    for row in rows:
        try:
            known = json.loads(row["known_by"] or "[]")
        except ValueError:
            known = []
        known_keys = [aux._name_key(who) for who in known] if isinstance(known, list) else []
        if aux._name_key(row["entity"]) == key or key in known_keys:
            out.append(f"{row['entity']}: {row['fact']}")
        if len(out) >= limit:
            break
    return out


def _commitments_for(conn, sid, name, limit=3):
    key = aux._name_key(name)
    out = []
    for item in commitments.open_commitments(conn, sid, limit=24):
        entities = [aux._name_key(e) for e in item.get("entities") or []]
        if key in entities or (key and key in aux._name_key(item.get("title"))):
            out.append(f"#{item['id']} [{item['kind']}] {item['title']}")
        if len(out) >= limit:
            break
    return out


def _now_text(layers, nr, plan_intent=""):
    persona = layers.get("persona") or {}
    session = layers.get("session") or {}
    moment = layers.get("moment") or {}
    parts = []
    if str(persona.get("arc") or "").strip():
        parts.append("arco: " + str(persona["arc"]).strip()[:140])
    if str(moment.get("mood") or "").strip():
        parts.append("emocao: " + str(moment["mood"]).strip()[:120])
    if str(session.get("goal") or "").strip():
        parts.append("objetivo: " + str(session["goal"]).strip()[:120])
    if plan_intent:
        parts.append("intencao agora: " + str(plan_intent).strip()[:160])
    parts.append(f"NR {nr}/100")
    return " | ".join(parts)


def _memory_cards(conn, sid, npc, protagonist, cast, state, plan_data=None):
    if not state:
        return ""
    plan_data = plan_data or {}
    foco = str(plan_data.get("foco") or "").strip()
    cards = []
    if npc:
        layers = aux.main_layers(state)
        try:
            nr = int((layers.get("session") or {}).get("nr", state.get("nr", 20)) or 20)
        except (ValueError, TypeError):
            nr = 20
        identity = layers.get("identity") or {}
        boundaries = list(identity.get("boundaries") or [])
        if npc.get("secrets") and nr < aux.SECRET_GATE:
            boundaries.append("Segredo central bloqueado")
        boundaries.append("Nao sabe o que nao presenciou")
        intent = plan_data.get("npc_intencao") if foco == npc.get("name") else ""
        cards.append(prompts.memory_card(
            npc.get("name") or "NPC principal",
            layers=layers,
            memories=(state.get("npc_notes") or [])[-4:],
            boundaries=boundaries,
            now=_now_text(layers, nr, intent),
            commitments=_commitments_for(conn, sid, npc.get("name") or ""),
        ))
    for c in list(cast or [])[:5]:
        rel = (state.get("cast") or {}).get(str(c.get("id"))) or {}
        if not isinstance(rel, dict):
            continue
        layers = aux.cast_layers(rel)
        try:
            nr = int((layers.get("session") or {}).get("nr", rel.get("nr", 20)) or 20)
        except (ValueError, TypeError):
            nr = 20
        identity = layers.get("identity") or {}
        boundaries = list(identity.get("boundaries") or [])
        if c.get("secrets") and nr < aux.SECRET_GATE:
            boundaries.append("Segredo central bloqueado")
        boundaries.append("Nao sabe o que nao presenciou")
        intent = plan_data.get("npc_intencao") if foco == c.get("name") else ""
        cards.append(prompts.memory_card(
            c.get("name") or "NPC",
            layers=layers,
            memories=(rel.get("notes") or [])[-4:],
            boundaries=boundaries,
            now=_now_text(layers, nr, intent),
            commitments=_commitments_for(conn, sid, c.get("name") or ""),
        ))
    return prompts.memory_cards_block(cards)


def _apply_plan_threads(conn, sid, plan_data, turn):
    now = time.time()
    for item in plan_data.get("threads") or []:
        action = item.get("acao")
        title = (item.get("titulo") or "").strip()
        if not title:
            continue
        thread_id = int(item.get("id") or 0)
        entities = json.dumps(item.get("entidades") or [], ensure_ascii=False)
        due = int(item.get("prazo_turno") or 0)
        payoff = (item.get("payoff") or "").strip()
        if thread_id > 0:
            row = conn.execute(
                "SELECT id FROM threads WHERE id=? AND session_id=?",
                (thread_id, sid),
            ).fetchone()
            if row:
                status = {
                    "abrir": "aberta",
                    "avancar": "aberta",
                    "resolver": "resolvida",
                    "abandonar": "abandonada",
                }.get(action, "aberta")
                conn.execute(
                    "UPDATE threads SET status=?, payoff=?, due_turn=?, "
                    "entities=?, last_turn=?, updated_at=? WHERE id=?",
                    (status, payoff, due, entities, int(turn or 0), now, thread_id),
                )
                continue
        if action != "abrir":
            continue
        existing = conn.execute(
            "SELECT id FROM threads WHERE session_id=? AND title=? "
            "AND status='aberta'",
            (sid, title),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE threads SET due_turn=?, entities=?, last_turn=?, "
                "updated_at=? WHERE id=?",
                (due, entities, int(turn or 0), now, existing["id"]),
            )
            continue
        conn.execute(
            "INSERT INTO threads (session_id, kind, title, status, setup, "
            "payoff, due_turn, entities, last_turn, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                sid,
                (item.get("tipo") or "promessa")[:40],
                title[:200],
                "aberta",
                (plan_data.get("objetivo") or title)[:400],
                payoff,
                due,
                entities,
                int(turn or 0),
                now,
                now,
            ),
        )
    for item in plan_data.get("fatos_entidade") or []:
        entity = (item.get("entidade") or "").strip()
        fact = (item.get("fato") or "").strip()
        if not entity or not fact:
            continue
        known = list(item.get("sabem") or [])
        existing = conn.execute(
            "SELECT id, known_by FROM entity_facts "
            "WHERE session_id=? AND entity=? AND fact=?",
            (sid, entity[:80], fact[:240]),
        ).fetchone()
        if existing:
            try:
                merged = json.loads(existing["known_by"] or "[]")
            except ValueError:
                merged = []
            if not isinstance(merged, list):
                merged = []
            for who in known:
                if who not in merged:
                    merged.append(who)
            conn.execute(
                "UPDATE entity_facts SET known_by=? WHERE id=?",
                (json.dumps(merged, ensure_ascii=False), existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO entity_facts (session_id, entity, fact, "
                "known_by, source_turn, created_at) VALUES (?,?,?,?,?,?)",
                (
                    sid,
                    entity[:80],
                    fact[:240],
                    json.dumps(known, ensure_ascii=False),
                    turn,
                    now,
                ),
            )
    conn.commit()


def _sanitize_plan(plan_data, npc, allow_secrets):
    def scrub(value):
        return guard.scrub(
            str(value or ""), npc, allow_secrets=allow_secrets
        )[:400]

    for key in ("vinculo", "objetivo", "npc_intencao", "npc_emocao", "consequencia"):
        plan_data[key] = scrub(plan_data.get(key))
    plan_data["foco"] = scrub(plan_data.get("foco"))
    plan_data["ritmo"] = scrub(plan_data.get("ritmo"))[:40]
    cena = plan_data.get("cena") if isinstance(plan_data.get("cena"), dict) else {}
    plan_data["cena"] = {
        "acao": str(cena.get("acao") or "continuar")[:20],
        "titulo": scrub(cena.get("titulo"))[:120],
        "local": scrub(cena.get("local"))[:120],
        "presentes": [
            scrub(name)[:60] for name in (cena.get("presentes") or [])
        ][:10] if isinstance(cena.get("presentes"), list) else [],
        "transicao": scrub(cena.get("transicao"))[:240],
        "motivo": scrub(cena.get("motivo"))[:240],
    }
    marco = plan_data.get("marco") if isinstance(plan_data.get("marco"), dict) else {}
    plan_data["marco"] = {
        "tipo": scrub(marco.get("tipo"))[:40],
        "descricao": scrub(marco.get("descricao"))[:240],
        "impacto": scrub(marco.get("impacto"))[:180],
    }
    raw_nr = plan_data.get("nr_por_npc")
    if isinstance(raw_nr, dict):
        cleaned = {}
        for name, value in list(raw_nr.items())[:8]:
            try:
                cleaned[scrub(name)[:80]] = max(
                    -10, min(10, int(value or 0))
                )
            except (ValueError, TypeError):
                continue
        plan_data["nr_por_npc"] = cleaned
    else:
        plan_data["nr_por_npc"] = {}
    for key in ("detalhes", "evitar", "fatos"):
        plan_data[key] = [scrub(item) for item in plan_data.get(key, [])][:4]
    estado = plan_data.get("estado") or {}
    for key in ("local", "hora"):
        estado[key] = scrub(estado.get(key, ""))
    for key in ("inventario", "ferimentos"):
        estado[key] = [scrub(item) for item in estado.get(key, [])][:20]
    for key in ("objetos", "npcs"):
        estado[key] = {
            str(name)[:40]: scrub(value)
            for name, value in (estado.get(key) or {}).items()
        }
    plan_data["estado"] = estado
    for item in plan_data.get("threads") or []:
        item["titulo"] = scrub(item.get("titulo"))
        item["tipo"] = scrub(item.get("tipo"))
        item["payoff"] = scrub(item.get("payoff"))
        item["entidades"] = [
            scrub(entity) for entity in item.get("entidades", [])
        ][:6]
    for item in plan_data.get("fatos_entidade") or []:
        item["entidade"] = scrub(item.get("entidade"))
        item["fato"] = scrub(item.get("fato"))
        item["sabem"] = [
            scrub(who) for who in item.get("sabem", [])
        ][:8]
    return plan_data


def _infer_foco(cast, npc, text):
    hay = aux._name_key(text or "")
    for c in cast or []:
        name = (c.get("name") or "").strip()
        first = name.split()[0] if name else ""
        for candidate in (name, first):
            key = aux._name_key(candidate)
            if key and re.search(r"\b" + re.escape(key) + r"\b", hay):
                return name
    return (npc.get("name") if npc else "") or ""


async def _plan_scene(
    conn,
    sess,
    npc,
    protagonist,
    world,
    user_msg,
    continue_scene,
    state=None,
    apply_side_effects=True,
    cast=None,
):
    def clip(x, n):
        return ((x or "")[:n]).strip()

    rows = conn.execute(
        "SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 3",
        (sess["id"],),
    ).fetchall()
    rows = list(reversed(rows))
    convo = "\n".join(
        f"{'Jogador' if r['role'] == 'user' else 'Narrador'}: {clip(r['content'], 500)}"
        for r in rows
    )
    partes = []
    nr = 20
    try:
        nr = int(state.get("nr", 20) or 20) if state else 20
    except (ValueError, TypeError):
        nr = 20
    if npc:
        partes.append(
            "NPC: " + npc["name"] + " — " + clip(npc.get("tagline"), 120)
            + "\nPersona: " + clip(npc.get("personality") or npc.get("persona"), 320)
        )
        if state:
            partes.append(aux.relationship_block(state, npc))
        secrets = clip(npc.get("secrets"), 320)
        if secrets:
            if nr < aux.SECRET_GATE:
                partes.append(
                    "## RESTRICAO DE SEGREDO (obrigatoria)\n"
                    "- SEGREDOS BLOQUEADOS: " + secrets + "\n"
                    "- NUNCA planeje revelar esses segredos. Planeje desconversa, teste, "
                    "mentira ou pista ambigua."
                )
            else:
                partes.append(
                    "## ABERTURA (NR alto)\n"
                    "- SEGREDOS: " + secrets + "\n"
                    "- Uma revelacao parcial/importante e permitida, mas com hesitacao, "
                    "medo e consequencia. Nao despeje tudo."
                )
    if cast:
        rels = (state or {}).get("cast") or {}
        cast_lines = []
        for c in list(cast)[:5]:
            rel = rels.get(str(c.get("id"))) or {}
            try:
                cnr = int(rel.get("nr", aux.NR_START) or aux.NR_START)
            except (ValueError, TypeError):
                cnr = aux.NR_START
            line = (
                "- " + c["name"] + f" (NR {cnr}) — "
                + clip(c.get("tagline"), 100)
                + " | Persona: " + clip(c.get("personality"), 180)
            )
            secret = clip(c.get("secrets"), 200)
            if secret:
                if cnr >= aux.SECRET_GATE:
                    line += " | SEGREDO (pode abrir com peso): " + secret
                else:
                    line += " | SEGREDO BLOQUEADO (nao revelar)"
            cast_lines.append(line)
        partes.append(
            "## ELENCO (NPCs secundarios; cada um age e sabe por si)\n"
            + "\n".join(cast_lines)
        )
    cards = _memory_cards(
        conn, sess["id"], npc, protagonist, cast, state
    )
    if cards:
        partes.append(cards)
    if state:
        partes.append(aux.ledger_block(state))
        scene = state.get("scene") or {}
        if not scene.get("id"):
            partes.append(
                "## CENA ATUAL\nAinda nao iniciada. Use cena.acao=iniciar, "
                "com titulo/local e presentes, para abrir a cena deste beat."
            )
    threads_block = _threads_block(
        conn, sess["id"], turn=int((state or {}).get("turn", 0)) + 1
    )
    if threads_block:
        partes.append(threads_block)
    entity_block = _entity_block(conn, sess["id"], npc, protagonist, cast=cast)
    if entity_block:
        partes.append(entity_block)
    if protagonist:
        partes.append(
            "Jogador: " + protagonist["name"] + " — " + clip(protagonist.get("personality"), 240)
        )
    wc = clip(prompts.world_card(world), 240)
    if wc:
        partes.append("Mundo: " + wc)
    pb = clip(prompts.place_block(_session_place(conn, sess)), 900)
    if pb:
        partes.append(pb)
    wid = (world or {}).get("id")
    try:
        if wid:
            place_rows = conn.execute(
                "SELECT name, kind, description FROM places "
                "WHERE world_id=? ORDER BY id LIMIT 12",
                (wid,),
            ).fetchall()
        else:
            place_rows = conn.execute(
                "SELECT name, kind, description FROM places ORDER BY id LIMIT 12"
            ).fetchall()
        if place_rows:
            partes.append(
                "## AMBIENTES PROPRIOS DISPONIVEIS (podem ser usados em "
                "cena.trocar/iniciar)\n"
                + "\n".join(
                    f"- {row['name']} ({row['kind'] or 'ambiente'}): "
                    f"{clip(row['description'], 160)}"
                    for row in place_rows
                )
            )
    except Exception:  # noqa: BLE001
        pass
    if sess["summary"]:
        partes.append("Memoria: " + clip(sess["summary"], 360))
    partes.append("Ultimas mensagens:\n" + (convo or "(vazio)"))
    if continue_scene and not user_msg.strip():
        partes.append("Agora: o jogador nao age; continue a cena avancando o mundo/NPCs.")
    else:
        partes.append("Acao do jogador agora: " + clip(user_msg, 500))
    partes.append(
        "Escolha o FOCO do beat (principal ou secundario). Use nr_delta para o "
        "FOCO e nr_por_npc para os demais. Quem nao esta na cena nao age."
    )
    partes.append("Gere o plano no JSON pedido.")
    try:
        out = await llm.complete(
            PLANNER_MODEL,
            [{"role": "system", "content": prompts.PLANNER_SYSTEM},
             {"role": "user", "content": "\n".join(partes)}],
            False,
            {"temperature": 0.6, "num_ctx": 8192, "num_predict": 1000},
            fmt=prompts.PLANNER_SCHEMA,
            base=llm.OLLAMA_UTILITY,
        )
        raw = out.strip()
        data = aux.parse_plan_json(raw)
        if data:
            if cast and not (data.get("foco") or "").strip():
                hay = " ".join(
                    str(data.get(key) or "")
                    for key in (
                        "objetivo",
                        "npc_intencao",
                        "npc_emocao",
                        "consequencia",
                        "vinculo",
                    )
                )
                hay += " " + " ".join(
                    str(name)
                    for name in ((data.get("estado") or {}).get("npcs") or {})
                )
                hay += " " + (user_msg or "")
                data["foco"] = _infer_foco(cast, npc, hay)
            data = commitments.infer_from_user(
                data,
                user_msg,
                data.get("foco") or (npc.get("name") if npc else ""),
                [c.get("name") for c in (cast or [])],
            )
            data = _sanitize_plan(
                data, npc, allow_secrets=nr >= aux.SECRET_GATE
            )
            existing_facts = [
                dict(row) for row in conn.execute(
                    "SELECT entity, fact FROM entity_facts WHERE session_id=?",
                    (sess["id"],),
                ).fetchall()
            ]
            data, cwarn = commitments.validate_plan(
                conn, sess["id"], data, state
            )
            data, wwarn = worldcheck.validate(
                data, state, npc, cast, existing_facts=existing_facts
            )
            warnings = cwarn + wwarn
            if apply_side_effects:
                turn = int((state or {}).get("turn", 0)) + 1
                commitments.audit_plan(conn, sess["id"], data, state, cwarn)
                commitments.record_warnings(
                    conn, sess["id"], turn, wwarn, "world"
                )
                _apply_plan_threads(conn, sess["id"], data, turn)
            if warnings:
                print("[commitments/world]", " | ".join(warnings)[:500])
            text = aux.plan_text(data)
            print(
                "[planner-json]",
                data.get("nr_delta"),
                "|",
                text[:400].replace("\n", " | "),
            )
            return text, data
        raw = guard.scrub(
            raw, npc, allow_secrets=nr >= aux.SECRET_GATE
        )
        pp = aux.parse_plan(raw)
        foco_name = _infer_foco(
            cast, npc, (user_msg or "") + " " + raw
        )
        estado = {}
        if (
            cast
            and foco_name
            and any((c.get("name") or "").strip() == foco_name for c in cast)
        ):
            estado = {"npcs": {foco_name: "presente na cena"}}
        if not pp:
            print("[planner] resposta sem campos; descartada", repr(raw[:300]))
            return "", {
                "foco": foco_name,
                "ritmo": "",
                "cena": {"acao": "continuar", "titulo": "", "local": "", "presentes": [], "transicao": "", "motivo": ""},
                "marco": {"tipo": "", "descricao": "", "impacto": ""},
                "nr_delta": 0,
                "nr_por_npc": {},
                "vinculo": "",
                "objetivo": "",
                "npc_intencao": "",
                "npc_emocao": "",
                "consequencia": "",
                "detalhes": [],
                "evitar": [],
                "fatos": [],
                "estado": estado,
                "threads": [],
                "fatos_entidade": [],
            }
        print(
            "[planner-fallback]",
            pp.get("NR_DELTA"),
            "|",
            (pp.get("VINCULO") or "")[:80],
        )
        print("[planner]", raw[:400].replace("\n", " | "))

        def split_field(key, size):
            return [
                item.strip()[:size]
                for item in (pp.get(key) or "").split(";")
                if item.strip()
            ]

        return raw, {
            "foco": foco_name,
            "ritmo": (pp.get("RITMO") or "").strip(),
            "cena": {"acao": "continuar", "titulo": "", "local": "", "presentes": [], "transicao": "", "motivo": ""},
            "marco": {"tipo": "", "descricao": "", "impacto": ""},
            "nr_delta": aux._parse_delta(pp.get("NR_DELTA", "")),
            "nr_por_npc": {},
            "vinculo": (pp.get("VINCULO") or "").strip(),
            "objetivo": (pp.get("OBJETIVO") or "").strip(),
            "npc_intencao": (pp.get("NPC_INTENCAO") or "").strip(),
            "npc_emocao": (pp.get("NPC_EMOCAO") or "").strip(),
            "consequencia": (pp.get("CONSEQUENCIA") or "").strip(),
            "detalhes": split_field("DETALHES", 160)[:3],
            "evitar": split_field("EVITAR", 160)[:2],
            "fatos": split_field("FATOS", 180)[:2],
            "estado": estado,
            "threads": [],
            "fatos_entidade": [],
        }
    except Exception as exc:
        print("[planner] erro:", exc)
        return "", None


_KW_STOP = {"para", "como", "voce", "você", "mais", "isso", "aqui", "onde",
            "porque", "sobre", "entre", "quando", "depois", "antes", "nada",
            "alguem", "algum", "muito", "pouco", "esta", "este", "esse", "essa"}


def _kw(s):
    words = re.findall(r"[a-z0-9áéíóúâêôãõàç]{4,}", (s or "").lower())
    return {w for w in words if w not in _KW_STOP}


def _budget_notes(notes, max_chars):
    out = []
    used = 0
    for note in notes or []:
        if not note:
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(note) > remaining:
            if remaining >= 200:
                out.append(note[:remaining].rstrip() + "\n[...]")
            break
        out.append(note)
        used += len(note) + 2
    return out


def _fit_messages(msgs, max_chars):
    total = sum(len(m.get("content") or "") for m in msgs)
    while total > max_chars and len(msgs) > 2:
        total -= len(msgs.pop(1).get("content") or "")
    if total > max_chars and msgs:
        tail = sum(len(m.get("content") or "") for m in msgs[1:])
        keep = max(1000, max_chars - tail)
        msgs[0]["content"] = msgs[0]["content"][:keep]
        print("[context] system truncado para caber no orcamento")
    return msgs


def _lore_block(conn, world, user_msg):
    if not world:
        return ""
    facts = conn.execute(
        "SELECT text, keys FROM world_facts WHERE world_id=?", (world["id"],)
    ).fetchall()
    if not facts:
        return ""
    q = _kw(user_msg)
    if not q:
        return ""
    scored = []
    for f in facts:
        k = _kw((f["keys"] or "") + " " + (f["text"] or ""))
        score = len(q & k)
        if score > 0:
            scored.append((score, f["text"]))
    scored.sort(key=lambda x: -x[0])
    lines = []
    used = 0
    for _, text in scored:
        line = "- " + text[:300]
        if used + len(line) > LORE_BUDGET_CHARS:
            if not lines:
                lines.append(line[:LORE_BUDGET_CHARS])
            break
        lines.append(line)
        used += len(line) + 1
        if len(lines) >= 4:
            break
    if not lines:
        return ""
    return "## LORE RELEVANTE (use se fizer sentido; nao invente)\n" + "\n".join(
        lines
    )


def _memory_block(conn, sid, query, exclude_ids=None):
    if not MEMORY_SEARCH or not (query or "").strip():
        return ""
    hits = db.search_messages(
        conn, sid, query, limit=MEMORY_HITS, exclude_ids=exclude_ids
    )
    if not hits:
        return ""
    metrics.inc("memory_fts_hits", len(hits))
    lines = []
    used = 0
    for hit in hits:
        content = (hit["content"] or "").strip().replace("\n", " ")
        if not content:
            continue
        line = f"- [{hit['role']}] {content[:240]}"
        if used + len(line) > MEMORY_BUDGET:
            break
        lines.append(line)
        used += len(line)
    if not lines:
        return ""
    return (
        "## MEMORIA RELEVANTE (trechos antigos; use se coerente)\n"
        + "\n".join(lines)
    )


def build_context(
    conn,
    sess,
    npc,
    protagonist,
    world,
    extra_notes=None,
    query="",
    extra_memory="",
    cast=None,
    state=None,
    memory_cards=None,
):
    system = prompts.build_system(
        npc, protagonist, world, None, cast=cast, state=state,
        memory_cards=memory_cards, place=_session_place(conn, sess),
    )
    chapters = conn.execute(
        "SELECT number, summary, end_id FROM chapters WHERE session_id=? ORDER BY number",
        (sess["id"],),
    ).fetchall()
    if chapters:
        blocks = []
        used = 0
        for chapter in reversed(chapters[-8:]):
            block = f"### Capitulo {chapter['number']}\n{chapter['summary']}"
            if used + len(block) > CHAPTER_BUDGET_CHARS:
                if not blocks:
                    blocks.append(block[:CHAPTER_BUDGET_CHARS])
                break
            blocks.append(block)
            used += len(block) + 2
        chap_txt = "\n\n".join(reversed(blocks))
        system += "\n\n## CAPITULOS ANTERIORES (memoria)\n" + chap_txt
        last_end = chapters[-1]["end_id"]
    else:
        if sess["summary"]:
            system += (
                "\n\n## MEMORIA (resumo do que ja aconteceu)\n"
                + sess["summary"][:SUMMARY_BUDGET_CHARS]
            )
        last_end = sess["summarized_until"]
    msgs = [{"role": "system", "content": system}]
    rows = conn.execute(
        "SELECT id, role, content FROM messages WHERE session_id=? AND id>? ORDER BY id",
        (sess["id"], last_end),
    ).fetchall()
    rows = rows[-HISTORY_HARD_CAP:]
    mem = _memory_block(
        conn, sess["id"], query, exclude_ids=[r["id"] for r in rows]
    )
    if mem:
        system += "\n\n" + mem
        msgs[0] = {"role": "system", "content": system}
    if extra_memory:
        system += "\n\n" + extra_memory
        msgs[0] = {"role": "system", "content": system}
    if extra_notes:
        notes = _budget_notes(extra_notes, EXTRA_BUDGET_CHARS)
        if notes:
            system = (
                system
                + "\n\n## INSTRUCOES DESTE TURNO\n"
                + "\n\n".join(notes)
            )
            msgs[0] = {"role": "system", "content": system}
    for r in rows:
        msgs.append({"role": r["role"], "content": r["content"]})
    return _fit_messages(msgs, PROMPT_BUDGET_CHARS)


async def _auto_index(sid):
    conn = db.connect()
    try:
        indexed = await embeddings.index_session(conn, sid, limit=500)
        if indexed:
            print("[rag] indexados:", indexed)
    except Exception as exc:
        metrics.inc("rag_errors")
        print("[rag] index erro:", exc)
    finally:
        conn.close()


async def _auto_consistency(sid):
    conn = db.connect()
    started = time.perf_counter()
    try:
        result = await consistency.check(conn, sid)
        consistency.save(conn, sid, result)
        metrics.inc("consistency_checks")
        metrics.observe(
            "consistency_seconds", time.perf_counter() - started
        )
        print(
            "[consistency]",
            result.get("ok"),
            result.get("severity"),
            (result.get("summary") or "")[:160].replace("\n", " | "),
        )
    except Exception as exc:
        metrics.inc("consistency_errors")
        print("[consistency] erro:", exc)
    finally:
        conn.close()


async def _maybe_summarize(sid):
    conn = db.connect()
    try:
        sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        if not sess:
            return
        rows = conn.execute(
            "SELECT id, role, content FROM messages WHERE session_id=? AND id>? ORDER BY id",
            (sid, sess["summarized_until"]),
        ).fetchall()
        if len(rows) <= SUMMARIZE_AFTER:
            return
        chunk = rows[:SUMMARIZE_CHUNK]
        char = conn.execute(
            "SELECT * FROM characters WHERE id=?", (sess["character_id"],)
        ).fetchone()
        model = llm.UTILITY_MODEL
        convo = "\n".join(f"{r['role']}: {r['content']}" for r in chunk)
        prev = sess["summary"] or "(vazio)"
        user = f"Resumo atual:\n{prev}\n\nNovas mensagens:\n{convo}\n\nGere o resumo atualizado."
        new = await llm.complete(
            model,
            [{"role": "system", "content": prompts.SUMMARY_SYSTEM},
             {"role": "user", "content": user}],
            False,
            {"temperature": 0.3, "num_ctx": 8192},
            base=llm.OLLAMA_UTILITY,
        )
        new = new.strip()
        if new:
            conn.execute(
                "UPDATE sessions SET summary=?, summarized_until=? WHERE id=?",
                (new, chunk[-1]["id"], sid),
            )
            conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def _extract_json(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    i, j = t.find("{"), t.rfind("}")
    if i == -1 or j == -1:
        raise ValueError("JSON nao encontrado")
    return json.loads(t[i:j + 1])


# ── raiz ─────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    host = request.url.hostname or os.environ.get("LUMINUS_HOST", "127.0.0.1")
    return HTMLResponse(
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Luminus API</title><style>body{background:#0B0A14;color:#eee;"
        "font-family:sans-serif;padding:32px;line-height:1.5}a{color:#a99bff}"
        "code{background:#1A1730;padding:2px 6px;border-radius:6px}</style></head>"
        "<body><h1>Luminus API</h1>"
        "<p>Servidor no ar. Isto e a API, nao o app.</p>"
        "<ul><li><a href='/docs'>Documentacao interativa (/docs)</a></li>"
        "<li><a href='/api/health'>Status (/api/health)</a></li></ul>"
        "<p>Para instalar o app no celular, baixe o APK na porta <code>8000</code>: "
        f"<code>http://{host}:8000</code></p>"
        "</body></html>"
    )


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


# ── health ───────────────────────────────────────────────────────────────────
@app.get("/api/ping")
async def ping():
    return {"ok": True}


_warm_state = {
    "running": False,
    "main": "idle",
    "utility": "idle",
    "fallback": "",
    "error": "",
    "started": 0.0,
    "finished": 0.0,
}
_warm_task: asyncio.Task | None = None
_models_cache: list[str] = []


def _same_model(a: str, b: str) -> bool:
    return a.split(":")[0] == b.split(":")[0]


async def _warm_one(base: str, model: str, num_ctx: int, key: str) -> None:
    """Carrega um modelo para o warmup.

    Para o Narrador principal, se o carregamento falhar por GPU/CUDA, o
    gpu_guard degrada o tier e este laço tenta o próximo modelo que cabe na
    VRAM restante — sem exigir reinício do servidor.
    """
    global _utility_model_cpu_only
    attempts = 0
    while True:
        attempts += 1
        if (
            key == "utility"
            and _gpu_guard is not None
            and not _gpu_guard.utility_healthy
        ):
            # Sem a RTX 2060 o app continua narrando; apenas planner, resumo,
            # capítulos e RAG ficam indisponíveis.
            _warm_state[key] = "ready"
            _warm_state["error"] = (
                "utility: GPU utilitária ausente; recursos auxiliares desativados"
            )
            print(
                "[warmup] GPU utilitária ausente; narração segue sem planner/RAG",
                flush=True,
            )
            return
        if key == "main":
            if not llm.primary_ok():
                fallback = llm.FALLBACK_MODEL or _active_main_model
                _warm_state[key] = "fallback" if fallback else "error"
                _warm_state["fallback"] = fallback
                _warm_state["error"] = (
                    "main: sem GPUs principais disponíveis; usando reserva"
                )
                return
            model = _active_main_model or model
            num_ctx = _active_num_ctx or num_ctx
        try:
            if not any(_same_model(model, name) for name in await llm.loaded(base)):
                _warm_state[key] = "loading"
                await llm.warm(model, num_ctx, base)
            vram = await llm.model_vram(model, base)
            if key == "main" and vram == 0:
                raise RuntimeError(
                    "modelo principal carregado 100% na CPU (sem VRAM); "
                    "verifique a GPU do barramento"
                )
            if key == "utility" and vram == 0:
                if attempts == 1:
                    # Descoberta CUDA pode falhar na primeira tentativa
                    # (concorrência com o Ollama principal no boot).
                    print(
                        "[warmup] utilitário subiu na CPU; tentando recarregar "
                        "na GPU uma vez",
                        flush=True,
                    )
                    await llm.unload(model, base)
                    await asyncio.sleep(2)
                    _warm_state[key] = "loading"
                    continue
                _utility_model_cpu_only = True
                llm.set_utility_available(False)
                _warm_state[key] = "ready"
                _warm_state["error"] = (
                    "utility: modelo carregado 100% na CPU (CUDA indisponível); "
                    "planner/resumo/RAG desativados"
                )
                print(
                    "[warmup] utilitário sem GPU; narração segue sem "
                    "planner/RAG",
                    flush=True,
                )
                return
            if key == "utility":
                _utility_model_cpu_only = False
                llm.set_utility_available(True)
            _warm_state[key] = "ready"
            if key == "main":
                llm.set_cpu_mode(False)
                llm.reset_primary()
                if _gpu_guard is not None:
                    await _gpu_guard.note_model_success(model)
            return
        except Exception as exc:
            _warm_state[key] = "error"
            _warm_state["error"] = f"{key}: {exc}"
            if key != "main":
                # Falha da GPU/modelo utilitário não deve impedir a narração.
                # O app entra com o principal e perde apenas os auxiliares.
                _warm_state[key] = "ready"
                print(
                    f"[warmup] {key} indisponível ({exc}); "
                    "narração segue sem recursos auxiliares",
                    flush=True,
                )
                return
            print(f"[warmup] main ({model}) falhou: {exc}", flush=True)
            await llm.unload(model, base)
            if (
                _gpu_guard is not None
                and attempts <= 3
                and not llm.cpu_mode()
            ):
                await _gpu_guard.note_model_failure(model, str(exc))
                nxt_model = _active_main_model
                nxt_ctx = _active_num_ctx
                if nxt_model and not _model_eq(nxt_model, model):
                    print(
                        f"[warmup] tentando modelo reserva {nxt_model} "
                        f"(ctx {nxt_ctx}) nas GPUs restantes",
                        flush=True,
                    )
                    model, num_ctx = nxt_model, nxt_ctx
                    _warm_state[key] = "loading"
                    continue
            llm.disable_primary()
            fallback = llm.FALLBACK_MODEL or _active_main_model
            _warm_state[key] = "fallback" if fallback else "error"
            _warm_state["fallback"] = fallback
            print(
                f"[warmup] main indisponivel; usando reserva "
                f"{fallback or 'nenhuma'}",
                flush=True,
            )
            return


async def _warm_all() -> None:
    await asyncio.gather(
        _warm_one(llm.OLLAMA, DEFAULT_MODEL, NUM_CTX, "main"),
        _warm_one(llm.OLLAMA_UTILITY, PLANNER_MODEL, 8192, "utility"),
    )
    _warm_state["running"] = False
    _warm_state["finished"] = time.time()


@app.post("/api/warmup")
async def start_warmup():
    global _warm_task
    if (
        not _warm_state.get("running")
        and _warm_state.get("utility") == "ready"
        and _warm_state.get("main") in ("ready", "fallback")
    ):
        return _warm_state
    if not _warm_state.get("running"):
        if _gpu_guard is not None:
            try:
                await _gpu_guard.evaluate(force=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[gpu-guard] reavaliação no warmup falhou: {exc}", flush=True)
            if _gpu_guard.main_healthy >= 1:
                llm.reset_primary()
            else:
                llm.disable_primary()
        else:
            llm.reset_primary()
        _warm_state.update(
            running=True,
            main="loading",
            utility="loading",
            fallback="",
            error="",
            started=time.time(),
            finished=0.0,
        )
        _warm_task = asyncio.create_task(_warm_all())
    return _warm_state


@app.get("/api/warmup")
async def warmup_status():
    return _warm_state


@app.get("/api/health")
async def health():
    global _models_cache
    ok = False
    try:
        models = await asyncio.wait_for(
            llm.list_models(), timeout=HEALTH_OLLAMA_TIMEOUT
        )
        _models_cache = models
        ok = True
    except Exception:
        models = _models_cache
    return {
        "status": "ok",
        "ollama": ok,
        "default_model": _active_main_model,
        "configured_model": DEFAULT_MODEL,
        "active_model": _active_main_model,
        "active_num_ctx": _active_num_ctx,
        "active_tier": _active_tier,
        "fallback_model": llm.FALLBACK_MODEL,
        "utility_available": llm.utility_available(),
        "deepseek": llm.deepseek_status(),
        "npc_forge": npc_forge.status(),
        "models": models,
        "gpu": _gpu_guard.status() if _gpu_guard is not None else None,
        "art": art.status(),
        "warmup": _warm_state,
    }


@app.get("/api/gpu")
async def gpu_status():
    """Estado do vigia de GPUs e modelo efetivo do Narrador."""
    if _gpu_guard is None:
        return {"available": False, "tier": _active_tier,
                "model": _active_main_model}
    return {"available": True, **_gpu_guard.status()}


@app.post("/api/gpu/check")
async def gpu_check():
    """Força uma leitura imediata do hardware (ignora a histerese)."""
    if _gpu_guard is None:
        return {"available": False}
    assessment = await _gpu_guard.evaluate(force=True)
    return {"available": True, **assessment.as_dict()}


@app.post("/api/gpu/reset")
async def gpu_reset():
    """Limpa a memória de falha e tenta voltar ao modelo principal."""
    if _gpu_guard is None:
        return {"available": False}
    _gpu_guard.clear_override()
    assessment = await _gpu_guard.evaluate(force=True)
    return {"available": True, **assessment.as_dict()}


@app.get("/api/deepseek")
async def deepseek_status_endpoint():
    return llm.deepseek_status()


@app.post("/api/deepseek")
async def deepseek_mode(body: DeepSeekIn):
    """Liga/desliga o modo forçado do Narrador via DeepSeek."""
    llm.set_force_deepseek(body.enabled)
    status = llm.deepseek_status()
    print(
        f"[deepseek] modo forçado {'ON' if body.enabled else 'OFF'} "
        f"(ativo={status['active']})",
        flush=True,
    )
    return status


@app.get("/api/models")
async def models():
    return {"models": await llm.list_models()}


# ── TTS (narracao por voz) ──────────────────────────────────────────────────
class TtsIn(BaseModel):
    text: str
    voice: str = ""


@app.get("/api/voices")
async def voices():
    return {"voices": tts.available_voices(), "default": tts.DEFAULT_VOICE}


@app.post("/api/tts")
async def synth(body: TtsIn):
    if not (body.text or "").strip():
        raise HTTPException(400, "texto vazio")
    try:
        data = await asyncio.to_thread(tts.synthesize, body.text, body.voice)
    except Exception as e:
        raise HTTPException(500, f"tts: {e}")
    return Response(content=data, media_type="audio/wav")


# ── capas com IA (DeepSeek + gerador de imagem) ─────────────────────────────
def _identity_body(char: dict, world: dict | None) -> dict:
    return {
        **char,
        "world": (world or {}).get("name") or "",
        "style": char.get("visual_style") or "anime",
    }


def _save_identity(cid: int, identity: dict, clear_portraits: bool = False):
    conn = db.connect()
    if clear_portraits:
        conn.execute(
            "UPDATE characters SET visual_prompt=?, art_seed=?, visual_style=?,"
            " portraits='{}' WHERE id=?",
            (
                identity["prompt"],
                int(identity["seed"]),
                identity["style"],
                cid,
            ),
        )
        conn.execute(
            "UPDATE characters SET avatar='' WHERE id=? AND avatar LIKE '/art/%'",
            (cid,),
        )
    else:
        conn.execute(
            "UPDATE characters SET visual_prompt=?, art_seed=?, visual_style=?"
            " WHERE id=?",
            (
                identity["prompt"],
                int(identity["seed"]),
                identity["style"],
                cid,
            ),
        )
    conn.commit()
    conn.close()


@app.post("/api/art/cover")
async def generate_cover(body: CoverIn):
    data = body.model_dump()
    identity = None
    cid = data.get("character_id")
    if cid:
        conn = db.connect()
        char = _char(conn, cid)
        world = (
            _world(conn, char["world_id"])
            if char and char.get("world_id")
            else None
        )
        conn.close()
        if char:
            world_name = (world or {}).get("name") or ""
            db_body = _identity_body(char, world)
            changed = (
                (data.get("name") or "").strip()
                != (char.get("name") or "").strip()
                or (data.get("appearance") or "").strip()
                != (char.get("appearance") or "").strip()
                or (data.get("tags") or []) != (char.get("tags") or [])
                or (data.get("kind") or "") != (char.get("kind") or "")
                or (data.get("world") or "").strip() != world_name.strip()
            )
            if changed:
                identity = await art.ensure_identity(
                    {
                        **db_body,
                        **data,
                        "character_id": cid,
                        "visual_prompt": "",
                        "art_seed": 0,
                    }
                )
            else:
                identity = await art.ensure_identity(db_body)
                _save_identity(cid, identity)
    if identity is None:
        identity = await art.ensure_identity(data)
    data["visual_prompt"] = identity["prompt"]
    data["seed"] = identity["seed"]
    data["style"] = identity["style"]
    reload_models = list(dict.fromkeys([PLANNER_MODEL, llm.UTILITY_MODEL]))
    try:
        result = await art.generate_cover(data, reload_models=reload_models)
        result["visual_prompt"] = identity["prompt"]
        result["art_seed"] = identity["seed"]
        result["visual_style"] = identity["style"]
        if identity.get("warning") and not result.get("warning"):
            result["warning"] = identity["warning"]
        return result
    except art.ArtError as e:
        raise HTTPException(e.status, e.message)


@app.post("/api/characters/{cid}/portraits")
async def generate_character_portraits(cid: int):
    conn = db.connect()
    char = _char(conn, cid)
    world = _world(conn, char["world_id"]) if char and char.get("world_id") else None
    conn.close()
    if not char:
        raise HTTPException(404, "personagem nao encontrado")
    identity = await art.ensure_identity(_identity_body(char, world))
    _save_identity(cid, identity)
    reload_models = list(dict.fromkeys([PLANNER_MODEL, llm.UTILITY_MODEL]))
    expressions = {
        "neutral": "calm neutral expression",
        "tense": "tense guarded expression, furrowed brows",
        "smile": "warm soft smile",
    }
    portraits = {}
    warnings = []
    errors = []
    for key, expression in expressions.items():
        body = {
            "character_id": cid,
            "name": char.get("name") or "",
            "visual_prompt": identity["prompt"],
            "seed": identity["seed"],
            "style": identity["style"],
            "width": 768,
            "height": 1024,
            "expression": expression,
        }
        try:
            result = await art.generate_cover(
                body, reload_models=reload_models
            )
            url = (result.get("url") or "").strip()
            if url:
                portraits[key] = url
            if result.get("warning"):
                warnings.append(str(result["warning"]))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{key}: {exc}")
            print(f"[portraits] {key} falhou: {exc}", flush=True)
    if not portraits:
        raise HTTPException(
            502,
            "Nao consegui gerar os retratos: " + "; ".join(errors[:2]),
        )
    conn = db.connect()
    conn.execute(
        "UPDATE characters SET portraits=? WHERE id=?",
        (json.dumps(portraits, ensure_ascii=False), cid),
    )
    if not (char.get("avatar") or "").strip() and portraits.get("neutral"):
        conn.execute(
            "UPDATE characters SET avatar=? WHERE id=?",
            (portraits["neutral"], cid),
        )
    conn.commit()
    conn.close()
    if identity.get("warning"):
        warnings.append(str(identity["warning"]))
    return {
        "portraits": portraits,
        "identity": identity["prompt"],
        "seed": identity["seed"],
        "warning": "; ".join(dict.fromkeys(warnings)),
        "errors": errors,
    }


@app.post("/api/characters/{cid}/visual")
async def regenerate_character_visual(cid: int):
    conn = db.connect()
    char = _char(conn, cid)
    world = _world(conn, char["world_id"]) if char and char.get("world_id") else None
    conn.close()
    if not char:
        raise HTTPException(404, "personagem nao encontrado")
    identity = await art.ensure_identity(_identity_body(char, world), force=True)
    _save_identity(cid, identity, clear_portraits=True)
    return identity


@app.post("/api/art/free")
async def generate_free_image(body: ArtFreeIn):
    reload_models = list(dict.fromkeys([PLANNER_MODEL, llm.UTILITY_MODEL]))
    try:
        return await art.generate_free(body.model_dump(), reload_models=reload_models)
    except art.ArtError as e:
        raise HTTPException(e.status, e.message)


@app.get("/api/art/gallery")
async def art_gallery(limit: int = 40):
    return art.gallery(limit)


@app.delete("/api/art/{name}")
async def delete_art(name: str, x_admin_key: str = Header(default="")):
    key = os.environ.get("LUMINUS_ADMIN_KEY", "").strip()
    if key and not hmac.compare_digest(key, x_admin_key):
        raise HTTPException(403, "Chave de administrador invalida.")
    safe = os.path.basename(name)
    if safe != name or not safe.lower().endswith((".jpg", ".jpeg", ".png")):
        raise HTTPException(400, "Nome de imagem invalido.")
    path = os.path.join(art.ART_DIR, safe)
    if not os.path.isfile(path):
        raise HTTPException(404, "Imagem nao encontrada.")
    os.remove(path)
    ref = f"/art/{safe}"
    conn = db.connect()
    conn.execute(
        "UPDATE sessions SET scene_image='' WHERE scene_image=?", (ref,)
    )
    conn.execute(
        "UPDATE characters SET avatar='' WHERE avatar=?", (ref,)
    )
    conn.commit()
    conn.close()
    return {"deleted": safe}


@app.post("/api/sessions/{sid}/scene")
async def generate_scene(sid: int, body: SceneIn | None = None):
    conn = db.connect()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    char = _char(conn, sess["character_id"])
    if not char:
        conn.close()
        raise HTTPException(404, "personagem nao encontrado")
    world = _world(conn, char["world_id"]) if char.get("world_id") else None
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 6",
        (sid,),
    ).fetchall()
    rows = list(reversed(rows))
    context = "\n".join(
        f"{'Jogador' if r['role'] == 'user' else 'Narrador'}: "
        f"{(r['content'] or '')[:700]}"
        for r in rows
    )
    setting = ""
    for r in reversed(rows):
        if r["role"] == "assistant" and (r["content"] or "").strip():
            setting = (r["content"] or "").strip()[:900]
            break
    if not setting:
        setting = (
            char.get("greeting") or char.get("description_public") or ""
        ).strip()[:900]
    payload = {
        "name": char.get("name") or "",
        "appearance": char.get("appearance") or "",
        "world": (world or {}).get("name") or "",
        "world_description": (world or {}).get("description") or "",
        "setting": setting,
        "context": context,
        "style": (body.style if body else "anime") or "anime",
        "seed": body.seed if body else None,
        "width": body.width if body else None,
        "height": body.height if body else None,
    }
    reload_models = list(
        dict.fromkeys([PLANNER_MODEL, llm.UTILITY_MODEL])
    )
    try:
        result = await art.generate_scene(payload, reload_models=reload_models)
    except art.ArtError as e:
        conn.close()
        raise HTTPException(e.status, e.message)
    except Exception as e:
        conn.close()
        raise HTTPException(502, f"Falha ao ilustrar a cena: {e}")
    conn.execute(
        "UPDATE sessions SET scene_image=?, scene_prompt=? WHERE id=?",
        (result["url"], result["prompt"], sid),
    )
    conn.commit()
    conn.close()
    return result


# ── worlds ───────────────────────────────────────────────────────────────────
@app.get("/api/metrics")
async def get_metrics():
    conn = db.connect()
    counts = {}
    for table in (
        "sessions",
        "messages",
        "chapters",
        "threads",
        "entity_facts",
        "message_states",
        "message_vectors",
        "consistency_checks",
        "world_voices",
    ):
        counts[table] = conn.execute(
            f"SELECT count(*) FROM {table}"
        ).fetchone()[0]
    conn.close()
    out = metrics.snapshot()
    out["db"] = counts
    try:
        out["db_size_bytes"] = os.path.getsize(db.DB_PATH)
    except OSError:
        out["db_size_bytes"] = 0
    out["config"] = {
        "main_model": _active_main_model,
        "configured_model": DEFAULT_MODEL,
        "gpu_tier": _active_tier,
        "gpu": _gpu_guard.status() if _gpu_guard is not None else None,
        "planner_model": PLANNER_MODEL,
        "embed_model": embeddings.MODEL,
        "rag_enabled": embeddings.ENABLED,
        "planner_enabled": PLANNER_ENABLED,
        "consistency_auto": CONSISTENCY_AUTO,
        "num_ctx": NUM_CTX,
        "prompt_budget_chars": PROMPT_BUDGET_CHARS,
    }
    return out


@app.get("/api/worlds")
async def list_worlds():
    conn = db.connect()
    rows = conn.execute("SELECT * FROM worlds ORDER BY id DESC").fetchall()
    conn.close()
    return [db.rowdict(r) for r in rows]


@app.post("/api/worlds")
async def create_world(w: WorldIn):
    conn = db.connect()
    cur = conn.execute(
        "INSERT INTO worlds (name, description, created_at) VALUES (?,?,?)",
        (w.name, w.description, time.time()),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM worlds WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return db.rowdict(row)


@app.put("/api/worlds/{wid}")
async def update_world(wid: int, w: WorldIn):
    conn = db.connect()
    ok = conn.execute("SELECT id FROM worlds WHERE id=?", (wid,)).fetchone()
    if not ok:
        conn.close()
        raise HTTPException(404, "mundo nao encontrado")
    conn.execute(
        "UPDATE worlds SET name=?, description=? WHERE id=?",
        (w.name, w.description, wid),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM worlds WHERE id=?", (wid,)).fetchone()
    conn.close()
    return db.rowdict(row)


@app.get("/api/worlds/{wid}/export")
async def export_world_pack(wid: int, include_sessions: bool = True):
    """Exporta mundo + elenco + lore + sessões em um World Pack JSON."""
    conn = db.connect()
    try:
        pack = world_pack.export_world(conn, wid, include_sessions=include_sessions)
    except ValueError as exc:
        conn.close()
        raise HTTPException(404, str(exc))
    conn.close()
    filename = f"luminus-world-{wid}.json"
    return JSONResponse(
        content=pack,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/worlds/import")
async def import_world_pack(request: Request):
    """Importa um World Pack, criando um mundo novo (sem sobrescrever)."""
    try:
        pack = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"JSON invalido: {exc}")
    conn = db.connect()
    try:
        counts = world_pack.import_world(conn, pack)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        conn.close()
        raise HTTPException(400, f"falha ao importar world pack: {exc}")
    conn.close()
    return {"imported": counts}


@app.get("/api/worlds/{wid}/facts")
async def list_facts(wid: int):
    conn = db.connect()
    rows = conn.execute(
        "SELECT * FROM world_facts WHERE world_id=? ORDER BY id", (wid,)
    ).fetchall()
    conn.close()
    return [db.rowdict(r) for r in rows]


@app.post("/api/worlds/{wid}/facts")
async def add_fact(wid: int, f: FactIn):
    conn = db.connect()
    ok = conn.execute("SELECT id FROM worlds WHERE id=?", (wid,)).fetchone()
    if not ok:
        conn.close()
        raise HTTPException(404, "mundo nao encontrado")
    cur = conn.execute(
        "INSERT INTO world_facts (world_id, text, keys, created_at) VALUES (?,?,?,?)",
        (wid, f.text.strip(), f.keys.strip(), time.time()),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM world_facts WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return db.rowdict(row)


@app.delete("/api/facts/{fid}")
async def del_fact(fid: int):
    conn = db.connect()
    conn.execute("DELETE FROM world_facts WHERE id=?", (fid,))
    conn.commit()
    conn.close()
    return {"deleted": fid}


# ── ambientes proprios (places) ──────────────────────────────────────────────
def _place_out(row):
    data = db.rowdict(row)
    if not data:
        return None
    raw = data.get("tags") or "[]"
    if isinstance(raw, str):
        try:
            tags = json.loads(raw) if raw.strip() else []
        except ValueError:
            tags = [part.strip() for part in raw.split(",") if part.strip()]
        data["tags"] = tags if isinstance(tags, list) else []
    return data


def _place_values(p: PlaceIn):
    return (
        p.world_id,
        p.name.strip(),
        (p.kind or "ambiente").strip(),
        p.description.strip(),
        p.atmosphere.strip(),
        p.rules.strip(),
        _tags_json(p.tags),
        time.time(),
    )


@app.get("/api/places")
async def list_places(world_id: int | None = None):
    conn = db.connect()
    if world_id is None:
        rows = conn.execute("SELECT * FROM places ORDER BY id DESC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM places WHERE world_id=? ORDER BY id DESC", (world_id,)
        ).fetchall()
    conn.close()
    return [_place_out(r) for r in rows]


@app.post("/api/places")
async def create_place(p: PlaceIn):
    if not p.name.strip():
        raise HTTPException(400, "nome do ambiente e obrigatorio")
    conn = db.connect()
    if p.world_id is not None:
        ok = conn.execute("SELECT id FROM worlds WHERE id=?", (p.world_id,)).fetchone()
        if not ok:
            conn.close()
            raise HTTPException(404, "mundo nao encontrado")
    cur = conn.execute(
        "INSERT INTO places "
        "(world_id, name, kind, description, atmosphere, rules, tags, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        _place_values(p),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM places WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return _place_out(row)


@app.put("/api/places/{pid}")
async def update_place(pid: int, p: PlaceIn):
    if not p.name.strip():
        raise HTTPException(400, "nome do ambiente e obrigatorio")
    conn = db.connect()
    ok = conn.execute("SELECT id FROM places WHERE id=?", (pid,)).fetchone()
    if not ok:
        conn.close()
        raise HTTPException(404, "ambiente nao encontrado")
    values = _place_values(p)
    conn.execute(
        "UPDATE places SET world_id=?, name=?, kind=?, description=?, "
        "atmosphere=?, rules=?, tags=? WHERE id=?",
        values[:-1] + (pid,),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM places WHERE id=?", (pid,)).fetchone()
    conn.close()
    return _place_out(row)


@app.delete("/api/places/{pid}")
async def delete_place(pid: int):
    conn = db.connect()
    conn.execute("UPDATE sessions SET place_id=NULL WHERE place_id=?", (pid,))
    conn.execute("DELETE FROM places WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return {"deleted": pid}


# ── mana ─────────────────────────────────────────────────────────────────────
MANA_START = int(os.environ.get("LUMINUS_MANA_START", "120"))
MANA_DAILY = int(os.environ.get("LUMINUS_MANA_DAILY", "40"))
MANA_DAILY_SECONDS = 20 * 3600


def _mana_row(conn):
    row = conn.execute("SELECT * FROM mana WHERE id=1").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO mana (id, balance, last_daily, updated_at) VALUES (1,?,?,?)",
            (MANA_START, 0, time.time()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM mana WHERE id=1").fetchone()
    return row


def _mana_payload(row):
    now = time.time()
    last = float(row["last_daily"] or 0)
    remaining = max(0.0, MANA_DAILY_SECONDS - (now - last))
    return {
        "balance": int(row["balance"]),
        "daily_amount": MANA_DAILY,
        "daily_available": remaining <= 0,
        "next_daily_in": int(remaining),
    }


@app.get("/api/mana")
async def get_mana():
    conn = db.connect()
    row = _mana_row(conn)
    conn.close()
    return _mana_payload(row)


@app.post("/api/mana/daily")
async def claim_daily_mana():
    conn = db.connect()
    row = _mana_row(conn)
    now = time.time()
    last = float(row["last_daily"] or 0)
    if now - last < MANA_DAILY_SECONDS:
        payload = _mana_payload(row)
        conn.close()
        payload["claimed"] = False
        return payload
    balance = int(row["balance"]) + MANA_DAILY
    conn.execute(
        "UPDATE mana SET balance=?, last_daily=?, updated_at=? WHERE id=1",
        (balance, now, now),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM mana WHERE id=1").fetchone()
    conn.close()
    payload = _mana_payload(row)
    payload["claimed"] = True
    payload["granted"] = MANA_DAILY
    return payload


@app.post("/api/mana/spend")
async def spend_mana(s: ManaSpendIn):
    amount = max(0, int(s.amount))
    conn = db.connect()
    row = _mana_row(conn)
    balance = int(row["balance"])
    if amount > balance:
        conn.close()
        raise HTTPException(402, "Mana insuficiente")
    new_balance = balance - amount
    conn.execute(
        "UPDATE mana SET balance=?, updated_at=? WHERE id=1",
        (new_balance, time.time()),
    )
    conn.commit()
    conn.close()
    return {"spent": amount, "balance": new_balance, "reason": s.reason}


# ── characters ───────────────────────────────────────────────────────────────
@app.get("/api/characters")
async def list_characters(
    kind: str | None = None,
    include_archived: bool = False,
):
    conn = db.connect()
    where = []
    params: list[object] = []
    if kind:
        where.append("kind=?")
        params.append(kind)
    if not include_archived:
        where.append("archived=0")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        f"SELECT * FROM characters{clause} ORDER BY id DESC", tuple(params)
    ).fetchall()
    conn.close()
    return [char_out(r) for r in rows]


@app.post("/api/characters")
async def create_character(c: CharacterIn):
    conn = db.connect()
    kind = c.kind if c.kind in ("npc", "protagonist") else "npc"
    cur = conn.execute(
        "INSERT INTO characters (kind, name, avatar, tagline, description_public, persona, greeting,"
        " appearance, personality, backstory, speech_style, likes, dislikes, fears, relationship,"
        " secrets, example_dialogue, tags, world_id, model, voice, visual_prompt, art_seed,"
        " visual_style, attributes, skills, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (kind, c.name, c.avatar, c.tagline, c.description_public, c.persona, c.greeting,
         c.appearance, c.personality, c.backstory, c.speech_style, c.likes, c.dislikes, c.fears,
         c.relationship, c.secrets, c.example_dialogue, _tags_json(c.tags), c.world_id, c.model,
         c.voice, c.visual_prompt, c.art_seed, c.visual_style,
         _attributes_json(c.attributes, kind), _skills_json(c.skills, kind), time.time()),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM characters WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return char_out(row)


@app.get("/api/characters/{cid}")
async def get_character(cid: int):
    conn = db.connect()
    out = _char(conn, cid)
    conn.close()
    if not out:
        raise HTTPException(404, "personagem nao encontrado")
    return out


@app.put("/api/characters/{cid}")
async def update_character(cid: int, c: CharacterIn):
    conn = db.connect()
    kind = c.kind if c.kind in ("npc", "protagonist") else "npc"
    old = conn.execute(
        "SELECT name, appearance, tags, world_id, avatar, attributes, skills FROM characters WHERE id=?",
        (cid,),
    ).fetchone()
    conn.execute(
        "UPDATE characters SET kind=?, name=?, avatar=?, tagline=?, description_public=?, persona=?,"
        " greeting=?, appearance=?, personality=?, backstory=?, speech_style=?, likes=?, dislikes=?,"
        " fears=?, relationship=?, secrets=?, example_dialogue=?, tags=?, world_id=?, model=?, voice=?,"
        " visual_prompt=?, art_seed=?, visual_style=?, attributes=?, skills=? WHERE id=?",
        (kind, c.name, c.avatar, c.tagline, c.description_public, c.persona, c.greeting,
         c.appearance, c.personality, c.backstory, c.speech_style, c.likes, c.dislikes, c.fears,
         c.relationship, c.secrets, c.example_dialogue, _tags_json(c.tags), c.world_id, c.model,
         c.voice, c.visual_prompt, c.art_seed, c.visual_style,
         _attributes_json(c.attributes, kind, old["attributes"] if old else None),
         _skills_json(c.skills, kind, old["skills"] if old else None), cid),
    )
    if old and (
        old["name"] != c.name
        or old["appearance"] != c.appearance
        or old["tags"] != _tags_json(c.tags)
        or old["world_id"] != c.world_id
    ):
        conn.execute(
            "UPDATE characters SET portraits='{}' WHERE id=?",
            (cid,),
        )
        if not c.visual_prompt.strip():
            conn.execute(
                "UPDATE characters SET visual_prompt='', art_seed=0 WHERE id=?",
                (cid,),
            )
        if (old["avatar"] or "") == (c.avatar or ""):
            conn.execute(
                "UPDATE characters SET avatar='' WHERE id=? AND avatar LIKE '/art/%'",
                (cid,),
            )
    conn.commit()
    out = _char(conn, cid)
    conn.close()
    if not out:
        raise HTTPException(404, "personagem nao encontrado")
    return out


@app.post("/api/characters/{cid}/archive")
async def archive_character(cid: int, body: ArchiveIn):
    conn = db.connect()
    cur = conn.execute(
        "UPDATE characters SET archived=? WHERE id=?",
        (1 if body.archived else 0, cid),
    )
    conn.commit()
    out = _char(conn, cid) if cur.rowcount else None
    conn.close()
    if not out:
        raise HTTPException(404, "personagem nao encontrado")
    return out


@app.delete("/api/characters/{cid}")
async def delete_character(cid: int):
    conn = db.connect()
    conn.execute("DELETE FROM characters WHERE id=?", (cid,))
    conn.commit()
    conn.close()
    return {"deleted": cid}


# ── assist (criacao/refino com IA) ──────────────────────────────────────────
@app.post("/api/assist/character")
async def assist_character(a: AssistIn):
    conn = db.connect()
    world = _world(conn, a.world_id)
    conn.close()
    world_name = world["name"] if world else ""
    world_desc = world["description"] if world else ""
    user = prompts.creation_prompt(
        a.seed, a.kind, a.tone, world_name, world_desc, a.name,
    )

    # Fluxo profundo inspirado no freellmpool: Pensador abre o dossiê, Juiz
    # critica e a Síntese devolve o JSON final. Se qualquer etapa estrutural
    # falhar, cai no caminho simples abaixo sem quebrar a criação.
    if a.deep and npc_forge.deep_available():
        try:
            draft, forge_info = await npc_forge.forge(
                seed=a.seed,
                kind=a.kind,
                tone=a.tone,
                world_name=world_name,
                world_desc=world_desc,
                name=a.name,
            )
            return {"draft": draft, "forge": forge_info}
        except Exception as exc:  # noqa: BLE001
            print(
                f"[assist] criação profunda falhou ({exc}); "
                "usando geração simples",
                flush=True,
            )

    raw = ""
    try:
        raw = await llm.complete(
            PLANNER_MODEL,
            [{"role": "system", "content": prompts.CHARACTER_CREATION_SYSTEM},
             {"role": "user", "content": user}],
            False,
            {"temperature": 0.85, "top_p": 0.95, "num_ctx": 8192, "num_predict": 1200},
            fmt=prompts.CHARACTER_SCHEMA,
            base=llm.OLLAMA_UTILITY,
            force_utility=True,
        )
        draft = _extract_json(raw)
    except Exception as e:
        print("[assist] raw:", repr(raw[:2000]))
        return {"error": str(e), "raw": raw}
    return {"draft": draft}


@app.post("/api/assist/refine")
async def assist_refine(r: RefineIn):
    user = (
        "Ficha atual (JSON):\n" + json.dumps(r.draft, ensure_ascii=False) +
        "\n\nInstrucao do usuario:\n" + r.instruction.strip()
    )
    raw = ""
    try:
        raw = await llm.complete(
            PLANNER_MODEL,
            [{"role": "system", "content": prompts.REFINE_SYSTEM},
             {"role": "user", "content": user}],
            False,
            {"temperature": 0.7, "num_ctx": 8192, "num_predict": 1200},
            fmt=prompts.CHARACTER_SCHEMA,
            base=llm.OLLAMA_UTILITY,
            force_utility=True,
        )
        draft = _extract_json(raw)
    except Exception as e:
        print("[refine] raw:", repr(raw[:2000]))
        return {"error": str(e), "raw": raw}
    return {"draft": draft}


# ── sessions ─────────────────────────────────────────────────────────────────
@app.get("/api/sessions")
async def list_sessions(
    character_id: int | None = None,
    include_archived: bool = False,
):
    conn = db.connect()
    where = []
    params: list[object] = []
    if character_id is not None:
        where.append("character_id=?")
        params.append(character_id)
    if not include_archived:
        where.append("archived=0")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        f"SELECT * FROM sessions{clause} ORDER BY updated_at DESC",
        tuple(params),
    ).fetchall()
    out = [_session_out(conn, r) for r in rows]
    conn.close()
    return out


@app.post("/api/sessions")
async def create_session(s: SessionIn):
    conn = db.connect()
    char = conn.execute("SELECT * FROM characters WHERE id=?", (s.character_id,)).fetchone()
    if not char:
        conn.close()
        raise HTTPException(404, "personagem nao encontrado")
    place = _place(conn, s.place_id)
    if s.place_id is not None and not place:
        conn.close()
        raise HTTPException(404, "ambiente nao encontrado")
    if (
        place
        and place.get("world_id")
        and char["world_id"]
        and int(place["world_id"]) != int(char["world_id"])
    ):
        conn.close()
        raise HTTPException(400, "o ambiente pertence a outro mundo")
    now = time.time()
    title = s.title or f"{char['name']} — {time.strftime('%d/%m %H:%M')}"
    cur = conn.execute(
        "INSERT INTO sessions (character_id, protagonist_id, title, summary, summarized_until,"
        " turn_count, place_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            s.character_id,
            s.protagonist_id,
            title,
            "",
            0,
            0,
            s.place_id,
            now,
            now,
        ),
    )
    sid = cur.lastrowid
    if place:
        seed = {
            "location": place["name"],
            "scene": {
                "title": place["name"],
                "location": place["name"],
            },
        }
        conn.execute(
            "UPDATE sessions SET ledger=? WHERE id=?",
            (json.dumps(seed, ensure_ascii=False), sid),
        )
    cast_ids = list(
        dict.fromkeys(
            int(cid)
            for cid in (s.cast_ids or [])
            if int(cid) != int(s.character_id)
        )
    )[:4]
    if cast_ids:
        placeholders = ",".join("?" for _ in cast_ids)
        valid = {
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM characters WHERE kind='npc' "
                f"AND id IN ({placeholders})",
                tuple(cast_ids),
            ).fetchall()
        }
        for pos, cid in enumerate(cid for cid in cast_ids if cid in valid):
            conn.execute(
                "INSERT OR IGNORE INTO session_cast "
                "(session_id, character_id, position, created_at) "
                "VALUES (?,?,?,?)",
                (sid, cid, pos, now),
            )
    if char["greeting"]:
        _insert_message_once(conn, sid, "assistant", char["greeting"])
    conn.commit()
    row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    out = _session_out(conn, row)
    conn.close()
    return out


@app.post("/api/sessions/{sid}/opening")
async def generate_opening(sid: int):
    lock = _opening_locks.setdefault(sid, asyncio.Lock())
    async with lock:
        return await _opening_body(sid)


async def _opening_body(sid: int):
    conn = db.connect()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    char = _char(conn, sess["character_id"])
    first = conn.execute(
        "SELECT id, role, content FROM messages WHERE session_id=? ORDER BY id LIMIT 1",
        (sid,),
    ).fetchone()
    if sess["opening_generated"] or sess["turn_count"] > 0:
        conn.close()
        return {
            "generated": False,
            "message": db.rowdict(first),
            "title": (sess["title"] or ""),
        }
    if not char:
        conn.close()
        raise HTTPException(404, "personagem nao encontrado")
    protagonist = _char(conn, sess["protagonist_id"])
    world = _world(conn, char["world_id"]) if char.get("world_id") else None
    try:
        text = await _generate_opening(
            char,
            protagonist,
            world,
            cast=_cast_rows(conn, sid),
            place=_session_place(conn, sess),
        )
    except Exception as e:
        fallback = (char.get("greeting") or "").strip()
        conn.close()
        return {
            "generated": False,
            "message": {
                "id": first["id"] if first else 0,
                "role": "assistant",
                "content": fallback,
            },
            "title": (sess["title"] or ""),
            "error": str(e),
        }
    if not text:
        conn.close()
        raise HTTPException(502, "a IA nao retornou a abertura")
    title = (sess["title"] or "").strip()
    if _looks_default_title(title, char["name"]):
        title = await _generate_session_title(text, char, world)
    now = time.time()
    if first and first["role"] == "assistant":
        conn.execute(
            "UPDATE messages SET content=?, created_at=? WHERE id=?",
            (text, now, first["id"]),
        )
        mid = first["id"]
    else:
        cur = conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?,?,?,?)",
            (sid, "assistant", text, now),
        )
        mid = cur.lastrowid
    conn.execute(
        "UPDATE sessions SET opening_generated=1, title=?, updated_at=? WHERE id=?",
        (title, now, sid),
    )
    conn.commit()
    conn.close()
    return {
        "generated": True,
        "message": {"id": mid, "role": "assistant", "content": text},
        "title": title,
    }


@app.get("/api/sessions/{sid}")
async def get_session(sid: int):
    conn = db.connect()
    row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    out = _session_out(conn, row)
    conn.close()
    return out


@app.post("/api/sessions/{sid}/archive")
async def archive_session(sid: int, body: ArchiveIn):
    conn = db.connect()
    cur = conn.execute(
        "UPDATE sessions SET archived=? WHERE id=?",
        (1 if body.archived else 0, sid),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    out = _session_out(conn, row) if cur.rowcount and row else None
    conn.close()
    if not out:
        raise HTTPException(404, "sessao nao encontrada")
    return out


@app.delete("/api/sessions/{sid}")
async def delete_session(sid: int):
    conn = db.connect()
    conn.execute("DELETE FROM chat_requests WHERE session_id=?", (sid,))
    conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
    conn.commit()
    conn.close()
    return {"deleted": sid}


@app.get("/api/sessions/{sid}/messages")
async def get_messages(sid: int):
    conn = db.connect()
    rows = conn.execute(
        "SELECT m.id, m.role, m.content, m.created_at, ms.state AS state "
        "FROM messages m LEFT JOIN message_states ms ON ms.message_id = m.id "
        "WHERE m.session_id=? ORDER BY m.id",
        (sid,),
    ).fetchall()
    conn.close()
    out = []
    for row in rows:
        item = {
            "id": row["id"],
            "role": row["role"],
            "content": row["content"],
            "created_at": row["created_at"],
        }
        state_raw = row["state"]
        if state_raw:
            try:
                state = json.loads(state_raw)
                if isinstance(state, dict) and state.get("resolution"):
                    item["resolution"] = state["resolution"]
            except (TypeError, ValueError):
                pass
        out.append(item)
    return out


@app.get("/api/sessions/{sid}/search")
async def search_messages(sid: int, q: str = "", limit: int = 40):
    query = (q or "").strip()
    conn = db.connect()
    sess = conn.execute("SELECT id FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    limit = max(1, min(100, int(limit or 40)))
    if len(query) < 2:
        conn.close()
        return {"query": query, "results": []}
    rows = []
    try:
        match = '"' + query.replace('"', '""') + '"'
        rows = conn.execute(
            "SELECT m.id, m.role, m.content, m.created_at "
            "FROM message_fts f JOIN messages m ON m.id=f.rowid "
            "WHERE f.session_id=? AND f.content MATCH ? "
            "ORDER BY m.id DESC LIMIT ?",
            (sid, match, limit),
        ).fetchall()
    except Exception:
        rows = []
    if not rows:
        rows = conn.execute(
            "SELECT id, role, content, created_at FROM messages "
            "WHERE session_id=? AND content LIKE ? ORDER BY id DESC LIMIT ?",
            (sid, f"%{query}%", limit),
        ).fetchall()
    conn.close()
    return {"query": query, "results": [db.rowdict(row) for row in rows]}


@app.get("/api/sessions/{sid}/chronicle")
async def session_chronicle(sid: int):
    conn = db.connect()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    char = _char(conn, sess["character_id"])
    world = _world(conn, char["world_id"]) if char and char.get("world_id") else None
    rows = conn.execute(
        "SELECT m.id, m.role, m.content, m.created_at, ms.state AS state "
        "FROM messages m LEFT JOIN message_states ms ON ms.message_id=m.id "
        "WHERE m.session_id=? ORDER BY m.id",
        (sid,),
    ).fetchall()
    chapters = conn.execute(
        "SELECT number, summary, start_id, end_id FROM chapters "
        "WHERE session_id=? ORDER BY number",
        (sid,),
    ).fetchall()
    conn.close()
    title = (sess["title"] or "").strip() or (char["name"] if char else "Crônica")
    speaker = (char["name"] if char else "Narrador")
    lines = [f"# {title}", ""]
    meta = [speaker]
    if world and world.get("name"):
        meta.append(world["name"])
    lines.append("*" + " · ".join(meta) + "*")
    lines.append("")
    if (sess["recap"] or "").strip():
        lines.extend(["## Recapitulação", sess["recap"].strip(), ""])
    chapter_by_start = {
        int(row["start_id"]): row for row in chapters if row["start_id"]
    }
    for row in rows:
        chapter = chapter_by_start.get(int(row["id"]))
        if chapter:
            lines.extend(
                [
                    f"## Capítulo {chapter['number']}",
                    (chapter["summary"] or "").strip(),
                    "",
                ]
            )
        content = (row["content"] or "").strip()
        if not content:
            continue
        prefix = "**Você:**" if row["role"] == "user" else f"**{speaker}:**"
        lines.append(f"{prefix} {content}")
        state_raw = row["state"]
        if state_raw:
            try:
                state = json.loads(state_raw)
                receipt = state.get("resolution") if isinstance(state, dict) else None
                if isinstance(receipt, dict) and receipt.get("outcome_label"):
                    lines.append(
                        "> Teste de "
                        f"{receipt.get('label', 'ação')}: "
                        f"d20 {receipt.get('roll', '?')} "
                        f"{int(receipt.get('modifier', 0) or 0):+d} = "
                        f"{receipt.get('total', '?')} vs CD {receipt.get('dc', '?')} "
                        f"— {receipt.get('outcome_label')}"
                    )
            except (TypeError, ValueError):
                pass
        lines.append("")
    markdown = "\n".join(lines).strip() + "\n"
    return {
        "title": title,
        "markdown": markdown,
        "turns": int(sess["turn_count"] or 0),
        "chapters": len(chapters),
    }


@app.post("/api/sessions/{sid}/reset")
async def reset_session(sid: int):
    conn = db.connect()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    char = conn.execute(
        "SELECT * FROM characters WHERE id=?", (sess["character_id"],)
    ).fetchone()
    conn.execute("DELETE FROM chat_requests WHERE session_id=?", (sid,))
    conn.execute("DELETE FROM messages WHERE session_id=?", (sid,))
    conn.execute("DELETE FROM chapters WHERE session_id=?", (sid,))
    conn.execute("DELETE FROM commitment_checks WHERE session_id=?", (sid,))
    conn.execute("DELETE FROM threads WHERE session_id=?", (sid,))
    conn.execute("DELETE FROM entity_facts WHERE session_id=?", (sid,))
    conn.execute(
        "UPDATE sessions SET summary='', summarized_until=0, turn_count=0, opening_generated=0, scene_image='', scene_prompt='', ledger='', recap='', updated_at=? WHERE id=?",
        (time.time(), sid),
    )
    if char and char["greeting"]:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?,?,?,?)",
            (sid, "assistant", char["greeting"], time.time()),
        )
    conn.commit()
    conn.close()
    return {"reset": sid}


@app.get("/api/sessions/{sid}/voices")
async def list_voices(sid: int, limit: int = 20):
    conn = db.connect()
    rows = world_voices.recent(conn, sid, limit=limit)
    conn.close()
    return {"voices": rows}


@app.post("/api/sessions/{sid}/voices")
async def make_voice(sid: int):
    """Gera um artefato (carta/boato/aviso) a partir do estado atual."""
    conn = db.connect()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    npc = _char(conn, sess["character_id"])
    protagonist = _char(conn, sess["protagonist_id"])
    cast = _cast_rows(conn, sid)
    world = _world(conn, npc["world_id"]) if npc else None
    state = aux.load_state(sess, world, npc)
    aux.ensure_cast_state(state, cast)
    if not state.get("seed"):
        state["seed"] = resolution.seed_for_session(sid, sess["created_at"])
    world_facts = []
    if world is not None:
        world_facts = [
            str(row["text"])
            for row in conn.execute(
                "SELECT text FROM world_facts WHERE world_id=? ORDER BY id DESC LIMIT 6",
                (world["id"],),
            ).fetchall()
        ]
    offscreen_events = state.get("offscreen")
    offscreen_events = offscreen_events[-3:] if isinstance(offscreen_events, list) else []
    voice = world_voices.generate(
        state,
        world,
        npc,
        cast,
        events=offscreen_events,
        seed=state.get("seed"),
        force=True,
        session_id=sid,
        world_facts=world_facts,
    )
    if not voice:
        conn.close()
        raise HTTPException(400, "sem material para gerar um artefato")
    voice_id = world_voices.save(conn, voice)
    aux.save_state(conn, sid, state)
    conn.commit()
    conn.close()
    return {**voice, "id": voice_id, "protagonist": protagonist["name"] if protagonist else ""}


@app.post("/api/sessions/{sid}/reindex")
async def reindex_session(sid: int):
    metrics.inc("reindex_requests")
    conn = db.connect()
    sess = conn.execute("SELECT id FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    try:
        indexed = await embeddings.index_session(conn, sid, limit=5000)
    except Exception as exc:
        metrics.inc("rag_errors")
        conn.close()
        raise HTTPException(502, f"embeddings indisponivel: {exc}") from exc
    conn.close()
    return {"indexed": indexed, "model": embeddings.MODEL}


@app.post("/api/sessions/{sid}/consistency")
async def run_consistency(sid: int):
    conn = db.connect()
    sess = conn.execute("SELECT id FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    started = time.perf_counter()
    try:
        result = await consistency.check(conn, sid)
        consistency.save(conn, sid, result)
        metrics.inc("consistency_checks")
        metrics.observe(
            "consistency_seconds", time.perf_counter() - started
        )
    except Exception as exc:
        metrics.inc("consistency_errors")
        conn.close()
        raise HTTPException(502, f"juiz indisponivel: {exc}") from exc
    conn.close()
    return result


@app.get("/api/sessions/{sid}/consistency")
async def list_consistency(sid: int, limit: int = 10):
    conn = db.connect()
    sess = conn.execute("SELECT id FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    rows = consistency.recent(conn, sid, limit)
    conn.close()
    return rows


@app.post("/api/sessions/{sid}/summarize")
async def summarize_session(sid: int):
    conn = db.connect()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    conn.close()
    if not sess:
        raise HTTPException(404, "sessao nao encontrada")
    await _maybe_summarize(sid)
    conn = db.connect()
    row = conn.execute(
        "SELECT summary, summarized_until FROM sessions WHERE id=?", (sid,)
    ).fetchone()
    conn.close()
    return db.rowdict(row)


# ── memoria (recap + capitulos) ─────────────────────────────────────────────
@app.get("/api/sessions/{sid}/memory")
async def memory(sid: int):
    conn = db.connect()
    sess = conn.execute("SELECT id, recap, ledger FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    chaps = conn.execute(
        "SELECT number, summary, created_at FROM chapters WHERE session_id=? ORDER BY number",
        (sid,),
    ).fetchall()
    conn.close()
    try:
        st = json.loads(sess["ledger"] or "{}")
    except ValueError:
        st = {}
    return {
        "recap": sess["recap"],
        "chapters": [db.rowdict(c) for c in chaps],
        "nr": int(st.get("nr", aux.NR_START)),
        "axes": st.get("axes") or {},
        "vinculo": st.get("vinculo") or "",
    }


@app.get("/api/sessions/{sid}/scene")
async def get_scene(sid: int):
    conn = db.connect()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    npc = _char(conn, sess["character_id"])
    cast = _cast_rows(conn, sid)
    world = _world(conn, npc["world_id"]) if npc else None
    st = aux.load_state(sess, world, npc)
    aux.ensure_cast_state(st, cast)
    scene = aux.ensure_scene(st, npc, cast)
    conn.close()
    return {
        "scene": scene,
        "present": [
            {
                "character_id": c["id"],
                "name": c["name"],
                "status": (st.get("cast", {}).get(str(c["id"]), {}) or {}).get("status") or "",
            }
            for c in cast
            if (c.get("name") or "") in (scene.get("present") or [])
        ],
    }


@app.get("/api/sessions/{sid}/sheet")
async def session_sheet(sid: int):
    conn = db.connect()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    npc = _char(conn, sess["character_id"])
    protagonist = _char(conn, sess["protagonist_id"])
    cast = _cast_rows(conn, sid)
    world = _world(conn, npc["world_id"]) if npc and npc.get("world_id") else None
    st = aux.load_state(sess, world, npc)
    _inject_player_sheet(st, protagonist)
    aux.ensure_cast_state(st, cast)
    scene = aux.ensure_scene(st, npc, cast)
    threads = commitments.open_commitments(conn, sid, limit=20)
    conn.close()

    def char_ref(c):
        if not c:
            return None
        return {
            "id": c.get("id"),
            "name": c.get("name") or "",
            "avatar": c.get("avatar") or "",
            "tagline": c.get("tagline") or "",
            "attributes": resolution.normalize_attributes(c.get("attributes")),
            "skills": resolution.normalize_skills(c.get("skills")),
        }

    attributes = resolution.normalize_attributes((protagonist or {}).get("attributes"))
    skills = resolution.normalize_skills((protagonist or {}).get("skills"))

    return {
        "scene": scene,
        "pacing": st.get("pacing") or {},
        "nr": int(st.get("nr", aux.NR_START)),
        "axes": st.get("axes") or {},
        "vinculo": st.get("vinculo") or "",
        "inventory": st.get("inventory") or [],
        "inventory_detail": resolution.inventory_detail(st.get("inventory")),
        "injuries": st.get("injuries") or [],
        "injuries_detail": resolution.injuries_detail(st.get("injuries")),
        "facts": st.get("facts") or [],
        "threads": threads,
        "attributes": attributes,
        "skills": skills,
        "modifiers": resolution.modifier_table(attributes, skills, st),
        "npc": char_ref(npc),
        "protagonist": char_ref(protagonist),
        "cast": [
            {
                "character_id": c["id"],
                "name": c["name"],
                "avatar": c.get("avatar") or "",
                **aux.cast_relationship(st, c["id"]),
            }
            for c in cast
        ],
    }


@app.get("/api/sessions/{sid}/threads")
async def get_threads(sid: int, limit: int = 100):
    conn = db.connect()
    sess = conn.execute("SELECT id, ledger FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    try:
        turn = int(json.loads(sess["ledger"] or "{}").get("turn", 0) or 0)
    except (ValueError, TypeError):
        turn = 0
    items = commitments.all_commitments(conn, sid, limit=limit)
    stats = commitments.stats(conn, sid, turn)
    conn.close()
    return {"threads": items, "stats": stats, "turn": turn}


@app.get("/api/sessions/{sid}/codex")
async def session_codex(sid: int):
    conn = db.connect()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    npc = _char(conn, sess["character_id"])
    protagonist = _char(conn, sess["protagonist_id"])
    cast = _cast_rows(conn, sid)
    world = _world(conn, npc["world_id"]) if npc and npc.get("world_id") else None
    st = aux.load_state(sess, world, npc)
    aux.ensure_cast_state(st, cast)
    scene = aux.ensure_scene(st, npc, cast)
    world_facts = []
    if world:
        world_facts = [
            db.rowdict(row)
            for row in conn.execute(
                "SELECT id, text, keys, created_at FROM world_facts "
                "WHERE world_id=? ORDER BY id DESC LIMIT 80",
                (world["id"],),
            ).fetchall()
        ]
    entity_facts = [
        db.rowdict(row)
        for row in conn.execute(
            "SELECT id, entity, fact, known_by, source_turn FROM entity_facts "
            "WHERE session_id=? ORDER BY id DESC LIMIT 120",
            (sid,),
        ).fetchall()
    ]
    voices = [
        db.rowdict(row)
        for row in conn.execute(
            "SELECT id, turn, kind, title, author, body, truth_status, "
            "visibility, known_by FROM world_voices WHERE session_id=? "
            "ORDER BY id DESC LIMIT 40",
            (sid,),
        ).fetchall()
    ]
    conn.close()
    codex = st.get("codex") if isinstance(st.get("codex"), dict) else {}
    tracked_locations = (
        codex.get("locations") if isinstance(codex.get("locations"), list) else []
    )
    locations = list(tracked_locations)
    if not locations and scene.get("location"):
        locations = [
            {
                "name": scene["location"],
                "first_turn": int(st.get("turn", 0) or 0),
                "last_turn": int(st.get("turn", 0) or 0),
            }
        ]
    chars = {}
    for char, role in [
        (npc, "npc"),
        (protagonist, "protagonist"),
        *((c, "cast") for c in cast),
    ]:
        if char and char.get("id") is not None:
            chars[int(char["id"])] = {**char, "role": role}
    people = []
    seen = set()
    tracked_people = codex.get("people") if isinstance(codex.get("people"), list) else []
    for item in tracked_people:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        key = aux._name_key(name)
        if key in seen:
            continue
        seen.add(key)
        match = next(
            (
                char
                for char in chars.values()
                if aux._name_key(char.get("name") or "") == key
            ),
            None,
        )
        people.append(
            {
                "name": name,
                "first_turn": int(item.get("first_turn", 0) or 0),
                "last_turn": int(item.get("last_turn", 0) or 0),
                "character_id": match.get("id") if match else None,
                "avatar": (match or {}).get("avatar") or "",
                "tagline": (match or {}).get("tagline") or "",
                "role": (match or {}).get("role") or "",
            }
        )
    for char in chars.values():
        name = str(char.get("name") or "").strip()
        key = aux._name_key(name)
        if not name or key in seen:
            continue
        seen.add(key)
        people.append(
            {
                "name": name,
                "first_turn": 0,
                "last_turn": int(st.get("turn", 0) or 0),
                "character_id": char.get("id"),
                "avatar": char.get("avatar") or "",
                "tagline": char.get("tagline") or "",
                "role": char.get("role") or "",
            }
        )
    return {
        "people": people,
        "locations": locations,
        "world_facts": world_facts,
        "entity_facts": entity_facts,
        "voices": voices,
        "scene": scene,
        "world": {
            "id": world.get("id") if world else None,
            "name": world.get("name") if world else "",
            "description": world.get("description") if world else "",
        },
    }


@app.get("/api/sessions/{sid}/director")
async def session_director(sid: int):
    conn = db.connect()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    npc = _char(conn, sess["character_id"])
    cast = _cast_rows(conn, sid)
    world = _world(conn, npc["world_id"]) if npc and npc.get("world_id") else None
    st = aux.load_state(sess, world, npc)
    aux.ensure_cast_state(st, cast)
    scene = aux.ensure_scene(st, npc, cast)
    conn.close()
    return {
        "scene": scene,
        "pacing": st.get("pacing") or {},
        "resolution": st.get("resolution"),
        "offscreen": st.get("offscreen") or [],
        "state": st,
        "metrics": metrics.snapshot(),
    }


@app.get("/api/sessions/{sid}/digest")
async def session_digest(sid: int):
    conn = db.connect()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    npc = _char(conn, sess["character_id"])
    world = _world(conn, npc["world_id"]) if npc and npc.get("world_id") else None
    st = aux.load_state(sess, world, npc)
    voices = conn.execute(
        "SELECT id, turn, kind, title, author, body, truth_status, visibility, "
        "known_by, created_at FROM world_voices WHERE session_id=? "
        "ORDER BY id DESC LIMIT 6",
        (sid,),
    ).fetchall()
    conn.close()
    offscreen = st.get("offscreen") if isinstance(st.get("offscreen"), list) else []
    return {
        "turn": int(st.get("turn", 0) or 0),
        "offscreen": offscreen[-6:],
        "voices": [db.rowdict(row) for row in voices],
    }


@app.get("/api/sessions/{sid}/commitments")
async def get_commitments(sid: int, limit: int = 20):
    conn = db.connect()
    sess = conn.execute("SELECT id FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    open_items = commitments.open_commitments(conn, sid, limit=40)
    state_row = conn.execute(
        "SELECT ledger FROM sessions WHERE id=?", (sid,)
    ).fetchone()
    try:
        turn = int(json.loads(state_row["ledger"] or "{}").get("turn", 0) or 0)
    except (ValueError, TypeError):
        turn = 0
    out = {
        "open": open_items,
        "stats": commitments.stats(conn, sid, turn),
        "checks": commitments.recent_checks(conn, sid, limit=limit),
    }
    conn.close()
    return out


@app.get("/api/sessions/{sid}/relationship")
async def relationship(sid: int, character_id: int | None = None):
    conn = db.connect()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    npc = _char(conn, sess["character_id"])
    cast = _cast_rows(conn, sid)
    world = _world(conn, npc["world_id"]) if npc else None
    st = aux.load_state(sess, world, npc)
    aux.ensure_cast_state(st, cast)
    scene = aux.ensure_scene(st, npc, cast)
    conn.close()

    if character_id is not None:
        if npc and character_id == npc["id"]:
            return {
                "character_id": npc["id"],
                "name": npc["name"],
                "nr": int(st.get("nr", aux.NR_START)),
                "axes": st.get("axes") or {},
                "vinculo": st.get("vinculo") or "",
                "layers": aux.main_layers(st),
                "scene": scene,
            }
        for c in cast:
            if c["id"] == character_id:
                return {
                    "character_id": c["id"],
                    "name": c["name"],
                    **aux.cast_relationship(st, c["id"]),
                }
        raise HTTPException(404, "personagem fora do elenco")

    return {
        "character_id": npc["id"] if npc else None,
        "name": (npc["name"] if npc else ""),
        "nr": int(st.get("nr", aux.NR_START)),
        "axes": st.get("axes") or {},
        "vinculo": st.get("vinculo") or "",
        "layers": aux.main_layers(st),
        "scene": scene,
        "relationship_events": st.get("relationship_events") or [],
        "cast": [
            {
                "character_id": c["id"],
                "name": c["name"],
                **aux.cast_relationship(st, c["id"]),
            }
            for c in cast
        ],
    }


@app.get("/api/sessions/{sid}/relationship/events")
async def get_relationship_events(sid: int, limit: int = 30):
    conn = db.connect()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    st = aux.load_state(sess, None, _char(conn, sess["character_id"]))
    conn.close()
    events = list(st.get("relationship_events") or [])[-max(1, int(limit)):]
    return {"events": events}


@app.get("/api/sessions/{sid}/cast")
async def get_cast(sid: int):
    conn = db.connect()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    npc = _char(conn, sess["character_id"])
    cast = _cast_rows(conn, sid)
    world = _world(conn, npc["world_id"]) if npc else None
    st = aux.load_state(sess, world, npc)
    aux.ensure_cast_state(st, cast)
    scene = aux.ensure_scene(st, npc, cast)
    conn.close()
    return {
        "scene": scene,
        "main": {
            "character": npc,
            "nr": int(st.get("nr", aux.NR_START)),
            "axes": st.get("axes") or {},
            "vinculo": st.get("vinculo") or "",
            "layers": aux.main_layers(st),
        } if npc else None,
        "cast": [
            {
                "character": c,
                **aux.cast_relationship(st, c["id"]),
            }
            for c in cast
        ],
    }


@app.post("/api/sessions/{sid}/cast")
async def add_cast(sid: int, body: CastIn):
    conn = db.connect()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    if body.character_id == sess["character_id"]:
        conn.close()
        raise HTTPException(400, "o NPC principal ja esta na sessao")
    char = conn.execute(
        "SELECT id, kind FROM characters WHERE id=?", (body.character_id,)
    ).fetchone()
    if not char or char["kind"] != "npc":
        conn.close()
        raise HTTPException(404, "NPC nao encontrado")
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM session_cast WHERE session_id=?", (sid,)
    ).fetchone()["n"]
    if total >= 4:
        conn.close()
        raise HTTPException(400, "o elenco ja tem 4 NPCs secundarios")
    pos = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM session_cast "
        "WHERE session_id=?",
        (sid,),
    ).fetchone()["p"]
    conn.execute(
        "INSERT OR IGNORE INTO session_cast "
        "(session_id, character_id, position, created_at) VALUES (?,?,?,?)",
        (sid, body.character_id, pos, time.time()),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    out = _session_out(conn, row)
    conn.close()
    return out


@app.delete("/api/sessions/{sid}/cast/{character_id}")
async def remove_cast(sid: int, character_id: int):
    conn = db.connect()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    conn.execute(
        "DELETE FROM session_cast WHERE session_id=? AND character_id=?",
        (sid, character_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    out = _session_out(conn, row)
    conn.close()
    return out


# ── recap curto (<=200 tokens) ─────────────────────────────────────────────
@app.get("/api/sessions/{sid}/recap")
async def get_recap(sid: int, refresh: bool = False):
    conn = db.connect()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    if sess["recap"] and not refresh:
        conn.close()
        return {"recap": sess["recap"]}
    chaps = conn.execute(
        "SELECT number, summary FROM chapters WHERE session_id=? ORDER BY number DESC LIMIT 4",
        (sid,),
    ).fetchall()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 8",
        (sid,),
    ).fetchall()
    conn.close()
    if chaps:
        src = "\n\n".join(f"Capitulo {c['number']}: {c['summary']}" for c in reversed(chaps))
    else:
        src = "\n".join(
            f"{'Jogador' if r['role'] == 'user' else 'Narrador'}: {r['content']}"
            for r in reversed(rows)
        )
    if not src.strip():
        return {"recap": ""}
    try:
        recap = await llm.complete(
            CHAPTER_MODEL,
            [{"role": "system", "content": prompts.RECAP_SYSTEM},
             {"role": "user", "content": src}],
            False,
            {"temperature": 0.4, "num_ctx": 8192, "num_predict": 300},
            base=llm.OLLAMA_UTILITY,
        )
        recap = recap.strip()
    except Exception as e:
        raise HTTPException(500, f"recap: {e}")
    conn = db.connect()
    conn.execute("UPDATE sessions SET recap=? WHERE id=?", (recap, sid))
    conn.commit()
    conn.close()
    return {"recap": recap}


# ── capitulos (memoria por capitulos) ───────────────────────────────────────
def _chapter_state(conn, sid):
    last_end = conn.execute(
        "SELECT MAX(end_id) AS m FROM chapters WHERE session_id=?", (sid,)
    ).fetchone()["m"] or 0
    pending = conn.execute(
        "SELECT COUNT(*) AS c FROM messages WHERE session_id=? AND id>?", (sid, last_end)
    ).fetchone()["c"]
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM chapters WHERE session_id=?", (sid,)
    ).fetchone()["c"]
    return last_end, pending, count


@app.get("/api/sessions/{sid}/chapter_status")
async def chapter_status(sid: int):
    conn = db.connect()
    ok = conn.execute("SELECT id FROM sessions WHERE id=?", (sid,)).fetchone()
    if not ok:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    _, pending, count = _chapter_state(conn, sid)
    conn.close()
    return {
        "chapter_every": CHAPTER_EVERY,
        "pending": pending,
        "chapters": count,
        "chapter_due": pending >= CHAPTER_EVERY,
    }


@app.get("/api/sessions/{sid}/chapters")
async def list_chapters(sid: int):
    conn = db.connect()
    rows = conn.execute(
        "SELECT * FROM chapters WHERE session_id=? ORDER BY number", (sid,)
    ).fetchall()
    conn.close()
    return [db.rowdict(r) for r in rows]


@app.post("/api/sessions/{sid}/chapters")
async def create_chapter(sid: int, body: ChapterIn | None = None):
    force = bool(body.force) if body else False
    conn = db.connect()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    last_end, pending, count = _chapter_state(conn, sid)
    if pending < CHAPTER_EVERY and not force:
        conn.close()
        return {"created": None, "pending": pending, "chapter_due": False}
    rows = conn.execute(
        "SELECT id, role, content FROM messages WHERE session_id=? AND id>? ORDER BY id",
        (sid, last_end),
    ).fetchall()
    if not rows:
        conn.close()
        return {"created": None, "pending": 0, "chapter_due": False}
    convo = "\n".join(
        f"{'Jogador' if r['role'] == 'user' else 'Narrador'}: {r['content']}" for r in rows
    )
    try:
        summary = await llm.complete(
            CHAPTER_MODEL,
            [{"role": "system", "content": prompts.CHAPTER_SYSTEM},
             {"role": "user", "content": "Mensagens do capitulo:\n" + convo}],
            False,
            {"temperature": 0.4, "num_ctx": 8192, "num_predict": 420},
            base=llm.OLLAMA_UTILITY,
        )
        summary = summary.strip()
    except Exception as e:
        conn.close()
        raise HTTPException(500, f"capitulo: {e}")
    if not summary:
        conn.close()
        raise HTTPException(500, "resumo vazio")
    now = time.time()
    cur = conn.execute(
        "INSERT INTO chapters (session_id, number, summary, start_id, end_id, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (sid, count + 1, summary, rows[0]["id"], rows[-1]["id"], now),
    )
    conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, sid))
    conn.commit()
    ch = conn.execute("SELECT * FROM chapters WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return {"created": db.rowdict(ch), "pending": 0, "chapter_due": False}


# ── escolhas ramificadas ────────────────────────────────────────────────────
@app.post("/api/sessions/{sid}/choices")
async def suggest_choices(sid: int):
    conn = db.connect()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    npc = _char(conn, sess["character_id"])
    protagonist = _char(conn, sess["protagonist_id"])
    world = _world(conn, npc["world_id"]) if npc and npc.get("world_id") else None
    state = aux.load_state(sess, world, npc)
    _inject_player_sheet(state, protagonist)
    recent = conn.execute(
        "SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 8",
        (sid,),
    ).fetchall()
    conn.close()
    recent = list(reversed(recent))
    names = {"assistant": "Narrador", "user": "Jogador"}
    convo = "\n".join(f"{names.get(r['role'], r['role'])}: {r['content']}" for r in recent)
    quem = f"O jogador interpreta {protagonist['name']}." if protagonist else ""
    user = (
        (quem + "\n" if quem else "")
        + "Contexto recente da historia:\n" + convo
        + "\n\nListe agora exatamente 3 opcoes de acao do jogador (linhas comecando com '- ')."
    )
    model = llm.UTILITY_MODEL
    try:
        raw = await llm.complete(
            model,
            [{"role": "system", "content": prompts.CHOICES_SYSTEM},
             {"role": "user", "content": user}],
            False,
            {"temperature": 0.8, "num_ctx": 8192, "num_predict": 160},
            base=llm.OLLAMA_UTILITY,
        )
    except Exception as e:
        return {"choices": [], "error": str(e)}
    out = []
    for line in raw.splitlines():
        t = line.strip()
        if t.startswith("- "):
            t = t[2:].strip()
        elif re.match(r"^\d+[.)]\s+", t):
            t = re.sub(r"^\d+[.)]\s+", "", t).strip()
        else:
            continue
        if t and not t.startswith('"') and len(t) <= 140:
            out.append({"text": t, **resolution.preview(t, state)})
    return {"choices": out[:4]}


@app.get("/api/sessions/{sid}/tree")
async def session_tree(sid: int):
    conn = db.connect()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    rows = conn.execute(
        "SELECT id, parent_id, branch_from, title, turn_count, updated_at, "
        "created_at FROM sessions WHERE character_id=? ORDER BY id",
        (sess["character_id"],),
    ).fetchall()
    conn.close()
    by_id = {int(row["id"]): db.rowdict(row) for row in rows}
    root = int(sess["id"])
    seen = set()
    while root in by_id and by_id[root].get("parent_id"):
        parent = int(by_id[root]["parent_id"])
        if parent in seen or parent not in by_id:
            break
        seen.add(root)
        root = parent
    children = {}
    for row in by_id.values():
        parent = row.get("parent_id")
        if parent is None:
            continue
        children.setdefault(int(parent), []).append(row)
    out = []

    def walk(node_id, depth):
        node = by_id.get(int(node_id))
        if node is None:
            return
        out.append(
            {
                **node,
                "depth": depth,
                "current": int(node_id) == int(sid),
            }
        )
        for child in children.get(int(node_id), []):
            walk(child["id"], depth + 1)

    walk(root, 0)
    return {
        "root_id": root,
        "current_id": int(sid),
        "sessions": out,
    }


@app.post("/api/sessions/{sid}/branch")
async def branch_session(sid: int, b: BranchIn):
    conn = db.connect()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    last_row = conn.execute(
        "SELECT MAX(id) AS m FROM messages WHERE session_id=?", (sid,)
    ).fetchone()
    last_id = int(last_row["m"] or 0)
    if b.message_id is None:
        cutoff = last_id
    else:
        cutoff = int(b.message_id)
    target = conn.execute(
        "SELECT id, created_at FROM messages WHERE id=? AND session_id=?",
        (cutoff, sid),
    ).fetchone()
    if not target:
        conn.close()
        raise HTTPException(404, "mensagem nao encontrada")
    at_end = cutoff >= last_id
    if at_end:
        ledger = sess["ledger"] or ""
        summary = sess["summary"] or ""
        recap = sess["recap"] or ""
        summarized_until = sess["summarized_until"] or 0
        scene_image = sess["scene_image"] or ""
        scene_prompt = sess["scene_prompt"] or ""
    else:
        snapshot = conn.execute(
            "SELECT state FROM message_states WHERE session_id=? "
            "AND message_id<=? ORDER BY message_id DESC LIMIT 1",
            (sid, cutoff),
        ).fetchone()
        ledger = snapshot["state"] if snapshot else ""
        summary = ""
        recap = ""
        summarized_until = 0
        scene_image = ""
        scene_prompt = ""
    rows = conn.execute(
        "SELECT id, role, content, created_at FROM messages "
        "WHERE session_id=? AND id<=? ORDER BY id",
        (sid, cutoff),
    ).fetchall()
    now = time.time()
    title = b.title or f"{sess['title']} (ramo)"
    cur = conn.execute(
        "INSERT INTO sessions (character_id, protagonist_id, title, summary,"
        " summarized_until, turn_count, opening_generated, scene_image,"
        " scene_prompt, ledger, recap, parent_id, branch_from, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            sess["character_id"],
            sess["protagonist_id"],
            title,
            summary,
            summarized_until,
            sum(1 for row in rows if row["role"] == "user"),
            1 if any(row["role"] == "assistant" for row in rows) else 0,
            scene_image,
            scene_prompt,
            ledger,
            recap,
            sid,
            cutoff,
            now,
            now,
        ),
    )
    nid = cur.lastrowid
    for cast_row in conn.execute(
        "SELECT character_id, position, created_at FROM session_cast "
        "WHERE session_id=? ORDER BY position",
        (sid,),
    ).fetchall():
        conn.execute(
            "INSERT OR IGNORE INTO session_cast "
            "(session_id, character_id, position, created_at) "
            "VALUES (?,?,?,?)",
            (
                nid,
                cast_row["character_id"],
                cast_row["position"],
                cast_row["created_at"],
            ),
        )
    id_map = {}
    for row in rows:
        msg_cur = conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at)"
            " VALUES (?,?,?,?)",
            (nid, row["role"], row["content"], row["created_at"]),
        )
        id_map[row["id"]] = msg_cur.lastrowid
    if id_map:
        placeholders = ",".join("?" for _ in id_map)
        snaps = conn.execute(
            "SELECT message_id, state, created_at FROM message_states "
            f"WHERE message_id IN ({placeholders})",
            tuple(id_map.keys()),
        ).fetchall()
        for snap in snaps:
            new_mid = id_map.get(snap["message_id"])
            if new_mid:
                conn.execute(
                    "INSERT OR REPLACE INTO message_states "
                    "(message_id, session_id, state, created_at) VALUES (?,?,?,?)",
                    (new_mid, nid, snap["state"], snap["created_at"]),
                )
    chapters = conn.execute(
        "SELECT number, summary, start_id, end_id, created_at FROM chapters "
        "WHERE session_id=? AND end_id<=? ORDER BY number",
        (sid, cutoff),
    ).fetchall()
    first_new = min(id_map.values()) if id_map else 0
    for chapter in chapters:
        new_end = id_map.get(chapter["end_id"])
        if not new_end:
            continue
        new_start = id_map.get(chapter["start_id"], first_new)
        conn.execute(
            "INSERT INTO chapters (session_id, number, summary, start_id,"
            " end_id, created_at) VALUES (?,?,?,?,?,?)",
            (
                nid,
                chapter["number"],
                chapter["summary"],
                new_start,
                new_end,
                chapter["created_at"],
            ),
        )
    if at_end:
        for thread in conn.execute(
            "SELECT * FROM threads WHERE session_id=?", (sid,)
        ).fetchall():
            conn.execute(
                "INSERT INTO threads (session_id, kind, title, status, setup,"
                " payoff, due_turn, entities, last_turn, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    nid,
                    thread["kind"],
                    thread["title"],
                    thread["status"],
                    thread["setup"],
                    thread["payoff"],
                    thread["due_turn"],
                    thread["entities"],
                    thread["last_turn"],
                    thread["created_at"],
                    thread["updated_at"],
                ),
            )
        for fact in conn.execute(
            "SELECT * FROM entity_facts WHERE session_id=?", (sid,)
        ).fetchall():
            conn.execute(
                "INSERT INTO entity_facts (session_id, entity, fact, known_by,"
                " source_turn, created_at) VALUES (?,?,?,?,?,?)",
                (
                    nid,
                    fact["entity"],
                    fact["fact"],
                    fact["known_by"],
                    fact["source_turn"],
                    fact["created_at"],
                ),
            )
    elif ledger:
        try:
            turn = int(json.loads(ledger).get("turn", 0) or 0)
        except (ValueError, TypeError):
            turn = 0
        if turn:
            for fact in conn.execute(
                "SELECT * FROM entity_facts WHERE session_id=? "
                "AND source_turn<=?",
                (sid, turn),
            ).fetchall():
                conn.execute(
                    "INSERT INTO entity_facts (session_id, entity, fact,"
                    " known_by, source_turn, created_at) VALUES (?,?,?,?,?,?)",
                    (
                        nid,
                        fact["entity"],
                        fact["fact"],
                        fact["known_by"],
                        fact["source_turn"],
                        fact["created_at"],
                    ),
                )
    conn.commit()
    new = conn.execute("SELECT * FROM sessions WHERE id=?", (nid,)).fetchone()
    conn.close()
    metrics.inc("branch_created")
    return db.rowdict(new)


@app.post("/api/sessions/{sid}/rewind")
async def rewind_session(sid: int, r: RewindIn):
    conn = db.connect()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    target = conn.execute(
        "SELECT id, role, created_at FROM messages WHERE id=? AND session_id=?",
        (r.message_id, sid),
    ).fetchone()
    if not target:
        conn.close()
        raise HTTPException(404, "mensagem nao encontrada")
    last_row = conn.execute(
        "SELECT MAX(id) AS m FROM messages WHERE session_id=?", (sid,)
    ).fetchone()
    last_id = int(last_row["m"] or 0)
    at_end = r.message_id >= last_id and target["role"] == "assistant"
    snapshot = conn.execute(
        "SELECT state FROM message_states WHERE session_id=? "
        "AND message_id<=? ORDER BY message_id DESC LIMIT 1",
        (sid, r.message_id),
    ).fetchone()
    # Regenerar a resposta de um turno não pode sortear de novo: guarda o
    # recibo autorizado da resposta que vai ser apagada para o regen reutilizar.
    pending_resolution = None
    if target["role"] == "user":
        next_state = conn.execute(
            "SELECT state FROM message_states WHERE session_id=? AND message_id>? "
            "ORDER BY message_id ASC LIMIT 1",
            (sid, r.message_id),
        ).fetchone()
        if next_state:
            try:
                next_parsed = json.loads(next_state["state"] or "{}")
            except (TypeError, ValueError):
                next_parsed = {}
            if isinstance(next_parsed, dict) and isinstance(
                next_parsed.get("resolution"), dict
            ):
                pending_resolution = next_parsed["resolution"]
    if snapshot:
        ledger = snapshot["state"]
    elif at_end:
        ledger = sess["ledger"] or ""
    else:
        ledger = ""
    if pending_resolution:
        try:
            ledger_state = json.loads(ledger) if ledger else {}
        except (TypeError, ValueError):
            ledger_state = {}
        if not isinstance(ledger_state, dict):
            ledger_state = {}
        ledger_state["pending_resolution"] = pending_resolution
        ledger = json.dumps(ledger_state, ensure_ascii=False)
    cur = conn.execute(
        "DELETE FROM messages WHERE session_id=? AND id>?",
        (sid, r.message_id),
    )
    deleted = cur.rowcount
    conn.execute(
        "DELETE FROM chapters WHERE session_id=? AND end_id>?",
        (sid, r.message_id),
    )
    if not at_end:
        conn.execute(
            "DELETE FROM threads WHERE session_id=? AND created_at>?",
            (sid, target["created_at"]),
        )
        conn.execute(
            "DELETE FROM entity_facts WHERE session_id=? AND created_at>?",
            (sid, target["created_at"]),
        )
        conn.execute(
            "DELETE FROM commitment_checks WHERE session_id=? AND created_at>?",
            (sid, target["created_at"]),
        )
    user_count = conn.execute(
        "SELECT count(*) AS n FROM messages WHERE session_id=? AND role='user'",
        (sid,),
    ).fetchone()["n"]
    has_assistant = conn.execute(
        "SELECT count(*) AS n FROM messages WHERE session_id=? "
        "AND role='assistant'",
        (sid,),
    ).fetchone()["n"]
    summary = sess["summary"] if at_end else ""
    summarized_until = sess["summarized_until"] if at_end else 0
    recap = sess["recap"] if at_end else ""
    scene_image = sess["scene_image"] if at_end else ""
    scene_prompt = sess["scene_prompt"] if at_end else ""
    conn.execute(
        "UPDATE sessions SET ledger=?, summary=?, summarized_until=?,"
        " turn_count=?, opening_generated=?, scene_image=?, scene_prompt=?,"
        " recap=?, updated_at=? WHERE id=?",
        (
            ledger,
            summary,
            summarized_until,
            int(user_count or 0),
            1 if has_assistant else 0,
            scene_image,
            scene_prompt,
            recap,
            time.time(),
            sid,
        ),
    )
    conn.commit()
    new = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    conn.close()
    metrics.inc("rewind")
    return {
        "session": db.rowdict(new),
        "deleted_messages": deleted,
    }


@app.put("/api/sessions/{sid}/messages/{mid}")
async def edit_message(sid: int, mid: int, body: MessageIn):
    conn = db.connect()
    row = conn.execute(
        "SELECT id FROM messages WHERE id=? AND session_id=?", (mid, sid)
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "mensagem nao encontrada")
    conn.execute("UPDATE messages SET content=? WHERE id=?", (body.content.strip(), mid))
    conn.execute("DELETE FROM message_vectors WHERE message_id=?", (mid,))
    conn.commit()
    conn.close()
    return {"updated": mid}


@app.delete("/api/sessions/{sid}/messages/{mid}")
async def delete_message(sid: int, mid: int):
    conn = db.connect()
    row = conn.execute(
        "SELECT id FROM messages WHERE id=? AND session_id=?", (mid, sid)
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "mensagem nao encontrada")
    conn.execute("DELETE FROM messages WHERE id=?", (mid,))
    conn.commit()
    conn.close()
    return {"deleted": mid}


@app.post("/api/sessions/{sid}/messages/{mid}/truncate")
async def truncate_messages(sid: int, mid: int):
    conn = db.connect()
    row = conn.execute(
        "SELECT id FROM messages WHERE id=? AND session_id=?", (mid, sid)
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "mensagem nao encontrada")
    cur = conn.execute("DELETE FROM messages WHERE session_id=? AND id>?", (sid, mid))
    conn.commit()
    n = cur.rowcount
    conn.close()
    return {"deleted": n}


async def _iter_with_pings(agen, interval: float = 5.0):
    """Repassa itens de um async generator, emitindo 'ping' em periodos ociosos."""
    queue: asyncio.Queue = asyncio.Queue()

    async def pump():
        try:
            async for item in agen:
                await queue.put(("data", item))
        except Exception as exc:  # noqa: BLE001 - repassado ao consumidor
            await queue.put(("error", exc))
        finally:
            await queue.put(("end", None))

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                kind, value = await asyncio.wait_for(
                    queue.get(), timeout=interval
                )
            except asyncio.TimeoutError:
                yield ("ping", None)
                continue
            if kind == "data":
                yield ("data", value)
            elif kind == "error":
                raise value
            else:
                break
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@app.post("/api/sessions/{sid}/chat")
async def chat(sid: int, body: ChatIn):
    metrics.inc("chat_requests")
    conn = db.connect()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        raise HTTPException(404, "sessao nao encontrada")
    npc = _char(conn, sess["character_id"])
    protagonist = _char(conn, sess["protagonist_id"])
    cast = _cast_rows(conn, sid)
    world = _world(conn, npc["world_id"]) if npc else None
    state = aux.load_state(sess, world, npc)
    _inject_player_sheet(state, protagonist)
    aux.ensure_cast_state(state, cast)
    if not body.regen:
        # Recibo guardado só vale para a reescrita imediata daquele turno.
        state.pop("pending_resolution", None)
    if not state.get("seed"):
        state["seed"] = resolution.seed_for_session(sid, sess["created_at"])
    user_msg = body.message.strip()
    if body.regen:
        last = conn.execute(
            "SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 1",
            (sid,),
        ).fetchone()
        if not last or last["role"] != "user":
            conn.close()
            raise HTTPException(400, "regen exige a ultima mensagem do jogador")
        if not user_msg:
            user_msg = last["content"]

    unanswered = ""
    if not body.regen:
        last = conn.execute(
            "SELECT role, content FROM messages WHERE session_id=? "
            "ORDER BY id DESC LIMIT 1",
            (sid,),
        ).fetchone()
        if (
            last
            and last["role"] == "user"
            and last["content"].strip()
            and last["content"].strip() != user_msg
        ):
            unanswered = last["content"].strip()

    prev_location = str(
        (state.get("scene") or {}).get("location")
        or state.get("location")
        or ""
    ).strip()
    request_key = ""
    chat_lock = None
    if not body.regen and user_msg:
        request_key = _chat_request_key(sid, user_msg, body.request_id)
        if len(_chat_locks) > 2048:
            for key, lock in list(_chat_locks.items()):
                if not lock.locked():
                    _chat_locks.pop(key, None)
        chat_lock = _chat_locks.setdefault(request_key, asyncio.Lock())
        await chat_lock.acquire()
        replay = _recent_chat_response(conn, request_key)
        if replay:
            chat_lock.release()
            conn.close()
            print("[chat] resposta repetida reutilizada", flush=True)
            return StreamingResponse(
                _replay_chat(replay, state), media_type="application/x-ndjson"
            )

    model = _effective_model(
        body.model or (npc["model"] if npc else "") or DEFAULT_MODEL
    )
    conn.close()

    async def gen():
        nonlocal state
        parts = []
        commitment_stats = {}
        started = time.time()
        plan_text = ""
        plan_data = None
        receipt = None
        work = db.connect()
        try:
            yield json.dumps({"status": "planning"}, ensure_ascii=False) + "\n"
            extra = []
            if unanswered:
                extra.append(
                    "## ACAO ANTERIOR SEM RESPOSTA\n"
                    f"O jogador escreveu antes: {unanswered[:500]}\n"
                    "Incorpore essa acao e a acao atual na mesma resposta, "
                    "sem ignorar nenhuma das duas."
                )
            if body.ooc:
                extra.append(prompts.OOC_NOTE)
            if body.continue_scene and not body.message.strip():
                extra.append(prompts.CONTINUE_NOTE)
            roll_expected = False
            if (
                RESOLUTION_ENABLED
                and not body.ooc
                and not body.continue_scene
                and not body.regen
                and user_msg
            ):
                try:
                    roll_expected = resolution.would_roll(user_msg, state)
                except Exception as exc:  # noqa: BLE001
                    print(f"[resolution] pre-check falhou: {exc}", flush=True)
                if roll_expected:
                    yield json.dumps(
                        {"status": "rolling"}, ensure_ascii=False
                    ) + "\n"
            if body.regen and RESOLUTION_ENABLED and not body.ooc and user_msg:
                try:
                    prior = state.get("pending_resolution") or state.get("resolution")
                    if (
                        isinstance(prior, dict)
                        and str(prior.get("action") or "").strip()
                        == user_msg.strip()
                    ):
                        receipt = resolution.Receipt.from_dict(prior)
                        if state.get("resolution") != prior:
                            state = resolution.apply(state, receipt)
                        extra.append(receipt.as_block())
                        yield json.dumps(
                            {"resolution": receipt.as_dict()}, ensure_ascii=False
                        ) + "\n"
                    state.pop("pending_resolution", None)
                except Exception as exc:  # noqa: BLE001
                    print(f"[resolution] regen sem recibo: {exc}", flush=True)
            if (
                PLANNER_ENABLED
                and not body.ooc
                and not llm.cpu_mode()
                and llm.utility_available()
            ):
                planner_started = time.perf_counter()
                plan_task = asyncio.create_task(
                    _plan_scene(
                        work,
                        sess,
                        npc,
                        protagonist,
                        world,
                        user_msg,
                        body.continue_scene,
                        state,
                        apply_side_effects=not body.regen,
                        cast=cast,
                    )
                )
                deadline = time.monotonic() + PLANNER_TIMEOUT
                try:
                    while not plan_task.done():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            plan_task.cancel()
                            await asyncio.gather(plan_task, return_exceptions=True)
                            print(
                                "[planner] timeout; seguindo sem direcao",
                                flush=True,
                            )
                            break
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(plan_task),
                                timeout=min(5.0, remaining),
                            )
                        except asyncio.TimeoutError:
                            yield json.dumps(
                                {"status": "planning"}, ensure_ascii=False
                            ) + "\n"
                    if plan_task.done() and not plan_task.cancelled():
                        exc = plan_task.exception()
                        if exc:
                            print("[planner] erro:", exc, flush=True)
                        else:
                            plan_text, plan_data = plan_task.result()
                finally:
                    if not plan_task.done():
                        plan_task.cancel()
                        await asyncio.gather(plan_task, return_exceptions=True)
                metrics.observe(
                    "planner_seconds", time.perf_counter() - planner_started
                )
                if plan_text:
                    extra.append(
                        "## DIRECAO DA CENA (guia interno do diretor; NAO mostre ao jogador)\n"
                        + plan_text
                    )

            ev = aux.event_block(state, user_msg, body.continue_scene)
            if not body.regen:
                state = aux.update_state(
                    state,
                    user_msg,
                    plan_text,
                    body.continue_scene,
                    plan_data=plan_data,
                    main_npc=npc,
                    cast=cast,
                )
                if (
                    RESOLUTION_ENABLED
                    and not body.ooc
                    and not body.continue_scene
                    and user_msg
                ):
                    try:
                        if not state.get("seed"):
                            state["seed"] = resolution.seed_for_session(
                                sid, sess["created_at"]
                            )
                        receipt = resolution.resolve(user_msg, state)
                        if receipt:
                            state = resolution.apply(state, receipt)
                            extra.append(receipt.as_block())
                            metrics.inc("resolution_rolls")
                            if not roll_expected:
                                yield json.dumps(
                                    {"status": "rolling"}, ensure_ascii=False
                                ) + "\n"
                            yield json.dumps(
                                {"resolution": receipt.as_dict()},
                                ensure_ascii=False,
                            ) + "\n"
                            print(
                                f"[resolution] {receipt.label}: "
                                f"d20={receipt.roll} mod={receipt.modifier} "
                                f"total={receipt.total} CD={receipt.dc} -> "
                                f"{receipt.outcome}",
                                flush=True,
                            )
                    except Exception as exc:  # noqa: BLE001
                        print(f"[resolution] erro (ignorado): {exc}", flush=True)
                offscreen_events = []
                if OFFSCREEN_ENABLED and not body.ooc:
                    try:
                        offscreen_events = offscreen.tick(
                            state,
                            cast,
                            npc,
                            seed=state.get("seed"),
                        )
                        if offscreen_events:
                            extra.append(offscreen.block(offscreen_events))
                            metrics.inc("offscreen_events", len(offscreen_events))
                            print(
                                "[offscreen] "
                                + " | ".join(
                                    str(item.get("text") or "")[:120]
                                    for item in offscreen_events
                                ),
                                flush=True,
                            )
                    except Exception as exc:  # noqa: BLE001
                        print(f"[offscreen] erro (ignorado): {exc}", flush=True)
                if VOICES_ENABLED and not body.ooc:
                    try:
                        world_facts = []
                        if world is not None:
                            world_facts = [
                                str(row["text"])
                                for row in work.execute(
                                    "SELECT text FROM world_facts WHERE world_id=? "
                                    "ORDER BY id DESC LIMIT 6",
                                    (world["id"],),
                                ).fetchall()
                            ]
                        voice = world_voices.generate(
                            state,
                            world,
                            npc,
                            cast,
                            events=offscreen_events,
                            seed=state.get("seed"),
                            session_id=sid,
                            world_facts=world_facts,
                        )
                        if voice:
                            try:
                                world_voices.save(work, voice)
                                work.commit()
                            except Exception as exc:  # noqa: BLE001
                                print(f"[voices] save falhou: {exc}", flush=True)
                            extra.append(world_voices.block(voice))
                            metrics.inc("world_voices")
                            print(
                                f"[voices] {voice['kind']} de {voice['author']}: "
                                f"{voice['claims'][0][:120] if voice.get('claims') else ''}",
                                flush=True,
                            )
                    except Exception as exc:  # noqa: BLE001
                        print(f"[voices] erro (ignorado): {exc}", flush=True)
            extra.append(aux.ledger_block(state))
            threads_block = _threads_block(work, sid, turn=int(state.get("turn", 0)))
            if threads_block:
                extra.append(threads_block)
            entity_block = _entity_block(work, sid, npc, protagonist, cast=cast)
            if entity_block:
                extra.append(entity_block)
            lore = _lore_block(work, world, user_msg)
            if lore:
                print("[lore]", lore[:200].replace("\n", " | "))
                extra.append(lore)
            kb = aux.knowledge_block(npc, state)
            if kb:
                extra.append(kb)
            if ev:
                extra.append(ev)

            rag_block = ""
            if (
                embeddings.ENABLED
                and not body.ooc
                and not llm.cpu_mode()
                and llm.utility_available()
            ):
                recent_ids = [
                    row["id"]
                    for row in work.execute(
                        "SELECT id FROM messages WHERE session_id=? "
                        "ORDER BY id DESC LIMIT ?",
                        (sid, HISTORY_HARD_CAP),
                    ).fetchall()
                ]
                try:
                    rag_block = await asyncio.wait_for(
                        embeddings.memory_block(
                            work, sid, user_msg, exclude_ids=recent_ids
                        ),
                        timeout=EMBED_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    print("[rag] timeout; seguindo sem memoria semantica", flush=True)
                    metrics.inc("rag_errors")
                    rag_block = ""
                if rag_block:
                    print("[rag] bloco:", len(rag_block))
            memory_cards = _memory_cards(
                work, sid, npc, protagonist, cast, state, plan_data
            )
            context = build_context(
                work,
                sess,
                npc,
                protagonist,
                world,
                extra,
                query=user_msg,
                extra_memory=rag_block,
                cast=cast,
                state=state,
                memory_cards=memory_cards,
            )
            if not body.continue_scene and not body.regen:
                now = time.time()
                work.execute(
                    "INSERT INTO messages (session_id, role, content, created_at) VALUES (?,?,?,?)",
                    (sid, "user", user_msg, now),
                )
                work.execute(
                    "UPDATE sessions SET updated_at=?, turn_count=turn_count+1 WHERE id=?",
                    (now, sid),
                )
                work.commit()
                context.append({"role": "user", "content": user_msg})
            if not any(m.get("role") == "user" for m in context):
                context.append(
                    {
                        "role": "user",
                        "content": user_msg
                        or "Continue a cena a partir daqui.",
                    }
                )
            work.close()
            work = None
            yield json.dumps({"status": "writing"}, ensure_ascii=False) + "\n"
            chat_ctx = 8192 if llm.cpu_mode() else _active_num_ctx
            stream_opts = {
                "temperature": 0.85,
                "num_ctx": chat_ctx,
                "num_predict": NARRATION_TOKENS,
            }
            async for kind, value in _iter_with_pings(
                llm.stream_chat(
                    model,
                    context,
                    False,
                    stream_opts,
                )
            ):
                if kind == "ping":
                    yield json.dumps(
                        {"status": "writing"}, ensure_ascii=False
                    ) + "\n"
                    continue
                parts.append(value)
                yield json.dumps({"delta": value}, ensure_ascii=False) + "\n"
            text_so_far = "".join(parts)
            done_reason = str(stream_opts.get("_done_reason") or "")
            for _ in range(max(0, NARRATION_CONTINUATIONS)):
                if done_reason != "length" and not _looks_truncated(text_so_far):
                    break
                try:
                    yield json.dumps(
                        {"status": "writing"}, ensure_ascii=False
                    ) + "\n"
                    extra = await _continue_narration(
                        model, context, text_so_far, chat_ctx
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"[narration] continuacao falhou: {exc}", flush=True)
                    break
                if not extra:
                    break
                parts.append(extra)
                text_so_far += extra
                done_reason = "stop"
                yield json.dumps({"delta": extra}, ensure_ascii=False) + "\n"
        except Exception as e:
            metrics.inc("chat_errors")
            yield json.dumps({"error": str(e)}, ensure_ascii=False) + "\n"
        finally:
            if work is not None:
                work.close()
            metrics.inc("chat_done")
            metrics.observe("narration_seconds", time.time() - started)
            text = "".join(parts).strip()
            final_text = text
            replace = None
            if receipt is not None:
                try:
                    conflict = resolution.check_contradiction(
                        final_text, receipt.as_dict()
                    )
                    if conflict:
                        metrics.inc("receipt_contradictions")
                        print(
                            f"[resolution] possível contradição: {conflict}",
                            flush=True,
                        )
                except Exception as exc:  # noqa: BLE001
                    print(f"[resolution] checagem falhou: {exc}", flush=True)
            new_location = str(
                (state.get("scene") or {}).get("location")
                or state.get("location")
                or ""
            ).strip()
            if len(text) > 80 and not body.ooc:
                try:
                    final_text, adjusted = await _review_narration(
                        text,
                        npc,
                        state,
                        user_msg,
                        prev_location,
                        new_location if not body.regen else "",
                    )
                    if adjusted and final_text.strip() != text.strip():
                        replace = final_text
                        print("[quality] texto ajustado para o app", flush=True)
                except Exception as exc:  # noqa: BLE001
                    print(f"[quality] revisao falhou: {exc}", flush=True)
                    final_text = text
            try:
                if final_text:
                    c = db.connect()
                    now = time.time()
                    mid = _insert_message_once(
                        c, sid, "assistant", final_text
                    )
                    state_json = json.dumps(state, ensure_ascii=False)
                    c.execute(
                        "INSERT OR REPLACE INTO message_states (message_id, session_id, state, created_at) VALUES (?,?,?,?)",
                        (mid, sid, state_json, now),
                    )
                    c.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, sid))
                    c.execute("UPDATE sessions SET ledger=? WHERE id=?", (state_json, sid))
                    c.commit()
                    _save_chat_response(
                        c,
                        request_key,
                        sid,
                        state.get("turn", 0),
                        user_msg,
                        final_text,
                    )
                    if not body.ooc:
                        commitments.audit_narration(
                            c, sid, int(state.get("turn", 0)), final_text, plan_data
                        )
                        commitment_stats = commitments.stats(
                            c, sid, int(state.get("turn", 0))
                        )
                    c.close()
                    if CONSISTENCY_AUTO:
                        asyncio.create_task(_auto_consistency(sid))
                    if embeddings.ENABLED and llm.utility_available():
                        asyncio.create_task(_auto_index(sid))
            finally:
                if chat_lock is not None:
                    try:
                        chat_lock.release()
                    except RuntimeError:
                        pass
            if replace:
                yield json.dumps({"replace": replace}, ensure_ascii=False) + "\n"
            yield json.dumps(
                {
                    "done": True,
                    "nr": int(state.get("nr", aux.NR_START)),
                    "axes": state.get("axes") or {},
                    "vinculo": state.get("vinculo") or "",
                    "foco": (plan_data or {}).get("foco") or "",
                    "commitments": commitment_stats,
                    "resolution": receipt.as_dict() if receipt is not None else None,
                    "scene": state.get("scene") or {},
                    "pacing": state.get("pacing") or {},
                    "mood": (state.get("moment") or {}).get("mood")
                    or state.get("mood")
                    or "",
                    "relationship_events": (state.get("relationship_events") or [])[-3:],
                    "cast": [
                        {
                            "character_id": c["id"],
                            "name": c["name"],
                            **aux.cast_relationship(state, c["id"]),
                        }
                        for c in cast
                    ],
                },
                ensure_ascii=False,
            ) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")
