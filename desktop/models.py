"""Qt process controller: network and native model checks never execute in the GUI process."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from PySide6.QtCore import QObject,QProcess,QTimer,Signal
from core import app_data
from model_store import REQUIRED,FILES,CATALOG,repo_id,validate_model,installed

class ModelTask(QObject):
    status=Signal(str);progress=Signal(int);result=Signal(dict);failed=Signal(str);finished=Signal()
    def __init__(self,operation,value,destination=None,metadata=None,parent=None):
        super().__init__(parent);self.operation=operation;self.value=value;self.destination=destination;self.metadata=metadata
        self.process=QProcess(self);self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.readyReadStandardError.connect(self.read_error);self.process.finished.connect(self.completed)
        self.process.errorOccurred.connect(self.process_error)
        self.buffer=b'';self.pending_result=None;self.error=None;self.last_activity=time.monotonic();self.ended=False;self.request_path=None
        self.watchdog=QTimer(self);self.watchdog.setInterval(500);self.watchdog.timeout.connect(self.check_timeout)
    def isRunning(self):return not self.ended and self.process.state()!=QProcess.NotRunning
    def start(self):
        folder=app_data()/'Jobs';folder.mkdir(exist_ok=True)
        fd,path=tempfile.mkstemp(suffix='.json',dir=folder);self.request_path=Path(path)
        with os.fdopen(fd,'w') as output:json.dump(dict(operation=self.operation,value=self.value,destination=self.destination,metadata=self.metadata),output)
        self.last_activity=time.monotonic();self.watchdog.start()
        arguments=['--model-job',path]
        if not getattr(sys,'frozen',False):arguments.insert(0,str(Path(__file__).with_name('app.py')))
        self.process.start(sys.executable,arguments)
    def cancel(self):
        if not self.isRunning():return
        self.error='Операция отменена. Полученная часть сохранена; можно повторить загрузку.'
        self.status.emit('Отмена…');self.process.kill()
    def check_timeout(self):
        if self.isRunning() and time.monotonic()-self.last_activity>35:
            self.error='Загрузка не отвечает более 35 секунд. Нажмите «Повторить» — скачанная часть сохранена.';self.process.kill()
    def read_output(self):
        self.buffer+=bytes(self.process.readAllStandardOutput())
        while b'\n' in self.buffer:
            line,self.buffer=self.buffer.split(b'\n',1)
            try:event=json.loads(line)
            except ValueError:continue
            self.last_activity=time.monotonic();kind=event.get('event')
            if kind=='status':self.status.emit(event['text'])
            elif kind=='progress':self.progress.emit(event['value']);self.status.emit(event.get('text',''))
            elif kind=='result':self.pending_result=event['data']
            elif kind=='error':self.error=event['text']
    def read_error(self):
        text=bytes(self.process.readAllStandardError()).decode(errors='replace')
        if text:
            try:
                with (app_data()/'model-download.log').open('a',encoding='utf-8') as log:log.write(text[-8000:])
            except OSError:pass
    def process_error(self,error):
        if error==QProcess.FailedToStart:self.error='Не удалось запустить загрузчик модели.';self.completed(-1,QProcess.CrashExit)
    def completed(self,code,status):
        if self.ended:return
        self.read_output();self.read_error();self.ended=True;self.watchdog.stop()
        if self.request_path:self.request_path.unlink(missing_ok=True)
        if self.error:self.failed.emit(self.error)
        elif code==0 and self.pending_result is not None:self.result.emit(self.pending_result)
        else:self.failed.emit('Загрузчик завершился с ошибкой. Нажмите «Повторить».')
        self.finished.emit()
