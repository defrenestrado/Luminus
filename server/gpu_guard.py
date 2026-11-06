"""Vigia de GPUs e seleção automática do modelo do Narrador.

Objetivo
--------
O Narrador do Luminus (``luminus-27b``) é distribuído entre as três RTX 3060 Ti
(``CUDA_VISIBLE_DEVICES=0,1,3`` no serviço ``ollama``). Se uma dessas placas
desaparecer do barramento PCIe, apresentar ``Unknown Error``/``ERR!`` ou falhar
com erro de CUDA durante o carregamento/inferência, o modelo de 27B deixa de
caber na VRAM restante e o app fica sem narração.

Este módulo:

1. consulta o ``nvidia-smi`` periodicamente e classifica cada GPU como saudável
   ou com defeito;
2. conta quantas GPUs principais (RTX 3060 Ti, por padrão) continuam saudáveis;
3. escolhe um modelo que caiba nessa capacidade:

   ===========  ==========================================================
   GPUs sadias  modelo
   ===========  ==========================================================
   3            27B (modelo principal)
   2            ``LUMINUS_MODEL_2GPU`` (9B Q8 por padrão — cabe em 2x 8GB)
   1            ``LUMINUS_MODEL_1GPU`` (9B Q5 por padrão)
   0            modelo utilitário na RTX 2060 ou, sem ela, CPU
   ===========  ==========================================================

4. aplica histerese (2 leituras ruins para degradar, 3 boas para recuperar)
   para não trocar de modelo por um soluço momentâneo do driver;
5. guarda por alguns minutos a última falha de modelo (erro de CUDA no load ou
   na geração), evitando tentar carregar o 27B repetidamente em uma GPU ruim.

O módulo não depende de FastAPI nem do cliente Ollama. Quem usa registra um
callback ``on_change`` (assíncrono ou não) para efetivar a troca de modelo.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import shutil
import subprocess
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


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


# Marcadores de erro de GPU/CUDA vistos nos logs do Ollama/llama.cpp e nas
# respostas HTTP. Usado tanto pelo vigia quanto pelo cliente LLM (via import).
GPU_ERROR_MARKERS = (
    "cuda",
    "cudnn",
    "cublas",
    "cudart",
    "nvml",
    "nvidia",
    "ggml",
    "gpu discovery",
    "device-side assert",
    "unspecified launch failure",
    "invalid device ordinal",
    "no cuda-capable device",
    "no cuda-capable device is detected",
    "out of memory",
    "oom",
    "cudamalloc",
    "device lost",
    "fallen off the bus",
    "gpu is lost",
    "failed to initialize cuda",
    "unable to find a compatible gpu",
    "error initializing cuda",
)


def is_gpu_error(text: str) -> bool:
    """True quando o texto contém um sintoma de falha de GPU/CUDA."""
    low = (text or "").lower()
    return any(marker in low for marker in GPU_ERROR_MARKERS)


# Sintomas que normalmente exigem reiniciar o processo do Ollama/llama-server:
# o contexto CUDA fica envenenado e novos runners falham com "failed to
# initialize CUDA: unknown error" até o serviço reiniciar.
FATAL_CUDA_MARKERS = (
    "unspecified launch failure",
    "device-side assert",
    "failed to initialize cuda",
    "llama-server terminated",
    "cuda error",
    "cudamalloc failed",
    "unknown error",
)


def is_fatal_cuda_error(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in FATAL_CUDA_MARKERS)


def request_service_restart(
    service: str = "ollama",
    reason: str = "",
    request_dir: Optional[str] = None,
) -> bool:
    """Pede ao helper root (systemd timer) que reinicie um serviço Ollama.

    O servidor roda como usuário comum e não pode `systemctl restart`. A ponte
    é um arquivo em um diretório gravável pelo app, criado pelo
    scripts/install_gpu_guard.sh. Se o helper não estiver instalado, apenas
    registramos o motivo e seguimos com o failover de modelo.
    """
    directory = Path(
        request_dir
        or os.environ.get("LUMINUS_GPU_RESTART_DIR", "/var/lib/luminus-gpu-guard")
    )
    if not directory.is_dir():
        print(
            f"[gpu-guard] reinício de {service} necessário, mas o helper root "
            f"não está instalado ({directory}). Rode: "
            "sudo bash scripts/install_gpu_guard.sh",
            flush=True,
        )
        return False
    safe_service = re.sub(r"[^a-zA-Z0-9_.-]", "_", service or "ollama")
    target = directory / f"restart-{safe_service}.request"
    payload = f"{int(time.time())}\n{safe_service}\n{(reason or '')[:400]}\n"
    try:
        tmp = target.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, target)
        print(
            f"[gpu-guard] reinício de {service} solicitado ao helper root: "
            f"{(reason or '')[:160]}",
            flush=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[gpu-guard] não consegui pedir reinício de {service}: {exc}", flush=True)
        return False


# ─── Leitura do hardware ─────────────────────────────────────────────────────

_NVIDIA_QUERY = (
    "index,uuid,pci.bus_id,name,memory.total,memory.free"
)


@dataclass
class GpuDevice:
    index: int
    uuid: str
    pci_bus: str
    name: str
    total_mib: int
    free_mib: int
    healthy: bool
    error: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "uuid": self.uuid,
            "pci_bus": self.pci_bus,
            "name": self.name,
            "total_mib": self.total_mib,
            "free_mib": self.free_mib,
            "healthy": self.healthy,
            "error": self.error,
        }


def _parse_int(value: str) -> Optional[int]:
    raw = (value or "").strip()
    if not raw or raw.upper() in ("N/A", "[N/A]", "NA", "-"):
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def probe_gpus(timeout: float = 6.0) -> Tuple[List[GpuDevice], str]:
    """Executa o nvidia-smi e devolve (dispositivos, erro_de_leitura).

    Um dispositivo é considerado com erro quando o nvidia-smi reporta VRAM
    total inválida/zero ou marcadores como ``Unknown Error``/``ERR!``. GPUs
    ausentes simplesmente não aparecem na lista.

    Quando o comando falha por completo, devolvemos lista vazia e o motivo;
    o chamador decide se mantém o modelo principal (evita pânico em falha
    transitória do próprio nvidia-smi).
    """
    exe = shutil.which("nvidia-smi") or "nvidia-smi"
    cmd = [
        exe,
        f"--query-gpu={_NVIDIA_QUERY}",
        "--format=csv,noheader,nounits",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=max(1.0, timeout)
        )
    except FileNotFoundError:
        return [], "nvidia-smi não encontrado"
    except subprocess.TimeoutExpired:
        return [], f"nvidia-smi não respondeu em {timeout:.0f}s"
    except Exception as exc:  # noqa: BLE001 - nunca derrubar o servidor
        return [], f"falha ao executar nvidia-smi: {exc}"

    devices: List[GpuDevice] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 6:
            continue
        index = _parse_int(parts[0])
        total = _parse_int(parts[4])
        free = _parse_int(parts[5])
        name = parts[3]
        blob = " ".join(parts).lower()
        error = ""
        if index is None:
            error = "índice inválido"
        elif total is None or total <= 0:
            error = "VRAM total inválida/zero"
        for marker in ("unknown error", "err!", "[n/a]", "not available"):
            if marker in blob:
                marker_error = f"nvidia-smi reportou {marker}"
                error = f"{error}; {marker_error}" if error else marker_error
                break
        devices.append(
            GpuDevice(
                index=index if index is not None else -1,
                uuid=parts[1],
                pci_bus=parts[2],
                name=name,
                total_mib=total or 0,
                free_mib=free or 0,
                healthy=not error,
                error=error,
            )
        )

    stderr = (proc.stderr or "").strip().splitlines()
    note = stderr[-1] if stderr else ""
    if proc.returncode != 0:
        if not devices:
            return [], note or f"nvidia-smi retornou código {proc.returncode}"
        note = note or f"nvidia-smi retornou código {proc.returncode}"
    return devices, note


# ─── Avaliação e escolha do modelo ───────────────────────────────────────────

TIER_RANK = {
    "cpu": 0,
    "utility": 1,
    "one_gpu": 2,
    "two_gpu": 3,
    "full": 4,
}


@dataclass
class GpuAssessment:
    tier: str
    model: str
    num_ctx: int
    reason: str
    healthy: bool
    degraded: bool
    devices: List[GpuDevice] = field(default_factory=list)
    main_healthy: int = 0
    main_expected: int = 0
    utility_healthy: bool = False
    utility_expected: int = 0
    checked_at: float = 0.0
    override: bool = False
    probe_error: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "model": self.model,
            "num_ctx": self.num_ctx,
            "reason": self.reason,
            "healthy": self.healthy,
            "degraded": self.degraded,
            "main_healthy": self.main_healthy,
            "main_expected": self.main_expected,
            "utility_healthy": self.utility_healthy,
            "utility_expected": self.utility_expected,
            "checked_at": self.checked_at,
            "override": self.override,
            "probe_error": self.probe_error,
            "devices": [device.as_dict() for device in self.devices],
        }


def _same(a: str, b: str) -> bool:
    return (a or "").split(":")[0].lower() == (b or "").split(":")[0].lower()


class GpuGuard:
    """Monitora as GPUs e mantém o tier/modelo efetivo do Narrador."""

    def __init__(
        self,
        on_change: Optional[Callable[[GpuAssessment], Any]] = None,
        probe: Optional[Callable[[], Tuple[List[GpuDevice], str]]] = None,
    ) -> None:
        self.on_change = on_change
        self._probe = probe or probe_gpus

        # Hardware esperado (por nome, não por índice: índices mudam quando uma
        # placa cai e o driver reenumera o barramento).
        self.main_match = os.environ.get("LUMINUS_GPU_MAIN_MATCH", "3060 Ti")
        self.utility_match = os.environ.get("LUMINUS_GPU_UTILITY_MATCH", "2060")
        self.main_expected = _env_int(
            "LUMINUS_EXPECTED_MAIN_GPUS",
            _env_int("LUMINUS_REQUIRED_GPUS", 3),
        )
        self.utility_expected = _env_int("LUMINUS_EXPECTED_UTILITY_GPUS", 1)
        self.primary_min_gpus = _env_int(
            "LUMINUS_PRIMARY_MIN_GPUS",
            max(1, self.main_expected),
        )

        # Modelos por capacidade.
        self.full_model = os.environ.get(
            "LUMINUS_MODEL_FULL",
            os.environ.get("LUMINUS_MODEL", "luminus-27b"),
        )
        self.two_gpu_model = os.environ.get(
            "LUMINUS_MODEL_2GPU",
            "qwen38-9b-heretic:latest",
        )
        self.one_gpu_model = os.environ.get(
            "LUMINUS_MODEL_1GPU",
            "qwen38-9b-heretic-q5",
        )
        self.utility_model = os.environ.get(
            "LUMINUS_MODEL_UTILITY",
            os.environ.get("LUMINUS_UTILITY_MODEL", "qwen38-9b-heretic-q5"),
        )
        self.cpu_model = os.environ.get(
            "LUMINUS_MODEL_CPU", self.one_gpu_model
        )

        self.full_num_ctx = _env_int("LUMINUS_NUM_CTX", 32768)
        self.two_gpu_num_ctx = _env_int("LUMINUS_CTX_2GPU", 16384)
        self.one_gpu_num_ctx = _env_int("LUMINUS_CTX_1GPU", 8192)
        self.utility_num_ctx = _env_int("LUMINUS_CTX_UTILITY", 8192)
        self.cpu_num_ctx = _env_int("LUMINUS_CTX_CPU", 8192)

        # Ritmo e histerese.
        self.poll_seconds = _env_float("LUMINUS_GPU_POLL_SECONDS", 20.0)
        self.failure_threshold = max(
            1, _env_int("LUMINUS_GPU_FAILURE_THRESHOLD", 2)
        )
        self.recovery_threshold = max(
            1, _env_int("LUMINUS_GPU_RECOVERY_THRESHOLD", 3)
        )
        self.failure_cooldown = _env_float(
            "LUMINUS_GPU_FAILURE_COOLDOWN", 900.0
        )

        self._committed: Optional[GpuAssessment] = None
        self._observed: Optional[GpuAssessment] = None
        self._bad_streak = 0
        self._good_streak = 0
        self._override_tier = ""
        self._override_until = 0.0
        self._failure_reason = ""
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None

    # ── propriedades usadas pelo servidor ────────────────────────────────

    @property
    def tier(self) -> str:
        if self._committed is None:
            return "full"
        return self._committed.tier

    @property
    def model(self) -> str:
        if self._committed is None:
            return self.full_model
        return self._committed.model

    @property
    def num_ctx(self) -> int:
        if self._committed is None:
            return self.full_num_ctx
        return self._committed.num_ctx

    @property
    def degraded(self) -> bool:
        return self._committed is not None and self._committed.degraded

    @property
    def main_healthy(self) -> int:
        if self._committed is None:
            return self.main_expected
        return self._committed.main_healthy

    @property
    def utility_healthy(self) -> bool:
        if self._committed is None:
            return True
        return self._committed.utility_healthy

    # ── construção da avaliação ──────────────────────────────────────────

    def _model_for(self, tier: str) -> Tuple[str, int]:
        if tier == "full":
            return self.full_model, self.full_num_ctx
        if tier == "two_gpu":
            return self.two_gpu_model, self.two_gpu_num_ctx
        if tier == "one_gpu":
            return self.one_gpu_model, self.one_gpu_num_ctx
        if tier == "utility":
            return self.utility_model, self.utility_num_ctx
        return self.cpu_model, self.cpu_num_ctx

    def _build(
        self,
        devices: List[GpuDevice],
        probe_error: str = "",
        override_tier: str = "",
        override_reason: str = "",
    ) -> GpuAssessment:
        main_devices = [
            device for device in devices if self.main_match.lower() in device.name.lower()
        ]
        utility_devices = [
            device
            for device in devices
            if self.utility_match.lower() in device.name.lower()
        ]
        main_healthy_devices = [device for device in main_devices if device.healthy]
        utility_ok = any(device.healthy for device in utility_devices)

        main_ok = len(main_healthy_devices)
        if main_ok >= self.primary_min_gpus:
            tier = "full"
        elif main_ok >= 2:
            tier = "two_gpu"
        elif main_ok >= 1:
            tier = "one_gpu"
        elif self.utility_expected > 0 and utility_ok:
            tier = "utility"
        else:
            tier = "cpu"

        if override_tier and TIER_RANK[override_tier] < TIER_RANK[tier]:
            tier = override_tier

        model, num_ctx = self._model_for(tier)

        healthy = (
            main_ok >= self.main_expected
            and (self.utility_expected <= 0 or utility_ok)
        )
        degraded = not healthy

        reasons: List[str] = []
        if probe_error:
            reasons.append(f"nvidia-smi: {probe_error}")
        missing = max(0, self.main_expected - main_ok)
        if missing:
            reasons.append(
                f"{main_ok}/{self.main_expected} GPUs principais saudáveis "
                f"({missing} ausente(s)/com erro)"
            )
        if self.utility_expected > 0 and not utility_ok:
            reasons.append("GPU utilitária (RTX 2060) ausente/com erro")
        for device in devices:
            if not device.healthy:
                reasons.append(
                    f"GPU {device.index} {device.name}: {device.error or 'erro'}"
                )
        if override_tier:
            if override_reason:
                reasons.append(override_reason)
            else:
                reasons.append(
                    f"modelo anterior falhou; mantendo {tier} por segurança"
                )
        if not reasons:
            reasons.append("hardware saudável")

        return GpuAssessment(
            tier=tier,
            model=model,
            num_ctx=num_ctx,
            reason="; ".join(reasons),
            healthy=healthy,
            degraded=degraded,
            devices=list(devices),
            main_healthy=main_ok,
            main_expected=self.main_expected,
            utility_healthy=utility_ok,
            utility_expected=self.utility_expected,
            checked_at=time.time(),
            override=bool(override_tier),
            probe_error=probe_error,
        )

    # ── avaliação com histerese ──────────────────────────────────────────

    async def evaluate(self, force: bool = False) -> GpuAssessment:
        """Lê o hardware e devolve a avaliação efetiva (com histerese).

        ``force=True`` ignora a histerese e efetiva imediatamente (usado no
        boot, no warmup e quando uma chamada LLM falha com erro de GPU).
        """
        async with self._lock:
            try:
                devices, probe_error = await asyncio.to_thread(self._probe)
            except Exception as exc:  # noqa: BLE001
                devices, probe_error = [], f"probe falhou: {exc}"

            now = time.monotonic()
            active_override = ""
            if self._override_tier and now < self._override_until:
                active_override = self._override_tier
            elif self._override_tier:
                # Cooldown vencido: permite tentar o modelo maior de novo.
                self._override_tier = ""
                self._failure_reason = ""

            if not devices and probe_error:
                # Sem qualquer leitura não dá para afirmar que as GPUs caíram.
                # Mantém o modelo principal e deixa o failover por requisição
                # agir se o carregamento realmente falhar.
                assessment = self._build(
                    [],
                    probe_error=probe_error,
                    override_tier="",
                )
                assessment.tier = "full"
                assessment.model, assessment.num_ctx = self._model_for("full")
                assessment.reason = (
                    "nvidia-smi indisponível; mantendo modelo principal "
                    "e failover por erro de requisição"
                )
                assessment.healthy = False
                assessment.degraded = True
                # Otimista: não desliga o pool principal por uma leitura
                # falha do nvidia-smi. Se o modelo realmente não carregar, o
                # warmup/LLM reporta a falha e degrada de verdade.
                assessment.main_healthy = self.main_expected
                assessment.utility_healthy = True
            else:
                assessment = self._build(
                    devices,
                    probe_error=probe_error,
                    override_tier=active_override,
                    override_reason=(
                        f"última falha: {self._failure_reason}"
                        if active_override and self._failure_reason
                        else ""
                    ),
                )

            self._observed = assessment

            previous = self._committed
            commit = assessment

            if previous is not None and not force:
                prev_rank = TIER_RANK[previous.tier]
                new_rank = TIER_RANK[assessment.tier]
                if new_rank < prev_rank:
                    self._bad_streak += 1
                    self._good_streak = 0
                    if self._bad_streak < self.failure_threshold:
                        commit = previous
                elif new_rank > prev_rank:
                    self._good_streak += 1
                    self._bad_streak = 0
                    if self._good_streak < self.recovery_threshold:
                        commit = previous
                else:
                    self._bad_streak = 0
                    self._good_streak = 0

            changed = previous is None or (
                previous.tier != commit.tier
                or not _same(previous.model, commit.model)
                or previous.num_ctx != commit.num_ctx
                or previous.utility_healthy != commit.utility_healthy
                or previous.degraded != commit.degraded
            )

            self._committed = commit
            if changed:
                self._bad_streak = 0
                self._good_streak = 0
                await self._notify(commit)

            return commit

    async def _notify(self, assessment: GpuAssessment) -> None:
        if self.on_change is None:
            return
        try:
            result = self.on_change(assessment)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:  # noqa: BLE001 - callback nunca derruba o vigia
            print(f"[gpu-guard] erro no callback de troca: {exc}", flush=True)

    # ── falha/sucesso reportados pelo cliente LLM ────────────────────────

    def _next_tier(self, model: str) -> str:
        if _same(model, self.full_model):
            return "two_gpu"
        if _same(model, self.two_gpu_model):
            return "one_gpu"
        if _same(model, self.one_gpu_model):
            return "utility" if self.utility_expected > 0 else "cpu"
        # Modelo desconhecido falhou: assume o tier de 2 GPUs (mais seguro que
        # continuar tentando o 27B).
        return "two_gpu"

    async def note_model_failure(self, model: str, reason: str) -> GpuAssessment:
        """Registra falha de CUDA/carregamento e degrada imediatamente."""
        self._failure_reason = (reason or "erro de GPU")[:300]
        self._override_tier = self._next_tier(model)
        self._override_until = time.monotonic() + self.failure_cooldown
        print(
            f"[gpu-guard] falha no modelo {model}: {self._failure_reason} "
            f"-> tier {self._override_tier}",
            flush=True,
        )
        return await self.evaluate(force=True)

    async def note_model_success(self, model: str) -> Optional[GpuAssessment]:
        """Se o modelo principal voltou a carregar/gerar, limpa o override."""
        if not _same(model, self.full_model):
            return None
        if not self._override_tier:
            return None
        self._override_tier = ""
        self._override_until = 0.0
        self._failure_reason = ""
        print(
            f"[gpu-guard] modelo principal {model} voltou a funcionar; "
            "reavaliando o hardware.",
            flush=True,
        )
        return await self.evaluate(force=True)

    def force_tier(self, tier: str, reason: str = "") -> None:
        """Força um tier por tempo limitado (usado no warmup em cascata)."""
        if tier not in TIER_RANK:
            return
        self._override_tier = tier
        self._override_until = time.monotonic() + self.failure_cooldown
        if reason:
            self._failure_reason = reason[:300]

    def clear_override(self) -> None:
        """Descarta a memória de falha e deixa o próximo evaluate decidir."""
        self._override_tier = ""
        self._override_until = 0.0
        self._failure_reason = ""

    # ── status e ciclo de vida ───────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        base = self._committed or self._observed
        if base is None:
            return {
                "tier": "full",
                "model": self.full_model,
                "num_ctx": self.full_num_ctx,
                "healthy": True,
                "degraded": False,
                "reason": "aguardando primeira leitura",
                "devices": [],
            }
        out = base.as_dict()
        remaining = max(0.0, self._override_until - time.monotonic())
        out["override_tier"] = self._override_tier
        out["override_remaining_seconds"] = round(remaining, 1)
        out["observed_tier"] = (
            self._observed.tier if self._observed is not None else base.tier
        )
        return out

    async def start(self) -> None:
        """Faz a primeira leitura e inicia o loop de monitoramento."""
        await self.evaluate(force=True)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.poll_seconds)
                await self.evaluate()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                print(f"[gpu-guard] erro no monitor: {exc}", flush=True)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self._task = None
