#!/usr/bin/env python3
"""Teste de autonomia, coerência narrativa e ambiente do Luminus.

Roda um roteiro curto contra a API real:

1. abre uma sessão nova (NPC + protagonista + elenco);
2. faz N turnos "idle" (continue_scene, sem agência do jogador) e verifica
   que o mundo/NPC avança, o turno incrementa e eventos fora de cena surgem;
3. faz M turnos do jogador com ações de ambiente e verifica a resposta;
4. audita o estado de ambiente (local/hora/cena sem teleporte) e chama o Juiz
   de consistência da API.

Uso:
  .venv/bin/python scripts/autonomy_coherence_test.py
  .venv/bin/python scripts/autonomy_coherence_test.py --idle 6 --player 2

Sai com código 1 se houver falha dura; salva relatório JSON/Markdown em evals/.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import time
import unicodedata

import httpx

API = os.environ.get("LUMINUS_API", "http://127.0.0.1:5111")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVALS = os.path.join(ROOT, "evals")

PLAYER_ACTIONS = [
    "Encosto na porta e fico quieto, escutando o ambiente por um instante.",
    "Olho ao redor com calma e reparo no que mudou desde que cheguei.",
]


def _norm(text: str) -> str:
    raw = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(ch for ch in raw if not unicodedata.combining(ch))


def get(path: str):
    with httpx.Client(timeout=60) as client:
        resp = client.get(API + path)
        resp.raise_for_status()
        return resp.json()


def post(path: str, body: dict | None = None, timeout: float = 900):
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(API + path, json=body or {})
        resp.raise_for_status()
        return resp.json()


def chat(sid: int, message: str = "", continue_scene: bool = False) -> str:
    parts: list[str] = []
    timeout = httpx.Timeout(900.0, connect=20.0)
    with httpx.Client(timeout=timeout) as client:
        with client.stream(
            "POST",
            f"{API}/api/sessions/{sid}/chat",
            json={"message": message, "continue_scene": continue_scene},
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                item = json.loads(line)
                if "error" in item:
                    raise RuntimeError(item["error"])
                if "delta" in item:
                    parts.append(item["delta"])
                if item.get("replace"):
                    parts = [item["replace"]]
    return "".join(parts).strip()


def ledger(sid: int) -> dict:
    sess = get(f"/api/sessions/{sid}")
    try:
        return json.loads(sess.get("ledger") or "{}")
    except ValueError:
        return {}


def voices(sid: int) -> list:
    data = get(f"/api/sessions/{sid}/voices")
    return data.get("voices", []) if isinstance(data, dict) else []


def character_names() -> dict[int, str]:
    return {int(c["id"]): c.get("name") or "" for c in get("/api/characters")}


def strip_tags(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("*", "")).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--npc", type=int, default=int(os.environ.get("LUMINUS_TEST_NPC", "13")))
    parser.add_argument("--protagonist", type=int, default=int(os.environ.get("LUMINUS_TEST_PROTAGONIST", "8")))
    parser.add_argument("--cast", default=os.environ.get("LUMINUS_TEST_CAST", "12,11,10"))
    parser.add_argument("--idle", type=int, default=4)
    parser.add_argument("--player", type=int, default=2)
    parser.add_argument("--opening", dest="opening", action="store_true", default=True)
    parser.add_argument("--no-opening", dest="opening", action="store_false")
    parser.add_argument("--keep", action="store_true", help="não apagar a sessão de teste")
    args = parser.parse_args()

    cast = [int(x) for x in args.cast.split(",") if x.strip()]
    names = character_names()
    os.makedirs(EVALS, exist_ok=True)
    start = dt.datetime.now()
    run = {
        "started_at": start.isoformat(timespec="seconds"),
        "api": API,
        "npc": args.npc,
        "protagonist": args.protagonist,
        "cast": cast,
        "idle_turns_planned": args.idle,
        "player_turns_planned": args.player,
        "turns": [],
        "failures": [],
        "warnings": [],
    }

    session = post(
        "/api/sessions",
        {
            "character_id": args.npc,
            "protagonist_id": args.protagonist,
            "cast_ids": cast,
            "title": "Teste autonomia/ambiente",
        },
    )
    sid = int(session["id"])
    run["session_id"] = sid
    print(f"[teste] sessao {sid} | NPC={names.get(args.npc, args.npc)} | elenco={cast}")

    if args.opening:
        t0 = time.perf_counter()
        opening = post(f"/api/sessions/{sid}/opening")
        run["opening_seconds"] = round(time.perf_counter() - t0, 1)
        text = ""
        if isinstance(opening, dict):
            msg = opening.get("message") or {}
            text = msg.get("content") or ""
        run["opening"] = strip_tags(text)[:1200]
        print(f"[teste] abertura em {run['opening_seconds']}s")

    expected_offscreen = 4
    offscreen_seen: list[dict] = []
    location_mismatches: list[dict] = []
    teleports: list[dict] = []
    present_unknown: list[dict] = []
    times: list[str] = []
    locations: list[str] = []

    def record(turn: dict, state: dict) -> None:
        scene = state.get("scene") or {}
        loc = str(state.get("location") or "")
        turn["env"] = {
            "location": loc,
            "time": str(state.get("time") or ""),
            "scene_id": scene.get("id"),
            "scene_location": scene.get("location"),
            "present": scene.get("present") or [],
            "transition": scene.get("transition") or "",
        }
        run["turns"].append(turn)
        scene_loc = str(scene.get("location") or "")
        times.append(str(state.get("time") or ""))
        locations.append(loc)
        if loc and scene_loc and _norm(loc) != _norm(scene_loc):
            location_mismatches.append(
                {"turn": turn["turn"], "state": loc, "scene": scene_loc}
            )
        unknown = [
            name for name in (scene.get("present") or [])
            if name not in names.values()
        ]
        if unknown:
            present_unknown.append({"turn": turn["turn"], "names": unknown})

    def new_world_event(state: dict, before_count: int) -> str:
        events = state.get("events") or []
        for item in events[before_count:]:
            text = str(item.get("text") or "").strip()
            if text:
                return text[:220]
        return ""

    previous_scene = None
    for index in range(1, args.idle + 1):
        before_events = len(ledger(sid).get("events") or [])
        t0 = time.perf_counter()
        text = chat(sid, continue_scene=True)
        elapsed = round(time.perf_counter() - t0, 1)
        state = ledger(sid)
        scene = state.get("scene") or {}
        turn = {
            "kind": "idle",
            "turn": int(state.get("turn", 0) or 0),
            "seconds": elapsed,
            "narration": strip_tags(text),
        }
        if not text:
            run["failures"].append(f"idle {index}: narração vazia")
        turn["world_event"] = new_world_event(state, before_events)
        if scene.get("transition"):
            turn["transition"] = scene.get("transition")
        if previous_scene is not None:
            changed = _norm(scene.get("location") or "") != _norm(
                previous_scene.get("location") or ""
            )
            if changed and int(scene.get("id", 0) or 0) <= int(
                previous_scene.get("id", 0) or 0
            ):
                teleports.append(
                    {
                        "turn": turn["turn"],
                        "from": previous_scene.get("location"),
                        "to": scene.get("location"),
                        "scene_id": scene.get("id"),
                    }
                )
        previous_scene = dict(scene)
        off = state.get("offscreen") or []
        if off:
            offscreen_seen = off[-6:]
        record(turn, state)
        print(
            f"[idle {index}] turn={turn['turn']} {elapsed}s "
            f"loc={state.get('location')!r} time={state.get('time')!r} "
            f"event={turn.get('world_event','')[:80]!r}"
        )

    for index in range(1, args.player + 1):
        action = PLAYER_ACTIONS[(index - 1) % len(PLAYER_ACTIONS)]
        t0 = time.perf_counter()
        text = chat(sid, message=action)
        elapsed = round(time.perf_counter() - t0, 1)
        state = ledger(sid)
        turn = {
            "kind": "player",
            "turn": int(state.get("turn", 0) or 0),
            "seconds": elapsed,
            "action": action,
            "narration": strip_tags(text),
        }
        if not text:
            run["failures"].append(f"player {index}: narração vazia")
        words = [
            word for word in re.findall(r"[a-zà-ÿ]{4,}", _norm(action))
            if word not in ("encosto", "quieto", "instante", "olho", "calma", "reparo", "mudou", "cheguei")
        ]
        echoed = [word for word in words if word in _norm(text)]
        turn["environment_echo"] = echoed
        if not echoed:
            run["warnings"].append(
                f"player {index}: resposta não ecoou palavras do ambiente "
                f"({', '.join(words[:5])})"
            )
        record(turn, state)
        print(
            f"[player {index}] turn={turn['turn']} {elapsed}s "
            f"eco={echoed} narr={turn['narration'][:90]!r}"
        )

    final_state = ledger(sid)
    final_turn = int(final_state.get("turn", 0) or 0)
    all_voices = voices(sid)
    consistency = post(f"/api/sessions/{sid}/consistency", timeout=600)

    if not any(t.get("world_event") for t in run["turns"] if t["kind"] == "idle"):
        run["failures"].append("nenhum turno idle produziu iniciativa do mundo/NPC")
    if final_turn < args.idle + args.player:
        run["failures"].append(
            f"turno não avançou o esperado: {final_turn} < {args.idle + args.player}"
        )
    if not any(times):
        run["failures"].append("estado de ambiente sem hora em nenhum turno")
    distinct_times = [value for value in dict.fromkeys(times) if value]
    if len(times) >= 4 and len(distinct_times) <= 1:
        run["warnings"].append(
            f"relógio do mundo não avançou em {len(times)} turnos "
            f"(hora fixa: {distinct_times[0] if distinct_times else 'vazia'})"
        )
    if location_mismatches:
        run["failures"].append(
            f"{len(location_mismatches)} divergência(s) local do estado x cena"
        )
    if teleports:
        run["failures"].append(f"{len(teleports)} mudança(s) de local sem troca de cena")
    if present_unknown:
        run["failures"].append("cena com presentes fora do elenco/cadastro")
    final_present = {
        _norm(name)
        for name in ((final_state.get("scene") or {}).get("present") or [])
    }
    absent_cast = [
        name for name in (names.get(cid, "") for cid in cast)
        if name and _norm(name) not in final_present
    ]
    if final_turn >= expected_offscreen and cast and not offscreen_seen:
        if absent_cast:
            run["failures"].append(
                f"sem eventos fora de cena até o turno {final_turn} "
                f"(esperado a cada {expected_offscreen}; ausentes: {absent_cast})"
            )
        else:
            run["warnings"].append(
                "todo o elenco está em cena; eventos fora de cena não se aplicam"
            )
    severity = str(consistency.get("severity") or "").lower()
    if severity == "alta":
        run["failures"].append(
            "juiz de consistência apontou contradição de severidade ALTA: "
            + str(consistency.get("summary") or "")[:200]
        )
    elif not consistency.get("ok"):
        run["warnings"].append(
            "juiz de consistência marcou severidade "
            + (severity or "?")
            + ": "
            + str(consistency.get("summary") or "")[:160]
        )
    if consistency.get("issues"):
        run["warnings"].append(
            f"{len(consistency['issues'])} issue(s) de consistência"
        )

    run["finished_at"] = dt.datetime.now().isoformat(timespec="seconds")
    run["final_state"] = {
        key: final_state.get(key)
        for key in ("turn", "location", "time", "scene", "offscreen", "passive")
    }
    run["environment"] = {
        "times": times,
        "locations": locations,
        "location_mismatches": location_mismatches,
        "teleports": teleports,
        "present_unknown": present_unknown,
    }
    run["autonomy"] = {
        "world_events": [
            t.get("world_event") for t in run["turns"] if t.get("world_event")
        ],
        "offscreen": offscreen_seen,
        "voices": [
            {"kind": v.get("kind"), "author": v.get("author")}
            for v in all_voices[-4:]
        ],
    }
    run["coherence"] = consistency
    run["result"] = "FAIL" if run["failures"] else "PASS"

    stamp = start.strftime("%Y%m%d_%H%M%S")
    report_json = os.path.join(EVALS, f"autonomy_{stamp}.json")
    with open(report_json, "w", encoding="utf-8") as fh:
        json.dump(run, fh, ensure_ascii=False, indent=2)

    report_md = os.path.join(EVALS, f"autonomy_{stamp}.md")
    lines = [
        f"# Autonomia + ambiente — {stamp}",
        "",
        f"- Resultado: **{run['result']}**",
        f"- Sessão: {sid}",
        f"- Turnos: {args.idle} idle + {args.player} jogador",
        f"- Consistência: ok={consistency.get('ok')} "
        f"severity={consistency.get('severity')}",
        "",
        "## Autonomia",
        "",
        f"- Eventos do mundo: {len(run['autonomy']['world_events'])}",
        f"- Eventos fora de cena: {len(offscreen_seen)}",
        f"- Vozes do mundo: {len(all_voices)}",
        "",
        "## Falhas",
        "",
    ]
    lines += [f"- {item}" for item in run["failures"]] or ["- nenhuma"]
    lines += ["", "## Avisos", ""]
    lines += [f"- {item}" for item in run["warnings"]] or ["- nenhum"]
    with open(report_md, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print("\n" + "\n".join(lines))
    print(f"[teste] relatório: {report_json}")
    if not args.keep:
        try:
            with httpx.Client(timeout=30) as client:
                client.delete(f"{API}/api/sessions/{sid}")
        except Exception:
            pass
    return 0 if run["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
