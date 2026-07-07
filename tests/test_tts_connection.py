"""Tests for test tts connection."""

import io
import sys
import types
import unittest
import wave
from unittest.mock import patch

import numpy as np

from core import tts


def _tiny_wav() -> bytes:
    """Return a small mono PCM WAV for fake TTS responses."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(32000)
        wf.writeframes(b"\x01\x00\x02\x00")
    return buf.getvalue()


class TtsConnectionTests(unittest.TestCase):
    """Test case for tts connection tests behavior."""
    def test_none_provider_reports_disabled(self):
        """Verify none provider reports disabled behavior."""
        ok, message = tts.test_connection("none")

        self.assertTrue(ok)
        self.assertIn("disabled", message)

    def test_cartesia_connection_requires_voice_id(self):
        """Verify cartesia connection requires voice id behavior."""
        ok, message = tts.test_connection(
            "cartesia",
            cartesia_api_key="cartesia-key",
            cartesia_voice_id="",
        )

        self.assertFalse(ok)
        self.assertIn("CARTESIA_VOICE_ID", message)

    def test_elevenlabs_connection_succeeds_when_audio_arrives(self):
        """Verify elevenlabs connection succeeds when audio arrives behavior."""
        class FakeElevenLabs:
            """Test case for fake eleven labs behavior."""
            def __init__(self, api_key):
                """Initialize the fake eleven labs instance."""
                self.api_key = api_key

            def generate(self, **kwargs):
                """Verify generate behavior."""
                yield b"audio"

        fake_module = types.ModuleType("elevenlabs.client")
        fake_module.ElevenLabs = FakeElevenLabs

        with patch.dict(sys.modules, {"elevenlabs.client": fake_module}):
            ok, message = tts.test_connection(
                "elevenlabs",
                elevenlabs_api_key="eleven-key",
            )

        self.assertTrue(ok)
        self.assertIn("elevenlabs", message)

    def test_gpt_sovits_requires_reference_audio(self):
        """Verify GPT-SoVITS connection requires reference audio."""
        ok, message = tts.test_connection(
            "gpt_sovits",
            gpt_sovits_url="http://127.0.0.1:9880",
            gpt_sovits_ref_audio_path="",
        )

        self.assertFalse(ok)
        self.assertIn("GPT_SOVITS_REF_AUDIO_PATH", message)

    def test_gpt_sovits_connection_posts_to_local_api(self):
        """Verify GPT-SoVITS connection posts the expected local API request."""
        calls = []

        class FakeResponse:
            """Fake requests response."""
            status_code = 200
            content = _tiny_wav()
            text = ""

        fake_requests = types.ModuleType("requests")

        def fake_post(url, json, timeout):
            """Capture the post request and return WAV bytes."""
            calls.append({"url": url, "json": json, "timeout": timeout})
            return FakeResponse()

        fake_requests.post = fake_post

        with patch.dict(sys.modules, {"requests": fake_requests}):
            ok, message = tts.test_connection(
                "gpt_sovits",
                gpt_sovits_url="http://127.0.0.1:9880",
                gpt_sovits_ref_audio_path=r"C:\voices\ref.wav",
                gpt_sovits_prompt_text="hello there",
                gpt_sovits_prompt_lang="en",
                gpt_sovits_text_lang="en",
            )

        self.assertTrue(ok)
        self.assertIn("gpt_sovits", message)
        self.assertEqual(calls[0]["url"], "http://127.0.0.1:9880/tts")
        self.assertEqual(calls[0]["json"]["ref_audio_path"], r"C:\voices\ref.wav")
        self.assertEqual(calls[0]["json"]["prompt_text"], "hello there")
        self.assertEqual(calls[0]["json"]["text"], "ok")

    def test_kokoro_requires_voice(self):
        """Verify Kokoro connection requires a voice name."""
        ok, message = tts.test_connection(
            "kokoro",
            kokoro_voice="",
            kokoro_lang_code="a",
        )

        self.assertFalse(ok)
        self.assertIn("KOKORO_VOICE", message)

    def test_kokoro_prewarm_skips_when_not_installed(self):
        """Verify Kokoro prewarm stays quiet until the optional package exists."""
        import config

        with patch.object(config, "TTS_PROVIDER", "kokoro"), \
             patch("core.tts.kokoro_installed", return_value=False), \
             patch("core.tts._stream_kokoro", side_effect=AssertionError("unexpected Kokoro warmup")):
            self.assertIsNone(tts.prewarm())

    def test_kokoro_import_failure_suggests_reinstall(self):
        """Broken Kokoro dependencies should produce a reinstall-oriented error."""
        original_import = __import__

        def fake_import(name, *args, **kwargs):
            if name == "kokoro":
                raise AttributeError("module 'regex' has no attribute 'compile'")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", fake_import):
            with self.assertRaisesRegex(RuntimeError, "reinstall Kokoro"):
                tts._import_kokoro_pipeline()

    def test_kokoro_missing_transitive_module_is_not_reported_as_uninstalled(self):
        """Missing bundled stdlib/native modules should not look like absent Kokoro."""
        original_import = __import__

        def fake_import(name, *args, **kwargs):
            if name == "kokoro":
                raise ModuleNotFoundError("No module named 'cmath'", name="cmath")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", fake_import):
            with self.assertRaisesRegex(RuntimeError, "failed to import.*cmath") as ctx:
                tts._import_kokoro_pipeline()

        self.assertNotIn("not installed", str(ctx.exception))

    def test_prepare_kokoro_assets_downloads_model_and_voice(self):
        """Verify asset preparation checks the local cache first, then fetches at the pin."""
        from core import tts_assets

        calls = []

        fake_hf = types.ModuleType("huggingface_hub")

        def fake_download(*, repo_id, filename, revision=None, local_files_only=False, force_download=False):
            calls.append((repo_id, filename, revision, local_files_only))
            if local_files_only:
                raise FileNotFoundError(filename)  # simulate an empty cache
            return f"cache/{filename}"

        fake_hf.hf_hub_download = fake_download
        pin = tts_assets.KOKORO.revision

        with patch.dict(sys.modules, {"huggingface_hub": fake_hf}), \
             patch("core.tts_assets._check_size", return_value=None), \
             patch("core.tts_assets._load_state", return_value={}), \
             patch("os.path.getsize", return_value=1_000_000):
            paths = tts.prepare_kokoro_assets(voice="af_heart")

        network_calls = [call for call in calls if not call[3]]
        self.assertEqual(
            network_calls,
            [
                ("hexgrad/Kokoro-82M", "config.json", pin, False),
                ("hexgrad/Kokoro-82M", "kokoro-v1_0.pth", pin, False),
                ("hexgrad/Kokoro-82M", "voices/af_heart.pt", pin, False),
            ],
        )
        # Every network fetch is preceded by a local-cache attempt at the pin.
        local_calls = [call for call in calls if call[3]]
        self.assertIn(("hexgrad/Kokoro-82M", "config.json", pin, True), local_calls)
        self.assertIn(("hexgrad/Kokoro-82M", "kokoro-v1_0.pth", pin, True), local_calls)
        self.assertIn(("hexgrad/Kokoro-82M", "voices/af_heart.pt", pin, True), local_calls)
        self.assertEqual(paths["config"], "cache/config.json")
        self.assertEqual(paths["model"], "cache/kokoro-v1_0.pth")
        self.assertEqual(paths["voice:af_heart"], "cache/voices/af_heart.pt")

    def test_prepare_kokoro_assets_skips_network_when_cached(self):
        """Verify asset preparation stays offline when pinned files are already intact."""
        from core import tts_assets

        calls = []

        fake_hf = types.ModuleType("huggingface_hub")

        def fake_download(*, repo_id, filename, revision=None, local_files_only=False, force_download=False):
            calls.append((repo_id, filename, revision, local_files_only))
            if not local_files_only:
                raise AssertionError(f"unexpected network download for {filename}")
            return f"cache/{filename}"

        fake_hf.hf_hub_download = fake_download

        with patch.dict(sys.modules, {"huggingface_hub": fake_hf}), \
             patch("core.tts_assets._check_size", return_value=None), \
             patch("core.tts_assets._load_state", return_value={}), \
             patch("os.path.getsize", return_value=1_000_000):
            paths = tts.prepare_kokoro_assets(voice="af_heart")

        self.assertTrue(all(call[3] for call in calls))
        self.assertEqual(paths["model"], "cache/kokoro-v1_0.pth")

    def test_kokoro_prewarm_never_downloads_and_reports_repair(self):
        """Warmup must fail with repair guidance instead of downloading assets."""
        import config
        from core import tts_assets

        damaged = tts_assets.AssetStatus(
            state="damaged",
            problems=["kokoro-v1_0.pth: expected 327212226 bytes, found 12"],
        )

        with patch.object(config, "TTS_PROVIDER", "kokoro"), \
             patch("core.tts.kokoro_installed", return_value=True), \
             patch("core.tts.verify_kokoro_assets", return_value=damaged), \
             patch("core.tts._stream_kokoro", side_effect=AssertionError("unexpected synthesis")), \
             patch("core.tts_assets._fetch", side_effect=AssertionError("unexpected download")):
            with self.assertRaisesRegex(RuntimeError, "reinstall Kokoro"):
                tts.prewarm()

    def test_kokoro_prewarm_warms_pipeline_only_when_voice_missing(self):
        """A not-yet-downloaded voice defers to user-initiated synthesis, no download."""
        import config
        from core import tts_assets

        status = tts_assets.AssetStatus(
            state="ok",
            paths={"config.json": "cache/config.json", "kokoro-v1_0.pth": "cache/kokoro-v1_0.pth"},
            missing_voices=["af_new"],
        )

        with patch.object(config, "TTS_PROVIDER", "kokoro"), \
             patch("core.tts.kokoro_installed", return_value=True), \
             patch("core.tts.verify_kokoro_assets", return_value=status), \
             patch("core.tts._get_kokoro_pipeline") as get_pipeline, \
             patch("core.tts._stream_kokoro", side_effect=AssertionError("unexpected synthesis")), \
             patch("core.tts_assets._fetch", side_effect=AssertionError("unexpected download")):
            tts.prewarm()

        get_pipeline.assert_called_once()

    def test_kokoro_pipeline_builds_offline_from_local_assets(self):
        """When pinned assets are cached, the model loads from local paths only."""
        built = {}

        class FakeKModel:
            def __init__(self, *, repo_id, config, model):
                built["repo_id"] = repo_id
                built["config"] = config
                built["model"] = model

            def to(self, device):
                built["device"] = device
                return self

            def eval(self):
                return self

        class FakeKPipeline:
            def __init__(self, lang_code, repo_id=None, model=True):
                built["pipeline_model"] = model
                built["lang_code"] = lang_code

        fake_module = types.ModuleType("kokoro")
        fake_module.KPipeline = FakeKPipeline
        fake_module.KModel = FakeKModel

        assets = {"config.json": "cache/config.json", "kokoro-v1_0.pth": "cache/kokoro-v1_0.pth"}

        with patch.dict(sys.modules, {"kokoro": fake_module}), \
             patch("core.tts._locate_kokoro_assets", return_value=assets), \
             patch("core.tts._resolve_kokoro_device", return_value="cpu"):
            pipeline, device, offline = tts._create_kokoro_pipeline("a", "cpu")

        self.assertTrue(offline)
        self.assertEqual(device, "cpu")
        self.assertEqual(built["config"], "cache/config.json")
        self.assertEqual(built["model"], "cache/kokoro-v1_0.pth")
        self.assertIsInstance(built["pipeline_model"], FakeKModel)
        self.assertIsInstance(pipeline, FakeKPipeline)

    def test_kokoro_connection_uses_local_pipeline(self):
        """Verify Kokoro connection uses the local Python pipeline."""
        calls = []

        class FakeKPipeline:
            """Fake Kokoro pipeline."""
            def __init__(self, lang_code):
                """Capture language code."""
                self.lang_code = lang_code

            def __call__(self, text, voice, speed, split_pattern):
                """Capture synthesis args and return float audio."""
                import numpy as np

                calls.append({
                    "lang_code": self.lang_code,
                    "text": text,
                    "voice": voice,
                    "speed": speed,
                    "split_pattern": split_pattern,
                })
                yield text, "phonemes", np.array([0.0, 0.25, -0.25], dtype=np.float32)

        fake_module = types.ModuleType("kokoro")
        fake_module.KPipeline = FakeKPipeline

        with patch.dict(sys.modules, {"kokoro": fake_module}):
            ok, message = tts.test_connection(
                "kokoro",
                kokoro_voice="af_heart",
                kokoro_lang_code="a",
            )

        self.assertTrue(ok)
        self.assertIn("kokoro", message)
        self.assertEqual(calls[0]["text"], "ok")
        self.assertEqual(calls[0]["voice"], "af_heart")
        self.assertEqual(calls[0]["lang_code"], "a")

    def test_kokoro_connection_passes_supported_device(self):
        """Verify Kokoro receives the configured CUDA device when supported."""
        calls = []

        class FakeCuda:
            @staticmethod
            def is_available():
                return True

        fake_torch = types.SimpleNamespace(cuda=FakeCuda())

        class FakeKPipeline:
            """Fake Kokoro pipeline with device support."""
            def __init__(self, lang_code, device="cpu"):
                """Capture language code and device."""
                self.lang_code = lang_code
                self.device = device

            def __call__(self, text, voice, speed, split_pattern):
                """Capture synthesis args and return float audio."""
                import numpy as np

                calls.append({
                    "lang_code": self.lang_code,
                    "device": self.device,
                    "text": text,
                    "voice": voice,
                })
                yield text, "phonemes", np.array([0.0, 0.25, -0.25], dtype=np.float32)

        fake_module = types.ModuleType("kokoro")
        fake_module.KPipeline = FakeKPipeline

        with patch.dict(sys.modules, {"kokoro": fake_module, "torch": fake_torch}):
            ok, message = tts.test_connection(
                "kokoro",
                kokoro_voice="af_heart",
                kokoro_lang_code="a",
                kokoro_device="cuda",
            )

        self.assertTrue(ok)
        self.assertIn("kokoro", message)
        self.assertEqual(calls[0]["device"], "cuda")

    def test_kokoro_connection_falls_back_to_cpu_when_cuda_build_fails(self):
        """Verify a bad CUDA Kokoro init does not fail local TTS outright."""
        calls = []

        class FakeCuda:
            @staticmethod
            def is_available():
                return True

        fake_torch = types.SimpleNamespace(cuda=FakeCuda())

        class FakeKPipeline:
            """Fake Kokoro pipeline that fails on CUDA but works on CPU."""
            def __init__(self, lang_code, device="cpu"):
                """Capture language code and device."""
                if device == "cuda":
                    raise RuntimeError("CUDA failed")
                self.lang_code = lang_code
                self.device = device

            def __call__(self, text, voice, speed, split_pattern):
                """Capture synthesis args and return float audio."""
                import numpy as np

                calls.append({"lang_code": self.lang_code, "device": self.device, "text": text})
                yield text, "phonemes", np.array([0.0, 0.25, -0.25], dtype=np.float32)

        fake_module = types.ModuleType("kokoro")
        fake_module.KPipeline = FakeKPipeline

        with patch.dict(sys.modules, {"kokoro": fake_module, "torch": fake_torch}):
            ok, message = tts.test_connection(
                "kokoro",
                kokoro_voice="af_heart",
                kokoro_lang_code="a",
                kokoro_device="cuda",
            )

        self.assertTrue(ok)
        self.assertIn("kokoro", message)
        self.assertEqual(calls[0]["device"], "cpu")
