"""Testes do vigia de GPUs e do failover de modelo (sem hardware real)."""

import asyncio
import unittest
from unittest import mock

import gpu_guard as gg


def _devices(main=3, utility=True, main_errors=None):
    devices = []
    names = "NVIDIA GeForce RTX 3060 Ti"
    for index in range(main):
        devices.append(
            gg.GpuDevice(
                index=index,
                uuid=f"GPU-main-{index}",
                pci_bus=f"0000:0{index + 1}:00.0",
                name=names,
                total_mib=8192,
                free_mib=7000,
                healthy=True,
            )
        )
    if main_errors:
        for index in main_errors:
            device = devices[index]
            device.healthy = False
            device.error = "Unknown Error"
    if utility:
        devices.append(
            gg.GpuDevice(
                index=2,
                uuid="GPU-utility",
                pci_bus="0000:05:00.0",
                name="NVIDIA GeForce RTX 2060",
                total_mib=12288,
                free_mib=11000,
                healthy=True,
            )
        )
    return devices


class TierSelectionTest(unittest.TestCase):
    def setUp(self):
        self.guard = gg.GpuGuard(probe=lambda: (_devices(), ""))

    def test_full_with_three_main_gpus(self):
        assessment = self.guard._build(_devices())
        self.assertEqual(assessment.tier, "full")
        self.assertEqual(assessment.model, "luminus-27b")
        self.assertTrue(assessment.healthy)

    def test_two_gpus_uses_smaller_model(self):
        assessment = self.guard._build(_devices(main=2))
        self.assertEqual(assessment.tier, "two_gpu")
        self.assertEqual(assessment.model, "qwen38-9b-heretic:latest")
        self.assertFalse(assessment.healthy)
        self.assertIn("2/3", assessment.reason)

    def test_one_gpu_uses_q5(self):
        assessment = self.guard._build(_devices(main=1))
        self.assertEqual(assessment.tier, "one_gpu")
        self.assertEqual(assessment.model, "qwen38-9b-heretic-q5")
        self.assertEqual(assessment.num_ctx, 8192)

    def test_no_main_but_utility(self):
        assessment = self.guard._build(_devices(main=0, utility=True))
        self.assertEqual(assessment.tier, "utility")
        self.assertEqual(assessment.model, self.guard.utility_model)

    def test_no_gpus_goes_cpu(self):
        assessment = self.guard._build([])
        self.assertEqual(assessment.tier, "cpu")
        self.assertEqual(assessment.model, self.guard.cpu_model)

    def test_error_marker_invalidates_gpu(self):
        devices = _devices(main_errors=[1])
        assessment = self.guard._build(devices)
        self.assertEqual(assessment.main_healthy, 2)
        self.assertEqual(assessment.tier, "two_gpu")
        self.assertIn("Unknown Error", assessment.reason)

    def test_missing_utility_does_not_degrade_main(self):
        assessment = self.guard._build(_devices(main=3, utility=False))
        self.assertEqual(assessment.tier, "full")
        self.assertEqual(assessment.main_healthy, 3)
        self.assertFalse(assessment.healthy)
        self.assertIn("2060", assessment.reason)


class HysteresisTest(unittest.IsolatedAsyncioTestCase):
    async def test_degrades_only_after_threshold(self):
        state = {"devices": _devices()}
        guard = gg.GpuGuard(probe=lambda: (state["devices"], ""))
        await guard.evaluate(force=True)
        self.assertEqual(guard.tier, "full")

        state["devices"] = _devices(main=2)
        await guard.evaluate()
        self.assertEqual(guard.tier, "full")  # primeira leitura ruim
        await guard.evaluate()
        self.assertEqual(guard.tier, "two_gpu")  # segunda confirma

    async def test_recovers_only_after_threshold(self):
        state = {"devices": _devices(main=2)}
        guard = gg.GpuGuard(probe=lambda: (state["devices"], ""))
        await guard.evaluate(force=True)
        self.assertEqual(guard.tier, "two_gpu")

        state["devices"] = _devices()
        await guard.evaluate()
        self.assertEqual(guard.tier, "two_gpu")
        await guard.evaluate()
        self.assertEqual(guard.tier, "two_gpu")
        await guard.evaluate()
        self.assertEqual(guard.tier, "full")

    async def test_model_failure_degrades_immediately(self):
        guard = gg.GpuGuard(probe=lambda: (_devices(), ""))
        await guard.evaluate(force=True)
        self.assertEqual(guard.tier, "full")
        await guard.note_model_failure("luminus-27b", "CUDA error: device lost")
        self.assertEqual(guard.tier, "two_gpu")
        self.assertIn("CUDA", guard.status()["reason"])

    async def test_callback_receives_new_model(self):
        state = {"devices": _devices()}
        seen = []
        guard = gg.GpuGuard(
            probe=lambda: (state["devices"], ""),
            on_change=lambda assessment: seen.append(assessment.model),
        )
        await guard.evaluate(force=True)
        state["devices"] = _devices(main=2)
        await guard.evaluate(force=True)
        self.assertEqual(seen[-1], "qwen38-9b-heretic:latest")

    async def test_probe_failure_keeps_primary(self):
        guard = gg.GpuGuard(probe=lambda: ([], "nvidia-smi não encontrado"))
        assessment = await guard.evaluate(force=True)
        self.assertEqual(assessment.tier, "full")
        self.assertTrue(assessment.degraded)
        self.assertIn("nvidia-smi", assessment.reason)


class FatalCudaAndRestartRequestTest(unittest.TestCase):
    def test_fatal_markers(self):
        self.assertTrue(
            gg.is_fatal_cuda_error("CUDA error: unspecified launch failure")
        )
        self.assertTrue(
            gg.is_fatal_cuda_error("failed to initialize CUDA: unknown error")
        )
        self.assertFalse(gg.is_fatal_cuda_error("context length exceeded"))

    def test_restart_request_file(self):
        import tempfile
        from pathlib import Path as _Path

        with tempfile.TemporaryDirectory() as tmp:
            ok = gg.request_service_restart(
                "ollama", "CUDA error", request_dir=tmp
            )
            self.assertTrue(ok)
            path = _Path(tmp) / "restart-ollama.request"
            self.assertTrue(path.exists())
            lines = path.read_text().splitlines()
            self.assertTrue(lines[0].isdigit())
            self.assertEqual(lines[1], "ollama")
            self.assertIn("CUDA", lines[2])

    def test_restart_request_without_helper(self):
        self.assertFalse(
            gg.request_service_restart(
                "ollama", "x", request_dir="/tmp/does-not-exist-luminus-guard"
            )
        )


class ProbeParsingTest(unittest.TestCase):
    def test_parse_csv_with_n_a(self):
        stdout = (
            "0, GPU-1, 0000:01:00.0, NVIDIA GeForce RTX 3060 Ti, 8192, 7000\n"
            "1, GPU-2, 0000:02:00.0, NVIDIA GeForce RTX 3060 Ti, N/A, N/A\n"
        )
        fake = mock.Mock(returncode=0, stdout=stdout, stderr="")
        with mock.patch("gpu_guard.subprocess.run", return_value=fake):
            devices, error = gg.probe_gpus()
        self.assertEqual(error, "")
        self.assertEqual(len(devices), 2)
        self.assertTrue(devices[0].healthy)
        self.assertFalse(devices[1].healthy)
        self.assertIn("VRAM", devices[1].error)

    def test_unknown_error_marks_unhealthy(self):
        stdout = (
            "0, GPU-1, 0000:01:00.0, NVIDIA GeForce RTX 3060 Ti, 8192, 7000\n"
            "2, GPU-9, 0000:05:00.0, [Unknown Error], 0, 0\n"
        )
        fake = mock.Mock(returncode=0, stdout=stdout, stderr="")
        with mock.patch("gpu_guard.subprocess.run", return_value=fake):
            devices, _ = gg.probe_gpus()
        self.assertFalse(devices[1].healthy)
        self.assertIn("unknown error", devices[1].error.lower())

    def test_command_failure_returns_error(self):
        fake = mock.Mock(returncode=9, stdout="", stderr="boom")
        with mock.patch("gpu_guard.subprocess.run", return_value=fake):
            devices, error = gg.probe_gpus()
        self.assertEqual(devices, [])
        self.assertIn("boom", error)


class LlmStreamFailoverTest(unittest.IsolatedAsyncioTestCase):
    async def test_cuda_error_retries_smaller_model_same_pool(self):
        import httpx

        import llm

        calls = []
        hooks = []
        unloaded = []

        async def fake_stream(model, base, messages, think, options):
            calls.append((model, base))
            if model == llm.DEFAULT_MODEL:
                request = httpx.Request("POST", "http://fake/api/chat")
                response = httpx.Response(
                    500,
                    request=request,
                    text="CUDA error: out of memory",
                )
                raise httpx.HTTPStatusError(
                    "500", request=request, response=response
                )
            yield "texto reserva"

        async def fake_unload(model, base=None):
            unloaded.append(model)

        llm.reset_primary()
        llm.set_utility_available(True)
        llm.set_main_failover_model("qwen38-9b-heretic:latest")
        llm.set_gpu_error_hook(hooks.append)
        with mock.patch.object(llm, "_stream_one", fake_stream), mock.patch.object(
            llm, "unload", fake_unload
        ):
            chunks = []
            async for chunk in llm.stream_chat(
                llm.DEFAULT_MODEL, [{"role": "user", "content": "oi"}]
            ):
                chunks.append(chunk)

        self.assertEqual("".join(chunks), "texto reserva")
        self.assertEqual([call[0] for call in calls][:2], [
            llm.DEFAULT_MODEL,
            "qwen38-9b-heretic:latest",
        ])
        self.assertIn(llm.DEFAULT_MODEL, unloaded)
        self.assertTrue(hooks)
        self.assertTrue(llm.primary_ok())
        llm.set_gpu_error_hook(None)


class WarmupUtilityCpuFallbackTest(unittest.IsolatedAsyncioTestCase):
    async def _run(self, vrams):
        import main
        import llm

        calls = {"warm": 0, "unload": 0}

        async def loaded(base=None):
            return []

        async def warm(model, ctx, base=None):
            calls["warm"] += 1

        async def unload(model, base=None):
            calls["unload"] += 1

        async def model_vram(model, base=None):
            return vrams.pop(0) if vrams else 0

        with mock.patch.object(llm, "loaded", loaded), mock.patch.object(
            llm, "warm", warm
        ), mock.patch.object(llm, "unload", unload), mock.patch.object(
            llm, "model_vram", model_vram
        ):
            main._gpu_guard = None
            main._utility_model_cpu_only = False
            llm.set_utility_available(True)
            main._warm_state.update(utility="idle", error="")
            await main._warm_one(
                llm.OLLAMA_UTILITY, "qwen38-9b-heretic-q5", 8192, "utility"
            )
        return calls, main, llm

    async def test_retries_once_then_uses_gpu(self):
        calls, main, llm = await self._run([0, 5_000_000_000])
        self.assertEqual(main._warm_state["utility"], "ready")
        self.assertTrue(llm.utility_available())
        self.assertFalse(main._utility_model_cpu_only)
        self.assertEqual(calls["warm"], 2)
        self.assertEqual(calls["unload"], 1)

    async def test_cpu_only_disables_auxiliaries_but_enters(self):
        calls, main, llm = await self._run([0, 0])
        self.assertEqual(main._warm_state["utility"], "ready")
        self.assertFalse(llm.utility_available())
        self.assertTrue(main._utility_model_cpu_only)
        self.assertIn("CPU", main._warm_state["error"])
        llm.set_utility_available(True)
        main._utility_model_cpu_only = False


class LlmFailoverHelpersTest(unittest.TestCase):
    def test_is_gpu_error(self):
        import llm

        self.assertTrue(llm.is_gpu_error("CUDA error: out of memory"))
        self.assertTrue(llm.is_gpu_error("llama-server GPU discovery watchdog"))
        self.assertFalse(llm.is_gpu_error("context length exceeded"))

    def test_utility_availability_flag(self):
        import llm

        llm.set_utility_available(True)
        self.assertTrue(llm.utility_available())
        self.assertEqual(llm.utility_base(), llm.OLLAMA_UTILITY)
        llm.set_utility_available(False)
        self.assertFalse(llm.utility_available())
        # A URL não muda: os chamadores devem desistir do auxiliar e não
        # carregar outro modelo no Ollama principal.
        self.assertEqual(llm.utility_base(), llm.OLLAMA_UTILITY)
        llm.set_utility_available(True)

    def test_candidates_include_2gpu_and_utility(self):
        import llm

        llm.set_main_failover_model("qwen38-9b-heretic:latest")
        llm.reset_primary()
        candidates = llm._candidates(llm.DEFAULT_MODEL)
        models = [name for name, _ in candidates]
        self.assertEqual(models[0], llm.DEFAULT_MODEL)
        self.assertIn("qwen38-9b-heretic:latest", models)
        self.assertIn(llm.UTILITY_MODEL, models)


if __name__ == "__main__":
    unittest.main()
