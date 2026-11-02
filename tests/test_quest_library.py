from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from companion.warcraft_audi.library import QuestLibrary,LocalLibraryStore
from companion.warcraft_audi.protocol import canonical_request
from companion.warcraft_audi.voice_profiles import profile_for,reference_key
from companion.warcraft_audi.voice_engine import QwenEngine

ROOT=Path(__file__).resolve().parents[1]


class Engine:
    def __init__(self,root):self.root=Path(root);self.calls=0;self.versions={};self.restored=False
    def revision_for(self,request):return self.versions.get(request['npc_id'],'voice-v1')
    def reference_files(self,request):return [self.root/'generated'/'reference.wav']
    def voice_info(self,request):return {'version':self.revision_for(request),'approved':False}
    def generate(self,request):
        self.calls+=1;time.sleep(.03)
        path=self.reference_files(request)[0];self.restored=path.exists()
        path.parent.mkdir(exist_ok=True);path.write_bytes(b'reusable reference fixture')
        return b'synthesized audio fixture','audio/ogg'


class QuestLibraryTests(unittest.TestCase):
    def test_single_render_shared_by_clients_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);engine=Engine(root);store=LocalLibraryStore(root/'library','http://127.0.0.1:1')
            library=QuestLibrary(store,engine)
            request=canonical_request({'action':'demo'})
            with ThreadPoolExecutor(max_workers=2) as executor:
                results=list(executor.map(lambda _:library.generate(request),range(2)))
            self.assertEqual(engine.calls,1)
            self.assertEqual(results[0],results[1])
            self.assertEqual(library.generate({**request,'gender':'female'}),results[0])
            self.assertEqual(engine.calls,1,'Identical text must not render again for a different player gender')
            self.assertNotIn('player_name',results[0]['request'])
            new_engine=Engine(root)
            restarted=QuestLibrary(store,new_engine)
            self.assertEqual(restarted.generate(request),results[0]);self.assertEqual(new_engine.calls,0)
            shutil.rmtree(root/'generated')
            restarted.generate({**request,'quest_id':999999})
            self.assertTrue(new_engine.restored,'New quests reuse the persisted NPC reference after restart')

    def test_voice_change_invalidates_only_its_npc_and_stores_immutable_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);engine=Engine(root);library=QuestLibrary(LocalLibraryStore(root/'library','http://127.0.0.1:1'),engine)
            one=canonical_request({'action':'demo'});two={**one,'npc_id':135154}
            a=library.generate(one);b=library.generate(two)
            engine.versions[one['npc_id']]='voice-v2'
            self.assertEqual(library.lookup(one)['status'],'missing')
            self.assertEqual(library.lookup(two),b)
            updated=library.generate(one)
            self.assertNotEqual(a['id'],updated['id'])
            self.assertEqual(a['audio']['url'],updated['audio']['url'])
            self.assertEqual(engine.calls,3)

    def test_player_name_is_removed_before_publication_and_bad_locale_never_renders(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);engine=Engine(root);library=QuestLibrary(LocalLibraryStore(root/'library','http://127.0.0.1:1'),engine)
            record=library.generate({'quest_id':42,'stage':'detail','text':'Вперёд, Тестовыйгерой.','player_name':'Тестовыйгерой'})
            self.assertNotIn('Тестовыйгерой',json.dumps(record,ensure_ascii=False))
            with self.assertRaises(ValueError):library.generate({**record['request'],'locale':'enUS'})
            self.assertEqual(engine.calls,1)

    def test_voice_quality_failure_never_publishes_audio_and_can_be_retried(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);engine=Engine(root);library=QuestLibrary(LocalLibraryStore(root/'library','http://127.0.0.1:1'),engine)
            request=canonical_request({'action':'demo'})
            with patch.object(library,'render',side_effect=ValueError('Generated speech drifted from the reference register')):
                with self.assertRaises(ValueError):library.generate(request)
            self.assertEqual(library.lookup(request)['status'],'missing')
            self.assertFalse(list((root/'library').rglob('*.ogg')))
            self.assertEqual(library.generate(request)['status'],'ready')

    def test_approved_npc_revisions_do_not_depend_on_unrelated_cards_or_review_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);voices=root/'voices';(voices/'npcs').mkdir(parents=True)
            shutil.copyfile(ROOT/'voices/models.json',voices/'models.json')
            for npc in (128152,135154):shutil.copyfile(ROOT/f'voices/npcs/{npc}.json',voices/f'npcs/{npc}.json')
            request=canonical_request({'action':'demo'});engine=QwenEngine(root,voices)
            before=engine.revision_for(request)
            other=voices/'npcs/135154.json';card=json.loads(other.read_text(encoding='utf-8'));card['voice_version']='another-version';other.write_text(json.dumps(card),encoding='utf-8')
            self.assertEqual(QwenEngine(root,voices).revision_for(request),before)
            own=voices/'npcs/128152.json';card=json.loads(own.read_text(encoding='utf-8'));card['approved']=False;own.write_text(json.dumps(card),encoding='utf-8')
            self.assertEqual(QwenEngine(root,voices).revision_for(request),before)

    def test_small_species_are_light_and_unknown_appearance_is_explicit(self):
        fox={'npc_id':1,'speaker':'Кто-то','npc_race':'Вульпера','npc_type':'Гуманоид','npc_gender':'male'}
        profile=profile_for(fox)
        self.assertEqual(profile['id'],'vulpera');self.assertIn('light male tenor',profile['instruction'])
        self.assertFalse(profile['approved'])
        self.assertEqual(reference_key(fox),reference_key({**fox,'text':'Другой квест'}))
        unknown=profile_for({**fox,'npc_race':'','npc_type':'Humanoid'})
        self.assertEqual(unknown['id'],'unspecified')
        self.assertIn('unknown',unknown['status'])


if __name__=='__main__':unittest.main()
