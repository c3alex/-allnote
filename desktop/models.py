"""Portable local CTranslate2 models, downloaded as data (never remote code)."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
from urllib.parse import urlparse
from PySide6.QtCore import QThread,Signal
from core import app_data,atomic_json

os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY','1')

REQUIRED={'model.bin','config.json','tokenizer.json'}
FILES=REQUIRED|{'vocabulary.json','vocabulary.txt','preprocessor_config.json','README.md','LICENSE','LICENSE.txt'}
CATALOG=[('Whisper Tiny','Systran/faster-whisper-tiny'),('Whisper Base','Systran/faster-whisper-base'),('Whisper Small','Systran/faster-whisper-small')]


def repo_id(value):
    value=value.strip()
    if value.startswith('https://'):
        url=urlparse(value)
        if url.hostname!='huggingface.co' or url.query or url.fragment:raise ValueError('Нужна ссылка на репозиторий huggingface.co')
        value=url.path.strip('/')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*',value):raise ValueError('Введите owner/model или ссылку Hugging Face')
    if any(part.endswith('.') or '..' in part for part in value.split('/')):raise ValueError('Некорректное имя репозитория')
    return value


def validate_model(path):
    path=Path(path)
    missing=[name for name in REQUIRED if not (path/name).is_file() or (path/name).stat().st_size==0]
    if missing:raise ValueError('В папке отсутствуют: '+', '.join(missing))
    json.loads((path/'config.json').read_text());json.loads((path/'tokenizer.json').read_text())
    import ctranslate2
    if not ctranslate2.contains_model(str(path)):raise ValueError('Это не модель CTranslate2')
    return path


def installed():
    folder=app_data()/'Models';folder.mkdir(exist_ok=True)
    return sorted([p for p in folder.iterdir() if p.is_dir() and not p.name.startswith('.') and all((p/n).is_file() for n in REQUIRED)])


class ModelTask(QThread):
    status=Signal(str);progress=Signal(int);result=Signal(dict);failed=Signal(str)
    def __init__(self,operation,value,destination=None,metadata=None,parent=None):
        super().__init__(parent);self.operation=operation;self.value=value;self.destination=destination;self.metadata=metadata;self.cancelled=threading.Event()
    def check(self):
        if self.cancelled.is_set():raise InterruptedError('Операция отменена')
    def run(self):
        try:
            if self.operation=='inspect':
                from huggingface_hub import HfApi
                name=repo_id(self.value);info=HfApi().model_info(name,files_metadata=True,timeout=20)
                files={f.rfilename:f for f in info.siblings}
                if not REQUIRED<=files.keys():raise ValueError('Репозиторий не содержит готовую модель faster-whisper/CTranslate2')
                if not info.sha:raise ValueError('Не удалось получить версию модели')
                size=sum((f.size or 0) for n,f in files.items() if n in FILES)
                card=info.card_data.to_dict() if info.card_data else {}
                self.check();self.result.emit(dict(repo=name,revision=info.sha,size=size,license=str(card.get('license','Не указана')),files=sorted(FILES&files.keys())))
            elif self.operation=='download':self.download()
            else:self.copy_model()
        except Exception as exc:self.failed.emit(str(exc))
    def download(self):
        from huggingface_hub import hf_hub_download
        from tqdm.auto import tqdm
        task=self;meta=self.metadata;total=max(1,meta['size']);done=0
        class Progress(tqdm):
            def update(self,n=1):
                task.check();result=super().update(n);task.progress.emit(min(99,int((done+self.n)*100/total)));return result
        root=app_data()/'Models';root.mkdir(exist_ok=True)
        name=meta['repo'].replace('/','--')+'--'+meta['revision'][:12]
        temporary=root/('.partial-'+name);destination=root/name
        if destination.exists():validate_model(destination);self.result.emit({'path':str(destination)});return
        for filename in meta['files']:
            self.check();self.status.emit('Загрузка: '+filename)
            path=hf_hub_download(meta['repo'],filename,revision=meta['revision'],local_dir=temporary,tqdm_class=Progress)
            done+=Path(path).stat().st_size
        self.check();validate_model(temporary)
        atomic_json(temporary/'callnote-model.json',meta)
        temporary.rename(destination);self.progress.emit(100);self.result.emit({'path':str(destination)})
    def copy_model(self):
        source=validate_model(self.value)
        if self.operation=='import':
            root=app_data()/'Models';root.mkdir(exist_ok=True)
            suffix=hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:8]
            destination=root/(source.name+'-'+suffix)
        else:destination=Path(self.destination)/source.name
        if destination.resolve()==source.resolve():self.result.emit({'path':str(source)});return
        if destination.exists():raise ValueError('Папка назначения уже существует: '+destination.name)
        temporary=destination.with_name('.copy-'+destination.name)
        if temporary.exists():raise ValueError('Осталась незавершённая копия. Выберите другую папку назначения.')
        temporary.mkdir(parents=True)
        names=sorted(n for n in FILES|{'callnote-model.json'} if (source/n).is_file())
        hashes={}
        try:
            for index,name in enumerate(names):
                self.status.emit('Копирование: '+name);digest=hashlib.sha256()
                with (source/name).open('rb') as src,(temporary/name).open('wb') as dst:
                    while True:
                        self.check();block=src.read(1024*1024)
                        if not block:break
                        dst.write(block);digest.update(block)
                hashes[name]=digest.hexdigest();self.progress.emit(int((index+1)*100/len(names)))
            validate_model(temporary);atomic_json(temporary/'checksums.json',hashes);self.check();temporary.rename(destination)
        except Exception:
            shutil.rmtree(temporary,ignore_errors=True);raise
        self.result.emit({'path':str(destination)})
