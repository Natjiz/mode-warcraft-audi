import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from companion.warcraft_audi.cache import AudioCache
from companion.warcraft_audi.client import Cancelled,RecordingMissing
from companion.warcraft_audi.protocol import canonical_request
from companion.warcraft_audi.space_client import SpaceProvider,AutomaticProvider,service_url

ROOT=Path(__file__).resolve().parents[1]


class SpaceClientTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.cache=AudioCache(Path(self.temp.name)/'cache')
        self.request=canonical_request({'action':'demo'})
        self.generated=False;self.calls=[];self.mode='normal';self.downloads=0
        self.data=(ROOT/'assets/kiro_light_streamed/progress.ogg').read_bytes()
        fixture=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def send(self,data,mime='application/json'):
                self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            def do_POST(self):
                value=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                fixture.calls.append((self.path,value))
                name=self.path.rsplit('/',1)[-1]
                self.send(json.dumps({'event_id':name}).encode())
            def do_GET(self):
                if self.path=='/audio.ogg':
                    fixture.downloads+=1
                    return self.send(fixture.data,'audio/ogg')
                generate=self.path.endswith('/generate/generate')
                if generate:fixture.generated=True
                if generate and fixture.mode=='quota':result={'status':'quota'}
                elif not fixture.generated:result={'status':'missing'}
                else:
                    checksum=hashlib.sha256(fixture.data).hexdigest() if fixture.mode!='corrupt' else '0'*64
                    result={'status':'ready','id':'b'*64,'audio':{'url':fixture.url+'/audio.ogg','sha256':checksum,'size':len(fixture.data),'mime':'audio/ogg'}}
                self.send(('event: heartbeat\ndata: null\n\nevent: complete\ndata: '+json.dumps([result])+'\n\n').encode(),'text/event-stream')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.url=f'http://127.0.0.1:{self.server.server_port}'
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.provider=SpaceProvider(self.cache,self.url,allow_local=True)

    def test_first_request_renders_once_other_client_uses_library_then_local_cache(self):
        key,path=self.provider.get_audio(self.request,threading.Event(),lambda _:None)
        self.assertEqual(path.read_bytes(),self.data)
        self.assertEqual([p[0].rsplit('/',1)[-1] for p in self.calls],['lookup','generate'])
        second=SpaceProvider(AudioCache(Path(self.temp.name)/'second'),self.url,allow_local=True)
        second.get_audio(self.request,threading.Event(),lambda _:None)
        self.assertEqual([p[0].rsplit('/',1)[-1] for p in self.calls],['lookup','generate','lookup'])
        self.provider.get_audio(self.request,threading.Event(),lambda _:None)
        self.assertEqual(len(self.calls),3)

    def test_quota_is_clear_and_no_invalid_recording_is_cached(self):
        self.mode='quota'
        with self.assertRaisesRegex(ValueError,'Лимит бесплатной'):self.provider.get_audio(self.request,threading.Event(),lambda _:None)
        self.assertEqual(self.cache.stats()['count'],0)
        self.mode='corrupt'
        with self.assertRaisesRegex(ValueError,'сумма'):self.provider.get_audio(self.request,threading.Event(),lambda _:None)
        self.assertEqual(self.cache.stats()['count'],0)

    def test_new_generation_revision_does_not_reuse_a_rejected_cached_voice(self):
        self.provider.get_audio(self.request,threading.Event(),lambda _:None)
        count=len(self.calls);downloads=self.downloads
        revised=SpaceProvider(self.cache,self.url,allow_local=True,revision='voice-policy-v4')
        revised.get_audio(self.request,threading.Event(),lambda _:None)
        self.assertGreater(len(self.calls),count)
        self.assertEqual(self.downloads,downloads,'An unchanged recording must not download again after policy refresh')

    def test_cancellation_prevents_request_and_download(self):
        stop=threading.Event();stop.set()
        with self.assertRaises(Cancelled):self.provider.get_audio(self.request,stop,lambda _:None)
        self.assertEqual(self.calls,[])

    def test_auto_discovery_uses_public_space_without_forwarding_machine_credentials(self):
        catalog={'schema_version':1,'items':[]}
        provider=AutomaticProvider(self.cache,catalog)
        with patch.object(provider.catalog,'get_audio',side_effect=RecordingMissing()),patch('companion.warcraft_audi.space_client.fetch_json',return_value={'schema_version':1,'provider':'gradio-zerogpu','space_url':'https://owner-demo.hf.space'}),patch('companion.warcraft_audi.space_client.SpaceProvider') as Space:
            provider.get_audio(self.request,threading.Event(),lambda _:None)
            Space.assert_called_once_with(self.cache,'https://owner-demo.hf.space',revision='qwen-clone-v3')

    def test_last_known_service_manifest_survives_restart_without_network(self):
        catalog={'schema_version':1,'items':[]}
        first=AutomaticProvider(self.cache,catalog)
        manifest={'schema_version':1,'provider':'gradio-zerogpu','space_url':'https://owner-demo.hf.space','generation_revision':'v3'}
        with patch.object(first.catalog,'get_audio',side_effect=RecordingMissing()),patch('companion.warcraft_audi.space_client.fetch_json',return_value=manifest),patch('companion.warcraft_audi.space_client.SpaceProvider'):
            first.get_audio(self.request,threading.Event(),lambda _:None)
        second=AutomaticProvider(self.cache,catalog)
        with patch.object(second.catalog,'get_audio',side_effect=RecordingMissing()),patch('companion.warcraft_audi.space_client.fetch_json',side_effect=OSError()),patch('companion.warcraft_audi.space_client.SpaceProvider') as Space:
            second.get_audio(self.request,threading.Event(),lambda _:None)
            Space.assert_called_once_with(self.cache,'https://owner-demo.hf.space',revision='v3')

    def test_disabled_service_keeps_published_records_working_without_configuration(self):
        provider=AutomaticProvider(self.cache,{'schema_version':1,'items':[]})
        with patch.object(provider.catalog,'get_audio',return_value=('key',Path('cached'))),patch('companion.warcraft_audi.space_client.fetch_json') as fetch:
            self.assertEqual(provider.get_audio(self.request,threading.Event(),lambda _:None)[0],'key');fetch.assert_not_called()
        with patch.object(provider.catalog,'get_audio',side_effect=RecordingMissing()),patch('companion.warcraft_audi.space_client.fetch_json',return_value={'schema_version':1,'provider':'gradio-zerogpu','space_url':None}):
            with self.assertRaisesRegex(ValueError,'ещё не подключён'):provider.get_audio(self.request,threading.Event(),lambda _:None)

    def test_sleeping_space_is_retried_automatically_without_new_user_action(self):
        import urllib.error
        from unittest.mock import Mock
        stop=Mock();stop.is_set.return_value=False;stop.wait.return_value=False
        operation=Mock(side_effect=[urllib.error.HTTPError('https://owner.hf.space',503,'sleeping',{},None),{'ready':True}])
        self.assertEqual(SpaceProvider._warmup(operation,stop,lambda _:None),{'ready':True})
        self.assertEqual(operation.call_count,2)
        stop.wait.return_value=True
        with self.assertRaises(Cancelled):SpaceProvider._warmup(Mock(side_effect=TimeoutError()),stop,lambda _:None)

    def test_offline_cached_generated_audio_does_not_wait_for_network(self):
        from companion.warcraft_audi.protocol import request_key
        key='d'*64;checksum=hashlib.sha256(self.data).hexdigest()
        self.cache.put(key,self.data,checksum,'audio/ogg',request_key(self.request,'space:v3'))
        provider=AutomaticProvider(self.cache,{'schema_version':1,'items':[]})
        provider.service={'schema_version':1,'provider':'gradio-zerogpu','space_url':'https://owner-demo.hf.space','generation_revision':'v3'}
        refreshed=threading.Event()
        with patch.object(provider,'refresh_service',side_effect=refreshed.set),patch.object(provider.catalog,'get_audio') as network:
            self.assertEqual(provider.get_audio(self.request,threading.Event(),lambda _:None)[0],key)
            network.assert_not_called()
            self.assertTrue(refreshed.wait(1))

    def test_corrupt_cached_manifest_and_null_task_id_fail_cleanly(self):
        (self.cache.directory.parent/'generation-service.json').write_text('[]',encoding='utf-8')
        automatic=AutomaticProvider(self.cache,{'schema_version':1,'items':[]})
        self.assertIsNone(automatic.service['space_url'])
        with patch('companion.warcraft_audi.space_client.fetch_json',return_value={'event_id':None}):
            with self.assertRaisesRegex(ValueError,'некорректную задачу'):
                self.provider._call('lookup',self.request,threading.Event(),lambda _:None)

    def test_manifest_cannot_redirect_client_to_unrelated_server(self):
        for url in [None,123,'http://example.hf.space','https://example.com','https://user:secret@example.hf.space','https://example.hf.space/path','https://example.hf.space?token=secret']:
            with self.assertRaises(ValueError):service_url(url)
        self.assertEqual(service_url('https://owner-demo.hf.space/'),'https://owner-demo.hf.space')


if __name__=='__main__':unittest.main()
