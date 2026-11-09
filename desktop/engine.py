"""Capture supervision and ASR. Audio is persisted before decoding."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import queue
from datetime import datetime
from PySide6.QtCore import QThread,Signal
from core import app_data,atomic_json,mix_recording


def helper_path():
    if getattr(sys,'frozen',False):return Path(sys._MEIPASS)/'native'/'AudioCapture'
    return Path(__file__).resolve().parent.parent/'build'/'native'/'AudioCapture'


class SessionWorker(QThread):
    segment=Signal(dict); status=Signal(str); failed=Signal(str); started_at=Signal(str)
    level=Signal(str,float); capture_ready=Signal(); capture_done=Signal(); recording=Signal(str,str)
    def __init__(self,model,language,source='',mode='both',parent=None):
        super().__init__(parent);self.model=model;self.language=language;self.source=source;self.mode=mode
        self.stop_event=threading.Event();self.cancel_event=threading.Event();self.had_error=False
        self.folder=None;self.chunks=[];self.proc=None
    def stop(self):self.stop_event.set()
    def cancel(self):self.stop_event.set();self.cancel_event.set()
    def error(self,text):self.had_error=True;self.failed.emit(text)
    def decode(self,model,audio,offset=0,source='file'):
        segments,_=model.transcribe(audio,language=self.language,word_timestamps=True,vad_filter=True,beam_size=3)
        for s in segments:
            if self.cancel_event.is_set():break
            self.segment.emit(dict(start=offset+s.start,end=offset+s.end,text=s.text.strip(),
                speaker={'microphone':'Вы','system':'Собеседники'}.get(source,''),source=source,
                words=[dict(start=offset+w.start,end=offset+w.end,text=w.word) for w in s.words or []]))
    def run(self):
        capture_thread=None;capture_done=threading.Event();pending=queue.Queue()
        try:
            if not self.source:
                if sys.platform!='darwin':raise RuntimeError('Системный захват этой версии предназначен для macOS.')
                if not helper_path().is_file():raise RuntimeError('Модуль захвата отсутствует. Пересоберите приложение.')
                self.folder=app_data()/'Recordings'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
                self.folder.mkdir(parents=True)
                capture_thread=threading.Thread(target=self.capture,args=(pending,capture_done),daemon=True)
                capture_thread.start()
            self.status.emit('Загрузка локальной модели…')
            import onnxruntime
            onnxruntime.disable_telemetry_events()
            from faster_whisper import WhisperModel
            model=WhisperModel(self.model,device='cpu',compute_type='int8',local_files_only=True)
            if self.source:
                if not self.cancel_event.is_set():self.decode(model,self.source)
            else:
                while not capture_done.is_set() or not pending.empty():
                    if self.cancel_event.is_set():break
                    try:event=pending.get(timeout=.2)
                    except queue.Empty:continue
                    self.status.emit(f'Распознавание · ожидают обработки: {pending.qsize()} фрагм.')
                    self.decode(model,event['path'],event['start'],event['source'])
        except Exception as exc:self.error(str(exc));self.stop_event.set()
        finally:
            if capture_thread:
                self.stop_event.set();capture_thread.join(timeout=20)
                if capture_thread.is_alive():
                    if self.proc:self.proc.kill()
                    capture_thread.join(timeout=5)
                    self.error('Захват остановлен принудительно. Проверьте сохранённые фрагменты.')
                try:
                    if self.chunks:
                        result=mix_recording(self.chunks,self.folder/'recording.wav')
                        self.recording.emit(result,str(self.folder))
                except Exception as exc:self.error('Фрагменты сохранены, но сборка дорожки не удалась: '+str(exc))
    def capture(self,pending,done):
        try:
            with (self.folder/'native.log').open('w') as log:
                self.proc=subprocess.Popen([str(helper_path()),str(self.folder),self.mode],stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,stderr=log,text=True,bufsize=1)
                def stop_monitor():
                    self.stop_event.wait()
                    try:self.proc.stdin.write('stop\n');self.proc.stdin.flush()
                    except (OSError,ValueError,AttributeError):pass
                threading.Thread(target=stop_monitor,daemon=True).start()
                for line in self.proc.stdout:
                    try:event=json.loads(line)
                    except ValueError:continue
                    kind=event.get('event')
                    if kind=='started':
                        self.started_at.emit(event['date'])
                        atomic_json(self.folder/'capture.json',{'start':event['date'],'mode':self.mode})
                    elif kind=='ready':self.capture_ready.emit()
                    elif kind=='level':self.level.emit(event['source'],float(event['peak']))
                    elif kind=='chunk':
                        self.chunks.append(event)
                        with (self.folder/'chunks.jsonl').open('a',encoding='utf-8') as manifest:manifest.write(json.dumps(event)+'\n')
                        pending.put(event)
                    elif kind=='error':self.error('Захват звука: '+event.get('message','Ошибка'));self.stop_event.set()
                code=self.proc.wait()
                if code and not self.had_error:self.error('Модуль захвата завершился с ошибкой. Подробности в папке записи.')
        except Exception as exc:self.error(str(exc))
        finally:
            self.stop_event.set();done.set();self.capture_done.emit()
