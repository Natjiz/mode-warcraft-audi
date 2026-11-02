import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('space_package',ROOT/'tools/package_space.py')
package=importlib.util.module_from_spec(spec);spec.loader.exec_module(package)


class SpacePackageTests(unittest.TestCase):
    def test_bundle_is_complete_small_and_keeps_approved_reference_checksums(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'space.zip';package.build(target)
            self.assertLess(target.stat().st_size,1024**2)
            with ZipFile(target) as archive:
                self.assertIsNone(archive.testzip())
                names=archive.namelist()
                for name in ('app.py','README.md','requirements.txt','packages.txt','companion/__init__.py',
                             'companion/warcraft_audi/voice_quality.py','space_runtime/bootstrap.py','tools/__init__.py','tools/fetch_voice_models.py'):
                    self.assertIn(name,names)
                self.assertFalse(any('generated/' in n or n.endswith('.safetensors') or n.endswith('.env') for n in names))
                for npc in (128152,135154):
                    card=json.loads(archive.read(f'voices/npcs/{npc}.json'))
                    self.assertTrue(card['approved'])
                    self.assertEqual(hashlib.sha256(archive.read(card['reference'])).hexdigest(),card['reference_sha256'])
                for name in names:
                    if name.endswith('.py'):compile(archive.read(name),name,'exec')


if __name__=='__main__':unittest.main()
