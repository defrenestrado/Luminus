import array
import math
import os
import time

import httpx

import llm
import metrics

ENABLED = os.environ.get("LUMINUS_RAG", "1") == "1"
MODEL = os.environ.get("LUMINUS_EMBED_MODEL", "bge-m3")
BASE = os.environ.get("LUMINUS_EMBED_URL", llm.OLLAMA_UTILITY)
HITS = int(os.environ.get("LUMINUS_RAG_HITS", "5"))
MAX_CHARS = int(os.environ.get("LUMINUS_RAG_BUDGET", "1400"))
MIN_SCORE = float(os.environ.get("LUMINUS_RAG_MIN_SCORE", "0.18"))
BATCH = int(os.environ.get("LUMINUS_RAG_BATCH", "8"))


def pack(vector):
    return array.array("f", vector).tobytes()


def unpack(blob):
    values = array.array("f")
    values.frombytes(blob)
    return values


async def _embed(texts):
    texts = [text[:4000] for text in texts]
    # Embeddings rodam na GPU utilitária. Se ela caiu, não usa o Ollama
    # principal: carregar outro modelo ali despejaria o Narrador da VRAM.
    if not llm.utility_available():
        raise RuntimeError("GPU utilitária indisponível; embeddings desativados")
    base = BASE
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(240.0, connect=15.0)
    ) as client:
        response = await client.post(
            f"{base}/api/embed",
            json={"model": MODEL, "input": texts},
        )
        if response.status_code < 400:
            data = response.json()
            vectors = data.get("embeddings")
            if isinstance(vectors, list) and len(vectors) == len(texts):
                return vectors
        vectors = []
        for text in texts:
            response = await client.post(
                f"{base}/api/embeddings",
                json={"model": MODEL, "prompt": text},
            )
            response.raise_for_status()
            vector = response.json().get("embedding")
            if not isinstance(vector, list) or not vector:
                raise ValueError("embedding vazio")
            vectors.append(vector)
        return vectors


async def index_session(conn, sid, limit=1000):
    started = time.perf_counter()
    rows = conn.execute(
        "SELECT m.id, m.content FROM messages m "
        "LEFT JOIN message_vectors v ON v.message_id=m.id AND v.model=? "
        "WHERE m.session_id=? AND v.message_id IS NULL "
        "AND length(m.content)>0 ORDER BY m.id LIMIT ?",
        (MODEL, sid, max(1, int(limit))),
    ).fetchall()
    if not rows:
        return 0
    total = 0
    for start in range(0, len(rows), max(1, BATCH)):
        batch = rows[start:start + max(1, BATCH)]
        vectors = await _embed([row["content"] for row in batch])
        now = time.time()
        for row, vector in zip(batch, vectors):
            conn.execute(
                "INSERT OR REPLACE INTO message_vectors "
                "(message_id, session_id, model, dim, vector, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    row["id"],
                    sid,
                    MODEL,
                    len(vector),
                    pack(vector),
                    now,
                ),
            )
        conn.commit()
        total += len(batch)
    if total:
        metrics.inc("rag_indexed", total)
        metrics.observe(
            "embedding_seconds", time.perf_counter() - started
        )
    return total


async def search(conn, sid, query, limit=HITS, exclude_ids=None):
    if not ENABLED or not (query or "").strip():
        return []
    started = time.perf_counter()
    metrics.inc("rag_searches")
    rows = conn.execute(
        "SELECT v.message_id, v.vector, m.role, m.content "
        "FROM message_vectors v JOIN messages m ON m.id=v.message_id "
        "WHERE v.session_id=? AND v.model=? ORDER BY m.id",
        (sid, MODEL),
    ).fetchall()
    if not rows:
        return []
    query_vector = (await _embed([query]))[0]
    query_norm = math.sqrt(sum(value * value for value in query_vector))
    if not query_norm:
        return []
    excluded = {int(value) for value in (exclude_ids or [])}
    scored = []
    for row in rows:
        if row["message_id"] in excluded:
            continue
        vector = unpack(row["vector"])
        if len(vector) != len(query_vector):
            continue
        norm = math.sqrt(sum(value * value for value in vector))
        if not norm:
            continue
        dot = sum(a * b for a, b in zip(query_vector, vector))
        score = dot / (query_norm * norm)
        if score < MIN_SCORE:
            continue
        scored.append(
            (
                score,
                {
                    "id": row["message_id"],
                    "role": row["role"],
                    "content": row["content"],
                    "score": score,
                },
            )
        )
    scored.sort(key=lambda item: -item[0])
    metrics.inc("rag_hits", len(scored))
    metrics.observe("rag_seconds", time.perf_counter() - started)
    return [item[1] for item in scored[: max(1, int(limit))]]


async def memory_block(conn, sid, query, exclude_ids=None):
    try:
        hits = await search(
            conn, sid, query, limit=HITS, exclude_ids=exclude_ids
        )
    except Exception as exc:
        metrics.inc("rag_errors")
        print("[rag] erro:", exc)
        return ""
    lines = []
    used = 0
    for hit in hits:
        content = (hit["content"] or "").strip().replace("\n", " ")
        if not content:
            continue
        line = f"- [{hit['role']}] {content[:240]}"
        if used + len(line) > MAX_CHARS:
            break
        lines.append(line)
        used += len(line)
    if not lines:
        return ""
    return (
        "## MEMORIA SEMANTICA (trechos antigos por similaridade)\n"
        + "\n".join(lines)
    )
