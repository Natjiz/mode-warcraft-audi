"""Real Gradio HTTP -> generator -> durable library -> desktop client integration.

Use --engine qwen for actual CPU speech. This does not claim a ZeroGPU/GPU test.
"""
import argparse
import functools
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from companion.warcraft_audi.cache import AudioCache
from companion.warcraft_audi.library import QuestLibrary,LocalLibraryStore
from companion.warcraft_audi.protocol import canonical_request
from companion.warcraft_audi.space_client import SpaceProvider
from space_runtime.ui import make_app


def run(engine_type,root,models,synthetic_profile=None):
    import gradio,torch
    torch.set_num_threads(4);torch.set_num_interop_threads(1)
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    source=json.loads((ROOT/'quests/kiro_48314_ruRU.json').read_text(encoding='utf-8'))['quests'][1]
    if synthetic_profile:
        source={**source,"quest_id":900000001,"npc_id":900000002,"speaker":"Synthetic voice test","text":"Я помогу тебе. Мы защитим наш дом.","npc_race":"Вульпера" if synthetic_profile=="vulpera" else "Таурен","npc_type":"Гуманоид","npc_gender":"male"}
    request=canonical_request(source)
    if engine_type=='qwen':
        from companion.warcraft_audi.voice_engine import QwenEngine
        underlying=QwenEngine(models,ROOT/'voices',device='cpu')
    else:
        class Fixture:
            def revision_for(self,r):return 'transport-fixture-v1'
            def reference_files(self,r):return []
            def voice_info(self,r):return {'version':'fixture','approved':False}
            def generate(self,r):return (ROOT/'assets/kiro_light_streamed/progress.ogg').read_bytes(),'audio/ogg'
        underlying=Fixture()
    class Counter:
        calls=0
        def __getattr__(self,n):return getattr(underlying,n)
        def generate(self,r):self.calls+=1;return underlying.generate(r)
    engine=Counter()
    if engine_type=='qwen':
        import numpy as np
        from companion.warcraft_audi.voice_quality import median_pitch
        median_pitch(np.sin(np.arange(8000)*2*np.pi*220/16000).astype(np.float32)*.1,16000)
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self,*args):pass
    static=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(root/'shared')))
    threading.Thread(target=static.serve_forever,daemon=True).start()
    library=QuestLibrary(LocalLibraryStore(root/'shared',f'http://127.0.0.1:{static.server_port}'),engine)
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    app=make_app(library)
    started=time.monotonic()
    app.launch(server_name='127.0.0.1',server_port=port,prevent_thread_lock=True,quiet=True)
    try:
        revision=engine.revision_for(request)
        provider=SpaceProvider(AudioCache(root/('first-client-'+revision[:8])),f'http://127.0.0.1:{port}',allow_local=True,revision=revision)
        _,path=provider.get_audio(request,threading.Event(),lambda s:print(s,flush=True))
        first_calls=engine.calls
        second=SpaceProvider(AudioCache(root/('second-client-'+revision[:8])),f'http://127.0.0.1:{port}',allow_local=True,revision=revision)
        second.get_audio(request,threading.Event(),lambda _:None)
        assert engine.calls==first_calls,'Second client must not generate again'
        restarted=QuestLibrary(library.store,engine)
        assert restarted.generate(request)['status']=='ready'
        assert engine.calls==first_calls,'Restart must reuse the shared recording'
        subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(path),'-f','null','-'],check=True)
        destination=root/(str(request['quest_id'])+'-progress.ogg');shutil.copyfile(path,destination)
        result={'engine':engine_type,'torch':torch.__version__,'gradio':gradio.__version__,'device':'cpu','quest_id':request['quest_id'],'stage':'progress','synthetic_profile':synthetic_profile,
                'voice':engine.voice_info(request),'generation_calls':engine.calls,'elapsed_seconds':round(time.monotonic()-started,2),
                'verified':['actual Gradio SSE API','desktop checksum/download/cache','shared recording reused by second client','shared recording reused after library restart','FFmpeg full decode'],
                'limits':['No hosted Hugging Face Space or real ZeroGPU allocation tested.','No real WoW or physical playback tested.']}
        (root/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False),flush=True)
    finally:app.close();static.shutdown();static.server_close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine',choices=['fixture','qwen'],default='fixture')
    parser.add_argument('--output',type=Path,default=ROOT/'dist/space-smoke')
    parser.add_argument('--models',type=Path,default=Path('/workspace/.warcraft-audi-models'))
    parser.add_argument('--synthetic-profile',choices=['vulpera','tauren'])
    args=parser.parse_args();run(args.engine,args.output,args.models,args.synthetic_profile)
