"""Model paths and validation; safe to import before Qt's first frame."""
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse
from core import app_data
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

def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()
