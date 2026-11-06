"""Geracao de capas: DeepSeek cria o prompt e um modelo de imagem renderiza."""

import asyncio
import hashlib
import io
import json
import math
import os
import random
import re
import time
import uuid
from urllib.parse import quote

import httpx
from PIL import Image

import llm
import prompts

ART_DIR = os.environ.get(
    "LUMINUS_ART_DIR", os.path.join(os.path.dirname(__file__), "art")
)
COMFY_URL = os.environ.get("LUMINUS_COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
COMFY_SERVICE = os.environ.get("LUMINUS_COMFY_SERVICE", "luminus-comfy")
COMFY_GPU = int(os.environ.get("LUMINUS_COMFY_GPU", "2"))
COMFY_POOL = (os.environ.get("LUMINUS_COMFY_POOL") or "").strip()
COMFY_PROBE_TIMEOUT = float(os.environ.get("LUMINUS_COMFY_PROBE_TIMEOUT", "1.5"))
COMFY_PROBE_TTL = float(os.environ.get("LUMINUS_COMFY_PROBE_TTL", "20"))
COMFY_AUTOSTART = os.environ.get("LUMINUS_COMFY_AUTOSTART", "0") == "1"
COMFY_FREE_AFTER = os.environ.get("LUMINUS_COMFY_FREE_AFTER", "1") != "0"
COMFY_ATTEMPTS = max(1, int(os.environ.get("LUMINUS_COMFY_ATTEMPTS", "2")))
OLLAMA_URL = (os.environ.get("OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")
UTILITY_URL = os.environ.get("LUMINUS_UTILITY_URL", "http://127.0.0.1:11435").rstrip("/")
GPU_LOCK = asyncio.Lock()


class ComfyWorker:
    """Worker de ComfyUI no pool (primario na GPU2 ou lote nas 3060 Ti)."""

    __slots__ = (
        "url", "gpu", "unit", "primary", "busy", "down_until",
        "autostarted", "lock",
    )

    def __init__(self, url: str, gpu: int | None = None, unit: str | None = None,
                 primary: bool = False):
        self.url = url.rstrip("/")
        self.gpu = gpu
        self.unit = unit
        self.primary = primary
        self.busy = False
        self.down_until = 0.0
        self.autostarted = False
        self.lock = asyncio.Lock()

    @property
    def label(self) -> str:
        if self.gpu is None:
            return self.url
        return f"{self.url} (GPU {self.gpu})"


def _parse_worker(
    raw: str,
    *,
    primary: bool = False,
    gpu: int | None = None,
    unit: str | None = None,
) -> ComfyWorker | None:
    """Le ``url`` ou ``url@gpu@unit``. Extras ausentes herdam os defaults."""
    parts = [part.strip() for part in str(raw or "").split("@")]
    url = (parts[0] if parts else "").rstrip("/")
    if not url:
        return None
    if len(parts) > 1 and parts[1]:
        try:
            gpu = int(parts[1])
        except ValueError:
            pass
    if len(parts) > 2 and parts[2]:
        unit = parts[2]
    return ComfyWorker(url, gpu=gpu, unit=unit, primary=primary)


def _build_workers(
    primary_url: str | None = None, pool_raw: str | None = None
) -> list[ComfyWorker]:
    primary = _parse_worker(
        COMFY_URL if primary_url is None else primary_url,
        primary=True,
        gpu=COMFY_GPU,
    )
    workers: list[ComfyWorker] = [primary] if primary else []
    raw = COMFY_POOL if pool_raw is None else pool_raw
    for item in raw.split(","):
        worker = _parse_worker(item)
        if worker and all(w.url != worker.url for w in workers):
            workers.append(worker)
    return workers


_WORKERS = _build_workers()
_POOL_LOCK = asyncio.Lock()
_POOL_RR = 0

ADULT_BLOCK_TERMS = (

)


STYLE_HINTS = {
    "anime": "high-end anime key visual, clean linework, expressive eyes, cel shading",
    "pintura": "painterly dark fantasy portrait, oil painting texture, chiaroscuro",
    "grimdark": "grimdark fantasy art, muted desaturated palette, harsh shadows, gritty detail",
    "aquarela": "watercolor illustration, soft washes, textured paper, atmospheric",
}

FREE_STYLE_HINTS = {
    "anime": "high-end anime key visual, clean linework, cel shading, vivid colors",
    "pintura": "painterly illustration, oil painting texture, chiaroscuro, rich color",
    "grimdark": "grimdark fantasy art, muted desaturated palette, harsh shadows, gritty detail",
    "aquarela": "watercolor illustration, soft washes, textured paper, atmospheric",
}

_LOCK = asyncio.Lock()
_LAST_REQUEST = 0.0



class ArtError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _adult_guard(prompt: str) -> str:
    """Valida o prompt antes de renderizar e bloqueia termos proibidos."""
    text = (prompt or "").strip()
    if not text:
        raise ArtError(400, "Descreva a imagem que voce quer.")
    low = text.lower()
    for term in ADULT_BLOCK_TERMS:
        term = (term or "").strip().lower()
        if term and term in low:
            raise ArtError(
                400,
                "O prompt contém termos não permitidos. Ajuste a descrição "
                "e tente de novo.",
            )
    return text


def ensure_dir():
    os.makedirs(ART_DIR, exist_ok=True)


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


FACE_MODEL = _env("LUMINUS_FACE_MODEL", "bbox/face_yolov8m.pt")
HAND_MODEL = _env("LUMINUS_HAND_MODEL", "bbox/hand_yolov8s.pt")
SAM_MODEL = _env("LUMINUS_SAM_MODEL", "sam_vit_b_01ec64.pth")
ANIME_UPSCALER = _env("LUMINUS_ANIME_UPSCALER", "4x-AnimeSharp.pth")
PHOTO_UPSCALER = _env("LUMINUS_PHOTO_UPSCALER", "4x-UltraSharp.pth")
USE_FACE_SAM = _env("LUMINUS_FACE_SAM", "0") == "1"


def deepseek_configured() -> bool:
    return bool(_env("DEEPSEEK_API_KEY"))


def status() -> dict:
    return {
        "provider": _env("LUMINUS_ART_PROVIDER", "pollinations"),
        "model": _env("LUMINUS_ART_MODEL", "sana") or "sana",
        "token": bool(_env("POLLINATIONS_TOKEN")),
        "deepseek": deepseek_configured(),
        "deepseek_model": _env("DEEPSEEK_MODEL", "deepseek-flash"),
        "adult_checkpoint": _env(
            "LUMINUS_ADULT_CHECKPOINT", "NoobAI-XL-v1.1.safetensors"
        ),
        "photoreal_checkpoint": _env(
            "LUMINUS_PHOTOREAL_CHECKPOINT",
            "lustifySDXLNSFWSFW_v40.safetensors",
        ),
        "width": int(_env("LUMINUS_ART_WIDTH", "768")),
        "height": int(_env("LUMINUS_ART_HEIGHT", "1024")),
        "face_detail": _env("LUMINUS_FACE_DETAIL", "1") == "1",
        "hand_detail": _env("LUMINUS_HAND_DETAIL", "0") == "1",
        "upscale": float(_env("LUMINUS_UPSCALE", "1.5")),
        "face_model": FACE_MODEL,
        "hand_model": HAND_MODEL,
        "sam_model": SAM_MODEL,
        "anime_upscaler": ANIME_UPSCALER,
        "photo_upscaler": PHOTO_UPSCALER,
        "comfy_gpu": COMFY_GPU,
        "workers": [
            {
                "url": worker.url,
                "gpu": worker.gpu,
                "primary": worker.primary,
                "autostart": bool(worker.unit) and COMFY_AUTOSTART,
            }
            for worker in _WORKERS
        ],
    }


def _style(body: dict) -> str:
    key = (body.get("style") or "anime").strip().lower()
    return STYLE_HINTS.get(key, STYLE_HINTS["anime"])


def _free_style(body: dict) -> str:
    key = (body.get("style") or "anime").strip().lower()
    return FREE_STYLE_HINTS.get(key, FREE_STYLE_HINTS["anime"])


def _quality_prefix(text: str, photoreal: bool = False) -> str:
    if photoreal:
        prefix = _env(
            "LUMINUS_PHOTOREAL_PREFIX", "photorealistic, cinematic, "
        ).strip()
    else:
        prefix = _env(
            "LUMINUS_ADULT_PREFIX", "masterpiece, best quality, "
        ).strip()
    return f"{prefix} {text}".strip() if prefix else text


def _short(value: str, limit: int = 360) -> str:
    value = re.sub(r"\s+", " ", (value or "")).strip()
    return value[:limit]


_HAIR = (
    "short black hair",
    "silver hair tied back",
    "auburn shoulder-length hair",
    "dark braided hair",
    "ash-blond cropped hair",
    "wavy chestnut hair",
    "white hair with a single braid",
    "copper hair in a loose tail",
)
_EYES = (
    "amber eyes",
    "grey eyes",
    "deep green eyes",
    "dark brown eyes",
    "pale blue eyes",
    "hazel eyes",
    "violet eyes",
    "golden eyes",
)
_SKIN = (
    "olive skin",
    "pale skin",
    "warm brown skin",
    "bronze skin",
    "freckled fair skin",
    "deep brown skin",
    "sun-weathered tan skin",
)
_BUILD = (
    "lean build",
    "broad-shouldered build",
    "slender build",
    "stocky build",
    "tall wiry build",
    "compact athletic build",
)
_OUTFIT = (
    "a worn leather coat with brass clasps",
    "a dark hooded cloak with silver embroidery",
    "a patched traveller's jacket and scarf",
    "a reinforced longcoat with a high collar",
    "a simple linen tunic under a leather harness",
    "a fur-trimmed coat over practical boots",
)
_MARK = (
    "a thin scar across the left brow",
    "a small tattoo beneath the right eye",
    "a chipped left ear",
    "a birthmark on the side of the neck",
    "a split right eyebrow",
    "a faded burn mark on the jaw",
)
_PALETTE = (
    "charcoal, rust red and tarnished gold",
    "deep blue, ivory and worn brass",
    "forest green, umber and pale silver",
    "plum, slate grey and warm copper",
    "black, bone white and muted crimson",
    "teal, sand and old bronze",
)


def _pick_trait(seed: int, name: str, salt: str, values: tuple[str, ...]) -> str:
    digest = hashlib.sha256(f"{seed}|{name}|{salt}".encode("utf-8")).digest()
    return values[int.from_bytes(digest[:4], "big") % len(values)]


def stable_seed(key, name: str = "") -> int:
    raw = f"{key}|{name}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:4], "big") % 2_147_483_646 + 1


def _local_identity(body: dict) -> str:
    name = _short(body.get("name"), 80) or "the character"
    seed = int(body.get("art_seed") or body.get("seed") or 0)
    if not seed:
        seed = stable_seed(body.get("character_id") or name, name)
    hair = _pick_trait(seed, name, "hair", _HAIR)
    eyes = _pick_trait(seed, name, "eyes", _EYES)
    skin = _pick_trait(seed, name, "skin", _SKIN)
    build = _pick_trait(seed, name, "build", _BUILD)
    outfit = _pick_trait(seed, name, "outfit", _OUTFIT)
    mark = _pick_trait(seed, name, "mark", _MARK)
    palette = _pick_trait(seed, name, "palette", _PALETTE)
    appearance = _short(body.get("appearance"), 620)
    tags = [
        str(tag).strip()
        for tag in (body.get("tags") or [])
        if str(tag).strip()
    ]
    role = f" Role: {', '.join(tags[:3])}." if tags else ""
    if len(appearance) >= 24:
        core = appearance
        if not re.search(
            r"scar|tattoo|mark|piercing|eye patch|cicatriz|marca|tatuagem|brinco",
            appearance,
            re.I,
        ):
            core = f"{core}; {mark}"
    else:
        core = f"{build}, {skin}, {hair}, {eyes}, wearing {outfit}; {mark}"
    return (
        f"{name}, {core}.{role} Fixed palette: {palette}. "
        "Use these exact physical features in every image."
    )


def _identity_payload(body: dict) -> dict:
    return {
        "name": body.get("name") or "",
        "kind": body.get("kind") or "npc",
        "appearance": _short(body.get("appearance"), 700),
        "description_public": _short(body.get("description_public"), 400),
        "tags": body.get("tags") or [],
        "world": body.get("world") or "",
        "style": body.get("style") or "anime",
    }


def _identity_cover_prompt(
    identity: str, expression: str = "", style: str = "anime"
) -> str:
    expr = (expression or "").strip() or "calm neutral expression"
    parts = [
        "single character, visual novel portrait, same character in every image",
        identity.strip(),
        f"facial expression: {expr}",
        "waist-up, facing the viewer, centered, simple dark neutral background",
        _style({"style": style}),
        "identity lock: identical face shape, same hairstyle and hair color, "
        "same eye color and skin tone, same outfit and accessories",
        "no text, no logo, no watermark, no border",
    ]
    return ", ".join(p.strip(" .,") for p in parts if p and p.strip())


def _local_prompt(body: dict) -> str:
    parts = [
        f"vertical 3:4 character cover portrait of {_short(body.get('name'), 80) or 'the protagonist'}",
        _short(body.get("appearance")),
        _short(body.get("personality")),
        _short(body.get("backstory")),
        f"world: {_short(body.get('world'), 80)}" if body.get("world") else "",
        f"tags: {', '.join(body.get('tags') or [])}" if body.get("tags") else "",
        _style(body),
        "cinematic visual novel key art, detailed face, dramatic lighting, "
        "deep purple and warm gold palette, elegant composition, "
        "no text, no logo, no watermark, no border",
    ]
    return ", ".join(p for p in parts if p)


def _local_scene_prompt(body: dict) -> str:
    parts = [
        "cinematic 16:9 visual novel background illustration",
        f"world: {_short(body.get('world'), 120)}" if body.get("world") else "",
        f"setting: {_short(body.get('setting'), 500)}" if body.get("setting") else "",
        f"context: {_short(body.get('context'), 400)}" if body.get("context") else "",
        f"npc: {_short(body.get('name'), 80)}" if body.get("name") else "",
        _style(body),
        "atmospheric perspective, painterly detail, dramatic lighting, "
        "deep purple and warm gold palette, elegant composition, "
        "no text, no logo, no watermark, no border",
    ]
    return ", ".join(p for p in parts if p)


def _local_free_prompt(
    text: str, body: dict, photoreal: bool = False
) -> str:
    if photoreal:
        style = _env(
            "LUMINUS_PHOTOREAL_STYLE",
            "photorealistic, cinematic, natural skin texture, realistic "
            "lighting, 85mm lens, shallow depth of field, high detail, RAW photo",
        )
        tail = "no text, no logo, no watermark, no border"
    else:
        style = _free_style(body)
        tail = (
            "high quality, detailed, cinematic lighting, elegant composition, "
            "no text, no logo, no watermark, no border"
        )
    return ", ".join(p for p in (text.strip(), style, tail) if p)


def _clean_prompt(raw: str) -> str:
    text = re.sub(r"\s+", " ", (raw or "")).strip()
    text = re.sub(r"^(prompt|image prompt)\s*:\s*", "", text, flags=re.I)
    text = text.strip("`\"' ")
    return text[:1200]


async def _deepseek_prompt(
    body: dict, scene: bool = False, identity: bool = False
) -> str:
    key = _env("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY ausente")
    base = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    if identity:
        system = prompts.IDENTITY_SYSTEM
        payload = _identity_payload(body)
    elif scene:
        system = prompts.SCENE_DIRECTOR_SYSTEM
        payload = {
            "name": body.get("name") or "",
            "world": body.get("world") or "",
            "world_description": _short(body.get("world_description"), 700),
            "setting": _short(body.get("setting"), 900),
            "context": _short(body.get("context"), 900),
            "style": body.get("style") or "anime",
            "style_hint": _style(body),
        }
    else:
        system = prompts.ART_DIRECTOR_SYSTEM
        payload = {
            "name": body.get("name") or "",
            "kind": body.get("kind") or "npc",
            "appearance": _short(body.get("appearance"), 700),
            "personality": _short(body.get("personality"), 500),
            "backstory": _short(body.get("backstory"), 500),
            "tags": body.get("tags") or [],
            "world": body.get("world") or "",
            "style": body.get("style") or "anime",
            "style_hint": _style(body),
        }
    request = {
        "model": _env("DEEPSEEK_MODEL", "deepseek-flash"),
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    "Dados do personagem (JSON):\n"
                    if identity
                    else "Dados da cena (JSON):\n"
                )
                + json.dumps(payload, ensure_ascii=False),
            },
        ],
        "thinking": {"type": "disabled"},
        "temperature": 0.45 if identity else 0.9,
        "max_tokens": 320,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{base}/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=request,
        )
    if response.status_code >= 300:
        detail = response.text[:400].replace("\n", " ")
        raise RuntimeError(f"DeepSeek HTTP {response.status_code}: {detail}")
    data = response.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("DeepSeek retornou uma resposta inesperada") from exc
    prompt = _clean_prompt(content)
    if len(prompt) < 20:
        raise RuntimeError("DeepSeek retornou um prompt vazio")
    return prompt


async def _deepseek_free_prompt(
    text: str,
    body: dict,
    adult: bool = False,
    photoreal: bool = False,
) -> str:
    key = _env("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY ausente")
    base = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    payload = {
        "idea": _short(text, 900),
        "style": body.get("style") or "anime",
        "style_hint": _free_style(body),
        "adult": adult,
        "photoreal": photoreal,
        "face_detail": face_detail,
        "hand_detail": hand_detail,
        "upscale": upscale,
    }
    if photoreal:
        system = prompts.FREE_IMAGE_PHOTOREAL_SYSTEM
    elif adult:
        system = prompts.FREE_IMAGE_ADULT_SYSTEM
    else:
        system = prompts.FREE_IMAGE_SYSTEM
    request = {
        "model": _env("DEEPSEEK_MODEL", "deepseek-flash"),
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": "Ideia do usuario (JSON):\n"
                + json.dumps(payload, ensure_ascii=False),
            },
        ],
        "thinking": {"type": "disabled"},
        "temperature": 0.9,
        "max_tokens": 320,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{base}/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=request,
        )
    if response.status_code >= 300:
        detail = response.text[:400].replace("\n", " ")
        raise RuntimeError(f"DeepSeek HTTP {response.status_code}: {detail}")
    data = response.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("DeepSeek retornou uma resposta inesperada") from exc
    prompt = _clean_prompt(content)
    if len(prompt) < 20:
        raise RuntimeError("DeepSeek retornou um prompt vazio")
    return prompt


async def _local_llm_prompt(
    body: dict, scene: bool = False, identity: bool = False
) -> str:
    if identity:
        system = prompts.IDENTITY_SYSTEM
        payload = _identity_payload(body)
    elif scene:
        system = prompts.SCENE_DIRECTOR_SYSTEM
        payload = {
            "name": body.get("name") or "",
            "world": body.get("world") or "",
            "world_description": _short(body.get("world_description"), 700),
            "setting": _short(body.get("setting"), 900),
            "context": _short(body.get("context"), 900),
            "style": body.get("style") or "anime",
            "style_hint": _style(body),
        }
    else:
        system = prompts.ART_DIRECTOR_SYSTEM
        payload = {
            "name": body.get("name") or "",
            "kind": body.get("kind") or "npc",
            "appearance": _short(body.get("appearance"), 700),
            "personality": _short(body.get("personality"), 500),
            "backstory": _short(body.get("backstory"), 500),
            "tags": body.get("tags") or [],
            "world": body.get("world") or "",
            "style": body.get("style") or "anime",
            "style_hint": _style(body),
        }
    out = await llm.complete(
        llm.UTILITY_MODEL,
        [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    "Dados do personagem (JSON):\n"
                    if identity
                    else "Dados da cena (JSON):\n"
                )
                + json.dumps(payload, ensure_ascii=False),
            },
        ],
        False,
        {
            "temperature": 0.35 if identity else 0.75,
            "num_ctx": 4096,
            "num_predict": 240,
        },
        base=llm.OLLAMA_UTILITY,
    )
    prompt = _clean_prompt(out)
    if len(prompt) < 20:
        raise RuntimeError("LLM local retornou um prompt vazio")
    return prompt


async def _local_llm_free_prompt(
    text: str,
    body: dict,
    adult: bool = False,
    photoreal: bool = False,
) -> str:
    if photoreal:
        system = prompts.FREE_IMAGE_PHOTOREAL_SYSTEM
    elif adult:
        system = prompts.FREE_IMAGE_ADULT_SYSTEM
    else:
        system = prompts.FREE_IMAGE_SYSTEM
    payload = {
        "idea": _short(text, 900),
        "style": body.get("style") or "anime",
        "style_hint": _free_style(body),
        "adult": adult,
        "photoreal": photoreal,
    }
    out = await llm.complete(
        llm.UTILITY_MODEL,
        [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": "Ideia do usuario (JSON):\n"
                + json.dumps(payload, ensure_ascii=False),
            },
        ],
        False,
        {"temperature": 0.8, "num_ctx": 4096, "num_predict": 240},
        base=llm.OLLAMA_UTILITY,
    )
    prompt = _clean_prompt(out)
    if len(prompt) < 20:
        raise RuntimeError("LLM local retornou um prompt vazio")
    return prompt


async def _build_prompt(body: dict, scene: bool = False) -> tuple[str, str, str]:
    fallback = _local_scene_prompt(body) if scene else _local_prompt(body)
    if deepseek_configured():
        try:
            return await _deepseek_prompt(body, scene=scene), "deepseek", ""
        except Exception as exc:
            print("[art] deepseek falhou:", exc)
    if _env("LUMINUS_ART_PROMPT_LLM", "1") == "1":
        try:
            return (
                await _local_llm_prompt(body, scene=scene),
                "local-llm",
                "",
            )
        except Exception as exc:
            print("[art] prompt LLM local falhou:", exc)
            return (
                fallback,
                "local",
                "LLM local indisponivel; usei um prompt local.",
            )
    return (
        fallback,
        "local",
        "DEEPSEEK_API_KEY nao configurada; usei um prompt local.",
    )


async def _build_identity(body: dict) -> tuple[str, str, str]:
    fallback = _local_identity(body)
    if deepseek_configured():
        try:
            return await _deepseek_prompt(body, identity=True), "deepseek", ""
        except Exception as exc:
            print("[art] deepseek identity falhou:", exc)
    if _env("LUMINUS_ART_PROMPT_LLM", "1") == "1":
        try:
            return (
                await _local_llm_prompt(body, identity=True),
                "local-llm",
                "",
            )
        except Exception as exc:
            print("[art] identity LLM local falhou:", exc)
            return (
                fallback,
                "local",
                "LLM local indisponivel; usei uma identidade local.",
            )
    return (
        fallback,
        "local",
        "DEEPSEEK_API_KEY nao configurada; usei uma identidade local.",
    )


async def ensure_identity(body: dict, force: bool = False) -> dict:
    style = str(body.get("visual_style") or body.get("style") or "anime").lower()
    if style not in STYLE_HINTS:
        style = "anime"
    seed = int(body.get("art_seed") or body.get("seed") or 0)
    name = str(body.get("name") or "")
    if force:
        seed = random.randint(1, 2_147_483_647)
    elif not seed:
        seed = stable_seed(body.get("character_id") or name, name)
    saved = str(body.get("visual_prompt") or "").strip()
    if saved and not force:
        return {
            "prompt": saved,
            "seed": seed,
            "style": style,
            "source": "saved",
            "warning": "",
        }
    prompt, source, warning = await _build_identity({**body, "seed": seed})
    return {
        "prompt": prompt,
        "seed": seed,
        "style": style,
        "source": source,
        "warning": warning,
    }


def _pollinations_url(prompt: str, seed: int, width: int, height: int) -> str:
    base = _env(
        "LUMINUS_ART_URL", "https://image.pollinations.ai/prompt"
    ).rstrip("/")
    params = f"width={width}&height={height}&nologo=true&seed={seed}"
    model = _env("LUMINUS_ART_MODEL", "")
    if model:
        params += f"&model={quote(model)}"
    token = _env("POLLINATIONS_TOKEN", "")
    if token:
        params += f"&token={quote(token)}"
    return f"{base}/{quote(prompt, safe='')}?{params}"


async def _render_pollinations(
    prompt: str, seed: int, width: int | None = None, height: int | None = None
) -> bytes:
    global _LAST_REQUEST
    width = width or int(_env("LUMINUS_ART_WIDTH", "768"))
    height = height or int(_env("LUMINUS_ART_HEIGHT", "1024"))
    cooldown = int(_env("LUMINUS_ART_COOLDOWN", "45"))
    async with _LOCK:
        wait = cooldown - (time.monotonic() - _LAST_REQUEST)
        if wait > 0:
            raise ArtError(
                429,
                f"Aguarde {math.ceil(wait)}s para gerar outra capa "
                "(limite do provedor gratuito).",
            )
        url = _pollinations_url(prompt, seed, width, height)
        try:
            async with httpx.AsyncClient(
                timeout=180.0, follow_redirects=True
            ) as client:
                response = await client.get(url)
        except Exception as exc:
            raise ArtError(502, f"Falha ao contatar o gerador de imagem: {exc}")
        content_type = (response.headers.get("content-type") or "").lower()
        if response.status_code >= 300 or not content_type.startswith("image/"):
            text = response.text[:500].replace("\n", " ") if response.text else ""
            if response.status_code == 402 or "FREE_LIMIT" in text:
                raise ArtError(
                    429,
                    "Limite gratuito do gerador atingido. Aguarde cerca de "
                    "45 segundos e tente de novo.",
                )
            raise ArtError(
                502,
                f"Gerador de imagem respondeu HTTP {response.status_code}: {text}",
            )
        _LAST_REQUEST = time.monotonic()
        return response.content


async def _gpu_memory_used(index: int) -> int:
    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            "-i",
            str(index),
            "--query-gpu=memory.used",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        return int(out.decode().strip().splitlines()[0].strip())
    except Exception:
        return 0


async def _comfy_up(url: str = COMFY_URL) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{url}/system_stats")
            return response.status_code == 200
    except Exception:
        return False


async def _comfy_queue(url: str) -> int | None:
    """Fila do worker (0 = livre) ou None se estiver fora do ar."""
    try:
        async with httpx.AsyncClient(timeout=COMFY_PROBE_TIMEOUT) as client:
            response = await client.get(f"{url}/queue")
            if response.status_code != 200:
                return None
            data = response.json()
            running = data.get("queue_running") or []
            pending = data.get("queue_pending") or []
            return len(running) + len(pending)
    except Exception:
        return None


async def _comfy_service(action: str, unit: str = COMFY_SERVICE) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl",
            "--user",
            action,
            unit,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return (await proc.wait()) == 0
    except Exception as exc:
        print(f"[art] systemctl {action} {unit} falhou: {exc}")
        return False


async def _comfy_wait_up(url: str = COMFY_URL, timeout: float = 60.0) -> bool:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if await _comfy_up(url):
            return True
        await asyncio.sleep(1)
    return False


async def _comfy_wait_down(url: str = COMFY_URL, timeout: float = 25.0) -> bool:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if not await _comfy_up(url):
            return True
        await asyncio.sleep(1)
    return False


async def _comfy_free(url: str) -> None:
    """Libera VRAM do worker sem parar o processo (ComfyUI /free)."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{url}/free",
                json={"unload_models": True, "free_memory": True},
            )
    except Exception as exc:
        print(f"[art] falha ao liberar VRAM de {url}: {exc}")


async def _ollama_busy() -> bool:
    """True se o Narrador (Ollama principal) estiver com modelo carregado."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{OLLAMA_URL}/api/ps")
            return bool(response.json().get("models"))
    except Exception:
        return True


async def _probe_worker(worker: ComfyWorker) -> int | None:
    return await _comfy_queue(worker.url)


async def _pick_worker(exclude: set[str] | None = None) -> ComfyWorker | None:
    """Escolhe o worker de menor fila; pula os que estao fora do ar."""
    global _POOL_RR
    exclude = exclude or set()
    now = time.monotonic()
    candidates = [
        w
        for w in _WORKERS
        if w.url not in exclude and (w.primary or w.down_until <= now)
    ]
    if not candidates:
        return None
    queues = await asyncio.gather(*(_probe_worker(w) for w in candidates))
    up: list[tuple[ComfyWorker, int]] = []
    for worker, queue in zip(candidates, queues):
        if queue is None:
            if not worker.primary:
                worker.down_until = now + COMFY_PROBE_TTL
            continue
        worker.down_until = 0.0
        up.append((worker, queue))
    if not up:
        return None
    async with _POOL_LOCK:
        start = _POOL_RR % len(_WORKERS)
        _POOL_RR += 1
        up.sort(
            key=lambda item: (
                item[1],
                1 if item[0].busy else 0,
                (_WORKERS.index(item[0]) - start) % len(_WORKERS),
            )
        )
        worker = up[0][0]
        worker.busy = True
        return worker


async def _autostart_worker(exclude: set[str]) -> ComfyWorker | None:
    """Sobe um worker do pool quando o Narrador esta ocioso."""
    if not COMFY_AUTOSTART or await _ollama_busy():
        return None
    now = time.monotonic()
    for worker in _WORKERS:
        if worker.primary or worker.url in exclude or not worker.unit:
            continue
        if worker.down_until > now:
            continue
        if not await _comfy_service("start", worker.unit):
            continue
        if await _comfy_wait_up(worker.url, 60):
            worker.autostarted = True
            worker.down_until = 0.0
            print(f"[art] worker {worker.label} iniciado automaticamente")
            return worker
        worker.down_until = now + COMFY_PROBE_TTL
    return None


async def _utility_models() -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{UTILITY_URL}/api/ps")
            response.raise_for_status()
            return [
                m["name"] for m in response.json().get("models", []) if m.get("name")
            ]
    except Exception:
        return []


async def _unload_utility(models: list[str]) -> None:
    if not models:
        return
    async with httpx.AsyncClient(timeout=30.0) as client:
        for name in models:
            try:
                await client.post(
                    f"{UTILITY_URL}/api/generate",
                    json={"model": name, "keep_alive": 0},
                )
            except Exception as exc:
                print(f"[art] falha ao descarregar {name}: {exc}")
    started = time.monotonic()
    while time.monotonic() - started < 25:
        if not await _utility_models():
            return
        await asyncio.sleep(1)


async def _warm_utility(models: list[str], num_ctx: int = 8192) -> None:
    if not models:
        return
    async with httpx.AsyncClient(timeout=300.0) as client:
        for name in models:
            try:
                await client.post(
                    f"{UTILITY_URL}/api/generate",
                    json={
                        "model": name,
                        "prompt": "ok",
                        "stream": False,
                        "keep_alive": _env("LUMINUS_KEEP_ALIVE", "30m"),
                        "options": {"num_predict": 1, "num_ctx": num_ctx},
                    },
                )
            except Exception as exc:
                print(f"[art] falha ao recarregar {name}: {exc}")


def _comfy_workflow(prompt: str, seed: int, width: int, height: int) -> dict:
    return {
        "1": {
            "class_type": "UnetLoaderGGUF",
            "inputs": {
                "unet_name": _env(
                    "LUMINUS_COMFY_UNET", "flux1-schnell-Q4_K_S.gguf"
                )
            },
        },
        "2": {
            "class_type": "DualCLIPLoaderGGUF",
            "inputs": {
                "clip_name1": _env(
                    "LUMINUS_COMFY_T5", "t5-v1_1-xxl-encoder-Q5_K_M.gguf"
                ),
                "clip_name2": _env("LUMINUS_COMFY_CLIP", "clip_l.safetensors"),
                "type": "flux",
            },
        },
        "3": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": _env("LUMINUS_COMFY_VAE", "ae.safetensors")},
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["2", 0]},
        },
        "5": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "", "clip": ["2", 0]},
        },
        "6": {
            "class_type": "FluxGuidance",
            "inputs": {
                "conditioning": ["4", 0],
                "guidance": float(_env("LUMINUS_COMFY_GUIDANCE", "3.5")),
            },
        },
        "7": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "8": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "seed": seed,
                "steps": int(_env("LUMINUS_COMFY_STEPS", "4")),
                "cfg": float(_env("LUMINUS_COMFY_CFG", "1.0")),
                "sampler_name": _env("LUMINUS_COMFY_SAMPLER", "euler"),
                "scheduler": _env("LUMINUS_COMFY_SCHEDULER", "simple"),
                "positive": ["6", 0],
                "negative": ["5", 0],
                "latent_image": ["7", 0],
                "denoise": 1.0,
            },
        },
        "9": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["8", 0], "vae": ["3", 0]},
        },
        "10": {
            "class_type": "SaveImage",
            "inputs": {"images": ["9", 0], "filename_prefix": "luminus_scene"},
        },
    }


def _comfy_adult_workflow(
    prompt: str,
    negative: str,
    seed: int,
    width: int,
    height: int,
    photoreal: bool = False,
) -> dict:
    if photoreal:
        checkpoint = _env(
            "LUMINUS_PHOTOREAL_CHECKPOINT",
            "lustifySDXLNSFWSFW_v40.safetensors",
        )
        steps = int(_env("LUMINUS_PHOTOREAL_STEPS", "30"))
        cfg = float(_env("LUMINUS_PHOTOREAL_CFG", "6.0"))
        sampler = _env("LUMINUS_PHOTOREAL_SAMPLER", "dpmpp_2m")
        scheduler = _env("LUMINUS_PHOTOREAL_SCHEDULER", "karras")
        prefix = "luminus_photoreal"
    else:
        checkpoint = _env(
            "LUMINUS_ADULT_CHECKPOINT", "NoobAI-XL-v1.1.safetensors"
        )
        steps = int(_env("LUMINUS_ADULT_STEPS", "28"))
        cfg = float(_env("LUMINUS_ADULT_CFG", "6.0"))
        sampler = _env("LUMINUS_ADULT_SAMPLER", "euler_ancestral")
        scheduler = _env("LUMINUS_ADULT_SCHEDULER", "normal")
        prefix = "luminus_adult"
    vpred = (
        not photoreal
        and _env("LUMINUS_ADULT_VPRED", "0") == "1"
    )
    if vpred:
        sampler = _env("LUMINUS_ADULT_VPRED_SAMPLER", "euler")
        scheduler = _env("LUMINUS_ADULT_VPRED_SCHEDULER", "normal")
        cfg = float(_env("LUMINUS_ADULT_VPRED_CFG", "4.0"))
    model_ref = ["8", 0] if vpred else ["1", 0]
    separate_vae = (
        not photoreal
        and _env("LUMINUS_ADULT_SEPARATE_VAE", "1") == "1"
    )
    vae_ref = ["9", 0] if separate_vae else ["1", 2]
    workflow = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": checkpoint},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["1", 1]},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": ["1", 1]},
        },
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "model": model_ref,
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": sampler,
                "scheduler": scheduler,
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
                "denoise": 1.0,
            },
        },
        "6": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["5", 0], "vae": vae_ref},
        },
        "7": {
            "class_type": "SaveImage",
            "inputs": {
                "images": ["6", 0],
                "filename_prefix": prefix,
            },
        },
    }
    if vpred:
        workflow["8"] = {
            "class_type": "ModelSamplingDiscrete",
            "inputs": {
                "model": ["1", 0],
                "sampling": "v_prediction",
                "zsnr": True,
            },
        }
    if separate_vae:
        workflow["9"] = {
            "class_type": "VAELoader",
            "inputs": {
                "vae_name": _env("LUMINUS_ADULT_VAE", "sdxl_vae.safetensors")
            },
        }
    return workflow


def _postprocess_workflow(
    workflow: dict,
    base_save_id: str,
    image_node: str,
    model_node: str,
    clip_node: str,
    vae_node: str,
    positive_node: str,
    negative_node: str,
    seed: int,
    steps: int,
    cfg: float,
    sampler: str,
    scheduler: str,
    face_detail: bool,
    hand_detail: bool,
    upscale: float,
    style: str,
    prefix: str,
    clip_slot: int = 0,
    vae_slot: int = 0,
) -> tuple[dict, str]:
    """Adiciona FaceDetailer/maos e Ultimate SD Upscale ao workflow base."""
    if not face_detail and not hand_detail and upscale <= 1.01:
        return workflow, base_save_id
    workflow.pop(base_save_id, None)
    current = image_node

    def detailer(node_id, detector_id, detector_model, denoise, threshold):
        workflow[node_id] = {
            "class_type": "FaceDetailer",
            "inputs": {
                "image": [current, 0],
                "model": [model_node, 0],
                "clip": [clip_node, clip_slot],
                "vae": [vae_node, vae_slot],
                "guide_size": 512.0,
                "guide_size_for": True,
                "max_size": 1024.0,
                "seed": seed,
                "steps": max(4, int(steps)),
                "cfg": cfg,
                "sampler_name": sampler,
                "scheduler": scheduler,
                "positive": [positive_node, 0],
                "negative": [negative_node, 0],
                "denoise": denoise,
                "feather": 5,
                "noise_mask": True,
                "force_inpaint": True,
                "bbox_threshold": threshold,
                "bbox_dilation": 10,
                "bbox_crop_factor": 3.0,
                "sam_detection_hint": "center-1",
                "sam_dilation": 0,
                "sam_threshold": 0.93,
                "sam_bbox_expansion": 0,
                "sam_mask_hint_threshold": 0.7,
                "sam_mask_hint_use_negative": "False",
                "drop_size": 10,
                "bbox_detector": [detector_id, 0],
                "wildcard": "",
                "cycle": 1,
            },
        }
        if USE_FACE_SAM:
            workflow[node_id]["inputs"]["sam_model_opt"] = ["21", 0]

    if face_detail or hand_detail:
        workflow["20"] = {
            "class_type": "UltralyticsDetectorProvider",
            "inputs": {"model_name": FACE_MODEL},
        }
        if USE_FACE_SAM:
            workflow["21"] = {
                "class_type": "SAMLoader",
                "inputs": {"model_name": SAM_MODEL, "device_mode": "AUTO"},
            }
    if face_detail:
        detailer("22", "20", FACE_MODEL, 0.45, 0.5)
        current = "22"
    if hand_detail:
        workflow["23"] = {
            "class_type": "UltralyticsDetectorProvider",
            "inputs": {"model_name": HAND_MODEL},
        }
        detailer("24", "23", HAND_MODEL, 0.4, 0.35)
        current = "24"

    if upscale > 1.01:
        upscaler = (
            ANIME_UPSCALER
            if (style or "").strip().lower() in ("anime", "aquarela", "pintura")
            else PHOTO_UPSCALER
        )
        workflow["30"] = {
            "class_type": "UpscaleModelLoader",
            "inputs": {"model_name": upscaler},
        }
        workflow["31"] = {
            "class_type": "UltimateSDUpscale",
            "inputs": {
                "image": [current, 0],
                "model": [model_node, 0],
                "positive": [positive_node, 0],
                "negative": [negative_node, 0],
                "vae": [vae_node, vae_slot],
                "upscale_by": max(1.01, min(2.0, float(upscale))),
                "seed": seed,
                "steps": max(8, min(20, int(steps))),
                "cfg": cfg,
                "sampler_name": sampler,
                "scheduler": scheduler,
                "denoise": float(_env("LUMINUS_UPSCALE_DENOISE", "0.2")),
                "upscale_model": ["30", 0],
                "mode_type": "Linear",
                "tile_width": 512,
                "tile_height": 512,
                "mask_blur": 8,
                "tile_padding": 32,
                "seam_fix_mode": "None",
                "seam_fix_denoise": 1.0,
                "seam_fix_width": 64,
                "seam_fix_mask_blur": 8,
                "seam_fix_padding": 32,
                "force_uniform_tiles": True,
                "tiled_decode": False,
                "batch_size": 1,
            },
        }
        current = "31"

    workflow["40"] = {
        "class_type": "SaveImage",
        "inputs": {"images": [current, 0], "filename_prefix": prefix},
    }
    return workflow, "40"


async def _comfy_generate(
    url: str,
    prompt: str,
    seed: int,
    width: int,
    height: int,
    adult: bool = False,
    photoreal: bool = False,
    face_detail: bool = False,
    hand_detail: bool = False,
    upscale: float = 1.0,
    style: str = "anime",
) -> bytes:
    if adult:
        if photoreal:
            negative = _env(
                "LUMINUS_PHOTOREAL_NEGATIVE",
                "worst quality, low quality, lowres, bad anatomy, bad hands, "
                "extra fingers, extra limbs, deformed, blurry, text, watermark, "
                "logo, jpeg artifacts, cartoon, anime, painting, illustration, "
                "3d render",
            )
            steps = int(_env("LUMINUS_PHOTOREAL_STEPS", "30"))
            cfg = float(_env("LUMINUS_PHOTOREAL_CFG", "6.0"))
            sampler = _env("LUMINUS_PHOTOREAL_SAMPLER", "dpmpp_2m")
            scheduler = _env("LUMINUS_PHOTOREAL_SCHEDULER", "karras")
            prefix = "luminus_photoreal"
        else:
            negative = _env(
                "LUMINUS_ADULT_NEGATIVE",
                "worst quality, low quality, lowres, bad anatomy, bad hands, "
                "extra fingers, extra limbs, deformed, blurry, text, watermark, "
                "logo, jpeg artifacts",
            )
            steps = int(_env("LUMINUS_ADULT_STEPS", "28"))
            cfg = float(_env("LUMINUS_ADULT_CFG", "6.0"))
            sampler = _env("LUMINUS_ADULT_SAMPLER", "euler_ancestral")
            scheduler = _env("LUMINUS_ADULT_SCHEDULER", "normal")
            prefix = "luminus_adult"
        workflow = _comfy_adult_workflow(
            prompt,
            negative,
            seed,
            width,
            height,
            photoreal=photoreal,
        )
        model_node = "8" if "8" in workflow else "1"
        separate_vae = (
            not photoreal
            and _env("LUMINUS_ADULT_SEPARATE_VAE", "1") == "1"
        )
        vae_node = "9" if separate_vae else "1"
        workflow, output_id = _postprocess_workflow(
            workflow,
            "7",
            "6",
            model_node,
            "1",
            vae_node,
            "2",
            "3",
            seed,
            steps,
            cfg,
            sampler,
            scheduler,
            face_detail,
            hand_detail,
            upscale,
            style,
            prefix,
            clip_slot=1,
            vae_slot=0 if separate_vae else 2,
        )
    else:
        workflow = _comfy_workflow(prompt, seed, width, height)
        steps = int(_env("LUMINUS_COMFY_STEPS", "4"))
        cfg = float(_env("LUMINUS_COMFY_CFG", "1.0"))
        sampler = _env("LUMINUS_COMFY_SAMPLER", "euler")
        scheduler = _env("LUMINUS_COMFY_SCHEDULER", "simple")
        workflow, output_id = _postprocess_workflow(
            workflow,
            "10",
            "9",
            "1",
            "2",
            "3",
            "6",
            "5",
            seed,
            steps,
            cfg,
            sampler,
            scheduler,
            face_detail,
            hand_detail,
            upscale,
            style,
            "luminus_scene",
        )
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{url}/prompt",
            json={"prompt": workflow},
        )
        if response.status_code >= 300:
            raise ArtError(502, f"ComfyUI recusou o trabalho: {response.text[:300]}")
        prompt_id = response.json().get("prompt_id")
        if not prompt_id:
            raise ArtError(502, "ComfyUI não retornou prompt_id.")
        started = time.monotonic()
        while time.monotonic() - started < 600:
            history = (await client.get(f"{url}/history/{prompt_id}")).json()
            item = history.get(prompt_id)
            if item:
                status = item.get("status") or {}
                if status.get("status_str") == "error":
                    raise ArtError(502, "ComfyUI reportou erro na geração.")
                images = ((item.get("outputs") or {}).get(output_id) or {}).get(
                    "images"
                ) or []
                if images:
                    image = images[0]
                    view = await client.get(
                        f"{url}/view",
                        params={
                            "filename": image.get("filename", ""),
                            "subfolder": image.get("subfolder", ""),
                            "type": image.get("type", "output"),
                        },
                    )
                    if view.status_code >= 300:
                        raise ArtError(
                            502, f"ComfyUI falhou ao entregar a imagem ({view.status_code})."
                        )
                    return view.content
            await asyncio.sleep(2)
    raise ArtError(504, "Tempo esgotado ao gerar a imagem no ComfyUI.")


async def _render_primary(
    prompt: str,
    seed: int,
    width: int,
    height: int,
    reload_models: list[str] | None,
    **kwargs,
) -> bytes:
    """Caminho da GPU2: descarrega a utilitaria, sobe/para o ComfyUI sozinho."""
    async with GPU_LOCK:
        loaded = await _utility_models()
        try:
            await _unload_utility(loaded)
            if not await _comfy_up(COMFY_URL):
                used = await _gpu_memory_used(COMFY_GPU)
                for _ in range(10):
                    if used <= 2500:
                        break
                    await asyncio.sleep(1)
                    used = await _gpu_memory_used(COMFY_GPU)
                if used > 2500:
                    raise ArtError(
                        409,
                        f"A GPU {COMFY_GPU} esta ocupada (provavelmente pelo "
                        "narrador). Aplique a configuracao de GPUs ou pare o "
                        "27B antes de gerar imagens.",
                    )
                await _comfy_service("start", COMFY_SERVICE)
                if not await _comfy_wait_up(COMFY_URL, 60):
                    raise ArtError(
                        502, f"O ComfyUI não iniciou na GPU {COMFY_GPU}."
                    )
            return await _comfy_generate(
                COMFY_URL, prompt, seed, width, height, **kwargs
            )
        finally:
            if await _comfy_up(COMFY_URL):
                await _comfy_service("stop", COMFY_SERVICE)
                await _comfy_wait_down(COMFY_URL, 25)
            await asyncio.sleep(2)
            await _warm_utility(reload_models or [])


async def _render_worker(worker: ComfyWorker, prompt: str, seed: int,
                         width: int, height: int, **kwargs) -> bytes:
    async with worker.lock:
        try:
            return await _comfy_generate(
                worker.url, prompt, seed, width, height, **kwargs
            )
        finally:
            if worker.autostarted or COMFY_FREE_AFTER:
                await _comfy_free(worker.url)


async def _render_comfy(
    prompt: str,
    seed: int,
    width: int,
    height: int,
    reload_models: list[str] | None,
    adult: bool = False,
    photoreal: bool = False,
    face_detail: bool = False,
    hand_detail: bool = False,
    upscale: float = 1.0,
    style: str = "anime",
) -> bytes:
    options = dict(
        adult=adult,
        photoreal=photoreal,
        face_detail=face_detail,
        hand_detail=hand_detail,
        upscale=upscale,
        style=style,
    )
    tried: set[str] = set()
    last_error: ArtError | None = None
    for _ in range(COMFY_ATTEMPTS):
        worker = await _pick_worker(tried)
        if worker is None:
            worker = await _autostart_worker(tried)
        if worker is None:
            break
        tried.add(worker.url)
        try:
            if worker.primary:
                return await _render_primary(
                    prompt, seed, width, height, reload_models, **options
                )
            print(f"[art] gerando em {worker.label}")
            return await _render_worker(
                worker, prompt, seed, width, height, **options
            )
        except ArtError as exc:
            last_error = exc
            print(f"[art] {worker.label} falhou ({exc.status}): {exc.message}")
            if exc.status not in (409, 502, 503, 504):
                raise
        except httpx.HTTPError as exc:
            last_error = ArtError(502, f"ComfyUI {worker.label} falhou: {exc}")
            print(f"[art] {worker.label} falhou: {exc}")
        finally:
            worker.busy = False
    if last_error is not None and _WORKERS and _WORKERS[0].url in tried:
        raise last_error
    return await _render_primary(
        prompt, seed, width, height, reload_models, **options
    )


async def _render(
    prompt: str,
    seed: int,
    width: int | None = None,
    height: int | None = None,
    reload_models: list[str] | None = None,
    adult: bool = False,
    photoreal: bool = False,
    face_detail: bool = False,
    hand_detail: bool = False,
    upscale: float = 1.0,
    style: str = "anime",
) -> bytes:
    if _env("LUMINUS_ART_PROVIDER", "pollinations").lower() == "comfyui":
        return await _render_comfy(
            prompt,
            seed,
            width or int(_env("LUMINUS_SCENE_WIDTH", "1280")),
            height or int(_env("LUMINUS_SCENE_HEIGHT", "720")),
            reload_models,
            adult=adult,
            photoreal=photoreal,
            face_detail=face_detail,
            hand_detail=hand_detail,
            upscale=upscale,
            style=style,
        )
    return await _render_pollinations(prompt, seed, width, height)


def _save(
    data: bytes, prefix: str = "cover", crop: bool = True
) -> tuple[str, int, int]:
    ensure_dir()
    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as exc:
        raise ArtError(502, f"O gerador retornou uma imagem inválida: {exc}")
    width, height = image.size
    # O Pollinations adiciona marca d'água no rodapé; o ComfyUI não.
    if crop:
        image = image.crop((0, 0, width, int(height * 0.92)))
    name = f"{prefix}-{uuid.uuid4().hex[:12]}.jpg"
    path = os.path.join(ART_DIR, name)
    image.save(path, "JPEG", quality=90, optimize=True)
    return f"/art/{name}", image.width, image.height


async def generate_cover(
    body: dict, reload_models: list[str] | None = None
) -> dict:
    ensure_dir()
    identity = str(body.get("visual_prompt") or "").strip()
    expression = str(body.get("expression") or "").strip()
    if identity:
        prompt = _identity_cover_prompt(
            identity,
            expression,
            str(body.get("style") or "anime"),
        )
        source = "identity"
        warning = ""
    else:
        prompt, source, warning = await _build_prompt(body)
        if expression:
            prompt = (
                prompt.rstrip(" .,")
                + f", {expression} expression, portrait framing"
            )
    adult = bool(body.get("adult"))
    photoreal = bool(body.get("photoreal"))
    if adult:
        prompt = _quality_prefix(prompt, photoreal=photoreal)
    _adult_guard(prompt)
    seed = body.get("seed")
    seed = int(seed) if seed else random.randint(1, 2_147_483_647)
    width = int(body.get("width") or _env("LUMINUS_ART_WIDTH", "768"))
    height = int(body.get("height") or _env("LUMINUS_ART_HEIGHT", "1024"))
    width = max(256, min(1536, width))
    height = max(256, min(1536, height))
    width -= width % 16
    height -= height % 16
    face_detail = bool(body.get("face_detail", _env("LUMINUS_FACE_DETAIL", "1") == "1"))
    hand_detail = bool(body.get("hand_detail", _env("LUMINUS_HAND_DETAIL", "0") == "1"))
    try:
        upscale = float(body.get("upscale") or _env("LUMINUS_UPSCALE", "1.5"))
    except (ValueError, TypeError):
        upscale = 1.0
    data = await _render(
        prompt,
        seed,
        width,
        height,
        reload_models=reload_models,
        adult=adult,
        photoreal=photoreal,
        face_detail=face_detail,
        hand_detail=hand_detail,
        upscale=upscale,
        style=body.get("style") or "anime",
    )
    crop = _env("LUMINUS_ART_PROVIDER", "pollinations").lower() != "comfyui"
    url, width, height = _save(data, prefix="cover", crop=crop)
    return {
        "url": url,
        "prompt": prompt,
        "prompt_source": source,
        "identity_used": bool(identity),
        "warning": warning,
        "seed": seed,
        "width": width,
        "height": height,
        "adult": adult,
        "photoreal": photoreal,
        "face_detail": face_detail,
        "hand_detail": hand_detail,
        "upscale": upscale,
    }


async def generate_free(
    body: dict, reload_models: list[str] | None = None
) -> dict:
    ensure_dir()
    text = (body.get("prompt") or "").strip()
    if not text:
        raise ArtError(400, "Descreva a imagem que voce quer.")
    adult = bool(body.get("adult"))
    photoreal = adult and (body.get("model") or "").strip().lower() in (
        "photoreal",
        "photo",
        "realistic",
        "realista",
    )
    _adult_guard(text)
    prompt = _local_free_prompt(text, body, photoreal=photoreal)
    if adult:
        prompt = _quality_prefix(prompt, photoreal=photoreal)
    source = "local"
    warning = ""
    if body.get("enhance", True):
        try:
            if deepseek_configured():
                prompt = await _deepseek_free_prompt(
                    text, body, adult=adult, photoreal=photoreal
                )
                source = "deepseek"
            elif _env("LUMINUS_ART_PROMPT_LLM", "1") == "1":
                prompt = await _local_llm_free_prompt(
                    text, body, adult=adult, photoreal=photoreal
                )
                source = "local-llm"
            if adult:
                prompt = _quality_prefix(prompt, photoreal=photoreal)
        except Exception as exc:
            print("[art] prompt free falhou:", exc)
            warning = "LLM de prompt indisponivel; usei seu texto direto."
    _adult_guard(prompt)
    seed = body.get("seed")
    seed = int(seed) if seed else random.randint(1, 2_147_483_647)
    width = int(body.get("width") or 1024)
    height = int(body.get("height") or 1024)
    width = max(256, min(1536, width))
    height = max(256, min(1536, height))
    width -= width % 16
    height -= height % 16
    face_detail = bool(body.get("face_detail", _env("LUMINUS_FACE_DETAIL", "1") == "1"))
    hand_detail = bool(body.get("hand_detail", _env("LUMINUS_HAND_DETAIL", "0") == "1"))
    try:
        upscale = float(body.get("upscale") or _env("LUMINUS_UPSCALE", "1.5"))
    except (ValueError, TypeError):
        upscale = 1.0
    data = await _render(
        prompt,
        seed,
        width,
        height,
        reload_models=reload_models,
        adult=adult,
        photoreal=photoreal,
        face_detail=face_detail,
        hand_detail=hand_detail,
        upscale=upscale,
        style=body.get("style") or "anime",
    )
    crop = _env("LUMINUS_ART_PROVIDER", "pollinations").lower() != "comfyui"
    url, width, height = _save(data, prefix="free", crop=crop)
    return {
        "url": url,
        "prompt": prompt,
        "prompt_source": source,
        "warning": warning,
        "seed": seed,
        "width": width,
        "height": height,
        "adult": adult,
        "photoreal": photoreal,
        "face_detail": face_detail,
        "hand_detail": hand_detail,
        "upscale": upscale,
    }


def gallery(limit: int = 40) -> list[dict]:
    ensure_dir()
    items: list[dict] = []
    for name in os.listdir(ART_DIR):
        if not name.lower().endswith((".jpg", ".jpeg", ".png")):
            continue
        path = os.path.join(ART_DIR, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        items.append(
            {
                "name": name,
                "url": f"/art/{name}",
                "created_at": stat.st_mtime,
                "size": stat.st_size,
            }
        )
    items.sort(key=lambda item: item["created_at"], reverse=True)
    return items[: max(1, min(200, limit))]


async def generate_scene(
    body: dict, reload_models: list[str] | None = None
) -> dict:
    ensure_dir()
    prompt, source, warning = await _build_prompt(body, scene=True)
    _adult_guard(prompt)
    seed = body.get("seed")
    seed = int(seed) if seed else random.randint(1, 2_147_483_647)
    width = int(body.get("width") or _env("LUMINUS_SCENE_WIDTH", "1280"))
    height = int(body.get("height") or _env("LUMINUS_SCENE_HEIGHT", "720"))
    face_detail = bool(body.get("face_detail", False))
    hand_detail = bool(body.get("hand_detail", False))
    try:
        upscale = float(body.get("upscale") or 1.0)
    except (ValueError, TypeError):
        upscale = 1.0
    data = await _render(
        prompt, seed, width, height, reload_models=reload_models,
        face_detail=face_detail, hand_detail=hand_detail,
        upscale=upscale, style=body.get("style") or "anime",
    )
    crop = _env("LUMINUS_ART_PROVIDER", "pollinations").lower() != "comfyui"
    url, width, height = _save(data, prefix="scene", crop=crop)
    return {
        "url": url,
        "prompt": prompt,
        "prompt_source": source,
        "warning": warning,
        "seed": seed,
        "width": width,
        "height": height,
        "face_detail": face_detail,
        "hand_detail": hand_detail,
        "upscale": upscale,
    }
