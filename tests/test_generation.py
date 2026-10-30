import importlib.util
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("generate_voices", ROOT / "tools" / "generate_voices.py")
generation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generation)


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.row = {"quest_id": 42, "locale": "ruRU", "stage": "detail", "text": "Здравствуй, путник.", "audio": []}

    def test_quest_plan_preserves_text_and_profile(self):
        row = {**self.row, "tts_profile": "sage", "tts_text": "Здр+авствуй, п+утник."}
        plan = generation.plan_quests({"schema_version": 1, "quests": [row]}, "commander")
        self.assertEqual(plan[0][0]["audio"], ["ruRU/42/detail.ogg"])
        self.assertEqual(plan[0][0]["text"], row["text"])
        self.assertEqual(plan[0][1:], (row["tts_text"], "sage"))
        self.assertEqual(row["audio"], [])

    def test_invalid_plan_rejected_before_rendering(self):
        for override in ({"quest_id": True}, {"locale": "enUS"}, {"stage": "wrong"}, {"text": ""}, {"tts_profile": "unknown"}, {"audio": ["existing.ogg"]}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                generation.plan_quests({"schema_version": 1, "quests": [{**self.row, **override}]}, "commander")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            generation.plan_quests({"schema_version": 1, "quests": [self.row, self.row]}, "commander")

    def test_ssml_escapes_quest_text(self):
        result = generation.make_ssml('Вперёд <герой> & "друг"!', "commander")
        self.assertIn("&lt;герой&gt; &amp;", result)
        self.assertIn('rate="slow"', result)
        self.assertNotIn("<герой>", result)

    def test_gender_variants_have_distinct_recording_paths(self):
        rows = [{**self.row, "gender": gender} for gender in ("male", "female")]
        plan = generation.plan_quests({"schema_version": 1, "quests": rows}, "loa")
        self.assertEqual([row["audio"][0] for row, _, _ in plan], ["ruRU/42/detail_male.ogg", "ruRU/42/detail_female.ogg"])

    def test_long_text_is_not_truncated(self):
        text = "Длинная реплика для проверки. " * 60
        parts = generation.split_text(text)
        self.assertGreater(len(parts), 1)
        self.assertEqual(" ".join(parts), " ".join(text.split()))
        self.assertTrue(all(len(part) <= 350 for part in parts))

    def test_model_checksum_and_origin_are_checked(self):
        model = self.root / "model.pt"
        model.write_bytes(b"test model fixture, not a real speech model")
        receipt = {"url": generation.MODEL_URL, "sha256": generation.sha256(model)}
        generation.receipt_path(model).write_text(json.dumps(receipt))
        self.assertEqual(generation.verify_model(model), receipt)
        model.write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "checksum"):
            generation.verify_model(model)

    def test_failed_download_leaves_no_model_or_receipt(self):
        model = self.root / "model.pt"
        with patch.object(generation.urllib.request, "urlopen", side_effect=urllib.error.URLError("403 Forbidden")):
            with self.assertRaises(urllib.error.URLError):
                generation.fetch_model(model)
        self.assertFalse(model.exists())
        self.assertFalse(generation.receipt_path(model).exists())

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required for encoder integration")
    def test_encoder_and_resume_with_non_speech_fixture(self):
        # This validates the real audio encoder, not the unavailable speech model.
        class Signal(list):
            def tolist(self):
                return self

        class SynthFixture:
            def __init__(self):
                self.calls = 0

            def apply_tts(self, **kwargs):
                self.calls += 1
                return Signal(0.2 * math.sin(2 * math.pi * 220 * index / 48000) for index in range(24000))

        model = SynthFixture()
        output = self.root / "fixture.ogg"
        generation.render(model, "Тест", "commander", output, "test-digest")
        self.assertTrue(output.read_bytes().startswith(b"OggS"))
        generation.render(model, "Тест", "commander", output, "test-digest")
        self.assertEqual(model.calls, 1)
        with self.assertRaisesRegex(ValueError, "differs"):
            generation.render(model, "Другой текст", "commander", output, "test-digest")
        probe = shutil.which("ffprobe")
        if probe:
            result = subprocess.run([probe, "-v", "error", "-show_entries", "stream=codec_name,sample_rate,channels", "-of", "json", str(output)], check=True, capture_output=True, text=True)
            audio = json.loads(result.stdout)["streams"][0]
            self.assertEqual(audio, {"codec_name": "vorbis", "sample_rate": "48000", "channels": 1})


if __name__ == "__main__":
    unittest.main()
