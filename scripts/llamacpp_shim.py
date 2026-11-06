#!/usr/bin/env python3
"""Ponte Ollama -> llama.cpp para o Luminus.

O app fala com o Ollama (/api/chat, /api/generate, /api/ps, /api/tags). Este
processo escuta em SHIM_PORT e responde no mesmo dialeto, mas:

  - modelos listados em NARRATORS  -> llama-server (llama.cpp), com tensor
    cores liberados, split em camadas e decodificacao especulativa MTP;
  - qualquer outro modelo          -> Ollama real (OLLAMA), sem mudanca.

Assim o servidor do jogo nao precisa de alteracao: basta apontar OLLAMA_URL
para a porta desta ponte (scripts/llamacpp.sh on).

Somente biblioteca padrao: roda com o python3 do sistema.
"""

from __future__ import annotations

import http.client
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HOME = os.path.expanduser("~/.local/share/luminus/llamacpp")

DEFAULTS = {
    "BIN": os.path.join(HOME, "llama-b11998", "llama-server"),
    "LIBDIR": os.path.join(HOME, "llama-b11998")
    + ":"
    + os.path.join(HOME, "cudart-llama-b11998-bin-ubuntu-cuda-12.8-x64"),
    "MODEL": os.path.join(HOME, "model.gguf"),
    "PORT": "8199",
    "CTX": "32768",
    "MAX_CTX": "32768",
    "GPUS": "0,1,3",
    "SPLIT": "layer",
    "SPEC": "draft-mtp",
    "DRAFT_N": "2",
    "KV": "q8_0",
    "EXTRA": "",
    "SHIM_PORT": "11436",
    "OLLAMA": "http://127.0.0.1:11434",
    "NARRATORS": "luminus-27b",
    "LOG": os.path.join(HOME, "server.log"),
    "SHIM_LOG": os.path.join(HOME, "shim.log"),
    "PIDFILE": os.path.join(HOME, "shim.pid"),
    "REPEAT_PENALTY": "1.05",
}

CONFIG_PATHS = [
    os.environ.get("LUMINUS_LLAMACPP_CONFIG", ""),
    os.path.join(HOME, "config.env"),
]


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    for path in CONFIG_PATHS:
        if not path or not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                cfg[key.strip()] = value.strip().strip('"').strip("'")
    for key in list(cfg):
        env_key = "LUMINUS_LLAMACPP_" + key
        if env_key in os.environ:
            cfg[key] = os.environ[env_key]
    return cfg


CFG = load_config()
NARRATORS = {m.strip() for m in CFG["NARRATORS"].split(",") if m.strip()}
OLLAMA = CFG["OLLAMA"].rstrip("/")
LLAMA_PORT = int(CFG["PORT"])
SERVER_CTX = int(CFG["CTX"])
MAX_CTX = int(CFG["MAX_CTX"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(msg: str) -> None:
    print(f"[shim {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def is_narrator(model: str | None) -> bool:
    if not model:
        return False
    short = str(model).split(":")[0]
    return short in NARRATORS or str(model) in NARRATORS


# --------------------------------------------------------------------------
# Gerenciador do llama-server
# --------------------------------------------------------------------------
class Runner:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.proc: subprocess.Popen | None = None
        self.ctx = 0
        self.lock = threading.RLock()

    # -- utilitarios -------------------------------------------------------
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _health(self) -> bool:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", LLAMA_PORT, timeout=2)
            conn.request("GET", "/health")
            resp = conn.getresponse()
            ok = resp.status == 200
            resp.read()
            conn.close()
            return ok
        except Exception:
            return False

    def _tail_log(self, lines: int = 12) -> str:
        try:
            with open(self.cfg["LOG"], "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 6000))
                data = fh.read().decode("utf-8", "replace").splitlines()
            return "\n".join(data[-lines:])
        except Exception:
            return ""

    # -- ciclo de vida -----------------------------------------------------
    def _command(self, ctx: int) -> list[str]:
        cfg = self.cfg
        args = [
            cfg["BIN"],
            "-m", cfg["MODEL"],
            "-ngl", "99",
            "-sm", cfg["SPLIT"],
            "-fa", "on",
            "-ctk", cfg["KV"],
            "-ctv", cfg["KV"],
            "-c", str(ctx),
            "-np", "1",
            "--jinja",
            "--no-reasoning-preserve",
            "--repeat-penalty", cfg["REPEAT_PENALTY"],
            "--host", "127.0.0.1",
            "--port", cfg["PORT"],
        ]
        if cfg["SPEC"] and cfg["SPEC"] != "off":
            args += ["--spec-type", cfg["SPEC"], "--spec-draft-n-max", cfg["DRAFT_N"]]
        if cfg["EXTRA"]:
            args += shlex.split(cfg["EXTRA"])
        return args

    def _start_locked(self, ctx: int) -> None:
        if not os.path.isfile(self.cfg["BIN"]):
            raise RuntimeError(
                f"llama-server nao encontrado em {self.cfg['BIN']}; "
                "rode scripts/install_llamacpp.sh"
            )
        if not os.path.isfile(self.cfg["MODEL"]):
            raise RuntimeError(
                f"modelo GGUF nao encontrado em {self.cfg['MODEL']}; "
                "rode scripts/install_llamacpp.sh"
            )
        env = os.environ.copy()
        libs = [p for p in self.cfg["LIBDIR"].split(":") if p]
        env["LD_LIBRARY_PATH"] = ":".join(libs + [env.get("LD_LIBRARY_PATH", "")]).strip(":")
        env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        env["CUDA_VISIBLE_DEVICES"] = self.cfg["GPUS"]
        log = open(self.cfg["LOG"], "ab", buffering=0)
        _log(f"subindo llama-server ctx={ctx} gpus={self.cfg['GPUS']} spec={self.cfg['SPEC']}")
        self.proc = subprocess.Popen(
            self._command(ctx),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        self.ctx = ctx
        deadline = time.time() + 300
        while time.time() < deadline:
            if self.proc.poll() is not None:
                self.proc = None
                raise RuntimeError(
                    "llama-server morreu ao carregar o modelo:\n" + self._tail_log()
                )
            if self._health():
                _log(f"llama-server pronto (ctx={ctx}, pid={self.proc.pid})")
                return
            time.sleep(1.5)
        self._stop_locked()
        raise RuntimeError("llama-server nao ficou pronto em 300s")

    def _stop_locked(self) -> None:
        proc = self.proc
        self.proc = None
        self.ctx = 0
        if proc is None or proc.poll() is not None:
            return
        _log(f"parando llama-server (pid={proc.pid})")
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=20)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=10)
            except Exception:
                pass

    def ensure(self, requested_ctx: int | None = None) -> None:
        want = SERVER_CTX
        if requested_ctx:
            try:
                want = max(want, min(int(requested_ctx), MAX_CTX))
            except (TypeError, ValueError):
                pass
        with self.lock:
            if self.alive():
                if want > self.ctx:
                    _log(f"aumentando contexto {self.ctx} -> {want}")
                    self._stop_locked()
                    self._start_locked(want)
                return
            self._start_locked(want)

    def stop(self) -> None:
        with self.lock:
            self._stop_locked()

    def status(self) -> dict:
        return {
            "running": self.alive(),
            "pid": self.proc.pid if self.alive() else None,
            "ctx": self.ctx,
            "port": LLAMA_PORT,
            "model": self.cfg["MODEL"],
            "split": self.cfg["SPLIT"],
            "spec": self.cfg["SPEC"],
            "draft_n": self.cfg["DRAFT_N"],
            "gpus": self.cfg["GPUS"],
        }


RUNNER = Runner(CFG)


# --------------------------------------------------------------------------
# Conversao Ollama <-> OpenAI
# --------------------------------------------------------------------------
def to_openai(payload: dict) -> dict:
    opts = payload.get("options") or {}
    think = bool(payload.get("think") or False)
    out: dict = {
        "messages": payload.get("messages") or [],
        "stream": bool(payload.get("stream")),
        "temperature": opts.get("temperature", 0.8),
        "chat_template_kwargs": {"enable_thinking": think},
    }
    for src, dst in (
        ("top_p", "top_p"),
        ("top_k", "top_k"),
        ("seed", "seed"),
        ("repeat_penalty", "repeat_penalty"),
        ("min_p", "min_p"),
    ):
        if opts.get(src) is not None:
            out[dst] = opts[src]
    limit = opts.get("num_predict")
    if isinstance(limit, int) and 0 < limit < 1_000_000:
        out["max_tokens"] = limit
    if opts.get("stop"):
        out["stop"] = opts["stop"]
    fmt = payload.get("format")
    if fmt is not None:
        if isinstance(fmt, dict) and fmt.get("type") in ("json_object", "json_schema"):
            out["response_format"] = fmt
        elif isinstance(fmt, dict) and "schema" in fmt:
            out["response_format"] = {"type": "json_schema", "json_schema": fmt}
        else:
            out["response_format"] = {"type": "json_object"}
    return out


def ollama_message(content: str, thinking: str = "") -> dict:
    msg = {"role": "assistant", "content": content}
    if thinking:
        msg["thinking"] = thinking
    return msg


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "LuminusShim/1.0"

    def log_message(self, fmt, *args):  # silencia o log padrao
        pass

    # -- saida -------------------------------------------------------------
    def _send_bytes(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, status: int = 200) -> None:
        self._send_bytes(status, json.dumps(obj).encode(), "application/json")

    def _chunked_start(self, status: int, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _chunk(self, data: bytes) -> None:
        if not data:
            return
        self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
        self.wfile.flush()

    def _chunk_end(self) -> None:
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    # -- entrada -----------------------------------------------------------
    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _json(self) -> dict:
        raw = self._body()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            return {}

    # -- upstream ----------------------------------------------------------
    def _upstream(self, base: str, method: str, path: str, body: bytes | None = None):
        parts = urlsplit(base)
        conn = http.client.HTTPConnection(
            parts.hostname, parts.port or 80, timeout=None
        )
        headers = {"Content-Type": "application/json"} if body is not None else {}
        conn.request(method, path, body=body, headers=headers)
        return conn, conn.getresponse()

    def _forward(self, method: str, path: str, body: bytes, stream: bool) -> None:
        try:
            conn, resp = self._upstream(OLLAMA, method, path, body)
        except Exception as exc:
            self.send_json({"error": f"ollama indisponivel: {exc}"}, 502)
            return
        try:
            ctype = resp.getheader("Content-Type", "application/json")
            if stream:
                self._chunked_start(resp.status, ctype)
                while True:
                    line = resp.readline()
                    if not line:
                        break
                    self._chunk(line)
                self._chunk_end()
            else:
                data = resp.read()
                self._send_bytes(resp.status, data, ctype)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # -- roteamento --------------------------------------------------------
    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/luminus/status":
            self.send_json(
                {
                    "ok": True,
                    "narrators": sorted(NARRATORS),
                    "server": RUNNER.status(),
                    "ollama": OLLAMA,
                }
            )
            return
        if path == "/health":
            self.send_json({"status": "ok"})
            return
        if path == "/api/tags":
            self._tags()
            return
        if path == "/api/ps":
            self._ps()
            return
        self._forward("GET", self.path, b"", stream=False)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        body = self._body()
        if path == "/api/chat":
            self._chat(body)
            return
        if path == "/api/generate":
            self._generate(body)
            return
        if path in ("/api/embed", "/api/embeddings"):
            self._forward("POST", self.path, body, stream=False)
            return
        self._forward("POST", self.path, body, stream=False)

    # -- endpoints Ollama --------------------------------------------------
    def _tags(self) -> None:
        conn, resp = None, None
        try:
            conn, resp = self._upstream(OLLAMA, "GET", "/api/tags")
            data = json.loads(resp.read() or b"{}")
            status = resp.status
        except Exception:
            data, status = {"models": []}, 200
        finally:
            if conn:
                conn.close()
        models = data.get("models") or []
        have = {m.get("name", "").split(":")[0] for m in models}
        for name in sorted(NARRATORS):
            if name not in have:
                models.append(
                    {
                        "name": name,
                        "model": name,
                        "modified_at": _now(),
                        "size": _model_size(),
                        "digest": "llamacpp",
                        "details": {
                            "format": "gguf",
                            "family": "qwen35",
                            "parameter_size": "27B",
                            "quantization_level": "Q4_K_M",
                        },
                    }
                )
        data["models"] = models
        self.send_json(data, status)

    def _ps(self) -> None:
        models = []
        if RUNNER.alive():
            size = _model_size()
            for name in sorted(NARRATORS):
                models.append(
                    {
                        "name": name,
                        "model": name,
                        "size": size,
                        "size_vram": size,
                        "digest": "llamacpp",
                        "details": {
                            "format": "gguf",
                            "family": "qwen35",
                            "parameter_size": "27B",
                            "quantization_level": "Q4_K_M",
                        },
                        "expires_at": _now(),
                    }
                )
        conn = None
        try:
            conn, resp = self._upstream(OLLAMA, "GET", "/api/ps")
            other = json.loads(resp.read() or b"{}").get("models") or []
            models += [m for m in other if not is_narrator(m.get("name"))]
        except Exception:
            pass
        finally:
            if conn:
                conn.close()
        self.send_json({"models": models})

    # -- chat --------------------------------------------------------------
    def _chat(self, body: bytes) -> None:
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            self.send_json({"error": "json invalido"}, 400)
            return
        model = payload.get("model")
        if not is_narrator(model):
            self._forward("POST", self.path, body, bool(payload.get("stream")))
            return

        opts = payload.get("options") or {}
        try:
            RUNNER.ensure(opts.get("num_ctx"))
        except Exception as exc:
            self.send_json({"error": str(exc)}, 503)
            return

        out = to_openai(payload)
        name = model or sorted(NARRATORS)[0]
        try:
            conn, resp = self._upstream(
                f"http://127.0.0.1:{LLAMA_PORT}",
                "POST",
                "/v1/chat/completions",
                json.dumps(out).encode(),
            )
        except Exception as exc:
            self.send_json({"error": f"llama-server indisponivel: {exc}"}, 502)
            return

        try:
            if resp.status != 200:
                detail = resp.read().decode("utf-8", "replace")
                self.send_json({"error": detail or f"http {resp.status}"}, 502)
                return
            if out["stream"]:
                self._chat_stream(resp, name)
            else:
                self._chat_full(resp, name)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _chat_full(self, resp, name: str) -> None:
        data = json.loads(resp.read() or b"{}")
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        usage = data.get("usage") or {}
        timings = data.get("timings") or {}
        out = {
            "model": name,
            "created_at": _now(),
            "message": ollama_message(
                msg.get("content") or "", msg.get("reasoning_content") or ""
            ),
            "done": True,
            "done_reason": choice.get("finish_reason") or "stop",
            "prompt_eval_count": usage.get("prompt_tokens", timings.get("prompt_n", 0)),
            "eval_count": usage.get("completion_tokens", timings.get("predicted_n", 0)),
        }
        self.send_json(out)

    def _chat_stream(self, resp, name: str) -> None:
        self._chunked_start(200, "application/x-ndjson")
        finish = "stop"
        timings = {}
        try:
            while True:
                line = resp.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").strip()
                if not text or not text.startswith("data:"):
                    continue
                data = text[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                if chunk.get("timings"):
                    timings = chunk["timings"]
                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                content = delta.get("content") or ""
                thinking = delta.get("reasoning_content") or ""
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
                if content or thinking:
                    self._chunk(
                        (
                            json.dumps(
                                {
                                    "model": name,
                                    "created_at": _now(),
                                    "message": ollama_message(content, thinking),
                                    "done": False,
                                }
                            )
                            + "\n"
                        ).encode()
                    )
            final = {
                "model": name,
                "created_at": _now(),
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "done_reason": finish,
                "prompt_eval_count": timings.get("prompt_n", 0),
                "eval_count": timings.get("predicted_n", 0),
            }
            self._chunk((json.dumps(final) + "\n").encode())
        finally:
            self._chunk_end()

    # -- generate (warmup / unload / texto puro) ---------------------------
    def _generate(self, body: bytes) -> None:
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            self.send_json({"error": "json invalido"}, 400)
            return
        model = payload.get("model")
        if not is_narrator(model):
            self._forward("POST", self.path, body, bool(payload.get("stream")))
            return

        prompt = payload.get("prompt")
        keep_alive = payload.get("keep_alive")
        if not prompt and keep_alive in (0, "0"):
            RUNNER.stop()
            self.send_json({"model": model, "created_at": _now(), "done": True})
            return

        opts = payload.get("options") or {}
        try:
            RUNNER.ensure(opts.get("num_ctx"))
        except Exception as exc:
            self.send_json({"error": str(exc)}, 503)
            return

        out = {
            "prompt": prompt or "",
            "stream": bool(payload.get("stream")),
            "temperature": opts.get("temperature", 0.8),
        }
        if opts.get("num_predict"):
            out["max_tokens"] = opts["num_predict"]
        if opts.get("stop"):
            out["stop"] = opts["stop"]
        if opts.get("seed") is not None:
            out["seed"] = opts["seed"]
        try:
            conn, resp = self._upstream(
                f"http://127.0.0.1:{LLAMA_PORT}",
                "POST",
                "/v1/completions",
                json.dumps(out).encode(),
            )
        except Exception as exc:
            self.send_json({"error": f"llama-server indisponivel: {exc}"}, 502)
            return
        try:
            if resp.status != 200:
                detail = resp.read().decode("utf-8", "replace")
                self.send_json({"error": detail or f"http {resp.status}"}, 502)
                return
            if out["stream"]:
                self._gen_stream(resp, model)
            else:
                data = json.loads(resp.read() or b"{}")
                choice = (data.get("choices") or [{}])[0]
                usage = data.get("usage") or {}
                self.send_json(
                    {
                        "model": model,
                        "created_at": _now(),
                        "response": choice.get("text") or "",
                        "done": True,
                        "done_reason": choice.get("finish_reason") or "stop",
                        "prompt_eval_count": usage.get("prompt_tokens", 0),
                        "eval_count": usage.get("completion_tokens", 0),
                    }
                )
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _gen_stream(self, resp, name: str) -> None:
        self._chunked_start(200, "application/x-ndjson")
        try:
            while True:
                line = resp.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").strip()
                if not text or not text.startswith("data:"):
                    continue
                data = text[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                choice = (chunk.get("choices") or [{}])[0]
                piece = choice.get("text") or ""
                if piece:
                    self._chunk(
                        (
                            json.dumps(
                                {
                                    "model": name,
                                    "created_at": _now(),
                                    "response": piece,
                                    "done": False,
                                }
                            )
                            + "\n"
                        ).encode()
                    )
            self._chunk(
                (
                    json.dumps(
                        {
                            "model": name,
                            "created_at": _now(),
                            "response": "",
                            "done": True,
                            "done_reason": "stop",
                        }
                    )
                    + "\n"
                ).encode()
            )
        finally:
            self._chunk_end()


def _model_size() -> int:
    try:
        return os.path.getsize(CFG["MODEL"]) + 2 * 1024**3
    except OSError:
        return 18 * 1024**3


def _shutdown(signum, frame) -> None:
    RUNNER.stop()
    try:
        os.unlink(CFG["PIDFILE"])
    except OSError:
        pass
    sys.exit(0)


def main() -> int:
    if "--check" in sys.argv:
        print(json.dumps({"config": CFG, "narrators": sorted(NARRATORS)}, indent=2))
        return 0
    if not os.path.isdir(HOME):
        print(f"[shim] diretorio ausente: {HOME}", file=sys.stderr)
        return 2
    try:
        with open(CFG["PIDFILE"], "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pass
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    server = ThreadingHTTPServer(("127.0.0.1", int(CFG["SHIM_PORT"])), Handler)
    server.daemon_threads = True
    _log(
        f"escutando em http://127.0.0.1:{CFG['SHIM_PORT']} "
        f"(narrators={sorted(NARRATORS)}, ollama={OLLAMA})"
    )
    try:
        server.serve_forever()
    finally:
        RUNNER.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
