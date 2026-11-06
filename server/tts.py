import hashlib
import io
import os
import subprocess
import tempfile
import threading
import wave

import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
VOICES_DIR = os.path.join(BASE, "voices")
CACHE_DIR = os.path.join(BASE, "tts_cache")
PIPER = os.path.join(BASE, ".venv", "bin", "piper")

KOKORO_DIR = os.path.join(BASE, "kokoro")
KOKORO_MODEL = os.path.join(KOKORO_DIR, "kokoro-v1.0.onnx")
KOKORO_VOICES_FILE = os.path.join(KOKORO_DIR, "voices-v1.0.bin")
KOKORO_PT_VOICES = ["pf_dora", "pm_alex", "pm_santa"]

XTTS_MODEL = os.environ.get("LUMINUS_XTTS_MODEL", "tts_models/multilingual/multi-dataset/xtts_v2")
XTTS_SPEAKER = os.environ.get("LUMINUS_XTTS_SPEAKER", "Ana Florence")

DEFAULT_VOICE = os.environ.get("LUMINUS_TTS_VOICE", "kokoro/pf_dora")
PIPER_LENGTH_SCALE = os.environ.get("LUMINUS_PIPER_LENGTH", "1.2")
KOKORO_SPEED = float(os.environ.get("LUMINUS_KOKORO_SPEED", "0.8"))
XTTS_SPEED = float(os.environ.get("LUMINUS_XTTS_SPEED", "0.8"))

os.makedirs(CACHE_DIR, exist_ok=True)

_kokoro = None
_kokoro_lock = threading.Lock()
_xtts = None
_xtts_lock = threading.Lock()
_cuda = None


def _cuda_ok():
    global _cuda
    if _cuda is None:
        try:
            import torch

            _cuda = bool(torch.cuda.is_available())
        except Exception:
            _cuda = False
    return _cuda


def _piper_voices():
    if not os.path.isdir(VOICES_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(VOICES_DIR) if f.endswith(".onnx"))


def available_voices():
    out = []
    if _cuda_ok():
        out.append(f"xtts/{XTTS_SPEAKER}")
    if os.path.isfile(KOKORO_MODEL) and os.path.isfile(KOKORO_VOICES_FILE):
        out += [f"kokoro/{v}" for v in KOKORO_PT_VOICES]
    out += [f"piper/{v}" for v in _piper_voices()]
    return out


def _resolve(voice):
    v = (voice or DEFAULT_VOICE).strip()
    if "/" in v:
        backend, name = v.split("/", 1)
    elif v.startswith(("pf_", "pm_")):
        backend, name = "kokoro", v
    else:
        backend, name = "piper", v
    return backend, name


def _kokoro_instance():
    global _kokoro
    if _kokoro is None:
        with _kokoro_lock:
            if _kokoro is None:
                from kokoro_onnx import Kokoro

                _kokoro = Kokoro(KOKORO_MODEL, KOKORO_VOICES_FILE)
    return _kokoro


def _xtts_instance():
    global _xtts
    if _xtts is None:
        with _xtts_lock:
            if _xtts is None:
                from TTS.api import TTS

                dev = "cuda" if _cuda_ok() else "cpu"
                t = TTS(XTTS_MODEL)
                t.to(dev)
                _xtts = t
    return _xtts


def _wav_bytes(samples, sr):
    arr = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm = (arr * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def _synth_kokoro(text, voice):
    if not (os.path.isfile(KOKORO_MODEL) and os.path.isfile(KOKORO_VOICES_FILE)):
        raise RuntimeError("kokoro nao instalado")
    name = voice if voice in KOKORO_PT_VOICES else "pf_dora"
    k = _kokoro_instance()
    samples, sr = k.create(text, voice=name, speed=KOKORO_SPEED, lang="pt")
    return _wav_bytes(samples, sr)


def _synth_xtts(text, speaker):
    if not _cuda_ok():
        raise RuntimeError("CUDA indisponivel para XTTS")
    t = _xtts_instance()
    sr = int(getattr(t.synthesizer, "output_sample_rate", 24000))
    try:
        wav = t.tts(text=text, speaker=speaker or XTTS_SPEAKER, language="pt", speed=XTTS_SPEED)
    except TypeError:
        wav = t.tts(text=text, speaker=speaker or XTTS_SPEAKER, language="pt")
    return _wav_bytes(wav, sr)


def _synth_piper(text, voice):
    name = os.path.basename((voice or "").strip())
    if name.endswith(".onnx"):
        name = name[:-5]
    path = os.path.join(VOICES_DIR, f"{name}.onnx")
    if not os.path.isfile(path):
        raise RuntimeError("voz nao encontrada")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        proc = subprocess.run(
            [PIPER, "-m", path, "-f", tmp_path, "--length-scale", PIPER_LENGTH_SCALE],
            input=text.encode("utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=180,
        )
        if proc.returncode != 0 or not os.path.isfile(tmp_path):
            raise RuntimeError(
                "piper falhou: " + (proc.stderr or b"").decode(errors="ignore")[-300:]
            )
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _fallback(text):
    try:
        return _synth_kokoro(text, "pf_dora")
    except Exception:
        return _synth_piper(text, _piper_voices()[0] if _piper_voices() else "")


def synthesize(text: str, voice: str = "") -> bytes:
    text = (text or "").strip()
    if not text:
        raise ValueError("texto vazio")
    text = text[:6000]
    backend, name = _resolve(voice)
    key = hashlib.sha256((backend + "/" + name + "\n" + text).encode()).hexdigest()
    out = os.path.join(CACHE_DIR, key + ".wav")
    if os.path.isfile(out) and os.path.getsize(out) > 44:
        with open(out, "rb") as f:
            return f.read()

    if backend == "xtts":
        try:
            data = _synth_xtts(text, name)
        except Exception:
            data = _fallback(text)
    elif backend == "kokoro":
        try:
            data = _synth_kokoro(text, name)
        except Exception:
            data = _fallback(text)
    else:
        data = _synth_piper(text, name)

    with open(out, "wb") as f:
        f.write(data)
    return data
