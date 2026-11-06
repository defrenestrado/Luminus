#!/usr/bin/env python3
"""Mede a velocidade de geracao do Luminus antes/depois do gpu-unlock.

Tres frentes, todas independentes de rede externa:

  tensor  Matmul denso BF16/FP16/TF32 por GPU (mostra o tensor core cru).
  llm     Prefill e decode do Narrador via API do Ollama (porta 11434).
  comfy   Flux schnell (cena) e SDXL NoobAI (adulto/fotorreal) via ComfyUI.

Uso:
  server/.venv/bin/python scripts/gpu_unlock_bench.py --phase pre
  server/.venv/bin/python scripts/gpu_unlock_bench.py --phase post

Grava evals/gpu_unlock_bench_<phase>.json e imprime um resumo. Use --skip-*
para pular uma frente (ex.: --skip-comfy quando nao quiser subir worker).
"""

import argparse
import json
import os
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
COMFY_UNIT = os.environ.get("LUMINUS_COMFY_UNIT", "luminus-comfy-gpu0")
COMFY_PORT = int(os.environ.get("LUMINUS_COMFY_PORT", "8189"))
COMFY_URL = f"http://127.0.0.1:{COMFY_PORT}"
LLM_MODEL = os.environ.get("LUMINUS_BENCH_LLM", "luminus-27b")
LLM_PROMPT_TOKENS = int(os.environ.get("LUMINUS_BENCH_PROMPT_TOKENS", "2000"))


def http_json(url, payload=None, timeout=60.0):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode()
    return json.loads(raw) if raw.strip() else {}


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw).stdout.strip()


def module_loaded():
    return "gpu_unlock" in Path("/proc/modules").read_text()


def nvidia_smi():
    return run([
        "nvidia-smi",
        "--query-gpu=index,name,pci.bus_id,power.limit,power.max_limit,clocks.max.sm",
        "--format=csv,noheader",
    ])


def bench_tensor(gpus):
    os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
    import torch

    info = []
    n = 8192
    clean = lambda: torch.cuda.empty_cache()

    def rate(dtype, iters=10, use_tf32=False, use_bf16=False):
        a = torch.randn(n, n, device="cuda", dtype=dtype)
        b = torch.randn(n, n, device="cuda", dtype=dtype)
        for _ in range(3):
            torch.mm(a, b)
        torch.cuda.synchronize()
        start = time.perf_counter()
        for _ in range(iters):
            torch.mm(a, b)
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - start
        del a, b
        clean()
        return 2 * n ** 3 * iters / elapsed / 1e12

    for idx in gpus:
        if idx >= torch.cuda.device_count():
            continue
        torch.cuda.set_device(idx)
        old_tf32 = torch.backends.cuda.matmul.allow_tf32
        old_bf16 = torch.backends.cuda.matmul.allow_bf16_reduced_precision_reduction
        torch.backends.cuda.matmul.allow_tf32 = False
        fp32 = rate(torch.float32)
        torch.backends.cuda.matmul.allow_tf32 = True
        tf32 = rate(torch.float32)
        torch.backends.cuda.matmul.allow_tf32 = old_tf32
        torch.backends.cuda.matmul.allow_bf16_reduced_precision_reduction = True
        bf16 = rate(torch.bfloat16)
        fp16 = rate(torch.float16)
        torch.backends.cuda.matmul.allow_bf16_reduced_precision_reduction = old_bf16
        info.append({
            "index": idx,
            "name": torch.cuda.get_device_name(idx),
            "fp32_tflops": round(fp32, 1),
            "tf32_tflops": round(tf32, 1),
            "bf16_tflops": round(bf16, 1),
            "fp16_tflops": round(fp16, 1),
        })
        print(f"  GPU {idx} {info[-1]['name']}: "
              f"BF16 {bf16:.1f} TFLOPS | FP16 {fp16:.1f} | TF32 {tf32:.1f} | FP32 {fp32:.1f}")
    return info


def bench_llm():
    words = ("luminus narrador cena escolha dado laço inventário fio memória "
             "personagem taverna floresta espada magia noite chuva").split()
    text_parts = [f"{i}: " + " ".join(words[i % len(words)] for i in range(14))
                  for i in range(LLM_PROMPT_TOKENS)]
    prompt = ("Leia os registros abaixo e responda em uma frase que resume "
              "o clima geral.\n\n" + "\n".join(text_parts))

    def generate(num_predict, keep_alive):
        payload = {
            "model": LLM_MODEL,
            "prompt": prompt,
            "stream": False,
            "keep_alive": keep_alive,
            "options": {"num_ctx": 8192, "num_predict": num_predict,
                        "temperature": 0, "seed": 42},
        }
        t0 = time.perf_counter()
        data = http_json(f"{OLLAMA_URL}/api/generate", payload, timeout=600.0)
        data["wall_s"] = round(time.perf_counter() - t0, 3)
        return data

    try:
        http_json(f"{OLLAMA_URL}/api/generate",
                  {"model": LLM_MODEL, "prompt": "ok", "stream": False,
                   "keep_alive": "10m",
                   "options": {"num_ctx": 8192, "num_predict": 1}},
                  timeout=600.0)
        runs = [generate(64, "10m") for _ in range(2)]
    finally:
        try:
            http_json(f"{OLLAMA_URL}/api/generate",
                      {"model": LLM_MODEL, "prompt": "ok", "stream": False,
                       "keep_alive": "0", "options": {"num_ctx": 512, "num_predict": 1}},
                      timeout=120.0)
        except Exception:
            pass

    best = max(runs, key=lambda r: r.get("prompt_eval_count", 0))
    prefill_tps = best["prompt_eval_count"] / (best["prompt_eval_duration"] / 1e9)
    decode_tps = best["eval_count"] / (best["eval_duration"] / 1e9)
    result = {
        "model": LLM_MODEL,
        "prompt_tokens": best["prompt_eval_count"],
        "prompt_tokens_per_s": round(prefill_tps, 1),
        "decode_tokens_per_s": round(decode_tps, 1),
        "wall_s": best["wall_s"],
        "load_s": round(best.get("load_duration", 0) / 1e9, 2),
    }
    print(f"  LLM {LLM_MODEL}: prefill {prefill_tps:.1f} tok/s | "
          f"decode {decode_tps:.1f} tok/s | parede {best['wall_s']:.1f}s")
    return result


def comfy_up():
    try:
        http_json(f"{COMFY_URL}/system_stats", timeout=2.0)
        return True
    except Exception:
        return False


def comfy_wait(timeout=240.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if comfy_up():
            return True
        time.sleep(1)
    return False


def comfy_time(build, runs=3):
    def one(seed):
        t0 = time.perf_counter()
        data = http_json(f"{COMFY_URL}/prompt", {"prompt": build(seed)}, timeout=30.0)
        prompt_id = data["prompt_id"]
        end = time.monotonic() + 300
        while time.monotonic() < end:
            hist = http_json(f"{COMFY_URL}/history/{prompt_id}", timeout=15.0)
            if prompt_id in hist:
                return time.perf_counter() - t0
            time.sleep(0.1)
        raise TimeoutError(f"ComfyUI nao concluiu {prompt_id}")

    cold = one(1000)
    warm = sorted(one(2000 + i) for i in range(runs))
    return {"cold_s": round(cold, 2),
            "warm_s": [round(x, 2) for x in warm],
            "median_s": round(statistics.median(warm), 2)}


def bench_comfy():
    sys.path.insert(0, str(ROOT / "server"))
    import art

    started = False
    if not comfy_up():
        subprocess.run(["systemctl", "--user", "start", COMFY_UNIT],
                       capture_output=True)
        started = True
    if not comfy_wait():
        raise RuntimeError(f"worker {COMFY_UNIT} nao subiu em {COMFY_URL}")

    prompt = "luminus benchmark, cinematic portrait, detailed lighting"
    flux_wf = lambda seed: art._comfy_workflow(prompt, seed, 832, 1216)
    sdxl_wf = lambda seed: art._comfy_adult_workflow(
        prompt, "worst quality, low quality, lowres, bad anatomy", seed, 832, 1216)

    result = {}
    try:
        http_json(f"{COMFY_URL}/free",
                  {"unload_models": True, "free_memory": True}, timeout=15.0)
        result["flux"] = comfy_time(flux_wf)
        print(f"  ComfyUI Flux schnell: mediana {result['flux']['median_s']}s "
              f"(frio {result['flux']['cold_s']}s)")
        http_json(f"{COMFY_URL}/free",
                  {"unload_models": True, "free_memory": True}, timeout=15.0)
        result["sdxl"] = comfy_time(sdxl_wf)
        print(f"  ComfyUI SDXL NoobAI: mediana {result['sdxl']['median_s']}s "
              f"(frio {result['sdxl']['cold_s']}s)")
    finally:
        if started:
            subprocess.run(["systemctl", "--user", "stop", COMFY_UNIT],
                           capture_output=True)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="pre", help="rotulo do resultado (pre/post)")
    ap.add_argument("--out", default=str(ROOT / "evals"))
    ap.add_argument("--gpus", default="0,1,2,3")
    ap.add_argument("--skip-tensor", action="store_true")
    ap.add_argument("--skip-llm", action="store_true")
    ap.add_argument("--skip-comfy", action="store_true")
    args = ap.parse_args()
    gpus = [int(x) for x in args.gpus.split(",") if x.strip()]

    result = {
        "phase": args.phase,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": socket.gethostname(),
        "kernel": run(["uname", "-r"]),
        "cmdline": Path("/proc/cmdline").read_text().strip(),
        "gpu_unlock_loaded": module_loaded(),
        "nvidia_smi": nvidia_smi(),
    }
    print(f"[{args.phase}] gpu_unlock carregado: {result['gpu_unlock_loaded']}")

    if not args.skip_tensor:
        print("Tensor cores:")
        try:
            result["tensor"] = bench_tensor(gpus)
        except Exception as exc:
            result["tensor"] = {"error": str(exc)}
            print(f"  ERRO: {exc}")
    if not args.skip_llm:
        print("LLM (Ollama):")
        try:
            result["llm"] = bench_llm()
        except Exception as exc:
            result["llm"] = {"error": str(exc)}
            print(f"  ERRO: {exc}")
    if not args.skip_comfy:
        print("Imagem (ComfyUI):")
        try:
            result["comfy"] = bench_comfy()
        except Exception as exc:
            result["comfy"] = {"error": str(exc)}
            print(f"  ERRO: {exc}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"gpu_unlock_bench_{args.phase}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"\nSalvo em {path}")


if __name__ == "__main__":
    main()
