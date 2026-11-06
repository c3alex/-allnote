"""Callnote desktop. Python 3.11+, Windows/macOS."""
import sys
import json
import wave
import queue
import threading
from pathlib import Path
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QThread, Signal, QUrl, QDateTime
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QPushButton, QFileDialog, QComboBox, QLabel, QTableWidget,
    QTableWidgetItem, QSlider, QCheckBox, QDateTimeEdit, QMessageBox)


def stamp(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}'


def export_text(rows, timestamps=True, speakers=True, continuous=False, start=None):
    parts = []
    for row in rows:
        prefix = []
        if timestamps:
            time = (start + timedelta(seconds=row['start'])).strftime('%Y-%m-%d %H:%M:%S') if start else stamp(row['start'])
            prefix.append(f'[{time}]')
        if speakers and row.get('speaker'):
            prefix.append(row['speaker'] + ':')
        parts.append(' '.join(prefix + [row['text']]))
    return (' ' if continuous else '\n\n').join(parts)


class Recognizer(QThread):
    segment = Signal(dict)
    status = Signal(str)
    failed = Signal(str)
    recording = Signal(str)
    capture_started = Signal(str)

    def __init__(self, model, language, source, parent=None):
        super().__init__(parent)
        self.model_name, self.language, self.source = model, language, source
        self.stop_event = threading.Event()

    def run(self):
        try:
            from faster_whisper import WhisperModel
            self.status.emit('Загрузка модели (при первом запуске требуется интернет)…')
            model = WhisperModel(self.model_name, device='cpu', compute_type='int8')
            if self.stop_event.is_set():
                return
            if self.source:
                self.decode(model, self.source, 0)
            else:
                self.capture(model)
        except Exception as exc:
            self.failed.emit(str(exc))

    def decode(self, model, audio, offset):
        self.status.emit('Распознавание…')
        segments, _ = model.transcribe(audio, language=self.language, vad_filter=True,
                                      word_timestamps=True, beam_size=3)
        for segment in segments:
            if self.stop_event.is_set() and self.source:
                break
            self.segment.emit(dict(start=offset + segment.start, end=offset + segment.end,
                speaker='', text=segment.text.strip(), words=[dict(start=offset+w.start,
                end=offset+w.end, text=w.word) for w in (segment.words or [])]))

    def capture(self, model):
        import numpy as np
        import sounddevice as sd
        blocks = queue.Queue(maxsize=240)
        overflow = threading.Event()
        def callback(data, frames, timing, status):
            if status:
                overflow.set()
            try:
                blocks.put_nowait(data.copy())
            except queue.Full:
                overflow.set()
                self.stop_event.set()
        destination = Path.home() / 'Documents' / 'Callnote Recordings'
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.wav')
        offset, pending, size = 0.0, [], 0
        with wave.open(str(path), 'wb') as recording:
            recording.setnchannels(1)
            recording.setsampwidth(2)
            recording.setframerate(16000)
            with sd.InputStream(samplerate=16000, channels=1, dtype='float32',
                                blocksize=8000, callback=callback):
                self.capture_started.emit(datetime.now().isoformat(timespec='seconds'))
                self.status.emit('Запись микрофона · текст появляется блоками около 5 секунд')
                while not self.stop_event.is_set():
                    try:
                        data = blocks.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    recording.writeframes((np.clip(data, -1, 1) * 32767).astype('<i2').tobytes())
                    pending.append(data[:, 0]); size += len(data)
                    if size >= 80000:
                        samples = np.concatenate(pending)
                        self.decode(model, samples, offset)
                        offset += size / 16000
                        pending, size = [], 0
            while not blocks.empty():
                data = blocks.get_nowait()
                recording.writeframes((np.clip(data, -1, 1) * 32767).astype('<i2').tobytes())
                pending.append(data[:, 0])
            if pending:
                samples = np.concatenate(pending)
                for index in range(0, len(samples), 80000):
                    self.decode(model, samples[index:index+80000], offset+index/16000)
        self.recording.emit(str(path))
        if overflow.is_set():
            self.failed.emit('Аудиобуфер переполнен или устройство пропустило данные. Запись может содержать пропуски. Выберите более быструю модель.')


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Callnote — локальная расшифровка')
        self.resize(1100, 760)
        self.worker = None
        self.rows, self.audio_path = [], ''
        root = QWidget(); self.setCentralWidget(root)
        layout = QVBoxLayout(root); layout.setContentsMargins(24,24,24,24); layout.setSpacing(15)
        heading = QLabel('Callnote'); heading.setStyleSheet('font-size:28px;font-weight:700')
        layout.addWidget(heading)
        toolbar = QHBoxLayout(); layout.addLayout(toolbar)
        self.model = QComboBox(); self.model.addItems(['tiny', 'base', 'small'])
        self.language = QComboBox(); self.language.addItems(['Авто', 'Русский', 'English'])
        toolbar.addWidget(QLabel('Модель')); toolbar.addWidget(self.model)
        self.import_model = QPushButton('Модель из папки'); self.import_model.clicked.connect(self.pick_model); toolbar.addWidget(self.import_model)
        toolbar.addWidget(self.language)
        self.import_audio = QPushButton('Открыть аудио'); self.import_audio.clicked.connect(self.open_audio); toolbar.addWidget(self.import_audio)
        self.transcribe = QPushButton('Расшифровать'); self.transcribe.clicked.connect(lambda: self.begin(False)); toolbar.addWidget(self.transcribe)
        self.mic = QPushButton('Записать микрофон'); self.mic.clicked.connect(lambda: self.begin(True)); toolbar.addWidget(self.mic)
        self.stop = QPushButton('Стоп'); self.stop.setEnabled(False); self.stop.clicked.connect(self.stop_worker); toolbar.addWidget(self.stop)
        self.file_label = QLabel('Откройте файл или начните запись микрофона'); layout.addWidget(self.file_label)
        self.player = QMediaPlayer(); self.output = QAudioOutput(); self.player.setAudioOutput(self.output)
        controls = QHBoxLayout(); layout.addLayout(controls)
        play = QPushButton('▶ / Ⅱ'); play.clicked.connect(self.toggle_play); controls.addWidget(play)
        self.seek = QSlider(Qt.Horizontal); controls.addWidget(self.seek)
        self.clock = QLabel('00:00:00'); controls.addWidget(self.clock)
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.player.durationChanged.connect(lambda duration: self.seek.setRange(0, duration))
        self.player.positionChanged.connect(self.position)
        self.player.errorOccurred.connect(lambda *args: self.status.setText('Аудиоплеер: '+self.player.errorString()))
        self.table = QTableWidget(0, 3); self.table.setHorizontalHeaderLabels(['Время', 'Говорящий (вручную)', 'Текст'])
        self.table.horizontalHeader().setStretchLastSection(True); self.table.setColumnWidth(1,180)
        self.table.cellClicked.connect(self.jump); layout.addWidget(self.table)
        options = QHBoxLayout(); layout.addLayout(options)
        self.times = QCheckBox('Метки времени'); self.times.setChecked(True)
        self.speakers = QCheckBox('Говорящие'); self.speakers.setChecked(True)
        self.plain = QCheckBox('Сплошной текст'); self.absolute = QCheckBox('Время встречи')
        self.start = QDateTimeEdit(QDateTime.currentDateTime()); self.start.setDisplayFormat('dd.MM.yyyy HH:mm:ss')
        for item in [self.times,self.speakers,self.plain,self.absolute,self.start]: options.addWidget(item)
        actions = QHBoxLayout(); layout.addLayout(actions)
        for label, callback in [('Копировать',self.copy),('Экспорт TXT',self.export),('Сохранить проект',self.save_project),('Открыть проект',self.open_project)]:
            button = QPushButton(label); button.clicked.connect(callback); actions.addWidget(button)
        self.status = QLabel('Локальное распознавание · первая загрузка модели требует интернета'); self.status.setWordWrap(True); layout.addWidget(self.status)

    def pick_model(self):
        path = QFileDialog.getExistingDirectory(self,'Папка модели CTranslate2')
        if path:
            if not (Path(path)/'model.bin').is_file():
                QMessageBox.warning(self,'Модель','Нужна совместимая модель faster-whisper/CTranslate2 с model.bin.'); return
            self.model.addItem(path); self.model.setCurrentText(path)

    def set_audio(self,path):
        self.audio_path = path
        self.player.setSource(QUrl.fromLocalFile(path)); self.file_label.setText(path)

    def open_audio(self):
        path,_ = QFileDialog.getOpenFileName(self,'Аудио','','Аудио (*.wav *.mp3 *.m4a *.flac *.ogg *.mp4);;Все файлы (*)')
        if path: self.set_audio(path); self.rows=[]; self.table.setRowCount(0)

    def begin(self,microphone):
        if self.worker and self.worker.isRunning(): return
        if not microphone and not self.audio_path: return
        if self.rows and QMessageBox.question(self,'Новая расшифровка','Заменить текущую расшифровку? Сначала сохраните нужные правки.',QMessageBox.Yes|QMessageBox.No)!=QMessageBox.Yes: return
        self.player.pause(); self.rows=[]; self.table.setRowCount(0)
        if microphone: self.start.setDateTime(QDateTime.currentDateTime()); self.player.setSource(QUrl()); self.audio_path=''
        self.worker = Recognizer(self.model.currentText(),[None,'ru','en'][self.language.currentIndex()],'' if microphone else self.audio_path,self)
        self.worker.segment.connect(self.add_segment); self.worker.status.connect(self.status.setText)
        self.worker.failed.connect(lambda error: QMessageBox.warning(self,'Ошибка',error))
        self.worker.capture_started.connect(lambda value: self.start.setDateTime(QDateTime.fromString(value,Qt.ISODate)))
        self.worker.recording.connect(self.set_audio); self.worker.finished.connect(self.finished)
        self.busy(True); self.worker.start()

    def busy(self,value):
        for control in [self.import_audio,self.transcribe,self.mic,self.model,self.language,self.import_model]: control.setEnabled(not value)
        self.stop.setEnabled(value)

    def stop_worker(self):
        if self.worker: self.worker.stop_event.set(); self.status.setText('Завершение обработки…'); self.stop.setEnabled(False)

    def finished(self):
        self.busy(False); self.status.setText('Обработка завершена. Имена говорящих можно указать в таблице.')

    def add_segment(self,row):
        index=len(self.rows); self.rows.append(row); self.table.insertRow(index)
        time=QTableWidgetItem(stamp(row['start'])); time.setFlags(time.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(index,0,time)
        self.table.setItem(index,1,QTableWidgetItem(row.get('speaker','')))
        self.table.setItem(index,2,QTableWidgetItem(row['text'])); self.table.scrollToBottom()

    def sync(self):
        for index,row in enumerate(self.rows):
            row['speaker']=self.table.item(index,1).text(); row['text']=self.table.item(index,2).text()

    def text(self):
        self.sync()
        return export_text(self.rows,self.times.isChecked(),self.speakers.isChecked(),self.plain.isChecked(),self.start.dateTime().toPython() if self.absolute.isChecked() else None)

    def copy(self): QApplication.clipboard().setText(self.text()); self.status.setText('Текст скопирован')
    def export(self):
        path,_=QFileDialog.getSaveFileName(self,'Экспорт','','Текст (*.txt)')
        if path: self.write(path,self.text())

    def write(self,path,text):
        try: Path(path).write_text(text,encoding='utf-8')
        except OSError as exc: QMessageBox.warning(self,'Сохранение',str(exc))

    def save_project(self):
        self.sync(); path,_=QFileDialog.getSaveFileName(self,'Сохранить проект','','Callnote (*.json)')
        if path: self.write(path,json.dumps(dict(version=1,audio=self.audio_path,start=self.start.dateTime().toString(Qt.ISODate),segments=self.rows),ensure_ascii=False,indent=2))

    def open_project(self):
        if self.worker and self.worker.isRunning(): return
        path,_=QFileDialog.getOpenFileName(self,'Открыть проект','','Callnote (*.json)')
        if not path:return
        try:
            data=json.loads(Path(path).read_text(encoding='utf-8'))
            assert data['version']==1 and isinstance(data['segments'],list)
            for row in data['segments']:
                assert isinstance(row['text'],str) and isinstance(row.get('speaker',''),str)
                assert 0<=float(row['start'])<=float(row['end'])
            self.rows=[]; self.table.setRowCount(0)
            for row in data['segments']: self.add_segment(row)
            self.start.setDateTime(QDateTime.fromString(data['start'],Qt.ISODate))
            self.set_audio(data.get('audio',''))
            if not Path(self.audio_path).is_file(): self.status.setText('Аудиофайл проекта не найден по сохранённому пути.')
        except Exception as exc: QMessageBox.warning(self,'Проект','Не удалось открыть проект: '+str(exc))

    def toggle_play(self):
        if self.player.playbackState()==QMediaPlayer.PlayingState:self.player.pause()
        else:self.player.play()

    def jump(self,row,column):
        if column==0:self.player.setPosition(int(self.rows[row]['start']*1000))

    def position(self,ms):
        if not self.seek.isSliderDown():self.seek.setValue(ms)
        self.clock.setText(stamp(ms/1000))
        for index,row in enumerate(self.rows):
            if row['start']<=ms/1000<row['end']:
                self.table.item(index,0).setBackground(Qt.lightGray)
            else:self.table.item(index,0).setBackground(Qt.white)

    def closeEvent(self,event):
        if self.worker and self.worker.isRunning():
            self.stop_worker(); QMessageBox.information(self,'Обработка','Дождитесь завершения обработки и закройте окно повторно.'); event.ignore()
        else:event.accept()


if __name__=='__main__':
    app=QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setStyleSheet('QWidget{font-size:13px} QPushButton{padding:8px} QTableWidget{background:white} QMainWindow{background:#f3f5f1}')
    window=Window(); window.show(); sys.exit(app.exec())
