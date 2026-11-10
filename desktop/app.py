"""Callnote 0.2.5 — macOS desktop recorder and local transcription."""
import time
PYTHON_ENTRY_NS = time.perf_counter_ns()
import json
import os
from pathlib import Path
import sys
# A model job must exit before importing any Qt widgets or creating windows.
if __name__ == '__main__' and len(sys.argv) == 3 and sys.argv[1] == '--model-job':
    if sys.stdout is None:sys.stdout=open(1,'w',encoding='utf-8',buffering=1,closefd=False)
    if sys.stderr is None:sys.stderr=open(2,'w',encoding='utf-8',buffering=1,closefd=False)
    from model_job import main as run_model_job
    sys.exit(run_model_job(sys.argv[2]))
from datetime import datetime
from PySide6.QtCore import Qt,QTimer,QDateTime,QUrl,QSignalBlocker,QFile
from PySide6.QtGui import QColor,QDesktopServices,QKeySequence
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QDialog,QVBoxLayout,QHBoxLayout,
    QGridLayout,QPushButton,QLabel,QComboBox,QFileDialog,QMessageBox,QTableWidget,QTableWidgetItem,
    QHeaderView,QSlider,QCheckBox,QDateTimeEdit,QTextEdit,QLineEdit,QProgressBar,
    QKeySequenceEdit,QSplitter,QFrame,QStackedWidget,QScrollArea)
from core import app_data,atomic_json,stamp,export_text,validate_project
from engine import SessionWorker,helper_path
from models import ModelTask
from model_store import CATALOG, installed, local_repo, model_card
from hotkeys import Hotkeys,DEFAULTS

STYLE='''
QWidget{font-family:"Helvetica Neue","Segoe UI";font-size:13px;color:#293d32;background:#f5f6f2}
QMainWindow,QDialog{background:#f5f6f2} QLabel{background:transparent}
QPushButton{background:#fff;border:1px solid #dce4d8;border-radius:8px;padding:9px 13px}
QPushButton:hover{background:#e8f0e5} QPushButton:disabled{color:#a6afa3;background:#f1f3ee}
QPushButton[primary="true"]{background:#32664d;color:white;border:0}
QComboBox,QLineEdit,QDateTimeEdit,QKeySequenceEdit{background:white;border:1px solid #dce4d8;border-radius:6px;padding:7px}
QTableWidget,QTextEdit,QListWidget{background:white;border:1px solid #dce4d8;border-radius:9px;selection-background-color:#e2ecdf;selection-color:#293d32}
QHeaderView::section{background:#edf2e9;padding:10px;border:0;text-align:left}
QProgressBar{background:#e7ece3;border:0;border-radius:4px;height:8px;text-align:center}
QProgressBar::chunk{background:#82a577;border-radius:4px}
QCheckBox{spacing:7px} QSlider::groove:horizontal{height:5px;background:#dce5d7} QSlider::handle:horizontal{background:#497c5b;width:13px;margin:-4px 0;border-radius:6px}
'''

def button(text,callback,primary=False):
    widget=QPushButton(text);widget.clicked.connect(callback);widget.setProperty('primary',primary);return widget

def note(text):
    label=QLabel(text);label.setWordWrap(True);label.setStyleSheet('color:#788675;font-size:11px');return label

class ModelPage(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.owner = parent
        self.task = None
        self.last_request = None
        self.confirm_path = None
        self.visible_limit = 30
        self.cards = {repo: model_card(repo, likes) for repo, likes in CATALOG.items()}
        self.cache = app_data() / 'model-catalog.json'
        try:
            cached = json.loads(self.cache.read_text())
            self.cards.update({card['repo']: card for card in cached})
        except (OSError, ValueError, TypeError, KeyError):
            pass

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 20)
        title = QLabel('Модели')
        title.setStyleSheet('font-size:24px;font-weight:650')
        layout.addWidget(title)
        layout.addWidget(note('Модели Hugging Face для локальной расшифровки. ♥ — отметки «Нравится».'))
        toolbar = QHBoxLayout()
        layout.addLayout(toolbar)
        self.filter = QComboBox()
        self.filter.addItems(['Все модели', 'На диске'])
        toolbar.addWidget(self.filter)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Поиск по названию, издателю или языку')
        toolbar.addWidget(self.search, 1)
        self.update_button = button('Обновить каталог', self.update_catalog)
        toolbar.addWidget(self.update_button)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        self.results = QVBoxLayout(body)
        self.results.setContentsMargins(0, 0, 6, 0)
        self.results.setAlignment(Qt.AlignTop)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        self.filter.currentIndexChanged.connect(self.reset_results)
        self.search.textChanged.connect(self.reset_results)

        custom = QHBoxLayout()
        layout.addLayout(custom)
        self.repo = QLineEdit()
        self.repo.setPlaceholderText('Другая модель: owner/model или ссылка Hugging Face')
        custom.addWidget(self.repo, 1)
        self.custom_install = button('Установить по ссылке', self.install_custom)
        self.import_button = button('Импорт папки', self.import_model)
        custom.addWidget(self.custom_install)
        custom.addWidget(self.import_button)

        self.info = note('Каталог доступен сразу. Данные Hugging Face обновляются в фоне.')
        layout.addWidget(self.info)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.progress.hide()
        controls = QHBoxLayout()
        layout.addLayout(controls)
        self.cancel = button('Отменить', self.cancel_task)
        self.retry = button('Повторить', self.retry_task)
        controls.addWidget(self.cancel)
        controls.addWidget(self.retry)
        controls.addStretch()
        self.cancel.hide()
        self.retry.hide()
        self.refresh()
        QTimer.singleShot(0, self.update_catalog)

    def busy(self):
        return self.task is not None and self.task.isRunning()

    def reset_results(self):
        self.visible_limit = 30
        self.refresh()

    def show_more(self):
        self.visible_limit += 30
        self.refresh()

    def refresh(self):
        while self.results.count():
            widget = self.results.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()

        paths = installed()
        local_by_repo = {}
        for path in paths:
            repo = local_repo(path)
            if repo:
                local_by_repo.setdefault(repo, []).append(path)
        entries = []
        if self.filter.currentIndex() == 0:
            entries.extend((card, local_by_repo.get(repo, [None])[-1])
                           for repo, card in self.cards.items())
            entries.extend((model_card(repo), local[-1]) for repo, local in local_by_repo.items()
                           if repo not in self.cards)
            entries.extend((None, path) for path in paths if not local_repo(path))
        else:
            entries.extend((self.cards.get(repo) or model_card(repo) if repo else None, path)
                           for path in paths for repo in [local_repo(path)])

        query = self.search.text().strip().casefold()
        matches = []
        for card, path in entries:
            if card:
                searchable = ' '.join([card['title'], card['repo'], card['publisher'], *card['languages']])
                if 'ru' in card['languages']:
                    searchable += ' русский'
                if 'en' in card['languages']:
                    searchable += ' английский'
            else:
                searchable = path.name
            if query in searchable.casefold():
                matches.append((card, path))
        for card, path in matches[:self.visible_limit]:
            self.add_card(card, path)
        if len(matches) > self.visible_limit:
            self.results.addWidget(button(
                f'Показать ещё · {self.visible_limit} из {len(matches)}', self.show_more))
        if not matches:
            self.results.addWidget(note('Подходящих моделей нет.'))
        for control in (self.update_button, self.custom_install, self.import_button):
            control.setEnabled(not self.busy())

    def add_card(self, card, path):
        frame = QFrame()
        frame.setStyleSheet('QFrame{background:white;border:1px solid #dce4d8;border-radius:9px}')
        layout = QVBoxLayout(frame)
        name = QLabel(card['title'] if card else path.name)
        name.setTextFormat(Qt.PlainText)
        name.setWordWrap(True)
        name.setStyleSheet('font-size:16px;font-weight:650')
        layout.addWidget(name)
        if card:
            languages = ', '.join({'ru': 'русский', 'en': 'английский'}.get(code, code)
                                  for code in card['languages']) or 'не указаны'
            likes = card['likes'] if card['likes'] is not None else '—'
            details = note(f"{card['repo']} · ♥ {likes}\n{card['description']}\n"
                           f"Разработчик: {card['developer']} · Издатель: {card['publisher']}\n"
                           f"Языки: {languages}" + (f" · {card['license']}" if card.get('license') else ''))
            details.setTextFormat(Qt.PlainText)
            layout.addWidget(details)
        status = 'Установлена' if path else 'Доступна'
        if path:
            try:
                size = sum(file.stat().st_size for file in path.iterdir() if file.is_file())
                status += f' · {size / 1_000_000:.0f} МБ на диске'
            except OSError:
                pass
        layout.addWidget(note(status))
        row = QHBoxLayout()
        layout.addLayout(row)
        if path == self.confirm_path and path is not None:
            row.addWidget(note('Переместить локальную копию в корзину?'))
            row.addWidget(button('В корзину', lambda: self.remove(path)))
            row.addWidget(button('Оставить', lambda: self.confirm_removal(None)))
        elif path:
            row.addWidget(button('Использовать', lambda: self.use(path), True))
            row.addWidget(button('Экспорт', lambda: self.export_model(path)))
            row.addWidget(button('Удалить с диска', lambda: self.confirm_removal(path)))
        else:
            row.addWidget(button('Установить', lambda: self.install(card['repo']), True))
        for i in range(row.count()):
            widget = row.itemAt(i).widget()
            if isinstance(widget, QPushButton):
                widget.setEnabled(not self.busy())
        row.addStretch()
        if card:
            row.addWidget(button('Hugging Face ↗', lambda: QDesktopServices.openUrl(
                QUrl('https://huggingface.co/' + card['repo']))))
        self.results.addWidget(frame)

    def confirm_removal(self, path):
        self.confirm_path = path
        self.refresh()

    def remove(self, path):
        if self.busy() or self.owner.active:
            self.info.setText('Завершите запись или операцию с моделями перед удалением.')
            return
        if path not in installed():
            self.info.setText('Локальная копия уже отсутствует.')
        else:
            file = QFile(str(path))
            if not file.moveToTrash():
                self.info.setText('Не удалось переместить модель в корзину: ' + file.errorString())
                return
            self.owner.refresh_models()
            self.owner.save_settings()
            self.info.setText('Локальная копия перемещена в корзину.')
        self.confirm_removal(None)

    def use(self, path):
        self.owner.refresh_models(str(path))
        self.owner.save_settings()
        self.owner.show_main()

    def launch(self, operation, value='', destination=None):
        if self.busy():
            return
        if self.task:
            self.task.deleteLater()
        self.last_request = (operation, value, destination)
        task = ModelTask(operation, value, destination=destination, parent=self)
        self.task = task
        self.retry.hide()
        self.cancel.show()
        self.progress.show()
        self.progress.setRange(0, 0)
        self.info.setText('Подготовка…')
        task.status.connect(self.info.setText)
        task.progress.connect(self.show_progress)
        task.result.connect(self.result)
        task.failed.connect(self.failure)
        task.finished.connect(self.finished)
        task.start()
        self.refresh()

    def show_progress(self, value):
        self.progress.setRange(0, 100)
        self.progress.setValue(value)

    def update_catalog(self):
        self.launch('catalog')

    def install(self, repo):
        self.launch('download', repo)

    def install_custom(self):
        self.install(self.repo.text())

    def import_model(self):
        path = QFileDialog.getExistingDirectory(self, 'Папка модели CTranslate2')
        if path:
            self.launch('import', path)

    def export_model(self, source):
        path = QFileDialog.getExistingDirectory(self, 'Куда скопировать модель')
        if path:
            self.launch('export', str(source), path)

    def result(self, data):
        if 'catalog' in data:
            cards = data['catalog']
            self.cards = {repo: model_card(repo, likes) for repo, likes in CATALOG.items()}
            self.cards.update({card['repo']: card for card in cards})
            try:
                atomic_json(self.cache, cards)
            except OSError:
                pass
            self.info.setText(f'Каталог обновлён · {len(self.cards)} моделей')
        else:
            self.info.setText('Готово: ' + Path(data['path']).name)
            if self.task.operation != 'export':
                self.owner.refresh_models(data['path'])
                self.owner.save_settings()

    def finished(self):
        self.progress.hide()
        self.cancel.hide()
        self.refresh()

    def failure(self, text):
        if self.task.operation == 'catalog':
            text += ' Показан сохранённый каталог.'
        self.info.setText(text)
        self.retry.show()

    def retry_task(self):
        if self.last_request:
            self.launch(*self.last_request)

    def cancel_task(self):
        if self.task:
            self.task.cancel()


class FloatingWindow(QWidget):
    def __init__(self,owner):
        super().__init__(owner,Qt.Tool|Qt.WindowStaysOnTopHint);self.owner=owner;self.setWindowTitle('Callnote · компактное окно');self.resize(365,240);self.setMinimumWidth(320)
        layout=QVBoxLayout(self);layout.setContentsMargins(18,16,18,16);layout.setSpacing(12)
        row=QHBoxLayout();layout.addLayout(row);brand=QLabel('● Callnote');brand.setStyleSheet('font-weight:700;font-size:16px');row.addWidget(brand);row.addStretch()
        self.clock=QLabel('00:00:00');row.addWidget(self.clock)
        self.state=note('Готов к записи');layout.addWidget(self.state)
        self.text=QLabel('Начните запись — здесь появятся последние фразы.');self.text.setWordWrap(True);self.text.setMinimumHeight(55);layout.addWidget(self.text)
        row=QHBoxLayout();layout.addLayout(row)
        self.record=button('● Запись',owner.toggle_record,True);row.addWidget(self.record)
        row.addWidget(button('Копировать',owner.copy));row.addWidget(button('↗',owner.show_main))
        self.keys=button('Горячие клавиши',owner.shortcuts);layout.addWidget(self.keys)
    def closeEvent(self,event):self.hide();event.ignore()

class Window(QMainWindow):
    def __init__(self):
        super().__init__();self.setWindowTitle('Callnote');self.resize(1210,800);self.setMinimumSize(950,640)
        self.rows=[];self.audio_path='';self.worker=None;self.session_folder=None;self.active=False;self.record_started=None;self.loading=False;self.dirty=False;self.model_page=None
        self.settings={}
        try:self.settings=json.loads((app_data()/'settings.json').read_text())
        except (OSError,ValueError):pass
        root=QWidget();self.setCentralWidget(root);outer=QHBoxLayout(root);outer.setContentsMargins(0,0,0,0);outer.setSpacing(0)
        sidebar=QWidget();sidebar.setFixedWidth(185);sidebar.setStyleSheet('background:#edf1e9');nav=QVBoxLayout(sidebar);nav.setContentsMargins(20,30,20,22);nav.setSpacing(14)
        brand=QLabel('≋ callnote.');brand.setStyleSheet('font-size:24px;font-weight:700');nav.addWidget(brand);nav.addSpacing(22)
        self.meeting_button=button('Текущая встреча',self.show_main);nav.addWidget(self.meeting_button);self.models_button=button('Модели',self.models);nav.addWidget(self.models_button)
        self.keys_button=button('Горячие клавиши',self.shortcuts);nav.addWidget(self.keys_button);self.audio_button=button('Доступ к звуку',self.permissions);nav.addWidget(self.audio_button);nav.addStretch();nav.addWidget(note('Локальное распознавание\nАудио остаётся на Mac\nВерсия 0.2.5'))
        outer.addWidget(sidebar);body=QWidget();layout=QVBoxLayout(body);layout.setContentsMargins(28,28,28,20);layout.setSpacing(16);self.pages=QStackedWidget();self.pages.addWidget(body);outer.addWidget(self.pages,1);self.meeting_page=body;self.shortcut_page=None;self.audio_page=None
        row=QHBoxLayout();layout.addLayout(row);heading=QLabel('Разговор становится текстом.');heading.setStyleSheet('font-size:25px;font-weight:650');row.addWidget(heading);row.addStretch();row.addWidget(button('↗ Компактное окно',self.show_floating))
        self.file_label=note('Новая встреча · выберите источник и локальную модель');layout.addWidget(self.file_label)
        row=QHBoxLayout();layout.addLayout(row);self.source=QComboBox();self.source.addItem('Системный звук + микрофон','both');self.source.addItem('Только системный звук','system');self.source.addItem('Только микрофон','mic');row.addWidget(self.source,1)
        self.record_button=button('● Начать запись',self.toggle_record,True);row.addWidget(self.record_button)
        self.stop_button=button('■ Стоп',self.stop_worker);self.stop_button.setEnabled(False);row.addWidget(self.stop_button)
        self.cancel_button=button('Отменить обработку',self.cancel_worker);self.cancel_button.setEnabled(False);row.addWidget(self.cancel_button)
        row=QHBoxLayout();layout.addLayout(row)
        self.model=QComboBox();self.model.setMinimumWidth(150);self.model.setMaximumWidth(290);row.addWidget(QLabel('Модель'));row.addWidget(self.model,1)
        self.language=QComboBox();self.language.addItems(['Авто','Русский','English']);row.addWidget(self.language)
        self.open_audio_button=button('Открыть аудио',self.open_audio);row.addWidget(self.open_audio_button)
        self.transcribe_button=button('Расшифровать файл',self.transcribe);row.addWidget(self.transcribe_button)
        self.levels={};row=QHBoxLayout();layout.addLayout(row)
        for key,title in [('microphone','Микрофон'),('system','Система')]:
            row.addWidget(note(title));bar=QProgressBar();bar.setRange(0,100);bar.setTextVisible(False);bar.setFixedHeight(7);row.addWidget(bar);self.levels[key]=bar
        self.live=note('Запись не идёт');row.addWidget(self.live)
        split=QSplitter(Qt.Horizontal);layout.addWidget(split,1)
        self.table=QTableWidget(0,3);self.table.setHorizontalHeaderLabels(['Время','Источник / говорящий','Текст']);self.table.setWordWrap(True);self.table.setAlternatingRowColors(False)
        self.table.horizontalHeader().setStretchLastSection(True);self.table.setColumnWidth(0,82);self.table.setColumnWidth(1,140);self.table.verticalHeader().hide();self.table.setShowGrid(False)
        self.table.cellClicked.connect(self.jump);self.table.cellChanged.connect(self.edited);split.addWidget(self.table)
        panel=QWidget();panel.setMinimumWidth(220);panel.setMaximumWidth(285);pl=QVBoxLayout(panel);pl.setContentsMargins(16,0,0,0);pl.addWidget(QLabel('Копировать текст'))
        self.plain=QCheckBox('Сплошной текст');self.times=QCheckBox('Метки времени');self.times.setChecked(True);self.speakers=QCheckBox('Метки говорящих');self.speakers.setChecked(True);self.absolute=QCheckBox('Дата и время встречи')
        for control in [self.plain,self.times,self.speakers,self.absolute]:pl.addWidget(control);control.toggled.connect(self.preview)
        self.start=QDateTimeEdit(QDateTime.currentDateTime());self.start.setDisplayFormat('dd.MM.yyyy HH:mm:ss');self.start.dateTimeChanged.connect(self.preview);pl.addWidget(self.start)
        self.copy_preview=QTextEdit();self.copy_preview.setReadOnly(True);pl.addWidget(self.copy_preview,1);pl.addWidget(button('Копировать текст',self.copy,True));pl.addWidget(button('Экспорт TXT',self.export))
        split.addWidget(panel);split.setSizes([650,245])
        row=QHBoxLayout();layout.addLayout(row);row.addWidget(button('▶ / Ⅱ',self.toggle_play));self.seek=QSlider(Qt.Horizontal);row.addWidget(self.seek,1);self.clock=QLabel('00:00:00');row.addWidget(self.clock)
        self.player=None;self.output=None
        self.seek.sliderMoved.connect(lambda value:self.player.setPosition(value) if self.player else None)
        row=QHBoxLayout();layout.addLayout(row);row.addWidget(button('Сохранить проект',self.save_project));self.open_project_button=button('Открыть проект',self.open_project);row.addWidget(self.open_project_button);row.addWidget(button('Папка записей',self.open_recordings));row.addStretch()
        self.status=note('Выберите или скачайте модель в разделе «Модели».');layout.addWidget(self.status)
        self.mini=FloatingWindow(self);self.hotkeys=None;self.keymap=self.settings.get('hotkeys',dict(DEFAULTS))
        if not os.environ.get('CALLNOTE_NO_HOTKEYS'):
            try:
                self.hotkeys=Hotkeys(self);self.hotkeys.activated.connect(self.hotkey);self.hotkeys.register(self.keymap)
            except Exception as exc:self.status.setText('Горячие клавиши: '+str(exc))
        self.refresh_models(self.settings.get('model'))
        self.timer=QTimer(self);self.timer.timeout.connect(self.tick);self.timer.start(1000)
        self.autosave=QTimer(self);self.autosave.timeout.connect(self.save_recovery);self.autosave.start(2500)
        recovery=app_data()/'recovery.json'
        if recovery.exists():QTimer.singleShot(250,self.offer_recovery)
    def paintEvent(self,event):
        super().paintEvent(event)
        if not getattr(self,'_first_paint',False):
            self._first_paint=True
            QTimer.singleShot(0,self.report_ready)
    def report_ready(self):
        now=time.perf_counter_ns()
        report=os.environ.get('CALLNOTE_STARTUP_REPORT')
        if report:
            external=int(os.environ.get('CALLNOTE_STARTUP_BEGIN_NS',PYTHON_ENTRY_NS))
            atomic_json(report,dict(phase='first_paint_and_event_loop',
                total_ms=(now-external)/1e6,python_ms=(now-PYTHON_ENTRY_NS)/1e6,
                heavy_modules=[name for name in ['faster_whisper','ctranslate2','onnxruntime','numpy','PySide6.QtMultimedia'] if name in sys.modules]))
            if os.environ.get('CALLNOTE_STARTUP_EXIT')=='1':QTimer.singleShot(0,self.close)
    def refresh_models(self,select=None):
        previous=select or self.model.currentData();self.model.clear()
        for path in installed():self.model.addItem(path.name,str(path))
        idx=self.model.findData(previous)
        if idx>=0:self.model.setCurrentIndex(idx)
        if not self.model.count():self.model.addItem('Модель не установлена',None)
    def select_page(self,page,button_widget):
        self.pages.setCurrentWidget(page)
        for nav_button in [self.meeting_button,self.models_button,self.keys_button,self.audio_button]:
            nav_button.setProperty('primary',nav_button is button_widget)
            nav_button.style().unpolish(nav_button);nav_button.style().polish(nav_button)
        self.showNormal();self.raise_()
    def models(self):
        if not self.model_page:
            self.model_page=ModelPage(self);self.pages.addWidget(self.model_page)
        self.model_page.refresh();self.select_page(self.model_page,self.models_button)
    def show_main(self):
        self.select_page(self.meeting_page,self.meeting_button);self.activateWindow()
    def show_floating(self):self.mini.show();self.mini.raise_()
    def hotkey(self,name):
        if name=='record':self.toggle_record()
        elif name=='copy':self.copy()
        elif name=='floating':self.mini.setVisible(not self.mini.isVisible())
    def shortcuts(self):
        if self.shortcut_page:
            self.select_page(self.shortcut_page,self.keys_button);return
        page=QWidget();self.shortcut_page=page;layout=QVBoxLayout(page);layout.setContentsMargins(28,28,28,24);layout.setSpacing(16)
        title=QLabel('Горячие клавиши');title.setStyleSheet('font-size:24px;font-weight:600');layout.addWidget(title);editors={}
        for name,label in [('record','Запись / остановка'),('copy','Копировать текст'),('floating','Компактное окно')]:
            layout.addWidget(QLabel(label));edit=QKeySequenceEdit(QKeySequence(self.keymap.get(name,DEFAULTS[name])));layout.addWidget(edit);editors[name]=edit
        message=note('Сочетания работают при фокусе другого приложения на macOS.');layout.addWidget(message)
        def save():
            mapping={name:edit.keySequence().toString(QKeySequence.PortableText) for name,edit in editors.items()}
            try:
                if self.hotkeys:self.hotkeys.register(mapping)
                self.keymap=mapping;self.save_settings();message.setText('Сочетания сохранены.')
            except Exception as exc:
                try:
                    if self.hotkeys:self.hotkeys.register(self.keymap)
                except Exception:pass
                message.setText(str(exc))
        layout.addWidget(button('Сохранить',save,True));layout.addStretch()
        self.pages.addWidget(page);self.select_page(page,self.keys_button)
    def permissions(self):
        if self.audio_page:
            self.select_page(self.audio_page,self.audio_button);return
        page=QWidget();self.audio_page=page;layout=QVBoxLayout(page);layout.setContentsMargins(28,28,28,24);layout.setSpacing(18)
        title=QLabel('Доступ к звуку');title.setStyleSheet('font-size:24px;font-weight:600');layout.addWidget(title)
        layout.addWidget(note('Для системного звука macOS запрашивает доступ к записи системного аудио. Для вашего голоса нужен отдельный доступ к микрофону. Запрос появится при старте записи. После изменения доступа может потребоваться перезапуск Callnote.'))
        for title,url in [('Открыть доступ к системному звуку','x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture'),('Открыть доступ к микрофону','x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone')]:
            layout.addWidget(button(title,lambda checked=False,u=url:QDesktopServices.openUrl(QUrl(u))))
        layout.addWidget(note('Разрешения меняются в системных настройках macOS. При смене устройства проверьте индикаторы; при пропадании сигнала остановите и начните запись заново.'))
        layout.addStretch();self.pages.addWidget(page);self.select_page(page,self.audio_button)
    def ensure_player(self):
        if self.player is not None:return
        # Device discovery and FFmpeg load only when audio is opened.
        from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
        self.player=QMediaPlayer();self.output=QAudioOutput();self.player.setAudioOutput(self.output)
        self.player.durationChanged.connect(lambda duration:self.seek.setRange(0,duration))
        self.player.positionChanged.connect(self.position)
        self.player.errorOccurred.connect(lambda *args:self.status.setText('Аудиоплеер: '+self.player.errorString()))
    def set_audio(self,path):
        self.audio_path=path
        if path:self.ensure_player()
        if self.player:self.player.setSource(QUrl.fromLocalFile(path) if path else QUrl())
        self.file_label.setText(Path(path).name if path else 'Новая встреча')
    def clear(self):
        self.save_recovery();self.rows=[];self.table.setRowCount(0);self.session_folder=None;self.copy_preview.clear();self.dirty=True
    def open_audio(self):
        if self.active:return
        path,_=QFileDialog.getOpenFileName(self,'Открыть аудио','','Аудио (*.wav *.mp3 *.m4a *.flac *.ogg *.mp4 *.aiff);;Все файлы (*)')
        if path:self.clear();self.set_audio(path)
    def toggle_record(self):
        if self.active:self.stop_worker()
        else:self.begin('')
    def transcribe(self):
        if self.audio_path:self.begin(self.audio_path)
        else:self.status.setText('Сначала откройте аудиофайл.')
    def begin(self,path):
        if self.active:return
        if self.model_page and self.model_page.busy() and self.model_page.task.operation != 'catalog':self.status.setText('Дождитесь загрузки модели.');return
        model=self.model.currentData()
        if not model:self.models();return
        if self.rows and QMessageBox.question(self,'Новая расшифровка','Заменить текущую расшифровку? Сохраните проект, если хотите вернуться к нему.',QMessageBox.Yes|QMessageBox.No)!=QMessageBox.Yes:return
        if self.player:self.player.pause()
        self.clear()
        if not path:self.set_audio('');self.start.setDateTime(QDateTime.currentDateTime())
        self.worker=SessionWorker(model,[None,'ru','en'][self.language.currentIndex()],path,self.source.currentData(),self)
        self.worker.segment.connect(self.add_segment);self.worker.status.connect(self.status.setText);self.worker.failed.connect(self.failed)
        self.worker.started_at.connect(self.started);self.worker.capture_ready.connect(self.ready);self.worker.capture_done.connect(self.capture_stopped)
        self.worker.level.connect(self.show_level);self.worker.recording.connect(self.recording_ready);self.worker.finished.connect(self.finished)
        self.busy(True);self.save_settings();self.worker.start()
    def busy(self,active):
        self.active=active
        for control in [self.model,self.language,self.source,self.models_button,self.open_audio_button,self.transcribe_button,self.open_project_button]:control.setEnabled(not active)
        self.stop_button.setEnabled(active);self.cancel_button.setEnabled(active);self.record_button.setText('■ Завершить запись' if active else '● Начать запись');self.mini.record.setText('■ Стоп' if active else '● Запись')
        self.live.setText('Подготовка…' if active else 'Запись не идёт');self.mini.state.setText(self.live.text())
    def started(self,value):
        dt=datetime.fromisoformat(value.replace('Z','+00:00')).astimezone();self.record_started=dt;self.start.setDateTime(QDateTime(dt))
    def ready(self):self.live.setText('● Запись');self.mini.state.setText('● '+self.source.currentText());self.status.setText('Аудио сохраняется. Текст появится после обработки первых фрагментов.')
    def show_level(self,source,peak):
        if source in self.levels:self.levels[source].setValue(min(100,int(peak*100)))
    def capture_stopped(self):
        self.record_started=None;self.live.setText('Запись остановлена');self.mini.state.setText('Обработка оставшихся фрагментов…')
        for bar in self.levels.values():bar.setValue(0)
    def stop_worker(self):
        if not self.worker or not self.active:return
        if self.worker.source:self.worker.cancel()
        else:self.worker.stop()
        self.status.setText('Останавливаю запись; оставшиеся фрагменты будут расшифрованы.');self.stop_button.setEnabled(False)
    def cancel_worker(self):
        if self.worker and self.active:self.worker.cancel();self.status.setText('Обработка отменяется. Записанное аудио сохраняется.');self.cancel_button.setEnabled(False)
    def failed(self,message):self.status.setText(message);self.mini.state.setText('Ошибка — откройте главное окно');self.show_main();QMessageBox.warning(self,'Callnote',message)
    def recording_ready(self,path,folder):self.set_audio(path);self.session_folder=Path(folder);self.dirty=True;self.save_recovery()
    def finished(self):
        self.busy(False);self.record_started=None
        if not self.worker.had_error:
            if self.worker.cancel_event.is_set():
                message = 'Обработка отменена.'
            elif not self.rows:
                message = 'Речь не распознана. Проверьте звук и выбранный язык.'
            else:
                message = 'Готово. Текст и имена можно редактировать; проект сохранён автоматически.'
            self.status.setText(message)
            self.mini.state.setText('Готово' if self.rows else message)
        self.dirty=True;self.save_recovery()
    def tick(self):
        if self.record_started:self.mini.clock.setText(stamp((datetime.now().astimezone()-self.record_started).total_seconds()))
    def add_segment(self,row):
        # Insert by time: microphone and system chunks may finish in either order.
        index=next((i for i,r in enumerate(self.rows) if r['start']>row['start']),len(self.rows))
        self.rows.insert(index,row)
        with QSignalBlocker(self.table):
            self.table.insertRow(index);time=QTableWidgetItem(stamp(row['start']));time.setFlags(time.flags()&~Qt.ItemIsEditable);self.table.setItem(index,0,time)
            self.table.setItem(index,1,QTableWidgetItem(row.get('speaker','')));self.table.setItem(index,2,QTableWidgetItem(row['text']));self.table.resizeRowToContents(index)
        self.mini.text.setText(row['text'][-240:]);self.table.scrollToBottom();self.dirty=True;self.preview()
    def edited(self,row,column):
        if row<len(self.rows) and column in (1,2):self.rows[row]['speaker' if column==1 else 'text']=self.table.item(row,column).text();self.dirty=True;self.preview()
    def preview(self,*args):
        if not hasattr(self,'copy_preview'):return
        self.copy_preview.setPlainText(self.text());self.start.setEnabled(self.absolute.isChecked());self.dirty=True
    def text(self):return export_text(self.rows,self.times.isChecked(),self.speakers.isChecked(),self.plain.isChecked(),self.start.dateTime().toPython() if self.absolute.isChecked() else None)
    def copy(self):QApplication.clipboard().setText(self.text());self.status.setText('Текст скопирован')
    def export(self):
        path,_=QFileDialog.getSaveFileName(self,'Экспорт TXT','Расшифровка.txt','Текст (*.txt)')
        if path:
            try:Path(path).write_text(self.text(),encoding='utf-8')
            except OSError as exc:QMessageBox.warning(self,'Сохранение',str(exc))
    def project(self):return dict(version=2,audio=self.audio_path,start=self.start.dateTime().toPython().isoformat(),segments=self.rows)
    def save_recovery(self):
        if not self.dirty or not self.rows:return
        try:
            atomic_json(app_data()/'recovery.json',self.project())
            if self.session_folder:atomic_json(self.session_folder/'project.json',self.project())
            self.dirty=False
        except OSError as exc:self.status.setText('Ошибка автосохранения: '+str(exc))
    def save_project(self):
        path,_=QFileDialog.getSaveFileName(self,'Сохранить проект','Встреча.json','Callnote (*.json)')
        if path:
            try:atomic_json(path,self.project());self.status.setText('Проект сохранён; аудио хранится отдельно.')
            except OSError as exc:QMessageBox.warning(self,'Сохранение',str(exc))
    def load_project(self,path):
        try:
            data=validate_project(json.loads(Path(path).read_text(encoding='utf-8')))
            self.clear();self.start.setDateTime(QDateTime.fromString(data['start'],Qt.ISODate));self.set_audio(data.get('audio',''))
            for row in data['segments']:self.add_segment(row)
            if not Path(self.audio_path).is_file():self.status.setText('Текст загружен, но аудиофайл по сохранённому пути отсутствует.')
        except Exception as exc:QMessageBox.warning(self,'Проект',str(exc))
    def open_project(self):
        if self.active:return
        path,_=QFileDialog.getOpenFileName(self,'Открыть проект','','Callnote (*.json)')
        if path:self.load_project(path)
    def offer_recovery(self):
        if QMessageBox.question(self,'Предыдущая встреча','Восстановить последнюю автоматически сохранённую расшифровку?',QMessageBox.Yes|QMessageBox.No)==QMessageBox.Yes:self.load_project(app_data()/'recovery.json')
    def open_recordings(self):
        folder=app_data()/'Recordings';folder.mkdir(exist_ok=True);QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
    def toggle_play(self):
        if not self.audio_path:return
        from PySide6.QtMultimedia import QMediaPlayer
        self.ensure_player()
        if self.active:self.status.setText('Прослушивание доступно после остановки записи.');return
        if self.player.playbackState()==QMediaPlayer.PlayingState:self.player.pause()
        else:self.player.play()
    def jump(self,row,column):
        if column==0 and not self.active and self.player:self.player.setPosition(int(self.rows[row]['start']*1000))
    def position(self,ms):
        if not self.seek.isSliderDown():self.seek.setValue(ms)
        self.clock.setText(stamp(ms/1000))
        for index,row in enumerate(self.rows):self.table.item(index,0).setBackground(QColor('#e1eedc') if row['start']<=ms/1000<row['end'] else QColor('white'))
    def save_settings(self):
        try:atomic_json(app_data()/'settings.json',{'hotkeys':self.keymap,'model':self.model.currentData()})
        except OSError as exc:self.status.setText('Настройки не сохранены: '+str(exc))
    def closeEvent(self,event):
        if self.active:
            self.stop_worker();self.status.setText('Дождитесь завершения обработки или отмените её.');event.ignore();return
        if self.model_page and self.model_page.task and self.model_page.task.isRunning():
            self.model_page.task.finished.connect(self.close);self.model_page.cancel_task();event.ignore();return
        self.save_recovery();self.save_settings();self.mini.hide()
        if self.hotkeys:self.hotkeys.close()
        event.accept()

if __name__=='__main__':
    app=QApplication(sys.argv);app.setApplicationName('Callnote');app.setOrganizationName('Callnote');app.setStyle('Fusion');app.setStyleSheet(STYLE)
    window=Window();window.show();sys.exit(app.exec())
