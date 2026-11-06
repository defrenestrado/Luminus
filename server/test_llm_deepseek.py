"""Testes do Narrador de emergência via DeepSeek (failover remoto)."""

import asyncio
import os
import unittest
from unittest import mock

import llm


ENV = {
    "DEEPSEEK_API_KEY": "test-key",
    "DEEPSEEK_MODEL": "deepseek-flash",
    "LUMINUS_DEEPSEEK_FALLBACK": "1",
    "LUMINUS_DEEPSEEK_MODEL": "deepseek-flash",
    "LUMINUS_DEEPSEEK_BUDGET_USD": "0",
    "LUMINUS_DEEPSEEK_THINKING": "0",
}


def _reset_usage():
    with llm._deepseek_usage_lock:
        llm._deepseek_usage.update(
            {
                "requests": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cache_hit_tokens": 0,
                "cost_usd": 0.0,
                "last_at": 0.0,
            }
        )
    llm._deepseek_budget_warned = False


class DeepSeekCandidatesTest(unittest.TestCase):
    def setUp(self):
        _reset_usage()
        self.env = mock.patch.dict(os.environ, ENV, clear=False)
        self.env.start()
        llm.set_main_failover_model("qwen38-9b-heretic:latest")
        llm.reset_primary()
        llm.set_utility_available(True)

    def tearDown(self):
        self.env.stop()
        llm.set_force_deepseek(False)
        llm.set_utility_available(True)
        llm.reset_primary()

    def test_last_candidate_when_local_pool_is_healthy(self):
        candidates = llm._candidates(llm.DEFAULT_MODEL)
        self.assertEqual(candidates[-1], (llm._deepseek_model(), llm.DEEPSEEK_BASE))
        self.assertNotEqual(candidates[0][1], llm.DEEPSEEK_BASE)

    def test_utility_then_deepseek_when_primary_is_down(self):
        llm.disable_primary()
        candidates = llm._candidates(llm.DEFAULT_MODEL)
        self.assertEqual(candidates[-1][1], llm.DEEPSEEK_BASE)
        self.assertNotEqual(candidates[0][1], llm.DEEPSEEK_BASE)

    def test_deepseek_first_when_no_gpu_pool(self):
        llm.disable_primary()
        llm.set_utility_available(False)
        candidates = llm._candidates(llm.DEFAULT_MODEL)
        self.assertEqual(
            candidates[0], (llm._deepseek_model(), llm.DEEPSEEK_BASE)
        )
        # CPU local continua como último recurso se o DeepSeek também falhar.
        self.assertNotEqual(candidates[-1][1], llm.DEEPSEEK_BASE)

    def test_no_key_disables_route(self):
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}, clear=False):
            candidates = llm._candidates(llm.DEFAULT_MODEL)
            self.assertFalse(llm.deepseek_status()["active"])
        self.assertNotIn(llm.DEEPSEEK_BASE, [base for _, base in candidates])

    def test_budget_exceeded_disables_route(self):
        _reset_usage()
        with mock.patch.dict(
            os.environ, {"LUMINUS_DEEPSEEK_BUDGET_USD": "0.000001"}, clear=False
        ):
            llm._deepseek_usage["cost_usd"] = 1.0
            self.assertFalse(llm._deepseek_enabled())
            candidates = llm._candidates(llm.DEFAULT_MODEL)
        self.assertNotIn(llm.DEEPSEEK_BASE, [base for _, base in candidates])

    def test_force_deepseek_bypasses_local(self):
        llm.set_force_deepseek(True)
        candidates = llm._candidates(llm.DEFAULT_MODEL)
        self.assertEqual(candidates, [(llm._deepseek_model(), llm.DEEPSEEK_BASE)])
        self.assertTrue(llm.deepseek_status()["forced"])

    def test_status_reports_model_and_prices(self):
        status = llm.deepseek_status()
        self.assertTrue(status["configured"])
        self.assertTrue(status["active"])
        self.assertEqual(status["model"], "deepseek-flash")
        self.assertIn("output_per_m", status["prices"])
        self.assertEqual(status["usage"]["scope"], "process")


class DeepSeekProtocolTest(unittest.TestCase):
    def test_parse_text_delta(self):
        line = (
            'data: {"choices":[{"delta":{"content":"Olá"},'
            '"finish_reason":null}]}'
        )
        text, usage, done = llm._parse_deepseek_sse(line)
        self.assertEqual(text, "Olá")
        self.assertIsNone(usage)
        self.assertFalse(done)

    def test_parse_usage_and_done(self):
        line = (
            'data: {"choices":[],"usage":{"prompt_tokens":10,'
            '"completion_tokens":2}}'
        )
        text, usage, done = llm._parse_deepseek_sse(line)
        self.assertEqual(text, "")
        self.assertEqual(usage["prompt_tokens"], 10)
        self.assertFalse(done)
        self.assertTrue(llm._parse_deepseek_sse("data: [DONE]")[2])
        self.assertEqual(llm._parse_deepseek_sse(": keep-alive"), ("", None, False))
        self.assertEqual(llm._parse_deepseek_sse("data: not-json"), ("", None, False))

    def test_payload(self):
        payload = llm._deepseek_payload(
            "deepseek-flash",
            [{"role": "user", "content": "oi"}],
            stream=True,
            options={"temperature": 0.7, "num_predict": 512, "top_p": 0.9},
        )
        self.assertEqual(payload["model"], "deepseek-flash")
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["max_tokens"], 512)
        self.assertEqual(payload["temperature"], 0.7)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertNotIn("response_format", payload)

    def test_payload_json_format(self):
        payload = llm._deepseek_payload(
            "deepseek-flash",
            [{"role": "user", "content": "oi"}],
            stream=False,
            fmt={"type": "object"},
        )
        self.assertEqual(payload["response_format"], {"type": "json_object"})


class DeepSeekUsageTest(unittest.TestCase):
    def setUp(self):
        _reset_usage()

    def test_cost_off_peak(self):
        prices = {
            "peak": False,
            "input_cache_miss_per_m": 0.15,
            "input_cache_hit_per_m": 0.003,
            "output_per_m": 0.60,
        }
        with mock.patch.object(llm, "_deepseek_prices", return_value=prices):
            llm._record_deepseek_usage(
                usage={
                    "prompt_tokens": 1_000_000,
                    "completion_tokens": 1_000_000,
                    "prompt_cache_hit_tokens": 0,
                }
            )
        self.assertAlmostEqual(llm._deepseek_usage["cost_usd"], 0.75, places=6)
        self.assertEqual(llm._deepseek_usage["requests"], 1)

    def test_cache_hit_is_cheaper(self):
        prices = {
            "peak": False,
            "input_cache_miss_per_m": 0.15,
            "input_cache_hit_per_m": 0.003,
            "output_per_m": 0.60,
        }
        with mock.patch.object(llm, "_deepseek_prices", return_value=prices):
            llm._record_deepseek_usage(
                usage={
                    "prompt_tokens": 1_000_000,
                    "completion_tokens": 0,
                    "prompt_cache_hit_tokens": 1_000_000,
                }
            )
        self.assertAlmostEqual(llm._deepseek_usage["cost_usd"], 0.003, places=6)


class DeepSeekDispatchTest(unittest.IsolatedAsyncioTestCase):
    async def test_stream_one_dispatches_to_deepseek(self):
        async def fake_stream(model, messages, options):
            yield "a"
            yield "b"

        with mock.patch.object(llm, "_stream_deepseek", fake_stream):
            chunks = []
            async for chunk in llm._stream_one(
                "deepseek-flash", llm.DEEPSEEK_BASE, [], False, {}
            ):
                chunks.append(chunk)
        self.assertEqual(chunks, ["a", "b"])

    async def test_complete_dispatches_to_deepseek(self):
        async def fake_complete(model, messages, options, fmt):
            return "ok"

        with mock.patch.object(llm, "_complete_deepseek", fake_complete):
            out = await llm.complete(
                "deepseek-flash",
                [{"role": "user", "content": "oi"}],
                base=llm.DEEPSEEK_BASE,
            )
        self.assertEqual(out, "ok")


class _FakeLines:
    """Async iterator que simula um stream que pode ficar mudo."""

    def __init__(self, lines, stall_at=None, delay=1.0):
        self._lines = list(lines)
        self._stall_at = stall_at
        self._delay = delay
        self._i = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._stall_at is not None and self._i >= self._stall_at:
            await asyncio.sleep(self._delay)
        if self._i >= len(self._lines):
            raise StopAsyncIteration
        item = self._lines[self._i]
        self._i += 1
        return item


class StreamWatchdogTest(unittest.IsolatedAsyncioTestCase):
    async def _collect(self, lines, **kwargs):
        out = []
        async for line in llm._iter_lines_watchdog(lines, **kwargs):
            out.append(line)
        return out

    async def test_passes_lines_through(self):
        out = await self._collect(
            _FakeLines(["a", "b"]), first_timeout=1.0, idle_timeout=1.0
        )
        self.assertEqual(out, ["a", "b"])

    async def test_aborts_when_stream_stalls_after_first_token(self):
        with self.assertRaises(RuntimeError) as ctx:
            await self._collect(
                _FakeLines(["a"], stall_at=1, delay=1.0),
                first_timeout=1.0,
                idle_timeout=0.05,
            )
        self.assertIn("parou de responder", str(ctx.exception))

    async def test_aborts_when_first_token_never_arrives(self):
        with self.assertRaises(RuntimeError):
            await self._collect(
                _FakeLines([], stall_at=0, delay=1.0),
                first_timeout=0.05,
                idle_timeout=1.0,
            )


if __name__ == "__main__":
    unittest.main()
