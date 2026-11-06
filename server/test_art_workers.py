"""Pool de workers ComfyUI: parsing, selecao, fallback e autostart."""

import unittest
from unittest import mock

import art


class WorkerParsingTest(unittest.TestCase):
    def test_parses_url_gpu_unit(self):
        worker = art._parse_worker(
            "http://127.0.0.1:8189@0@luminus-comfy-gpu0"
        )
        self.assertEqual(worker.url, "http://127.0.0.1:8189")
        self.assertEqual(worker.gpu, 0)
        self.assertEqual(worker.unit, "luminus-comfy-gpu0")
        self.assertFalse(worker.primary)
        self.assertIs(worker.busy, False)

    def test_parses_url_only_and_strips_slash(self):
        worker = art._parse_worker("http://127.0.0.1:8191/")
        self.assertEqual(worker.url, "http://127.0.0.1:8191")
        self.assertIsNone(worker.gpu)
        self.assertIsNone(worker.unit)

    def test_empty_is_ignored(self):
        self.assertIsNone(art._parse_worker("  "))
        self.assertIsNone(art._parse_worker("@0@unit"))

    def test_builders_keep_primary_first_and_dedupe(self):
        workers = art._build_workers(
            primary_url="http://127.0.0.1:8188",
            pool_raw=(
                "http://127.0.0.1:8189@0@u0,"
                "http://127.0.0.1:8188,"
                "http://127.0.0.1:8190@1@u1"
            ),
        )
        self.assertEqual(
            [w.url for w in workers],
            ["http://127.0.0.1:8188", "http://127.0.0.1:8189",
             "http://127.0.0.1:8190"],
        )
        self.assertTrue(workers[0].primary)
        self.assertFalse(workers[1].primary)


class WorkerSelectionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = [
            art.ComfyWorker("http://127.0.0.1:8189", gpu=0),
            art.ComfyWorker("http://127.0.0.1:8190", gpu=1),
        ]
        self.patch = mock.patch.object(art, "_WORKERS", self.pool)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    async def test_picks_worker_with_smallest_queue(self):
        queues = {
            "http://127.0.0.1:8189": 2,
            "http://127.0.0.1:8190": 0,
        }

        async def probe(worker):
            return queues[worker.url]

        with mock.patch.object(art, "_probe_worker", probe):
            worker = await art._pick_worker()
        self.assertEqual(worker.url, "http://127.0.0.1:8190")
        self.assertTrue(worker.busy)

    async def test_skips_offline_and_excluded(self):
        async def probe(worker):
            return None if worker.url.endswith("8189") else 0

        with mock.patch.object(art, "_probe_worker", probe):
            worker = await art._pick_worker()
        self.assertEqual(worker.url, "http://127.0.0.1:8190")
        self.assertGreater(self.pool[0].down_until, 0)

        with mock.patch.object(art, "_probe_worker", probe):
            self.assertIsNone(
                await art._pick_worker({"http://127.0.0.1:8190"})
            )

    async def test_returns_none_when_everything_is_down(self):
        async def probe(worker):
            return None

        with mock.patch.object(art, "_probe_worker", probe):
            self.assertIsNone(await art._pick_worker())


class RenderFallbackTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.primary = art.ComfyWorker(
            "http://127.0.0.1:8188", gpu=2, primary=True
        )
        self.pool = art.ComfyWorker(
            "http://127.0.0.1:8189", gpu=0, unit="luminus-comfy-gpu0"
        )
        self.patch = mock.patch.object(
            art, "_WORKERS", [self.primary, self.pool]
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)

    async def test_uses_pool_worker_when_primary_is_down(self):
        calls = []

        async def probe(worker):
            return None if worker.primary else 0

        async def fake_generate(url, *args, **kwargs):
            calls.append(url)
            return b"image"

        with mock.patch.object(art, "_probe_worker", probe), \
                mock.patch.object(art, "_comfy_generate", fake_generate):
            data = await art._render_comfy("p", 1, 512, 512, None)
        self.assertEqual(data, b"image")
        self.assertEqual(calls, ["http://127.0.0.1:8189"])
        self.assertFalse(self.pool.busy)

    async def test_falls_back_to_primary_when_pool_fails(self):
        calls = []
        primary_calls = []

        async def probe(worker):
            return None if worker.primary else 0

        async def fake_generate(url, *args, **kwargs):
            calls.append(url)
            raise art.ArtError(502, "boom")

        async def fake_primary(*args, **kwargs):
            primary_calls.append(1)
            return b"image"

        with mock.patch.object(art, "_probe_worker", probe), \
                mock.patch.object(art, "_comfy_generate", fake_generate), \
                mock.patch.object(art, "_render_primary", fake_primary):
            data = await art._render_comfy("p", 1, 512, 512, None)
        self.assertEqual(data, b"image")
        self.assertEqual(calls, ["http://127.0.0.1:8189"])
        self.assertEqual(len(primary_calls), 1)
        self.assertFalse(self.pool.busy)


    async def test_frees_pool_vram_after_render(self):
        freed = []

        async def probe(worker):
            return None if worker.primary else 0

        async def fake_generate(url, *args, **kwargs):
            return b"image"

        async def fake_free(url):
            freed.append(url)

        with mock.patch.object(art, "_probe_worker", probe), \
                mock.patch.object(art, "_comfy_generate", fake_generate), \
                mock.patch.object(art, "_comfy_free", fake_free), \
                mock.patch.object(art, "COMFY_FREE_AFTER", True):
            await art._render_comfy("p", 1, 512, 512, None)
        self.assertEqual(freed, ["http://127.0.0.1:8189"])

    async def test_does_not_free_when_disabled_and_not_autostarted(self):
        freed = []

        async def probe(worker):
            return None if worker.primary else 0

        async def fake_generate(url, *args, **kwargs):
            return b"image"

        async def fake_free(url):
            freed.append(url)

        with mock.patch.object(art, "_probe_worker", probe), \
                mock.patch.object(art, "_comfy_generate", fake_generate), \
                mock.patch.object(art, "_comfy_free", fake_free), \
                mock.patch.object(art, "COMFY_FREE_AFTER", False):
            await art._render_comfy("p", 1, 512, 512, None)
        self.assertEqual(freed, [])


class AutostartTest(unittest.IsolatedAsyncioTestCase):
    async def test_autostart_is_off_by_default(self):
        worker = art.ComfyWorker(
            "http://127.0.0.1:8189", gpu=0, unit="luminus-comfy-gpu0"
        )
        with mock.patch.object(art, "_WORKERS", [worker]), \
                mock.patch.object(art, "COMFY_AUTOSTART", False), \
                mock.patch.object(art, "_ollama_busy", mock.AsyncMock(return_value=False)):
            self.assertIsNone(await art._autostart_worker(set()))

    async def test_autostart_respects_busy_narrator(self):
        worker = art.ComfyWorker(
            "http://127.0.0.1:8189", gpu=0, unit="luminus-comfy-gpu0"
        )
        with mock.patch.object(art, "_WORKERS", [worker]), \
                mock.patch.object(art, "COMFY_AUTOSTART", True), \
                mock.patch.object(art, "_ollama_busy", mock.AsyncMock(return_value=True)):
            self.assertIsNone(await art._autostart_worker(set()))


if __name__ == "__main__":
    unittest.main()
