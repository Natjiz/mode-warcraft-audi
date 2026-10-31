import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from companion.warcraft_audi.protocol import canonical_request
from companion.warcraft_audi.service import Catalog

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('catalog_builder', ROOT / 'tools/build_catalog.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class CatalogBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'downloads').mkdir()
        (self.root / 'companion').mkdir()
        demo = self.root / 'downloads/Akunda-character-voice-0.3.0.mp3'
        demo.write_bytes(b'audio fixture, not synthesized speech')
        demo.with_suffix('.json').write_text(json.dumps({'audio_sha256': hashlib.sha256(demo.read_bytes()).hexdigest()}))

    def recording(self, name, npc, voice, text='Реплика', quest=48313):
        directory = self.root / 'assets' / (name + '_streamed')
        directory.mkdir(parents=True)
        path = directory / 'detail.ogg'
        path.write_bytes(b'catalog fixture ' + name.encode())
        request = canonical_request({'quest_id':quest,'stage':'detail','npc_id':npc,'text':text,'locale':'ruRU'})
        (directory / 'manifest.json').write_text(json.dumps({'voice':voice,'items':[{'request':request,'file':str(path.relative_to(self.root)),
            'mime':'audio/ogg','sha256':hashlib.sha256(path.read_bytes()).hexdigest()}]},ensure_ascii=False))
        return request, path

    def build(self):
        with patch.object(builder, 'ROOT', self.root):
            builder.build()
        return json.loads((self.root / 'companion/catalog.json').read_text())

    def test_each_npc_keeps_its_own_voice_and_journal_alias_is_available(self):
        request, _ = self.recording('kiro', 135154, 'kiro-candidate', 'Реплика Киро')
        self.recording('akunda', 128152, 'akunda-approved', 'Реплика Акунды', quest=50913)
        catalog = Catalog(self.build())
        self.assertEqual(catalog.find(request)['voice'], 'kiro-candidate')
        journal = {**request, 'npc_id':0, 'speaker':'Рассказчик', 'gender':'female'}
        self.assertEqual(catalog.find(journal)['voice'], 'kiro-candidate')

    def test_ambiguous_speaker_is_not_guessed_for_journal(self):
        request, _ = self.recording('one', 135154, 'kiro-candidate')
        self.recording('two', 128152, 'akunda-approved')
        catalog = Catalog(self.build())
        self.assertIsNone(catalog.find({**request, 'npc_id':0}))
        self.assertEqual(catalog.find(request)['voice'], 'kiro-candidate')

    def test_changed_recording_does_not_replace_published_checksum(self):
        _, path = self.recording('kiro',135154,'kiro-candidate')
        self.build()
        previous = (self.root / 'companion/catalog.json').read_bytes()
        path.write_bytes(b'changed after rendering')
        with self.assertRaisesRegex(ValueError,'checksum'):
            self.build()
        self.assertEqual((self.root / 'companion/catalog.json').read_bytes(), previous)

    def test_rejected_voice_cannot_return_to_catalog_or_journal(self):
        request, path = self.recording('old_kiro', 135154, 'kiro-rejected')
        manifest_path = path.parent / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['status'] = 'rejected'
        manifest_path.write_text(json.dumps(manifest))
        catalog = Catalog(self.build())
        self.assertIsNone(catalog.find(request))
        self.assertIsNone(catalog.find({**request, 'npc_id': 0}))
        self.recording('new_kiro', 135154, 'kiro-light-candidate')
        catalog = Catalog(self.build())
        self.assertEqual(catalog.find(request)['voice'], 'kiro-light-candidate')
        self.assertEqual(catalog.find({**request, 'npc_id': 0})['voice'], 'kiro-light-candidate')


if __name__ == '__main__':
    unittest.main()
