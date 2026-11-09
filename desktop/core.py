"""Storage and transcript formatting; no GUI dependencies."""
from datetime import datetime, timedelta
from pathlib import Path
import json
import os
import math
import wave


def app_data():
    import sys
    if os.environ.get('CALLNOTE_DATA_DIR'):
        path=Path(os.environ['CALLNOTE_DATA_DIR'])
    elif sys.platform=='darwin':
        path=Path.home()/'Library'/'Application Support'/'Callnote'
    else:
        path=Path(os.environ.get('LOCALAPPDATA',Path.home()))/'Callnote'
    path.mkdir(parents=True,exist_ok=True)
    return path


def atomic_json(path,data):
    path=Path(path); temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8'); temp.replace(path)


def stamp(seconds):
    seconds=max(0,int(seconds))
    return f'{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}'


def export_text(rows,timestamps=True,speakers=True,continuous=False,start=None):
    parts=[]
    for row in sorted(rows,key=lambda r:r['start']):
        prefix=[]
        if timestamps:
            time=(start+timedelta(seconds=row['start'])).strftime('%Y-%m-%d %H:%M:%S') if start else stamp(row['start'])
            prefix.append(f'[{time}]')
        if speakers and row.get('speaker'):prefix.append(row['speaker']+':')
        parts.append(' '.join(prefix+[row['text']]))
    return (' ' if continuous else '\n\n').join(parts)


def validate_project(data):
    if data.get('version') not in (1,2) or not isinstance(data.get('segments'),list):
        raise ValueError('Неизвестный формат проекта')
    for row in data['segments']:
        if not isinstance(row.get('text'),str) or not isinstance(row.get('speaker',''),str):raise ValueError('Некорректный текст')
        a,b=float(row['start']),float(row['end'])
        if not math.isfinite(a) or not math.isfinite(b) or not 0<=a<=b:raise ValueError('Некорректное время')
    datetime.fromisoformat(data['start'])
    return data


def mix_recording(chunks,destination):
    """Bounded-memory merge into stereo: microphone left, system right."""
    import numpy as np
    if not chunks:return ''
    groups={}
    for event in chunks:groups.setdefault(round(event['start']*16000),[]).append(event)
    cursor=0
    sources={e['source'] for e in chunks}
    with wave.open(str(destination),'wb') as out:
        out.setnchannels(2);out.setsampwidth(2);out.setframerate(16000)
        for offset,events in sorted(groups.items()):
            while cursor<offset:
                count=min(80000,offset-cursor);out.writeframes(b'\0'*(count*4));cursor+=count
            tracks=[]
            for event in events:
                with wave.open(event['path'],'rb') as audio:
                    values=np.frombuffer(audio.readframes(audio.getnframes()),dtype='<i2')
                tracks.append((event['source'],values))
            count=max(len(v) for _,v in tracks);buffer=np.zeros((count,2),dtype='<i2')
            for source,values in tracks:buffer[:len(values),0 if source=='microphone' else 1]=values
            # Both sources retain their timeline. Single-source recording plays in both ears.
            if len(sources)==1:buffer[:,1 if 'microphone' in sources else 0]=buffer[:,0 if 'microphone' in sources else 1]
            skip=max(0,cursor-offset)
            if skip<count:out.writeframes(buffer[skip:].tobytes());cursor=offset+count
    return str(destination)
