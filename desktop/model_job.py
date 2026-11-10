"""Isolated model I/O process: bounded HTTP, resumable files, real byte progress."""
import hashlib
import json
from pathlib import Path
import shutil
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
from core import app_data,atomic_json
from model_store import REQUIRED,FILES,repo_id,validate_model,sha256,model_card

TIMEOUT=12
RETRIES=3

def emit(event,**values):
    print(json.dumps(dict(event=event,**values),ensure_ascii=False),flush=True)

def open_url(request):
    # certifi is packaged so the standalone app does not depend on a Python install's CA path.
    import certifi
    return urllib.request.urlopen(request,timeout=TIMEOUT,context=ssl.create_default_context(cafile=certifi.where()))

def catalog():
    """Follow Hub pagination and keep public ASR repositories with installable files."""
    url = ('https://huggingface.co/api/models?pipeline_tag=automatic-speech-recognition'
           '&filter=ctranslate2&full=true&limit=100')
    seen = set()
    cards = {}
    while url and url not in seen:
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.netloc != 'huggingface.co' or parsed.path != '/api/models':
            raise ValueError('Некорректная ссылка на страницу каталога.')
        seen.add(url)
        emit('status', text=f'Обновление каталога Hugging Face · страница {len(seen)}')
        with open_url(url) as response:
            data = json.load(response)
            link = response.headers.get('Link', '')
        for item in data:
            if item.get('private') or item.get('gated') or item.get('disabled'):
                continue
            files = {entry.get('rfilename') for entry in item.get('siblings', [])}
            if not REQUIRED <= files:
                continue
            try:
                repo = repo_id(item['id'])
            except (KeyError, ValueError):
                continue
            tags = item.get('tags') or []
            languages = [tag[9:] for tag in tags if tag.startswith('language:')]
            # Hub may also return bare language codes.
            languages += [tag for tag in tags if re.fullmatch(r'[a-z]{2,3}', tag) and tag not in ('asr', 'ct2')]
            license_name = next((tag[8:] for tag in tags if tag.startswith('license:')), '')
            cards[repo] = model_card(repo, item.get('likes'), dict.fromkeys(languages), license_name)
        match = re.search(r'<([^>]+)>;\s*rel="next"', link)
        url = match.group(1) if match else None
    return {'catalog': list(cards.values())}


def inspect(value):
    repo=repo_id(value)
    emit('status',text='Подключение к Hugging Face…')
    request=urllib.request.Request('https://huggingface.co/api/models/'+repo+'?blobs=true',headers={'User-Agent':'Callnote/0.2.5'})
    with open_url(request) as response:
        raw=response.read(4*1024*1024+1)
        if len(raw)>4*1024*1024:raise ValueError('Слишком большой ответ каталога')
        data=json.loads(raw)
    entries={f['rfilename']:f for f in data.get('siblings',[]) if f['rfilename'] in FILES}
    if not REQUIRED<=entries.keys():raise ValueError('В репозитории нет готовой модели faster-whisper/CTranslate2')
    revision=data.get('sha','')
    if not revision or any(c not in '0123456789abcdef' for c in revision):raise ValueError('Не удалось получить версию модели')
    sizes={name:int(entry.get('size',0)) for name,entry in entries.items()}
    if any(size<=0 for size in sizes.values()):raise ValueError('Каталог не сообщил размер файлов. Повторите проверку.')
    return dict(repo=repo,revision=revision,size=sum(sizes.values()),license=str((data.get('cardData') or {}).get('license','Не указана')),
                files=sorted(entries),sizes=sizes,hashes={n:f.get('lfs',{}).get('sha256') for n,f in entries.items()})

def transfer(url,target,size,expected_hash=None,progress=lambda n:None,opener=open_url,retries=RETRIES):
    """Range resume is accepted only with a matching Content-Range. Never append a 200 response."""
    target=Path(target);partial=target.with_name(target.name+'.part')
    if target.exists():
        if target.stat().st_size==size and (not expected_hash or sha256(target)==expected_hash):progress(size);return
        target.replace(partial)
    for attempt in range(retries):
        received=partial.stat().st_size if partial.exists() else 0
        if received>size:partial.unlink();received=0
        try:
            if received<size:
                headers={'User-Agent':'Callnote/0.2.5','Accept-Encoding':'identity'}
                if received:headers['Range']=f'bytes={received}-'
                with opener(urllib.request.Request(url,headers=headers)) as response:
                    status=response.getcode()
                    if status==206:
                        expected=f'bytes {received}-'
                        if not response.headers.get('Content-Range','').startswith(expected):raise ValueError('Некорректный ответ на запрос продолжения')
                    elif status==200:received=0
                    else:raise OSError(f'HTTP {status}')
                    mode='ab' if received else 'wb'
                    with partial.open(mode,buffering=0) as destination:
                        while True:
                            block=getattr(response,'read1',response.read)(64*1024)
                            if not block:break
                            if received+len(block)>size:raise ValueError('Размер файла не совпадает с каталогом')
                            destination.write(block);received+=len(block);progress(received)
                if received!=size:raise OSError('Передача прервалась до конца файла')
            if expected_hash and sha256(partial)!=expected_hash:
                partial.unlink();raise ValueError('Контрольная сумма не совпала; повторная загрузка')
            partial.replace(target);progress(size);return
        except (OSError,ValueError,urllib.error.URLError) as exc:
            if attempt+1==retries:raise RuntimeError('Не удалось скачать '+target.name+'. Проверьте сеть и нажмите «Повторить». Полученная часть сохранена. Причина: '+type(exc).__name__) from exc
            emit('status',text=f'Связь прервалась · повтор {attempt+2}/{retries}: {target.name}')
            time.sleep(attempt+1)

def download(meta):
    # Re-read metadata for legacy saved requests which lack exact sizes.
    if 'sizes' not in meta:meta=inspect(meta['repo'])
    root=app_data()/'Models';root.mkdir(exist_ok=True)
    name=repo_id(meta['repo']).replace('/','--')+'--'+meta['revision'][:12]
    temporary=root/('.partial-'+name);destination=root/name
    if destination.exists():validate_model(destination);return {'path':str(destination)}
    temporary.mkdir(exist_ok=True)
    # Directory naming matches v0.2: already completed files can be reused.
    completed=0;last_emit=0;last_count=0;last_time=time.monotonic()
    for filename in meta['files']:
        if filename not in FILES:raise ValueError('Недопустимое имя файла модели')
        size=meta['sizes'][filename]
        emit('status',text='Подключение: '+filename)
        base=completed;last_count=0;last_time=time.monotonic();last_emit=0
        def progress(count):
            nonlocal last_emit,last_count,last_time
            now=time.monotonic()
            if now-last_emit<.15 and count!=size:return
            speed=max(0,count-last_count)/max(.001,now-last_time)
            last_count=count;last_time=now;last_emit=now
            emit('progress',value=min(99,int((base+count)*100/max(1,meta['size']))),
                 text=f"{filename} · {(base+count)/1048576:.1f} / {meta['size']/1048576:.1f} МБ · {speed/1048576:.1f} МБ/с")
        url=f"https://huggingface.co/{meta['repo']}/resolve/{meta['revision']}/{filename}?download=true"
        transfer(url,temporary/filename,size,meta.get('hashes',{}).get(filename),progress)
        completed+=size
    emit('status',text='Проверка скачанной модели…');validate_model(temporary)
    atomic_json(temporary/'callnote-model.json',meta);temporary.rename(destination)
    emit('progress',value=100,text='Модель готова');return {'path':str(destination)}

def copy_model(operation,value,dest):
    source=validate_model(value)
    if operation=='import':
        root=app_data()/'Models';root.mkdir(exist_ok=True)
        destination=root/(source.name+'-'+hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:8])
    else:destination=Path(dest)/source.name
    if destination.resolve()==source.resolve():return {'path':str(source)}
    if destination.exists():validate_model(destination);return {'path':str(destination)}
    temporary=destination.with_name('.copy-'+destination.name);temporary.mkdir(parents=True,exist_ok=True)
    names=sorted(n for n in FILES|{'callnote-model.json'} if (source/n).is_file())
    hashes={}
    for i,name in enumerate(names):
        emit('status',text='Копирование: '+name)
        # Partial copies are overwritten safely on retry; final directory remains invisible until ready.
        with (source/name).open('rb') as src,(temporary/name).open('wb') as dst:
            count=0;size=(source/name).stat().st_size
            for block in iter(lambda:src.read(1024*1024),b''):
                dst.write(block);count+=len(block)
                emit('progress',value=int((i+count/max(1,size))*100/len(names)),text='Копирование: '+name)
        hashes[name]=sha256(temporary/name)
    validate_model(temporary);atomic_json(temporary/'checksums.json',hashes);temporary.rename(destination)
    return {'path':str(destination)}

def main(request_path):
    try:
        args=json.loads(Path(request_path).read_text())
        op=args['operation']
        if op=='inspect':result=inspect(args['value'])
        elif op=='download':result=download(args['metadata'] or inspect(args['value']))
        elif op=='catalog':result=catalog()
        elif op in ('import','export'):result=copy_model(op,args['value'],args.get('destination'))
        else:raise ValueError('Неизвестная операция')
        emit('result',data=result);return 0
    except urllib.error.HTTPError as exc:
        emit('error',text=f'Сервер Hugging Face вернул HTTP {exc.code}. Проверьте доступ к модели и повторите попытку.');return 1
    except (TimeoutError,socket.timeout):
        emit('error',text='Сервер не отвечает. Проверьте подключение и нажмите «Повторить».');return 1
    except Exception as exc:
        emit('error',text=str(exc) if not isinstance(exc,urllib.error.URLError) else 'Не удалось подключиться к Hugging Face. Проверьте сеть и повторите попытку.');return 1
