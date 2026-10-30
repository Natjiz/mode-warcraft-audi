import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_akunda import build


class VoiceVariantTests(unittest.TestCase):
    def test_new_bundle_uses_new_recordings_and_preserves_original_demo(self):
        original = (ROOT / "addon/WarcraftAudi/Audio/demo.ogg").read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "trial.zip"
            build(output, "mature")
            with zipfile.ZipFile(output) as archive:
                self.assertIsNone(archive.testzip())
                expected = (ROOT / "assets/akunda_mature/ruRU/50913/detail_male.ogg").read_bytes()
                self.assertEqual(archive.read("WarcraftAudi/Audio/demo.ogg"), expected)
                self.assertNotEqual(hashlib.sha256(expected).digest(), hashlib.sha256(original).digest())
                self.assertIn(b"## Version: 0.2.1", archive.read("WarcraftAudi_Akunda_ruRU/WarcraftAudi_Akunda_ruRU.toc"))
                for name in ("detail_male", "detail_female", "progress", "complete"):
                    path = f"ruRU/50913/{name}.ogg"
                    self.assertEqual(archive.read("WarcraftAudi_Akunda_ruRU/Audio/" + path), (ROOT / "assets/akunda_mature" / path).read_bytes())
        self.assertEqual((ROOT / "addon/WarcraftAudi/Audio/demo.ogg").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
