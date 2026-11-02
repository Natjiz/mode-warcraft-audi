"""Small Gradio SSE client. The Windows bundle needs no Gradio/browser dependency."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .network import fetch,fetch_json,NoRedirect
from .protocol import request_key
from .client import Cancelled,CatalogProvider,RecordingMissing

SERVICE_URL='https://raw.githubusercontent.com/Natjiz/mode-warcraft-audi/main/companion/service.json'
QUOTA_MESSAGE='Лимит бесплатной генерации исчерпан. Готовые записи доступны; повторите позже.'


def service_url(value,allow_local=False):
    if not isinstance(value,str):raise ValueError('Некорректный адрес сервиса генерации')
    parsed=urllib.parse.urlsplit(value)
    valid=parsed.scheme=='https' and bool(parsed.hostname) and parsed.hostname.endswith('.hf.space')
    local=allow_local and parsed.scheme=='http' and parsed.hostname in {'127.0.0.1','localhost'}
    if not (valid or local) or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'','/'}:
        raise ValueError('Некорректный адрес сервиса генерации')
    return value.rstrip('/')


class SpaceProvider:
    def __init__(self,cache,url,token='',*,allow_local=False,revision='qwen-clone-v3'):
        self.cache,self.url,self.token,self.allow_local=cache,service_url(url,allow_local),token,allow_local
        self.revision=revision

    @staticmethod
    def _warmup(operation,cancelled,status):
        deadline=time.monotonic()+120
        while True:
            if cancelled.is_set():raise Cancelled()
            try:return operation()
            except urllib.error.HTTPError as error:
                if error.code not in {502,503,504}:raise
            except TimeoutError:
                pass
            if time.monotonic()>deadline:raise ValueError('Сервис ещё запускается. Готовые записи доступны; повторите позже.')
            status('Сервис генерации просыпается; ожидаю подключение')
            if cancelled.wait(2):raise Cancelled()

    def _call(self,name,request,cancelled,status):
        if cancelled.is_set():raise Cancelled()
        endpoint=self.url+'/gradio_api/call/'+name
        result=self._warmup(lambda:fetch_json(endpoint,payload={'data':[request]},token=self.token,timeout=15),cancelled,status)
        event_id=result.get('event_id','')
        if not isinstance(event_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',event_id):
            raise ValueError('Сервис вернул некорректную задачу')
        headers={'Accept':'text/event-stream'}
        if self.token:headers['Authorization']='Bearer '+self.token
        opener=urllib.request.build_opener(NoRedirect()) if self.token else urllib.request.build_opener()
        started=time.monotonic();event='';data=[];size=0
        with self._warmup(lambda:opener.open(urllib.request.Request(endpoint+'/'+event_id,headers=headers),timeout=30),cancelled,status) as response:
            while True:
                if cancelled.is_set():raise Cancelled()
                if time.monotonic()-started>900:raise ValueError('Сервис не успел подготовить запись; повторите позже')
                line=response.readline(65537)
                if len(line)>65536:raise ValueError('Слишком большой ответ сервиса')
                if not line:break
                text=line.decode('utf-8').rstrip('\r\n')
                if text.startswith('event:'):event=text[6:].strip()
                elif text.startswith('data:'):
                    part=text[5:].strip();size+=len(line)
                    if size>256*1024:raise ValueError('Слишком большой ответ сервиса')
                    data.append(part)
                elif not text:
                    if event=='complete':
                        try:value=json.loads('\n'.join(data))
                        except ValueError:raise ValueError('Некорректный результат генерации') from None
                        if not isinstance(value,list) or len(value)!=1 or not isinstance(value[0],dict):
                            raise ValueError('Некорректный результат генерации')
                        return value[0]
                    if event=='error':
                        if any(word in ' '.join(data).casefold() for word in ('quota','exceeded','gpu duration')):
                            raise ValueError(QUOTA_MESSAGE)
                        raise ValueError('Сервис не смог обработать запрос; повторите позже')
                    if event=='heartbeat' and name=='generate':status('Ожидаю очередь или генерацию озвучки')
                    event='';data=[];size=0
        raise ValueError('Соединение с сервисом прервано; повторите позже')

    def get_audio(self,request,cancelled,status):
        cache_request=request_key(request,"space:"+self.revision)
        previous=self.cache.find_request(cache_request)
        if previous:
            status('Воспроизвожу сохранённую запись')
            return previous
        try:
            status('Проверяю общую библиотеку озвучек')
            record=self._call('lookup',request,cancelled,status)
            if record.get('status')=='missing':
                status('Создаю новую озвучку; первый запрос может занять время')
                record=self._call('generate',request,cancelled,status)
            if record.get('status')=='quota':raise ValueError(QUOTA_MESSAGE)
            if record.get('status')=='voice_unavailable':raise ValueError('Голос персонажа не прошёл проверку. Запись не опубликована; повторите позже.')
            if record.get('status')!='ready':raise ValueError('Запись пока не подготовлена; повторите позже')
            key=record.get('id','');audio=record.get('audio',{})
            if not re.fullmatch('[0-9a-f]{64}',str(key)) or not re.fullmatch('[0-9a-f]{64}',str(audio.get('sha256',''))):
                raise ValueError('Нет корректной контрольной суммы записи')
            url=audio.get('url','');parsed=urllib.parse.urlsplit(url)
            public=parsed.scheme=='https' and parsed.hostname=='huggingface.co' and parsed.path.startswith('/datasets/')
            local=self.allow_local and parsed.scheme=='http' and parsed.hostname in {'127.0.0.1','localhost'}
            if not (public or local) or parsed.username or parsed.password or parsed.fragment:
                raise ValueError('Некорректный адрес записи')
            if not isinstance(audio.get('size'),int) or not 0<audio['size']<=16*1024**2:
                raise ValueError('Недопустимый размер записи')
            existing=self.cache.get(key)
            if existing:
                cached_data=existing.read_bytes()
                if len(cached_data)==audio['size'] and hashlib.sha256(cached_data).hexdigest()==audio['sha256']:
                    return key,self.cache.put(key,cached_data,audio['sha256'],audio['mime'],cache_request)
            status('Скачиваю готовую озвучку')
            data,_=fetch(url)
            if cancelled.is_set():raise Cancelled()
            if len(data)!=audio['size']:raise ValueError('Размер загруженной записи не совпадает')
            return key,self.cache.put(key,data,audio['sha256'],audio['mime'],cache_request)
        except urllib.error.HTTPError as error:
            if error.code==429:raise ValueError(QUOTA_MESSAGE) from None
            raise ValueError('Сервис генерации сейчас недоступен. Готовые записи и кэш продолжают работать.') from None
        except OSError:
            raise ValueError('Нет соединения с сервисом генерации. Готовые записи и кэш доступны.') from None


class AutomaticProvider:
    def __init__(self,cache,catalog):
        self.cache,self.catalog=cache,CatalogProvider(cache,catalog)
        self.service=json.loads((Path(__file__).resolve().parents[1]/'service.json').read_text(encoding='utf-8'))
        self.service_cache=cache.directory.parent/'generation-service.json'
        try:
            saved=json.loads(self.service_cache.read_text(encoding='utf-8'))
            self.validate(saved)
            self.service=saved
        except (ValueError,OSError):pass
        self.checked,self.lock=0.0,threading.Lock()

    @staticmethod
    def validate(candidate):
        if not isinstance(candidate,dict):raise ValueError('Invalid service manifest')
        if candidate.get('schema_version')!=1 or candidate.get('provider')!='gradio-zerogpu':raise ValueError('Invalid service manifest')
        if candidate.get('space_url'):service_url(candidate['space_url'])
        revision=candidate.get('generation_revision','qwen-clone-v3')
        if not isinstance(revision,str) or not 1<=len(revision)<=128:raise ValueError('Invalid generation revision')

    def refresh_service(self):
        with self.lock:
            if self.checked and time.monotonic()-self.checked<=300:return
            self.checked=time.monotonic()
            try:
                candidate=fetch_json(SERVICE_URL,timeout=10)
                self.validate(candidate)
                self.service=candidate
                temporary=self.service_cache.with_suffix('.tmp')
                temporary.write_text(json.dumps(candidate),encoding='utf-8');temporary.replace(self.service_cache)
            except (OSError,ValueError):pass

    def get_audio(self,request,cancelled,status):
        # A known local policy lets an offline repeat start immediately. Refresh
        # policy in the background rather than waiting for failed network calls.
        if not self.catalog.catalog.find(request):
            revision=self.service.get('generation_revision','qwen-clone-v3')
            cached=self.cache.find_request(request_key(request,'space:'+revision))
            if cached:
                if not self.checked or time.monotonic()-self.checked>300:
                    threading.Thread(target=self.refresh_service,daemon=True,name='generation-policy').start()
                status('Воспроизвожу сохранённую запись')
                return cached
        try:return self.catalog.get_audio(request,cancelled,status)
        except RecordingMissing:pass
        if cancelled.is_set():raise Cancelled()
        self.refresh_service()
        config=self.service
        url=config.get('space_url')
        if not url:raise ValueError('Готовой записи пока нет. Бесплатный сервис генерации ещё не подключён.')
        return SpaceProvider(self.cache,url,revision=config.get('generation_revision','qwen-clone-v3')).get_audio(request,cancelled,status)
