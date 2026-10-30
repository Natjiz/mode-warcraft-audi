import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

try:
    from lupa.lua51 import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_addon", ROOT / "tools" / "package_addon.py")
packaging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packaging)


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.audio = self.root / "audio"
        self.audio.mkdir()
        # Header fixture tests packaging, not audio decoding or playback.
        (self.audio / "clip.ogg").write_bytes(b"OggS" + bytes(20))
        self.source = self.root / "quests.json"
        self.row = {"quest_id": 42, "stage": "detail", "locale": "ruRU", "audio": ["clip.ogg"]}
        self.output = self.root / "pack.zip"

    def build(self, rows=None, name="Test_ruRU"):
        self.source.write_text(json.dumps({"schema_version": 1, "quests": rows or [self.row]}), encoding="utf-8")
        return packaging.build_pack(self.source, self.audio, name, self.output)

    def test_base_archive_has_installable_layout_and_interface(self):
        packaging.build_addon(self.output)
        with zipfile.ZipFile(self.output) as archive:
            self.assertTrue({"WarcraftAudi/WarcraftAudi.toc", "WarcraftAudi/Core.lua", "WarcraftAudi/UI.lua"}.issubset(archive.namelist()))
            self.assertIn(b"## Interface: 100207", archive.read("WarcraftAudi/WarcraftAudi.toc"))

    def test_pack_manifest_and_skipping_unrecorded_stages(self):
        missing = {**self.row, "stage": "progress", "audio": []}
        self.assertEqual(self.build([self.row, missing]), 1)
        with zipfile.ZipFile(self.output) as archive:
            manifest = archive.read("WarcraftAudi_Test_ruRU/Voices.lua").decode()
            self.assertIn('questID = 42, locale = "ruRU", stage = "detail"', manifest)
            self.assertNotIn('stage = "progress"', manifest)
            self.assertIn("Interface\\\\AddOns\\\\WarcraftAudi_Test_ruRU\\\\Audio\\\\clip.ogg", manifest)
            self.assertEqual(archive.read("WarcraftAudi_Test_ruRU/Audio/clip.ogg"), (self.audio / "clip.ogg").read_bytes())

    def test_duplicate_stage_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.build([self.row, self.row])
        self.assertFalse(self.output.exists())

    def test_gender_variants_are_preserved_in_manifest(self):
        self.build([{**self.row, "gender": gender} for gender in ("male", "female")])
        with zipfile.ZipFile(self.output) as archive:
            manifest = archive.read("WarcraftAudi_Test_ruRU/Voices.lua").decode()
        self.assertIn('gender = "male"', manifest)
        self.assertIn('gender = "female"', manifest)

    @unittest.skipIf(LuaRuntime is None, "Install lupa==2.6 to validate generated Lua")
    def test_generated_manifest_registers_real_paths_in_lua51(self):
        self.build()
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.execute('WarcraftAudi = { RegisterPack = function(self, name, entries) self.received = entries; self.name = name; return true end }')
        with zipfile.ZipFile(self.output) as archive:
            lua.execute(archive.read("WarcraftAudi_Test_ruRU/Voices.lua").decode())
        received = lua.globals().WarcraftAudi.received[1]
        self.assertEqual(received.questID, 42)
        self.assertEqual(received.files[1], "Interface\\AddOns\\WarcraftAudi_Test_ruRU\\Audio\\clip.ogg")

    def test_invalid_ids_locales_stages_and_empty_audio(self):
        for override in ({"quest_id": True}, {"quest_id": 0}, {"locale": "xxXX"}, {"stage": "gossip"}, {"audio": []}, {"audio": "clip.ogg"}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.build([{**self.row, **override}])

    def test_traversal_absolute_paths_and_symlink_escape_rejected(self):
        outside = self.root / "outside.ogg"
        outside.write_bytes(b"OggS" + bytes(20))
        (self.audio / "escape.ogg").symlink_to(outside)
        for path in ("../outside.ogg", "/tmp/outside.ogg", "escape.ogg", "bad\\clip.ogg"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.build([{**self.row, "audio": [path]}])

    def test_missing_or_wrong_format_rejected(self):
        (self.audio / "wrong.mp3").write_text("this is not audio")
        for path in ("absent.ogg", "wrong.mp3"):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                self.build([{**self.row, "audio": [path]}])

    def test_unsafe_pack_name_rejected(self):
        with self.assertRaises(ValueError):
            self.build(name="../../Test")

    def test_audible_demo_cannot_be_packaged_without_a_recording(self):
        from unittest.mock import patch
        with patch.object(packaging, "ROOT", self.root):
            with self.assertRaisesRegex(ValueError, "generate"):
                packaging.build_addon(self.output, with_demo=True)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
