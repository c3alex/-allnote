"""Model paths and validation; safe to import before Qt's first frame."""
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse
from core import app_data
REQUIRED={'model.bin','config.json','tokenizer.json'}
FILES=REQUIRED|{'vocabulary.json','vocabulary.txt','preprocessor_config.json','README.md','LICENSE','LICENSE.txt'}
# Bundled snapshot of the SYSTRAN collection, available before the Hub replies.
CATALOG = {
    'Systran/faster-' + name: likes for name, likes in (
        ('whisper-tiny', 36), ('whisper-base', 55), ('whisper-small', 90),
        ('whisper-medium', 79), ('whisper-large-v1', 3), ('whisper-large-v2', 49),
        ('whisper-large-v3', 712), ('whisper-tiny.en', 10), ('whisper-base.en', 9),
        ('whisper-small.en', 26), ('whisper-medium.en', 4),
        ('distil-whisper-small.en', 3), ('distil-whisper-medium.en', 4),
        ('distil-whisper-large-v2', 21), ('distil-whisper-large-v3', 71),
    )
}

def model_card(repo, likes=None, languages=(), license_name=''):
    publisher, name = repo.split('/', 1)
    known = repo in CATALOG
    if known:
        english_only = name.endswith('.en') or 'distil-whisper' in name
        languages = ('en',) if english_only else ('ru', 'en', 'другие')
    descriptions = {
        'tiny': 'Лёгкая модель для быстрой черновой расшифровки.',
        'base': 'Баланс скорости и качества для повседневных записей.',
        'small': 'Более крупная модель для расшифровки разговоров.',
        'medium': 'Модель среднего размера. Требует больше памяти.',
        'large': 'Крупная модель. На CPU обработка может занять больше времени.',
    }
    tier = name.split('whisper-', 1)[-1].split('-')[0].split('.')[0]
    description = descriptions.get(tier, 'Модель распознавания речи в формате CTranslate2.') if known else 'Модель распознавания речи в формате CTranslate2.'
    if known and 'distil-whisper' in name:
        description = 'Уменьшенная англоязычная версия Whisper для быстрого распознавания.'
    return dict(repo=repo, title=name.removeprefix('faster-').replace('-', ' ').title(),
                publisher=publisher, developer=('Hugging Face · Distil-Whisper' if 'distil-whisper' in name else 'OpenAI') if known else publisher,
                description=description, likes=likes, languages=list(languages), license=license_name)


def local_repo(path):
    try:
        return repo_id(json.loads((path / 'callnote-model.json').read_text())['repo'])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None

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
    return sorted([p for p in folder.iterdir() if p.is_dir() and not p.is_symlink() and not p.name.startswith('.') and all((p/n).is_file() for n in REQUIRED)])

def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()
