"""Shared durable audio library. CPU lookups never allocate GPU time."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import threading

from .protocol import canonical_request
from .service import catalog_key


def checked_key(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value):
        raise ValueError('Invalid library key')
    return value


def audio_name(data,mime):
    extension={'audio/ogg':'.ogg','audio/mpeg':'.mp3','audio/wav':'.wav'}[mime]
    return hashlib.sha256(data).hexdigest()+extension


class LocalLibraryStore:
    def __init__(self, root, public_url):
        self.root,self.public_url=Path(root),public_url.rstrip('/')
        self.root.mkdir(parents=True,exist_ok=True)

    def get(self,key):
        path=self.root/'records'/checked_key(key)[:2]/(key+'.json')
        if not path.exists():return None
        return json.loads(path.read_text(encoding='utf-8'))

    def publish(self,key,record,data,mime,references):
        name=audio_name(data,mime)
        audio=self.root/'audio'/name[:2]/name;audio.parent.mkdir(parents=True,exist_ok=True)
        if not audio.exists():audio.write_bytes(data)
        record={**record,'audio':{**record['audio'],'url':self.public_url+'/audio/'+name[:2]+'/'+name}}
        for path in references:
            target=self.root/'references'/path.name[:2]/path.name;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(path.read_bytes())
        target=self.root/'records'/checked_key(key)[:2]/(key+'.json');target.parent.mkdir(parents=True,exist_ok=True)
        temporary=target.with_suffix('.tmp');temporary.write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8');temporary.replace(target)
        return record

    def restore_references(self,paths):
        for path in paths:
            source=self.root/'references'/path.name[:2]/path.name
            if source.exists():path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(source.read_bytes())


class HubLibraryStore:
    """A public HF dataset survives Space restarts. Only its owner can publish."""
    def __init__(self,repo_id,token=None):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo_id):raise ValueError('Invalid dataset repository')
        from huggingface_hub import HfApi
        self.repo_id,self.token,self.api=repo_id,token,HfApi(token=token)

    def _download(self,name):
        from huggingface_hub import hf_hub_download
        from huggingface_hub.errors import EntryNotFoundError
        try:return Path(hf_hub_download(self.repo_id,name,repo_type='dataset',token=self.token))
        except EntryNotFoundError:return None

    def get(self,key):
        path=self._download('records/'+checked_key(key)[:2]+'/'+key+'.json')
        return json.loads(path.read_text(encoding='utf-8')) if path else None

    def publish(self,key,record,data,mime,references):
        from huggingface_hub import CommitOperationAdd
        name=audio_name(data,mime)
        url=f'https://huggingface.co/datasets/{self.repo_id}/resolve/main/audio/{name[:2]}/{name}'
        record={**record,'audio':{**record['audio'],'url':url}}
        operations=[CommitOperationAdd(path_in_repo='audio/'+name[:2]+'/'+name,path_or_fileobj=data)]
        operations += [CommitOperationAdd(path_in_repo='references/'+p.name[:2]+'/'+p.name,path_or_fileobj=p.read_bytes()) for p in references]
        operations.append(CommitOperationAdd(path_in_repo='records/'+checked_key(key)[:2]+'/'+key+'.json',path_or_fileobj=json.dumps(record,ensure_ascii=False).encode()))
        # Metadata and immutable audio/reference objects become visible atomically.
        self.api.create_commit(self.repo_id,repo_type='dataset',operations=operations,commit_message='Add quest recording '+key[:12])
        return record

    def restore_references(self,paths):
        for path in paths:
            if path.exists():continue
            source=self._download('references/'+path.name[:2]+'/'+path.name)
            if source:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(source.read_bytes())


class QuestLibrary:
    def __init__(self,store,engine,render=None):
        self.store,self.engine,self.render=store,engine,render or engine.generate
        # Gradio additionally bounds the queue. The lock closes simultaneous
        # lookup misses so only the first requester renders the shared recording.
        self.lock=threading.RLock()

    def lookup(self,payload):
        request=canonical_request(payload)
        if request['locale']!='ruRU':raise ValueError('Поддерживается русский язык клиента')
        # Player gender is already reflected in the actual text. Identical
        # lines spoken by the same NPC share one recording across players.
        key=catalog_key({**request,'gender':'unknown'})
        record=self.store.get(key)
        if record and record.get('voice_revision')==self.engine.revision_for(request):
            audio=record.get('audio',{})
            if not re.fullmatch('[0-9a-f]{64}',str(audio.get('sha256',''))):raise ValueError('Invalid stored checksum')
            if not 0<audio.get('size',0)<=16*1024**2:raise ValueError('Invalid stored audio size')
            return record
        return {'status':'missing'}

    def generate(self,payload):
        request=canonical_request(payload)
        with self.lock:
            existing=self.lookup(request)
            if existing['status']=='ready':return existing
            paths=self.engine.reference_files(request)
            self.store.restore_references(paths)
            data,mime=self.render(request)
            if not data or len(data)>16*1024**2:raise ValueError('Invalid generated audio size')
            revision=self.engine.revision_for(request)
            # Player gender is already reflected in the actual text. Identical
            # lines spoken by the same NPC share one recording across players.
            key=catalog_key({**request,'gender':'unknown'})
            record={'schema_version':1,'id':hashlib.sha256((key+revision).encode()).hexdigest(),'status':'ready',
                    'request':request,'voice_revision':revision,'voice':self.engine.voice_info(request),
                    'audio':{'sha256':hashlib.sha256(data).hexdigest(),'size':len(data),'mime':mime}}
            return self.store.publish(key,record,data,mime,[p for p in paths if p.is_file()])
